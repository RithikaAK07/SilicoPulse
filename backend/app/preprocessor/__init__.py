"""Universal file preprocessor: INPUT FILE -> detect -> parse -> detect fields -> map -> validate.

The output describes one or more *parts* (a file, an Excel sheet, a ZIP member, or a combined
part when several are schema-compatible). Each part says whether it can go through the existing
execution-log pipeline (it has a real pass/fail outcome) and/or the telemetry path. Nothing is
fabricated: a part without an outcome is never given PASS/FAIL labels.
"""
from __future__ import annotations

import logging
import posixpath

import polars as pl

from .execution import adapt
from .mapper import DATA_TYPE_LABELS, classify, detect_roles
from .models import PREPROCESSOR_VERSION, SUPPORTED_EXTENSIONS, SUPPORTED_LABEL, Issue, ParsedTable, PreprocessError
from .parsers import detect_file_type, parse_bytes
from .validator import validate
from .zip_handler import read_members, safe_display_name

log = logging.getLogger("silicopulse.preprocessor")

__all__ = ["preprocess", "PreprocessError", "PREPROCESSOR_VERSION", "SUPPORTED_EXTENSIONS", "SUPPORTED_LABEL", "load_part", "execution_frame"]

STAGES = [("detected", "File detected"), ("parsing", "Parsing"), ("fields", "Detecting fields"), ("mapping", "Mapping fields"),
          ("validating", "Validating"), ("ready", "Ready for ingestion")]

NO_OUTCOME = ("PASS/FAIL outcome is not present. This file was detected as telemetry-only data and can be processed "
              "using telemetry analysis.")


def _telemetry_mapping(roles: list[dict]) -> dict:
    ts = next((r["name"] for r in roles if r["role"] == "timestamp"), None)
    out = {}
    for r in roles:
        if r["name"] == ts:
            continue
        numeric = r["dtype"] not in ("String", "Utf8")
        if r["role"] in ("telemetry", "performance") or (numeric and r["role"] == "config"):
            out[r["name"]] = "telemetry"
        elif r["role"] in ("log",):
            out[r["name"]] = "log"
        elif r["role"] in ("run_id", "seed") and numeric:
            out[r["name"]] = "ignore"
        else:
            out[r["name"]] = "context"
    return {"timestamp": ts, "roles": out}


def _roles(table: ParsedTable) -> list[dict]:
    roles = detect_roles(table.frame, table.invalid_numeric)
    vunits = table.extra.get("value_normalization") or {}
    for r in roles:  # a unit written inside the values ("500 MB/s") is explicit too
        if vunits.get(r["name"], {}).get("unit") and not r.get("unit"):
            r["unit"] = vunits[r["name"]]["unit"]
    return roles


def _part(table: ParsedTable, part_id: str, raw: bytes | None) -> dict:
    roles = _roles(table)
    issues = list(table.issues) + validate(table.frame, roles, table.invalid_numeric)
    try:
        exec_df, det, report = adapt(table, raw, roles)
    except Exception as e:  # e.g. a single-column CSV: the column table is still produced
        detail = getattr(e, "detail", None)
        if not isinstance(detail, str):
            log.warning("execution adapter failed for %s: %s", table.label, type(e).__name__)
        exec_df, det = table.frame, None
        report = {"confirmations": [], "transforms": [], "outcome": None, "renamed_for_detection": {},
                  "error": detail if isinstance(detail, str) else "The file layout is not usable as an execution log."}
    mapping = det["mapping"] if det else {"outcome": None}
    # an outcome column exists even when its FAIL values still need the user's confirmation
    has_outcome = bool(mapping.get("outcome"))
    for c in report["confirmations"]:
        issues.append(Issue("warning", f"Confirmation needed: {c['message']}", c.get("column")))
    dtype = classify(roles, table.kind, has_outcome)
    telemetry_cols = [r for r in roles if _telemetry_mapping(roles)["roles"].get(r["name"]) == "telemetry"]
    if not has_outcome:
        issues.append(Issue("info", NO_OUTCOME if telemetry_cols else "PASS/FAIL outcome is not present."))
    errors = [i for i in issues if i.severity == "error"]
    return {
        "part_id": part_id, "label": safe_display_name(table.label), "source": safe_display_name(table.source), "parser": table.kind,
        "parser_details": table.extra, "data_type": dtype, "data_type_label": DATA_TYPE_LABELS[dtype],
        "rows": len(table.frame), "columns": [{k: v for k, v in r.items() if k != "values"} for r in roles],
        "issues": [i.to_dict() for i in issues], "ok": not errors,
        "execution": {
            "available": has_outcome and not errors and det is not None,
            "reason": report.get("error") or (None if has_outcome else ("PASS/FAIL outcome is not present." if not errors else errors[0].message)),
            # payload consumed by the existing mapping dialog
            "preview": {"rows": len(exec_df), "columns": det["columns"], "mapping": mapping, "low_card_values": det["low_card_values"]} if det else None,
            "confirmations": report["confirmations"],
            "transforms": report["transforms"],
            "outcome_assessment": report["outcome"],
        },
        "telemetry": {
            "available": bool(telemetry_cols) and not errors,
            "reason": None if telemetry_cols else "No numeric measurement columns were detected.",
            "mapping": _telemetry_mapping(roles),
        },
    }


def _combine(tables: list[ParsedTable]) -> tuple[ParsedTable | None, str]:
    """Combine only when every table has the same (normalized) columns; never merge incompatible data."""
    from .mapper import norm

    if len(tables) < 2:
        return None, ""
    schemas = [tuple(sorted(norm(c) for c in t.frame.columns)) for t in tables]
    if len(set(schemas)) > 1:
        base = set(schemas[0])
        diffs = []
        for t, sch in zip(tables[1:], schemas[1:]):
            extra, missing = set(sch) - base, base - set(sch)
            if extra or missing:
                diffs.append(f"{safe_display_name(t.label)}: {'+' + ', +'.join(sorted(extra)[:4]) if extra else ''}{' ' if extra and missing else ''}"
                             f"{'-' + ', -'.join(sorted(missing)[:4]) if missing else ''}")
        return None, "Files have different columns, so they are kept separate: " + "; ".join(diffs[:5])
    frames = []
    for t in tables:
        f = t.frame.rename({c: norm(c) or c for c in t.frame.columns})
        frames.append(f.with_columns(pl.lit(safe_display_name(t.label)).alias("source_file")))
    try:
        combined = pl.concat(frames, how="vertical_relaxed")
    except Exception as e:  # incompatible dtypes
        return None, f"Columns match but their data types are incompatible ({type(e).__name__}); files are kept separate."
    invalid: dict[str, int] = {}
    for t in tables:
        for c, k in t.invalid_numeric.items():
            invalid[norm(c)] = invalid.get(norm(c), 0) + k
    issues = [Issue(i.severity, f"{safe_display_name(t.label)}: {i.message}", i.column) for t in tables for i in t.issues]
    label = f"Combined ({len(tables)} files)"
    return ParsedTable(label=label, source=label, kind=tables[0].kind, frame=combined, issues=issues, invalid_numeric=invalid,
                       extra={"members": [safe_display_name(t.label) for t in tables]}), "All files share the same columns, so they can be safely combined."


def parse_all(raw: bytes, filename: str) -> dict:
    """Parse a file into tables (no mapping yet). Raises PreprocessError for unusable input."""
    if not raw:
        raise PreprocessError("The file is empty.")
    kind, issues = detect_file_type(filename, raw)
    tables: list[ParsedTable] = []
    member_raw: dict[str, bytes] = {}
    archive = None
    sheets: list[dict] = []
    if kind == "zip":
        members, report = read_members(raw)
        for name, data in members:
            entry = next(e for e in report if e["name"] == safe_display_name(name))
            try:
                mkind, _ = detect_file_type(name, data)
                ts, _, sh = parse_bytes(data, posixpath.basename(name), mkind)
                for t in ts:
                    t.label = safe_display_name(name) + (f" › {t.extra['sheet']}" if t.extra.get("sheet") else "")
                    member_raw[t.label] = data
                tables += ts
                entry["rows"] = sum(len(t.frame) for t in ts)
            except PreprocessError as e:
                entry.update(status="failed", reason=str(e))
        archive = {"files": report, "supported": [e["name"] for e in report if e["status"] == "supported"],
                   "ignored": [e["name"] for e in report if e["status"] in ("ignored", "rejected", "failed")]}
        if not tables:
            raise PreprocessError("The archive contains no supported files with readable data.")
    else:
        tables, _, sheets = parse_bytes(raw, filename, kind)
        if kind not in ("xlsx", "xls"):
            member_raw[tables[0].label] = raw
        if kind in ("xlsx", "xls") and not tables:
            raise PreprocessError("No tabular or structured data could be detected (all sheets are empty).")
    return {"kind": kind, "issues": issues, "tables": tables, "member_raw": member_raw, "archive": archive, "sheets": sheets}


def _part_tables(parsed: dict) -> list[tuple[str, ParsedTable, bytes | None]]:
    tables = parsed["tables"]
    out = []
    if len(tables) == 1:
        out.append(("main", tables[0], parsed["member_raw"].get(tables[0].label)))
    else:
        combined, reason = _combine(tables)
        parsed["combine_reason"] = reason
        parsed["combinable"] = combined is not None
        if combined is not None:
            out.append(("combined", combined, None))
        for i, t in enumerate(tables):
            out.append((f"p{i}", t, parsed["member_raw"].get(t.label)))
    return out


def preprocess(raw: bytes, filename: str) -> dict:
    stages = {k: {"key": k, "label": lbl, "status": "pending", "detail": ""} for k, lbl in STAGES}
    display = safe_display_name(filename or "upload")
    try:
        parsed = parse_all(raw, filename)
    except PreprocessError as e:
        failed = "detected" if "Unsupported" in str(e) or "extension" in str(e) or "empty" in str(e) else "parsing"
        for k, st in stages.items():
            if k == failed:
                st.update(status="error", detail=str(e))
                break
            st.update(status="done")
        return {"filename": display, "file_type": None, "size_bytes": len(raw), "preprocessing_version": PREPROCESSOR_VERSION,
                "stages": list(stages.values()), "errors": [str(e)], "parts": [], "default_part": None, "archive": None, "sheets": []}
    except Exception:  # never leak a stack trace to the user
        log.exception("preprocessing failed for %s", display)
        stages["parsing"].update(status="error", detail="The file could not be parsed.")
        stages["detected"].update(status="done")
        return {"filename": display, "file_type": None, "size_bytes": len(raw), "preprocessing_version": PREPROCESSOR_VERSION,
                "stages": list(stages.values()), "errors": ["The file could not be parsed. It may be malformed or in an unexpected layout."],
                "parts": [], "default_part": None, "archive": None, "sheets": []}
    stages["detected"].update(status="done", detail=f"{parsed['kind'].upper()} file")
    stages["parsing"].update(status="done", detail=f"{len(parsed['tables'])} table(s) · {', '.join(sorted({t.kind for t in parsed['tables']}))}")
    parts = []
    for part_id, table, member_bytes in _part_tables(parsed):
        try:
            parts.append(_part(table, part_id, member_bytes))
        except Exception:
            log.exception("mapping failed for part %s of %s", part_id, display)
            try:  # still list every header column so the user can see and map them
                cols = [{k: v for k, v in r.items() if k != "values"} for r in _roles(table)]
            except Exception:
                cols = []
            parts.append({"part_id": part_id, "label": safe_display_name(table.label), "ok": False, "rows": len(table.frame),
                          "issues": [{"severity": "error", "message": "This part could not be analysed (unexpected layout).", "column": None}],
                          "columns": cols, "data_type": "tabular", "data_type_label": DATA_TYPE_LABELS["tabular"],
                          "execution": {"available": False, "reason": "unreadable", "preview": None},
                          "telemetry": {"available": False, "reason": "unreadable", "mapping": {"timestamp": None, "roles": {}}}})
    stages["fields"].update(status="done", detail=f"{sum(len(p['columns']) for p in parts[:1])} columns profiled")
    stages["mapping"].update(status="done", detail=", ".join(sorted({p["data_type_label"] for p in parts})))
    has_err = any(not p["ok"] for p in parts)
    warn = any(i["severity"] == "warning" for p in parts for i in p["issues"])
    stages["validating"].update(status="error" if has_err and not any(p["ok"] for p in parts) else ("warning" if (warn or has_err) else "done"),
                                detail="issues found" if (warn or has_err) else "no issues")
    ready = any(p["ok"] and (p["execution"]["available"] or p["telemetry"]["available"]) for p in parts)
    stages["ready"].update(status="done" if ready else "error", detail="" if ready else "No part can be ingested.")
    default = next((p["part_id"] for p in parts if p["part_id"] == "combined" and p["ok"]), None) or next((p["part_id"] for p in parts if p["ok"]), None)
    archive = parsed["archive"]
    if archive is not None:
        archive["combinable"] = parsed.get("combinable", len(parsed["tables"]) == 1)
        archive["combine_reason"] = parsed.get("combine_reason") or ("Only one supported file." if len(parsed["tables"]) == 1 else "")
    sheets_info = parsed["sheets"]
    return {"filename": display, "file_type": parsed["kind"], "size_bytes": len(raw), "preprocessing_version": PREPROCESSOR_VERSION,
            "stages": list(stages.values()), "errors": [], "file_issues": [i.to_dict() for i in parsed["issues"]],
            "parts": parts, "default_part": default, "archive": archive, "sheets": sheets_info,
            "combinable": parsed.get("combinable"), "combine_reason": parsed.get("combine_reason")}


def execution_frame(table: ParsedTable, raw: bytes | None) -> tuple[pl.DataFrame, dict, dict]:
    """(frame, detect() result, adaptation report) for the existing execution pipeline."""
    return adapt(table, raw, _roles(table))


def load_part(raw: bytes, filename: str, part_id: str) -> tuple[ParsedTable, bytes | None]:
    """Re-parse deterministically and return one part's table (used at ingest time)."""
    parsed = parse_all(raw, filename)
    for pid, table, member_bytes in _part_tables(parsed):
        if pid == part_id:
            return table, member_bytes
    raise PreprocessError("The selected part no longer exists; please upload the file again.")
