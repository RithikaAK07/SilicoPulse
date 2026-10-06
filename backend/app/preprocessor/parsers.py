"""File-type detection and safe parsers (CSV, TXT, LOG, JSON, XLS/XLSX, ZIP members).

Nothing here executes or evaluates uploaded content: JSON goes through json.loads, Excel
through the calamine reader (fastexcel), everything else is text parsing.
"""
from __future__ import annotations

import csv
import io
import json
import posixpath
import re
from collections import Counter

import polars as pl

from .models import (MAX_COLUMNS, MAX_JSON_DEPTH, MAX_LOG_LINES, SUPPORTED_EXTENSIONS, SUPPORTED_LABEL, Issue, ParsedTable,
                     PreprocessError)

DELIMITERS = [",", "\t", "|", ";"]
RECORD_KEYS = ("records", "data", "results", "items", "rows", "entries", "logs", "executions", "runs", "events", "measurements", "values")


# --------------------------------------------------------------------------- detection
def detect_file_type(filename: str, raw: bytes) -> tuple[str, list[Issue]]:
    """Return (type, issues) where type is one of csv|txt|log|json|xlsx|xls|zip."""
    issues: list[Issue] = []
    ext = posixpath.splitext((filename or "").lower())[1]
    is_zip = raw[:4] == b"PK\x03\x04"
    is_ole = raw[:8] == bytes.fromhex("D0CF11E0A1B11AE1")
    if ext == ".xlsv":  # common typo: accept only if the bytes really are an Excel workbook
        if is_zip and b"xl/" in raw[:65536]:
            issues.append(Issue("warning", "'.xlsv' treated as an Excel .xlsx workbook (file contents confirmed)."))
            return "xlsx", issues
        if is_ole:
            issues.append(Issue("warning", "'.xlsv' treated as an Excel .xls workbook (file contents confirmed)."))
            return "xls", issues
    if ext not in SUPPORTED_EXTENSIONS:
        raise PreprocessError(f"Unsupported file type{f' ({ext})' if ext else ''}. Supported: {SUPPORTED_LABEL}.")
    kind = ext[1:]
    if kind == "zip" and not is_zip:
        raise PreprocessError("The file has a .zip extension but is not a ZIP archive.")
    if kind == "xlsx" and not is_zip:
        raise PreprocessError("The file has a .xlsx extension but is not an Excel workbook.")
    if kind == "xls" and not (is_ole or is_zip):
        raise PreprocessError("The file has a .xls extension but is not an Excel workbook.")
    if kind in ("csv", "txt", "log", "json") and (is_zip or is_ole):
        raise PreprocessError(f"The file has a .{kind} extension but contains binary (archive/Excel) data.")
    return kind, issues


# --------------------------------------------------------------------------- text helpers
def decode_text(raw: bytes) -> tuple[str, list[Issue]]:
    issues: list[Issue] = []
    if not raw.strip():
        raise PreprocessError("The file is empty.")
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16"), [Issue("info", "Decoded as UTF-16.")]
    sample = raw[:65536]
    if sample.count(b"\x00") > len(sample) * 0.1:
        raise PreprocessError("The file looks binary, not text; no tabular or structured data could be detected.")
    try:
        return raw.decode("utf-8-sig"), issues
    except UnicodeDecodeError:
        pass
    try:
        text = raw.decode("cp1252")
        issues.append(Issue("warning", "File is not valid UTF-8; decoded as Windows-1252 (check special characters)."))
        return text, issues
    except UnicodeDecodeError:
        issues.append(Issue("warning", "File is not valid UTF-8; decoded as Latin-1 (check special characters)."))
        return raw.decode("latin-1"), issues


def infer_types(df: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, int]]:
    """Type string columns: numeric when >= 90% of non-empty values parse (failures counted, set null)."""
    invalid: dict[str, int] = {}
    cols = []
    for c in df.columns:
        s = df[c]
        if s.dtype != pl.Utf8:
            cols.append(s)
            continue
        st = pl.select(pl.when(s.str.strip_chars() == "").then(None).otherwise(s.str.strip_chars()).alias(c)).to_series()
        nonempty = int(st.drop_nulls().len())
        num = st.str.replace_all(",", "").cast(pl.Float64, strict=False) if nonempty else st.cast(pl.Float64, strict=False)
        ok = int(num.drop_nulls().len())
        if nonempty and ok == nonempty:
            vals = num.drop_nulls()
            is_int = bool((vals == vals.round(0)).all()) and float(vals.abs().max() or 0) < 2**53 and not st.drop_nulls().str.contains(r"\.").any()
            cols.append(num.cast(pl.Int64) if is_int else num)
        elif nonempty and ok >= 0.9 * nonempty:
            invalid[c] = nonempty - ok
            cols.append(num)
        else:
            cols.append(st)
    return pl.DataFrame(cols), invalid


def _dedupe_header(names: list[str]) -> tuple[list[str], list[Issue]]:
    seen: Counter = Counter()
    out, issues = [], []
    for n in names:
        n = (n or "").strip() or "column"
        if seen[n]:
            new = f"{n}_{seen[n] + 1}"
            issues.append(Issue("warning", f"Duplicate column '{n}' renamed to '{new}'.", n))
            out.append(new)
        else:
            out.append(n)
        seen[n] += 1
    return out, issues


def sniff_delimiter(text: str) -> str | None:
    lines = [ln for ln in text.splitlines()[:200] if ln.strip()]
    if len(lines) < 2:
        return "," if lines and "," in lines[0] else None
    try:
        d = csv.Sniffer().sniff("\n".join(lines[:50]), delimiters="".join(DELIMITERS)).delimiter
        if d in DELIMITERS:
            return d
    except csv.Error:
        pass
    best, best_score = None, 0.0
    for d in DELIMITERS:
        counts = [ln.count(d) for ln in lines]
        mode = Counter(counts).most_common(1)[0][0]
        if mode < 1:
            continue
        score = sum(1 for c in counts if c == mode) / len(counts)
        if score >= 0.8 and score > best_score:
            best, best_score = d, score
    return best


def parse_delimited(text: str, label: str, kind: str) -> ParsedTable | None:
    """Delimited text -> typed table, or None when the text is not tabular."""
    delim = sniff_delimiter(text)
    if delim is None:
        return None
    issues: list[Issue] = []
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if len(rows) < 2:
        return None
    header, dup = _dedupe_header(rows[0])
    issues += dup
    if len(header) > MAX_COLUMNS:
        raise PreprocessError(f"{label}: {len(header)} columns exceeds the limit of {MAX_COLUMNS}.")
    width = len(header)
    body, malformed = [], 0
    for r in rows[1:]:
        if len(r) != width:
            malformed += 1
            r = (r + [""] * width)[:width]
        body.append(r)
    if malformed:
        issues.append(Issue("warning", f"{malformed} malformed row(s) had the wrong number of fields (padded/truncated)."))
    if width < 2 and kind != "csv":
        return None
    frame = pl.DataFrame({h: [r[i] for r in body] for i, h in enumerate(header)}, schema={h: pl.Utf8 for h in header})
    frame, invalid = infer_types(frame)
    name = {",": "comma", "\t": "tab", "|": "pipe", ";": "semicolon"}[delim]
    return ParsedTable(label=label, source=label, kind="csv" if kind == "csv" else "delimited", frame=frame, issues=issues,
                       invalid_numeric=invalid, legacy_csv=(kind == "csv" and delim == "," and not dup), extra={"delimiter": name})


# --------------------------------------------------------------------------- logs
_TS_PATTERNS = [
    re.compile(r"^\[?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\]?\s*"),
    re.compile(r"^\[?(\d{4}/\d{2}/\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]?\s*"),
    re.compile(r"^\[?(\d{2}/\d{2}/\d{4}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]?\s*"),
    re.compile(r"^([A-Z][a-z]{2}\s+\d{1,2}\s\d{2}:\d{2}:\d{2})\s*"),
    re.compile(r"^(\d{10}(?:\.\d+)?|\d{13})\s+"),
]
_LEVEL = re.compile(r"^\[?(TRACE|DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|ERR|CRITICAL|FATAL|SEVERE)\]?[:\s-]*", re.I)
_KV = re.compile(r"(?<![\w.])([A-Za-z_][\w.\-]{0,63})=(\"[^\"]*\"|'[^']*'|[^\s,;]+)")


def parse_log(text: str, label: str) -> ParsedTable | None:
    lines = text.splitlines()
    if len(lines) > MAX_LOG_LINES:
        raise PreprocessError(f"{label}: {len(lines):,} lines exceeds the limit of {MAX_LOG_LINES:,}.")
    nonblank = [ln for ln in lines if ln.strip()]
    if not nonblank:
        return None
    # JSON lines?
    js = []
    for ln in nonblank[:2000]:
        try:
            v = json.loads(ln)
            if isinstance(v, dict):
                js.append(v)
        except json.JSONDecodeError:
            pass
    if len(js) >= 0.8 * min(len(nonblank), 2000):
        recs = []
        bad = 0
        for ln in nonblank:
            try:
                v = json.loads(ln)
                if isinstance(v, dict):
                    recs.append(v)
                else:
                    bad += 1
            except json.JSONDecodeError:
                bad += 1
        t = records_to_table(recs, label, "json_lines")
        if bad:
            t.issues.append(Issue("warning", f"{bad} line(s) were not valid JSON objects and were skipped."))
        return t
    events: list[dict] = []
    structured = 0
    orphans = 0
    for ln in lines:
        if not ln.strip():
            continue
        rest, ts = ln.rstrip(), None
        for rx in _TS_PATTERNS:
            m = rx.match(rest)
            if m:
                ts, rest = m.group(1), rest[m.end():]
                break
        lvl = None
        m = _LEVEL.match(rest)
        if m:
            lvl, rest = m.group(1).upper(), rest[m.end():]
            lvl = {"WARNING": "WARN", "ERR": "ERROR", "SEVERE": "ERROR"}.get(lvl, lvl)
        kv = {k: v.strip("\"'") for k, v in _KV.findall(rest)}
        if ts is None and lvl is None and not kv and events:
            # continuation line (stack trace etc.): keep it with the previous event
            events[-1]["raw"] += "\n" + ln
            events[-1]["message"] += "\n" + ln.strip()
            continue
        if ts is None and lvl is None and not kv:
            orphans += 1
        else:
            structured += 1
        ev = {"timestamp": ts, "level": lvl, "message": rest.strip(), "raw": ln}
        ev.update({f"kv.{k}": v for k, v in kv.items()})
        events.append(ev)
    if not events or structured < 0.3 * len(events):
        return None
    issues: list[Issue] = []
    keys = Counter(k for e in events for k in e if k.startswith("kv."))
    keep = [k for k, _ in keys.most_common(200)]
    if len(keys) > len(keep):
        issues.append(Issue("warning", f"Only the 200 most frequent key=value fields were kept (of {len(keys)})."))
    cols: dict[str, list] = {c: [] for c in ["timestamp", "level", "message", "raw", *[k[3:] for k in keep]]}
    for e in events:
        cols["timestamp"].append(e["timestamp"])
        cols["level"].append(e["level"])
        cols["message"].append(e["message"])
        cols["raw"].append(e["raw"])
        for k in keep:
            cols[k[3:]].append(e.get(k))
    for c in ["timestamp", "level"]:
        if all(v is None for v in cols[c]):
            del cols[c]
    frame = pl.DataFrame(cols, schema={c: pl.Utf8 for c in cols})
    frame, invalid = infer_types(frame)
    if orphans:
        issues.append(Issue("warning", f"{orphans} line(s) had no timestamp, level or key=value fields; kept as raw messages."))
    return ParsedTable(label=label, source=label, kind="log", frame=frame, issues=issues, invalid_numeric=invalid,
                       extra={"events": len(events), "kv_fields": len(keep)})


# --------------------------------------------------------------------------- JSON
def _flatten(obj, prefix: str, out: dict, issues: set, depth: int = 0) -> None:
    if isinstance(obj, dict) and depth < MAX_JSON_DEPTH:
        for k, v in obj.items():
            _flatten(v, f"{prefix}.{k}" if prefix else str(k), out, issues, depth + 1)
    elif isinstance(obj, dict):
        out[prefix] = json.dumps(obj, default=str)
        issues.add(f"Objects nested deeper than {MAX_JSON_DEPTH} levels were kept as JSON text.")
    elif isinstance(obj, list):
        if any(isinstance(x, (dict, list)) for x in obj):
            issues.add("Nested arrays of objects were kept as JSON text (not expanded into rows).")
        out[prefix] = json.dumps(obj, default=str)
    else:
        out[prefix] = obj


def records_to_table(records: list, label: str, kind: str, extra: dict | None = None) -> ParsedTable:
    notes: set = set()
    flat = []
    for r in records:
        row: dict = {}
        _flatten(r if isinstance(r, dict) else {"value": r}, "", row, notes)
        flat.append(row)
    keys: list[str] = []
    seen = set()
    for row in flat:
        for k in row:
            if k not in seen:
                seen.add(k)
                keys.append(k)
    if len(keys) > MAX_COLUMNS:
        raise PreprocessError(f"{label}: {len(keys)} fields exceeds the limit of {MAX_COLUMNS}.")
    cols = {}
    for k in keys:
        vals = [row.get(k) for row in flat]
        nonnull = [v for v in vals if v is not None]
        if nonnull and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in nonnull):
            cols[k] = pl.Series(k, [float(v) if v is not None else None for v in vals], dtype=pl.Float64)
            if all(float(v).is_integer() for v in nonnull) and all(isinstance(v, int) for v in nonnull):
                cols[k] = cols[k].cast(pl.Int64)
        else:
            cols[k] = pl.Series(k, [None if v is None else (str(v).lower() if isinstance(v, bool) else str(v)) for v in vals], dtype=pl.Utf8)
    frame = pl.DataFrame(list(cols.values())) if cols else pl.DataFrame()
    frame, invalid = infer_types(frame)
    return ParsedTable(label=label, source=label, kind=kind, frame=frame, issues=[Issue("info", n) for n in sorted(notes)],
                       invalid_numeric=invalid, extra=extra or {})


def parse_json(text: str, label: str) -> ParsedTable | None:
    try:
        root = json.loads(text)
    except json.JSONDecodeError:
        t = parse_log(text, label)  # JSON lines?
        if t is not None and t.kind == "json_lines":
            return t
        raise PreprocessError(f"{label}: invalid JSON (not a JSON document or JSON lines).")
    extra: dict = {}
    if isinstance(root, list):
        records = root
        extra["root"] = "array"
    elif isinstance(root, dict):
        key = next((k for k in root if str(k).lower() in RECORD_KEYS and isinstance(root[k], list)), None)
        if key is None:  # the largest list of objects anywhere within two levels
            cands = [(k, v) for k, v in root.items() if isinstance(v, list) and v and isinstance(v[0], dict)]
            for k, v in root.items():
                if isinstance(v, dict):
                    cands += [(f"{k}.{k2}", v2) for k2, v2 in v.items() if isinstance(v2, list) and v2 and isinstance(v2[0], dict)]
            if cands:
                key, _ = max(cands, key=lambda kv: len(kv[1]))
        if key is not None:
            node = root
            for part in key.split("."):
                node = node[part]
            records = node
            extra["root"] = f"object.{key}"
            meta = {k: v for k, v in root.items() if k != key.split(".")[0] and not isinstance(v, (list, dict))}
            if meta:
                extra["document_metadata"] = meta  # preserved for provenance, not turned into columns
        elif root and all(isinstance(v, list) for v in root.values()) and len({len(v) for v in root.values()}) == 1:
            n = len(next(iter(root.values())))
            records = [{k: root[k][i] for k in root} for i in range(n)]
            extra["root"] = "object of columns"
        else:
            records = [root]
            extra["root"] = "single object"
    else:
        return None
    if not records:
        raise PreprocessError(f"{label}: the JSON contains no records.")
    return records_to_table(records, label, "json", extra)


# --------------------------------------------------------------------------- Excel
def parse_excel(raw: bytes, label: str) -> tuple[list[ParsedTable], list[dict]]:
    try:
        import fastexcel
    except ImportError:  # pragma: no cover
        raise PreprocessError("Excel support is not installed on the server (missing 'fastexcel').")
    try:
        reader = fastexcel.read_excel(raw)
    except Exception:
        raise PreprocessError(f"{label}: the Excel workbook could not be read (corrupt or password-protected).")
    tables, sheets = [], []
    for name in reader.sheet_names:
        try:
            df = reader.load_sheet_by_name(name).to_polars()
        except Exception:
            sheets.append({"name": name, "rows": 0, "columns": 0, "status": "unreadable"})
            continue
        df = df.filter(~pl.all_horizontal(pl.all().is_null())) if df.width else df
        df = df.select([c for c in df.columns if df[c].null_count() < len(df)]) if len(df) else df
        if len(df) == 0 or df.width == 0:
            sheets.append({"name": name, "rows": 0, "columns": 0, "status": "empty (ignored)"})
            continue
        issues = []
        unnamed = [c for c in df.columns if c.startswith("__UNNAMED__")]
        if unnamed:
            issues.append(Issue("warning", f"Sheet '{name}': {len(unnamed)} column(s) have no header."))
        df = df.with_columns([pl.col(c).cast(pl.Utf8) for c in df.columns if df[c].dtype == pl.Null])
        df, invalid = infer_types(df.with_columns([pl.col(c).cast(pl.Utf8) for c in df.columns if df[c].dtype == pl.Object]))
        tables.append(ParsedTable(label=f"{label} › {name}", source=label, kind="excel", frame=df, issues=issues,
                                  invalid_numeric=invalid, extra={"sheet": name}))
        sheets.append({"name": name, "rows": len(df), "columns": df.width, "status": "data"})
    return tables, sheets


# --------------------------------------------------------------------------- dispatcher
def parse_bytes(raw: bytes, filename: str, kind: str) -> tuple[list[ParsedTable], list[Issue], list[dict]]:
    """Parse one non-archive file -> (tables, issues, sheet report)."""
    if kind in ("xlsx", "xls"):
        tables, sheets = parse_excel(raw, filename)
        return tables, [], sheets
    text, issues = decode_text(raw)
    if kind == "json":
        t = parse_json(text, filename)
    elif kind == "csv":
        t = parse_delimited(text, filename, "csv")
    elif kind == "txt":
        t = parse_delimited(text, filename, "txt") or parse_log(text, filename)
    else:  # log
        t = parse_log(text, filename) or parse_delimited(text, filename, "txt")
    if t is None or t.frame.width == 0 or len(t.frame) == 0:
        raise PreprocessError(f"{filename}: no tabular or structured data could be detected.")
    t.issues = issues + t.issues
    return [t], [], []
