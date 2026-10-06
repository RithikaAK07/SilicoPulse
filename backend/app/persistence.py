"""Durable metadata registry + safe file writes for the data folder (DATA_DIR).

Storage layout (unchanged file locations, so existing data keeps working):
  data/runs.parquet, meta.json                 benchmark rows + metadata          (files)
  data/uploaded.parquet, uploaded_meta.json    uploaded execution dataset         (files)
  data/telemetry.parquet, telemetry_meta.json  uploaded telemetry dataset         (files)
  data/scenarios/<id>.parquet + .json          derived scenarios                  (files)
  data/silicopulse.db                          SQLite registry (this module):
      datasets   one row per dataset: id, name, type, rows, columns, detected fields,
                 field mapping, file path, status, created/updated, error
      app_state  key/value (the active-dataset selection)
      scenarios  one row per generated scenario (metadata + file path)

Large datasets are never stored in the database; it only holds metadata. The registry can be
rebuilt from the files at any time (startup migration), so a missing or corrupt database never
loses data: it is moved aside and re-created. Uses only the standard library (sqlite3); every
call opens its own short-lived connection, so it is safe across FastAPI's worker threads.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .config import DATA_DIR

log = logging.getLogger("silicopulse.persistence")

DB_PATH = DATA_DIR / "silicopulse.db"
SCHEMA_VERSION = 1
_init_lock = threading.Lock()
_ready_for: Path | None = None


class StorageError(RuntimeError):
    """The persistent store could not be read or written (message is safe to show)."""


# ------------------------------------------------------------------------------- safe file writes
def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write to a temp file in the same folder, fsync, then atomically replace the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_parquet(frame, path: Path) -> None:
    """polars DataFrame -> parquet, atomically (a crash never leaves a half-written dataset)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        frame.write_parquet(tmp)
        with open(tmp, "rb+") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def quarantine(path: Path, reason: str) -> Path | None:
    """Move an unreadable file aside (never delete user data) and return its new path."""
    if not path.exists():
        return None
    target = path.with_name(f"{path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        os.replace(path, target)
        log.warning("quarantined %s -> %s (%s)", path.name, target.name, reason)
        return target
    except OSError:
        log.exception("could not quarantine %s", path)
        return None


# ------------------------------------------------------------------------------- database
_SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    dataset_id    TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    dataset_name  TEXT NOT NULL,
    dataset_type  TEXT NOT NULL,
    pipeline      TEXT NOT NULL,
    row_count     INTEGER,
    column_count  INTEGER,
    file_path     TEXT,
    status        TEXT NOT NULL DEFAULT 'available',
    error         TEXT,
    entry         TEXT NOT NULL,            -- JSON: the full dataset description served by /api/active-dataset
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS app_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS scenarios (
    scenario_id      TEXT PRIMARY KEY,
    base_dataset_id  TEXT NOT NULL,
    scenario         TEXT NOT NULL,
    label            TEXT NOT NULL,
    row_count        INTEGER,
    column_count     INTEGER,
    file_path        TEXT NOT NULL,
    info             TEXT NOT NULL,          -- JSON: the metadata returned when it was generated
    created_at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS scenarios_created ON scenarios(created_at);
"""


def _connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=15, isolation_level=None)  # autocommit; explicit BEGIN for writes
    try:
        con.execute("PRAGMA busy_timeout = 15000")
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = NORMAL")
    except BaseException:
        con.close()  # never leave a handle open on a bad file (Windows would keep it locked)
        raise
    return con


def _create(path: Path) -> None:
    con = _connect(path)
    try:
        con.executescript(_SCHEMA)
        version = con.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise StorageError(f"The database {path.name} was created by a newer version (schema {version}).")
        if version < SCHEMA_VERSION:  # future migrations go here, one step at a time
            con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    finally:
        con.close()


def init(path: Path | None = None) -> bool:
    """Create or open the registry. A corrupt database is moved aside and re-created; returns True
    when the registry is new (the caller then rebuilds it from the data files)."""
    global _ready_for
    path = path or DB_PATH
    with _init_lock:
        fresh = not path.exists()
        if not fresh:
            try:
                con = _connect(path)
                try:
                    ok = con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
                finally:
                    con.close()
            except sqlite3.DatabaseError as e:
                ok = False
                log.warning("registry unreadable: %s", e)
            if not ok:
                for suffix in ("", "-wal", "-shm"):
                    quarantine(Path(str(path) + suffix), "corrupt registry database")
                fresh = True
        try:
            _create(path)
        except sqlite3.Error as e:
            raise StorageError(f"The metadata database could not be opened ({type(e).__name__}).") from e
        _ready_for = path
        return fresh


@contextmanager
def _db(write: bool = False, path: Path | None = None):
    path = path or DB_PATH
    if _ready_for != path:
        init(path)
    try:
        con = _connect(path)
    except sqlite3.Error as e:
        raise StorageError(f"The metadata database is unavailable ({type(e).__name__}).") from e
    try:
        if write:
            con.execute("BEGIN IMMEDIATE")
        yield con
        if write:
            con.execute("COMMIT")
    except sqlite3.Error as e:
        if write:
            try:
                con.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        raise StorageError(f"A metadata {'write' if write else 'read'} failed ({type(e).__name__}: {e}).") from e
    finally:
        con.close()


# ------------------------------------------------------------------------------- app state
def get_state(key: str, default=None):
    with _db() as con:
        row = con.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        log.warning("invalid JSON in app_state[%s]; ignoring", key)
        return default


def set_state(key: str, value) -> None:
    with _db(write=True) as con:
        con.execute("INSERT INTO app_state(key, value, updated_at) VALUES(?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                    (key, json.dumps(value), time.time()))


# ------------------------------------------------------------------------------- datasets
_REQUIRED = ("dataset_id", "source", "dataset_name", "dataset_type", "pipeline")


def upsert_dataset(entry: dict, file_path: Path | None, status: str = "available", error: str | None = None) -> None:
    missing = [k for k in _REQUIRED if not entry.get(k)]
    if missing:
        raise StorageError(f"Invalid dataset metadata (missing {', '.join(missing)}).")
    now = time.time()
    entry = {k: v for k, v in entry.items() if k != "status"}  # status is derived from the active selection
    with _db(write=True) as con:
        con.execute(
            "INSERT INTO datasets(dataset_id, source, dataset_name, dataset_type, pipeline, row_count, column_count, file_path, status,"
            " error, entry, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(dataset_id) DO UPDATE SET source=excluded.source, dataset_name=excluded.dataset_name,"
            " dataset_type=excluded.dataset_type, pipeline=excluded.pipeline, row_count=excluded.row_count,"
            " column_count=excluded.column_count, file_path=excluded.file_path, status=excluded.status, error=excluded.error,"
            " entry=excluded.entry, updated_at=excluded.updated_at",
            (entry["dataset_id"], entry["source"], entry["dataset_name"], entry["dataset_type"], entry["pipeline"], entry.get("row_count"),
             entry.get("column_count"), str(file_path) if file_path else None, status, error, json.dumps(entry, default=str), now, now))


def get_dataset(dataset_id: str) -> dict | None:
    with _db() as con:
        row = con.execute("SELECT entry, status, error, file_path, created_at, updated_at FROM datasets WHERE dataset_id = ?",
                          (dataset_id,)).fetchone()
    if row is None:
        return None
    try:
        entry = json.loads(row[0])
    except json.JSONDecodeError:
        log.warning("invalid metadata for dataset %s", dataset_id)
        return None
    return {"entry": entry, "status": row[1], "error": row[2], "file_path": row[3], "created_at": row[4], "updated_at": row[5]}


def record_error(dataset_id: str, source: str, name: str, dataset_type: str, pipeline: str, file_path: Path | None, error: str) -> None:
    """Record a dataset that exists on disk but cannot be loaded (status 'error'), even with no prior record."""
    upsert_dataset({"dataset_id": dataset_id, "source": source, "dataset_name": name, "dataset_type": dataset_type, "pipeline": pipeline,
                    "row_count": None, "column_count": None}, file_path, status="error", error=error)


def list_datasets() -> list[dict]:
    with _db() as con:
        ids = [r[0] for r in con.execute("SELECT dataset_id FROM datasets ORDER BY created_at")]
    return [d for d in (get_dataset(i) for i in ids) if d]


def mark_dataset(dataset_id: str, status: str, error: str | None = None) -> None:
    with _db(write=True) as con:
        con.execute("UPDATE datasets SET status = ?, error = ?, updated_at = ? WHERE dataset_id = ?", (status, error, time.time(), dataset_id))


def delete_dataset(dataset_id: str) -> None:
    with _db(write=True) as con:
        con.execute("DELETE FROM datasets WHERE dataset_id = ?", (dataset_id,))


# ------------------------------------------------------------------------------- scenarios
def add_scenario(info: dict, file_path: Path) -> None:
    with _db(write=True) as con:
        con.execute("INSERT INTO scenarios(scenario_id, base_dataset_id, scenario, label, row_count, column_count, file_path, info, created_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (info["scenario_id"], info["base_dataset"]["dataset_id"], info["scenario"], info["label"], info.get("rows"),
                     info.get("column_count"), str(file_path), json.dumps(info, default=str), time.time()))


def get_scenario(scenario_id: str) -> dict | None:
    with _db() as con:
        row = con.execute("SELECT info, file_path FROM scenarios WHERE scenario_id = ?", (scenario_id,)).fetchone()
    if row is None:
        return None
    return {"info": json.loads(row[0]), "file_path": row[1]}


def scenarios_to_prune(keep: int) -> list[dict]:
    """Oldest scenarios beyond the newest `keep` (id + file path), for the caller to delete."""
    with _db() as con:
        rows = con.execute("SELECT scenario_id, file_path FROM scenarios ORDER BY created_at DESC LIMIT -1 OFFSET ?", (keep,)).fetchall()
    return [{"scenario_id": r[0], "file_path": r[1]} for r in rows]


def delete_scenario(scenario_id: str) -> None:
    with _db(write=True) as con:
        con.execute("DELETE FROM scenarios WHERE scenario_id = ?", (scenario_id,))
