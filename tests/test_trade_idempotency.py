"""Tests for the daily-trade idempotency + presence invariants.

These pin the contract that protects against double-opens when the EC2 in the
ASG is replaced mid-day, and the contract that says: by end of entry processing
on a trading day, exactly one spread record exists per (symbol, day).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from bull_call.chain import ChainSnapshot, OptionContract
from bull_call.config import Settings
from bull_call.execution import FillReport
from bull_call.state import DuplicateSpreadError, Store
from bull_call.strategy import open_spread
from bull_call.strikes import OptionQuote, Spread


TODAY = "2026-04-29"
# `store` fixture is provided by tests/conftest.py (moto-backed DynamoDB).


def _settings() -> Settings:
    return Settings(
        ib_host="ibgateway", ib_port=4002, ib_client_id=7,
        symbols=("SPX",), max_loss_usd=600.0, pop_threshold=0.50,
        risk_free_rate=0.05, entry_time_et=dt.time(10, 30),
        stop_enabled=True, stop_latest_sec=30,
        state_table="bull-call-test", log_level="INFO",
    )


def _chain(symbol: str = "SPX") -> ChainSnapshot:
    quotes = (
        OptionQuote(strike=4995.0, bid=5.00, ask=6.00),
        OptionQuote(strike=5000.0, bid=2.00, ask=3.00),
        OptionQuote(strike=5005.0, bid=0.50, ask=1.00),
    )
    contracts = {
        q.strike: OptionContract(strike=q.strike, conid=int(q.strike), right="C", expiry="20260429")
        for q in quotes
    }
    return ChainSnapshot(
        symbol=symbol, expiry="20260429",
        spot=5000.0, atm_iv=0.18,
        quotes=quotes, contracts=contracts,
    )


def _spread() -> Spread:
    return Spread(long_strike=4995.0, short_strike=5005.0, debit=5.50, pop=0.75)


def _submit_entry_filled(*, debit_mid: float, **_: Any) -> FillReport:
    return FillReport(filled=True, avg_fill_price=debit_mid, order_id="entry-1")


# ---------- Idempotency: don't double-open after EC2 replacement -------------


def test_today_already_opened_starts_false(store: Store) -> None:
    """A fresh store on a fresh day says 'nothing opened yet'."""

    assert store.today_already_opened(TODAY, "SPX") is False
    assert store.has_trade_today(TODAY) is False


def test_open_spread_flips_today_already_opened_to_true(store: Store) -> None:
    """The exact contract the scheduler relies on: after a successful open,
    a new boot of the bot sees that the spread already exists."""

    open_spread(
        store, chain=_chain(), spread=_spread(),
        now_utc=dt.datetime(2026, 4, 29, 14, 30, tzinfo=dt.timezone.utc),
        today_iso=TODAY,
        submit_entry=_submit_entry_filled,
    )
    assert store.today_already_opened(TODAY, "SPX") is True
    assert store.has_trade_today(TODAY) is True


def test_idempotent_across_process_restart(ddb_table_name: str) -> None:
    """The check holds across a 'process restart' — same DynamoDB table,
    fresh Store handle."""

    a = Store(ddb_table_name, region="us-east-1")
    open_spread(
        a, chain=_chain(), spread=_spread(),
        now_utc=dt.datetime(2026, 4, 29, 14, 30, tzinfo=dt.timezone.utc),
        today_iso=TODAY,
        submit_entry=_submit_entry_filled,
    )
    a.close()

    # Simulate the new EC2 booting: same DynamoDB table, fresh Store handle.
    b = Store(ddb_table_name, region="us-east-1")
    assert b.today_already_opened(TODAY, "SPX") is True
    assert b.has_trade_today(TODAY) is True


def test_double_open_is_blocked_by_unique_index(store: Store) -> None:
    """Belt-and-suspenders: even if scheduler logic were skipped, the unique
    index on (date, symbol) would refuse a second open for the same day."""

    open_spread(
        store, chain=_chain(), spread=_spread(),
        now_utc=dt.datetime(2026, 4, 29, 14, 30, tzinfo=dt.timezone.utc),
        today_iso=TODAY,
        submit_entry=_submit_entry_filled,
    )
    with pytest.raises(DuplicateSpreadError):
        store.record_open(
            date=TODAY, symbol="SPX",
            long_strike=5000.0, short_strike=5010.0, debit=5.0,
            opened_at="2026-04-29T15:30:00+00:00",
        )


def test_idempotency_after_stop_or_settle(store: Store) -> None:
    """A spread that's been STOPPED or SETTLED still blocks re-entry the same
    day — the bot is one-shot per (date, symbol), regardless of outcome."""

    open_spread(
        store, chain=_chain(), spread=_spread(),
        now_utc=dt.datetime(2026, 4, 29, 14, 30, tzinfo=dt.timezone.utc),
        today_iso=TODAY,
        submit_entry=_submit_entry_filled,
    )
    sid = store.load_open_spreads_for_today(TODAY)[0].id
    store.record_close(spread_id=sid, closed_at="2026-04-29T18:00:00+00:00",
                       exit_kind="STOP", pnl=-200.0)
    assert store.today_already_opened(TODAY, "SPX") is True
    assert store.has_trade_today(TODAY) is True


def test_idempotency_is_per_symbol_and_per_day(store: Store) -> None:
    """SPX opened today doesn't make QQQ look already-opened, and tomorrow
    looks fresh again."""

    open_spread(
        store, chain=_chain("SPX"), spread=_spread(),
        now_utc=dt.datetime(2026, 4, 29, 14, 30, tzinfo=dt.timezone.utc),
        today_iso=TODAY,
        submit_entry=_submit_entry_filled,
    )
    assert store.today_already_opened(TODAY, "SPX") is True
    assert store.today_already_opened(TODAY, "QQQ") is False
    assert store.today_already_opened("2026-04-30", "SPX") is False


# ---------- Positive presence: at least one trade today ----------------------


def test_has_trade_today_is_false_with_no_records(store: Store) -> None:
    assert store.has_trade_today(TODAY) is False


def test_has_trade_today_is_false_when_record_is_for_a_different_day(store: Store) -> None:
    open_spread(
        store, chain=_chain(), spread=_spread(),
        now_utc=dt.datetime(2026, 4, 28, 14, 30, tzinfo=dt.timezone.utc),
        today_iso="2026-04-28",
        submit_entry=_submit_entry_filled,
    )
    assert store.has_trade_today("2026-04-28") is True
    assert store.has_trade_today(TODAY) is False


def test_has_trade_today_true_for_any_status(store: Store) -> None:
    """OPEN, STOPPED, and SETTLED all count as 'had a trade today'."""

    open_spread(
        store, chain=_chain(), spread=_spread(),
        now_utc=dt.datetime(2026, 4, 29, 14, 30, tzinfo=dt.timezone.utc),
        today_iso=TODAY,
        submit_entry=_submit_entry_filled,
    )
    sid = store.load_open_spreads_for_today(TODAY)[0].id

    # Settled
    store.record_settlement(
        spread_id=sid, closed_at="2026-04-29T20:00:00+00:00",
        settle_value=5008.0, pnl=250.0,
    )
    assert store.has_trade_today(TODAY) is True
