"""In-memory dataset + model state shared by all API routes.

Exactly one EXECUTION dataset is loaded at a time: the synthetic benchmark or an uploaded
execution file. Every analytics/ML/AI endpoint reads `store.df` / `store.sql(...)`, so
activating an upload transparently re-points all of them. An upload is persisted to disk and
restored on API restart.

The app-wide "active dataset" (benchmark | uploaded execution | uploaded telemetry) is recorded
in active_dataset.json. Switching to the benchmark keeps the uploaded file on disk, so the user
can switch back without uploading it again (see app/active_dataset.py).
"""
from __future__ import annotations

import json
import threading
import time

import duckdb
import polars as pl

from . import ml
from .config import DATA_DIR, DEFAULT_CONFIG_PARAMS, DEFAULT_RANDOM_VARS, DEFAULT_RUNS, META_PATH, RUNS_PATH
from .generator import generate_dataset

UPLOAD_PATH = DATA_DIR / "uploaded.parquet"
UPLOAD_META_PATH = DATA_DIR / "uploaded_meta.json"
ACTIVE_PATH = DATA_DIR / "active_dataset.json"


def read_active() -> dict:
    """{"active": benchmark|uploaded_execution|uploaded_telemetry, "execution_source": benchmark|uploaded}.
    Without the file (older installs) the previous rule applies: an uploaded file on disk is active."""
    try:
        a = json.loads(ACTIVE_PATH.read_text())
        if a.get("active") in ("benchmark", "uploaded_execution", "uploaded_telemetry") and a.get("execution_source") in ("benchmark", "uploaded"):
            return a
    except Exception:
        pass
    up = UPLOAD_PATH.exists() and UPLOAD_META_PATH.exists()
    return {"active": "uploaded_execution" if up else "benchmark", "execution_source": "uploaded" if up else "benchmark"}


def write_active(active: str, execution_source: str) -> None:
    ACTIVE_PATH.write_text(json.dumps({"active": active, "execution_source": execution_source}))


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

    def load_or_generate(self) -> None:
        if read_active()["execution_source"] == "uploaded" and UPLOAD_PATH.exists() and UPLOAD_META_PATH.exists():
            try:
                self._activate(pl.read_parquet(UPLOAD_PATH), json.loads(UPLOAD_META_PATH.read_text()))
                return
            except Exception:  # corrupt / incompatible upload -> fall back to the benchmark
                self._clear_upload()
        self._load_benchmark()

    def _load_benchmark(self) -> None:
        if RUNS_PATH.exists() and META_PATH.exists():
            self._activate(pl.read_parquet(RUNS_PATH), json.loads(META_PATH.read_text()))
        else:
            self.regenerate(DEFAULT_RUNS, DEFAULT_CONFIG_PARAMS, DEFAULT_RANDOM_VARS, 42)

    def regenerate(self, n_runs: int, n_config: int, n_random: int, seed: int) -> dict:
        t0 = time.perf_counter()
        df = generate_dataset(n_runs, n_config, n_random, seed)
        gen_s = time.perf_counter() - t0
        self._activate(df, json.loads(META_PATH.read_text()))
        write_active("benchmark", "benchmark")  # the uploaded file is kept and stays selectable
        return {"generation_seconds": round(gen_s, 2), "training_seconds": round(self.train_seconds, 2)}

    def activate_upload(self, df: pl.DataFrame, meta: dict) -> None:
        self._activate(df, meta)  # train first: a CSV that fails training never replaces the active dataset
        df.write_parquet(UPLOAD_PATH)
        UPLOAD_META_PATH.write_text(json.dumps(meta, indent=2, default=str))
        write_active("uploaded_execution", "uploaded")

    def reset_to_benchmark(self) -> None:
        """Make the benchmark active. The uploaded file is kept so it can be selected again."""
        self._load_benchmark()
        write_active("benchmark", "benchmark")

    def activate_saved_upload(self) -> None:
        """Re-activate the uploaded execution dataset kept on disk (no re-upload needed)."""
        if not (UPLOAD_PATH.exists() and UPLOAD_META_PATH.exists()):
            raise FileNotFoundError("No uploaded execution dataset is available.")
        self._activate(pl.read_parquet(UPLOAD_PATH), json.loads(UPLOAD_META_PATH.read_text()))
        write_active("uploaded_execution", "uploaded")

    def _clear_upload(self) -> None:
        UPLOAD_PATH.unlink(missing_ok=True)
        UPLOAD_META_PATH.unlink(missing_ok=True)

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

            st = telemetry_store.status()
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
