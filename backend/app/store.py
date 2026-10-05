"""In-memory dataset + model state shared by all API routes.

Exactly one dataset is active at a time: the synthetic benchmark or an uploaded CSV.
Every analytics/ML/AI endpoint reads `store.df` / `store.sql(...)`, so activating an
upload transparently re-points all of them. An active upload is persisted to disk and
restored on API restart.
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

    def load_or_generate(self) -> None:
        if UPLOAD_PATH.exists() and UPLOAD_META_PATH.exists():
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
        self._clear_upload()
        self._activate(df, json.loads(META_PATH.read_text()))
        return {"generation_seconds": round(gen_s, 2), "training_seconds": round(self.train_seconds, 2)}

    def activate_upload(self, df: pl.DataFrame, meta: dict) -> None:
        self._activate(df, meta)  # train first: a CSV that fails training never replaces the active dataset
        df.write_parquet(UPLOAD_PATH)
        UPLOAD_META_PATH.write_text(json.dumps(meta, indent=2, default=str))

    def reset_to_benchmark(self) -> None:
        self._clear_upload()
        self._load_benchmark()

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
        }

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
        if key not in self.cache:
            self.cache[key] = fn()
        return self.cache[key]


store = Store()
