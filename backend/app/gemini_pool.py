"""Shared Gemini model pool.

Free-tier quotas are enforced per model (e.g. 20 requests/day/model), so SilicoPulse rotates
across several Flash models. A model that returns 429 is parked until Google's retryDelay
expires; 503 (overloaded) parks it briefly. Every Gemini caller asks the pool which models are
currently usable, in preference order.
"""
from __future__ import annotations

import json
import time

from .config import GEMINI_MODELS

_blocked_until: dict[str, float] = {}


def available_models() -> list[str]:
    now = time.time()
    return [m for m in GEMINI_MODELS if _blocked_until.get(m, 0) <= now]


def next_available_in() -> float:
    """Seconds until the earliest parked model frees up (0 if one is usable now)."""
    if available_models():
        return 0.0
    return max(0.0, min(_blocked_until.values()) - time.time())


def block(model: str, seconds: float) -> None:
    _blocked_until[model] = max(_blocked_until.get(model, 0), time.time() + seconds)


def retry_delay(body: bytes) -> float | None:
    """google.rpc.RetryInfo.retryDelay (e.g. "17s") from an error body."""
    try:
        for d in json.loads(body).get("error", {}).get("details", []):
            if str(d.get("@type", "")).endswith("RetryInfo"):
                return float(str(d.get("retryDelay", "")).rstrip("s"))
    except (ValueError, AttributeError):
        pass
    return None


def note_failure(model: str, status: int, body: bytes) -> float | None:
    """Park a model after a failed call; returns the retry delay for 429s."""
    if status == 429:
        delay = retry_delay(body) or 30.0
        block(model, delay)
        return delay
    if status in (500, 502, 503, 504):
        block(model, 20.0)
    elif status == 404:  # model retired / not enabled for this key
        block(model, 6 * 3600.0)
    return None
