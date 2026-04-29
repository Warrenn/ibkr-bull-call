"""Tests for bull_call.state — DynamoDB-backed (mocked via moto)."""

from __future__ import annotations

import pytest

from bull_call.state import (
    DuplicateSpreadError,
    SpreadRecord,
    StopEvent,
    Store,
)


def test_record_open_and_lookup(store: Store) -> None:
    sid = store.record_open(
        date="2026-04-29",
        symbol="SPX",
        long_strike=4995.0,
        short_strike=5005.0,
        debit=5.50,
        opened_at="2026-04-29T14:30:00+00:00",
    )
    assert sid == "2026-04-29#SPX"

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
        adopted_from_ibkr=False,
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
    store.record_open(
        date="2026-04-28", symbol="SPX",
        long_strike=4990.0, short_strike=5000.0, debit=4.0,
        opened_at="2026-04-28T14:30:00+00:00",
    )
    store.record_close(spread_id=s2, closed_at="2026-04-29T16:00:00+00:00",
                       exit_kind="STOP", pnl=-200.0)

    open_today = store.load_open_spreads_for_today("2026-04-29")
    assert [r.id for r in open_today] == [s1]


def test_adopt_existing_spread_marks_adopted(store: Store) -> None:
    sid = store.adopt_existing_spread(
        date="2026-04-29", symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.5,
        opened_at="2026-04-29T14:30:00+00:00",
    )
    rec = store.get_spread(sid)
    assert rec.adopted_from_ibkr is True
    # Idempotency still holds: a second adopt for the same date+symbol fails.
    with pytest.raises(DuplicateSpreadError):
        store.adopt_existing_spread(
            date="2026-04-29", symbol="SPX",
            long_strike=4995.0, short_strike=5005.0, debit=5.5,
            opened_at="2026-04-29T14:31:00+00:00",
        )


def test_get_spread_unknown_raises(store: Store) -> None:
    with pytest.raises(KeyError):
        store.get_spread("2026-04-29#SPX")


# ---- monthly_pnl_total -----------------------------------------------------


def _settle_open(
    store: Store, *, date: str, symbol: str, debit: float, pnl: float,
) -> str:
    sid = store.record_open(
        date=date, symbol=symbol,
        long_strike=4995.0, short_strike=5005.0, debit=debit,
        opened_at=f"{date}T14:30:00+00:00",
    )
    store.record_settlement(
        spread_id=sid,
        closed_at=f"{date}T20:00:00+00:00",
        settle_value=5000.0,
        pnl=pnl,
    )
    return sid


def _stop_open(
    store: Store, *, date: str, symbol: str, debit: float, pnl: float,
) -> str:
    sid = store.record_open(
        date=date, symbol=symbol,
        long_strike=4995.0, short_strike=5005.0, debit=debit,
        opened_at=f"{date}T14:30:00+00:00",
    )
    store.record_close(
        spread_id=sid, closed_at=f"{date}T16:30:00+00:00",
        exit_kind="STOP", pnl=pnl,
    )
    return sid


def test_monthly_pnl_empty_month_returns_zero(store: Store) -> None:
    assert store.monthly_pnl_total("2026-04") == 0.0


def test_monthly_pnl_sums_settled_and_stopped(store: Store) -> None:
    _settle_open(store, date="2026-04-01", symbol="SPX", debit=4.5, pnl=120.0)
    _settle_open(store, date="2026-04-15", symbol="SPX", debit=4.8, pnl=-80.0)
    _stop_open(store, date="2026-04-22", symbol="SPX", debit=5.0, pnl=-200.0)
    assert store.monthly_pnl_total("2026-04") == pytest.approx(120.0 - 80.0 - 200.0)


def test_monthly_pnl_excludes_open_spreads(store: Store) -> None:
    """An OPEN spread has no realized pnl yet — must not affect the gate."""

    _settle_open(store, date="2026-04-01", symbol="SPX", debit=4.5, pnl=100.0)
    store.record_open(
        date="2026-04-29", symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.0,
        opened_at="2026-04-29T14:30:00+00:00",
    )
    assert store.monthly_pnl_total("2026-04") == pytest.approx(100.0)


def test_monthly_pnl_only_counts_target_month(store: Store) -> None:
    _settle_open(store, date="2026-03-30", symbol="SPX", debit=4.5, pnl=500.0)
    _settle_open(store, date="2026-04-01", symbol="SPX", debit=4.5, pnl=-30.0)
    _settle_open(store, date="2026-04-30", symbol="SPX", debit=4.5, pnl=-70.0)
    _settle_open(store, date="2026-05-01", symbol="SPX", debit=4.5, pnl=-999.0)
    assert store.monthly_pnl_total("2026-04") == pytest.approx(-100.0)


def test_monthly_pnl_invalid_year_month_raises(store: Store) -> None:
    with pytest.raises(ValueError):
        store.monthly_pnl_total("2026/04")
    with pytest.raises(ValueError):
        store.monthly_pnl_total("202604")
    with pytest.raises(ValueError):
        store.monthly_pnl_total("2026-4")
