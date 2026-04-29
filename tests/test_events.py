"""Tests for the structured-JSON event logger."""

from __future__ import annotations

import json
import logging

import pytest

from bull_call import events


def test_emits_one_json_line_per_event(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="bull_call.events"):
        events.emit("spread_opened", spread_id="2026-04-29#SPX", debit=4.55)

    records = [r for r in caplog.records if r.name == "bull_call.events"]
    assert len(records) == 1
    payload = json.loads(records[0].getMessage())
    assert payload["event"] == "spread_opened"
    assert payload["spread_id"] == "2026-04-29#SPX"
    assert payload["debit"] == 4.55
    assert "ts" in payload


def test_omits_none_valued_fields(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="bull_call.events"):
        events.emit("spread_opened", spread_id="A", pnl=None, debit=4.55)

    payload = json.loads(caplog.records[-1].getMessage())
    assert "pnl" not in payload
    assert payload["debit"] == 4.55


def test_serializes_non_json_natives_via_str(caplog: pytest.LogCaptureFixture) -> None:
    """e.g. floats with extreme precision, datetime objects, etc."""

    import datetime as dt

    with caplog.at_level(logging.INFO, logger="bull_call.events"):
        events.emit(
            "stop_armed",
            spread_id="A",
            opened_at=dt.datetime(2026, 4, 29, 14, 30, tzinfo=dt.timezone.utc),
        )

    payload = json.loads(caplog.records[-1].getMessage())
    # `default=str` on the datetime gives the same calendar/time but with a
    # space separator (Python's default str() for datetimes).
    assert payload["opened_at"].startswith("2026-04-29")
    assert "14:30:00" in payload["opened_at"]
