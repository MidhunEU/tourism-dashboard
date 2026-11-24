#!/usr/bin/env python3
"""Production live-signal collector for the tourism dashboard.

Every ``FETCH_INTERVAL_SECONDS`` (default 120 s) the collector, for each of the
six cities in :mod:`config`:

* counts aircraft inside a +/-0.5 degree box around the city (OpenSky Network),
* reads current temperature / precipitation / weather code (Open-Meteo),
* reads the USD -> EUR rate once per loop (Frankfurter),

and batch-inserts one row per city into the SQLite table ``live_signals``.

Reliability rules:

* every request has a timeout and a bounded retry policy (3 retries, 2/4/8 s);
* HTTP 4xx errors are not retried (except 429, the rate limit);
* a malformed or unexpected payload is logged and never crashes the loop;
* a failing city falls back to its last stored values;
* the loop never exits because of an API or database error.

Usage (Linux):
    source .venv/bin/activate
    python3 fetcher.py             # run forever, one loop every 120 s
    python3 fetcher.py --loops 2   # run exactly two loops (handy for tests)
    python3 fetcher.py --health    # print stored health as JSON and exit
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import sqlite3
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

import config

LOGGER_NAME = "tourism.fetcher"

COLUMNS: tuple[str, ...] = (
    "timestamp",
    "city",
    "planes_in_box",
    "temp_c",
    "precipitation",
    "weather_code",
    "eur_rate",
)
WEATHER_FIELDS: tuple[str, ...] = ("temp_c", "precipitation", "weather_code")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS live_signals (
    timestamp      TEXT NOT NULL,
    city           TEXT NOT NULL,
    planes_in_box  INTEGER,
    temp_c         REAL,
    precipitation  REAL,
    weather_code   INTEGER,
    eur_rate       REAL
);
CREATE INDEX IF NOT EXISTS idx_live_signals_timestamp ON live_signals (timestamp);
CREATE INDEX IF NOT EXISTS idx_live_signals_city ON live_signals (city);
CREATE INDEX IF NOT EXISTS idx_live_signals_city_ts ON live_signals (city, timestamp);
"""

# Counters for the current process, reported by get_health().
_RUN_STATS: dict[str, int] = {"success": 0, "failure": 0}


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class FetcherError(RuntimeError):
    """A request failed after every retry was used up."""


class RetryableHTTPError(requests.RequestException):
    """An HTTP status that is worth retrying (429, 5xx)."""

    def __init__(self, status_code: int, url: str) -> None:
        super().__init__(f"HTTP {status_code} from {url}")
        self.status_code = status_code
        self.url = url


class ResponseValidationError(ValueError):
    """The API answered, but the payload did not have the expected shape."""


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
class _CityFilter(logging.Filter):
    """Give every record a ``city`` attribute so the format string never fails."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "city"):
            record.city = "-"
        return True


def get_logger() -> logging.Logger:
    """Return the collector logger (configured by :func:`setup_logging`)."""
    return logging.getLogger(LOGGER_NAME)


def setup_logging(
    log_file: str | None = None, level: str | None = None
) -> logging.Logger:
    """Configure console + rotating-file logging once and return the logger.

    The console handler writes to stdout; the rotating file handler writes to
    ``config.LOG_FILE`` (1 MB per file, 5 backups). Repeated calls are no-ops.
    """
    logger = get_logger()
    if getattr(logger, "_tourism_configured", False):
        return logger

    config.ensure_directories()
    target = Path(log_file or config.LOG_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)

    logger.setLevel(getattr(logging, (level or config.LOG_LEVEL).upper(), logging.INFO))
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s [%(city)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    city_filter = _CityFilter()

    console = logging.StreamHandler(stream=sys.stdout)
    file_handler = logging.handlers.RotatingFileHandler(
        target, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    for handler in (console, file_handler):
        handler.setFormatter(formatter)
        handler.addFilter(city_filter)
        logger.addHandler(handler)

    logger._tourism_configured = True  # type: ignore[attr-defined]
    return logger


def city_logger(city: str) -> logging.LoggerAdapter:
    """Return a logger adapter that tags every record with ``city``."""
    return logging.LoggerAdapter(get_logger(), {"city": city})


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def create_session() -> requests.Session:
    """Create the shared HTTP session with the project's User-Agent."""
    session = requests.Session()
    session.headers.update(
        {"User-Agent": config.USER_AGENT, "Accept": "application/json"}
    )
    return session


def request_json(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    city: str = "-",
    timeout: int | None = None,
    retries: int | None = None,
    backoff: Sequence[float] | None = None,
    sleep: Callable[[float], None] | None = None,
    log: logging.Logger | logging.LoggerAdapter | None = None,
) -> dict[str, Any]:
    """GET ``url`` and return the decoded JSON object.

    Retries timeouts, connection errors, HTTP 429 and HTTP 5xx with exponential
    backoff (2 s, 4 s, 8 s by default). Other 4xx responses are raised
    immediately. A non-JSON or malformed body raises
    :class:`ResponseValidationError` and is not retried.

    ``sleep`` is injectable so tests can assert the backoff schedule without
    actually waiting.
    """
    log = log or city_logger(city)
    pause = sleep if sleep is not None else time.sleep
    timeout = config.REQUEST_TIMEOUT if timeout is None else timeout
    retries = config.RETRY_COUNT if retries is None else retries
    delays = tuple(backoff) if backoff is not None else config.RETRY_BACKOFF_SECONDS

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = session.get(url, params=params, timeout=timeout)
            if response.status_code == 429 or response.status_code >= 500:
                raise RetryableHTTPError(response.status_code, url)
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                raise ResponseValidationError(
                    f"malformed JSON from {url}: {exc}"
                ) from exc
            if not isinstance(payload, dict):
                raise ResponseValidationError(f"expected a JSON object from {url}")
            return payload
        except (requests.Timeout, requests.ConnectionError, RetryableHTTPError) as exc:
            last_error = exc
            if attempt >= retries:
                break
            delay = delays[min(attempt, len(delays) - 1)]
            log.warning(
                "%s failed (%s); retry %d/%d in %.0f s",
                url,
                exc,
                attempt + 1,
                retries,
                delay,
            )
            pause(delay)
        except requests.HTTPError:
            # Permanent client error (4xx other than 429): do not retry.
            raise

    raise FetcherError(
        f"giving up on {url} after {retries + 1} attempts: {last_error}"
    ) from last_error


def _as_float(value: Any) -> float | None:
    """Coerce ``value`` to float, returning None for missing/invalid input."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    """Coerce ``value`` to int, returning None for missing/invalid input."""
    number = _as_float(value)
    return None if number is None else int(number)


def count_states(payload: dict[str, Any]) -> int:
    """Count aircraft in an OpenSky ``states/all`` payload.

    ``{"states": null}`` legitimately means "no aircraft" and yields 0. A payload
    without the ``states`` key is treated as an invalid response, which the
    caller logs as a warning and skips for that city.
    """
    if "states" not in payload:
        raise ResponseValidationError("OpenSky response has no 'states' key")
    states = payload["states"]
    if states is None:
        return 0
    if not isinstance(states, list):
        raise ResponseValidationError("OpenSky 'states' is not a list")
    return len(states)


def parse_current(payload: dict[str, Any]) -> dict[str, float | int | None]:
    """Extract the three Open-Meteo current values, None when absent."""
    current = payload.get("current")
    if not isinstance(current, dict):
        return {key: None for key in WEATHER_FIELDS}
    return {
        "temp_c": _as_float(current.get("temperature_2m")),
        "precipitation": _as_float(current.get("precipitation")),
        "weather_code": _as_int(current.get("weather_code")),
    }


def fetch_planes(
    session: requests.Session,
    city: dict[str, Any],
    *,
    log: logging.Logger | logging.LoggerAdapter | None = None,
    timeout: int | None = None,
    retries: int | None = None,
) -> int:
    """Number of aircraft inside the city's +/-0.5 degree box.

    ``timeout`` and ``retries`` override the collector defaults; the dashboard's
    on-demand rounds pass a short timeout and no retries so a blocked upstream
    costs seconds instead of minutes.
    """
    lat, lon = float(city["lat"]), float(city["lon"])
    half = config.BOX_HALF_SIZE
    params = {
        "lamin": round(lat - half, 4),
        "lamax": round(lat + half, 4),
        "lomin": round(lon - half, 4),
        "lomax": round(lon + half, 4),
    }
    payload = request_json(
        session,
        config.OPENSKY_URL,
        params=params,
        city=str(city["city"]),
        log=log,
        timeout=timeout,
        retries=retries,
    )
    return count_states(payload)


def fetch_weather(
    session: requests.Session,
    city: dict[str, Any],
    *,
    log: logging.Logger | logging.LoggerAdapter | None = None,
    timeout: int | None = None,
    retries: int | None = None,
) -> dict[str, float | int | None]:
    """Current weather for one city from Open-Meteo."""
    params = {
        "latitude": float(city["lat"]),
        "longitude": float(city["lon"]),
        "current": config.OPENMETEO_CURRENT,
    }
    payload = request_json(
        session,
        config.OPENMETEO_URL,
        params=params,
        city=str(city["city"]),
        log=log,
        timeout=timeout,
        retries=retries,
    )
    return parse_current(payload)


def fetch_fx(session: requests.Session) -> float | None:
    """USD -> EUR rate from Frankfurter (requested once per loop).

    The endpoint is asked for EUR/INR/JPY/GBP/SGD/AED but Frankfurter follows the
    ECB reference set, which has no AED; only the EUR rate is stored.
    """
    payload = request_json(session, config.FRANKFURTER_URL, city="-")
    rates = payload.get("rates")
    if not isinstance(rates, dict):
        return None
    return _as_float(rates.get("EUR"))


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open (and initialise) the SQLite database in WAL mode."""
    path = Path(db_path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def insert_rows(conn: sqlite3.Connection, rows: Sequence[Sequence[Any]]) -> int:
    """Batch-insert one loop's rows in a single transaction; return the count."""
    if not rows:
        return 0
    placeholders = ", ".join("?" for _ in COLUMNS)
    conn.executemany(
        f"INSERT INTO live_signals ({', '.join(COLUMNS)}) VALUES ({placeholders})",
        rows,
    )
    conn.commit()
    return len(rows)


def last_good_row(conn: sqlite3.Connection, city: str) -> dict[str, Any]:
    """Most recent stored values for ``city``, used when an API fails."""
    row = conn.execute(
        "SELECT planes_in_box, temp_c, precipitation, weather_code "
        "FROM live_signals WHERE city = ? "
        "ORDER BY timestamp DESC LIMIT 1",
        (city,),
    ).fetchone()
    if row is None:
        return {}
    return {
        "planes_in_box": row[0],
        "temp_c": row[1],
        "precipitation": row[2],
        "weather_code": row[3],
    }


def last_eur_rate(conn: sqlite3.Connection) -> float | None:
    """Most recent stored USD -> EUR rate, used when Frankfurter fails."""
    row = conn.execute(
        "SELECT eur_rate FROM live_signals "
        "WHERE eur_rate IS NOT NULL ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


@dataclass(frozen=True)
class Health:
    """Snapshot of collector health for monitoring and the CLI."""

    last_fetch: str | None
    rows: int
    cities: int
    success_count: int
    failure_count: int

    def as_dict(self) -> dict[str, Any]:
        """Return the snapshot as a plain dictionary."""
        return asdict(self)


def get_health(conn: sqlite3.Connection | None = None) -> Health:
    """Return the last stored fetch time plus success/failure counters.

    ``success_count`` and ``failure_count`` count city collections in this
    process; ``rows`` and ``cities`` describe what is stored in SQLite.
    """
    owns_connection = conn is None
    connection = conn if conn is not None else connect()
    try:
        row = connection.execute(
            "SELECT MAX(timestamp), COUNT(*), COUNT(DISTINCT city) FROM live_signals"
        ).fetchone()
    finally:
        if owns_connection:
            connection.close()

    last_fetch = row[0] if row else None
    rows = int(row[1] or 0) if row else 0
    cities = int(row[2] or 0) if row else 0
    return Health(
        last_fetch=last_fetch,
        rows=rows,
        cities=cities,
        success_count=_RUN_STATS["success"],
        failure_count=_RUN_STATS["failure"],
    )


# --------------------------------------------------------------------------- #
# Collection
# --------------------------------------------------------------------------- #
def utc_now() -> str:
    """Current UTC time as an ISO-8601 string used for every timestamp."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def collect_city(
    session: requests.Session,
    conn: sqlite3.Connection,
    city: dict[str, Any],
    *,
    log: logging.Logger | logging.LoggerAdapter | None = None,
    timeout: int | None = None,
    retries: int | None = None,
) -> tuple[dict[str, Any], bool]:
    """Collect one city's live signals.

    Returns ``(signals, ok)``. ``ok`` is False when any upstream call failed;
    failed fields fall back to the city's last stored row, and a missing
    OpenSky ``states`` key only skips the aircraft count.
    """
    name = str(city["city"])
    log = log or city_logger(name)
    fallback = last_good_row(conn, name)
    signals: dict[str, Any] = {key: None for key in ("planes_in_box", *WEATHER_FIELDS)}
    ok = True

    try:
        signals["planes_in_box"] = fetch_planes(
            session, city, log=log, timeout=timeout, retries=retries
        )
    except (FetcherError, ResponseValidationError, requests.RequestException) as exc:
        ok = False
        log.warning("skipping planes for this city: %s", exc)
        signals["planes_in_box"] = fallback.get("planes_in_box")

    try:
        signals.update(
            fetch_weather(session, city, log=log, timeout=timeout, retries=retries)
        )
    except (FetcherError, ResponseValidationError, requests.RequestException) as exc:
        ok = False
        log.warning("weather unavailable, reusing last values: %s", exc)
        for key in WEATHER_FIELDS:
            signals[key] = fallback.get(key)

    if signals["planes_in_box"] is None:
        log.warning("no aircraft value available (no fallback row yet)")
    return signals, ok


def run(
    loops: int | None = None,
    *,
    session: requests.Session | None = None,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """Collect live signals until interrupted, or exactly ``loops`` times."""
    log = setup_logging()
    pause = sleep if sleep is not None else time.sleep
    client = session if session is not None else create_session()
    conn = connect()
    iteration = 0
    try:
        while loops is None or iteration < loops:
            iteration += 1
            log.info("loop %d starting", iteration)

            eur_rate: float | None = None
            try:
                eur_rate = fetch_fx(client)
            except (
                FetcherError,
                ResponseValidationError,
                requests.RequestException,
            ) as exc:
                log.warning("Frankfurter unavailable, reusing last rate: %s", exc)
            if eur_rate is None:
                eur_rate = last_eur_rate(conn)

            batch: list[tuple[Any, ...]] = []
            for city in config.CITIES:
                timestamp = utc_now()
                signals, ok = collect_city(client, conn, city)
                _RUN_STATS["success" if ok else "failure"] += 1
                batch.append(
                    (
                        timestamp,
                        city["city"],
                        signals["planes_in_box"],
                        signals["temp_c"],
                        signals["precipitation"],
                        signals["weather_code"],
                        eur_rate,
                    )
                )
                city_logger(str(city["city"])).info("fetched at [%s]", timestamp)

            stored = insert_rows(conn, batch)
            health = get_health(conn)
            log.info(
                "loop %d stored %d rows (last_fetch=%s, successes=%d, failures=%d)",
                iteration,
                stored,
                health.last_fetch,
                health.success_count,
                health.failure_count,
            )

            if loops is not None and iteration >= loops:
                break
            log.info(
                "sleeping %d s before the next loop", config.FETCH_INTERVAL_SECONDS
            )
            pause(config.FETCH_INTERVAL_SECONDS)
    finally:
        conn.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and run the collector."""
    parser = argparse.ArgumentParser(
        description="Collect live tourism signals into SQLite"
    )
    parser.add_argument(
        "--loops",
        type=int,
        default=None,
        help="number of collection loops to run (default: forever)",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="print stored health as JSON and exit",
    )
    args = parser.parse_args(argv)

    log = setup_logging()
    if args.health:
        print(json.dumps(get_health().as_dict(), indent=2))
        return 0

    try:
        run(args.loops)
    except KeyboardInterrupt:
        log.info("stopped by user")
    except Exception:
        log.exception("fatal error in the collector loop")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
