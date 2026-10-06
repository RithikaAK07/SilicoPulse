"""Semantic column detection: explainable role + confidence for every column.

Signals combined per column: normalized name tokens/aliases, dtype, cardinality, value patterns
(pass/fail tokens, log levels), timestamp parse rate and numeric continuity. Nothing is invented:
roles describe columns that exist, and a role is only proposed when the data supports it.
"""
from __future__ import annotations

import re

import numpy as np
import polars as pl

from .normalizer import parse_timestamps, unit_info

FAIL_TOKENS = {"fail", "failed", "failure", "error", "err", "ko", "nok", "abort", "aborted", "crash", "crashed", "timeout", "f"}
PASS_TOKENS = {"pass", "passed", "ok", "success", "succeeded", "true", "false", "0", "1", "p", "s", "good", "bad", "yes", "no", "0.0", "1.0"}
LEVELS = {"trace", "debug", "info", "notice", "warn", "warning", "error", "err", "critical", "fatal", "severe"}

ALIASES: dict[str, set[str]] = {
    "timestamp": {"timestamp", "time", "datetime", "date", "event_time", "event_timestamp", "started_at", "start_time", "ts", "created_at",
                  "recorded_at", "logged_at", "time_stamp", "sample_time", "measured_at", "date_time"},
    "outcome": {"outcome", "status", "result", "verdict", "pass", "passed", "fail", "failed", "pass_fail", "passfail", "test_result",
                "is_fail", "is_failed", "is_pass", "is_passed", "success", "label"},
    "performance": {"throughput", "throughput_mbps", "bandwidth", "speed", "performance", "score", "iops", "latency", "mbps", "gbps"},
    "run_id": {"run_id", "execution_id", "exec_id", "test_id", "job_id", "id", "run", "uuid", "record_id"},
    "config_id": {"config_id", "profile_id", "configuration_id", "config_name"},
    "seed": {"seed", "random_seed", "rng_seed", "test_seed"},
    "environment": {"environment", "env", "site", "stage", "lab", "location"},
    "hardware": {"hardware", "hw", "device", "machine", "model", "platform", "board", "cnc", "equipment", "asset", "sku", "unit", "machine_id",
                 "device_id", "asset_id", "host", "hostname"},
    "workload": {"workload", "scenario", "benchmark", "pattern", "test_type", "job_type"},
    "error": {"error", "error_code", "error_signature", "signature", "failure_reason", "fail_reason", "exception", "fault", "error_type", "alarm",
              "fault_code"},
    "level": {"level", "severity", "loglevel", "log_level"},
    "log": {"log", "message", "msg", "trace", "raw", "text", "stderr", "stdout", "description", "log_trace", "details"},
}
TELEMETRY_TOKENS = {"vibration", "vib", "temperature", "temp", "rpm", "cpu", "memory", "mem", "power", "pressure", "humidity", "current", "voltage",
                    "volt", "frequency", "freq", "retry", "retries", "torque", "load", "accel", "acceleration", "flow", "util", "utilization", "watt",
                    "amps", "amp", "rms", "noise", "spindle", "motor", "bearing", "coolant", "vibration_g", "kw", "energy", "level_pct",
                    "error_count", "retry_count", "latency", "jitter", "iops", "throughput", "duty", "duration", "elapsed"}
CONFIG_TOKENS = {"config", "setting", "settings", "parameter", "param", "mode", "batch", "batch_size", "queue", "depth", "block", "threads", "policy",
                 "profile", "size", "level_setting", "option", "flag", "cfg"}
MACHINE_TOKENS = {"vibration", "rpm", "spindle", "motor", "torque", "pressure", "cnc", "machine", "coolant", "current", "voltage", "bearing",
                  "humidity", "temp", "temperature", "power", "flow", "accel"}


def tokens(name: str) -> list[str]:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return [t for t in re.split(r"[^a-z0-9]+", s.lower()) if t]


def norm(name: str) -> str:
    return "_".join(tokens(name))


def _is_numeric(s: pl.Series) -> bool:
    return s.dtype.is_numeric() or s.dtype == pl.Boolean


BOOL_WORDS = {"true", "false", "yes", "no", "t", "f", "y", "n"}


def display_type(s: pl.Series, values: list[str] | None, ts_rate: float = 0.0) -> str:
    """Basic type shown to the user: Boolean / Datetime / Int64 / Float64 / String / Empty."""
    if s.null_count() == len(s):
        return "Empty"
    if s.dtype == pl.Boolean or (s.dtype == pl.Utf8 and values and set(values) <= BOOL_WORDS and len(values) <= 2
                                 and not (set(values) <= {"t", "f", "y", "n"} and len(s) < 3)):
        return "Boolean"
    if s.dtype in (pl.Date, pl.Datetime) or isinstance(s.dtype, pl.Datetime) or (s.dtype == pl.Utf8 and ts_rate >= 0.8):
        return "Datetime"
    if s.dtype.is_integer():
        return "Int64"
    if s.dtype.is_float():
        return "Float64"
    return "String"


def profile_column(df: pl.DataFrame, c: str, invalid: int = 0) -> dict:
    s = df[c]
    n = len(s)
    nn = s.drop_nulls()
    nu = int(nn.n_unique()) if len(nn) else 0
    missing = int(s.null_count())
    info = {"name": c, "normalized": norm(c), "dtype": str(s.dtype), "rows": n, "missing": missing,
            "missing_pct": round(missing / n, 4) if n else None, "n_unique": nu,
            "invalid": int(invalid), "sample": [str(v)[:80] for v in nn.head(3).to_list()]}
    vals = None
    if nu <= 12:
        vals = sorted({str(v).strip().lower() for v in nn.unique().to_list()})
    info["values"] = vals
    if _is_numeric(s) and len(nn):
        arr = nn.cast(pl.Float64).to_numpy()
        info["inf"] = int(np.isinf(arr).sum())
        info["nan"] = int(np.isnan(arr).sum())
        info["continuous"] = nu > 20 and nu > 0.05 * len(nn)
    if s.dtype == pl.Utf8 and len(nn):
        info["avg_len"] = float(nn.str.len_chars().mean())
    return info


def _name_score(nm: str, toks: list[str], role: str) -> tuple[float, str]:
    al = ALIASES.get(role, set())
    if nm in al:
        return 0.9, f"name '{nm}' is a known {role} field"
    hit = [t for t in toks if t in al]
    if hit:
        return 0.7, f"name contains '{hit[0]}'"
    return 0.0, ""


def _fallback_column(df: pl.DataFrame, c: str, reason: str) -> dict:
    """Safe entry for a column that could not be profiled: the table row is still shown."""
    try:
        s = df[c]
        n, missing, dtype = len(s), int(s.null_count()), str(s.dtype)
    except Exception:
        n, missing, dtype = len(df), 0, "Unknown"
    return {"name": c, "normalized": norm(c), "dtype": dtype, "display_type": "Empty" if n and missing == n else ("String" if dtype in ("String", "Utf8") else dtype),
            "rows": n, "missing": missing, "missing_pct": round(missing / n, 4) if n else None, "n_unique": 0, "invalid": 0, "sample": [],
            "values": None, "role": "unclassified", "confidence": 0.5, "reason": reason, "alternatives": [], "ambiguous": False,
            "unit": None, "timestamp_parse_rate": 0.0}


def detect_roles(df: pl.DataFrame, invalid: dict[str, int] | None = None) -> list[dict]:
    """Return one explainable mapping entry per column (every column of the header, never fails)."""
    out = []
    for c in df.columns:
        try:
            out.append(_detect_column(df, c, (invalid or {}).get(c, 0)))
        except Exception:
            out.append(_fallback_column(df, c, "column could not be profiled; please set its role manually"))
    return out


def _detect_column(df: pl.DataFrame, c: str, invalid_count: int) -> dict:
    if df[c].null_count() == len(df[c]):
        return _fallback_column(df, c, "column has no values" if len(df) else "no data rows yet")
    ts_parsed: dict[str, float] = {}
    p = profile_column(df, c, invalid_count)
    s = df[c]
    nm, toks = p["normalized"], tokens(c)
    cands: list[tuple[float, str, str]] = []  # (confidence, role, reason)
    numeric = _is_numeric(s)
    # --- timestamp (name and/or values)
    name_ts, why_ts = _name_score(nm, toks, "timestamp")
    _, rate, fmt = parse_timestamps(s, name_hint=name_ts > 0)
    ts_parsed[c] = rate
    if rate >= 0.8 and (name_ts > 0 or s.dtype in (pl.Datetime, pl.Date) or not numeric):
        conf = 0.95 if name_ts > 0 else (0.85 if s.dtype in (pl.Datetime, pl.Date) else 0.75)
        cands.append((conf, "timestamp", f"{why_ts + '; ' if why_ts else ''}{rate:.0%} of values parse as timestamps ({fmt})"))
    # --- outcome (pass/fail tokens in a low-cardinality column)
    vals = p["values"] or []
    name_oc, why_oc = _name_score(nm, toks, "outcome")
    has_fail = any(v in FAIL_TOKENS for v in vals)
    two_valued = 2 <= len(vals) <= 6
    if two_valued and has_fail and all(v in FAIL_TOKENS | PASS_TOKENS for v in vals):
        cands.append((0.95, "outcome", f"values {vals} look like pass/fail labels"))
    elif two_valued and name_oc > 0 and set(vals) <= {"0", "1", "true", "false", "0.0", "1.0", "yes", "no"}:
        cands.append((0.85, "outcome", f"{why_oc}; binary values {vals}"))
    # --- log level / log text
    if not numeric and vals and set(vals) <= LEVELS:
        cands.append((0.9, "level", f"values {vals} are log levels"))
    name_log, why_log = _name_score(nm, toks, "log")
    if not numeric and (p.get("avg_len", 0) > 40 or name_log >= 0.9):
        cands.append((0.8 if name_log else 0.6, "log", why_log or "long free-text values"))
    # --- identifiers
    for role in ("run_id", "config_id", "seed"):
        sc, why = _name_score(nm, toks, role)
        if sc:
            if role == "run_id" and p["n_unique"] < 0.9 * max(1, p["rows"] - p["missing"]):
                continue
            if role == "seed" and not numeric:
                continue
            cands.append((sc + 0.05, role, why))
    if not numeric and p["n_unique"] > 50 and p["n_unique"] >= 0.95 * max(1, p["rows"] - p["missing"]) and not cands:
        cands.append((0.5, "run_id", "unique per row (identifier-like)"))
    # --- context
    if not numeric:
        for role in ("environment", "hardware", "workload", "error"):
            sc, why = _name_score(nm, toks, role)
            if sc:
                cands.append((sc, role, why))
    # --- numeric measurements
    if numeric:
        unit = unit_info(c)
        tel_hit = [t for t in toks if t in TELEMETRY_TOKENS] or ([nm] if nm in TELEMETRY_TOKENS else [])
        perf_sc, why_perf = _name_score(nm, toks, "performance")
        cfg_hit = [t for t in toks if t in CONFIG_TOKENS]
        if perf_sc:
            cands.append((perf_sc + 0.05, "performance", why_perf))
        if tel_hit:
            cands.append((0.85, "telemetry", f"name contains measurement term '{tel_hit[0]}'" + (f"; unit {unit['unit']}" if unit["unit"] else "")))
        elif unit["unit"]:
            cands.append((0.75, "telemetry", f"unit suffix indicates {unit['unit']}"))
        if cfg_hit:
            cands.append((0.7, "config", f"name contains setting term '{cfg_hit[0]}'"))
        if p.get("continuous"):
            cands.append((0.55, "telemetry", f"continuous numeric measurement ({p['n_unique']} distinct values)"))
        else:
            cands.append((0.5, "config", f"discrete numeric values ({p['n_unique']} distinct)"))
    elif not cands:
        cands.append((0.5, "config", f"categorical values ({p['n_unique']} distinct)") if p["n_unique"] > 1 else (0.6, "ignore", "constant column (same value in every row)"))
    if not cands:  # nothing recognisable: say so instead of guessing
        cands.append((0.5, "unclassified", "no name or value pattern recognised"))
    cands.sort(key=lambda x: -x[0])
    best = cands[0]
    alts = [{"role": r, "confidence": round(cf, 2)} for cf, r, _ in cands[1:3] if r != best[1]]
    return {**p, "display_type": display_type(s, p["values"], ts_parsed[c]), "role": best[1], "confidence": round(best[0], 2),
            "reason": best[2], "alternatives": alts, "ambiguous": bool(alts and best[0] - alts[0]["confidence"] < 0.1),
            "unit": unit_info(c)["unit"] if numeric else (unit_info(c)["unit"] if "%" in c else None),
            "timestamp_parse_rate": round(ts_parsed[c], 3)}


DATA_TYPE_LABELS = {
    "execution_log": "Execution log (pass/fail outcomes)",
    "industrial_telemetry": "Industrial telemetry",
    "time_series_telemetry": "Time-series telemetry",
    "event_log": "Event log",
    "measurement_table": "Measurement table (no timestamp)",
    "tabular": "Generic table",
}


def classify(roles: list[dict], kind: str, has_outcome: bool) -> str:
    if has_outcome:
        return "execution_log"
    by = {r["name"]: r["role"] for r in roles}
    ts = [n for n, r in by.items() if r == "timestamp"]
    tele = [r for r in roles if r["role"] in ("telemetry", "performance") or (r["role"] == "config" and r["dtype"] != "String" and r.get("continuous"))]
    if kind in ("log",) or (kind == "json_lines" and any(r == "level" for r in by.values())):
        return "event_log"
    if tele and ts:
        names = " ".join(r["normalized"] for r in tele)
        return "industrial_telemetry" if any(t in names for t in MACHINE_TOKENS) else "time_series_telemetry"
    if tele or any(r["role"] == "config" and r["dtype"] != "String" for r in roles):
        return "measurement_table"
    return "tabular"
