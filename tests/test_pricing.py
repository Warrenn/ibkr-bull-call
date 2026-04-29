"""Tests for bull_call.pricing."""

from __future__ import annotations

import datetime as dt
import math

import pytest

from bull_call.pricing import (
    pop_bs,
    seconds_to_session_close,
    years_to_session_close,
)


def test_at_breakeven_with_no_drift_is_half() -> None:
    # spot == breakeven, r=0, sigma>0 → d2 = -sigma*sqrt(T)/2 ≈ slightly < 0.5
    pop = pop_bs(spot=5000.0, breakeven=5000.0, iv=0.20, time_years=1 / 252, r=0.0)
    assert 0.45 <= pop <= 0.50


def test_pop_monotone_decreasing_in_breakeven() -> None:
    pops = [
        pop_bs(spot=5000.0, breakeven=k, iv=0.20, time_years=1 / 365, r=0.05)
        for k in (4990.0, 5000.0, 5010.0, 5020.0)
    ]
    assert pops == sorted(pops, reverse=True)


def test_pop_zero_time_above_breakeven_is_one() -> None:
    assert pop_bs(spot=5010.0, breakeven=5000.0, iv=0.20, time_years=0.0, r=0.05) == 1.0


def test_pop_zero_time_below_breakeven_is_zero() -> None:
    assert pop_bs(spot=4990.0, breakeven=5000.0, iv=0.20, time_years=0.0, r=0.05) == 0.0


def test_pop_zero_iv_above_drift_target_is_one() -> None:
    # zero vol with positive drift: deterministic forward S*exp(rT) > K → POP = 1
    pop = pop_bs(spot=5000.0, breakeven=5001.0, iv=0.0, time_years=1.0, r=0.05)
    assert pop == 1.0


def test_pop_zero_iv_below_drift_target_is_zero() -> None:
    # zero vol with positive drift: S*exp(rT) < K with K well above forward → POP = 0
    pop = pop_bs(spot=5000.0, breakeven=6000.0, iv=0.0, time_years=1.0, r=0.05)
    assert pop == 0.0


def test_pop_realistic_0dte_above_spot() -> None:
    # 0DTE, breakeven 5 points above spot (~0.1% OTM), 18% IV, 4 hours left
    T = 4 / 24 / 365.25
    pop = pop_bs(spot=5000.0, breakeven=5005.0, iv=0.18, time_years=T, r=0.05)
    # plenty of room — POP should be solidly under 0.5 but not vanishing
    assert 0.05 <= pop <= 0.45


def test_negative_iv_raises() -> None:
    with pytest.raises(ValueError, match="iv"):
        pop_bs(spot=5000.0, breakeven=5005.0, iv=-0.01, time_years=0.01, r=0.05)


def test_negative_time_raises() -> None:
    with pytest.raises(ValueError, match="time_years"):
        pop_bs(spot=5000.0, breakeven=5005.0, iv=0.20, time_years=-0.001, r=0.05)


def test_seconds_to_session_close_basic() -> None:
    # 09:30 ET = 14:30 UTC during EDT (March-Nov); same-day 16:00 ET = 21:00 UTC → 6.5h = 23400s
    now = dt.datetime(2026, 6, 1, 13, 30, tzinfo=dt.timezone.utc)  # 09:30 EDT
    close = dt.datetime(2026, 6, 1, 20, 0, tzinfo=dt.timezone.utc)  # 16:00 EDT
    assert seconds_to_session_close(now, close) == pytest.approx(6.5 * 3600)


def test_seconds_to_session_close_clamped_to_zero() -> None:
    now = dt.datetime(2026, 6, 1, 21, 0, tzinfo=dt.timezone.utc)
    close = dt.datetime(2026, 6, 1, 20, 0, tzinfo=dt.timezone.utc)
    assert seconds_to_session_close(now, close) == 0.0


def test_years_to_session_close_consistency() -> None:
    now = dt.datetime(2026, 6, 1, 13, 30, tzinfo=dt.timezone.utc)
    close = dt.datetime(2026, 6, 1, 20, 0, tzinfo=dt.timezone.utc)
    secs = seconds_to_session_close(now, close)
    yrs = years_to_session_close(now, close)
    assert yrs == pytest.approx(secs / (365.25 * 86400))
    assert math.isfinite(yrs)
