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


# ---------- ticks_from_csv ---------------------------------------------------


def test_ticks_from_csv_parses_two_column_csv(tmp_path: pytest.TempPathFactory) -> None:
    """Happy path: ISO timestamps + spot prices in two columns, no header."""

    from pathlib import Path

    from bull_call.simulate import ticks_from_csv

    p = Path(tmp_path) / "ticks.csv"
    p.write_text(
        "2026-04-29T15:00:00+00:00,5001.0\n"
        "2026-04-29T15:01:00+00:00,5002.5\n"
        "2026-04-29T15:02:00+00:00,4999.5\n"
    )

    ticks = ticks_from_csv(p)
    assert len(ticks) == 3
    assert ticks[0][0] == pytest.approx(5001.0)
    assert ticks[2][0] == pytest.approx(4999.5)


def test_ticks_from_csv_skips_header_row(tmp_path: pytest.TempPathFactory) -> None:
    """If the first row's spot column isn't a number, treat as a header
    and skip cleanly (don't raise)."""

    from pathlib import Path

    from bull_call.simulate import ticks_from_csv

    p = Path(tmp_path) / "ticks.csv"
    p.write_text(
        "timestamp_utc,spot\n"
        "2026-04-29T15:00:00+00:00,5001.0\n"
    )
    ticks = ticks_from_csv(p)
    assert len(ticks) == 1
    assert ticks[0][0] == pytest.approx(5001.0)


def test_ticks_from_csv_attaches_utc_to_naive_timestamps(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """A timestamp without tzinfo gets ``tzinfo=UTC`` attached."""

    from pathlib import Path

    from bull_call.simulate import ticks_from_csv

    p = Path(tmp_path) / "ticks.csv"
    p.write_text("2026-04-29T15:00:00,5001.0\n")
    ticks = ticks_from_csv(p)
    assert ticks[0][1].tzinfo == dt.timezone.utc


def test_ticks_from_csv_skips_invalid_rows(tmp_path: pytest.TempPathFactory) -> None:
    """Empty rows, single-column rows, and rows with bad timestamps are
    silently dropped — only well-formed rows make it through."""

    from pathlib import Path

    from bull_call.simulate import ticks_from_csv

    p = Path(tmp_path) / "ticks.csv"
    p.write_text(
        "\n"                                           # empty row
        "lone-column\n"                                # only one column
        "2026-04-29T15:00:00+00:00,5001.0\n"           # good
        "not-a-timestamp,5002.0\n"                     # bad timestamp
        "2026-04-29T15:01:00+00:00,5002.5\n"           # good
    )
    ticks = ticks_from_csv(p)
    assert len(ticks) == 2


def test_ticks_from_csv_raises_when_no_usable_rows(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """All rows malformed → ValueError, so the simulator doesn't run on
    an empty list and silently report nonsense."""

    from pathlib import Path

    from bull_call.simulate import ticks_from_csv

    p = Path(tmp_path) / "ticks.csv"
    p.write_text(
        "header,row\n"     # garbage row
        "more,bad-data\n"  # garbage row
    )
    with pytest.raises(ValueError, match="no usable rows"):
        ticks_from_csv(p)


def test_ticks_from_csv_sorts_by_timestamp(tmp_path: pytest.TempPathFactory) -> None:
    """Out-of-order CSV rows get sorted by timestamp before return —
    callers always see chronological ticks."""

    from pathlib import Path

    from bull_call.simulate import ticks_from_csv

    p = Path(tmp_path) / "ticks.csv"
    p.write_text(
        "2026-04-29T15:02:00+00:00,5002.0\n"
        "2026-04-29T15:00:00+00:00,5000.0\n"
        "2026-04-29T15:01:00+00:00,5001.0\n"
    )
    ticks = ticks_from_csv(p)
    assert [t[0] for t in ticks] == [5000.0, 5001.0, 5002.0]


# ---------- synthetic_ticks error path --------------------------------------


def test_synthetic_ticks_raises_for_unknown_scenario() -> None:
    """Defensive: an unrecognised scenario name should fail loudly so
    typos in CLI args surface immediately, not as silent empty ticks."""

    from bull_call.simulate import synthetic_ticks

    close_utc = dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc)
    with pytest.raises(ValueError, match="unknown scenario"):
        synthetic_ticks(5000.0, scenario="nope", close_utc=close_utc)
