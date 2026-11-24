"""Unit tests for configuration defaults and environment overrides."""

from __future__ import annotations

import importlib

import pytest

import config

ENV_KEYS = (
    "FETCH_INTERVAL_SECONDS",
    "REQUEST_TIMEOUT",
    "RETRY_COUNT",
    "DB_PATH",
    "CSV_PATH",
    "LOG_FILE",
    "LOG_LEVEL",
    "OPENSKY_URL",
    "OPENMETEO_URL",
    "FRANKFURTER_URL",
    "USER_AGENT",
    "DASHBOARD_REFRESH_SECONDS",
    "LIVE_MAX_AGE_SECONDS",
    "STALE_MAX_AGE_SECONDS",
    "LIVE_MODE",
    "ON_DEMAND_TTL",
)


@pytest.fixture
def fresh_config(monkeypatch):
    """Reload config with a controlled environment, then restore it."""

    def load(**overrides: object):
        for key in ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        for key, value in overrides.items():
            monkeypatch.setenv(key, str(value))
        return importlib.reload(config)

    yield load
    monkeypatch.undo()
    importlib.reload(config)


def test_defaults_when_no_env_vars_are_set(fresh_config) -> None:
    cfg = fresh_config()
    assert cfg.FETCH_INTERVAL_SECONDS == 120
    assert cfg.REQUEST_TIMEOUT == 10
    assert cfg.RETRY_COUNT == 3
    assert cfg.RETRY_BACKOFF_SECONDS == (2.0, 4.0, 8.0)
    assert cfg.USER_AGENT == "tourism-project/1.0"
    assert cfg.DB_PATH.endswith("tourism.db")
    assert cfg.CSV_PATH.endswith("data/clean_tourism.csv")
    assert cfg.LOG_FILE.endswith("logs/app.log")
    assert cfg.LOG_LEVEL == "INFO"
    assert cfg.OPENSKY_URL == "https://opensky-network.org/api/states/all"
    assert cfg.OPENMETEO_URL == "https://api.open-meteo.com/v1/forecast"
    assert cfg.FRANKFURTER_URL == (
        "https://api.frankfurter.app/latest?from=USD&to=EUR,INR,JPY,GBP,SGD,AED"
    )
    assert cfg.DASHBOARD_REFRESH_SECONDS == 60
    assert cfg.LIVE_MAX_AGE_SECONDS == 300
    assert cfg.STALE_MAX_AGE_SECONDS == 1800
    # "ondemand" behaves like "db" when a collector is running, so it is safe as
    # the default: the live cards fill in on a host with no background collector.
    assert cfg.LIVE_MODE == "ondemand"
    assert cfg.on_demand() is True


def test_city_list_is_fixed(fresh_config) -> None:
    cfg = fresh_config()
    assert [c["city"] for c in cfg.CITIES] == [
        "Paris",
        "London",
        "New York",
        "Dubai",
        "Singapore",
        "Tokyo",
    ]
    assert cfg.CITY_TO_COUNTRY["Tokyo"] == "Japan"
    assert cfg.CITIES[0]["lat"] == 48.85
    assert cfg.CITIES[0]["lon"] == 2.35
    assert cfg.BOX_HALF_SIZE == 0.5


def test_env_vars_override_defaults(fresh_config) -> None:
    cfg = fresh_config(
        FETCH_INTERVAL_SECONDS=45,
        REQUEST_TIMEOUT=3,
        DB_PATH="/tmp/other.db",
        LOG_LEVEL="debug",
    )
    assert cfg.FETCH_INTERVAL_SECONDS == 45
    assert cfg.REQUEST_TIMEOUT == 3
    assert cfg.DB_PATH == "/tmp/other.db"
    assert cfg.LOG_LEVEL == "DEBUG"


def test_invalid_numbers_fall_back_to_defaults(fresh_config) -> None:
    cfg = fresh_config(REQUEST_TIMEOUT="not-a-number", FETCH_INTERVAL_SECONDS="")
    assert cfg.REQUEST_TIMEOUT == 10
    assert cfg.FETCH_INTERVAL_SECONDS == 120
