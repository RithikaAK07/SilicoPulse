"""Analytics engine answering hackathon questions Q1-Q6 (Q7/Q8 live in ml.py).

Aggregations run through DuckDB over the in-memory Arrow/Polars frame; the
heavier statistical pieces (clustering, lift mining, diffs) use Polars + NumPy.
"""
from __future__ import annotations

import difflib
import re
from itertools import combinations

import numpy as np
import polars as pl
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

from . import evidence, stats
from .config import MIN_PAIR_SAMPLES, MIN_PROFILE_REPEATS, MIN_PROFILE_SEEDS, MIN_SEED_RUNS, SIGNIFICANCE_ALPHA
from .generator import SIGNATURES
from .store import store

# Risk thresholds for randomized variables are LEARNED from the active dataset (evidence.py);
# nothing here assumes benchmark-specific limits such as a fixed temperature.
DETERMINISTIC_FAIL_RATE = 0.8


def _r(x, n=4):
    return None if x is None else round(float(x), n)


# --------------------------------------------------------------------------- filters
def where_clause(f: dict) -> tuple[str, list]:
    conds, params = [], []
    for col in ("environment", "hardware", "workload"):
        if f.get(col):
            conds.append(f"{col} = ?")
            params.append(f[col])
    if f.get("date_from"):
        conds.append("timestamp >= CAST(? AS TIMESTAMP)")
        params.append(f["date_from"])
    if f.get("date_to"):
        conds.append("timestamp < CAST(? AS TIMESTAMP) + INTERVAL 1 DAY")
        params.append(f["date_to"])
    return ("WHERE " + " AND ".join(conds)) if conds else "", params


# --------------------------------------------------------------------------- overview
def overview(f: dict) -> dict:
    w, p = where_clause(f)
    k = store.sql(f"""
        SELECT count(*) AS total, sum(failed) AS fails,
               avg(CASE WHEN failed = 0 THEN throughput_mbps END) AS mean_tput,
               avg(instability_index) AS instability, avg(latency_p99_ms) AS p99,
               count(DISTINCT config_id) AS configs, count(DISTINCT seed) AS seeds
        FROM runs {w}""", p)[0]
    risky = store.sql(f"""
        SELECT count(*) AS n FROM (SELECT config_id FROM runs {w} GROUP BY config_id
        HAVING count(*) >= 5 AND avg(failed) > 0.3)""", p)[0]["n"]
    trend = store.sql(f"""
        SELECT strftime(date_trunc('day', timestamp), '%Y-%m-%d') AS day, count(*) AS runs,
               avg(failed) AS fail_rate, avg(CASE WHEN failed = 0 THEN throughput_mbps END) AS tput,
               avg(instability_index) AS instability
        FROM runs {w} GROUP BY 1 ORDER BY 1""", p)
    sigs = store.sql(f"""
        SELECT error_signature AS signature, count(*) AS count FROM runs {w}
        {"AND" if w else "WHERE"} failed = 1 GROUP BY 1 ORDER BY 2 DESC""", p)
    by_hw = store.sql(f"""
        SELECT hardware, count(*) AS runs, avg(failed) AS fail_rate,
               avg(CASE WHEN failed = 0 THEN throughput_mbps END) AS tput
        FROM runs {w} GROUP BY 1 ORDER BY 1""", p)
    total = k["total"] or 0
    fails = int(k["fails"] or 0)
    synth = set(store.meta.get("synthetic_columns", []))
    if "instability_index" in synth:
        k["instability"] = None
    if "latency_p99_ms" in synth:
        k["p99"] = None
    if "throughput_mbps" in synth:
        k["mean_tput"] = None
    return {
        "kpis": {
            "total_executions": total, "passes": total - fails, "failures": fails,
            "pass_rate": _r((total - fails) / total if total else 0),
            "mean_throughput": _r(k["mean_tput"], 1), "high_risk_configs": risky,
            "instability_index": _r(k["instability"]), "p99_latency": _r(k["p99"], 2),
            "unique_configs": k["configs"], "unique_seeds": k["seeds"],
        },
        "trend": [{**t, "fail_rate": _r(t["fail_rate"]), "tput": _r(t["tput"], 1), "instability": _r(t["instability"])} for t in trend],
        "signatures": sigs,
        "by_hardware": [{**h, "fail_rate": _r(h["fail_rate"]), "tput": _r(h["tput"], 1)} for h in by_hw],
    }


def filter_options() -> dict:
    df = store.df
    return {
        "environment": sorted(df["environment"].unique().to_list()),
        "hardware": sorted(df["hardware"].unique().to_list()),
        "workload": sorted(df["workload"].unique().to_list()),
        "date_min": str(df["timestamp"].min().date()),
        "date_max": str(df["timestamp"].max().date()),
    }


# --------------------------------------------------------------------------- Q1 / Q2
def _profile_stats() -> pl.DataFrame:
    def build():
        key = [p["name"] for p in store.meta["key_params"]]
        g = store.df.group_by("config_id").agg(
            pl.len().alias("runs"),
            pl.col("failed").mean().alias("fail_rate"),
            pl.col("throughput_mbps").filter(pl.col("failed") == 0).mean().alias("throughput"),
            pl.col("instability_index").mean().alias("instability"),
            pl.col("latency_p99_ms").mean().alias("p99"),
            pl.col("seed").n_unique().alias("distinct_seeds"),
            *[pl.col(c).first() for c in key],
        )
        return g.filter(pl.col("runs") >= 5).fill_null(0.0)
    return store.cached("profiles", build)


def discovery() -> dict:
    b = store.bundle
    cfg_cols = store.meta["config_params"]
    rf = {c: b.importance_full[c] for c in cfg_cols}
    tot = sum(rf.values()) or 1
    mi = {c: b.mutual_info[c] for c in cfg_cols}
    mi_max = max(mi.values()) or 1
    key_names = {p["name"] for p in store.meta["key_params"]}
    rows = []
    for c in cfg_cols:
        score = 0.6 * rf[c] / tot / max(v / tot for v in rf.values()) + 0.4 * mi[c] / mi_max
        rows.append({"feature": c, "rf_importance": _r(rf[c] / tot), "mutual_info": _r(mi[c]),
                     "correlation": b.correlation[c], "score": _r(score), "named": c in key_names})
    rows.sort(key=lambda r: -r["score"])

    prof = _profile_stats()
    pts = prof.select("config_id", "runs", "fail_rate", "throughput", "instability").to_dicts()
    pts.sort(key=lambda r: (-r["throughput"], r["fail_rate"]))
    best_fail = 1.1
    for pnt in pts:  # Pareto: maximise throughput, minimise failure rate
        pnt["pareto"] = pnt["fail_rate"] < best_fail
        if pnt["pareto"]:
            best_fail = pnt["fail_rate"]
    for pnt in pts:
        pnt["fail_rate"], pnt["throughput"], pnt["instability"] = _r(pnt["fail_rate"]), _r(pnt["throughput"], 1), _r(pnt["instability"])
    if len(pts) > 1500:  # chart payload cap: every Pareto point + an even sample of dominated ones
        dominated = [x for x in pts if not x["pareto"]]
        step = max(1, len(dominated) // 1400)
        pts = [x for x in pts if x["pareto"]] + dominated[::step]

    tmax = prof["throughput"].max() or 1
    top = (prof.with_columns(((pl.col("throughput") / tmax) * (1 - pl.col("fail_rate")) ** 2 * (1 - 0.3 * pl.col("instability"))).alias("score"))
           .sort("score", descending=True).head(25))
    pareto_ids = {p["config_id"] for p in pts if p["pareto"]}
    top_rows = [{**{k: (_r(v, 4) if isinstance(v, float) else v) for k, v in r.items()}, "pareto": r["config_id"] in pareto_ids} for r in top.to_dicts()]
    return {"importance": rows[:25], "pareto": pts, "top_configs": top_rows, "pairs": optimal_pairs(), "model_auc": b.auc,
            "key_params": [p["name"] for p in store.meta["key_params"]]}


def optimal_pairs() -> dict:
    def build():
        df = store.df
        min_support = MIN_PAIR_SAMPLES or max(15, min(60, len(df) // 170))
        k_all, n_all = int(df["failed"].sum()), len(df)
        key = [p["name"] for p in store.meta["key_params"]]
        marg = {c: dict(df.group_by(c).agg(pl.col("failed").mean().alias("fr")).select(c, "fr").iter_rows()) for c in key}
        base = float(df["failed"].mean())
        tmax = float(df.filter(pl.col("failed") == 0)["throughput_mbps"].quantile(0.95) or 0) or 1.0
        cells = []
        for a, bcol in combinations(key, 2):
            g = df.group_by([a, bcol]).agg(pl.len().alias("n"), pl.col("failed").mean().alias("fr"),
                                           pl.col("throughput_mbps").filter(pl.col("failed") == 0).mean().alias("tp"))
            tested = len(g)
            for r in g.filter(pl.col("n") >= min_support).iter_rows(named=True):
                k = int(round(r["fr"] * r["n"]))
                z, pv = stats.two_prop_z(k, r["n"], k_all - k, n_all - r["n"])
                m = max(marg[a].get(r[a], 0) or 0, marg[bcol].get(r[bcol], 0) or 0)
                cells.append({"pair": f"{a} × {bcol}", "a": a, "a_val": r[a], "b": bcol, "b_val": r[bcol], "runs": r["n"],
                              "fail_rate": _r(r["fr"]), "throughput": _r(r["tp"] or 0, 1), "lift": _r(r["fr"] / base if base else 0, 2),
                              "score": (r["tp"] or 0) / tmax * (1 - r["fr"]) ** 2,
                              "failures": k, "odds_ratio": _r(stats.odds_ratio(k, r["n"], k_all - k, n_all - r["n"]), 3),
                              "z_score": _r(z, 2), "p_value": float(f"{pv:.3g}"),
                              "interaction_lift": _r(r["fr"] / m if m else 0, 3),
                              "ci95": [_r(x) for x in stats.wilson(k, r["n"])],
                              "evidence_strength": stats.evidence_strength(pv, r["n"], min_support, tested * 66, SIGNIFICANCE_ALPHA)})
        best = sorted(cells, key=lambda c: -c["score"])[:10]
        worst = sorted(cells, key=lambda c: -c["fail_rate"])[:10]
        for c in best + worst:
            c["score"] = _r(c["score"])
        return {"best": best, "worst": worst, "baseline_fail_rate": _r(base), "min_support": min_support}
    return store.cached("pairs", build)


# --------------------------------------------------------------------------- Q3 / Q4
def _has_seed() -> bool:
    return "seed" in store.meta["random_vars"]


def _continuous_random() -> list[str]:
    """Randomized variables usable as perturbation axes (numeric, not the seed id, enough distinct values)."""
    df = store.df
    return [c for c in store.meta["random_vars"] if c != "seed" and df[c].dtype.is_numeric() and df[c].n_unique() >= 6]


def _seed_stats() -> pl.DataFrame:
    def build():
        b = store.bundle
        df = store.df
        if not _has_seed():
            return pl.DataFrame(schema={"seed": pl.Int64, "runs": pl.UInt32, "observed": pl.Float64, "expected": pl.Float64,
                                        "z": pl.Float64, "lift": pl.Float64, "repeatability": pl.Float64})
        exp = b.predict_frame_risk(df)
        d = df.select("seed", "failed").with_columns(pl.Series("expected", exp))
        g = d.group_by("seed").agg(pl.len().alias("runs"), pl.col("failed").mean().alias("observed"), pl.col("expected").mean().alias("expected"))
        var = (g["expected"] * (1 - g["expected"]) / g["runs"]).sqrt()
        z = (g["observed"] - g["expected"]) / var
        return g.with_columns(pl.Series("z", z), (pl.col("observed") / pl.col("expected")).alias("lift")) \
                .with_columns((-(pl.col("z").clip(0, None)) / 3).exp().alias("repeatability")).sort("z", descending=True)

    return store.cached("seeds", build)


def randomization() -> dict:
    df = store.df
    b = store.bundle
    rv = store.meta["random_vars"]
    rf = {c: b.importance_full[c] for c in rv}
    rf_max = max(rf.values()) or 1
    mi_max = max(b.mutual_info[c] for c in rv) or 1
    seeds = _seed_stats()
    sens, curves = [], {}
    for c in rv:
        if c == "seed":
            obs = seeds.filter(pl.col("runs") >= 30)["observed"]
            spread = float((obs.quantile(0.95) or 0) - (obs.quantile(0.05) or 0)) if len(obs) else 0.0
        else:
            if not df[c].dtype.is_numeric() or df[c].n_unique() < 2:
                continue
            binned = df.select(pl.col(c).qcut(8, labels=[str(i) for i in range(8)], allow_duplicates=True).alias("bin"), pl.col(c), "failed") \
                       .group_by("bin").agg(pl.col(c).mean().alias("x"), pl.col("failed").mean().alias("fr")).sort("x")
            spread = float(binned["fr"].max() - binned["fr"].min())
            curves[c] = [{"x": _r(r["x"], 3), "fail_rate": _r(r["fr"])} for r in binned.iter_rows(named=True)]
        score = 0.45 * spread / 0.5 + 0.35 * rf[c] / rf_max + 0.2 * b.mutual_info[c] / mi_max
        sens.append({"variable": c, "fail_rate_spread": _r(spread), "rf_importance": _r(rf[c]), "mutual_info": _r(b.mutual_info[c]),
                     "impact_score": _r(score), "threshold": (evidence.threshold_for(c) or {}).get("threshold")})
    sens.sort(key=lambda r: -r["impact_score"])
    top_curve_vars = [s["variable"] for s in sens if s["variable"] in curves][:4]

    # heatmaps: top seeds vs environment perturbation bands
    min_runs = 30 if len(df) >= 5000 else 8
    eligible = seeds.filter(pl.col("runs") >= min_runs)
    top_seeds = eligible.head(10)["seed"].to_list() + eligible.tail(6)["seed"].to_list() if len(eligible) > 16 else eligible["seed"].to_list()
    cont = _continuous_random()
    preferred = [v for v in ("traffic_intensity", "temperature_c", "timing_jitter_us") if v in cont]
    by_impact = [x["variable"] for x in sens if x["variable"] in cont and x["variable"] not in preferred]
    heatmaps = {}
    for pv in ((preferred + by_impact)[:3] if top_seeds else []):
        labels = ["Q1 low", "Q2", "Q3", "Q4", "Q5 high"]
        d = df.filter(pl.col("seed").is_in(top_seeds)).with_columns(
            pl.col(pv).qcut(5, labels=labels, allow_duplicates=True).alias("band")).group_by("seed", "band").agg(pl.len().alias("n"), pl.col("failed").mean().alias("fr"))
        heatmaps[pv] = {"rows": [str(s) for s in top_seeds], "cols": labels,
                        "cells": [{"row": str(r["seed"]), "col": r["band"], "fail_rate": _r(r["fr"]), "runs": r["n"]} for r in d.iter_rows(named=True)]}

    seed_pts = seed_points(seeds)
    return {"sensitivity": sens, "curves": {c: curves[c] for c in top_curve_vars}, "heatmaps": heatmaps,
            "seeds": seed_pts, "has_seed": _has_seed(), "determinism": _determinism_payload(),
            "seed_policy": {"min_runs": MIN_SEED_RUNS, "alpha": SIGNIFICANCE_ALPHA, "correction": "Bonferroni over scored seeds",
                            "insufficient_seeds": sum(1 for x in seed_pts if not x["sufficient_samples"])}}


def _determinism_payload(max_profiles: int = 500) -> dict:
    """determinism() with the per-profile list capped for page payloads (full list: /api/insights/determinism)."""
    d = determinism()
    profs = sorted(d["profiles"], key=lambda r: (r["class"] != "deterministic", -r["runs"]))
    return {**d, "profiles": profs[:max_profiles], "profiles_total": len(profs)}


def seed_points(seeds: pl.DataFrame | None = None) -> list[dict]:
    """Seed scores with 95% Wilson CIs and Bonferroni-corrected significance. A seed is only
    flagged as anomalous with >= MIN_SEED_RUNS runs and a significant excess over expectation."""
    seeds = _seed_stats() if seeds is None else seeds
    rows = [r for r in seeds.iter_rows(named=True) if r["z"] is not None and np.isfinite(r["z"])]
    tested = sum(1 for r in rows if r["runs"] >= MIN_SEED_RUNS) or 1
    out = []
    for r in rows:
        k = int(round(r["observed"] * r["runs"]))
        p = stats.two_sided_p(r["z"])
        enough = r["runs"] >= MIN_SEED_RUNS
        significant = enough and p * tested < SIGNIFICANCE_ALPHA
        lo, hi = stats.wilson(k, r["runs"])
        out.append({"seed": str(r["seed"]), "runs": r["runs"], "observed": _r(r["observed"]), "expected": _r(r["expected"]),
                    "z": _r(r["z"], 2), "lift": _r(r["lift"], 2), "repeatability": _r(r["repeatability"]),
                    "flag": bool(significant and r["z"] > 0), "failures": k, "ci95": [_r(lo), _r(hi)],
                    "p_value": float(f"{p:.3g}"), "significant": bool(significant), "sufficient_samples": enough})
    return out


def determinism() -> dict:
    def build():
        prof = _profile_stats()
        enough = (pl.col("runs") >= MIN_PROFILE_REPEATS) & (pl.col("distinct_seeds") >= min(MIN_PROFILE_SEEDS, 1 if not _has_seed() else MIN_PROFILE_SEEDS))
        cls = prof.with_columns(
            (2 * pl.col("fail_rate") - 1).abs().alias("repeatability"),
            pl.when(~enough).then(pl.lit("insufficient"))
              .when(pl.col("fail_rate") >= DETERMINISTIC_FAIL_RATE).then(pl.lit("deterministic"))
              .when(pl.col("fail_rate") >= 0.08).then(pl.lit("stochastic")).otherwise(pl.lit("stable")).alias("class"))
        cls_map = dict(zip(cls["config_id"].to_list(), cls["class"].to_list()))
        fails = store.df.filter(pl.col("failed") == 1).with_columns(
            pl.col("config_id").replace_strict(cls_map, default="insufficient").alias("class"))
        by_sig = fails.group_by("error_signature").agg(
            (pl.col("class") == "deterministic").sum().alias("deterministic"),
            (pl.col("class").is_in(["stochastic", "stable"])).sum().alias("stochastic"),
            (pl.col("class") == "insufficient").sum().alias("insufficient")).sort("error_signature")
        by_sig = by_sig.with_columns(
            (pl.col("deterministic") / (pl.col("deterministic") + pl.col("stochastic")).clip(1, None)).alias("deterministic_share"))
        by_sig = by_sig.with_columns(
            pl.when(pl.col("deterministic") + pl.col("stochastic") < MIN_PROFILE_REPEATS).then(pl.lit("insufficient data"))
              .when(pl.col("deterministic_share") >= 0.7).then(pl.lit("deterministic"))
              .when(pl.col("deterministic_share") <= 0.3).then(pl.lit("stochastic")).otherwise(pl.lit("mixed")).alias("tendency"))
        counts = fails["class"].value_counts().to_dicts()
        profiles = [{"config_id": r["config_id"], "fail_rate": _r(r["fail_rate"]), "repeatability": _r(r["repeatability"]),
                     "instability": _r(r["instability"]), "runs": r["runs"], "distinct_seeds": r["distinct_seeds"], "class": r["class"],
                     "failures": int(round(r["fail_rate"] * r["runs"])),
                     "ci95": [_r(x) for x in stats.wilson(int(round(r["fail_rate"] * r["runs"])), r["runs"])]}
                    for r in cls.iter_rows(named=True)]
        return {"failure_classes": counts, "by_signature": [{k: (_r(v) if isinstance(v, float) else v) for k, v in r.items()} for r in by_sig.to_dicts()],
                "profiles": profiles, "profile_classes": cls["class"].value_counts().to_dicts(), "clusters": failure_clusters(),
                "policy": {"min_repeats": MIN_PROFILE_REPEATS, "min_distinct_seeds": MIN_PROFILE_SEEDS, "deterministic_fail_rate": DETERMINISTIC_FAIL_RATE,
                           "stable_below": 0.08, "note": "Profiles with too few repeats/seeds are 'insufficient', never labelled deterministic."}}
    return store.cached("determinism", build)


def failure_clusters(k: int = 6) -> dict:
    df = store.df.filter(pl.col("failed") == 1)
    if len(df) < 20:
        return {"points": [], "summary": []}
    k = min(k, max(2, len(df) // 40))
    enc = store.bundle.full_enc
    imp = store.bundle.importance_full
    synth = set(store.meta.get("synthetic_columns", []))
    candidates = [c for c in store.meta["config_params"] + store.meta["random_vars"] if c != "seed"]
    feats = sorted(candidates, key=lambda c: -imp.get(c, 0))[:9]
    feats += [t for t in ("latency_p99_ms", "retry_count", "instability_index") if t not in synth and t in df.columns]
    cols = []
    for c in feats:
        if c in enc.cat_maps:
            cols.append(np.array([enc.cat_maps[c].get(v, 0) for v in df[c].to_list()], float))
        else:
            v = df[c].cast(pl.Float64).to_numpy()
            # log-scale wide positive ranges (queue depths, block sizes, thread counts...)
            cols.append(np.log2(v) if v.min() > 0 and v.max() / max(v.min(), 1e-9) > 50 else v)
    X = np.nan_to_num(StandardScaler().fit_transform(np.column_stack(cols)))
    km = KMeans(n_clusters=k, n_init=5, random_state=0).fit(X)
    xy = PCA(n_components=2, random_state=0).fit_transform(X)
    labels = km.labels_
    sigs = df["error_signature"].to_numpy()
    rng = np.random.default_rng(1)
    idx = rng.choice(len(df), size=min(1400, len(df)), replace=False)
    points = [{"x": _r(xy[i, 0], 3), "y": _r(xy[i, 1], 3), "cluster": int(labels[i]), "signature": sigs[i]} for i in idx]
    summary = []
    for c in range(k):
        m = labels == c
        vals, cnt = np.unique(sigs[m], return_counts=True)
        centroid = km.cluster_centers_[c]
        top = np.argsort(-np.abs(centroid))[:3]
        summary.append({"cluster": c, "size": int(m.sum()), "dominant_signature": str(vals[cnt.argmax()]),
                        "purity": _r(cnt.max() / m.sum()),
                        "drivers": [f"{'high' if centroid[j] > 0 else 'low'} {feats[j]}" for j in top]})
    return {"points": points, "summary": summary}


# --------------------------------------------------------------------------- drift / topology
def drift() -> dict:
    cont = _continuous_random()
    preferred = [v for v in ("temperature_c", "traffic_intensity", "timing_jitter_us") if v in cont]
    learned = {t["variable"]: t for t in evidence.environment_thresholds(None)}
    if not preferred:  # uploaded data: the three variables with the strongest learned thresholds
        preferred = [t for t in learned if t in cont][:3] or sorted(cont, key=lambda c: -store.bundle.importance_full.get(c, 0))[:3]
    sel = "".join(f'avg("{v}") AS "{v}", quantile_cont("{v}", 0.95) AS "{v}__p95", ' for v in preferred)
    daily = store.sql(f"""
        SELECT strftime(date_trunc('day', timestamp), '%Y-%m-%d') AS day, count(*) AS runs,
               {sel} avg(failed) AS fail_rate
        FROM runs GROUP BY 1 ORDER BY 1""")
    series = [{k: (_r(v, 3) if isinstance(v, float) else v) for k, v in d.items()} for d in daily]
    variables, crossings = [], []
    for v in preferred:
        thr = learned.get(v)
        means = [x[v] for x in series if x[v] is not None]
        if thr:
            limit, src, direction = thr["threshold"], "learned", thr["direction"]
        else:  # no significant failure threshold: show the 80th percentile of daily means as a neutral reference line
            limit, src, direction = (_r(float(np.quantile(means, 0.8)), 3) if means else 0.0), "reference (80th pct of daily mean; no significant risk threshold)", "above"
        key = f"{v}__p95" if direction == "above" else v
        beyond = (lambda x: x > limit) if direction == "above" else (lambda x: x <= limit)
        above = [x["day"] for x in series if x.get(key) is not None and beyond(x[key])]
        unit = {"temperature_c": "°C", "timing_jitter_us": "µs"}.get(v, "")
        variables.append({"key": v, "p95_key": f"{v}__p95", "label": v.replace("_", " "), "unit": unit, "limit": limit,
                          "limit_source": src, "direction": direction, "evidence": thr})
        crossings.append({"variable": v, "threshold": limit, "first_crossing": above[0] if above else None, "days_above": len(above),
                          "limit_source": src, **evidence.time_drift(store.df, v, thr)})
    temp = "avg(temperature_c)" if "temperature_c" in store.df.columns else "NULL"
    topo = store.sql(f"""
        SELECT hardware, environment, count(*) AS runs, avg(failed) AS fail_rate, {temp} AS temp,
               avg(CASE WHEN failed = 0 THEN throughput_mbps END) AS tput, mode(NULLIF(error_signature, 'NONE')) AS top_signature
        FROM runs GROUP BY 1, 2 ORDER BY 1, 2""")
    return {"series": series, "variables": variables, "crossings": crossings,
            "topology": [{**t, "fail_rate": _r(t["fail_rate"]), "temp": _r(t["temp"], 1), "tput": _r(t["tput"], 1)} for t in topo]}


# --------------------------------------------------------------------------- Q5
_NUM = re.compile(r"0x[0-9A-Fa-f]+|\b[0-9A-F]{4,}\b|\d+(\.\d+)?")


_SEV = re.compile(r"\b(WARN|WARNING|ERROR|FATAL|CRITICAL|PANIC)\b")


def _severity(line: str) -> str | None:
    m = _SEV.search(line)
    return None if m is None else ("WARN" if m.group(1) == "WARNING" else m.group(1))


def log_template(line: str) -> str:
    return _NUM.sub("<*>", line)


def _conditions(df: pl.DataFrame) -> list[tuple[str, pl.Series]]:
    conds = []
    for p in store.meta["key_params"]:
        col = df[p["name"]]
        if p["kind"] in ("cat", "bool") or col.n_unique() <= 24:
            for v in p["choices"]:
                conds.append((f"{p['name']} = {v}", col == v))
        else:  # continuous parameter: upper / lower tail bands
            q_lo, q_hi = col.quantile(0.2), col.quantile(0.8)
            conds += [(f"{p['name']} >= {q_hi:g}", col >= q_hi), (f"{p['name']} <= {q_lo:g}", col <= q_lo)]
    for v in _continuous_random():
        thr = evidence.threshold_for(v)
        if thr:
            conds.append((thr["condition"], df[v] > thr["threshold"] if thr["direction"] == "above" else df[v] <= thr["threshold"]))
        else:
            limit = float(df[v].quantile(0.9))
            conds.append((f"{v} > {limit:g}", df[v] > limit))
    if "io_scheduler" in df.columns and "timing_jitter_us" in df.columns:
        conds.append(("io_scheduler = none & jitter > 15", (df["io_scheduler"] == "none") & (df["timing_jitter_us"] > 15)))
    flagged = [x["seed"] for x in randomization_seeds_flagged()]
    if flagged:
        conds.append((f"seed ∈ anomalous seeds ({len(flagged)})", df["seed"].is_in(flagged)))
    if df["hardware"].n_unique() > 1:
        for h in df["hardware"].unique().to_list():
            conds.append((f"hardware = {h}", df["hardware"] == h))
    return conds


def randomization_seeds_flagged() -> list[dict]:
    s = _seed_stats().filter(pl.col("z") > 3)
    return s.to_dicts()


def _associated_seeds(df: pl.DataFrame, sub: pl.DataFrame, top: int = 3) -> list[dict]:
    """Seeds over-represented in one signature's failures (binomial z vs the seed's share of all runs)."""
    n_sig, n_all = len(sub), len(df)
    if n_sig < 10:
        return []
    share = df.group_by("seed").agg(pl.len().alias("n"))
    inside = sub.group_by("seed").agg(pl.len().alias("k"))
    j = inside.join(share, on="seed")
    out = []
    for r in j.iter_rows(named=True):
        if r["k"] < 5:
            continue
        expected = r["n"] / n_all
        z, p = stats.binom_z(r["k"], n_sig, expected)
        if z > 3:
            out.append({"seed": str(r["seed"]), "failures_with_signature": r["k"], "share_of_signature": _r(r["k"] / n_sig),
                        "expected_share": _r(expected), "lift": _r((r["k"] / n_sig) / expected, 2), "z_score": _r(z, 2)})
    return sorted(out, key=lambda x: -x["z_score"])[:top]


def _associated_environment(df: pl.DataFrame, sub: pl.DataFrame, top: int = 3) -> list[dict]:
    """Randomized variables whose distribution in this signature's failures differs most from passing runs (Cohen's d)."""
    passes = df.filter(pl.col("failed") == 0)
    if len(sub) < 10 or len(passes) < 10:
        return []
    out = []
    for v in _continuous_random():
        a, b = sub[v].cast(pl.Float64).to_numpy(), passes[v].cast(pl.Float64).to_numpy()
        d = stats.cohens_d(a, b)
        if abs(d) >= 0.2:
            out.append({"variable": v, "mean_in_signature": _r(a.mean(), 3), "mean_in_passing_runs": _r(b.mean(), 3), "cohens_d": _r(d, 2)})
    return sorted(out, key=lambda x: -abs(x["cohens_d"]))[:top]


def root_cause() -> dict:
    def build():
        df = store.df
        fails = df.filter(pl.col("failed") == 1)
        conds = _conditions(df)
        det = {r["error_signature"]: r for r in determinism()["by_signature"]}
        cards = []
        for sig, n in fails["error_signature"].value_counts(sort=True).iter_rows():
            mask = (df["error_signature"] == sig)
            lifts = []
            for name, c in conds:
                support = float((c & mask).sum() / n)
                base = float(c.mean())
                if support >= 0.3 and base > 0:
                    lifts.append({"condition": name, "support": _r(support), "lift": _r(support / base, 2)})
            lifts.sort(key=lambda x: -x["lift"])
            lines = fails.filter(pl.col("error_signature") == sig)["log_trace"].head(300).to_list()
            tmpl: dict[str, int] = {}
            for t in lines:
                for ln in t.split("\n"):
                    if _severity(ln) and not ln.startswith("RESULT"):
                        key = log_template(ln)
                        tmpl[key] = tmpl.get(key, 0) + 1
            d = det.get(sig, {"deterministic": 0, "stochastic": 0})
            sub = fails.filter(pl.col("error_signature") == sig)
            assoc_seeds = _associated_seeds(df, sub) if _has_seed() else []
            assoc_env = _associated_environment(df, sub)
            cards.append({
                "signature": sig, "description": SIGNATURES.get(sig) or f"Failure signature observed in {n} runs", "count": n, "share": _r(n / len(fails)),
                "deterministic_share": _r(d["deterministic"] / max(1, d["deterministic"] + d["stochastic"])),
                "top_conditions": lifts[:4], "log_templates": sorted(tmpl, key=lambda k: -tmpl[k])[:3],
                "hardware": sub["hardware"].value_counts(sort=True).head(3).to_dicts(),
                "avg_latency": _r(sub["latency_p99_ms"].mean(), 2),
                "fingerprint": f"FP-{abs(hash(sig + str(lifts[:2]))) % 0xFFFFFF:06X}",
                "tendency": d.get("tendency", "insufficient data"), "associated_seeds": assoc_seeds,
                "associated_environment": assoc_env,
            })
        # global log anomaly template clusters
        tmpl_all: dict[str, dict] = {}
        for sig, trace in fails.select("error_signature", "log_trace").head(4000).iter_rows():
            for ln in trace.split("\n"):
                sev = _severity(ln)
                if sev and not ln.startswith("RESULT"):
                    k = log_template(ln)
                    e = tmpl_all.setdefault(k, {"template": k, "count": 0, "severity": sev, "signatures": {}})
                    e["count"] += 1
                    e["signatures"][sig] = e["signatures"].get(sig, 0) + 1
        anomalies = sorted(tmpl_all.values(), key=lambda e: -e["count"])[:15]
        for e in anomalies:
            e["primary_signature"] = max(e["signatures"], key=e["signatures"].get)
            e["specificity"] = _r(e["signatures"][e["primary_signature"]] / e["count"])
            del e["signatures"]
        return {"fingerprints": cards, "log_anomalies": anomalies}
    return store.cached("rootcause", build)


# --------------------------------------------------------------------------- Q6
def list_runs(outcome: str | None, signature: str | None, q: str | None, limit: int = 60) -> list[dict]:
    df = store.df
    if outcome:
        df = df.filter(pl.col("outcome") == outcome)
    if signature:
        df = df.filter(pl.col("error_signature") == signature)
    if q:
        df = df.filter(pl.col("run_id").str.contains(q.upper()) | pl.col("config_id").str.contains(q.upper()))
    return df.select("run_id", "config_id", "outcome", "error_signature", "hardware", "environment", "workload", "throughput_mbps", "seed") \
             .head(limit).to_dicts()


def suggest_pair(signature: str | None = None) -> dict:
    """Pick a failing run and its most similar passing run (same config if possible)."""
    df = store.df
    fails = df.filter(pl.col("failed") == 1)
    if signature:
        fails = fails.filter(pl.col("error_signature") == signature)
    prof = _profile_stats().filter((pl.col("fail_rate") > 0.2) & (pl.col("fail_rate") < 0.8))
    cand = fails.filter(pl.col("config_id").is_in(prof["config_id"])) if not signature else fails
    rng = np.random.default_rng()
    fail_row = cand.row(int(rng.integers(0, len(cand))), named=True) if len(cand) else fails.row(0, named=True)
    passes = df.filter(pl.col("failed") == 0)
    same = passes.filter(pl.col("config_id") == fail_row["config_id"])
    if len(same):
        pass_row = same.row(0, named=True)
    else:
        key = [p["name"] for p in store.meta["key_params"]]
        dist = sum((passes[k] != fail_row[k]).cast(pl.Int32) for k in key)
        pass_row = passes.row(int(dist.arg_min()), named=True)
    return {"run_a": pass_row["run_id"], "run_b": fail_row["run_id"]}


def diff(run_a: str, run_b: str) -> dict:
    df = store.df
    a = df.filter(pl.col("run_id") == run_a)
    b_ = df.filter(pl.col("run_id") == run_b)
    if not len(a) or not len(b_):
        raise KeyError("run not found")
    a, b = a.row(0, named=True), b_.row(0, named=True)
    bundle = store.bundle
    imp = bundle.importance_full

    def section(cols):
        out = []
        for c in cols:
            va, vb = a[c], b[c]
            changed = va != vb
            delta = pct = None
            if changed and isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                delta = _r(vb - va, 3)
                pct = _r((vb - va) / abs(va) * 100, 1) if va else None
            out.append({"key": c, "a": va, "b": vb, "changed": changed, "delta": delta, "pct": pct, "importance": _r(imp.get(c, 0))})
        return out

    cfg = section(store.meta["config_params"])
    rnd = section(store.meta["random_vars"])
    tel = section(store.meta["telemetry"])
    ctx = section(store.meta["context"])
    # change impact: SHAP delta between the two configurations on the pre-execution risk model
    pre_cols = bundle.pre_enc.columns
    sa = {s["feature"]: s["contribution"] for s in bundle.shap({c: a[c] for c in pre_cols}, top=len(pre_cols))}
    sb = {s["feature"]: s["contribution"] for s in bundle.shap({c: b[c] for c in pre_cols}, top=len(pre_cols))}
    impact = sorted(({"feature": c, "a": a[c], "b": b[c], "shap_delta": _r(sb.get(c, 0) - sa.get(c, 0))} for c in pre_cols if a[c] != b[c]),
                    key=lambda x: -abs(x["shap_delta"]))
    pa, pb = bundle.predict([{c: a[c] for c in pre_cols}, {c: b[c] for c in pre_cols}])["risk"]

    la, lb = a["log_trace"].split("\n"), b["log_trace"].split("\n")
    sm = difflib.SequenceMatcher(a=[log_template(x) for x in la], b=[log_template(x) for x in lb])
    log_rows = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            log_rows += [{"op": "equal", "a": la[i1 + k], "b": lb[j1 + k]} for k in range(i2 - i1)]
        else:
            for k in range(max(i2 - i1, j2 - j1)):
                lna = la[i1 + k] if i1 + k < i2 else None
                lnb = lb[j1 + k] if j1 + k < j2 else None
                log_rows.append({"op": op, "a": lna, "b": lnb, "anomalous": bool(lnb and (_severity(lnb) or lnb.startswith("RESULT FAIL")))})
    return {
        "run_a": {k: a[k] for k in ("run_id", "config_id", "outcome", "error_signature", "timestamp")},
        "run_b": {k: b[k] for k in ("run_id", "config_id", "outcome", "error_signature", "timestamp")},
        "predicted_risk": {"a": _r(pa), "b": _r(pb)},
        "config": sorted(cfg, key=lambda r: (not r["changed"], -r["importance"])),
        "random": sorted(rnd, key=lambda r: (not r["changed"], -r["importance"])),
        "telemetry": tel, "context": ctx, "change_impact": impact[:12], "logs": log_rows,
        "summary": {"config_changes": sum(r["changed"] for r in cfg), "random_changes": sum(r["changed"] for r in rnd)},
    }
