"""Storage layer: registry, migration from pre-registry installs, restarts, corruption handling,
atomic writes, concurrency and API-contract stability. Restarts are real: each `_boot` starts a
separate Python process on its own data folder, exactly like a restarted API."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import polars as pl
import pytest

from app import persistence

BACKEND = Path(__file__).resolve().parent.parent
BOOT = """
import json
from app.store import store, read_active
from app import persistence
store.load_or_generate()
store.sync_registry()  # same two steps as the API lifespan
print("RESULT " + json.dumps({"source": store.meta.get("source", "benchmark"), "rows": len(store.df), "active": read_active(),
    "registry": {r["entry"]["dataset_id"]: {"status": r["status"], "rows": r["entry"]["row_count"], "name": r["entry"]["dataset_name"],
                 "error": r["error"]} for r in persistence.list_datasets()}}))
"""


def _boot(data_dir: Path) -> dict:
    env = {**os.environ, "SILICOPULSE_DATA_DIR": str(data_dir), "SILICOPULSE_DEFAULT_RUNS": "1000", "GEMINI_API_KEY": "", "JWT_SECRET": "t"}
    out = subprocess.run([sys.executable, "-c", BOOT], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    line = next(l for l in out.stdout.splitlines() if l.startswith("RESULT "))
    return json.loads(line[7:])


@pytest.fixture(scope="module")
def booted(tmp_path_factory) -> tuple[Path, dict]:
    d = tmp_path_factory.mktemp("clean")
    return d, _boot(d)


# ---------------------------------------------------------------- startup, benchmark, restart
def test_clean_start_creates_registry_and_benchmark(booted):
    d, r = booted
    assert (d / "silicopulse.db").exists() and (d / "runs.parquet").exists() and (d / "meta.json").exists()
    assert r["source"] == "benchmark" and r["rows"] == 1000
    assert r["active"] == {"active": "benchmark", "execution_source": "benchmark"}
    assert r["registry"]["benchmark"] == {"status": "available", "rows": 1000, "name": "Synthetic benchmark", "error": None}


def test_restart_keeps_benchmark_without_regenerating(booted):
    d, first = booted
    mtime = (d / "runs.parquet").stat().st_mtime_ns
    again = _boot(d)
    assert (d / "runs.parquet").stat().st_mtime_ns == mtime  # loaded, not regenerated
    assert again["rows"] == first["rows"] and again["registry"] == first["registry"] and again["active"] == first["active"]


def _legacy_install(src: Path, dst: Path, active: str = "uploaded_execution") -> None:
    """A data folder as written before the registry existed: files only, no database."""
    dst.mkdir()
    for f in ("runs.parquet", "meta.json"):
        (dst / f).write_bytes((src / f).read_bytes())
    df = pl.read_parquet(src / "runs.parquet").head(600)
    df.write_parquet(dst / "uploaded.parquet")
    meta = {**json.loads((src / "meta.json").read_text()), "source": "uploaded", "filename": "legacy_upload.csv", "synthetic_columns": []}
    (dst / "uploaded_meta.json").write_text(json.dumps(meta))
    (dst / "active_dataset.json").write_text(json.dumps({"active": active, "execution_source": "uploaded"}))


def test_pre_registry_install_is_migrated_without_changing_anything(booted, tmp_path):
    src, _ = booted
    d = tmp_path / "legacy"
    _legacy_install(src, d)
    r = _boot(d)
    assert (d / "silicopulse.db").exists()
    assert r["active"] == {"active": "uploaded_execution", "execution_source": "uploaded"}  # selection preserved
    assert r["source"] == "uploaded" and r["rows"] == 600  # the upload is what the dashboards see, as before
    assert r["registry"]["upload-execution"]["name"] == "legacy_upload.csv" and r["registry"]["upload-execution"]["rows"] == 600
    assert (d / "uploaded.parquet").exists() and (d / "active_dataset.json").exists()  # nothing deleted
    assert _boot(d)["registry"] == r["registry"]  # idempotent on the next start


# ---------------------------------------------------------------- corruption / missing data
def test_unreadable_upload_is_quarantined_not_deleted(booted, tmp_path):
    src, _ = booted
    d = tmp_path / "corrupt_upload"
    _legacy_install(src, d)
    (d / "uploaded.parquet").write_bytes(b"not a parquet file")
    r = _boot(d)
    assert r["source"] == "benchmark" and r["active"]["active"] == "benchmark"  # starts, falls back
    kept = list(d.glob("uploaded.parquet.corrupt-*"))
    assert kept and kept[0].read_bytes() == b"not a parquet file"  # user data kept aside, not deleted
    assert r["registry"]["upload-execution"]["status"] == "error" and "moved aside" in r["registry"]["upload-execution"]["error"]


def test_corrupt_registry_is_rebuilt_from_files(booted, tmp_path):
    src, _ = booted
    d = tmp_path / "corrupt_db"
    _legacy_install(src, d)
    _boot(d)
    for f in d.glob("silicopulse.db*"):
        f.unlink()
    (d / "silicopulse.db").write_bytes(b"\x00garbage" * 100)
    r = _boot(d)
    assert list(d.glob("silicopulse.db.corrupt-*"))  # moved aside for inspection
    assert r["active"]["active"] == "uploaded_execution" and r["source"] == "uploaded"  # recovered from the file mirror
    assert r["registry"]["upload-execution"]["rows"] == 600 and r["registry"]["benchmark"]["rows"] == 1000


def test_unreadable_benchmark_does_not_break_startup(booted, tmp_path):
    src, _ = booted
    d = tmp_path / "corrupt_bench"
    d.mkdir()
    (d / "runs.parquet").write_bytes(b"broken")
    (d / "meta.json").write_text((src / "meta.json").read_text())
    r = _boot(d)
    assert r["source"] == "benchmark" and r["rows"] == 1000  # default benchmark rebuilt
    assert list(d.glob("runs.parquet.corrupt-*"))


# ---------------------------------------------------------------- atomic writes + concurrency
def test_atomic_writes(tmp_path):
    target = tmp_path / "meta.json"
    persistence.atomic_write_text(target, '{"v": 1}')

    class Boom:
        def write_parquet(self, path):
            Path(path).write_bytes(b"partial")
            raise OSError("disk full")

    with pytest.raises(OSError):
        persistence.atomic_write_parquet(Boom(), tmp_path / "data.parquet")
    assert not (tmp_path / "data.parquet").exists()  # no half-written dataset
    assert target.read_text() == '{"v": 1}' and not list(tmp_path.glob(".*.tmp"))  # no leftovers


def test_concurrent_metadata_writes_are_safe():
    errors = []

    def worker(i):
        try:
            for j in range(40):
                persistence.set_state(f"test_concurrency_{i}", {"j": j})
                assert persistence.get_state(f"test_concurrency_{i}")["j"] == j
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and all(persistence.get_state(f"test_concurrency_{i}") == {"j": 39} for i in range(8))


def test_invalid_metadata_is_rejected():
    with pytest.raises(persistence.StorageError):
        persistence.upsert_dataset({"dataset_id": "x"}, None)


# ---------------------------------------------------------------- API behaviour with the registry
def test_storage_failures_are_explicit(client, h, monkeypatch):
    # reads degrade to describing the files (real data), never a fake response
    monkeypatch.setattr(persistence, "list_datasets", lambda: (_ for _ in ()).throw(persistence.StorageError("db down")))
    r = client.get("/api/active-dataset", headers=h)
    assert r.status_code == 200 and r.json()["active_id"] in ("benchmark", "upload-execution")
    # a write that cannot be persisted fails with a clear 503, it does not pretend to succeed
    monkeypatch.setattr(persistence, "set_state", lambda *a, **k: (_ for _ in ()).throw(persistence.StorageError("The metadata database is unavailable.")))
    r = client.post("/api/active-dataset", json={"dataset_id": "benchmark"}, headers=h)
    assert r.status_code == 503 and r.json() == {"detail": "The metadata database is unavailable."}


def test_missing_dataset_file_is_reported_not_offered(client, h):
    from app.store import UPLOAD_META_PATH, UPLOAD_PATH

    sample = client.get("/api/download-sample-csv", headers=h).content
    pv = client.post("/api/upload/preview", files={"file": ("s.csv", sample)}, headers=h).json()
    m = pv["parts"][0]["execution"]["preview"]["mapping"]
    assert client.post("/api/upload/ingest", data={"upload_id": pv["upload_id"], "mode": "execution", "mapping": json.dumps(m)}, headers=h).status_code == 200
    assert persistence.get_dataset("upload-execution")["entry"]["row_count"] == 1000  # metadata persisted on upload
    client.post("/api/dataset/reset", headers=h)
    moved = UPLOAD_PATH.with_name("moved.parquet")
    os.replace(UPLOAD_PATH, moved)
    try:
        av = client.get("/api/active-dataset", headers=h).json()
        assert [d["dataset_id"] for d in av["datasets"]] == ["benchmark"]
        assert persistence.get_dataset("upload-execution")["status"] == "missing"
        assert client.post("/api/active-dataset", json={"dataset_id": "upload-execution"}, headers=h).status_code == 404
        assert client.post("/api/active-dataset", json={"dataset_id": "nope"}, headers=h).status_code == 400
    finally:
        os.replace(moved, UPLOAD_PATH)
    assert "upload-execution" in [d["dataset_id"] for d in client.get("/api/active-dataset", headers=h).json()["datasets"]]


def test_response_contracts_unchanged(client, h):
    status = client.get("/api/dataset/status", headers=h).json()
    assert set(status) == {"source", "label", "filename", "rows", "config_params", "random_vars", "synthetic_columns", "leakage_dropped",
                           "activated_at", "version", "active"}
    av = client.get("/api/active-dataset", headers=h).json()
    assert set(av) == {"active_id", "active", "datasets"}
    assert set(av["datasets"][0]) == {"dataset_id", "source", "dataset_name", "dataset_type", "type_label", "pipeline", "row_count", "column_count",
                                      "derived_columns", "detected_fields", "field_mapping", "counts", "activated_at", "status"}
    health = client.get("/api/health").json()
    assert set(health) == {"status", "runs", "analytics_cache"}
    for k in ("telemetry", "scenarios"):
        assert not any(t.startswith(k) for t in status)


def test_metadata_reads_stay_lightweight(client, h):
    """Dataset metadata (registry, selector, header badge) never computes the telemetry statistics."""
    from app.telemetry import telemetry_store

    cnc = ("Timestamp,vibration_g,motor_temp_c,spindle_rpm\n" + "\n".join(
        f"2026-10-05T15:{i // 6:02d}:{(i % 6) * 10:02d}Z,{0.4 + (i % 7) / 100},{48 + (i % 5) / 2},{2400 + i % 9}" for i in range(120))).encode()
    pv = client.post("/api/upload/preview", files={"file": ("t.csv", cnc)}, headers=h).json()
    assert client.post("/api/upload/ingest", data={"upload_id": pv["upload_id"], "mode": "telemetry"}, headers=h).status_code == 200
    assert client.post("/api/active-dataset", json={"dataset_id": "upload-telemetry"}, headers=h).status_code == 200
    telemetry_store._summary = None
    persistence.delete_dataset("upload-telemetry")  # force a registry rebuild from the files
    assert client.get("/api/active-dataset", headers=h).json()["active_id"] == "upload-telemetry"
    status = client.get("/api/dataset/status", headers=h).json()
    assert status["active"] == "uploaded_telemetry" and status["active_rows"] == 120
    assert telemetry_store._summary is None  # nothing heavy was computed
    assert client.get("/api/telemetry/status", headers=h).json()["summary"]["rows"] == 120  # still computed when actually needed
    client.post("/api/telemetry/reset", headers=h)
    client.post("/api/dataset/reset", headers=h)
