"""Central configuration for the SilicoPulse backend (all secrets come from the environment)."""
import os
from pathlib import Path

try:  # local development: read backend/.env (gitignored). On Railway, variables come from the service.
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # pragma: no cover
    pass

# ---------------------------------------------------------------------------
# Gemini AI layer
# NEVER commit a real key. Put it in backend/.env (GEMINI_API_KEY=...) for local
# development, or in the Railway service variables for production. The constant
# below stays empty in source control.
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


DATA_DIR = Path(os.getenv("SILICOPULSE_DATA_DIR", "").strip() or Path(__file__).resolve().parent.parent / "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
RUNS_PATH = DATA_DIR / "runs.parquet"
META_PATH = DATA_DIR / "meta.json"


def _env_num(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Evidence thresholds (override via environment variables). They stop tiny subgroups
# from being reported as "toxic", "anomalous" or "deterministic".
# ---------------------------------------------------------------------------
MIN_PAIR_SAMPLES = int(_env_num("MIN_PAIR_SAMPLES", 0))  # 0 = auto: scales with dataset size (min 15)
MIN_GROUP_SAMPLES = int(_env_num("MIN_GROUP_SAMPLES", 30))  # parameter values / threshold groups
MIN_SEED_RUNS = int(_env_num("MIN_SEED_RUNS", 20))  # seeds need this many runs to be scored
MIN_PROFILE_REPEATS = int(_env_num("MIN_PROFILE_REPEATS", 5))  # repeats before calling a config deterministic
MIN_PROFILE_SEEDS = int(_env_num("MIN_PROFILE_SEEDS", 3))  # distinct seeds before calling a config deterministic
SIGNIFICANCE_ALPHA = _env_num("SIGNIFICANCE_ALPHA", 0.01)

# CORS: comma-separated allowed origins ("*" = any, the local-development default).
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]

DEFAULT_RUNS = int(_env_num("SILICOPULSE_DEFAULT_RUNS", 10_000))
DEFAULT_CONFIG_PARAMS = 100
DEFAULT_RANDOM_VARS = 51
