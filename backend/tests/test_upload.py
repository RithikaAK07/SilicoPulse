"""CSV upload, mapping, leakage guard, data quality - runs last-ish and restores the benchmark."""
import io
import json

import numpy as np
import polars as pl

from app import upload_handler as uh


def test_sample_csv_roundtrip_and_reset(client, h):
    sample = client.get("/api/download-sample-csv", headers=h)
    assert sample.status_code == 200 and sample.content.count(b"\n") == 1001
    pv = client.post("/api/upload-csv/preview", files={"file": ("sample.csv", sample.content, "text/csv")}, headers=h).json()
    m = pv["mapping"]
    assert m["outcome"] == "status" and m["fail_values"] == ["fail"] and m["seed"] == "seed" and m["performance"] == "throughput_mbps"
    r = client.post("/api/upload-csv", data={"upload_id": pv["upload_id"], "mapping": json.dumps({"outcome": "status", "fail_values": ["fail"]})}, headers=h)
    assert r.status_code == 200, r.text[:300]
    assert r.json()["dataset"]["source"] == "uploaded" and r.json()["summary"]["rows"] == 1000
    for path in ("/api/overview", "/api/discovery", "/api/randomization", "/api/rootcause", "/api/insights", "/api/insights/guardrails", "/api/data-quality"):
        assert client.get(path, headers=h).status_code == 200, path
    dq = client.get("/api/data-quality", headers=h).json()
    assert dq["source"] == "uploaded" and dq["missing_values_total"] == 0 and dq["log_coverage"] == 1.0
    reset = client.post("/api/dataset/reset", headers=h).json()
    assert reset["source"] == "benchmark"


def test_foreign_schema_with_missing_values(client, h):
    rng = np.random.default_rng(0)
    n = 600
    volt = rng.uniform(0.7, 1.1, n)
    mode = rng.choice(["eco", "std", "turbo"], n)
    fail = rng.uniform(size=n) < np.where(volt < 0.8, 0.7, 0.15)
    vib = rng.uniform(0, 1, n).astype(object)
    vib[:25] = None  # missing values must be measured, not hidden
    df = pl.DataFrame({"Test ID": [f"t{i}" for i in range(n)], "Core Voltage": volt, "Power Mode": mode, "Vibration Noise": list(vib),
                       "Verdict": np.where(fail, "FAILED", "PASSED")})
    buf = io.BytesIO()
    df.write_csv(buf)
    r = client.post("/api/upload-csv", files={"file": ("foreign.csv", buf.getvalue())}, headers=h)
    assert r.status_code == 200, r.text[:300]
    dq = client.get("/api/data-quality", headers=h).json()
    assert dq["missing_values"].get("vibration_noise") == 25
    assert any("Seed analysis unavailable" in w for w in dq["warnings"])
    assert "seed" in dq["derived_fields"]
    rnd = client.get("/api/randomization", headers=h).json()
    assert rnd["has_seed"] is False and rnd["seeds"] == []
    assert client.get("/api/insights", headers=h).status_code == 200
    client.post("/api/dataset/reset", headers=h)


def test_leakage_guard():
    from app.generator import generate_dataset
    df = generate_dataset(800, 30, 10, seed=3, persist=False)
    raw = df.drop("config_id", "log_trace").rename({"outcome": "status"})
    buf = io.BytesIO()
    raw.write_csv(buf)
    d, _ = uh.sanitize(uh.read_csv(buf.getvalue()))
    det = uh.detect(d)
    assert det["mapping"]["roles"].get("failed") == "ignore"
    m = det["mapping"]
    m["roles"] = {**m["roles"], "failed": "config"}  # even if forced back, ingest drops it
    _, meta = uh.canonicalize(d, m, "leaky.csv")
    assert meta["leakage_dropped"] and "x_failed" not in meta["config_params"]


def test_rejects_bad_files(client, h):
    assert client.post("/api/upload-csv", files={"file": ("x.txt", b"a")}, headers=h).status_code == 400
    bad = client.post("/api/upload-csv", files={"file": ("x.csv", b"a,b\n1,2\n3,4\n")}, headers=h)
    assert bad.status_code == 422
    assert client.get("/api/dataset/status", headers=h).json()["source"] == "benchmark"
