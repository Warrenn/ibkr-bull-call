"""Tests for bull_call.calendar."""

from __future__ import annotations

import datetime as dt

import pytest

from bull_call.calendar import (
    SessionTimes,
    is_half_day,
    is_trading_day,
    next_session_open_utc,
    session_times,
)


# 2026 calendar references:
#   2026-04-29 (Wed) — regular trading day
#   2026-05-02 (Sat) — weekend
#   2026-12-25 (Fri) — Christmas, market closed
#   2026-11-26 (Thu) — Thanksgiving, market closed
#   2026-11-27 (Fri) — half-day (1 pm ET close)
#   2026-12-24 (Thu) — half-day (1 pm ET close)


def test_is_trading_day_regular() -> None:
    assert is_trading_day(dt.date(2026, 4, 29)) is True


def test_is_trading_day_weekend() -> None:
    assert is_trading_day(dt.date(2026, 5, 2)) is False
    assert is_trading_day(dt.date(2026, 5, 3)) is False


def test_is_trading_day_christmas() -> None:
    assert is_trading_day(dt.date(2026, 12, 25)) is False


def test_is_trading_day_thanksgiving() -> None:
    assert is_trading_day(dt.date(2026, 11, 26)) is False


def test_session_times_regular_day_is_4pm_eastern_close() -> None:
    s = session_times(dt.date(2026, 4, 29))
    assert isinstance(s, SessionTimes)
    # 2026-04-29 is in EDT (UTC-4); 4 pm ET = 20:00 UTC
    assert s.close_utc == dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc)
    # 9:30 am ET = 13:30 UTC during EDT
    assert s.open_utc == dt.datetime(2026, 4, 29, 13, 30, tzinfo=dt.timezone.utc)
    assert s.is_half_day is False


def test_session_times_winter_day_uses_est() -> None:
    s = session_times(dt.date(2026, 1, 5))  # Monday in EST (UTC-5)
    assert s is not None
    assert s.close_utc == dt.datetime(2026, 1, 5, 21, 0, tzinfo=dt.timezone.utc)
    assert s.open_utc == dt.datetime(2026, 1, 5, 14, 30, tzinfo=dt.timezone.utc)


def test_session_times_non_trading_day_is_none() -> None:
    assert session_times(dt.date(2026, 12, 25)) is None
    assert session_times(dt.date(2026, 5, 2)) is None


def test_half_day_detected() -> None:
    # Day after Thanksgiving 2026 is 11-27, expected 1 pm ET close.
    assert is_half_day(dt.date(2026, 11, 27)) is True
    s = session_times(dt.date(2026, 11, 27))
    assert s is not None
    assert s.is_half_day is True
    # 1 pm EST = 18:00 UTC (Nov 27 is in EST)
    assert s.close_utc == dt.datetime(2026, 11, 27, 18, 0, tzinfo=dt.timezone.utc)


def test_christmas_eve_half_day() -> None:
    assert is_half_day(dt.date(2026, 12, 24)) is True


def test_regular_day_not_half() -> None:
    assert is_half_day(dt.date(2026, 4, 29)) is False


def test_next_session_open_skips_weekend() -> None:
    # Friday 2026-04-24 close 16:00 ET -> next is Monday 2026-04-27 09:30 ET
    fri_close = dt.datetime(2026, 4, 24, 21, 0, tzinfo=dt.timezone.utc)  # past close
    nxt = next_session_open_utc(fri_close)
    assert nxt == dt.datetime(2026, 4, 27, 13, 30, tzinfo=dt.timezone.utc)


def test_next_session_open_same_day_if_before_open() -> None:
    pre_open = dt.datetime(2026, 4, 29, 10, 0, tzinfo=dt.timezone.utc)  # 06:00 ET
    nxt = next_session_open_utc(pre_open)
    assert nxt == dt.datetime(2026, 4, 29, 13, 30, tzinfo=dt.timezone.utc)


def test_next_session_open_during_session_returns_next_day() -> None:
    mid_session = dt.datetime(2026, 4, 29, 18, 0, tzinfo=dt.timezone.utc)  # 14:00 ET
    nxt = next_session_open_utc(mid_session)
    assert nxt.date() == dt.date(2026, 4, 30)
    assert nxt == dt.datetime(2026, 4, 30, 13, 30, tzinfo=dt.timezone.utc)


def test_next_session_open_skips_holiday() -> None:
    # 2026-12-25 is Christmas; previous close on 12-24 is half-day 1pm ET (18:00 UTC).
    after_christmas_eve = dt.datetime(2026, 12, 24, 19, 0, tzinfo=dt.timezone.utc)
    nxt = next_session_open_utc(after_christmas_eve)
    # next session after 12-24 close is 12-28 Monday (since 12-25 is closed and 12-26/27 weekend)
    assert nxt == dt.datetime(2026, 12, 28, 14, 30, tzinfo=dt.timezone.utc)


@pytest.mark.parametrize("d", [
    dt.date(2026, 1, 19),  # MLK
    dt.date(2026, 2, 16),  # Presidents
    dt.date(2026, 4, 3),   # Good Friday
    dt.date(2026, 5, 25),  # Memorial Day
    dt.date(2026, 7, 3),   # Independence Day observed
    dt.date(2026, 9, 7),   # Labor Day
])
def test_known_holidays_are_closed(d: dt.date) -> None:
    assert is_trading_day(d) is False
