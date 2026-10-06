"""Universal upload API: any supported file -> preprocessor -> EXISTING ingestion pipeline.

  POST /api/upload/preview   -> detect type, parse, map, validate (no side effects except a pending copy)
  POST /api/upload/ingest    -> mode=execution: existing canonicalize + store.activate_upload (needs a real outcome)
                                mode=telemetry: telemetry store (no outcome, no model training, nothing fabricated)
  GET  /api/telemetry/status -> active telemetry dataset + statistics
  POST /api/telemetry/reset  -> remove the telemetry dataset

The legacy /api/upload-csv/preview and /api/upload-csv endpoints are unchanged.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from . import upload_handler as uh
from .auth import get_current_user, require_permission
from .preprocessor import PREPROCESSOR_VERSION, SUPPORTED_LABEL, PreprocessError, execution_frame, load_part, preprocess
from .preprocessor.execution import assess_outcome
from .preprocessor.dataset_types import dataset_type_of, pipeline_of
from .preprocessor.mapper import classify, detect_roles
from .preprocessor.models import MAX_UPLOAD_BYTES
from .preprocessor.normalizer import normalize_telemetry
from .preprocessor.zip_handler import safe_display_name
from .store import store
from .telemetry import telemetry_store

log = logging.getLogger("silicopulse.upload")
router = APIRouter(prefix="/api", tags=["upload"])
PENDING = uh.PENDING_DIR
TELEMETRY_ROLES = {"telemetry", "context", "log", "ignore"}


async def _read(file: UploadFile) -> bytes:
    buf = bytearray()
    while chunk := await file.read(1 << 20):
        buf += chunk
        if len(buf) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"File exceeds the {MAX_UPLOAD_BYTES // 2**20} MB upload limit.")
    if not buf:
        raise HTTPException(400, "The file is empty.")
    return bytes(buf)


def _cleanup(max_age_s: int = 7200) -> None:
    now = time.time()
    for p in PENDING.glob("u_*"):
        try:
            if now - p.stat().st_mtime > max_age_s:
                p.unlink(missing_ok=True)
        except OSError:
            pass


def _pending(upload_id: str) -> tuple[bytes, dict]:
    if not re.fullmatch(r"[0-9a-f]{32}", upload_id or ""):
        raise HTTPException(400, "Invalid upload_id.")
    data, info = PENDING / f"u_{upload_id}.bin", PENDING / f"u_{upload_id}.json"
    if not data.exists() or not info.exists():
        raise HTTPException(404, "This upload has expired; please upload the file again.")
    return data.read_bytes(), json.loads(info.read_text())


@router.post("/upload/preview")
async def universal_preview(file: UploadFile = File(...), user: dict = Depends(require_permission("upload"))):
    raw = await _read(file)
    filename = file.filename or "upload"
    try:
        res = await run_in_threadpool(preprocess, raw, filename)
    except Exception:
        log.exception("preview failed")
        raise HTTPException(400, "The file could not be processed. It may be malformed or in an unexpected layout.")
    if res["errors"]:
        status = 415 if res["errors"][0].startswith("Unsupported file type") else 400
        raise HTTPException(status, res["errors"][0])
    _cleanup()
    upload_id = uuid.uuid4().hex
    (PENDING / f"u_{upload_id}.bin").write_bytes(raw)
    (PENDING / f"u_{upload_id}.json").write_text(json.dumps({"filename": filename, "file_type": res["file_type"]}))
    return {"upload_id": upload_id, "supported": SUPPORTED_LABEL, **res}


def _merge(base: dict, user_map: dict) -> dict:
    m = {**base, **{k: v for k, v in user_map.items() if k != "roles"}}
    if isinstance(user_map.get("roles"), dict):
        m["roles"] = {**(base.get("roles") or {}), **user_map["roles"]}
    return m


@router.post("/upload/ingest")
async def universal_ingest(
    upload_id: str = Form(...),
    part_id: str = Form("main"),
    mode: str = Form("execution"),
    mapping: str | None = Form(None),
    user: dict = Depends(require_permission("upload")),
):
    if mode not in ("execution", "telemetry"):
        raise HTTPException(400, "mode must be 'execution' or 'telemetry'.")
    raw, info = _pending(upload_id)
    try:
        user_map = json.loads(mapping) if mapping else {}
    except json.JSONDecodeError:
        raise HTTPException(400, "mapping must be JSON.")
    if not isinstance(user_map, dict):
        raise HTTPException(400, "mapping must be a JSON object.")

    def run():
        t0 = time.perf_counter()
        table, member_raw = load_part(raw, info["filename"], part_id)
        provenance = {"original_filename": safe_display_name(info["filename"]), "file_type": info["file_type"], "part": table.label,
                      "parser": table.kind, "preprocessing_version": PREPROCESSOR_VERSION}
        if mode == "execution":
            df, det, report = execution_frame(table, member_raw)
            confirmed = set(user_map.pop("confirmed", None) or [])
            m = _merge(det["mapping"], user_map)
            if not m.get("outcome"):
                raise HTTPException(422, "PASS/FAIL outcome is not present. This file was detected as telemetry-only data and can be "
                                         "processed using telemetry analysis.")
            # never ingest a guessed PASS/FAIL polarity or date order: the user must confirm it
            pending = []
            if "outcome" not in confirmed:
                a = assess_outcome(m["outcome"], det["low_card_values"].get(m["outcome"], []))
                if not a["reliable"]:
                    pending.append(a["reason"])
            for c in report["confirmations"]:
                if c["field"] == "timestamp" and c["column"] == m.get("timestamp") and "timestamp" not in confirmed:
                    pending.append(c["message"])
            if pending:
                raise HTTPException(422, "Please confirm the mapping before ingesting: " + " ".join(pending))
            frame, meta = uh.canonicalize(df, m, safe_display_name(info["filename"]))
            meta["preprocessing"] = {**provenance, "transforms": report["transforms"], "confirmed": sorted(confirmed),
                                     "renamed_for_detection": report["renamed_for_detection"]}
            prep_s = time.perf_counter() - t0
            store.activate_upload(frame, meta)
            return {"mode": "execution", "detected": det["columns"], "mapping": m, "prep_seconds": round(prep_s, 2)}
        roles = detect_roles(table.frame, table.invalid_numeric)
        cols = {r["name"]: r for r in roles}
        ts = user_map.get("timestamp", "__auto__")
        if ts == "__auto__":
            ts = next((r["name"] for r in roles if r["role"] == "timestamp"), None)
        if ts is not None and ts not in cols:
            raise HTTPException(400, f"Unknown timestamp column '{ts}'.")
        role_map = user_map.get("roles") or {}
        if not isinstance(role_map, dict) or any(c not in cols or r not in TELEMETRY_ROLES for c, r in role_map.items()):
            raise HTTPException(400, f"Telemetry roles must map existing columns to one of: {', '.join(sorted(TELEMETRY_ROLES))}.")
        if not role_map:
            from .preprocessor import _telemetry_mapping
            role_map = _telemetry_mapping(roles)["roles"]
        if not ts:
            raise HTTPException(422, "Telemetry ingestion needs a timestamp column: none was detected or selected. "
                                     "Select the timestamp column, or add one to the file.")
        if not any(r == "telemetry" for c, r in role_map.items() if c != ts):
            raise HTTPException(422, "No telemetry channels: select at least one numeric measurement column as telemetry.")
        frame, nmeta = normalize_telemetry(table.frame, ts, role_map, table.invalid_numeric)
        if "timestamp" not in frame.columns:
            raise HTTPException(422, f"Column '{ts}' could not be read as timestamps. Select another timestamp column.")
        if not any(frame[c["name"]].drop_nulls().len() for c in nmeta["channels"]):
            raise HTTPException(422, "The selected telemetry columns contain no numeric values.")
        columns = [{"original": r["name"], "normalized": next((p["normalized"] for p in nmeta["provenance"] if p["original"] == r["name"]), None),
                    "semantic_role": "timestamp" if r["name"] == ts else role_map.get(r["name"], "ignore"),
                    "detected_role": r["role"], "confidence": r["confidence"], "dtype": r["dtype"], "missing": r["missing"],
                    "unit": (r.get("unit") or "unit unknown") if role_map.get(r["name"]) == "telemetry" else None} for r in roles]
        dtype = classify(roles, table.kind, False)
        dataset_type = dataset_type_of(dtype) if pipeline_of(dataset_type_of(dtype)) == "telemetry" else "time_series_telemetry"
        meta = {**provenance, "data_type": "telemetry", "dataset_type": dataset_type, "pipeline": "telemetry", "rows": len(frame), "columns": columns, "channels": nmeta["channels"],
                "context": nmeta["context"], "logs": nmeta["logs"], "timestamp": nmeta["timestamp"], "warnings": nmeta["warnings"],
                "transforms": nmeta["provenance"], "outcome": None, "outcome_note": "PASS/FAIL outcome is not present; none was inferred."}
        status = telemetry_store.activate(frame, meta)
        return {"mode": "telemetry", "telemetry": status, "prep_seconds": round(time.perf_counter() - t0, 2)}

    try:
        res = await run_in_threadpool(run)
    except HTTPException:
        raise
    except PreprocessError as e:
        raise HTTPException(400, str(e))
    except Exception:
        log.exception("ingest failed")
        raise HTTPException(500, "Ingestion failed unexpectedly. The previous dataset is still active.")
    for ext in ("bin", "json"):
        (PENDING / f"u_{upload_id}.{ext}").unlink(missing_ok=True)
    if res["mode"] == "telemetry":
        return res
    return {**res, "dataset": store.dataset_status(), "model_auc": store.bundle.auc, "training_seconds": round(store.train_seconds, 2),
            "summary": {"rows": len(store.df), "config_params": len(store.meta["config_params"]), "random_vars": len(store.meta["random_vars"]),
                        "profiles": store.meta["n_profiles"], "failures": int(store.df["failed"].sum())}}


@router.get("/telemetry/status")
def telemetry_status(user: dict = Depends(get_current_user)):
    return telemetry_store.status()


@router.post("/telemetry/reset")
def telemetry_reset(user: dict = Depends(require_permission("upload"))):
    telemetry_store.clear()
    from .active_dataset import telemetry_removed
    telemetry_removed()  # if telemetry was the active dataset, fall back to the loaded execution dataset
    return {"active": False}
