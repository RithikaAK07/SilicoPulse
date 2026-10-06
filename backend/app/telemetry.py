"""Telemetry-only datasets (no PASS/FAIL outcome).

Kept separate from the execution-log store: activating a telemetry dataset never replaces the
active execution dataset, never trains the failure model and never invents outcome labels.
Every statistic is computed from measured values only. Execution analyses (PASS/FAIL, configuration,
seed, failure signatures) belong to the execution pipeline and are simply not part of this one; the
dataset-type registry (preprocessor/dataset_types.py) lists what each pipeline provides.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

import numpy as np
import polars as pl

from . import persistence
from .config import DATA_DIR
from .preprocessor.dataset_types import describe

log = logging.getLogger("silicopulse.telemetry")
TELEMETRY_PATH = DATA_DIR / "telemetry.parquet"
TELEMETRY_META_PATH = DATA_DIR / "telemetry_meta.json"
MAX_POINTS = 600
ROBUST_Z = 3.5
MAX_WINDOWS = 25
# channel health rules (deterministic; shown to the user with each status)
ATTENTION_ANOMALY_SHARE, WATCH_ANOMALY_SHARE = 0.05, 0.0
ATTENTION_DRIFT_SD, WATCH_DRIFT_SD = 1.0, 0.5




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


def _robust_flags(vals: np.ndarray) -> tuple[np.ndarray, float]:
    """Boolean mask of samples outside the robust band (|z| > ROBUST_Z, median/MAD); NaN = missing."""
    ok = ~np.isnan(vals)
    flags = np.zeros(len(vals), dtype=bool)
    if ok.sum() < 3:
        return flags, float("nan")
    med = float(np.median(vals[ok]))
    mad = float(np.median(np.abs(vals[ok] - med)))
    if mad > 0:
        flags[ok] = np.abs(0.6745 * (vals[ok] - med) / mad) > ROBUST_Z
    return flags, med


def anomaly_windows(frame: pl.DataFrame, channels: list[dict], has_ts: bool) -> list[dict]:
    """Contiguous runs of anomalous samples per channel (rows are in time order), with what the other
    channels did meanwhile. Purely descriptive: no cause is inferred."""
    out = []
    cols = {c["name"]: frame[c["name"]].cast(pl.Float64).fill_null(float("nan")).to_numpy() for c in channels}
    ts = frame["timestamp"] if has_ts else None
    for ch in channels:
        vals = cols[ch["name"]]
        flags, med = _robust_flags(vals)
        if not flags.any():
            continue
        edges = np.flatnonzero(np.diff(np.concatenate([[0], flags.astype(np.int8), [0]])))
        for a, b in zip(edges[::2], edges[1::2]):  # [a, b) anomalous run
            seg = vals[a:b]
            w = {"channel": ch["name"], "original": ch["original"], "unit": ch["unit"], "samples": int(b - a),
                 "start_row": int(a), "end_row": int(b - 1), "min": float(np.nanmin(seg)), "max": float(np.nanmax(seg)),
                 "median": float(np.nanmedian(seg)), "direction": "low" if float(np.nanmedian(seg)) < med else "high",
                 "typical": med}
            if ts is not None:
                t0, t1 = ts[int(a)], ts[int(b - 1)]
                w["start"] = t0.isoformat() if t0 is not None else None
                w["end"] = t1.isoformat() if t1 is not None else None
                w["duration_s"] = (t1 - t0).total_seconds() if t0 is not None and t1 is not None else None
            during = {}
            for other in channels:
                if other["name"] == ch["name"]:
                    continue
                o = cols[other["name"]]
                inside, outside = o[a:b], np.concatenate([o[:a], o[b:]])
                if np.isfinite(inside).any() and np.isfinite(outside).any():
                    during[other["original"]] = {"inside": float(np.nanmean(inside)), "outside": float(np.nanmean(outside))}
            w["other_channels"] = during
            out.append(w)
    out.sort(key=lambda w: (-w["samples"], w["start_row"]))
    return out


def channel_health(stats: dict, windows: list[dict]) -> dict:
    """stable / watch / attention from anomaly share and drift, with the reasons that triggered it."""
    n = stats.get("count") or 0
    share = (stats.get("anomalies") or 0) / n if n else 0.0
    std = stats.get("std") or 0.0
    drift_sd = abs(stats["trend_change_over_span"]) / std if std and stats.get("trend_change_over_span") is not None else 0.0
    mine = [w for w in windows if w["channel"] == stats["name"]]
    reasons = []
    if share > WATCH_ANOMALY_SHARE:
        lows, highs = sum(w["direction"] == "low" for w in mine), sum(w["direction"] == "high" for w in mine)
        reasons.append(f"{stats['anomalies']} samples ({share:.0%}) outside the normal band in {len(mine)} window(s)"
                       + (f": {lows} low" if lows else "") + (f"{', ' if lows and highs else ': ' if highs else ''}{highs} high" if highs else ""))
    if drift_sd >= WATCH_DRIFT_SD:
        reasons.append(f"level drifted by {drift_sd:.1f} standard deviations over the recording")
    status = ("attention" if share >= ATTENTION_ANOMALY_SHARE or drift_sd >= ATTENTION_DRIFT_SD
              else "watch" if reasons else "stable")
    return {"status": status, "anomaly_share": round(share, 4), "drift_sd": round(drift_sd, 3),
            "reasons": reasons or ["no samples outside the normal band and no material drift"],
            "rule": f"attention: ≥{ATTENTION_ANOMALY_SHARE:.0%} anomalous samples or drift ≥{ATTENTION_DRIFT_SD:g} SD; "
                    f"watch: any anomalous sample or drift ≥{WATCH_DRIFT_SD:g} SD"}


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
    windows = anomaly_windows(frame, meta["channels"], has_ts)
    for c in channels:
        if c.get("count"):
            c["health"] = channel_health(c, windows)
    return {"rows": len(frame), "channels": channels, "timing": timing, "correlations": corr[:15], "series": series,
            "anomaly_windows": windows[:MAX_WINDOWS], "anomaly_windows_total": len(windows),
            "dataset_type": describe(meta.get("dataset_type") or "industrial_telemetry")}


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
            except Exception as e:  # unreadable: keep the files aside (never delete user data)
                log.exception("telemetry dataset could not be loaded")
                self.frame, self.meta = None, None
                for path in (TELEMETRY_PATH, TELEMETRY_META_PATH):
                    persistence.quarantine(path, f"unreadable telemetry dataset ({type(e).__name__})")

    def activate(self, frame: pl.DataFrame, meta: dict) -> dict:
        meta = {**meta, "activated_at": datetime.now(timezone.utc).isoformat()}
        summary = summarize(frame, meta)
        with self.lock:
            persistence.atomic_write_parquet(frame, TELEMETRY_PATH)
            persistence.atomic_write_text(TELEMETRY_META_PATH, json.dumps(meta, default=str))
            self.frame, self.meta, self._summary = frame, meta, summary
        self._register()
        return self.status()

    @staticmethod
    def _register() -> None:
        from .active_dataset import register_telemetry  # local import: active_dataset imports this module

        try:
            register_telemetry()
        except Exception:
            log.exception("could not register the telemetry dataset metadata")

    def info(self) -> dict:
        """Metadata only (no statistics): for registry/selection logic that must stay lightweight."""
        with self.lock:
            if self.frame is None or self.meta is None:
                return {"active": False}
            return {"active": True, "meta": self.meta}

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
