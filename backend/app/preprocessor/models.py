"""Data structures shared by the universal preprocessor."""
from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl

PREPROCESSOR_VERSION = "1.0.0"

SUPPORTED_EXTENSIONS = (".csv", ".txt", ".log", ".json", ".xlsx", ".xls", ".zip")
SUPPORTED_LABEL = "CSV, TXT, LOG, JSON, XLS, XLSX, ZIP"

# Resource limits (uploads are never executed, but parsing must stay bounded)
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_ZIP_MEMBERS = 100
MAX_ZIP_MEMBER_BYTES = 200 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 500 * 1024 * 1024
MAX_ZIP_RATIO = 200  # uncompressed / compressed; higher looks like a decompression bomb
MAX_LOG_LINES = 2_000_000
MAX_COLUMNS = 1000
MAX_JSON_DEPTH = 6


class PreprocessError(Exception):
    """A user-facing error (message is safe to show; never a stack trace)."""


@dataclass
class Issue:
    severity: str  # "error" | "warning" | "info"
    message: str
    column: str | None = None

    def to_dict(self) -> dict:
        return {"severity": self.severity, "message": self.message, "column": self.column}


@dataclass
class ParsedTable:
    """One table produced by a parser (a CSV, a sheet, a ZIP member, a parsed log, ...)."""
    label: str  # display label, e.g. "machine1.csv" or "report.xlsx › Sheet1"
    source: str  # original file / member name
    kind: str  # csv | delimited | log | json | json_lines | excel
    frame: pl.DataFrame
    issues: list[Issue] = field(default_factory=list)
    invalid_numeric: dict[str, int] = field(default_factory=dict)  # column -> values that failed numeric parsing
    legacy_csv: bool = False  # plain comma CSV: execution path uses the original read_csv for identical behaviour
    extra: dict = field(default_factory=dict)  # parser-specific metadata (sheet name, JSON root key, log stats, ...)
