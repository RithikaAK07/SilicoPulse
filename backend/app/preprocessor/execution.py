"""Execution-data adapter: any execution table -> the input the EXISTING pipeline already expects.

RAW TABLE -> value normalization -> semantic field detection -> mapping -> outcome / timestamp
normalization -> confirmations -> (frame, detect() result) handed unchanged to
upload_handler.canonicalize + store.activate_upload.

The existing algorithms are reused, not modified:
  * upload_handler.detect() still assigns every role. Synonymous column names are temporarily
    shown to it under the canonical concept name it already understands (e.g. `spindle_speed` is
    read as `rpm`), so equivalent files map identically. The mapping is then translated back, so
    the user's own column names are kept in every dashboard.
  * canonicalize() receives native datetimes and numbers, which its existing branches handle.

Nothing is guessed silently: if the PASS/FAIL polarity or the date order cannot be determined
reliably, the field is returned with `confirm: required` and ingestion is blocked until the user
confirms it.
"""
from __future__ import annotations

import re

import numpy as np
import polars as pl

from .mapper import tokens
from .models import ParsedTable
from .normalizer import parse_timestamps

# ----------------------------------------------------------------------------- concepts
# sanitized column name -> concept name the existing detector already understands.
CONCEPT_ALIASES: dict[str, str] = {
    # rotational speed (a machine measurement, never the throughput/performance metric)
    "spindle_speed": "rpm", "rotational_speed": "rpm", "rotation_speed": "rpm", "motor_speed": "rpm", "shaft_speed": "rpm",
    "fan_speed": "rpm", "rpm_value": "rpm", "spindle_rpm": "rpm", "motor_rpm": "rpm", "speed_rpm": "rpm", "drehzahl": "rpm",
    # temperature
    "temp": "temperature", "temp_c": "temperature", "temperatur": "temperature", "temperatura": "temperature", "temperature_c": "temperature",
    "motor_temperature": "temperature", "motor_temp": "temperature", "motor_temp_c": "temperature", "temp_value": "temperature",
    # time
    "zeit": "timestamp", "zeitstempel": "timestamp", "datum": "timestamp", "fecha": "timestamp", "fecha_hora": "timestamp",
    "horodatage": "timestamp", "date_heure": "timestamp", "data_ora": "timestamp", "tijd": "timestamp", "datetime_utc": "timestamp",
    "event_time": "timestamp", "event_timestamp": "timestamp", "date_time": "timestamp", "time_stamp": "timestamp", "logged_at": "timestamp",
    "recorded_at": "timestamp", "measured_at": "timestamp", "sample_time": "timestamp",
    # outcome
    "ergebnis": "result", "resultado": "result", "resultat": "result", "risultato": "result", "estado": "status", "statut": "status",
    "test_status": "status", "run_status": "status", "execution_status": "status", "final_status": "status", "test_outcome": "outcome",
    "pass_fail_status": "status", "pf": "pass_fail",
    # performance
    "bandwidth_mbps": "throughput_mbps", "throughput_mb_s": "throughput_mbps", "mb_per_sec": "throughput_mbps", "mbps": "throughput_mbps",
    "read_throughput": "throughput", "write_throughput": "throughput", "transfer_rate": "throughput",
}
_MACHINE_SPEED = re.compile(r"(spindle|motor|fan|shaft|rotor|rotation|rotational|wheel|pump|drum)")
_DATA_SPEED = re.compile(r"(read|write|transfer|io|data|network|net|link|bus|copy|seq|rand)")
_GENERIC_PERF = re.compile(r"^(speed|score|perf|performance|value|speed_value|perf_score)$")
_INDEX_NAME = re.compile(r"^(unnamed(_\d+)?|index|idx|row|row_?(id|num|number|no|index)|c_\d+|col|column|no|n|sr_?no|s_?no|serial|seq|sequence|_)$")

# ----------------------------------------------------------------------------- outcome polarity
FAIL_WORDS = {"fail", "failed", "failure", "error", "err", "errored", "ko", "nok", "abort", "aborted", "crash", "crashed", "timeout",
              "timed_out", "f", "fault", "faulted", "reject", "rejected", "ng", "bad", "red", "broken", "not_ok", "not ok"}
PASS_WORDS = {"pass", "passed", "success", "successful", "succeeded", "ok", "okay", "good", "p", "green", "accept", "accepted", "done",
              "complete", "completed"}
TRUE_VALUES, FALSE_VALUES = {"1", "true", "yes", "y", "t", "1.0"}, {"0", "false", "no", "n", "0.0"}
_FAIL_NAME = re.compile(r"(fail|error|err|defect|fault|crash|abort|reject|broken)")
_PASS_NAME = re.compile(r"(pass|success|succeed|ok|good|accept)")
_EMPTY = {"", "null", "none", "nan", "na", "n/a"}


def concept_of(col: str, numeric: bool) -> str | None:
    if col in CONCEPT_ALIASES:
        return CONCEPT_ALIASES[col]
    if numeric and "speed" in col and _MACHINE_SPEED.search(col) and not _DATA_SPEED.search(col):
        return "rpm"
    return None


def assess_outcome(name: str, values: list[str]) -> dict:
    """Decide which values mean FAIL, and whether that decision is reliable."""
    vals = [v for v in values if v not in _EMPTY]
    fail = [v for v in vals if v in FAIL_WORDS]
    passed = [v for v in vals if v in PASS_WORDS]
    binary = [v for v in vals if v in TRUE_VALUES | FALSE_VALUES]
    unknown = [v for v in vals if v not in FAIL_WORDS | PASS_WORDS | TRUE_VALUES | FALSE_VALUES]
    toks = set(tokens(name))
    nm = "_".join(toks)
    if fail and not binary and not unknown:
        return {"fail_values": fail, "reliable": True, "reason": f"values {vals} are explicit pass/fail words"}
    if fail and (unknown or binary):
        other = unknown + binary
        return {"fail_values": fail, "reliable": False,
                "reason": f"'{name}' contains {', '.join(repr(v) for v in other)} in addition to pass/fail words; "
                          f"rows with those values would be counted as NOT failed. Please confirm which values mean FAIL."}
    if binary and not unknown and not fail and not passed:
        fail_pol, pass_pol = bool(_FAIL_NAME.search(nm)), bool(_PASS_NAME.search(nm))
        if fail_pol and not pass_pol:
            return {"fail_values": [v for v in vals if v in TRUE_VALUES], "reliable": True,
                    "reason": f"column name '{name}' describes failure, so {sorted(set(vals) & TRUE_VALUES)} mean FAIL"}
        if pass_pol and not fail_pol:
            return {"fail_values": [v for v in vals if v in FALSE_VALUES], "reliable": True,
                    "reason": f"column name '{name}' describes success, so {sorted(set(vals) & FALSE_VALUES)} mean FAIL"}
        return {"fail_values": [], "reliable": False,
                "reason": f"'{name}' contains {' / '.join(vals)}; it is not clear which value means FAIL. Please select the FAIL value(s)."}
    if passed and not fail:
        return {"fail_values": [], "reliable": False,
                "reason": f"'{name}' contains {vals}: only success words were recognised. Please select which value(s) mean FAIL."}
    return {"fail_values": [], "reliable": False,
            "reason": f"The values {vals} of '{name}' are not recognised pass/fail labels. Please select which value(s) mean FAIL."}


def _legacy_ts_parse(s: pl.Series) -> tuple[float, bool]:
    """How canonicalize() would parse this text column on its own: (parse rate, timezone-aware result?)."""
    try:
        parsed = s.cast(pl.Utf8).str.to_datetime(strict=False, time_unit="ms")
    except Exception:
        return 0.0, False
    valid = len(s) - s.null_count()
    tz_aware = isinstance(parsed.dtype, pl.Datetime) and parsed.dtype.time_zone is not None
    return ((len(parsed) - parsed.null_count()) / valid if valid else 0.0), tz_aware


def _is_row_index(s: pl.Series) -> bool:
    if not s.dtype.is_integer() or s.null_count() or len(s) < 3:
        return False
    arr = s.to_numpy()
    return bool(arr[0] in (0, 1) and np.all(np.diff(arr) == 1))


def adapt(table: ParsedTable, raw: bytes | None, roles: list[dict]) -> tuple[pl.DataFrame, dict, dict]:
    """Return (frame for canonicalize, detect() result in the user's column names, adaptation report)."""
    from .. import upload_handler as uh
    from .parsers import infer_types

    transforms: list[dict] = []
    confirmations: list[dict] = []
    # 1. structure / value normalization ----------------------------------------------------
    if table.legacy_csv and raw is not None:
        frame = uh.read_csv(raw)  # the legacy reader: correct-format CSVs are read exactly as before
        notes: dict = {}
        frame, _ = infer_types(frame, notes=notes, only_special=True)  # only unit-suffixed / decimal-comma text
    else:
        frame = table.frame
        notes = dict(table.extra.get("value_normalization") or {})
    frame = frame.with_columns([pl.col(c).cast(pl.Utf8) for c, t in frame.schema.items()
                                if t in (pl.Date, pl.Time) or isinstance(t, (pl.Datetime, pl.Duration))])
    df, original = uh.sanitize(frame)  # original: sanitized -> original header
    for c, v in notes.items():
        sc = next((k for k, o in original.items() if o == c), c)
        transforms.append({"column": sc, "original": c, "transform": v["transform"], "unit": v.get("unit")})

    # 2. semantic field detection: show synonyms to the existing detector under their concept name
    rename: dict[str, str] = {}
    taken = set(df.columns)
    for c in df.columns:
        concept = concept_of(c, df[c].dtype.is_numeric())
        if concept and concept != c and concept not in taken and concept not in rename.values():
            rename[c] = concept
    back = {v: k for k, v in rename.items()}
    det = uh.detect(df.rename(rename)) if rename else uh.detect(df)
    if rename:
        tr = lambda x: back.get(x, x)  # noqa: E731
        m = det["mapping"]
        for k, v in list(m.items()):
            if k not in ("roles", "fail_values") and isinstance(v, str):
                m[k] = tr(v)
        m["roles"] = {tr(c): r for c, r in m["roles"].items()}
        det["low_card_values"] = {tr(c): v for c, v in det["low_card_values"].items()}
        for col in det["columns"]:
            col["name"] = tr(col["name"])
        for c, concept in rename.items():
            transforms.append({"column": c, "original": original.get(c, c), "transform": f"recognised as '{concept}' (synonym)"})
    m = det["mapping"]

    # 3. irrelevant columns: the provenance tag added when compatible files are combined, and a plain
    #    row index, are not configuration parameters (the model must not learn which file a row came from)
    if table.extra.get("members") and m["roles"].get("source_file", "ignore") != "ignore":
        m["roles"]["source_file"] = "ignore"
        transforms.append({"column": "source_file", "original": "source_file",
                           "transform": "provenance (file each row came from): kept as metadata, not a configuration parameter"})
    for c, r in list(m["roles"].items()):
        if r != "ignore" and (_INDEX_NAME.match(c) and _is_row_index(df[c]) or (_is_row_index(df[c]) and df[c].n_unique() == len(df))):
            m["roles"][c] = "ignore"
            transforms.append({"column": c, "original": original.get(c, c), "transform": "row index: ignored (not a configuration parameter)"})
    # semantic detector fills only fields the existing detector left empty (e.g. non-English names)
    _fill_gaps(m, roles, {o: sc for sc, o in original.items()})
    # a generically named "performance" column ("speed", "score") holding only a few fixed values is a
    # setting (e.g. 1200/1800/2400 rpm), not a measured metric: give it the role the existing detector
    # gives any discrete numeric column. Specific names (throughput, mbps, iops, ...) are never touched.
    perf = m.get("performance")
    if perf and _GENERIC_PERF.match(perf) and df[perf].dtype.is_numeric():
        nu = df[perf].drop_nulls().n_unique()
        if nu <= 12 and nu <= 0.1 * max(len(df), 1):
            m["performance"] = None
            m["roles"][perf] = "config"
            transforms.append({"column": perf, "original": original.get(perf, perf),
                               "transform": f"only {nu} distinct values: treated as a configuration setting, not a performance metric"})
    for col in det["columns"]:
        col["role"] = next((k for k in ("outcome", "run_id", "config_id", "seed", "error_signature", "log", "timestamp", "environment",
                                         "hardware", "workload", "performance") if m.get(k) == col["name"]), None) or m["roles"].get(col["name"], "ignore")

    # 4. timestamps: hand canonicalize a native datetime when its own text parser would do worse
    ts = m.get("timestamp")
    if ts and df[ts].dtype == pl.Utf8:
        parsed, rate, method = parse_timestamps(df[ts], name_hint=True)
        legacy, legacy_tz = _legacy_ts_parse(df[ts])
        # day/month ambiguity is checked whichever parser would succeed: the pipeline must use the order the user confirms
        ambiguous = parsed is not None and "ambiguous" in method
        # "...Z" / "+02:00" text would become a timezone-aware column, which the analytics cannot load on hosts
        # without a time-zone database: hand over the same instants as naive UTC instead
        if parsed is not None and rate >= 0.5 and (rate > legacy + 1e-9 or ambiguous or legacy_tz):
            df = df.with_columns(parsed.alias(ts))
            transforms.append({"column": ts, "original": original.get(ts, ts),
                               "transform": f"timestamps parsed ({method}; {rate:.0%} valid)"})
            if "ambiguous" in method:
                confirmations.append({"field": "timestamp", "column": ts, "required": True,
                                      "message": f"Dates in '{original.get(ts, ts)}' could be day-first or month-first; they were read as "
                                                 f"{method.split('format ')[-1].split(' (')[0]}. Please confirm the date order."})
            if rate < 1:
                transforms.append({"column": ts, "original": original.get(ts, ts),
                                   "transform": f"{1 - rate:.0%} of rows have invalid timestamps"})
        elif legacy < 0.5 and (parsed is None or rate < 0.5):
            transforms.append({"column": ts, "original": original.get(ts, ts),
                               "transform": "timestamps unreadable: the pipeline will report the timeline as derived"})

    # 5. outcome polarity
    oc = m.get("outcome")
    if oc:
        a = assess_outcome(oc, det["low_card_values"].get(oc, []))
        m["fail_values"] = a["fail_values"]
        if not a["reliable"]:
            confirmations.append({"field": "outcome", "column": oc, "required": True, "message": a["reason"]})
    else:
        a = None
    report = {"transforms": transforms, "confirmations": confirmations, "renamed_for_detection": rename,
              "outcome": {"column": oc, "reason": a["reason"], "reliable": a["reliable"]} if a else None}
    return df, det, report


_SEMANTIC_FIELDS = (("outcome", "outcome"), ("timestamp", "timestamp"), ("run_id", "run_id"), ("seed", "seed"), ("error_signature", "error"),
                    ("log", "log"), ("config_id", "config_id"), ("environment", "environment"), ("hardware", "hardware"),
                    ("workload", "workload"), ("performance", "performance"))


def _fill_gaps(m: dict, roles: list[dict], sanitized: dict[str, str]) -> None:
    """Fill fields the existing detector left empty, using the semantic detector (never overrides it)."""
    by_role: dict[str, list[dict]] = {}
    for r in roles:
        by_role.setdefault(r["role"], []).append(r)
    for field, role in _SEMANTIC_FIELDS:
        if m.get(field) or not by_role.get(role):
            continue
        col = sanitized.get(by_role[role][0]["name"])
        used = {v for k, v in m.items() if k not in ("roles", "fail_values") and isinstance(v, str)}
        if col and col not in used:
            m[field] = col
            m.get("roles", {}).pop(col, None)
