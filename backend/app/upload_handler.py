"""CSV upload & ingestion engine.

Flow:
  1. POST /api/upload-csv/preview  -> stores the file, detects column roles and suggests a mapping
  2. POST /api/upload-csv          -> applies the (possibly user-edited) mapping, canonicalizes the
                                      CSV into the platform schema, re-indexes DuckDB and retrains
                                      every model (feature importance, risk, throughput)
  GET  /api/download-sample-csv    -> 1,000-row SanDisk-style execution log to try the pipeline
  GET  /api/dataset/status          -> which dataset is active (benchmark vs uploaded)
  POST /api/dataset/reset           -> revert to the synthetic benchmark dataset

Arbitrary CSVs are mapped onto the canonical columns the analytics use (run_id, config_id,
failed/outcome, throughput_mbps, seed, timestamp, context, error_signature, log_trace, telemetry).
Missing canonical columns are filled with neutral defaults and listed in meta["synthetic_columns"].
"""
from __future__ import annotations

import io
import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import polars as pl
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sklearn.ensemble import RandomForestClassifier

from .auth import get_current_user, require_permission
from .config import DATA_DIR
from .generator import generate_dataset
from .store import store

MAX_BYTES = 200 * 1024 * 1024
PENDING_DIR = DATA_DIR / "pending"
PENDING_DIR.mkdir(exist_ok=True)
MIN_ROWS, MIN_CLASS = 50, 10
N_KEY, N_SANDBOX = 12, 10

CANON_TELEMETRY = ["iops", "latency_p99_ms", "cpu_util", "mem_util", "retry_count", "instability_index"]
RESERVED = {"run_id", "config_id", "outcome", "failed", "error_signature", "log_trace", "timestamp", "environment",
            "hardware", "workload", "throughput_mbps", "seed", *CANON_TELEMETRY}

# role detection patterns (applied to sanitized lower-case names)
PATTERNS = {
    "outcome": re.compile(r"^(outcome|status|result|verdict|pass_?fail|passed|failed|is_?fail(ed|ure)?|is_?pass(ed)?|failure|label|test_result)$"),
    "performance": re.compile(r"(throughput|mbps|mb_s|bandwidth|perf(ormance)?(_score)?|speed|gbps|score)"),
    "run_id": re.compile(r"^(run_?id|execution_?id|exec_?id|test_?id|job_?id|id|run)$"),
    "timestamp": re.compile(r"(timestamp|datetime|date|time|started?_?at|start_?time)"),
    "seed": re.compile(r"^(seed|random_?seed|rng_?seed|test_?seed)$"),
    "error_signature": re.compile(r"(error_?signature|signature|error_?code|fail(ure)?_?(reason|code|mode|type)|error_?type)"),
    "log": re.compile(r"(log|trace|message|stderr|stdout)"),
    "config_id": re.compile(r"^(config_?id|profile_?id|configuration_?id|config_?name|profile)$"),
    "environment": re.compile(r"^(environment|env|stage|site|lab)$"),
    "hardware": re.compile(r"^(hardware|hw|device|platform|gpu|sku|board|drive_?model|model)$"),
    "workload": re.compile(r"^(workload|pattern|test_?type|benchmark|scenario)$"),
}
RANDOM_RE = re.compile(r"(^rand|random|temperature|temp_?c?$|jitter|traffic|noise|droop|perturb|ambient|humidity)")
TELEMETRY_RE = re.compile(r"(latency|cpu|mem(ory)?_|util|retry_?count|retries|instab|iops|duration|elapsed|power_w|error_count|crc_errors|queue_wait)")

FAIL_TOKENS = {"fail", "failed", "failure", "error", "err", "ko", "nok", "abort", "aborted", "crash", "crashed", "timeout", "f"}


def sanitize(df: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, str]]:
    """Lower-case snake_case, unique column names (DuckDB identifiers are case-insensitive)."""
    seen: dict[str, int] = {}
    mapping = {}
    for c in df.columns:
        base = re.sub(r"[^0-9a-zA-Z]+", "_", c.strip()).strip("_").lower() or "col"
        if base[0].isdigit():
            base = f"c_{base}"
        name = base
        while name in seen:
            seen[base] += 1
            name = f"{base}_{seen[base]}"
        seen.setdefault(name, 0)
        mapping[c] = name
    return df.rename(mapping), {v: k for k, v in mapping.items()}


def read_csv(raw: bytes) -> pl.DataFrame:
    try:
        df = pl.read_csv(io.BytesIO(raw), infer_schema_length=20000, try_parse_dates=False, ignore_errors=True, truncate_ragged_lines=True)
    except Exception as e:
        raise HTTPException(400, f"Could not parse CSV: {e}")
    if df.width < 2:
        raise HTTPException(400, "CSV needs at least 2 columns (an outcome and one configuration parameter)")
    return df


def _is_numeric(s: pl.Series) -> bool:
    return s.dtype.is_numeric() or s.dtype == pl.Boolean


def _fail_values(name: str, values: list[str]) -> list[str]:
    vals = [v for v in values if v not in ("", "null", "none")]
    tokens = [v for v in vals if v in FAIL_TOKENS]
    if tokens:
        return tokens
    if set(vals) <= {"0", "1", "true", "false", "0.0", "1.0"}:
        if re.search(r"pass|success|ok", name):
            return [v for v in vals if v in ("0", "false", "0.0")]
        return [v for v in vals if v in ("1", "true", "1.0")]
    return []


def leaks_outcome(col: pl.Series, failed: pl.Series) -> bool:
    """True if a low-cardinality column determines pass/fail exactly (a leaked label such as a
    second `failed`/`passed` flag or an unmapped error-signature column). Training on it would
    make every analysis trivially 'explained' by the label itself."""
    n = len(col)
    nu = col.n_unique()
    if nu < 2 or nu > max(20, int(0.2 * n)):
        return False
    pure = pl.DataFrame({"v": col, "f": failed}).group_by("v").agg(pl.col("f").n_unique().alias("k"))["k"].max()
    return pure == 1


def _failed_series(df: pl.DataFrame, outcome: str | None, fail_values: list[str]) -> pl.Series | None:
    if not outcome or not fail_values:
        return None
    return df[outcome].cast(pl.Utf8).str.strip_chars().str.to_lowercase().is_in(fail_values).cast(pl.Int64)


def detect(df: pl.DataFrame) -> dict:
    n = len(df)
    cols_info = []
    low_card_values: dict[str, list[str]] = {}
    for c in df.columns:
        s = df[c]
        nu = s.n_unique()
        if nu <= 12:
            low_card_values[c] = sorted({str(v).strip().lower() for v in s.drop_nulls().unique().to_list()})
        cols_info.append({"name": c, "dtype": str(s.dtype), "n_unique": nu, "null_pct": round(s.null_count() / max(n, 1), 3),
                          "sample": [str(v) for v in s.drop_nulls().head(3).to_list()]})

    mapping: dict = {k: None for k in PATTERNS}
    taken: set[str] = set()
    for role in ("outcome", "run_id", "config_id", "seed", "error_signature", "log", "timestamp", "environment", "hardware", "workload", "performance"):
        for c in df.columns:
            if c in taken or not PATTERNS[role].search(c):
                continue
            s = df[c]
            if role == "outcome" and (c not in low_card_values or len(low_card_values[c]) > 6):
                continue
            if role == "performance" and not _is_numeric(s):
                continue
            if role in ("seed",) and not _is_numeric(s):
                continue
            if role == "log" and _is_numeric(s):
                continue
            mapping[role] = c
            taken.add(c)
            break
    if mapping["outcome"] is None:  # fall back to any 2-valued column with pass/fail-like tokens
        for c, vals in low_card_values.items():
            if c not in taken and len(vals) == 2 and _fail_values(c, vals):
                mapping["outcome"] = c
                taken.add(c)
                break
    mapping["fail_values"] = _fail_values(mapping["outcome"], low_card_values.get(mapping["outcome"], [])) if mapping["outcome"] else []

    roles = {}
    for info in cols_info:
        c = info["name"]
        if c in taken:
            continue
        s = df[c]
        if _is_numeric(s) and RANDOM_RE.search(c):
            roles[c] = "random"
        elif _is_numeric(s) and (TELEMETRY_RE.search(c) or c in CANON_TELEMETRY):
            roles[c] = "telemetry"
        elif not _is_numeric(s) and info["n_unique"] > 50 and info["n_unique"] > 0.5 * n:
            roles[c] = "ignore"  # id-like / free text
        elif info["n_unique"] <= 1:
            roles[c] = "ignore"  # constant
        else:
            roles[c] = "config"
    failed = _failed_series(df, mapping["outcome"], mapping["fail_values"])
    if failed is not None:
        for c, r in roles.items():
            if r != "ignore" and leaks_outcome(df[c], failed):
                roles[c] = "ignore"  # leaked label: perfectly predicts the outcome
    mapping["roles"] = roles
    for info in cols_info:
        info["role"] = next((r for r in PATTERNS if mapping.get(r) == info["name"]), None) or roles.get(info["name"], "ignore")
    return {"rows": n, "columns": cols_info, "mapping": mapping, "low_card_values": low_card_values}


# --------------------------------------------------------------------------- canonicalization
def _num(s: pl.Series) -> pl.Series:
    s = s.cast(pl.Float64, strict=False)
    med = s.median()
    return s.fill_null(med if med is not None else 0.0).fill_nan(med if med is not None else 0.0)


def _config_series(s: pl.Series) -> pl.Series:
    if s.dtype == pl.Boolean:
        return s.cast(pl.Int64).fill_null(0)
    if s.dtype.is_integer():
        med = s.median()
        return s.fill_null(int(med) if med is not None else 0).cast(pl.Int64)
    if s.dtype.is_numeric():
        return _num(s)
    return s.cast(pl.Utf8).fill_null("unknown").str.strip_chars()


def _choices(s: pl.Series) -> tuple[str, list]:
    if s.dtype == pl.Utf8:
        vals = s.value_counts(sort=True).head(24)[s.name].to_list()
        return "cat", sorted(vals)
    uniq = sorted(s.unique().to_list())
    if set(uniq) <= {0, 1}:
        return "bool", [0, 1]
    if len(uniq) <= 24:
        return ("int" if s.dtype.is_integer() else "float"), uniq
    qs = sorted({round(float(q), 4) for q in np.quantile(s.to_numpy(), np.linspace(0, 1, 9))})
    return ("int" if s.dtype.is_integer() else "float"), [int(q) if s.dtype.is_integer() else q for q in qs]


def _derive_config_id(df: pl.DataFrame, ranked: list[str]) -> pl.Series:
    """Group rows into configuration profiles; coarsen until profiles repeat enough to analyse."""
    for k in (len(ranked), 8, 5, 3, 2, 1):
        cols = ranked[:k]
        gid = df.select(pl.struct(cols).hash(seed=0).rank("dense").alias("g"))["g"]
        sizes = gid.value_counts()["count"]
        if float(sizes.median()) >= 3 or k == 1:
            return pl.Series("config_id", [f"CFG-{g:04d}" for g in gid.to_list()])
    raise AssertionError


def canonicalize(raw_df: pl.DataFrame, mapping: dict, filename: str) -> tuple[pl.DataFrame, dict]:
    df = raw_df
    n = len(df)
    oc = mapping.get("outcome")
    if not oc or oc not in df.columns:
        raise HTTPException(422, "Select the column that holds the pass/fail outcome")
    fail_vals = {str(v).strip().lower() for v in mapping.get("fail_values") or []}
    if not fail_vals:
        raise HTTPException(422, f"Select which value(s) of '{oc}' mean FAIL")
    failed = df[oc].cast(pl.Utf8).str.strip_chars().str.to_lowercase().is_in(list(fail_vals)).cast(pl.Int64)
    n_fail = int(failed.sum())
    if n < MIN_ROWS:
        raise HTTPException(422, f"Need at least {MIN_ROWS} rows (got {n})")
    if n_fail < MIN_CLASS or n - n_fail < MIN_CLASS:
        raise HTTPException(422, f"Need at least {MIN_CLASS} passing and {MIN_CLASS} failing runs (got {n - n_fail} pass / {n_fail} fail)")

    special = {mapping.get(k) for k in ("outcome", "performance", "run_id", "timestamp", "seed", "error_signature", "log", "config_id", "environment", "hardware", "workload")} - {None}
    roles = {c: r for c, r in (mapping.get("roles") or {}).items() if c in df.columns and c not in special}
    out: dict[str, pl.Series] = {}
    synthetic: list[str] = []

    def safe(name: str) -> str:
        return f"x_{name}" if name in RESERVED else name

    used = sorted(special | {c for c, r in roles.items() if r != "ignore"})
    missing = {c: int(df[c].null_count()) for c in used if df[c].null_count()}  # recorded before neutral fills
    config_cols = [c for c, r in roles.items() if r == "config"]
    if not config_cols:
        raise HTTPException(422, "Mark at least one column as a configuration parameter")
    cfg = {safe(c): _config_series(df[c]).alias(safe(c)) for c in config_cols}
    rnd = {}
    for c in [c for c, r in roles.items() if r == "random"]:
        if _is_numeric(df[c]):
            rnd[safe(c)] = _num(df[c]).alias(safe(c))
        else:  # non-numeric "random" columns behave like configuration
            cfg[safe(c)] = _config_series(df[c]).alias(safe(c))
    tel = {}
    for c in [c for c, r in roles.items() if r == "telemetry"]:
        if _is_numeric(df[c]):
            name = c if c in CANON_TELEMETRY else safe(c)
            tel[name] = _num(df[c]).alias(name)

    # run id / outcome / performance
    if mapping.get("run_id"):
        rid = df[mapping["run_id"]].cast(pl.Utf8).fill_null("").str.to_uppercase()
        if rid.n_unique() < n or (rid == "").any():
            rid = pl.Series([f"{v}-{i}" if v else f"RUN-{i:06d}" for i, v in enumerate(rid.to_list())])
    else:
        rid = pl.Series([f"RUN-{i:06d}" for i in range(n)])
    out["run_id"] = rid.alias("run_id")
    out["failed"] = failed.alias("failed")
    out["outcome"] = pl.Series("outcome", np.where(failed.to_numpy() == 1, "fail", "pass"))
    if mapping.get("performance"):
        out["throughput_mbps"] = _num(df[mapping["performance"]]).alias("throughput_mbps")
    else:
        out["throughput_mbps"] = pl.Series("throughput_mbps", np.zeros(n))
        synthetic.append("throughput_mbps")

    # timestamp
    ts = None
    if mapping.get("timestamp"):
        s = df[mapping["timestamp"]]
        if s.dtype in (pl.Datetime, pl.Date):
            ts = s.cast(pl.Datetime("ms"))
        elif s.dtype.is_numeric():  # epoch seconds / ms
            v = s.cast(pl.Float64)
            unit = "ms" if (v.median() or 0) > 1e11 else "s"
            ts = (v * (1 if unit == "ms" else 1000)).cast(pl.Int64).cast(pl.Datetime("ms"))
        else:
            ts = s.cast(pl.Utf8).str.to_datetime(strict=False, time_unit="ms")
        if ts.null_count() > 0.5 * n:
            ts = None
        else:
            ts = ts.fill_null(strategy="forward").fill_null(strategy="backward")
    if ts is None:
        start = datetime(2026, 7, 1)
        ts = pl.Series([start + timedelta(minutes=int(i * 90 * 24 * 60 / max(n, 1))) for i in range(n)], dtype=pl.Datetime("ms"))
        synthetic.append("timestamp")
    out["timestamp"] = ts.alias("timestamp")

    for ctx in ("environment", "hardware", "workload"):
        if mapping.get(ctx):
            out[ctx] = df[mapping[ctx]].cast(pl.Utf8).fill_null("unknown").alias(ctx)
        else:
            out[ctx] = pl.Series(ctx, ["all"] * n)
            synthetic.append(ctx)

    # seed
    if mapping.get("seed"):
        out["seed"] = df[mapping["seed"]].cast(pl.Int64, strict=False).fill_null(0).alias("seed")
    else:
        out["seed"] = pl.Series("seed", np.zeros(n, dtype=np.int64))
        synthetic.append("seed")

    # telemetry defaults
    for t in CANON_TELEMETRY:
        if t not in tel:
            tel[t] = pl.Series(t, np.zeros(n))
            synthetic.append(t)

    # error signature + logs
    if mapping.get("error_signature"):
        sig = df[mapping["error_signature"]].cast(pl.Utf8).fill_null("").str.strip_chars().str.to_uppercase().str.replace_all(r"\s+", "_")
        sig = pl.Series(np.where(failed.to_numpy() == 1, np.where(sig.to_numpy() == "", "UNCLASSIFIED_FAILURE", sig.to_numpy()), "NONE"))
    else:
        sig = pl.Series(np.where(failed.to_numpy() == 1, "UNCLASSIFIED_FAILURE", "NONE"))
    out["error_signature"] = sig.alias("error_signature")

    leaked = [name for group in (cfg, rnd, tel) for name, col in list(group.items()) if leaks_outcome(col, failed)]
    for group in (cfg, rnd, tel):
        for name in leaked:
            group.pop(name, None)
    if not cfg:
        raise HTTPException(422, f"Every configuration column perfectly predicts the outcome ({', '.join(leaked)}); nothing left to analyse")
    frame = pl.DataFrame([*out.values(), *cfg.values(), *rnd.values(), *tel.values()])

    # rank configuration parameters with a quick forest to pick key / sandbox params and profiles
    cfg_names = list(cfg.keys())
    X = np.column_stack([
        frame[c].cast(pl.Utf8).rank("dense").cast(pl.Float64).to_numpy() if frame[c].dtype == pl.Utf8 else frame[c].cast(pl.Float64).to_numpy()
        for c in cfg_names
    ])
    rf = RandomForestClassifier(n_estimators=80, max_depth=10, min_samples_leaf=3, n_jobs=-1, random_state=0, class_weight="balanced")
    rf.fit(X, failed.to_numpy())
    ranked = [cfg_names[i] for i in np.argsort(-rf.feature_importances_)]

    if mapping.get("config_id"):
        cid = df[mapping["config_id"]].cast(pl.Utf8).fill_null("UNKNOWN").str.to_uppercase()
    else:
        cid = _derive_config_id(frame, ranked)
    frame = frame.with_columns(cid.alias("config_id"))

    if mapping.get("log"):
        logs = df[mapping["log"]].cast(pl.Utf8).fill_null("").str.replace_all(r" \| |\\n|\r\n", "\n")
    else:
        logs = None
    frame = frame.with_columns(_build_logs(frame, ranked[:5], logs).alias("log_trace"))

    key_params = []
    for c in ranked[:N_KEY]:
        kind, choices = _choices(frame[c])
        key_params.append({"name": c, "kind": kind, "choices": choices, "desc": f"Uploaded column '{c}'"})
    random_vars = list(rnd.keys()) + ([] if "seed" in synthetic else ["seed"])
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_runs": n,
        "config_params": ranked,
        "key_params": key_params,
        "sandbox_params": [p["name"] for p in key_params[:N_SANDBOX]],
        "random_vars": random_vars,
        "telemetry": ["throughput_mbps", *tel.keys()],
        "context": ["environment", "hardware", "workload"],
        "n_profiles": int(frame["config_id"].n_unique()),
        "log_lines": int(frame["log_trace"].str.count_matches("\n").sum() + n),
        "synthetic_columns": synthetic,
        "leakage_dropped": leaked,
        "missing_values": missing,
        "log_coverage": round(float((df[mapping["log"]].cast(pl.Utf8).fill_null("").str.strip_chars() != "").mean()), 4) if mapping.get("log") else 0.0,
        "source": "uploaded",
        "filename": filename,
    }
    if not random_vars:  # the ML layer expects at least one randomized feature
        frame = frame.with_columns(pl.lit(0.0).alias("rand_none"))
        meta["random_vars"] = ["rand_none"]
    return frame, meta


def _build_logs(frame: pl.DataFrame, cols: list[str], logs: pl.Series | None) -> pl.Series:
    vals = frame.select(cols).rows()
    sig = frame["error_signature"].to_list()
    tput = frame["throughput_mbps"].to_list()
    given = logs.to_list() if logs is not None else [None] * len(frame)
    out = []
    for row, s, t, g in zip(vals, sig, tput, given):
        head = "INFO  cfg: " + " ".join(f"{c}={v}" for c, v in zip(cols, row))
        if g:
            out.append(f"{head}\n{g}")
        elif s != "NONE":
            out.append(f"{head}\nERROR run: execution failed signature={s}\nRESULT FAIL signature={s} throughput={t}")
        else:
            out.append(f"{head}\nINFO  perf: throughput={t}\nRESULT PASS")
    return pl.Series(out)


# --------------------------------------------------------------------------- routes
router = APIRouter(prefix="/api", tags=["upload"])


async def _read_upload(file: UploadFile) -> bytes:
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "Only .csv files are supported")
    buf = bytearray()
    while chunk := await file.read(1 << 20):
        buf += chunk
        if len(buf) > MAX_BYTES:
            raise HTTPException(413, f"File exceeds {MAX_BYTES // (1024 * 1024)} MB")
    if not buf:
        raise HTTPException(400, "Empty file")
    return bytes(buf)


def _cleanup_pending(max_age_s: int = 7200) -> None:
    now = time.time()
    for p in PENDING_DIR.glob("*.csv"):
        if now - p.stat().st_mtime > max_age_s:
            p.unlink(missing_ok=True)


@router.post("/upload-csv/preview")
async def upload_preview(file: UploadFile = File(...), user: dict = Depends(require_permission("upload"))):
    raw = await _read_upload(file)
    df, _ = sanitize(await run_in_threadpool(read_csv, raw))
    _cleanup_pending()
    upload_id = uuid.uuid4().hex
    (PENDING_DIR / f"{upload_id}.csv").write_bytes(raw)
    (PENDING_DIR / f"{upload_id}.json").write_text(json.dumps({"filename": file.filename}))
    det = await run_in_threadpool(detect, df)
    return {"upload_id": upload_id, "filename": file.filename, "size_bytes": len(raw), **det}


@router.post("/upload-csv")
async def upload_csv(
    file: UploadFile | None = File(None),
    upload_id: str | None = Form(None),
    mapping: str | None = Form(None),
    user: dict = Depends(require_permission("upload")),
):
    if file is not None:
        raw, filename = await _read_upload(file), file.filename
    elif upload_id and re.fullmatch(r"[0-9a-f]{32}", upload_id) and (PENDING_DIR / f"{upload_id}.csv").exists():
        raw = (PENDING_DIR / f"{upload_id}.csv").read_bytes()
        filename = json.loads((PENDING_DIR / f"{upload_id}.json").read_text())["filename"]
    else:
        raise HTTPException(400, "Provide a CSV file or a valid upload_id from /api/upload-csv/preview")

    def ingest():
        t0 = time.perf_counter()
        df, _ = sanitize(read_csv(raw))
        det = detect(df)
        m = det["mapping"]
        if mapping:
            try:
                user_map = json.loads(mapping)
            except json.JSONDecodeError:
                raise HTTPException(400, "mapping must be JSON")
            m = {**m, **{k: v for k, v in user_map.items() if k != "roles"}}
            if isinstance(user_map.get("roles"), dict):
                m["roles"] = {**m["roles"], **user_map["roles"]}
        frame, meta = canonicalize(df, m, filename)
        prep_s = time.perf_counter() - t0
        store.activate_upload(frame, meta)
        return {"detected": det["columns"], "mapping": m, "prep_seconds": round(prep_s, 2)}

    res = await run_in_threadpool(ingest)
    if upload_id:
        for ext in ("csv", "json"):
            (PENDING_DIR / f"{upload_id}.{ext}").unlink(missing_ok=True)
    return {**res, "dataset": store.dataset_status(), "model_auc": store.bundle.auc, "training_seconds": round(store.train_seconds, 2),
            "summary": {"rows": len(store.df), "config_params": len(store.meta["config_params"]), "random_vars": len(store.meta["random_vars"]),
                        "profiles": store.meta["n_profiles"], "failures": int(store.df["failed"].sum())}}


@router.get("/download-sample-csv")
def download_sample_csv(user: dict = Depends(get_current_user)):
    df = generate_dataset(1000, 100, 51, seed=7, persist=False)
    df = (df.drop("failed", "config_id")
            .with_columns(pl.col("outcome").str.to_uppercase().alias("status"), pl.col("log_trace").str.replace_all("\n", " | "))
            .drop("outcome"))
    front = ["run_id", "timestamp", "status", "error_signature", "throughput_mbps", "environment", "hardware", "workload", "seed"]
    df = df.select(front + [c for c in df.columns if c not in front])
    buf = io.BytesIO()
    df.write_csv(buf)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="sandisk_execution_log_sample.csv"'})


@router.get("/dataset/status")
def dataset_status(user: dict = Depends(get_current_user)):
    return store.dataset_status()


@router.post("/dataset/reset")
async def dataset_reset(user: dict = Depends(require_permission("upload"))):
    await run_in_threadpool(store.reset_to_benchmark)
    return store.dataset_status()
