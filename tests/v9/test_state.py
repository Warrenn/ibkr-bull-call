"""Tests for ``bull_call.v9.state`` — DynamoDB-backed v9 record types
for the paper-trading pilot.

Schema additions (single-table design, alongside the SPX records):

    pk                          sk             attrs
    V9#POSITION                 {ticker}       shares, last_price, updated_at
    V9#FILL#{date}              {ts}#{seq}     ticker, action, qty, price, slippage_bps
    V9#NAV                      {date}         nav_dollars, positions_dump
    V9#META                     PILOT          start_date, end_date,
                                               initial_capital, status, halt_reason

Pilot lifecycle status:
    AWAITING_START → ACTIVE → COMPLETED
                  ↘  HALTED (catastrophic stop)
"""

from __future__ import annotations

import pytest

from bull_call.v9.state import (
    V9Fill,
    V9NavSnapshot,
    V9PilotMetadata,
    V9Position,
    V9Store,
)


@pytest.fixture
def v9_store(ddb_table_name: str) -> V9Store:
    return V9Store(ddb_table_name, region="us-east-1")


# ---- pilot metadata ---------------------------------------------------------


def test_pilot_metadata_initialize_and_fetch(v9_store: V9Store) -> None:
    v9_store.initialize_pilot(initial_capital=30000.0)

    meta = v9_store.get_pilot_metadata()
    assert meta is not None
    assert meta.initial_capital == 30000.0
    assert meta.status == "AWAITING_START"
    assert meta.start_date is None
    assert meta.end_date is None
    assert meta.halt_reason is None


def test_initialize_pilot_is_idempotent_after_existing(v9_store: V9Store) -> None:
    """Re-initializing should refuse rather than reset a running pilot."""
    v9_store.initialize_pilot(initial_capital=30000.0)

    with pytest.raises(RuntimeError, match="pilot already initialized"):
        v9_store.initialize_pilot(initial_capital=50000.0)


def test_get_pilot_metadata_returns_none_when_uninitialized(v9_store: V9Store) -> None:
    assert v9_store.get_pilot_metadata() is None


def test_pilot_lifecycle_start_to_complete(v9_store: V9Store) -> None:
    v9_store.initialize_pilot(initial_capital=30000.0)

    v9_store.start_pilot(start_date="2026-05-01", end_date="2026-09-09")
    meta = v9_store.get_pilot_metadata()
    assert meta is not None
    assert meta.status == "ACTIVE"
    assert meta.start_date == "2026-05-01"
    assert meta.end_date == "2026-09-09"

    v9_store.complete_pilot()
    meta = v9_store.get_pilot_metadata()
    assert meta is not None
    assert meta.status == "COMPLETED"


def test_pilot_halt_records_reason(v9_store: V9Store) -> None:
    v9_store.initialize_pilot(initial_capital=30000.0)
    v9_store.start_pilot(start_date="2026-05-01", end_date="2026-09-09")

    v9_store.halt_pilot(reason="cum DD < -25%")
    meta = v9_store.get_pilot_metadata()
    assert meta is not None
    assert meta.status == "HALTED"
    assert meta.halt_reason == "cum DD < -25%"


# ---- positions --------------------------------------------------------------


def test_upsert_and_get_position(v9_store: V9Store) -> None:
    pos = V9Position(ticker="XLK", shares=50.0, last_price=200.0,
                     updated_at="2026-05-01T14:30:00Z")
    v9_store.upsert_position(pos)

    fetched = v9_store.list_positions()
    assert len(fetched) == 1
    assert fetched[0] == pos


def test_upsert_position_updates_existing(v9_store: V9Store) -> None:
    v9_store.upsert_position(V9Position(
        ticker="XLK", shares=50.0, last_price=200.0,
        updated_at="2026-05-01T14:30:00Z"))
    v9_store.upsert_position(V9Position(
        ticker="XLK", shares=60.0, last_price=210.0,
        updated_at="2026-06-01T14:30:00Z"))

    positions = v9_store.list_positions()
    assert len(positions) == 1
    assert positions[0].shares == 60.0
    assert positions[0].last_price == 210.0


def test_list_positions_returns_alphabetical(v9_store: V9Store) -> None:
    for ticker in ["XLY", "XLK", "XLF"]:
        v9_store.upsert_position(V9Position(
            ticker=ticker, shares=10.0, last_price=100.0,
            updated_at="2026-05-01T14:30:00Z"))

    positions = v9_store.list_positions()
    assert [p.ticker for p in positions] == ["XLF", "XLK", "XLY"]


def test_delete_position(v9_store: V9Store) -> None:
    v9_store.upsert_position(V9Position(
        ticker="XLK", shares=50.0, last_price=200.0,
        updated_at="2026-05-01T14:30:00Z"))
    v9_store.upsert_position(V9Position(
        ticker="XLF", shares=30.0, last_price=50.0,
        updated_at="2026-05-01T14:30:00Z"))

    v9_store.delete_position("XLK")

    positions = v9_store.list_positions()
    assert [p.ticker for p in positions] == ["XLF"]


# ---- fills ------------------------------------------------------------------


def test_record_and_list_fills_for_rebalance_date(v9_store: V9Store) -> None:
    fill = V9Fill(
        rebalance_date="2026-05-01",
        ts="2026-05-01T14:32:01Z",
        ticker="XLK",
        action="BUY",
        quantity=50.0,
        price=200.0,
        slippage_bps=5.0,
    )
    v9_store.record_fill(fill)

    fills = v9_store.list_fills_for_date("2026-05-01")
    assert len(fills) == 1
    assert fills[0] == fill


def test_record_fill_preserves_order_with_seq(v9_store: V9Store) -> None:
    """Two fills at the same timestamp should be persisted in insertion order."""
    base = V9Fill(rebalance_date="2026-05-01", ts="2026-05-01T14:32:01Z",
                  ticker="XLK", action="BUY", quantity=10.0, price=200.0,
                  slippage_bps=None)
    v9_store.record_fill(base)
    v9_store.record_fill(V9Fill(rebalance_date="2026-05-01",
                                ts="2026-05-01T14:32:01Z",
                                ticker="XLF", action="BUY", quantity=20.0,
                                price=50.0, slippage_bps=None))

    fills = v9_store.list_fills_for_date("2026-05-01")
    assert len(fills) == 2
    assert [f.ticker for f in fills] == ["XLK", "XLF"]


def test_list_fills_for_other_date_empty(v9_store: V9Store) -> None:
    v9_store.record_fill(V9Fill(rebalance_date="2026-05-01",
                                ts="2026-05-01T14:32:01Z", ticker="XLK",
                                action="BUY", quantity=10.0, price=200.0,
                                slippage_bps=None))

    assert v9_store.list_fills_for_date("2026-06-01") == []


# ---- NAV snapshots ----------------------------------------------------------


def test_record_and_get_nav_snapshot(v9_store: V9Store) -> None:
    snap = V9NavSnapshot(
        date="2026-05-01",
        nav_dollars=30000.0,
        positions_dump={"XLK": 10000.0, "XLF": 10000.0, "XLE": 10000.0},
    )
    v9_store.record_nav_snapshot(snap)

    fetched = v9_store.get_nav_snapshot("2026-05-01")
    assert fetched == snap


def test_get_nav_snapshot_missing_returns_none(v9_store: V9Store) -> None:
    assert v9_store.get_nav_snapshot("2026-05-01") is None


def test_list_nav_snapshots_in_range_returns_sorted(v9_store: V9Store) -> None:
    for date in ["2026-05-15", "2026-05-01", "2026-05-08"]:
        v9_store.record_nav_snapshot(V9NavSnapshot(
            date=date, nav_dollars=30000.0, positions_dump={}))

    snaps = v9_store.list_nav_snapshots(
        start_date="2026-05-01", end_date="2026-05-31"
    )
    assert [s.date for s in snaps] == ["2026-05-01", "2026-05-08", "2026-05-15"]


def test_list_nav_snapshots_excludes_outside_range(v9_store: V9Store) -> None:
    for date in ["2026-04-30", "2026-05-15", "2026-06-01"]:
        v9_store.record_nav_snapshot(V9NavSnapshot(
            date=date, nav_dollars=30000.0, positions_dump={}))

    snaps = v9_store.list_nav_snapshots(
        start_date="2026-05-01", end_date="2026-05-31"
    )
    assert [s.date for s in snaps] == ["2026-05-15"]
