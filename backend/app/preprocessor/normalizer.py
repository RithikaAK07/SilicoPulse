"""Normalization: timestamps, explicit units, numeric coercion and the telemetry representation.

Units are never guessed. A unit is recorded only when the column name states it (e.g. `_c`,
`_rpm`, `fahrenheit`), and the only conversion performed is an explicit °F -> °C.
"""
from __future__ import annotations

import re

import numpy as np
import polars as pl

TS_FORMATS = [
    "%Y-%m-%dT%H:%M:%S%.fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%.f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S%.f",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S%.f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S%.f%z", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d", "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M",
    "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y %H:%M:%S", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S,%3f",
]
_DATEISH = re.compile(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|\d{1,2}:\d{2}")


def parse_timestamps(s: pl.Series, name_hint: bool = False) -> tuple[pl.Series | None, float, str]:
    """Best-effort timestamp parsing -> (datetime[ms] series or None, parse rate, method)."""
    n_valid = len(s) - s.null_count()
    if n_valid == 0:
        return None, 0.0, "empty"
    if s.dtype in (pl.Datetime, pl.Date):
        out = s.cast(pl.Datetime("ms"))
        if isinstance(out.dtype, pl.Datetime) and out.dtype.time_zone:
            out = out.dt.convert_time_zone("UTC").dt.replace_time_zone(None)
        return out, 1.0, "native datetime"
    if s.dtype.is_numeric():
        if not name_hint:
            return None, 0.0, ""
        v = s.cast(pl.Float64)
        if v.drop_nulls().is_between(1e9, 4.2e9).mean() >= 0.95:
            return (v * 1000).cast(pl.Int64).cast(pl.Datetime("ms")), float(v.drop_nulls().is_between(1e9, 4.2e9).mean()), "epoch seconds"
        if v.drop_nulls().is_between(1e12, 4.2e12).mean() >= 0.95:
            return v.cast(pl.Int64).cast(pl.Datetime("ms")), float(v.drop_nulls().is_between(1e12, 4.2e12).mean()), "epoch milliseconds"
        return None, 0.0, ""
    if s.dtype != pl.Utf8:
        return None, 0.0, ""
    sample = s.drop_nulls().head(20).to_list()
    if not sum(1 for x in sample if _DATEISH.search(str(x))) >= 0.6 * len(sample):
        return None, 0.0, ""
    st = s.str.strip_chars()
    best, best_rate, best_fmt, ties = None, 0.0, "", []
    for fmt in TS_FORMATS:
        try:
            parsed = st.str.to_datetime(format=fmt, strict=False, time_unit="ms")
        except Exception:
            continue
        if isinstance(parsed.dtype, pl.Datetime) and parsed.dtype.time_zone:
            parsed = parsed.dt.convert_time_zone("UTC").dt.replace_time_zone(None)
        rate = (len(parsed) - parsed.null_count()) / n_valid
        if rate > best_rate + 1e-9:
            best, best_rate, best_fmt, ties = parsed, rate, fmt, []
        elif rate > 0 and abs(rate - best_rate) < 1e-9:
            ties.append(fmt)
        if best_rate == 1.0 and fmt.startswith("%Y"):
            break
    if best is None:
        return None, 0.0, ""
    method = f"format {best_fmt}"
    if any(("%d/%m" in a) != ("%d/%m" in best_fmt) and ("/" in a) for a in ties):
        method += " (day/month order ambiguous; assumed as shown)"
    return best, best_rate, method


# --------------------------------------------------------------------------- units
def _tok(name: str) -> list[str]:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return [t for t in re.split(r"[^a-z0-9]+", s.lower()) if t]


def unit_info(name: str) -> dict:
    """Explicit unit from the column name, or {'unit': None} ("unit unknown")."""
    t = _tok(name)
    last = t[-1] if t else ""
    joined = "_".join(t)
    tempish = any(x in t for x in ("temp", "temperature")) or "temp" in joined
    if tempish and (last in ("c", "degc", "celsius") or "celsius" in t):
        return {"unit": "°C"}
    if tempish and (last in ("degf", "fahrenheit", "f") or "fahrenheit" in t):
        return {"unit": "°F", "convert_to": "°C"}
    if "rpm" in t:
        return {"unit": "RPM"}
    if last == "g" and any(x in t for x in ("vibration", "vib", "accel", "acceleration")):
        return {"unit": "g"}
    table = {"mbps": "MB/s", "gbps": "GB/s", "ms": "ms", "us": "µs", "hz": "Hz", "khz": "kHz", "mhz": "MHz", "kw": "kW",
             "pct": "%", "percent": "%", "psi": "psi", "kpa": "kPa", "bar": "bar", "nm": "N·m", "ma": "mA", "mv": "mV", "rh": "%RH"}
    if last in table:
        return {"unit": table[last]}
    if last == "v" and any(x in t for x in ("volt", "voltage", "vcc", "vdd")):
        return {"unit": "V"}
    if last == "a" and any(x in t for x in ("current", "amp", "amps")):
        return {"unit": "A"}
    if last in ("w", "watt", "watts") and any(x in t for x in ("power", "watt", "watts")):
        return {"unit": "W"}
    return {"unit": None}


def sanitize_name(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^0-9a-zA-Z]+", "_", name.strip()).strip("_").lower() or "col"
    if base[0].isdigit():
        base = f"c_{base}"
    out, i = base, 1
    while out in taken:
        i += 1
        out = f"{base}_{i}"
    taken.add(out)
    return out


# --------------------------------------------------------------------------- telemetry representation
RESERVED = {"timestamp", "row_index", "source_file"}


def normalize_telemetry(df: pl.DataFrame, timestamp: str | None, roles: dict[str, str], invalid: dict[str, int]) -> tuple[pl.DataFrame, dict]:
    """Build the canonical telemetry frame: timestamp | row_index | [source_file] | channels | context.

    roles: original column -> telemetry | context | log | ignore. No value is invented: missing or
    non-numeric measurements stay null and are reported.
    """
    taken = set(RESERVED)
    cols: list[pl.Series] = [pl.Series("row_index", np.arange(len(df), dtype=np.int64))]
    provenance, channels, context, logs, warnings = [], [], [], [], []
    ts_meta = {"column": None, "parse_rate": None, "method": None}
    if timestamp:
        parsed, rate, method = parse_timestamps(df[timestamp], name_hint=True)
        if parsed is None or rate == 0:
            warnings.append(f"Column '{timestamp}' could not be parsed as timestamps; row order is used instead.")
        else:
            if rate < 1:
                warnings.append(f"{(1 - rate):.0%} of rows have invalid timestamps in '{timestamp}'.")
            cols.append(parsed.alias("timestamp"))
            ts_meta = {"column": timestamp, "parse_rate": round(rate, 4), "method": method}
            provenance.append({"original": timestamp, "normalized": "timestamp", "role": "timestamp", "transform": method})
    if "source_file" in df.columns:
        cols.append(df["source_file"].cast(pl.Utf8))
    for c in df.columns:
        role = roles.get(c, "ignore")
        if c == timestamp or c == "source_file" or role == "ignore":
            if role == "ignore" and c != timestamp and c != "source_file":
                provenance.append({"original": c, "normalized": None, "role": "ignore", "transform": "excluded by mapping"})
            continue
        new = sanitize_name(c, taken)
        if role == "telemetry":
            s = df[c]
            if not (s.dtype.is_numeric() or s.dtype == pl.Boolean):
                raw = s.cast(pl.Utf8).str.strip_chars()
                s = raw.str.replace_all(",", "").cast(pl.Float64, strict=False)
                bad = int((raw.is_not_null() & (raw != "") & s.is_null()).sum())
                if bad:
                    warnings.append(f"Column {c} contains {bad} non-numeric values (kept as missing).")
            s = s.cast(pl.Float64)
            arr = s.to_numpy()
            n_inf = int(np.isinf(arr).sum()) if len(arr) else 0
            if n_inf:
                s = pl.Series(c, np.where(np.isinf(arr), np.nan, arr)).fill_nan(None)
                warnings.append(f"Column {c} contains {n_inf} infinite values (kept as missing).")
            s = s.fill_nan(None)
            u = unit_info(c)
            transform = "numeric"
            if u.get("convert_to") == "°C":
                s = (s - 32) * 5 / 9
                transform = "converted °F -> °C (explicit Fahrenheit column)"
                new = sanitize_name(re.sub(r"(?i)(_?(degf|fahrenheit|f))$", "", c) + "_c", taken)
                u = {"unit": "°C"}
            cols.append(s.alias(new))
            channels.append({"name": new, "original": c, "unit": u["unit"] or "unit unknown", "invalid_values": invalid.get(c, 0)})
            provenance.append({"original": c, "normalized": new, "role": "telemetry", "transform": transform, "unit": u["unit"] or "unit unknown"})
        else:
            cols.append(df[c].cast(pl.Utf8).alias(new))
            (logs if role == "log" else context).append({"name": new, "original": c})
            provenance.append({"original": c, "normalized": new, "role": role, "transform": "text"})
    frame = pl.DataFrame(cols)
    sorted_note = None
    if "timestamp" in frame.columns:
        ts = frame["timestamp"]
        if not ts.drop_nulls().is_sorted():
            frame = frame.sort("timestamp", nulls_last=True)
            sorted_note = "Rows were not in time order and were sorted by timestamp."
            warnings.append(sorted_note)
    return frame, {"channels": channels, "context": context, "logs": logs, "timestamp": ts_meta, "provenance": provenance, "warnings": warnings}
