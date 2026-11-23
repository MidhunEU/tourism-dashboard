"""Central configuration for the tourism fetcher and dashboard.

Every tunable value lives here. Each one can be overridden with an environment
variable (see ``.env.example``); a local ``.env`` file is loaded when
``python-dotenv`` is installed, and real environment variables always win.

Nothing in this module performs I/O beyond creating directories on demand, so it
is safe to import from tests, the fetcher and the Streamlit app.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Final

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent

try:  # optional dependency: only needed to read a local .env file
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv is optional at runtime
    load_dotenv = None  # type: ignore[assignment]

if load_dotenv is not None:  # pragma: no cover - trivial wrapper
    load_dotenv(PROJECT_ROOT / ".env")


def _env_str(name: str, default: str) -> str:
    """Read ``name`` from the environment, falling back to ``default``."""
    value = os.environ.get(name, "").strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    """Read an integer from the environment, falling back to ``default``."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    """Read a float from the environment, falling back to ``default``."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def resolve(path: str) -> Path:
    """Resolve ``path`` relative to the project root (absolute paths kept)."""
    candidate = Path(path).expanduser()
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


# --------------------------------------------------------------------------- #
# Collection schedule and HTTP behaviour
# --------------------------------------------------------------------------- #
FETCH_INTERVAL_SECONDS: Final[int] = _env_int("FETCH_INTERVAL_SECONDS", 120)
REQUEST_TIMEOUT: Final[int] = _env_int("REQUEST_TIMEOUT", 10)
RETRY_COUNT: Final[int] = _env_int("RETRY_COUNT", 3)
RETRY_BACKOFF_SECONDS: Final[tuple[float, ...]] = (2.0, 4.0, 8.0)
USER_AGENT: Final[str] = _env_str("USER_AGENT", "tourism-project/1.0")

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
DB_PATH: Final[str] = str(resolve(_env_str("DB_PATH", "tourism.db")))
CSV_PATH: Final[str] = str(resolve(_env_str("CSV_PATH", "data/clean_tourism.csv")))
LOG_FILE: Final[str] = str(resolve(_env_str("LOG_FILE", "logs/app.log")))
LOG_LEVEL: Final[str] = _env_str("LOG_LEVEL", "INFO").upper()

# --------------------------------------------------------------------------- #
# Upstream APIs (must match the course brief; no keys required)
# --------------------------------------------------------------------------- #
OPENSKY_URL: Final[str] = _env_str(
    "OPENSKY_URL", "https://opensky-network.org/api/states/all"
)
OPENMETEO_URL: Final[str] = _env_str(
    "OPENMETEO_URL", "https://api.open-meteo.com/v1/forecast"
)
FRANKFURTER_URL: Final[str] = _env_str(
    "FRANKFURTER_URL",
    "https://api.frankfurter.app/latest?from=USD&to=EUR,INR,JPY,GBP,SGD,AED",
)
OPENMETEO_CURRENT: Final[str] = "temperature_2m,precipitation,weather_code"

# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
DASHBOARD_REFRESH_SECONDS: Final[int] = _env_int("DASHBOARD_REFRESH_SECONDS", 60)
LIVE_MAX_AGE_SECONDS: Final[int] = _env_int("LIVE_MAX_AGE_SECONDS", 300)
STALE_MAX_AGE_SECONDS: Final[int] = _env_int("STALE_MAX_AGE_SECONDS", 1800)
TABLE_NAME: Final[str] = "live_signals"

# --------------------------------------------------------------------------- #
# Cities: exactly the six required by the brief. OpenSky box = lat/lon +/- 0.5.
# --------------------------------------------------------------------------- #
BOX_HALF_SIZE: Final[float] = 0.5

CITIES: Final[list[dict[str, Any]]] = [
    {"city": "Paris", "country": "France", "lat": 48.85, "lon": 2.35},
    {"city": "London", "country": "United Kingdom", "lat": 51.50, "lon": -0.12},
    {"city": "New York", "country": "United States", "lat": 40.71, "lon": -74.00},
    {"city": "Dubai", "country": "United Arab Emirates", "lat": 25.20, "lon": 55.27},
    {"city": "Singapore", "country": "Singapore", "lat": 1.35, "lon": 103.82},
    {"city": "Tokyo", "country": "Japan", "lat": 35.68, "lon": 139.69},
]

CITY_ORDER: Final[tuple[str, ...]] = tuple(c["city"] for c in CITIES)
CITY_TO_COUNTRY: Final[dict[str, str]] = {c["city"]: c["country"] for c in CITIES}


def ensure_directories() -> None:
    """Create the folders the fetcher and dashboard write into."""
    resolve(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
