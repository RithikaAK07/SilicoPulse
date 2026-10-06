"""Active Dataset + dataset-aware Scenario Lab (cases A-E from the specification)."""
import hashlib
import io
import json

import numpy as np
import polars as pl

from app.store import UPLOAD_PATH, Store, read_active, store
from app.telemetry import TELEMETRY_PATH

CNC_COLS = ["Timestamp", "vibration_g", "motor_temp_c", "spindle_rpm"]
EXEC_ONLY = {"outcome", "failed", "seed", "config_id", "error_signature", "pass", "fail", "status", "result"}


def _md5(path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _cnc(n=356) -> bytes:
    rng = np.random.default_rng(3)
    rows = [f"2026-10-05T15:{(i // 6) % 60:02d}:{(i % 6) * 10:02d}.277Z,{0.4 + rng.normal(0, .02):.4f},{48 + rng.normal(0, 1):.2f},"
            f"{0 if 100 <= i < 112 else 2400 + rng.normal(0, 20):.1f}" for i in range(n)]
    return (",".join(CNC_COLS) + "\n" + "\n".join(rows) + "\n").encode()


def _ingest(client, h, name, raw, mode, mapping=None):
    pv = client.post("/api/upload/preview", files={"file": (name, raw)}, headers=h).json()
    data = {"upload_id": pv["upload_id"], "mode": mode}
    if mapping is not None:
        data["mapping"] = json.dumps(mapping)
    elif mode == "execution":
        data["mapping"] = json.dumps(pv["parts"][0]["execution"]["preview"]["mapping"])
    r = client.post("/api/upload/ingest", data=data, headers=h)
    assert r.status_code == 200, r.text
    return pv


def _activate(client, h, dataset_id):
    r = client.post("/api/active-dataset", json={"dataset_id": dataset_id}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_active_dataset_cases_a_to_e(client, h, headers):
    # ---- CASE A: no upload -> benchmark only, existing generator mode
    client.post("/api/dataset/reset", headers=h)
    client.post("/api/telemetry/reset", headers=h)
    UPLOAD_PATH.unlink(missing_ok=True)
    av = client.get("/api/active-dataset", headers=h).json()
    assert av["active_id"] == "benchmark" and [d["dataset_id"] for d in av["datasets"]] == ["benchmark"]
    bench = av["datasets"][0]
    assert bench["counts"]["config_params"] > 0 and bench["row_count"] == len(store.df)
    assert client.get("/api/scenarios?dataset_id=benchmark", headers=h).json()["mode"] == "benchmark"
    assert client.post("/api/active-dataset", json={"dataset_id": "upload-execution"}, headers=h).status_code == 404

    # ---- CASE B: upload an execution CSV -> selectable, execution scenarios from its real fields
    sample = client.get("/api/download-sample-csv", headers=h).content
    _ingest(client, h, "exec.csv", sample, "execution")
    av = client.get("/api/active-dataset", headers=h).json()
    assert av["active_id"] == "upload-execution" and {d["dataset_id"] for d in av["datasets"]} == {"benchmark", "upload-execution"}
    up = next(d for d in av["datasets"] if d["dataset_id"] == "upload-execution")
    assert up["dataset_name"] == "exec.csv" and up["dataset_type"] == "execution" and up["row_count"] == 1000
    assert "outcome" in up["field_mapping"] and "config" in up["field_mapping"]
    cat = client.get("/api/scenarios?dataset_id=upload-execution", headers=h).json()
    assert cat["mode"] == "execution" and all(s["enabled"] for s in cat["scenarios"])
    before = _md5(UPLOAD_PATH)
    g = client.post("/api/scenarios/generate", json={"dataset_id": "upload-execution", "scenario": "configuration_failure", "intensity": "high"}, headers=h)
    assert g.status_code == 200, g.text
    g = g.json()
    assert g["detail"]["failures_after"] > g["detail"]["failures_before"] and g["rows"] == 1000
    assert _md5(UPLOAD_PATH) == before  # the uploaded dataset is never modified
    assert set(g["columns"]) == set(pl.read_parquet_schema(UPLOAD_PATH))  # same structure
    dl = client.get(f"/api/scenarios/{g['scenario_id']}/download", headers=h)
    assert dl.status_code == 200 and pl.read_csv(io.BytesIO(dl.content)).height == 1000

    # ---- CASE C: telemetry upload -> industrial telemetry, 3 telemetry scenarios, no execution fields
    _ingest(client, h, "BT_San_Telemetry.csv", _cnc(), "telemetry")
    av = client.get("/api/active-dataset", headers=h).json()
    assert av["active_id"] == "upload-execution"  # ingesting telemetry does not switch the execution views by itself
    tel = next(d for d in av["datasets"] if d["dataset_id"] == "upload-telemetry")
    assert tel["dataset_type"] == "industrial_telemetry" and tel["row_count"] == 356 and tel["column_count"] == 4
    assert [f["name"] for f in tel["detected_fields"]] == CNC_COLS and len(tel["channels"]) == 3
    act = _activate(client, h, "upload-telemetry")
    assert act["active_id"] == "upload-telemetry" and act["dataset"]["active"] == "uploaded_telemetry"
    cat = client.get("/api/scenarios", headers=h).json()  # defaults to the active dataset
    assert cat["mode"] == "telemetry"
    assert {s["label"]: s["enabled"] for s in cat["scenarios"]} == {"Vibration Spike": True, "Temperature Rise": True, "RPM Deviation": True}
    original = pl.read_parquet(TELEMETRY_PATH)
    tel_md5 = _md5(TELEMETRY_PATH)
    for sc, col in (("temperature_rise", "motor_temp_c"), ("vibration_spike", "vibration_g"), ("rpm_deviation", "spindle_rpm")):
        g = client.post("/api/scenarios/generate", json={"dataset_id": "upload-telemetry", "scenario": sc, "intensity": "medium", "seed": 11}, headers=h).json()
        assert g["columns"] == CNC_COLS and g["rows"] == 356 and not (set(c.lower() for c in g["columns"]) & EXEC_ONLY)
        assert g["modified_fields"] == [col] and g["detail"]["rows_modified"] > 0
        frame = pl.read_csv(io.BytesIO(client.get(f"/api/scenarios/{g['scenario_id']}/download", headers=h).content))
        assert frame.columns == CNC_COLS
        for other in set(CNC_COLS[1:]) - {col}:  # unrelated channels unchanged
            assert frame[other].to_list() == original[other].to_list()
        assert (frame[col] != original[col]).sum() == g["detail"]["rows_modified"]
    assert _md5(TELEMETRY_PATH) == tel_md5  # telemetry dataset untouched
    temp = next(c for c in client.post("/api/scenarios/generate", json={"dataset_id": "upload-telemetry", "scenario": "temperature_rise"},
                                       headers=h).json()["detail"]["changes"])
    assert temp["mean_after"] > temp["mean_before"] and temp["min_after"] >= temp["min_before"] - 1e-9  # a rise, nothing invented below range

    # ---- CASE D: back to benchmark -> existing generator; uploads stay selectable
    act = _activate(client, h, "benchmark")
    assert act["active_id"] == "benchmark" and act["dataset"]["source"] == "benchmark"
    assert {d["dataset_id"] for d in act["datasets"]} == {"benchmark", "upload-execution", "upload-telemetry"}
    from app.config import DEFAULT_CONFIG_PARAMS, DEFAULT_RANDOM_VARS, DEFAULT_RUNS
    # the existing generator still works; same size + seed as the session benchmark so other tests see the same data
    gen = client.post("/api/dataset/generate", json={"n_runs": DEFAULT_RUNS, "n_config": DEFAULT_CONFIG_PARAMS,
                                                     "n_random": DEFAULT_RANDOM_VARS, "seed": 42}, headers=h)
    assert gen.status_code == 200 and len(store.df) == DEFAULT_RUNS
    assert UPLOAD_PATH.exists()  # regenerating the benchmark no longer deletes the upload
    assert client.get("/api/overview", headers=h).status_code == 200

    # ---- CASE E: the active selection survives a restart (existing persistence model: DATA_DIR files)
    _activate(client, h, "upload-execution")
    assert read_active() == {"active": "uploaded_execution", "execution_source": "uploaded"}
    fresh = Store()
    fresh.load_or_generate()  # what a restarted API does
    assert fresh.meta.get("source") == "uploaded" and len(fresh.df) == 1000
    _activate(client, h, "upload-telemetry")
    assert client.get("/api/active-dataset", headers=h).json()["active_id"] == "upload-telemetry"
    assert read_active()["active"] == "uploaded_telemetry"

    # read-only role cannot switch the shared active dataset
    assert client.post("/api/active-dataset", json={"dataset_id": "benchmark"}, headers=headers["executive"]).status_code == 403
    # removing the telemetry dataset falls back to the loaded execution dataset
    client.post("/api/telemetry/reset", headers=h)
    assert client.get("/api/active-dataset", headers=h).json()["active_id"] == "upload-execution"
    client.post("/api/dataset/reset", headers=h)
    assert client.get("/api/active-dataset", headers=h).json()["active_id"] == "benchmark"


def test_execution_scenarios_enabled_only_when_fields_exist(client, h):
    rng = np.random.default_rng(1)
    n = 200
    temp = rng.normal(60, 8, n)
    fail = rng.uniform(size=n) < np.where(temp > 65, 0.6, 0.15)
    df = pl.DataFrame({"time": [f"2026-03-{1 + i // 24:02d} {i % 24:02d}:00:00" for i in range(n)], "temperature": temp,
                       "rpm": rng.choice([1200, 1800, 2400], n), "result": np.where(fail, "FAIL", "PASS")})
    buf = io.BytesIO()
    df.write_csv(buf)
    _ingest(client, h, "minimal.csv", buf.getvalue(), "execution")
    cat = client.get("/api/scenarios?dataset_id=upload-execution", headers=h).json()
    en = {s["id"]: s["enabled"] for s in cat["scenarios"]}
    assert en == {"configuration_failure": True, "seed_sensitivity": False, "performance_failure": False, "multi_factor_failure": False}
    assert "seed" in next(s for s in cat["scenarios"] if s["id"] == "seed_sensitivity")["reason"]
    r = client.post("/api/scenarios/generate", json={"dataset_id": "upload-execution", "scenario": "seed_sensitivity"}, headers=h)
    assert r.status_code == 422  # disabled scenarios cannot be generated
    client.post("/api/dataset/reset", headers=h)
