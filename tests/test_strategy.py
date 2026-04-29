"""Tests for bull_call.strategy (sync, library-agnostic)."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterator
from typing import Any

import pytest

from bull_call.chain import ChainSnapshot, OptionContract
from bull_call.config import Settings
from bull_call.execution import FillReport
from bull_call.state import Store
from bull_call.strategy import (
    OpenResult,
    StopOutcome,
    monitor_stop,
    open_spread,
    propose_trade,
    settlement_pnl,
)
from bull_call.strikes import OptionQuote, Spread


# ---------- helpers ----------------------------------------------------------


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = dict(
        ib_host="ibgateway", ib_port=4002, ib_client_id=7,
        symbols=("SPX",), max_loss_usd=600.0, pop_threshold=0.70,
        risk_free_rate=0.05,
        entry_time_et=dt.time(10, 30),
        stop_enabled=True, stop_latest_sec=30,
        state_table="bull-call-test", log_level="INFO",
    )
    base.update(overrides)
    return Settings(**base)


def make_chain(spot: float = 5000.0, atm_iv: float = 0.18) -> ChainSnapshot:
    quotes = (
        OptionQuote(strike=4995.0, bid=5.00, ask=6.00),
        OptionQuote(strike=5000.0, bid=2.00, ask=3.00),
        OptionQuote(strike=5005.0, bid=0.50, ask=1.00),
        OptionQuote(strike=5010.0, bid=0.10, ask=0.30),
    )
    contracts = {
        q.strike: OptionContract(
            strike=q.strike, conid=int(q.strike), right="C", expiry="20260429",
        )
        for q in quotes
    }
    return ChainSnapshot(
        symbol="SPX",
        expiry="20260429",
        spot=spot,
        atm_iv=atm_iv,
        quotes=quotes,
        contracts=contracts,
    )


def fake_submit_entry_filled(*, debit_mid: float, **_: Any) -> FillReport:
    return FillReport(filled=True, avg_fill_price=debit_mid, order_id="entry-1")


def fake_submit_entry_unfilled(**_: Any) -> FillReport:
    return FillReport(filled=False, avg_fill_price=math.nan)


def fake_submit_close_filled(**_: Any) -> FillReport:
    return FillReport(filled=True, avg_fill_price=1.50, order_id="close-1")


def ticks(items: list[tuple[float, dt.datetime]]) -> Iterator[tuple[float, dt.datetime]]:
    yield from items


# `store` fixture is provided by tests/conftest.py (moto-backed DynamoDB).


# ---------- propose_trade ----------------------------------------------------


def test_propose_trade_picks_a_spread() -> None:
    chain = make_chain()
    settings = make_settings(pop_threshold=0.50)
    now = dt.datetime(2026, 4, 29, 14, 30, tzinfo=dt.timezone.utc)
    close = dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc)

    spread = propose_trade(chain, settings=settings, now_utc=now, close_utc=close)

    assert spread is not None
    assert spread.long_strike == 4995.0
    assert spread.short_strike >= 5000.0


def test_propose_trade_returns_none_when_pop_threshold_unreachable() -> None:
    chain = make_chain(spot=5000.0, atm_iv=0.18)
    settings = make_settings(pop_threshold=0.70)
    now = dt.datetime(2026, 4, 29, 14, 30, tzinfo=dt.timezone.utc)
    close = dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc)

    assert propose_trade(chain, settings=settings, now_utc=now, close_utc=close) is None


# ---------- open_spread ------------------------------------------------------


def test_open_spread_persists_and_returns_id(store: Store) -> None:
    chain = make_chain()
    spread = Spread(long_strike=4995.0, short_strike=5005.0, debit=5.5, pop=0.75)

    result = open_spread(
        store,
        chain=chain, spread=spread,
        now_utc=dt.datetime(2026, 4, 29, 14, 30, tzinfo=dt.timezone.utc),
        today_iso="2026-04-29",
        submit_entry=fake_submit_entry_filled,
    )

    assert isinstance(result, OpenResult)
    assert result.fill_price == pytest.approx(5.5)
    rec = store.get_spread(result.spread_id)
    assert rec.long_strike == 4995.0
    assert rec.short_strike == 5005.0
    assert rec.status == "OPEN"


def test_open_spread_returns_none_on_unfilled(store: Store) -> None:
    chain = make_chain()
    spread = Spread(long_strike=4995.0, short_strike=5005.0, debit=5.5, pop=0.75)

    result = open_spread(
        store,
        chain=chain, spread=spread,
        now_utc=dt.datetime(2026, 4, 29, 14, 30, tzinfo=dt.timezone.utc),
        today_iso="2026-04-29",
        submit_entry=fake_submit_entry_unfilled,
    )
    assert result is None
    assert store.load_open_spreads_for_today("2026-04-29") == []


# ---------- monitor_stop -----------------------------------------------------


def test_monitor_fires_on_breakeven_cross_after_arm(store: Store) -> None:
    sid = store.record_open(
        date="2026-04-29", symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.50,
        opened_at="2026-04-29T14:30:00+00:00",
    )
    settings = make_settings()
    close = dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc)
    breakeven = 5000.50
    closes_called: list[bool] = []

    def submit_close(**_: Any) -> FillReport:
        closes_called.append(True)
        return FillReport(filled=True, avg_fill_price=1.50)

    outcome = monitor_stop(
        store,
        spread_id=sid, breakeven=breakeven, settings=settings,
        close_utc=close,
        tick_stream=ticks([
            (4998.0, dt.datetime(2026, 4, 29, 14, 31, tzinfo=dt.timezone.utc)),
            (5001.0, dt.datetime(2026, 4, 29, 15, 0, tzinfo=dt.timezone.utc)),
            (4999.0, dt.datetime(2026, 4, 29, 18, 0, tzinfo=dt.timezone.utc)),
        ]),
        submit_close=submit_close,
    )

    assert outcome is StopOutcome.FIRED
    rec = store.get_spread(sid)
    assert rec.status == "STOPPED"
    assert rec.exit_kind == "STOP"
    assert rec.pnl == pytest.approx(-400.0)
    assert closes_called == [True]
    events = [e.event for e in store.stop_events(sid)]
    assert events == ["armed", "fired"]


def test_monitor_suppresses_in_last_window(store: Store) -> None:
    sid = store.record_open(
        date="2026-04-29", symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.50,
        opened_at="2026-04-29T14:30:00+00:00",
    )
    settings = make_settings(stop_latest_sec=30)
    close = dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc)

    outcome = monitor_stop(
        store,
        spread_id=sid, breakeven=5000.50, settings=settings, close_utc=close,
        tick_stream=ticks([
            (5001.0, dt.datetime(2026, 4, 29, 15, 0, tzinfo=dt.timezone.utc)),
            (4999.0, dt.datetime(2026, 4, 29, 19, 59, 45, tzinfo=dt.timezone.utc)),
        ]),
        submit_close=fake_submit_close_filled,
    )

    assert outcome is StopOutcome.SUPPRESSED
    rec = store.get_spread(sid)
    assert rec.status == "OPEN"
    events = [e.event for e in store.stop_events(sid)]
    assert events == ["armed", "suppressed"]


def test_monitor_resumes_armed_from_journal(store: Store) -> None:
    sid = store.record_open(
        date="2026-04-29", symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.50,
        opened_at="2026-04-29T14:30:00+00:00",
    )
    store.record_stop_event(spread_id=sid, ts="2026-04-29T15:00:00+00:00",
                            event="armed", spot=5001.0, breakeven=5000.5)

    outcome = monitor_stop(
        store,
        spread_id=sid, breakeven=5000.50, settings=make_settings(),
        close_utc=dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc),
        tick_stream=ticks([
            (4999.0, dt.datetime(2026, 4, 29, 18, 0, tzinfo=dt.timezone.utc)),
        ]),
        submit_close=fake_submit_close_filled,
    )
    assert outcome is StopOutcome.FIRED


def test_monitor_disabled_drains_stream_returns_never(store: Store) -> None:
    sid = store.record_open(
        date="2026-04-29", symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.50,
        opened_at="2026-04-29T14:30:00+00:00",
    )
    settings = make_settings(stop_enabled=False)

    outcome = monitor_stop(
        store,
        spread_id=sid, breakeven=5000.50, settings=settings,
        close_utc=dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc),
        tick_stream=ticks([
            (4990.0, dt.datetime(2026, 4, 29, 18, 0, tzinfo=dt.timezone.utc)),
        ]),
        submit_close=fake_submit_close_filled,
    )

    assert outcome is StopOutcome.NEVER
    rec = store.get_spread(sid)
    assert rec.status == "OPEN"


def test_monitor_skips_fire_when_close_credit_nonpositive(store: Store) -> None:
    sid = store.record_open(
        date="2026-04-29", symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.50,
        opened_at="2026-04-29T14:30:00+00:00",
    )
    closes_called: list[bool] = []

    def submit_close(**_: Any) -> FillReport:
        closes_called.append(True)
        return FillReport(filled=True, avg_fill_price=0.0)

    outcome = monitor_stop(
        store,
        spread_id=sid, breakeven=5000.50, settings=make_settings(),
        close_utc=dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc),
        tick_stream=ticks([
            (5001.0, dt.datetime(2026, 4, 29, 15, 0, tzinfo=dt.timezone.utc)),
            (4999.0, dt.datetime(2026, 4, 29, 18, 0, tzinfo=dt.timezone.utc)),
        ]),
        submit_close=submit_close,
        estimate_close_credit=lambda: 0.0,
    )

    assert outcome is StopOutcome.UNECONOMIC
    assert store.get_spread(sid).status == "OPEN"
    assert closes_called == []
    events = [e.event for e in store.stop_events(sid)]
    assert events == ["armed", "uneconomic"]


def test_monitor_fires_when_close_credit_positive(store: Store) -> None:
    sid = store.record_open(
        date="2026-04-29", symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.50,
        opened_at="2026-04-29T14:30:00+00:00",
    )

    outcome = monitor_stop(
        store,
        spread_id=sid, breakeven=5000.50, settings=make_settings(),
        close_utc=dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc),
        tick_stream=ticks([
            (5001.0, dt.datetime(2026, 4, 29, 15, 0, tzinfo=dt.timezone.utc)),
            (4999.0, dt.datetime(2026, 4, 29, 18, 0, tzinfo=dt.timezone.utc)),
        ]),
        submit_close=fake_submit_close_filled,
        estimate_close_credit=lambda: 1.20,
    )
    assert outcome is StopOutcome.FIRED


# ---------- settlement_pnl ---------------------------------------------------


def test_settlement_pnl_max_profit() -> None:
    pnl = settlement_pnl(entry_debit=5.50, long_strike=4995.0, short_strike=5005.0, settle_spot=5020.0)
    assert pnl == pytest.approx(450.0)


def test_settlement_pnl_max_loss() -> None:
    pnl = settlement_pnl(entry_debit=5.50, long_strike=4995.0, short_strike=5005.0, settle_spot=4980.0)
    assert pnl == pytest.approx(-550.0)


def test_settlement_pnl_partial_inside_strikes() -> None:
    pnl = settlement_pnl(entry_debit=5.50, long_strike=4995.0, short_strike=5005.0, settle_spot=5001.0)
    assert pnl == pytest.approx(50.0)


def test_settlement_pnl_at_breakeven() -> None:
    pnl = settlement_pnl(entry_debit=5.50, long_strike=4995.0, short_strike=5005.0, settle_spot=5000.50)
    assert pnl == pytest.approx(0.0)
