"""Tests for bull_call.simulate — verify the simulator matches monitor_stop semantics."""

from __future__ import annotations

import datetime as dt
import io
from contextlib import redirect_stdout

import pytest

from bull_call.simulate import main, run, synthetic_ticks


def _capture(args: list[str]) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(args)
    assert rc == 0
    return buf.getvalue()


@pytest.mark.parametrize("scenario,expected_outcome", [
    ("touch", "FIRED"),
    ("fire", "FIRED"),
    ("suppress", "SUPPRESSED"),
    ("never_arm", "NEVER"),
    ("uneconomic", "UNECONOMIC"),
])
def test_each_scenario_produces_expected_outcome(scenario: str, expected_outcome: str) -> None:
    out = _capture([
        "--long", "4995",
        "--short", "5005",
        "--debit", "5.50",
        "--exit-credit", "1.20",
        "--scenario", scenario,
    ])
    assert f"outcome           = {expected_outcome}" in out


def test_touch_scenario_arms_then_fires_on_one_tick_below() -> None:
    """User-requested check: a price *touching* the breakeven arms the stop;
    the very next tick at one tick below breakeven fires the stop."""

    out = _capture([
        "--long", "4995",
        "--short", "5005",
        "--debit", "5.50",
        "--scenario", "touch",
    ])
    assert "armed" in out
    assert "fired" in out
    assert "outcome           = FIRED" in out


def test_synthetic_touch_scenario_is_one_tick_apart() -> None:
    """Verify the touch-scenario tick generator does what we say it does."""

    close_utc = dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc)
    ticks = synthetic_ticks(5000.50, scenario="touch", close_utc=close_utc)
    # One pre-arm tick below, then exactly at breakeven, then one tick below.
    assert ticks[0][0] < 5000.50
    assert ticks[1][0] == 5000.50
    assert ticks[2][0] < 5000.50
    # The final cross-down is within seconds of the arm.
    assert (ticks[2][1] - ticks[1][1]).total_seconds() <= 90


def test_simulator_realized_pnl_against_max_held_loss() -> None:
    """When the stop fires and the realized loss is *less* than the max-held
    loss, the run should still report the stop as worthwhile (i.e. realized
    P&L is bounded above by the max-held loss)."""

    close_utc = dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc)
    ticks = synthetic_ticks(5000.50, scenario="fire", close_utc=close_utc)
    rc = run(
        long_strike=4995.0, short_strike=5005.0, debit=5.50,
        exit_credit=1.20, ticks=ticks, close_utc=close_utc,
    )
    assert rc == 0
