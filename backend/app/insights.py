"""Structured insight engine: evidence-backed findings, guardrails, hierarchies and data quality.

Python computes every statistic; the Copilot / UI only present them. Each insight follows:

    {title, category, severity, finding, evidence{sample_size, failure_rate, baseline_failure_rate,
     lift, correlation, importance, ci95, p_value, z_score, ...}, affected_parameters,
     failure_signatures, supporting_runs, recommendation, explanation, evidence_strength}

Language is associational ("associated with", "elevated failure risk"): no causal method is
implemented, so no insight claims causation.
"""
from __future__ import annotations

import math
from itertools import combinations

import numpy as np
import polars as pl

from . import analytics, evidence, stats
from .config import MIN_GROUP_SAMPLES, MIN_PAIR_SAMPLES, SIGNIFICANCE_ALPHA
from .evidence import _r, baseline, filter_key, frame
from .store import store

STRENGTH_WEIGHT = {"strong": 3, "moderate": 2, "weak": 1, "not significant": 0, "insufficient": 0}


def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _key_params(top: int | None = None) -> list[str]:
    names = [p["name"] for p in store.meta["key_params"]]
    return names[:top] if top else names


def _min_pair(df: pl.DataFrame) -> int:
    return MIN_PAIR_SAMPLES or max(15, min(60, len(df) // 170))


# --------------------------------------------------------------------------- toxic / best pairs
def toxic_pairs(f: dict | None = None, top_params: int = 10) -> dict:
    """Two-parameter combinations whose failure rate departs from the baseline, with support guards."""
    def build():
        df = frame(f)
        k0, n0, base = baseline(df)
        if n0 == 0:
            return {"baseline_failure_rate": None, "min_support": 0, "toxic": [], "best": []}
        min_n = _min_pair(df)
        params = _key_params(top_params)
        marg = {c: {r[c]: (r["k"], r["n"]) for r in df.group_by(c).agg(pl.len().alias("n"), pl.col("failed").sum().alias("k")).iter_rows(named=True)}
                for c in params}
        cells, tested = [], 0
        for a, b in combinations(params, 2):
            g = df.group_by([a, b]).agg(pl.len().alias("n"), pl.col("failed").sum().alias("k"),
                                        pl.col("throughput_mbps").filter(pl.col("failed") == 0).mean().alias("tp"))
            tested += len(g)
            for r in g.filter(pl.col("n") >= min_n).iter_rows(named=True):
                k, n = int(r["k"]), int(r["n"])
                z, p = stats.two_prop_z(k, n, k0 - k, n0 - n)
                ma, mb = marg[a][r[a]], marg[b][r[b]]
                m = max(ma[0] / ma[1], mb[0] / mb[1])
                cells.append({"a": a, "a_val": r[a], "b": b, "b_val": r[b], "condition": f"{a}={r[a]} AND {b}={r[b]}",
                              "runs": n, "failures": k, "failure_rate": _r(k / n), "baseline_failure_rate": _r(base),
                              "lift": _r((k / n) / base if base else 0, 3), "odds_ratio": _r(stats.odds_ratio(k, n, k0 - k, n0 - n), 3),
                              "interaction_lift": _r((k / n) / m if m else 0, 3), "marginal_failure_rates": {a: _r(ma[0] / ma[1]), b: _r(mb[0] / mb[1])},
                              "z_score": _r(z, 2), "p_value": float(f"{p:.3g}"), "ci95": [_r(x) for x in stats.wilson(k, n)],
                              "throughput": _r(r["tp"], 1)})
        for c in cells:
            c["evidence_strength"] = stats.evidence_strength(c["p_value"], c["runs"], min_n, tested, SIGNIFICANCE_ALPHA)
        toxic = sorted((c for c in cells if c["lift"] >= 1.3 and c["z_score"] > 0), key=lambda c: (-STRENGTH_WEIGHT[c["evidence_strength"]], -c["lift"]))
        best = sorted((c for c in cells if c["z_score"] < 0), key=lambda c: (c["failure_rate"], -(c["throughput"] or 0)))
        return {"baseline_failure_rate": _r(base), "runs": n0, "min_support": min_n, "combinations_tested": tested,
                "correction": "Bonferroni over all tested combinations", "toxic": toxic[:15], "best": best[:10]}
    return store.cached(f"pairs2::{filter_key(f)}::{top_params}", build)


# --------------------------------------------------------------------------- failure signature hierarchy
def failure_signatures(f: dict | None = None) -> dict:
    def build():
        df = frame(f)
        fails = df.filter(pl.col("failed") == 1)
        if not len(fails):
            return {"total_failures": 0, "signatures": []}
        det = {r["error_signature"]: r for r in analytics.determinism()["by_signature"]}
        logs = {c["signature"]: c["log_templates"] for c in analytics.root_cause()["fingerprints"]}
        conds = analytics._conditions(df)
        hw_share = {r["hardware"]: r["n"] / len(df) for r in df.group_by("hardware").agg(pl.len().alias("n")).iter_rows(named=True)}
        out = []
        for sig, n in fails["error_signature"].value_counts(sort=True).iter_rows():
            mask = df["error_signature"] == sig
            sub = fails.filter(pl.col("error_signature") == sig)
            params = []
            for name, c in conds:
                support = float((c & mask).sum() / n)
                base = float(c.mean())
                if support >= 0.3 and base > 0:
                    params.append({"condition": name, "support": _r(support), "lift": _r(support / base, 2)})
            params.sort(key=lambda x: -x["lift"])
            hw = [{"hardware": r["hardware"], "failures": r["n"], "share": _r(r["n"] / n), "lift": _r((r["n"] / n) / hw_share[r["hardware"]], 2)}
                  for r in sub.group_by("hardware").agg(pl.len().alias("n")).sort("n", descending=True).iter_rows(named=True)]
            d = det.get(sig, {})
            out.append({
                "signature": sig, "failures": n, "share_of_failures": _r(n / len(fails)), "ci95_share": [_r(x) for x in stats.wilson(n, len(fails))],
                "tendency": d.get("tendency", "insufficient data"), "deterministic_share": d.get("deterministic_share"),
                "associated_parameters": params[:5],
                "associated_environment": analytics._associated_environment(df, sub),
                "associated_randomization": analytics._associated_seeds(df, sub) if analytics._has_seed() else [],
                "hardware": hw[:4], "log_patterns": logs.get(sig, [])[:3],
                "supporting_runs": {"count": n, "examples": sub["run_id"].head(3).to_list()},
            })
        return {"total_failures": len(fails), "runs": len(df), "signatures": out,
                "hierarchy": "failure -> signature -> associated parameters -> environment -> randomization -> supporting runs"}
    return store.cached(f"sigs::{filter_key(f)}", build)


# --------------------------------------------------------------------------- hardware comparison
def hardware_comparison(f: dict | None = None) -> dict:
    def build():
        df = frame(f)
        k0, n0, base = baseline(df)
        synth = set(store.meta.get("synthetic_columns", []))
        metrics = [m for m in ("latency_p99_ms", "cpu_util", "mem_util", "instability_index") if m not in synth]
        duration = [c for c in store.meta.get("telemetry", []) if any(t in c for t in ("duration", "elapsed", "exec_time", "runtime"))]
        params = _key_params(6)
        rows = []
        for hw in sorted(df["hardware"].unique().to_list()):
            sub = df.filter(pl.col("hardware") == hw)
            k, n = int(sub["failed"].sum()), len(sub)
            z, p = stats.two_prop_z(k, n, k0 - k, n0 - n)
            fails = sub.filter(pl.col("failed") == 1)
            sigs = [{"signature": s_, "share": _r(c / max(1, len(fails)))} for s_, c in fails["error_signature"].value_counts(sort=True).head(3).iter_rows()]
            sens = []
            for c in params:
                g = sub.group_by(c).agg(pl.len().alias("n"), pl.col("failed").mean().alias("fr")).filter(pl.col("n") >= MIN_GROUP_SAMPLES)
                if len(g) >= 2:
                    worst = g.sort("fr", descending=True).row(0, named=True)
                    sens.append({"parameter": c, "failure_rate_spread": _r(g["fr"].max() - g["fr"].min()), "riskiest_value": worst[c], "riskiest_failure_rate": _r(worst["fr"])})
            sens.sort(key=lambda x: -x["failure_rate_spread"])
            rows.append({
                "hardware": hw, "runs": n, "failures": k, "failure_rate": _r(k / n if n else 0), "ci95": [_r(x) for x in stats.wilson(k, n)],
                "vs_rest_z": _r(z, 2), "vs_rest_p": float(f"{p:.3g}"),
                "evidence_strength": stats.evidence_strength(p, n, MIN_GROUP_SAMPLES, max(1, df["hardware"].n_unique()), SIGNIFICANCE_ALPHA),
                "mean_throughput_pass": _r(sub.filter(pl.col("failed") == 0)["throughput_mbps"].mean(), 1) if "throughput_mbps" not in synth else None,
                **{f"mean_{m}": _r(sub[m].mean(), 3) for m in metrics},
                "mean_execution_time": _r(sub[duration[0]].mean(), 3) if duration else None,
                "dominant_signatures": sigs, "config_sensitivity": sens[:3],
            })
        # same configurations on different hardware: compare failure rates within shared profiles
        prof = df.group_by("config_id", "hardware").agg(pl.len().alias("n"), pl.col("failed").mean().alias("fr")).filter(pl.col("n") >= 3)
        shared_ids = prof.group_by("config_id").agg(pl.len().alias("tiers")).filter(pl.col("tiers") >= 2)["config_id"]
        shared = prof.filter(pl.col("config_id").is_in(shared_ids))
        matched = {r["hardware"]: _r(r["fr"]) for r in shared.group_by("hardware").agg(pl.col("fr").mean()).iter_rows(named=True)}
        for r in rows:
            r["matched_profile_failure_rate"] = matched.get(r["hardware"])
        # do toxic combinations stay elevated across tiers?
        cross = []
        for t in toxic_pairs(f)["toxic"][:3]:
            elevated = []
            for hw in sorted(df["hardware"].unique().to_list()):
                sub = df.filter(pl.col("hardware") == hw)
                cell = sub.filter((pl.col(t["a"]) == t["a_val"]) & (pl.col(t["b"]) == t["b_val"]))
                _, _, hb = baseline(sub)
                if len(cell) >= max(10, MIN_GROUP_SAMPLES // 3) and hb:
                    lift = cell["failed"].mean() / hb
                    elevated.append({"hardware": hw, "runs": len(cell), "failure_rate": _r(cell["failed"].mean()), "lift": _r(lift, 2)})
            cross.append({"condition": t["condition"], "tiers_checked": len(elevated),
                          "tiers_elevated": sum(1 for e in elevated if e["lift"] >= 1.3), "per_tier": elevated})
        least = max(rows, key=lambda r: r["failure_rate"]) if rows else None
        return {"baseline_failure_rate": _r(base), "hardware": rows, "least_reliable": least and least["hardware"],
                "shared_profiles": int(len(shared_ids)), "cross_tier_patterns": cross,
                "execution_time_available": bool(duration),
                "note": "Differences are associations; matched_profile_failure_rate compares tiers on the same configurations."}
    return store.cached(f"hw::{filter_key(f)}", build)


# --------------------------------------------------------------------------- guardrails
def _safer(df: pl.DataFrame, fixed: str, fixed_val, vary: str, min_n: int) -> dict | None:
    g = (df.filter(pl.col(fixed) == fixed_val).group_by(vary)
           .agg(pl.len().alias("n"), pl.col("failed").mean().alias("fr")).filter(pl.col("n") >= min_n).sort("fr"))
    if not len(g):
        return None
    r = g.row(0, named=True)
    return {"condition": f"{fixed}={fixed_val} AND {vary}={r[vary]}", "failure_rate": _r(r["fr"]), "runs": r["n"]}


def guardrails(f: dict | None = None, limit: int = 12) -> dict:
    """Candidate guardrails discovered from data (never hard-coded rules)."""
    def build():
        df = frame(f)
        k0, n0, base = baseline(df)
        cands = []
        for t in toxic_pairs(f)["toxic"]:
            if t["evidence_strength"] not in ("strong", "moderate"):
                continue
            alt = _safer(df, t["a"], t["a_val"], t["b"], max(10, _min_pair(df) // 2)) or _safer(df, t["b"], t["b_val"], t["a"], max(10, _min_pair(df) // 2))
            cands.append({"type": "parameter_combination", "condition": t["condition"],
                          "conditions": [{"parameter": t["a"], "op": "=", "value": t["a_val"]}, {"parameter": t["b"], "op": "=", "value": t["b_val"]}],
                          **{k: t[k] for k in ("runs", "failures", "failure_rate", "baseline_failure_rate", "lift", "odds_ratio", "ci95", "p_value", "z_score", "evidence_strength")},
                          "safer_alternative": alt})
        for prm in evidence.parameter_risk(f)["parameters"]:
            w = prm["riskiest_value"]
            strength = stats.evidence_strength(w["p_value"], w["runs"], MIN_GROUP_SAMPLES, 200, SIGNIFICANCE_ALPHA)
            if w["lift"] >= 1.4 and strength in ("strong", "moderate"):
                s = prm["safest_value"]
                cands.append({"type": "parameter_value", "condition": f"{prm['parameter']}={w['value']}",
                              "conditions": [{"parameter": prm["parameter"], "op": "=", "value": w["value"]}],
                              "runs": w["runs"], "failures": w["failures"], "failure_rate": w["failure_rate"], "baseline_failure_rate": _r(base),
                              "lift": w["lift"], "ci95": [_r(x) for x in stats.wilson(w["failures"], w["runs"])], "p_value": w["p_value"],
                              "z_score": w["z_score"], "evidence_strength": strength,
                              "safer_alternative": {"condition": f"{prm['parameter']}={s['value']}", "failure_rate": s["failure_rate"], "runs": s["runs"]}})
        for t in evidence.environment_thresholds(f):
            if t["evidence_strength"] in ("strong", "moderate") and t["lift"] >= 1.2:
                cands.append({"type": "environment_threshold", "condition": t["condition"],
                              "conditions": [{"parameter": t["variable"], "op": ">" if t["direction"] == "above" else "<=", "value": t["threshold"]}],
                              "runs": t["risky_runs"], "failures": t["risky_failures"], "failure_rate": t["risky_failure_rate"],
                              "baseline_failure_rate": t["baseline_failure_rate"], "lift": t["lift"], "ci95": t["risky_ci95"],
                              "p_value": t["p_value"], "z_score": t["z_score"], "evidence_strength": t["evidence_strength"],
                              "safer_alternative": {"condition": f"{t['variable']} {'<=' if t['direction'] == 'above' else '>'} {t['threshold']:.4g}",
                                                    "failure_rate": t["safe_failure_rate"], "runs": t["safe_runs"]}})
        for c in cands:
            c["severity"] = stats.severity(c["failure_rate"], c["lift"], c["evidence_strength"])
            alt = c.get("safer_alternative")
            verb = "Review or restrict" if c["severity"] in ("critical", "high") else "Monitor"
            c["recommendation"] = (f"{verb} {c['condition']}: observed failure rate {_pct(c['failure_rate'])} vs baseline "
                                   f"{_pct(c['baseline_failure_rate'])} ({c['lift']}x, n={c['runs']:,}, {c['evidence_strength']} evidence)."
                                   + (f" Observed alternative: {alt['condition']} at {_pct(alt['failure_rate'])} (n={alt['runs']:,})." if alt else ""))
            c["score"] = round(c["lift"] * math.log10(max(c["runs"], 10)) * STRENGTH_WEIGHT[c["evidence_strength"]], 3)
        cands.sort(key=lambda c: -c["score"])
        for i, c in enumerate(cands[:limit]):
            c["id"] = f"GR-{i + 1:02d}"
        return {"baseline_failure_rate": _r(base), "runs": n0, "guardrails": cands[:limit],
                "note": "Guardrails are discovered from observed associations (support + Bonferroni-corrected significance), not hard-coded rules."}
    return store.cached(f"guard::{filter_key(f)}::{limit}", build)


# --------------------------------------------------------------------------- data quality / trust
def data_quality() -> dict:
    def build():
        df, m = store.df, store.meta
        synth = set(m.get("synthetic_columns", []))
        uploaded = m.get("source") == "uploaded"
        k, n, base = baseline(df)
        num = [c for c in m["config_params"] + m["random_vars"] + m["telemetry"] if c in df.columns and df[c].dtype.is_numeric() and c not in synth]
        invalid = {}
        for c in ("throughput_mbps", "latency_p99_ms", "iops", "retry_count"):
            if c in df.columns and c not in synth:
                bad = int((df[c] < 0).sum())
                if bad:
                    invalid[c] = bad
        for c in ("cpu_util", "mem_util"):
            if c in df.columns and c not in synth:
                bad = int(((df[c] < 0) | (df[c] > 100)).sum())
                if bad:
                    invalid[c] = bad
        dup_rows = int(df.select(pl.exclude("run_id", "log_trace")).is_duplicated().sum())
        ts = df["timestamp"]
        warnings = []
        if "seed" in synth:
            warnings.append("Seed analysis unavailable: random seed field not present.")
        if "throughput_mbps" in synth:
            warnings.append("Performance analysis unavailable: no performance metric was mapped.")
        if "timestamp" in synth:
            warnings.append("Timestamps not provided: run order is used as a proxy, so time-based drift is not meaningful.")
        if {"environment", "hardware", "workload"} & synth:
            warnings.append(f"Context fields not provided ({', '.join(sorted({'environment', 'hardware', 'workload'} & synth))}): related filters/comparisons are limited.")
        if n < 1000:
            warnings.append(f"Small dataset ({n:,} runs): confidence intervals are wide and many subgroups fall below minimum-support thresholds.")
        if m.get("leakage_dropped"):
            warnings.append(f"Dropped label-leaking column(s): {', '.join(m['leakage_dropped'])}.")
        provenance = ("SYNTHETIC BENCHMARK: every field is generated by the built-in simulator" if not uploaded
                      else "REAL DATA from the uploaded CSV; fields listed under 'derived' were not in the file and use neutral defaults")
        return {
            "source": "uploaded" if uploaded else "benchmark", "provenance": provenance, "filename": m.get("filename"),
            "total_executions": n, "successful_executions": n - k, "failures": k, "failure_rate": _r(base),
            "configuration_parameters": len(m["config_params"]),
            "randomized_variables": len([c for c in m["random_vars"] if c not in synth]),
            "telemetry_variables": len([c for c in m["telemetry"] if c not in synth]),
            "missing_values": m.get("missing_values", {}) if uploaded else {},
            "missing_values_total": int(sum((m.get("missing_values") or {}).values())) if uploaded else 0,
            "missing_values_note": None if uploaded and "missing_values" in m else ("generated data is complete by construction" if not uploaded else "not recorded for this upload (re-upload to measure)"),
            "duplicate_run_ids": int(n - df["run_id"].n_unique()), "duplicate_rows": dup_rows, "invalid_values": invalid,
            "log_coverage": m.get("log_coverage", 1.0 if not uploaded else None),
            "log_source": "generated" if not uploaded else ("uploaded" if m.get("log_coverage") else "derived summaries (no log column mapped)"),
            "timestamp_coverage": 0.0 if "timestamp" in synth else _r(1 - ts.null_count() / max(n, 1)),
            "timestamp_source": "derived (row order)" if "timestamp" in synth else ("generated" if not uploaded else "uploaded"),
            "time_span": {"start": str(ts.min()), "end": str(ts.max()), "days": int(ts.dt.date().n_unique())},
            "real_fields": [] if not uploaded else sorted(set(m["config_params"] + m["random_vars"] + m["telemetry"] + ["outcome"]) - synth),
            "derived_fields": sorted(synth), "numeric_fields_checked": len(num), "warnings": warnings,
        }
    return store.cached("dataquality", build)


# --------------------------------------------------------------------------- insights
def _insight(**kw) -> dict:
    kw.setdefault("affected_parameters", [])
    kw.setdefault("failure_signatures", [])
    kw["severity"] = kw.get("severity") or "low"
    return kw


def insights(f: dict | None = None) -> dict:
    def build():
        df = frame(f)
        k0, n0, base = baseline(df)
        out: list[dict] = []
        if n0 == 0:
            return {"filters": evidence.clean_filters(f), "runs": 0, "insights": [], "note": "No executions match the filters."}
        # configuration: strongest failure-associated parameter
        pr = evidence.parameter_risk(f)["parameters"]
        if pr:
            p = pr[0]
            w = p["riskiest_value"]
            out.append(_insight(
                title=f"{p['parameter']} is the strongest observed failure-associated parameter", category="configuration",
                severity=stats.severity(w["failure_rate"], w["lift"], p["evidence_strength"]), evidence_strength=p["evidence_strength"],
                finding=(f"{p['parameter']} ranks #1 by model importance; runs with {p['parameter']}={w['value']} fail at {_pct(w['failure_rate'])} "
                         f"vs {_pct(base)} overall ({w['lift']}x)."),
                evidence={"sample_size": w["runs"], "failures": w["failures"], "failure_rate": w["failure_rate"], "baseline_failure_rate": _r(base),
                          "lift": w["lift"], "importance": p["importance"], "correlation": p["correlation"], "cramers_v": p["cramers_v"],
                          "p_value": p["chi2_p_value"], "z_score": w["z_score"], "ci95": [_r(x) for x in stats.wilson(w["failures"], w["runs"])]},
                affected_parameters=[p["parameter"]], supporting_runs=w["runs"],
                recommendation=f"Prefer {p['parameter']}={p['safest_value']['value']} (observed {_pct(p['safest_value']['failure_rate'])}, n={p['safest_value']['runs']:,}) where requirements allow.",
                explanation="Importance from the Random Forest; value-level rates compared with all other runs (two-proportion z, chi-square). Association, not proven cause."))
        # configuration: most dangerous combination
        tp = [t for t in toxic_pairs(f)["toxic"] if t["evidence_strength"] in ("strong", "moderate")]
        if tp:
            t = tp[0]
            out.append(_insight(
                title=f"High-risk combination: {t['condition']}", category="configuration",
                severity=stats.severity(t["failure_rate"], t["lift"], t["evidence_strength"]), evidence_strength=t["evidence_strength"],
                finding=(f"{t['condition']} fails {_pct(t['failure_rate'])} of {t['runs']:,} runs vs {_pct(base)} baseline ({t['lift']}x); "
                         f"{t['interaction_lift']}x higher than either setting alone."),
                evidence={"sample_size": t["runs"], "failures": t["failures"], "failure_rate": t["failure_rate"], "baseline_failure_rate": t["baseline_failure_rate"],
                          "lift": t["lift"], "odds_ratio": t["odds_ratio"], "interaction_lift": t["interaction_lift"], "p_value": t["p_value"],
                          "z_score": t["z_score"], "ci95": t["ci95"]},
                affected_parameters=[t["a"], t["b"]], supporting_runs=t["runs"],
                recommendation=f"Review or restrict {t['condition']} in the test matrix.",
                explanation=f"Pairs need >= {toxic_pairs(f)['min_support']} runs; significance is Bonferroni-corrected over all tested combinations."))
        # environment: strongest learned threshold + drift over time
        env = evidence.environment_thresholds(f)
        if env and env[0]["evidence_strength"] in ("strong", "moderate"):
            e = env[0]
            drift = evidence.time_drift(df, e["variable"], e)
            out.append(_insight(
                title=f"Observed risk threshold: {e['condition']}", category="environment",
                severity=stats.severity(e["risky_failure_rate"], e["lift"], e["evidence_strength"]), evidence_strength=e["evidence_strength"],
                finding=(f"Runs with {e['condition']} fail at {_pct(e['risky_failure_rate'])} vs {_pct(e['safe_failure_rate'])} otherwise "
                         f"({e['risk_ratio']}x); threshold learned from the data."
                         + (f" Share of runs beyond it moved from {_pct(drift.get('early_share_beyond_threshold'))} (first quarter) to "
                            f"{_pct(drift.get('late_share_beyond_threshold'))} (last quarter)." if drift.get("late_share_beyond_threshold") is not None else "")),
                evidence={"sample_size": e["risky_runs"], "failure_rate": e["risky_failure_rate"], "baseline_failure_rate": e["baseline_failure_rate"],
                          "lift": e["lift"], "risk_ratio": e["risk_ratio"], "p_value": e["p_value"], "z_score": e["z_score"], "ci95": e["risky_ci95"],
                          "threshold": e["threshold"], "thresholds_tested": e["thresholds_tested"], **drift},
                affected_parameters=[e["variable"]], supporting_runs=e["risky_runs"],
                recommendation=f"Keep {e['variable']} {'at or below' if e['direction'] == 'above' else 'above'} {e['threshold']:.4g} or monitor it as a test precondition.",
                explanation=e["source"]))
        if "timestamp" not in set(store.meta.get("synthetic_columns", [])) and n0 >= 8 * MIN_GROUP_SAMPLES:
            d = df.sort("timestamp")
            q = n0 // 4
            ke, kl = int(d.head(q)["failed"].sum()), int(d.tail(q)["failed"].sum())
            z, p = stats.two_prop_z(kl, q, ke, q)
            strength = stats.evidence_strength(p, q, MIN_GROUP_SAMPLES, 1, SIGNIFICANCE_ALPHA)
            if strength in ("strong", "moderate"):
                out.append(_insight(
                    title="Failure rate " + ("increased" if z > 0 else "decreased") + " over the campaign", category="environment",
                    severity="medium" if z > 0 else "low", evidence_strength=strength,
                    finding=f"Last quarter of runs failed at {_pct(kl / q)} vs {_pct(ke / q)} in the first quarter (n={q:,} each).",
                    evidence={"sample_size": 2 * q, "failure_rate": _r(kl / q), "baseline_failure_rate": _r(ke / q), "lift": _r((kl / q) / (ke / q) if ke else 0, 3),
                              "z_score": _r(z, 2), "p_value": float(f"{p:.3g}")},
                    supporting_runs=2 * q, recommendation="Check what drifted (environment, firmware, fleet mix) between early and late runs.",
                    explanation="Two-proportion z-test, first vs last quarter of executions by timestamp."))
        # failure: dominant signature + determinism
        sigs = failure_signatures(f)["signatures"]
        if sigs and sigs[0]["signature"] not in ("NONE",):
            s = sigs[0]
            top_c = s["associated_parameters"][0] if s["associated_parameters"] else None
            out.append(_insight(
                title=f"Dominant failure signature: {s['signature']}", category="failure", severity="high" if s["share_of_failures"] >= 0.3 else "medium",
                evidence_strength="strong" if s["failures"] >= 30 else "insufficient",
                finding=(f"{s['signature']} accounts for {_pct(s['share_of_failures'])} of failures ({s['failures']:,}); tendency: {s['tendency']}."
                         + (f" Most associated condition: {top_c['condition']} ({top_c['lift']}x, present in {_pct(top_c['support'])} of these failures)." if top_c else "")),
                evidence={"sample_size": s["failures"], "share_of_failures": s["share_of_failures"], "ci95": s["ci95_share"],
                          "deterministic_share": s["deterministic_share"], "top_condition_lift": top_c and top_c["lift"]},
                affected_parameters=[c["condition"].split(" ")[0].split("=")[0] for c in s["associated_parameters"][:3]],
                failure_signatures=[s["signature"]], supporting_runs=s["failures"],
                recommendation=f"Prioritise {s['signature']}: review its top associated conditions first.",
                explanation="Signature share of failures with Wilson CI; associated conditions ranked by lift (support >= 30%)."))
        det = analytics.determinism()
        classes = {c["class"]: c["count"] for c in det["failure_classes"]}
        dsum = classes.get("deterministic", 0) + classes.get("stochastic", 0) + classes.get("stable", 0)
        if dsum:
            share = classes.get("deterministic", 0) / dsum
            out.append(_insight(
                title=f"{_pct(share)} of classifiable failures are deterministic", category="failure",
                severity="medium", evidence_strength="strong" if dsum >= 100 else "weak",
                finding=(f"{classes.get('deterministic', 0):,} failures come from profiles failing >= 80% of runs across seeds; "
                         f"{classes.get('stochastic', 0) + classes.get('stable', 0):,} are stochastic"
                         + (f"; {classes.get('insufficient', 0):,} lack enough repeats to classify." if classes.get("insufficient") else ".")),
                evidence={"sample_size": dsum, "deterministic_failures": classes.get("deterministic", 0),
                          "stochastic_failures": classes.get("stochastic", 0) + classes.get("stable", 0), "insufficient": classes.get("insufficient", 0),
                          "min_repeats": det["policy"]["min_repeats"], "min_distinct_seeds": det["policy"]["min_distinct_seeds"]},
                supporting_runs=dsum,
                recommendation="Fix deterministic profiles through configuration rules; investigate stochastic ones with seed/environment control.",
                explanation="Repeatability across seeds per configuration profile (global dataset)."))
        # randomization: anomalous seeds
        if analytics._has_seed():
            flagged = [s for s in analytics.seed_points() if s["flag"]]
            if flagged:
                s = flagged[0]
                out.append(_insight(
                    title=f"Seed {s['seed']} fails more than its configurations predict", category="randomization",
                    severity="high" if s["lift"] >= 1.5 else "medium", evidence_strength="strong" if s["p_value"] < 1e-4 else "moderate",
                    finding=(f"Observed {_pct(s['observed'])} vs expected {_pct(s['expected'])} over {s['runs']:,} runs (z={s['z']}); "
                             f"{len(flagged)} seed(s) are significantly anomalous after Bonferroni correction."),
                    evidence={"sample_size": s["runs"], "failure_rate": s["observed"], "baseline_failure_rate": s["expected"], "lift": s["lift"],
                              "z_score": s["z"], "p_value": s["p_value"], "ci95": s["ci95"]},
                    affected_parameters=["seed"], supporting_runs=s["runs"],
                    recommendation=f"Re-run seed {s['seed']} under controlled conditions; add flagged seeds to regression.",
                    explanation="Expected rate = configuration-only model prediction for the same runs; seeds need enough runs to be scored."))
            else:
                out.append(_insight(title="No seed shows a statistically significant excess failure rate", category="randomization",
                                    severity="low", evidence_strength="not significant", finding="All scored seeds are consistent with model expectations.",
                                    evidence={"seeds_scored": sum(1 for s in analytics.seed_points() if s["sufficient_samples"])},
                                    recommendation="No seed-specific action needed.", explanation="Bonferroni-corrected binomial z-tests."))
        # hardware
        hw = hardware_comparison(f)
        if len(hw["hardware"]) > 1:
            worst = max(hw["hardware"], key=lambda r: r["failure_rate"])
            cross = hw["cross_tier_patterns"][0] if hw["cross_tier_patterns"] else None
            significant = worst["evidence_strength"] in ("strong", "moderate")
            out.append(_insight(
                title=(f"{worst['hardware']} has a significantly higher failure rate" if significant
                       else f"No hardware tier differs significantly in failure rate (highest: {worst['hardware']})"), category="hardware",
                severity=stats.severity(worst["failure_rate"], worst["failure_rate"] / base if base else 0, worst["evidence_strength"]),
                evidence_strength=worst["evidence_strength"],
                finding=(f"{worst['hardware']}: {_pct(worst['failure_rate'])} over {worst['runs']:,} runs vs {_pct(base)} overall."
                         + (f" The top risky combination ({cross['condition']}) is elevated on {cross['tiers_elevated']} of {cross['tiers_checked']} hardware tiers, "
                            "so the pattern is not specific to one platform." if cross and cross["tiers_checked"] else "")),
                evidence={"sample_size": worst["runs"], "failure_rate": worst["failure_rate"], "baseline_failure_rate": _r(base), "ci95": worst["ci95"],
                          "z_score": worst["vs_rest_z"], "p_value": worst["vs_rest_p"], "matched_profile_failure_rate": worst["matched_profile_failure_rate"]},
                affected_parameters=["hardware"], supporting_runs=worst["runs"],
                recommendation="Compare tiers on matched configurations before attributing differences to hardware.",
                explanation="Two-proportion z vs other tiers; matched comparison uses configuration profiles run on several tiers."))
        # prediction quality
        v = store.bundle.validation
        out.append(_insight(
            title=f"Failure risk is predictable before execution (ROC-AUC {v['holdout']['roc_auc']:.3f})", category="prediction", severity="low",
            evidence_strength="strong" if v["cv"]["roc_auc_mean"] >= 0.7 else "weak",
            finding=(f"Hold-out ROC-AUC {v['holdout']['roc_auc']:.3f}; 5-fold CV {v['cv']['roc_auc_mean']:.3f} ± {v['cv']['roc_auc_std']:.3f}; "
                     f"Brier {v['holdout']['brier']:.3f}."),
            evidence={"sample_size": v["test_size"], "roc_auc": v["holdout"]["roc_auc"], "cv_roc_auc_mean": v["cv"]["roc_auc_mean"],
                      "cv_roc_auc_std": v["cv"]["roc_auc_std"], "precision": v["holdout"]["precision"], "recall": v["holdout"]["recall"],
                      "f1": v["holdout"]["f1"], "brier": v["holdout"]["brier"]},
            recommendation="Use the risk model as a pre-flight gate (see Model Validation for calibration).",
            explanation="Global model trained on configuration + context only (no post-run telemetry)."))
        # recommendation: top guardrail
        gr = guardrails(f)["guardrails"]
        if gr:
            g = gr[0]
            out.append(_insight(
                title=f"Top guardrail: {g['condition']}", category="recommendation", severity=g["severity"], evidence_strength=g["evidence_strength"],
                finding=g["recommendation"],
                evidence={k: g[k] for k in ("runs", "failures", "failure_rate", "baseline_failure_rate", "lift", "p_value", "z_score", "ci95")} | {"sample_size": g["runs"]},
                affected_parameters=[c["parameter"] for c in g["conditions"]], supporting_runs=g["runs"],
                recommendation=g["recommendation"], explanation="Highest-scoring data-discovered guardrail (lift x log10(n) x evidence)."))
        sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        out.sort(key=lambda i: (sev_rank[i["severity"]], -STRENGTH_WEIGHT.get(i.get("evidence_strength", "weak"), 0)))
        for i, ins in enumerate(out):
            ins["id"] = f"INS-{i + 1:02d}"
        return {"filters": evidence.clean_filters(f), "runs": n0, "baseline_failure_rate": _r(base), "insights": out,
                "language": "associational: no causal inference is performed"}
    return store.cached(f"insights::{filter_key(f)}", build)
