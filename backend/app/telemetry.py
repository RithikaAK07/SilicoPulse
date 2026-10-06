"""Telemetry-only datasets (no PASS/FAIL outcome).

Kept separate from the execution-log store: activating a telemetry dataset never replaces the
active execution dataset, never trains the failure model and never invents outcome labels.
Every statistic is computed from measured values only; analyses that need an outcome are
reported as unavailable.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

import numpy as np
import polars as pl

from .config import DATA_DIR

TELEMETRY_PATH = DATA_DIR / "telemetry.parquet"
TELEMETRY_META_PATH = DATA_DIR / "telemetry_meta.json"
MAX_POINTS = 600
ROBUST_Z = 3.5

UNAVAILABLE = {
    "PASS/FAIL rate": "outcome",
    "Failure prediction / risk model": "outcome",
    "Configuration recommendation": "outcome",
    "Root cause by failure signature": "error_signature",
    "Seed / determinism analysis": "seed",
}
REQUIRED_MSG = "Required field not available for this analysis."


def _channel_stats(frame: pl.DataFrame, ch: dict, has_ts: bool) -> dict:
    s = frame[ch["name"]]
    vals = s.drop_nulls().to_numpy().astype(float)
    out = {**ch, "count": int(len(vals)), "missing": int(s.null_count())}
    if len(vals) == 0:
        return {**out, "note": "no numeric values"}
    q = np.percentile(vals, [5, 50, 95])
    med = float(q[1])
    mad = float(np.median(np.abs(vals - med)))
    if mad > 0:
        z = 0.6745 * (vals - med) / mad
        anomalies = int((np.abs(z) > ROBUST_Z).sum())
    else:
        anomalies = 0
    out.update(min=float(vals.min()), max=float(vals.max()), mean=float(vals.mean()), std=float(vals.std(ddof=1)) if len(vals) > 1 else 0.0,
               p05=float(q[0]), p50=med, p95=float(q[2]), anomalies=anomalies, anomaly_method=f"robust z-score > {ROBUST_Z} (median/MAD)")
    if has_ts and len(vals) >= 3:
        sub = frame.select(["timestamp", ch["name"]]).drop_nulls()
        if len(sub) >= 3:
            t = sub["timestamp"].cast(pl.Int64).to_numpy().astype(float) / 3_600_000.0
            y = sub[ch["name"]].to_numpy().astype(float)
            if np.ptp(t) > 0:
                slope = float(np.polyfit(t - t[0], y, 1)[0])
                out["trend_per_hour"] = slope
                span = float(np.ptp(t))
                out["trend_change_over_span"] = slope * span
                out["trend_relative"] = (slope * span / abs(out["mean"])) if out["mean"] else None
    return out


def summarize(frame: pl.DataFrame, meta: dict) -> dict:
    has_ts = "timestamp" in frame.columns and frame["timestamp"].drop_nulls().len() > 0
    channels = [_channel_stats(frame, ch, has_ts) for ch in meta["channels"]]
    timing = None
    if has_ts:
        ts = frame["timestamp"].drop_nulls()
        diffs = ts.diff().drop_nulls().cast(pl.Int64).to_numpy() / 1000.0 if len(ts) > 1 else np.array([])
        med = float(np.median(diffs)) if len(diffs) else None
        gaps = int((diffs > 3 * med).sum()) if med else 0
        timing = {"start": ts.min().isoformat(), "end": ts.max().isoformat(),
                  "duration_s": (ts.max() - ts.min()).total_seconds(), "median_interval_s": med, "gaps": gaps,
                  "gap_rule": "interval > 3x the median sampling interval", "duplicates": int(ts.is_duplicated().sum())}
    names = [c["name"] for c in meta["channels"] if frame[c["name"]].drop_nulls().len() > 2]
    corr = []
    if len(names) >= 2:
        sub = frame.select(names).drop_nulls()
        if len(sub) > 2:
            m = np.corrcoef(sub.to_numpy().T)
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    if np.isfinite(m[i, j]):
                        corr.append({"a": names[i], "b": names[j], "r": round(float(m[i, j]), 3), "n": len(sub)})
            corr.sort(key=lambda x: -abs(x["r"]))
    # downsampled series for charts (every k-th row; measured values only)
    k = max(1, len(frame) // MAX_POINTS)
    pts = frame.gather_every(k)
    x = pts["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S").to_list() if has_ts else pts["row_index"].to_list()
    series = {"x": x, "x_kind": "time" if has_ts else "row", "step": k,
              "channels": {c["name"]: [None if v is None or not np.isfinite(v) else round(float(v), 6) for v in pts[c["name"]].to_list()]
                           for c in meta["channels"]}}
    unavailable = [{"analysis": a, "missing_field": f, "message": REQUIRED_MSG} for a, f in UNAVAILABLE.items()]
    return {"rows": len(frame), "channels": channels, "timing": timing, "correlations": corr[:15], "series": series,
            "unavailable": unavailable}


class TelemetryStore:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.frame: pl.DataFrame | None = None
        self.meta: dict | None = None
        self._summary: dict | None = None
        if TELEMETRY_PATH.exists() and TELEMETRY_META_PATH.exists():
            try:
                self.frame = pl.read_parquet(TELEMETRY_PATH)
                self.meta = json.loads(TELEMETRY_META_PATH.read_text())
            except Exception:
                self.frame, self.meta = None, None

    def activate(self, frame: pl.DataFrame, meta: dict) -> dict:
        meta = {**meta, "activated_at": datetime.now(timezone.utc).isoformat()}
        summary = summarize(frame, meta)
        with self.lock:
            tmp = TELEMETRY_PATH.with_suffix(".tmp")
            frame.write_parquet(tmp)
            tmp.replace(TELEMETRY_PATH)
            TELEMETRY_META_PATH.write_text(json.dumps(meta, default=str))
            self.frame, self.meta, self._summary = frame, meta, summary
        return self.status()

    def status(self) -> dict:
        with self.lock:
            if self.frame is None or self.meta is None:
                return {"active": False}
            if self._summary is None:
                self._summary = summarize(self.frame, self.meta)
            return {"active": True, "meta": self.meta, "summary": self._summary}

    def clear(self) -> None:
        with self.lock:
            TELEMETRY_PATH.unlink(missing_ok=True)
            TELEMETRY_META_PATH.unlink(missing_ok=True)
            self.frame, self.meta, self._summary = None, None, None


telemetry_store = TelemetryStore()
