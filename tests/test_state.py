"""Tests for bull_call.state."""

from __future__ import annotations

from pathlib import Path

import pytest

from bull_call.state import (
    DuplicateSpreadError,
    SpreadRecord,
    StopEvent,
    Store,
)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "state.db")


def test_record_open_and_lookup(store: Store) -> None:
    sid = store.record_open(
        date="2026-04-29",
        symbol="SPX",
        long_strike=4995.0,
        short_strike=5005.0,
        debit=5.50,
        opened_at="2026-04-29T14:30:00+00:00",
    )
    assert isinstance(sid, int) and sid > 0

    rec = store.get_spread(sid)
    assert rec == SpreadRecord(
        id=sid,
        date="2026-04-29",
        symbol="SPX",
        long_strike=4995.0,
        short_strike=5005.0,
        debit=5.50,
        status="OPEN",
        opened_at="2026-04-29T14:30:00+00:00",
        closed_at=None,
        exit_kind=None,
        settle_value=None,
        pnl=None,
    )


def test_today_already_opened(store: Store) -> None:
    assert store.today_already_opened("2026-04-29", "SPX") is False
    store.record_open(
        date="2026-04-29",
        symbol="SPX",
        long_strike=4995.0,
        short_strike=5005.0,
        debit=5.5,
        opened_at="2026-04-29T14:30:00+00:00",
    )
    assert store.today_already_opened("2026-04-29", "SPX") is True
    assert store.today_already_opened("2026-04-29", "QQQ") is False
    assert store.today_already_opened("2026-04-30", "SPX") is False


def test_double_open_same_date_symbol_raises(store: Store) -> None:
    store.record_open(
        date="2026-04-29", symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.5,
        opened_at="2026-04-29T14:30:00+00:00",
    )
    with pytest.raises(DuplicateSpreadError):
        store.record_open(
            date="2026-04-29", symbol="SPX",
            long_strike=5000.0, short_strike=5010.0, debit=5.0,
            opened_at="2026-04-29T15:30:00+00:00",
        )


def test_stop_journal_round_trip(store: Store) -> None:
    sid = store.record_open(
        date="2026-04-29", symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.5,
        opened_at="2026-04-29T14:30:00+00:00",
    )
    store.record_stop_event(spread_id=sid, ts="2026-04-29T15:00:00+00:00",
                            event="armed", spot=5001.0, breakeven=5000.5)
    store.record_stop_event(spread_id=sid, ts="2026-04-29T18:00:00+00:00",
                            event="fired", spot=5000.0, breakeven=5000.5)

    events = store.stop_events(sid)
    assert events == [
        StopEvent(spread_id=sid, ts="2026-04-29T15:00:00+00:00",
                  event="armed", spot=5001.0, breakeven=5000.5),
        StopEvent(spread_id=sid, ts="2026-04-29T18:00:00+00:00",
                  event="fired", spot=5000.0, breakeven=5000.5),
    ]


def test_record_close_marks_stopped(store: Store) -> None:
    sid = store.record_open(
        date="2026-04-29", symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.5,
        opened_at="2026-04-29T14:30:00+00:00",
    )
    store.record_close(
        spread_id=sid,
        closed_at="2026-04-29T18:30:00+00:00",
        exit_kind="STOP",
        pnl=-450.0,
    )
    rec = store.get_spread(sid)
    assert rec.status == "STOPPED"
    assert rec.exit_kind == "STOP"
    assert rec.closed_at == "2026-04-29T18:30:00+00:00"
    assert rec.pnl == pytest.approx(-450.0)


def test_record_settlement_marks_settled(store: Store) -> None:
    sid = store.record_open(
        date="2026-04-29", symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.5,
        opened_at="2026-04-29T14:30:00+00:00",
    )
    store.record_settlement(
        spread_id=sid,
        closed_at="2026-04-29T20:00:00+00:00",
        settle_value=5008.21,
        pnl=450.0,
    )
    rec = store.get_spread(sid)
    assert rec.status == "SETTLED"
    assert rec.exit_kind == "SETTLE"
    assert rec.settle_value == pytest.approx(5008.21)
    assert rec.pnl == pytest.approx(450.0)


def test_load_open_spreads_filters_by_status_and_date(store: Store) -> None:
    s1 = store.record_open(
        date="2026-04-29", symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.5,
        opened_at="2026-04-29T14:30:00+00:00",
    )
    s2 = store.record_open(
        date="2026-04-29", symbol="QQQ",
        long_strike=400.0, short_strike=405.0, debit=2.0,
        opened_at="2026-04-29T14:30:00+00:00",
    )
    # Old day; should be excluded.
    store.record_open(
        date="2026-04-28", symbol="SPX",
        long_strike=4990.0, short_strike=5000.0, debit=4.0,
        opened_at="2026-04-28T14:30:00+00:00",
    )
    # Close one of today's; only the still-OPEN should come back.
    store.record_close(spread_id=s2, closed_at="2026-04-29T16:00:00+00:00",
                       exit_kind="STOP", pnl=-200.0)

    open_today = store.load_open_spreads_for_today("2026-04-29")
    assert [r.id for r in open_today] == [s1]


def test_restart_persistence(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    store_a = Store(db_path)
    sid = store_a.record_open(
        date="2026-04-29", symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.5,
        opened_at="2026-04-29T14:30:00+00:00",
    )
    store_a.record_stop_event(spread_id=sid, ts="2026-04-29T15:00:00+00:00",
                              event="armed", spot=5001.0, breakeven=5000.5)
    store_a.close()

    store_b = Store(db_path)
    rec = store_b.get_spread(sid)
    assert rec.status == "OPEN"
    assert [e.event for e in store_b.stop_events(sid)] == ["armed"]


def test_idempotent_migration(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    Store(db_path).close()
    Store(db_path).close()  # should not raise
