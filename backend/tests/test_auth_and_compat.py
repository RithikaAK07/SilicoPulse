"""Authentication, RBAC and backward compatibility of every pre-existing endpoint."""
import pytest


def test_health_is_public(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["runs"] == 3000


def test_login_and_me(client, headers):
    me = client.get("/api/me", headers=headers["admin"]).json()
    assert me["role"] == "admin" and "upload" in me["permissions"]


def test_bad_credentials_and_missing_token(client):
    assert client.post("/api/login", json={"email": "admin@sandisk.com", "password": "nope"}).status_code == 401
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/overview", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_executive_is_read_only(client, headers):
    ex = headers["executive"]
    assert client.get("/api/insights", headers=ex).status_code == 200
    assert client.post("/api/dataset/generate", json={"n_runs": 1000}, headers=ex).status_code == 403
    assert client.post("/api/upload-csv/preview", files={"file": ("a.csv", b"a,b\n1,2\n")}, headers=ex).status_code == 403


@pytest.mark.parametrize("path,keys", [
    ("/api/meta", ["n_runs", "key_params", "sandbox_params", "choices", "defaults", "model", "dataset"]),
    ("/api/overview", ["kpis", "trend", "signatures", "by_hardware"]),
    ("/api/discovery", ["importance", "pareto", "top_configs", "pairs", "model_auc"]),
    ("/api/randomization", ["sensitivity", "curves", "heatmaps", "seeds", "determinism"]),
    ("/api/drift", ["series", "variables", "crossings", "topology"]),
    ("/api/rootcause", ["fingerprints", "log_anomalies"]),
    ("/api/runs?outcome=fail", None),
    ("/api/diff/suggest", ["run_a", "run_b"]),
    ("/api/dataset/status", ["source", "label", "rows"]),
])
def test_existing_endpoints_keep_their_contract(client, h, path, keys):
    r = client.get(path, headers=h)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    for k in keys or []:
        assert k in body, f"{path} lost field {k}"


def test_existing_pair_fields_preserved(client, h):
    worst = client.get("/api/discovery", headers=h).json()["pairs"]["worst"][0]
    for k in ("pair", "a", "a_val", "b", "b_val", "runs", "fail_rate", "throughput", "lift", "score"):
        assert k in worst
    for k in ("failures", "odds_ratio", "p_value", "interaction_lift", "evidence_strength"):  # new, additive
        assert k in worst


def test_diff_predict_recommend_contracts(client, h):
    pair = client.get("/api/diff/suggest", headers=h).json()
    d = client.get("/api/diff", params=pair, headers=h).json()
    assert {"config", "random", "telemetry", "change_impact", "logs"} <= d.keys()
    p = client.post("/api/predict", json={"config": {"queue_depth": 256, "write_cache": 0}}, headers=h).json()
    assert {"failure_risk", "risk_gbm", "risk_rf", "expected_throughput", "shap", "base_rate", "model_quality"} <= p.keys()
    rec = client.post("/api/recommend", json={"context": {}}, headers=h).json()
    best = rec["recommendations"][0]
    assert {"rank", "config", "failure_risk", "expected_throughput", "confidence", "model_agreement"} <= best.keys()
    assert {"support", "extrapolation", "uncertainty", "pareto_optimal", "why", "tradeoffs"} <= best.keys()
    assert "explanation" in rec and "shap" in rec


def test_legacy_copilot_endpoint_still_answers(client, h):
    r = client.post("/api/copilot", json={"query": "Which settings influence failure?"}, headers=h)
    assert r.status_code == 200 and r.json()["markdown"]
