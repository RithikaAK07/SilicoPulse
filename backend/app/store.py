"""In-memory dataset + model state shared by all API routes.

Exactly one EXECUTION dataset is loaded at a time: the synthetic benchmark or an uploaded
execution file. Every analytics/ML/AI endpoint reads `store.df` / `store.sql(...)`, so
activating an upload transparently re-points all of them. An upload is persisted to disk and
restored on API restart.

The app-wide "active dataset" (benchmark | uploaded execution | uploaded telemetry) is recorded
in the SQLite registry (app/persistence.py; mirrored to active_dataset.json for recovery).
Switching to the benchmark keeps the uploaded file on disk, so the user can switch back without
uploading it again (see app/active_dataset.py).

Persistence model: datasets live in parquet files written atomically; their metadata lives in
the registry. The DataFrame, Arrow table, trained models and analytics cache are derived state
and are rebuilt from those files at startup. A file that cannot be loaded is quarantined (moved
aside), never deleted. Dataset switches are serialized by `switch_lock`.
"""
from __future__ import annotations

import json
import logging
import threading
import time

import duckdb
import polars as pl

from . import ml, persistence
from .config import DATA_DIR, DEFAULT_CONFIG_PARAMS, DEFAULT_RANDOM_VARS, DEFAULT_RUNS, META_PATH, RUNS_PATH
from .generator import generate_dataset

log = logging.getLogger("silicopulse.store")

UPLOAD_PATH = DATA_DIR / "uploaded.parquet"
UPLOAD_META_PATH = DATA_DIR / "uploaded_meta.json"
ACTIVE_PATH = DATA_DIR / "active_dataset.json"


_ACTIVE_VALUES = ("benchmark", "uploaded_execution", "uploaded_telemetry")


def _valid_active(a) -> bool:
    return isinstance(a, dict) and a.get("active") in _ACTIVE_VALUES and a.get("execution_source") in ("benchmark", "uploaded")


def _legacy_active() -> dict | None:
    try:
        a = json.loads(ACTIVE_PATH.read_text())
        return a if _valid_active(a) else None
    except (OSError, ValueError):
        return None


def read_active() -> dict:
    """{"active": benchmark|uploaded_execution|uploaded_telemetry, "execution_source": benchmark|uploaded}.

    Source of truth: the registry. Older installs are migrated from active_dataset.json, and
    without either the previous rule applies (an uploaded file on disk is active). If the registry
    is unavailable the JSON mirror is used, so a read never invents a selection."""
    try:
        a = persistence.get_state("active")
        if _valid_active(a):
            return {"active": a["active"], "execution_source": a["execution_source"]}
        legacy = _legacy_active()
        if legacy:  # one-time migration of the pre-registry selection
            persistence.set_state("active", legacy)
            return legacy
    except persistence.StorageError as e:
        log.warning("registry unavailable for the active selection, using the file mirror: %s", e)
        legacy = _legacy_active()
        if legacy:
            return legacy
    up = UPLOAD_PATH.exists() and UPLOAD_META_PATH.exists()
    return {"active": "uploaded_execution" if up else "benchmark", "execution_source": "uploaded" if up else "benchmark"}


def write_active(active: str, execution_source: str) -> None:
    """Persist the selection (registry first; a failed write raises StorageError, never fakes success)."""
    value = {"active": active, "execution_source": execution_source}
    if not _valid_active(value):
        raise ValueError(f"invalid active dataset selection: {value}")
    persistence.set_state("active", value)
    persistence.atomic_write_text(ACTIVE_PATH, json.dumps(value))  # recovery mirror


def _register(dataset_id: str, df: pl.DataFrame | None, path, meta: dict) -> None:
    """Record execution dataset metadata in the registry (best effort: the dataset itself is already saved)."""
    from .active_dataset import register_execution  # local import: active_dataset imports this module

    try:
        register_execution(dataset_id, df, path, meta)
    except Exception:
        log.exception("could not register %s metadata", dataset_id)


class Store:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.df: pl.DataFrame | None = None
        self.arrow = None
        self.meta: dict = {}
        self.bundle: ml.ModelBundle | None = None
        self.cache: dict = {}
        self.train_seconds = 0.0
        self.activated_at = 0.0
        self._key_locks: dict[str, threading.Lock] = {}
        self.warm_status = {"state": "idle", "seconds": 0.0}
        # one dataset switch / upload / regeneration at a time (they write the same files)
        self.switch_lock = threading.RLock()

    def load_or_generate(self) -> None:
        """Startup: open/migrate the registry, then load the persisted execution dataset."""
        with self.switch_lock:
            persistence.init()
            act = read_active()
            if act["execution_source"] == "uploaded" and UPLOAD_PATH.exists() and UPLOAD_META_PATH.exists():
                try:
                    meta = json.loads(UPLOAD_META_PATH.read_text())
                    self._activate(pl.read_parquet(UPLOAD_PATH), meta)
                    _register("upload-execution", self.df, UPLOAD_PATH, meta)
                    return
                except Exception as e:  # unreadable upload -> keep it aside (never delete it) and use the benchmark
                    log.exception("uploaded dataset could not be loaded")
                    reason = f"The uploaded dataset could not be loaded ({type(e).__name__}); it was moved aside."
                    try:  # its name, for the error record (read before the files are moved aside)
                        name = json.loads(UPLOAD_META_PATH.read_text()).get("filename")
                    except (OSError, ValueError):
                        name = None
                    for path in (UPLOAD_PATH, UPLOAD_META_PATH):
                        persistence.quarantine(path, reason)
                    try:
                        persistence.record_error("upload-execution", "uploaded", name or "uploaded execution file", "execution", "execution",
                                                 UPLOAD_PATH, reason)
                    except persistence.StorageError:
                        log.warning("could not record the unreadable upload in the registry")
                    write_active("benchmark" if act["active"] == "uploaded_execution" else act["active"], "benchmark")
            self._load_benchmark()

    def sync_registry(self) -> None:
        """Bring the registry in line with the data files (startup migration; best effort, never fatal)."""
        from .active_dataset import sync_registry  # local import: active_dataset imports this module

        try:
            sync_registry()
        except Exception:
            log.exception("registry synchronisation failed")

    def _load_benchmark(self) -> None:
        if RUNS_PATH.exists() and META_PATH.exists():
            try:
                meta = json.loads(META_PATH.read_text())
                self._activate(pl.read_parquet(RUNS_PATH), meta)
                _register("benchmark", self.df, RUNS_PATH, meta)
                return
            except Exception as e:  # unreadable benchmark: keep it aside and rebuild the default one
                log.exception("benchmark dataset could not be loaded")
                for path in (RUNS_PATH, META_PATH):
                    persistence.quarantine(path, f"unreadable benchmark ({type(e).__name__})")
        self._generate_benchmark(DEFAULT_RUNS, DEFAULT_CONFIG_PARAMS, DEFAULT_RANDOM_VARS, 42)

    def _generate_benchmark(self, n_runs: int, n_config: int, n_random: int, seed: int) -> float:
        t0 = time.perf_counter()
        df = generate_dataset(n_runs, n_config, n_random, seed)
        gen_s = time.perf_counter() - t0
        meta = json.loads(META_PATH.read_text())
        self._activate(df, meta)
        _register("benchmark", df, RUNS_PATH, meta)
        return gen_s

    def regenerate(self, n_runs: int, n_config: int, n_random: int, seed: int) -> dict:
        with self.switch_lock:
            gen_s = self._generate_benchmark(n_runs, n_config, n_random, seed)
            write_active("benchmark", "benchmark")  # the uploaded file is kept and stays selectable
        return {"generation_seconds": round(gen_s, 2), "training_seconds": round(self.train_seconds, 2)}

    def activate_upload(self, df: pl.DataFrame, meta: dict) -> None:
        with self.switch_lock:
            self._activate(df, meta)  # train first: a CSV that fails training never replaces the active dataset
            persistence.atomic_write_parquet(df, UPLOAD_PATH)
            persistence.atomic_write_text(UPLOAD_META_PATH, json.dumps(meta, indent=2, default=str))
            _register("upload-execution", df, UPLOAD_PATH, meta)
            write_active("uploaded_execution", "uploaded")

    def reset_to_benchmark(self) -> None:
        """Make the benchmark active. The uploaded file is kept so it can be selected again."""
        with self.switch_lock:
            self._load_benchmark()
            write_active("benchmark", "benchmark")

    def activate_saved_upload(self) -> None:
        """Re-activate the uploaded execution dataset kept on disk (no re-upload needed)."""
        with self.switch_lock:
            if not (UPLOAD_PATH.exists() and UPLOAD_META_PATH.exists()):
                raise FileNotFoundError("No uploaded execution dataset is available.")
            meta = json.loads(UPLOAD_META_PATH.read_text())
            self._activate(pl.read_parquet(UPLOAD_PATH), meta)
            write_active("uploaded_execution", "uploaded")

    def _clear_upload(self) -> None:
        """Kept for compatibility; uploads are no longer deleted by the store (they are quarantined)."""
        for path in (UPLOAD_PATH, UPLOAD_META_PATH):
            persistence.quarantine(path, "cleared")

    def _activate(self, df: pl.DataFrame, meta: dict) -> None:
        t0 = time.perf_counter()
        bundle = ml.train(df, meta)
        with self.lock:
            self.df, self.meta, self.bundle, self.cache = df, meta, bundle, {}
            self.arrow = df.to_arrow()
            self.train_seconds = time.perf_counter() - t0
            self.activated_at = time.time()
        threading.Thread(target=self._warm, args=(self.activated_at,), daemon=True).start()

    def _warm(self, version: float) -> None:
        """Pre-compute the heavy analytics in the background so the first page loads stay fast (100K+ runs)."""
        from . import analytics, insights  # local import: those modules import the store

        t0 = time.perf_counter()
        self.warm_status = {"state": "warming", "seconds": 0.0}
        jobs = [analytics.discovery, analytics.randomization, analytics.root_cause, analytics.drift,
                insights.data_quality, lambda: insights.insights({}), lambda: insights.guardrails({})]
        for job in jobs:
            if version != self.activated_at:  # a newer dataset was activated meanwhile
                return
            try:
                job()
            except Exception as e:  # warm-up is best effort; the request path reports real errors
                self.warm_status = {"state": f"error: {type(e).__name__}: {e}", "seconds": round(time.perf_counter() - t0, 2)}
                return
        self.warm_status = {"state": "ready", "seconds": round(time.perf_counter() - t0, 2)}

    def dataset_status(self) -> dict:
        m = self.meta
        uploaded = m.get("source") == "uploaded"
        return {
            "source": "uploaded" if uploaded else "benchmark",
            "label": "Using Uploaded CSV" if uploaded else "Using Benchmark Data",
            "filename": m.get("filename"),
            "rows": 0 if self.df is None else len(self.df),
            "config_params": len(m.get("config_params", [])),
            "random_vars": len(m.get("random_vars", [])),
            "synthetic_columns": m.get("synthetic_columns", []),
            "leakage_dropped": m.get("leakage_dropped", []),
            "activated_at": m.get("generated_at"),
            "version": self.activated_at,
            **self._active_info(),
        }

    @staticmethod
    def _active_info() -> dict:
        """App-wide active dataset (see app/active_dataset.py); telemetry is described from its own store."""
        active = read_active()["active"]
        info = {"active": active}
        if active == "uploaded_telemetry":
            from .telemetry import telemetry_store  # local import: telemetry is optional at start-up

            st = telemetry_store.info()
            if st.get("active"):
                m = st["meta"]
                info.update(active_name=m.get("original_filename"), active_rows=m.get("rows"), active_type=m.get("dataset_type"),
                            active_channels=len(m.get("channels", [])))
            else:
                info["active"] = "uploaded_execution" if read_active()["execution_source"] == "uploaded" else "benchmark"
        return info

    def sql(self, query: str, params: list | None = None) -> list[dict]:
        """Run an OLAP query with DuckDB directly over the in-memory Polars frame (zero-copy via Arrow)."""
        con = duckdb.connect()
        try:
            con.register("runs", self.arrow)
            rel = con.execute(query, params or [])
            cols = [d[0] for d in rel.description]
            return [dict(zip(cols, r)) for r in rel.fetchall()]
        finally:
            con.close()

    def cached(self, key: str, fn):
        """Memoise per active dataset; a per-key lock stops concurrent requests computing the same thing twice."""
        cache = self.cache
        if key in cache:
            return cache[key]
        with self.lock:
            lk = self._key_locks.setdefault(key, threading.Lock())
        with lk:
            if key not in cache:
                cache[key] = fn()
            return cache[key]


store = Store()
