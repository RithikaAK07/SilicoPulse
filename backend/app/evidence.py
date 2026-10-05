"""Evidence core: filtered views, learned thresholds and per-parameter risk statistics.

Every function works on whatever dataset is active (benchmark or uploaded CSV) and on any
dashboard filter (hardware / environment / workload / date range / signature / config / seed).
Nothing here encodes findings about the benchmark: thresholds, risky values and significance
are all derived from counts in the data.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import numpy as np
import polars as pl

from . import stats
from .config import MIN_GROUP_SAMPLES, SIGNIFICANCE_ALPHA
from .store import store

FILTER_KEYS = ("environment", "hardware", "workload", "date_from", "date_to", "signature", "config_id", "seed")


def _r(x, n=4):
    return None if x is None else round(float(x), n)


def clean_filters(f: dict | None) -> dict:
    return {k: v for k, v in (f or {}).items() if k in FILTER_KEYS and v not in (None, "", [])}


def filter_key(f: dict | None) -> str:
    return json.dumps(clean_filters(f), sort_keys=True, default=str)


def apply_filters(df: pl.DataFrame, f: dict | None) -> pl.DataFrame:
    f = clean_filters(f)
    for col in ("environment", "hardware", "workload"):
        if col in f:
            df = df.filter(pl.col(col) == str(f[col]))
    if "config_id" in f:
        df = df.filter(pl.col("config_id") == str(f["config_id"]).upper())
    if "seed" in f:
        df = df.filter(pl.col("seed") == int(f["seed"]))
    if "date_from" in f:
        df = df.filter(pl.col("timestamp") >= datetime.fromisoformat(str(f["date_from"])[:10]))
    if "date_to" in f:
        df = df.filter(pl.col("timestamp") < datetime.fromisoformat(str(f["date_to"])[:10]) + timedelta(days=1))
    if "signature" in f:  # failure-type focus: that signature's failures vs all passing runs
        df = df.filter((pl.col("failed") == 0) | (pl.col("error_signature") == str(f["signature"]).upper()))
    return df


def frame(f: dict | None = None) -> pl.DataFrame:
    key = filter_key(f)
    if key == "{}":
        return store.df
    return store.cached(f"frame::{key}", lambda: apply_filters(store.df, f))


def baseline(df: pl.DataFrame) -> tuple[int, int, float]:
    n = len(df)
    k = int(df["failed"].sum()) if n else 0
    return k, n, (k / n if n else 0.0)


def continuous_random(df: pl.DataFrame | None = None) -> list[str]:
    df = store.df if df is None else df
    synth = set(store.meta.get("synthetic_columns", []))
    return [c for c in store.meta["random_vars"]
            if c != "seed" and c not in synth and c in df.columns and df[c].dtype.is_numeric() and df[c].n_unique() >= 6]


# --------------------------------------------------------------------------- learned thresholds
def learned_threshold(df: pl.DataFrame, col: str, tests: int = 1) -> dict | None:
    """Scan candidate cut points (5th-95th percentile) and keep the one that best separates
    failure rates (largest |z| of a two-proportion test). Direction says which side is riskier."""
    if col not in df.columns or len(df) < 2 * MIN_GROUP_SAMPLES:
        return None
    v = df[col].cast(pl.Float64).to_numpy()
    y = df["failed"].to_numpy()
    k, n = int(y.sum()), len(y)
    if k == 0 or k == n:
        return None
    cuts = np.unique(np.quantile(v, np.arange(0.05, 0.96, 0.05)))
    order = np.argsort(v, kind="stable")
    vs, ys = v[order], np.cumsum(y[order])
    best = None
    for t in cuts:
        idx = int(np.searchsorted(vs, t, side="right"))  # rows with v <= t
        n_lo, k_lo = idx, int(ys[idx - 1]) if idx else 0
        n_hi, k_hi = n - n_lo, k - k_lo
        if n_lo < MIN_GROUP_SAMPLES or n_hi < MIN_GROUP_SAMPLES:
            continue
        z, p = stats.two_prop_z(k_hi, n_hi, k_lo, n_lo)
        if best is None or abs(z) > abs(best[1]):
            best = (float(t), z, p, n_lo, k_lo, n_hi, k_hi)
    if best is None:
        return None
    t, z, p, n_lo, k_lo, n_hi, k_hi = best
    above = z > 0
    nr, kr, ns, ks = (n_hi, k_hi, n_lo, k_lo) if above else (n_lo, k_lo, n_hi, k_hi)
    fr_r, fr_s, base = kr / nr, ks / ns, k / n
    total_tests = tests * len(cuts)
    strength = stats.evidence_strength(p, min(nr, ns), MIN_GROUP_SAMPLES, total_tests, SIGNIFICANCE_ALPHA)
    lo, hi = stats.wilson(kr, nr)
    return {
        "variable": col, "threshold": _r(t, 4), "direction": "above" if above else "below",
        "condition": f"{col} {'>' if above else '<='} {t:.4g}",
        "risky_runs": nr, "risky_failures": kr, "risky_failure_rate": _r(fr_r), "risky_ci95": [_r(lo), _r(hi)],
        "safe_runs": ns, "safe_failure_rate": _r(fr_s), "baseline_failure_rate": _r(base),
        "lift": _r(fr_r / base if base else 0, 3), "risk_ratio": _r(fr_r / fr_s if fr_s else float("inf"), 3),
        "z_score": _r(abs(z), 2), "p_value": float(f"{p:.3g}"), "thresholds_tested": total_tests,
        "evidence_strength": strength, "source": "learned from data (max two-proportion z over percentile cut points)",
    }


def environment_thresholds(f: dict | None = None) -> list[dict]:
    def build():
        df = frame(f)
        cols = continuous_random(df)
        out = [t for c in cols if (t := learned_threshold(df, c, tests=len(cols)))]
        return sorted(out, key=lambda r: -r["z_score"])
    return store.cached(f"envthr::{filter_key(f)}", build)


def threshold_for(var: str) -> dict | None:
    """Global learned risk threshold for one variable (None if no significant separation)."""
    for t in environment_thresholds(None):
        if t["variable"] == var:
            return t
    return None


def time_drift(df: pl.DataFrame, var: str, thr: dict | None) -> dict:
    """Early (first 25% of the timeline) vs late (last 25%) comparison of a variable."""
    d = df.select("timestamp", var, "failed").sort("timestamp")
    n = len(d)
    if n < 4 * MIN_GROUP_SAMPLES:
        return {}
    q = n // 4
    early, late = d.head(q), d.tail(q)
    out = {"early_mean": _r(early[var].mean(), 3), "late_mean": _r(late[var].mean(), 3),
           "early_failure_rate": _r(early["failed"].mean()), "late_failure_rate": _r(late["failed"].mean())}
    z, p = stats.two_prop_z(int(late["failed"].sum()), q, int(early["failed"].sum()), q)
    out.update({"failure_rate_change_z": _r(z, 2), "failure_rate_change_p": float(f"{p:.3g}")})
    if thr:
        risky = (lambda s: s > thr["threshold"]) if thr["direction"] == "above" else (lambda s: s <= thr["threshold"])
        out["early_share_beyond_threshold"] = _r(risky(early[var]).mean())
        out["late_share_beyond_threshold"] = _r(risky(late[var]).mean())
    return out


# --------------------------------------------------------------------------- parameter risk
def value_groups(df: pl.DataFrame, col: str) -> pl.DataFrame:
    """value | n | k, with high-cardinality numerics bucketed into quintiles."""
    s = df[col]
    if s.dtype.is_numeric() and s.n_unique() > 24:
        qs = np.unique(np.quantile(s.cast(pl.Float64).to_numpy(), [0.2, 0.4, 0.6, 0.8]))
        labels = [f"<= {qs[0]:.4g}"] + [f"({a:.4g}, {b:.4g}]" for a, b in zip(qs[:-1], qs[1:])] + [f"> {qs[-1]:.4g}"]
        g = df.select(s.cut(list(qs), labels=labels).cast(pl.Utf8).alias("value"), "failed")
    else:
        g = df.select(s.alias("value"), "failed")
    return g.group_by("value").agg(pl.len().alias("n"), pl.col("failed").sum().alias("k")).sort("value")


def parameter_risk(f: dict | None = None, top: int = 20) -> dict:
    def build():
        df = frame(f)
        b = store.bundle
        k0, n0, base = baseline(df)
        cfg = store.meta["config_params"]
        imp_tot = sum(b.importance_full[c] for c in cfg) or 1
        ranked = sorted(cfg, key=lambda c: -b.importance_full.get(c, 0))[:top]
        rows = []
        for c in ranked:
            g = value_groups(df, c)
            table = np.column_stack([g["k"].to_numpy(), (g["n"] - g["k"]).to_numpy()]).astype(float)
            chi2, p, v = stats.chi2_independence(table)
            vals = []
            for r in g.iter_rows(named=True):
                if r["n"] < MIN_GROUP_SAMPLES:
                    continue
                z, pv = stats.two_prop_z(r["k"], r["n"], k0 - r["k"], n0 - r["n"])
                vals.append({"value": r["value"], "runs": r["n"], "failures": r["k"], "failure_rate": _r(r["k"] / r["n"]),
                             "lift": _r((r["k"] / r["n"]) / base if base else 0, 3), "z_score": _r(z, 2), "p_value": float(f"{pv:.3g}")})
            if not vals:
                continue
            worst = max(vals, key=lambda x: x["failure_rate"])
            safest = min(vals, key=lambda x: x["failure_rate"])
            strength = stats.evidence_strength(p, min(worst["runs"], n0 - worst["runs"]), MIN_GROUP_SAMPLES, len(ranked), SIGNIFICANCE_ALPHA)
            rows.append({
                "parameter": c, "importance": _r(b.importance_full.get(c, 0) / imp_tot), "correlation": b.correlation.get(c),
                "mutual_information": b.mutual_info.get(c), "chi2_p_value": float(f"{p:.3g}"), "cramers_v": _r(v, 3),
                "failure_rate_spread": _r(worst["failure_rate"] - safest["failure_rate"]),
                "riskiest_value": worst, "safest_value": safest, "values": vals[:24], "evidence_strength": strength,
            })
        return {"baseline_failure_rate": _r(base), "runs": n0, "min_group_samples": MIN_GROUP_SAMPLES, "parameters": rows,
                "note": "Associations, not proven causes: values are compared with all other runs (two-proportion z, chi-square)."}
    return store.cached(f"paramrisk::{filter_key(f)}::{top}", build)
