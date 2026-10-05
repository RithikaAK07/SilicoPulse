"""Evidence layer: structure, thresholds and - crucially - that numbers are computed from data."""
import polars as pl

from app import analytics, evidence, insights, stats
from app.config import MIN_SEED_RUNS
from app.store import store

SEVERITIES = {"low", "medium", "high", "critical"}
CATEGORIES = {"configuration", "randomization", "environment", "failure", "hardware", "prediction", "recommendation"}


def test_stats_helpers():
    lo, hi = stats.wilson(50, 100)
    assert lo < 0.5 < hi and 0.39 < lo < 0.41
    z, p = stats.two_prop_z(80, 100, 20, 100)
    assert z > 8 and p < 1e-10
    assert stats.evidence_strength(1e-9, 500, 30) == "strong"
    assert stats.evidence_strength(1e-9, 10, 30) == "insufficient"
    assert stats.odds_ratio(10, 20, 10, 20) == 1.0


def test_insight_schema(client, h):
    body = client.get("/api/insights", headers=h).json()
    assert body["insights"], "expected insights on the benchmark"
    for i in body["insights"]:
        assert i["severity"] in SEVERITIES and i["category"] in CATEGORIES
        assert i["title"] and i["finding"] and isinstance(i["evidence"], dict)
        assert "causes" not in i["finding"].lower(), "insights must use associational language"


def test_insights_respect_filters(client, h):
    hw = store.df["hardware"].unique().sort()[0]
    body = client.get(f"/api/insights?hardware={hw}", headers=h).json()
    assert body["filters"] == {"hardware": hw}
    assert body["runs"] == int((store.df["hardware"] == hw).sum())


def test_toxic_pair_numbers_match_raw_data(client, h):
    pairs = client.get("/api/insights/pairs", headers=h).json()
    assert pairs["toxic"], "benchmark should contain toxic combinations"
    t = pairs["toxic"][0]
    sub = store.df.filter((pl.col(t["a"]) == t["a_val"]) & (pl.col(t["b"]) == t["b_val"]))
    assert t["runs"] == len(sub) >= pairs["min_support"]
    assert t["failures"] == int(sub["failed"].sum())
    assert abs(t["failure_rate"] - sub["failed"].mean()) < 1e-3
    assert abs(t["baseline_failure_rate"] - store.df["failed"].mean()) < 1e-3


def test_min_support_guard_applies_to_all_pairs(client, h):
    pairs = client.get("/api/insights/pairs", headers=h).json()
    assert all(c["runs"] >= pairs["min_support"] for c in pairs["toxic"] + pairs["best"])


def test_learned_thresholds_are_data_derived():
    thr = evidence.environment_thresholds(None)
    assert thr, "expected at least one learned threshold"
    t = thr[0]
    col = store.df[t["variable"]]
    risky = store.df.filter(col > t["threshold"]) if t["direction"] == "above" else store.df.filter(col <= t["threshold"])
    assert len(risky) == t["risky_runs"]
    assert abs(risky["failed"].mean() - t["risky_failure_rate"]) < 1e-3
    assert "learned" in t["source"]


def test_drift_limits_are_labelled():
    for v in analytics.drift()["variables"]:
        assert v["limit_source"].startswith(("learned", "reference"))


def test_seed_flags_require_minimum_runs_and_significance():
    for s in analytics.seed_points():
        if s["flag"]:
            assert s["runs"] >= MIN_SEED_RUNS and s["significant"] and s["z"] > 0
        assert s["ci95"][0] <= s["observed"] <= s["ci95"][1]


def test_determinism_policy_and_tendency():
    d = analytics.determinism()
    assert {"min_repeats", "min_distinct_seeds"} <= d["policy"].keys()
    for p in d["profiles"]:
        if p["class"] == "deterministic":
            assert p["runs"] >= d["policy"]["min_repeats"] and p["fail_rate"] >= 0.8
    assert all(r["tendency"] in ("deterministic", "stochastic", "mixed", "insufficient data") for r in d["by_signature"])


def test_signature_hierarchy_shares_sum_to_one(client, h):
    sg = client.get("/api/insights/signatures", headers=h).json()
    assert abs(sum(s["share_of_failures"] for s in sg["signatures"]) - 1) < 1e-2
    top = sg["signatures"][0]
    assert {"associated_parameters", "associated_environment", "associated_randomization", "hardware", "supporting_runs"} <= top.keys()


def test_guardrails_have_required_fields(client, h):
    gr = client.get("/api/insights/guardrails", headers=h).json()["guardrails"]
    assert gr
    for g in gr:
        for k in ("condition", "failure_rate", "baseline_failure_rate", "lift", "runs", "evidence_strength", "recommendation", "severity"):
            assert k in g
        assert g["evidence_strength"] in ("strong", "moderate")


def test_hardware_comparison(client, h):
    hw = client.get("/api/insights/hardware", headers=h).json()
    assert len(hw["hardware"]) == store.df["hardware"].n_unique()
    assert sum(r["runs"] for r in hw["hardware"]) == len(store.df)


def test_parameter_risk_reports_effect_and_significance():
    pr = evidence.parameter_risk(None)["parameters"]
    assert pr and {"importance", "correlation", "chi2_p_value", "cramers_v", "riskiest_value", "safest_value"} <= pr[0].keys()


def test_data_quality_benchmark(client, h):
    dq = client.get("/api/data-quality", headers=h).json()
    assert dq["total_executions"] == 3000 and dq["successful_executions"] + dq["failures"] == 3000
    assert dq["source"] == "benchmark" and "SYNTHETIC" in dq["provenance"]


def test_model_validation(client, h):
    v = client.get("/api/model/validation", headers=h).json()
    assert v["holdout"]["roc_auc"] > 0.6 and v["cv"]["folds"] == 5
    assert {"tn", "fp", "fn", "tp"} == set(v["holdout"]["confusion_matrix_at_best"])
    assert v["calibration"] and sum(b["count"] for b in v["calibration"]) == v["test_size"]
