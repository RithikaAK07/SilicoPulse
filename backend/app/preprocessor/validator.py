"""User-friendly validation of a parsed table before ingestion (never raises)."""
from __future__ import annotations

import polars as pl

from .models import Issue

SMALL_DATASET = 20


def validate(df: pl.DataFrame, roles: list[dict], invalid: dict[str, int]) -> list[Issue]:
    issues: list[Issue] = []
    n = len(df)
    if n == 0 or df.width == 0:
        return [Issue("error", "No tabular or structured data could be detected.")]
    if n < SMALL_DATASET:
        issues.append(Issue("warning", f"Very small dataset ({n} row{'s' if n != 1 else ''}): statistics will be unreliable."))
    try:
        dups = int(df.is_duplicated().sum())
        if dups:
            issues.append(Issue("info", f"{dups} row(s) are exact duplicates of another row."))
    except Exception:
        pass
    for c, k in invalid.items():
        issues.append(Issue("warning", f"Column {c} contains {k} non-numeric value{'s' if k != 1 else ''}.", c))
    missing = sorted(((r["missing"], r["name"]) for r in roles if r["missing"]), reverse=True)
    for m, c in missing[:10]:
        issues.append(Issue("warning", f"Column {c} has {m} missing value{'s' if m != 1 else ''} ({m / n:.0%}).", c))
    if len(missing) > 10:
        issues.append(Issue("warning", f"{len(missing) - 10} more column(s) have missing values."))
    for r in roles:
        if r.get("inf"):
            issues.append(Issue("warning", f"Column {r['name']} contains {r['inf']} infinite value(s).", r["name"]))
        if r.get("nan"):
            issues.append(Issue("warning", f"Column {r['name']} contains {r['nan']} NaN value(s).", r["name"]))
        if r["dtype"] == "String" and r["n_unique"] > 1:
            s = df[r["name"]].drop_nulls()
            if len(s):
                frac = float(s.str.strip_chars().str.replace_all(",", "").cast(pl.Float64, strict=False).is_not_null().mean())
                if 0.3 <= frac < 0.9:
                    issues.append(Issue("warning", f"Column {r['name']} mixes numbers and text ({frac:.0%} numeric): inconsistent datatypes.", r["name"]))
    ts = [r for r in roles if r["role"] == "timestamp"]
    if len(ts) > 1:
        issues.append(Issue("warning", f"Multiple columns could represent timestamp ({', '.join(r['name'] for r in ts)}). Please select one."))
    for r in ts[:1]:
        rate = r.get("timestamp_parse_rate", 1)
        if rate < 1:
            issues.append(Issue("warning" if rate >= 0.5 else "error", f"{1 - rate:.0%} of rows have invalid timestamps in {r['name']}.", r["name"]))
    if not ts:
        issues.append(Issue("info", "No timestamp column detected: time-based analysis is unavailable (row order is used)."))
    amb = [r for r in roles if r["ambiguous"] and r["role"] not in ("timestamp",)]
    if amb:
        issues.append(Issue("info", f"Ambiguous mapping for {', '.join(r['name'] for r in amb[:6])}: please review the suggested roles."))
    return issues
