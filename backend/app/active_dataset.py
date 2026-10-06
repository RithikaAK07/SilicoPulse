"""One app-wide "Active Dataset", built on the EXISTING stores (no second upload/storage system).

  benchmark           -> store (runs.parquet, generator)            pipeline: execution
  upload-execution    -> store (uploaded.parquet + uploaded_meta)   pipeline: execution
  upload-telemetry    -> telemetry_store (telemetry.parquet + meta) pipeline: telemetry

GET  /api/active-dataset   -> the active dataset + every available dataset (id, name, type, rows,
                              columns, detected fields, field mapping, status)
POST /api/active-dataset   -> make one of them active (same permission as "revert to benchmark")

Activating telemetry leaves the execution store loaded (the models need a dataset) but the
execution views then report that their analyses are not available for the active dataset.
"""
from __future__ import annotations

import json

import polars as pl
from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from .auth import get_current_user, require_permission
from .config import META_PATH, RUNS_PATH
from .preprocessor.dataset_types import describe
from .store import UPLOAD_META_PATH, UPLOAD_PATH, read_active, store, write_active
from .telemetry import telemetry_store

router = APIRouter(prefix="/api", tags=["active-dataset"])

BENCHMARK, UPLOAD_EXEC, UPLOAD_TEL = "benchmark", "upload-execution", "upload-telemetry"
_ACTIVE_KEY = {BENCHMARK: "benchmark", UPLOAD_EXEC: "uploaded_execution", UPLOAD_TEL: "uploaded_telemetry"}
CANON_ROLES = {"run_id": "run_id", "config_id": "config_id", "failed": "outcome", "outcome": "outcome", "throughput_mbps": "performance",
               "seed": "seed", "timestamp": "timestamp", "environment": "environment", "hardware": "hardware", "workload": "workload",
               "error_signature": "error_signature", "log_trace": "log"}


def _execution_fields(schema: dict, meta: dict) -> list[dict]:
    synthetic = set(meta.get("synthetic_columns") or [])
    cfg, rnd = set(meta.get("config_params") or []), set(meta.get("random_vars") or [])
    tel = set(meta.get("telemetry") or [])
    out = []
    for c, t in schema.items():
        role = CANON_ROLES.get(c) or ("config" if c in cfg else "random" if c in rnd else "telemetry" if c in tel else "other")
        out.append({"name": c, "role": role, "dtype": str(t), "derived": c in synthetic})
    return out


def _mapping(fields: list[dict]) -> dict:
    m: dict[str, list[str]] = {}
    for f in fields:
        if not f.get("derived"):
            m.setdefault(f["role"], []).append(f["name"])
    return m


def _execution_entry(dataset_id: str, df: pl.DataFrame | None, path, meta: dict) -> dict:
    if df is not None:
        schema, rows = dict(df.schema), len(df)
    else:
        schema = dict(pl.read_parquet_schema(path))
        rows = int(pl.scan_parquet(path).select(pl.len()).collect().item())
    fields = _execution_fields(schema, meta)
    real = [f for f in fields if not f["derived"]]
    info = describe("execution")
    return {
        "dataset_id": dataset_id, "source": "benchmark" if dataset_id == BENCHMARK else "uploaded",
        "dataset_name": "Synthetic benchmark" if dataset_id == BENCHMARK else (meta.get("filename") or "uploaded execution file"),
        "dataset_type": "execution", "type_label": info["label"], "pipeline": "execution",
        "row_count": rows, "column_count": len(real), "derived_columns": sorted(meta.get("synthetic_columns") or []),
        "detected_fields": fields, "field_mapping": _mapping(fields),
        "counts": {"config_params": len(meta.get("config_params") or []), "random_vars": len(meta.get("random_vars") or []),
                   "profiles": meta.get("n_profiles"), "log_lines": meta.get("log_lines")},
        "activated_at": meta.get("generated_at") or (meta.get("preprocessing") or {}).get("activated_at"),
    }


def _telemetry_entry() -> dict | None:
    st = telemetry_store.status()
    if not st.get("active"):
        return None
    m = st["meta"]
    dtype = m.get("dataset_type") or "industrial_telemetry"
    info = describe(dtype) or {"label": "Telemetry"}
    fields = [{"name": c["original"], "role": c["semantic_role"], "dtype": c.get("dtype"), "unit": c.get("unit"), "derived": False,
               "missing": c.get("missing")} for c in m.get("columns", []) if c.get("semantic_role") != "ignore"]
    return {
        "dataset_id": UPLOAD_TEL, "source": "uploaded", "dataset_name": m.get("original_filename") or "telemetry file",
        "dataset_type": dtype, "type_label": info["label"], "pipeline": "telemetry", "row_count": m.get("rows"),
        "column_count": len(fields), "derived_columns": [], "detected_fields": fields, "field_mapping": _mapping(fields),
        "channels": [{"name": c["original"], "unit": c["unit"]} for c in m.get("channels", [])],
        "activated_at": m.get("activated_at"),
    }


def available() -> dict:
    act = read_active()
    loaded_upload = store.meta.get("source") == "uploaded"
    datasets = []
    if loaded_upload:
        datasets.append(_execution_entry(BENCHMARK, None, RUNS_PATH, json.loads(META_PATH.read_text())) if RUNS_PATH.exists() and META_PATH.exists()
                        else None)
        datasets.append(_execution_entry(UPLOAD_EXEC, store.df, None, store.meta))
    else:
        datasets.append(_execution_entry(BENCHMARK, store.df, None, store.meta))
        if UPLOAD_PATH.exists() and UPLOAD_META_PATH.exists():
            datasets.append(_execution_entry(UPLOAD_EXEC, None, UPLOAD_PATH, json.loads(UPLOAD_META_PATH.read_text())))
    tel = _telemetry_entry()
    if tel:
        datasets.append(tel)
    datasets = [d for d in datasets if d]
    active_id = next((k for k, v in _ACTIVE_KEY.items() if v == act["active"]), BENCHMARK)
    if not any(d["dataset_id"] == active_id for d in datasets):  # e.g. telemetry was removed
        active_id = UPLOAD_EXEC if act["execution_source"] == "uploaded" and any(d["dataset_id"] == UPLOAD_EXEC for d in datasets) else BENCHMARK
    for d in datasets:
        d["status"] = "active" if d["dataset_id"] == active_id else "available"
    return {"active_id": active_id, "active": next(d for d in datasets if d["dataset_id"] == active_id), "datasets": datasets}


def activate(dataset_id: str) -> None:
    act = read_active()
    if dataset_id == BENCHMARK:
        if store.meta.get("source") == "uploaded":
            store.reset_to_benchmark()  # keeps the uploaded file
        write_active("benchmark", "benchmark")
    elif dataset_id == UPLOAD_EXEC:
        if store.meta.get("source") != "uploaded":
            store.activate_saved_upload()
        write_active("uploaded_execution", "uploaded")
    elif dataset_id == UPLOAD_TEL:
        if not telemetry_store.status().get("active"):
            raise HTTPException(404, "No uploaded telemetry dataset available.")
        write_active("uploaded_telemetry", act["execution_source"])  # execution store unchanged
    else:
        raise HTTPException(400, "Unknown dataset_id.")


def telemetry_removed() -> None:
    """Called when the telemetry dataset is deleted: fall back to the loaded execution dataset."""
    act = read_active()
    if act["active"] == "uploaded_telemetry":
        write_active("uploaded_execution" if act["execution_source"] == "uploaded" else "benchmark", act["execution_source"])


class ActivateReq(BaseModel):
    dataset_id: str


@router.get("/active-dataset")
def get_active(user: dict = Depends(get_current_user)):
    return available()


@router.post("/active-dataset")
async def set_active(req: ActivateReq, user: dict = Depends(require_permission("upload"))):
    try:
        await run_in_threadpool(activate, req.dataset_id)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return {**available(), "dataset": store.dataset_status()}
