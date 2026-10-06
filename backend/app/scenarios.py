"""Scenario Lab: dataset-aware synthetic scenarios derived from an UPLOADED dataset.

The uploaded dataset is the structural baseline and is never modified: a scenario is generated
from a copy and stored separately (DATA_DIR/scenarios/<id>.parquet + .json). Columns, column
order, dtypes and timestamps are preserved; only the scenario's target field(s) change.

Scenarios are offered only when their required fields really exist in the dataset:
  telemetry  : Vibration Spike (vibration channel), Temperature Rise (temperature channel),
               RPM Deviation (rotational-speed channel)
  execution  : Configuration Failure (outcome + configuration parameter), Seed Sensitivity (real
               seed), Resource/Performance Failure (real performance metric), Multi-factor
               Failure (outcome + two configuration parameters)
Telemetry scenarios never add PASS/FAIL, seed, configuration or error fields. Benchmark mode keeps
the existing generator (/api/dataset/generate) unchanged.
"""
from __future__ import annotations

import io
import json
import re
import time
import uuid

import numpy as np
import polars as pl
from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import persistence
from .active_dataset import BENCHMARK, UPLOAD_EXEC, UPLOAD_TEL, available
from .auth import get_current_user, require_permission
from .config import DATA_DIR
from .preprocessor.mapper import tokens
from .store import UPLOAD_META_PATH, UPLOAD_PATH
from .telemetry import TELEMETRY_PATH, telemetry_store

router = APIRouter(prefix="/api", tags=["scenarios"])
SCENARIO_DIR = DATA_DIR / "scenarios"
SCENARIO_DIR.mkdir(exist_ok=True)
MAX_KEEP = 20
LEVELS = {"low": 1, "medium": 2, "high": 3}

_VIB = {"vibration", "vib", "accel", "acceleration"}
_TEMP = {"temp", "temperature", "temperatur", "thermal"}
_RPM = {"rpm", "rotational", "rotation"}


def _channel_kind(name: str, unit: str | None) -> str | None:
    t = set(tokens(name))
    if t & _VIB or unit == "g":
        return "vibration"
    if t & _TEMP or unit in ("°C", "°F"):
        return "temperature"
    if t & _RPM or unit == "RPM" or ("speed" in t and t & {"spindle", "motor", "fan", "shaft"}):
        return "rpm"
    return None


TELEMETRY_SCENARIOS = [
    ("vibration_spike", "Vibration Spike", "vibration", "Short bursts of high vibration (+4 to +8 robust SD) at seeded positions."),
    ("temperature_rise", "Temperature Rise", "temperature", "A gradual temperature ramp over the last part of the recording."),
    ("rpm_deviation", "RPM Deviation", "rpm", "A sustained window where rotational speed drops 15-45% below its recorded value."),
]
EXECUTION_SCENARIOS = [
    ("configuration_failure", "Configuration Failure", "A value of the most important configuration parameter starts failing more often."),
    ("seed_sensitivity", "Seed Sensitivity", "A subset of random seeds becomes failure-prone across configurations."),
    ("performance_failure", "Resource/Performance Failure", "Performance degrades in a subset of runs, which then fail more often."),
    ("multi_factor_failure", "Multi-factor Failure", "Failures concentrate where two configuration values occur together."),
]


# ------------------------------------------------------------------------------------ catalog
def _telemetry_catalog(entry: dict) -> list[dict]:
    kinds: dict[str, list[str]] = {}
    for ch in entry.get("channels", []):
        k = _channel_kind(ch["name"], ch.get("unit"))
        if k:
            kinds.setdefault(k, []).append(ch["name"])
    out = []
    for sid, label, kind, desc in TELEMETRY_SCENARIOS:
        cols = kinds.get(kind, [])
        out.append({"id": sid, "label": label, "description": desc, "enabled": bool(cols), "fields": cols,
                    "reason": None if cols else f"No {kind} channel was detected in this dataset."})
    return out


def _execution_catalog(meta: dict) -> list[dict]:
    synthetic = set(meta.get("synthetic_columns") or [])
    cfg = list(meta.get("config_params") or [])
    real_tel = [c for c in (meta.get("telemetry") or []) if c not in synthetic]
    perf = "throughput_mbps" not in synthetic
    req = {
        "configuration_failure": (bool(cfg), cfg[:1], "Needs a PASS/FAIL outcome and at least one configuration parameter."),
        "seed_sensitivity": ("seed" not in synthetic, ["seed"], "The dataset has no random-seed column."),
        "performance_failure": (perf, ["throughput_mbps"] + real_tel[:1], "The dataset has no performance metric."),
        "multi_factor_failure": (len(cfg) >= 2, cfg[:2], "Needs at least two configuration parameters."),
    }
    out = []
    for sid, label, desc in EXECUTION_SCENARIOS:
        ok, fields, why = req[sid]
        out.append({"id": sid, "label": label, "description": desc, "enabled": ok, "fields": fields if ok else [],
                    "reason": None if ok else why})
    return out


def catalog(dataset_id: str | None) -> dict:
    av = available()
    entry = next((d for d in av["datasets"] if d["dataset_id"] == (dataset_id or av["active_id"])), None)
    if entry is None:
        raise HTTPException(404, "No uploaded dataset available.")
    if entry["dataset_id"] == BENCHMARK:
        return {"dataset": entry, "supported": True, "mode": "benchmark", "scenarios": [],
                "message": "Benchmark mode uses the existing synthetic generator."}
    if entry["pipeline"] == "telemetry":
        sc = _telemetry_catalog(entry)
    elif entry["dataset_id"] == UPLOAD_EXEC:
        sc = _execution_catalog(json.loads(UPLOAD_META_PATH.read_text()))
    else:
        return {"dataset": entry, "supported": False, "mode": "unsupported", "scenarios": [],
                "message": f"Scenarios are not supported for {entry['type_label']} datasets."}
    return {"dataset": entry, "supported": True, "mode": entry["pipeline"], "scenarios": sc,
            "message": None if any(s["enabled"] for s in sc) else "None of the scenarios' required fields were detected in this dataset."}


# ------------------------------------------------------------------------------------ generators
def _robust_sd(v: np.ndarray) -> float:
    ok = v[np.isfinite(v)]
    if len(ok) < 2:
        return 0.0
    mad = float(np.median(np.abs(ok - np.median(ok))))
    return 1.4826 * mad if mad > 0 else float(np.std(ok))


def _windows(n: int, count: int, length: int, rng: np.random.Generator) -> list[tuple[int, int]]:
    length = max(1, min(length, n))
    starts = sorted(rng.choice(max(1, n - length + 1), size=min(count, max(1, n - length + 1)), replace=False))
    return [(int(s), int(s) + length) for s in starts]


def _telemetry_scenario(sid: str, lvl: int, seed: int) -> tuple[pl.DataFrame, dict]:
    frame = pl.read_parquet(TELEMETRY_PATH)  # read-only copy of the stored telemetry dataset
    meta = telemetry_store.info()["meta"]
    rng = np.random.default_rng(seed)
    kind = next(k for s, _, k, _ in TELEMETRY_SCENARIOS if s == sid)
    targets = [c for c in meta["channels"] if _channel_kind(c["original"], c.get("unit")) == kind]
    n = len(frame)
    changed_rows = np.zeros(n, dtype=bool)
    changes = []
    for ch in targets:
        v = frame[ch["name"]].cast(pl.Float64).fill_null(float("nan")).to_numpy().copy()
        before = v.copy()
        sd = _robust_sd(v) or 1.0
        if sid == "vibration_spike":
            for a, b in _windows(n, 2 + lvl, max(1, n // 100), rng):
                v[a:b] = v[a:b] + (2 + 2 * lvl) * sd
        elif sid == "temperature_rise":
            start = int(n * rng.uniform(0.4, 0.7))
            delta = lvl * max(2 * sd, 0.05 * abs(float(np.nanmedian(v))))
            v[start:] = v[start:] + np.linspace(0, delta, n - start)
        elif sid == "rpm_deviation":
            for a, b in _windows(n, 1, max(5, int(n * 0.05 * lvl)), rng):
                v[a:b] = v[a:b] * (1 - 0.15 * lvl)
        v = np.where(np.isnan(before), np.nan, v)  # missing stays missing
        diff = np.isfinite(v) & (np.abs(v - before) > 1e-12)
        changed_rows |= diff
        orig = frame[ch["name"]]
        new = pl.Series(ch["name"], v).fill_nan(None)
        new = new.round(0).cast(orig.dtype) if orig.dtype.is_integer() else new.cast(orig.dtype)
        frame = frame.with_columns(new)
        changes.append({"column": ch["original"], "rows_modified": int(diff.sum()), "mean_before": float(np.nanmean(before)),
                        "mean_after": float(np.nanmean(v)), "max_before": float(np.nanmax(before)), "max_after": float(np.nanmax(v)),
                        "min_before": float(np.nanmin(before)), "min_after": float(np.nanmin(v))})
    # restore the user's original column names / order (internal row_index is not part of the dataset)
    rename = {"timestamp": meta["timestamp"]["column"]} if meta.get("timestamp", {}).get("column") else {}
    for group in ("channels", "context", "logs"):
        rename.update({c["name"]: c["original"] for c in meta.get(group, [])})
    order = [c["original"] for c in meta["columns"] if c.get("semantic_role") != "ignore"]
    frame = frame.drop("row_index").rename(rename)
    frame = frame.select([c for c in order if c in frame.columns] + [c for c in frame.columns if c not in order])
    return frame, {"changes": changes, "rows_modified": int(changed_rows.sum())}


def _flip(df: pl.DataFrame, mask: np.ndarray) -> pl.DataFrame:
    """Turn passing runs selected by mask into failures (fields that already exist only)."""
    failed = df["failed"].to_numpy().copy()
    flip = mask & (failed == 0)
    failed[flip] = 1
    cols = [pl.Series("failed", failed).cast(df["failed"].dtype)]
    if "outcome" in df.columns:
        cols.append(pl.Series("outcome", np.where(failed == 1, "fail", "pass")))
    if "error_signature" in df.columns:  # the pipeline's own label for a failure without a known signature
        sig = df["error_signature"].to_numpy().copy()
        sig[flip] = "UNCLASSIFIED_FAILURE"
        cols.append(pl.Series("error_signature", sig))
    return df.with_columns(cols)


def _execution_scenario(sid: str, lvl: int, seed: int) -> tuple[pl.DataFrame, dict]:
    df = pl.read_parquet(UPLOAD_PATH)  # read-only copy of the stored upload
    meta = json.loads(UPLOAD_META_PATH.read_text())
    rng = np.random.default_rng(seed)
    n = len(df)
    cfg = list(meta.get("config_params") or [])
    before_fail = int(df["failed"].sum())
    detail: dict = {}
    if sid == "configuration_failure":
        p = cfg[0]
        rates = df.group_by(p).agg(pl.col("failed").mean().alias("r"), pl.len().alias("n")).filter(pl.col("n") >= max(5, n // 100)).sort("r", descending=True)
        val = rates[p][0]
        mask = (df[p] == val).to_numpy() & (rng.uniform(size=n) < 0.2 * lvl)
        detail = {"parameter": p, "value": str(val)}
    elif sid == "seed_sensitivity":
        seeds = df["seed"].unique().to_numpy()
        pick = rng.choice(seeds, size=max(1, len(seeds) // 10), replace=False)
        mask = np.isin(df["seed"].to_numpy(), pick) & (rng.uniform(size=n) < 0.25 * lvl)
        detail = {"seeds": [int(s) for s in pick[:20]], "seed_count": int(len(pick))}
    elif sid == "performance_failure":
        idx = rng.uniform(size=n) < 0.1 * lvl
        tp = df["throughput_mbps"].to_numpy().astype(float)
        tp = np.where(idx, tp * (1 - 0.15 * lvl), tp)
        df = df.with_columns(pl.Series("throughput_mbps", tp).cast(df["throughput_mbps"].dtype))
        mask = idx & (rng.uniform(size=n) < 0.3 * lvl)
        detail = {"runs_degraded": int(idx.sum()), "throughput_drop": f"{15 * lvl}%"}
    else:  # multi_factor_failure
        a, b = cfg[0], cfg[1]
        combos = df.group_by([a, b]).len().filter(pl.col("len") >= max(5, n // 50)).sort("len", descending=True)
        va, vb = combos[a][0], combos[b][0]
        mask = ((df[a] == va) & (df[b] == vb)).to_numpy() & (rng.uniform(size=n) < 0.2 + 0.2 * lvl)
        detail = {"parameters": [a, b], "values": [str(va), str(vb)]}
    out = _flip(df, mask)
    after_fail = int(out["failed"].sum())
    detail.update({"failures_before": before_fail, "failures_after": after_fail, "rows_modified": int((out["failed"] != df["failed"]).sum())})
    if sid == "performance_failure":
        detail["rows_modified"] = int((out["throughput_mbps"] != pl.read_parquet(UPLOAD_PATH, columns=["throughput_mbps"])["throughput_mbps"]).sum())
    return out, detail


def _prune() -> None:
    """Keep the newest MAX_KEEP scenarios (registry first, then any older files without a record)."""
    for old in persistence.scenarios_to_prune(MAX_KEEP):
        (SCENARIO_DIR / f"{old['scenario_id']}.parquet").unlink(missing_ok=True)
        (SCENARIO_DIR / f"{old['scenario_id']}.json").unlink(missing_ok=True)
        persistence.delete_scenario(old["scenario_id"])
    files = sorted(SCENARIO_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[MAX_KEEP:]:
        old.unlink(missing_ok=True)
        old.with_suffix(".parquet").unlink(missing_ok=True)


def generate(dataset_id: str, scenario: str, intensity: str, seed: int) -> dict:
    cat = catalog(dataset_id)
    if not cat["supported"] or cat["mode"] == "benchmark":
        raise HTTPException(400, cat["message"] or "Scenarios are not available for this dataset.")
    sc = next((s for s in cat["scenarios"] if s["id"] == scenario), None)
    if sc is None or not sc["enabled"]:
        raise HTTPException(422, (sc or {}).get("reason") or "Unknown scenario for this dataset.")
    lvl = LEVELS[intensity]
    t0 = time.perf_counter()
    if cat["mode"] == "telemetry":
        frame, detail = _telemetry_scenario(scenario, lvl, seed)
    else:
        frame, detail = _execution_scenario(scenario, lvl, seed)
    sid = uuid.uuid4().hex
    entry = cat["dataset"]
    info = {"scenario_id": sid, "scenario": scenario, "label": sc["label"], "intensity": intensity, "seed": seed,
            "base_dataset": {"dataset_id": entry["dataset_id"], "dataset_name": entry["dataset_name"], "dataset_type": entry["dataset_type"]},
            "rows": len(frame), "columns": frame.columns, "column_count": frame.width, "modified_fields": sc["fields"], "detail": detail,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "seconds": round(time.perf_counter() - t0, 3),
            "note": "Synthetic scenario derived from the uploaded dataset; the original dataset is unchanged."}
    persistence.atomic_write_parquet(frame, SCENARIO_DIR / f"{sid}.parquet")
    persistence.atomic_write_text(SCENARIO_DIR / f"{sid}.json", json.dumps(info, default=str))
    persistence.add_scenario(info, SCENARIO_DIR / f"{sid}.parquet")
    _prune()
    preview = frame.drop([c for c in ("log_trace",) if c in frame.columns]).head(50)
    return {**info, "preview": json.loads(preview.write_json())}


# ------------------------------------------------------------------------------------ routes
class ScenarioReq(BaseModel):
    dataset_id: str
    scenario: str
    intensity: str = Field("medium", pattern="^(low|medium|high)$")
    seed: int = Field(7, ge=0, le=999_999)


@router.get("/scenarios")
def get_catalog(dataset_id: str | None = None, user: dict = Depends(get_current_user)):
    return catalog(dataset_id)


@router.post("/scenarios/generate")
async def post_generate(req: ScenarioReq, user: dict = Depends(require_permission("generate"))):
    return await run_in_threadpool(generate, req.dataset_id, req.scenario, req.intensity, req.seed)


@router.get("/scenarios/{scenario_id}/download")
def download(scenario_id: str, user: dict = Depends(get_current_user)):
    if not re.fullmatch(r"[0-9a-f]{32}", scenario_id):
        raise HTTPException(404, "Scenario not found.")  # same status as before the registry
    rec = persistence.get_scenario(scenario_id)
    path = SCENARIO_DIR / f"{scenario_id}.parquet"
    if not path.exists():
        raise HTTPException(404, "Scenario not found." if rec is None else "The scenario's data file is missing.")
    frame = pl.read_parquet(path)
    info = rec["info"] if rec else json.loads((SCENARIO_DIR / f"{scenario_id}.json").read_text())
    buf = io.BytesIO()
    frame.write_csv(buf)
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{info['base_dataset']['dataset_name'].rsplit('.', 1)[0]}_{info['scenario']}_{info['intensity']}.csv")
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})
