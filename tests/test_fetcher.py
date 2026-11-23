"""Unit tests for the fetcher's HTTP, parsing, storage and health logic."""

from __future__ import annotations

import logging

import pytest
import requests

import fetcher

CITY = {"city": "Paris", "country": "France", "lat": 48.85, "lon": 2.35}


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(
        self,
        status_code: int = 200,
        payload: object = None,
        *,
        json_error: bool = False,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self) -> object:
        if self._json_error:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class FakeSession:
    """Session double that replays a list of responses/exceptions."""

    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class RecordingHandler(logging.Handler):
    """Collect log records so assertions do not depend on root logging."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


# --------------------------------------------------------------------------- #
# OpenSky parsing: 0, 1 and 5 states
# --------------------------------------------------------------------------- #
def test_count_states_zero_one_and_five() -> None:
    assert fetcher.count_states({"time": 1, "states": []}) == 0
    assert fetcher.count_states({"time": 1, "states": [["abc123"]]}) == 1
    assert fetcher.count_states({"time": 1, "states": [["abc123"]] * 5}) == 5


def test_count_states_handles_null_states() -> None:
    """OpenSky returns ``states: null`` when the box is empty."""
    assert fetcher.count_states({"time": 1, "states": None}) == 0


def test_count_states_rejects_payload_without_states() -> None:
    with pytest.raises(fetcher.ResponseValidationError):
        fetcher.count_states({"time": 1})


def test_fetch_planes_uses_half_degree_box() -> None:
    session = FakeSession([FakeResponse(payload={"states": [["a"], ["b"]]})])
    assert fetcher.fetch_planes(session, CITY) == 2
    assert session.calls[0]["params"] == {
        "lamin": 48.35,
        "lamax": 49.35,
        "lomin": 1.85,
        "lomax": 2.85,
    }


# --------------------------------------------------------------------------- #
# Retries
# --------------------------------------------------------------------------- #
def test_retry_on_timeout_then_success() -> None:
    sleeps: list[float] = []
    session = FakeSession(
        [
            requests.Timeout("slow"),
            requests.Timeout("slow"),
            FakeResponse(payload={"states": []}),
        ]
    )
    payload = fetcher.request_json(
        session, "https://example.invalid/states/all", sleep=sleeps.append
    )
    assert payload == {"states": []}
    assert len(session.calls) == 3
    assert sleeps == [2.0, 4.0]


def test_retry_gives_up_after_three_retries() -> None:
    sleeps: list[float] = []
    session = FakeSession([requests.Timeout("slow")] * 4)
    with pytest.raises(fetcher.FetcherError):
        fetcher.request_json(
            session, "https://example.invalid/states/all", sleep=sleeps.append
        )
    assert len(session.calls) == 4
    assert sleeps == [2.0, 4.0, 8.0]


def test_retries_429_and_5xx_but_not_other_4xx() -> None:
    retried = FakeSession(
        [FakeResponse(status_code=503), FakeResponse(payload={"ok": True})]
    )
    assert fetcher.request_json(retried, "u", sleep=lambda _s: None) == {"ok": True}
    assert len(retried.calls) == 2

    rate_limited = FakeSession(
        [FakeResponse(status_code=429), FakeResponse(payload={"ok": True})]
    )
    assert fetcher.request_json(rate_limited, "u", sleep=lambda _s: None) == {
        "ok": True
    }

    not_found = FakeSession([FakeResponse(status_code=404)])
    with pytest.raises(requests.HTTPError):
        fetcher.request_json(not_found, "u", sleep=lambda _s: None)
    assert len(not_found.calls) == 1


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def test_malformed_json_raises_validation_error() -> None:
    session = FakeSession([FakeResponse(payload=None, json_error=True)])
    with pytest.raises(fetcher.ResponseValidationError):
        fetcher.request_json(session, "https://example.invalid", sleep=lambda _s: None)


def test_non_object_json_raises_validation_error() -> None:
    session = FakeSession([FakeResponse(payload=[1, 2, 3])])
    with pytest.raises(fetcher.ResponseValidationError):
        fetcher.request_json(session, "https://example.invalid", sleep=lambda _s: None)


def test_weather_without_current_key_returns_none_values() -> None:
    assert fetcher.parse_current({}) == {
        "temp_c": None,
        "precipitation": None,
        "weather_code": None,
    }
    assert fetcher.parse_current({"current": {"temperature_2m": 4.5}}) == {
        "temp_c": 4.5,
        "precipitation": None,
        "weather_code": None,
    }


def test_missing_states_key_skips_planes_without_crashing(tmp_path) -> None:
    conn = fetcher.connect(tmp_path / "test.db")
    handler = RecordingHandler()
    logger = fetcher.get_logger()
    logger.addHandler(handler)
    try:
        session = FakeSession(
            [
                FakeResponse(payload={"time": 1}),  # no "states" key
                FakeResponse(
                    payload={
                        "current": {
                            "temperature_2m": 4.5,
                            "precipitation": 0.0,
                            "weather_code": 3,
                        }
                    }
                ),
            ]
        )
        signals, ok = fetcher.collect_city(session, conn, CITY)
    finally:
        logger.removeHandler(handler)
        conn.close()

    assert ok is False
    assert signals["planes_in_box"] is None
    assert signals["temp_c"] == 4.5
    warnings = [
        record.getMessage()
        for record in handler.records
        if record.levelno == logging.WARNING
    ]
    assert any("states" in message for message in warnings)


# --------------------------------------------------------------------------- #
# Storage and health
# --------------------------------------------------------------------------- #
def test_batch_insert_and_health(tmp_path) -> None:
    conn = fetcher.connect(tmp_path / "health.db")
    try:
        stored = fetcher.insert_rows(
            conn,
            [
                ("2026-01-01T00:00:00Z", "Paris", 1, 2.0, 0.0, 3, 0.9),
                ("2026-01-01T00:00:00Z", "Tokyo", 5, 6.0, 0.2, 61, 0.9),
            ],
        )
        health = fetcher.get_health(conn)
    finally:
        conn.close()

    assert stored == 2
    assert health.rows == 2
    assert health.cities == 2
    assert health.last_fetch == "2026-01-01T00:00:00Z"


def test_database_uses_wal_mode(tmp_path) -> None:
    conn = fetcher.connect(tmp_path / "wal.db")
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert mode.lower() == "wal"
