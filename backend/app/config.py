"""Central configuration for the SilicoPulse backend."""
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Gemini AI layer
# Paste a free key from https://aistudio.google.com/apikey here, or set the
# GEMINI_API_KEY environment variable (the env var wins). If the key is the
# placeholder, empty, invalid, or rate limited, every AI feature falls back to
# the local heuristic engine in app/ai.py - the app never crashes.
# ---------------------------------------------------------------------------
GEMINI_API_KEY = ""
# Free-tier quotas are per model (~20 requests/day each), so calls rotate through this pool
# (see app/gemini_pool.py). Order = preference. All verified to work with the configured key.
GEMINI_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-flash-latest",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
]
GEMINI_TIMEOUT_S = 45.0


def gemini_key() -> str:
    key = os.getenv("GEMINI_API_KEY", "").strip() or GEMINI_API_KEY.strip()
    if not key or key.startswith("YOUR_"):
        return ""
    return key


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
RUNS_PATH = DATA_DIR / "runs.parquet"
META_PATH = DATA_DIR / "meta.json"

DEFAULT_RUNS = 10_000
DEFAULT_CONFIG_PARAMS = 100
DEFAULT_RANDOM_VARS = 51
