#!/usr/bin/env python3
"""Live tourism signal fetcher.

Every 120 seconds, for each of the six cities below, this script:

* counts aircraft inside a +/-0.5 degree box around the city (OpenSky Network),
* reads current temperature / precipitation / weather code (Open-Meteo),
* reads the USD -> EUR rate once per loop (Frankfurter),
* appends one row per city to the SQLite table ``live_signals``.

Failures are handled per city: the error is logged, the most recent good value
for that city is reused (or NULL if none exists yet), and the loop continues.
The script never exits because of an API error.

Usage (Linux):
    source .venv/bin/activate
    python3 fetcher.py            # runs forever, one loop every 120 s
    python3 fetcher.py --loops 2  # runs exactly two loops (handy for tests)
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
import traceback
from datetime import datetime, timezone

import requests

DB_PATH = "tourism.db"
POLL_SECONDS = 120
HTTP_TIMEOUT = 25

OPEN_SKY_URL = "https://opensky-network.org/api/states/all"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
FX_URL = "https://api.frankfurter.app/latest?from=USD&to=EUR,INR,JPY,GBP,SGD,AED"

# Exactly the six cities required by the brief (lat, lon).
CITIES = [
    {"city": "Paris", "lat": 48.85, "lon": 2.35},
    {"city": "London", "lat": 51.50, "lon": -0.12},
    {"city": "New York", "lat": 40.71, "lon": -74.00},
    {"city": "Dubai", "lat": 25.20, "lon": 55.27},
    {"city": "Singapore", "lat": 1.35, "lon": 103.82},
    {"city": "Tokyo", "lat": 35.68, "lon": 139.69},
]

COLUMNS = (
    "timestamp",
    "city",
    "planes_in_box",
    "temp_c",
    "precipitation",
    "weather_code",
    "eur_rate",
)


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS live_signals (
            timestamp      TEXT,
            city           TEXT,
            planes_in_box  INTEGER,
            temp_c         REAL,
            precipitation  REAL,
            weather_code   INTEGER,
            eur_rate       REAL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_live_city_ts "
        "ON live_signals (city, timestamp)"
    )
    conn.commit()
    return conn


def last_good_row(conn: sqlite3.Connection, city: str) -> dict:
    """Most recent row for the city, used as fallback when an API fails."""
    row = conn.execute(
        "SELECT planes_in_box, temp_c, precipitation, weather_code "
        "FROM live_signals WHERE city = ? ORDER BY timestamp DESC LIMIT 1",
        (city,),
    ).fetchone()
    if not row:
        return {}
    return {
        "planes_in_box": row[0],
        "temp_c": row[1],
        "precipitation": row[2],
        "weather_code": row[3],
    }


def last_eur_rate(conn: sqlite3.Connection) -> float | None:
    row = conn.execute(
        "SELECT eur_rate FROM live_signals "
        "WHERE eur_rate IS NOT NULL ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


# --------------------------------------------------------------------------- #
# APIs
# --------------------------------------------------------------------------- #
def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_planes(lat: float, lon: float) -> int:
    """Number of aircraft states inside lat/lon +/- 0.5 degrees."""
    params = {
        "lamin": round(lat - 0.5, 4),
        "lamax": round(lat + 0.5, 4),
        "lomin": round(lon - 0.5, 4),
        "lomax": round(lon + 0.5, 4),
    }
    response = requests.get(OPEN_SKY_URL, params=params, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    states = payload.get("states") or []
    return len(states)


def fetch_weather(lat: float, lon: float) -> dict:
    """Current weather for one city from Open-Meteo."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,precipitation,weather_code",
    }
    response = requests.get(WEATHER_URL, params=params, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    current = (response.json() or {}).get("current") or {}
    return {
        "temp_c": current.get("temperature_2m"),
        "precipitation": current.get("precipitation"),
        "weather_code": current.get("weather_code"),
    }


def fetch_fx() -> float | None:
    """USD -> EUR rate from Frankfurter (requested once per loop).

    The endpoint is asked for EUR/INR/JPY/GBP/SGD/AED but Frankfurter follows
    the ECB reference set, which has no AED; only the EUR rate is stored.
    """
    response = requests.get(FX_URL, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    rates = (response.json() or {}).get("rates") or {}
    eur = rates.get("EUR")
    return float(eur) if eur is not None else None


# --------------------------------------------------------------------------- #
# Loop
# --------------------------------------------------------------------------- #
def collect_city(conn: sqlite3.Connection, city: dict) -> dict:
    """Fetch one city's signals, falling back to its last good row."""
    name, lat, lon = city["city"], city["lat"], city["lon"]
    fallback = last_good_row(conn, name)
    signals = {
        "planes_in_box": None,
        "temp_c": None,
        "precipitation": None,
        "weather_code": None,
    }

    try:
        signals["planes_in_box"] = fetch_planes(lat, lon)
    except Exception as exc:  # noqa: BLE001 - one failing API must not stop the loop
        print(f"[error] OpenSky {name}: {exc}", file=sys.stderr)
        signals["planes_in_box"] = fallback.get("planes_in_box")

    try:
        signals.update(fetch_weather(lat, lon))
    except Exception as exc:  # noqa: BLE001
        print(f"[error] Open-Meteo {name}: {exc}", file=sys.stderr)
        for key in ("temp_c", "precipitation", "weather_code"):
            signals[key] = fallback.get(key)

    return signals


def run(loops: int | None = None) -> None:
    conn = connect()
    iteration = 0

    try:
        while loops is None or iteration < loops:
            iteration += 1

            eur_rate = None
            try:
                eur_rate = fetch_fx()
            except Exception as exc:  # noqa: BLE001
                print(f"[error] Frankfurter: {exc}", file=sys.stderr)
                eur_rate = last_eur_rate(conn)
            if eur_rate is None:
                eur_rate = last_eur_rate(conn)

            for city in CITIES:
                timestamp = utc_now()
                signals = collect_city(conn, city)
                conn.execute(
                    f"INSERT INTO live_signals ({', '.join(COLUMNS)}) "
                    f"VALUES ({', '.join('?' for _ in COLUMNS)})",
                    (
                        timestamp,
                        city["city"],
                        signals["planes_in_box"],
                        signals["temp_c"],
                        signals["precipitation"],
                        signals["weather_code"],
                        eur_rate,
                    ),
                )
                conn.commit()
                print(f"fetched [{city['city']}] at [{timestamp}]", flush=True)

            if loops is not None and iteration >= loops:
                break
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\nstopped by user")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect live tourism signals")
    parser.add_argument(
        "--loops",
        type=int,
        default=None,
        help="number of collection loops to run (default: forever)",
    )
    args = parser.parse_args()
    try:
        run(args.loops)
    except Exception:  # noqa: BLE001 - keep a trace but never die silently
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
