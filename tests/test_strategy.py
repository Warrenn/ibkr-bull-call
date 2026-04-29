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


def ticks_typed(items: list[tuple[float | None, dt.datetime]]) -> Iterator[tuple[float | None, dt.datetime]]:
    yield from items


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


# ---------- SIGTERM during monitor (shutdown handling) -------------------


def test_monitor_exits_immediately_when_should_stop_at_start(store: Store) -> None:
    """If shutdown is requested before any tick, monitor returns NEVER and
    never invokes submit_close — even if subsequent ticks would have fired."""

    sid = store.record_open(
        date="2026-04-29", symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.50,
        opened_at="2026-04-29T14:30:00+00:00",
    )
    closes_called: list[bool] = []

    def submit_close(**_: Any) -> FillReport:
        closes_called.append(True)
        return FillReport(filled=True, avg_fill_price=1.50)

    outcome = monitor_stop(
        store,
        spread_id=sid, breakeven=5000.50, settings=make_settings(),
        close_utc=dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc),
        tick_stream=ticks([
            (5001.0, dt.datetime(2026, 4, 29, 15, 0, tzinfo=dt.timezone.utc)),
            (4999.0, dt.datetime(2026, 4, 29, 18, 0, tzinfo=dt.timezone.utc)),
        ]),
        submit_close=submit_close,
        should_stop_fn=lambda: True,
    )

    assert outcome is StopOutcome.NEVER
    assert closes_called == []
    rec = store.get_spread(sid)
    assert rec.status == "OPEN"
    assert store.stop_events(sid) == []


def test_monitor_exits_after_should_stop_set_mid_stream(store: Store) -> None:
    """Stop is requested between ticks; monitor exits at the next iteration
    boundary, before processing the would-fire tick."""

    sid = store.record_open(
        date="2026-04-29", symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.50,
        opened_at="2026-04-29T14:30:00+00:00",
    )
    flag = {"stop": False}
    closes_called: list[bool] = []

    def submit_close(**_: Any) -> FillReport:
        closes_called.append(True)
        return FillReport(filled=True, avg_fill_price=1.50)

    def stream():
        yield (5001.0, dt.datetime(2026, 4, 29, 15, 0, tzinfo=dt.timezone.utc))
        flag["stop"] = True
        yield (4999.0, dt.datetime(2026, 4, 29, 18, 0, tzinfo=dt.timezone.utc))

    outcome = monitor_stop(
        store,
        spread_id=sid, breakeven=5000.50, settings=make_settings(),
        close_utc=dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc),
        tick_stream=stream(),
        submit_close=submit_close,
        should_stop_fn=lambda: flag["stop"],
    )

    assert outcome is StopOutcome.NEVER
    assert closes_called == []
    # ARM happened on the first tick (before stop was set)
    journal_events = [e.event for e in store.stop_events(sid)]
    assert journal_events == ["armed"]
    assert store.get_spread(sid).status == "OPEN"


def test_monitor_disabled_drain_respects_should_stop_fn(store: Store) -> None:
    """When stop_enabled=False the function drains the tick stream until
    close. With shutdown requested it should exit early instead of looping
    over ticks indefinitely."""

    sid = store.record_open(
        date="2026-04-29", symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.50,
        opened_at="2026-04-29T14:30:00+00:00",
    )
    settings = make_settings(stop_enabled=False)
    flag = {"first_seen": False}

    def stop_fn() -> bool:
        # The drain loop calls this AFTER pulling each tick. Flip on after
        # the first one so we exit before draining the whole sequence.
        if flag["first_seen"]:
            return True
        flag["first_seen"] = True
        return False

    def stream():
        yield (5001.0, dt.datetime(2026, 4, 29, 15, 0, tzinfo=dt.timezone.utc))
        yield (5002.0, dt.datetime(2026, 4, 29, 16, 0, tzinfo=dt.timezone.utc))
        # Sentinel so a regression that drains the whole stream is detectable
        # via store assertions, not test hangs.
        yield (5003.0, dt.datetime(2026, 4, 29, 17, 0, tzinfo=dt.timezone.utc))

    outcome = monitor_stop(
        store,
        spread_id=sid, breakeven=5000.50, settings=settings,
        close_utc=dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc),
        tick_stream=stream(),
        submit_close=fake_submit_close_filled,
        should_stop_fn=stop_fn,
    )
    assert outcome is StopOutcome.NEVER
    # Stop journal stays empty (stop_enabled=False short-circuits before
    # the state machine).
    assert store.stop_events(sid) == []


# ---------- R23a data-outage emergency flatten -----------------------------


def _opened(store: Store, *, sid_iso: str = "2026-04-29") -> str:
    return store.record_open(
        date=sid_iso, symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.50,
        opened_at=f"{sid_iso}T14:30:00+00:00",
    )


def test_outage_flattens_after_max_blind(store: Store) -> None:
    """A long enough silence (no fresh ticks) after entry triggers an
    emergency MKT flatten regardless of price/state."""

    sid = _opened(store)
    settings = make_settings(
        monitoring_quote_grace_sec=15,
        monitoring_reconnect_max_attempts=3,
        monitoring_quote_max_blind_sec=60,
    )
    close = dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc)
    base = dt.datetime(2026, 4, 29, 15, 0, tzinfo=dt.timezone.utc)
    closes_called: list[bool] = []
    reconnect_calls = [0]

    def submit_close(**_: Any) -> FillReport:
        closes_called.append(True)
        return FillReport(filled=True, avg_fill_price=2.00)

    def reconnect_fn() -> None:
        reconnect_calls[0] += 1

    # Stream: one fresh tick at +0s, then 7 silent polls every 10s up to +70s.
    # At +60s the bot must emergency-flatten and stop processing.
    stream = ticks([
        (5005.0, base),                                  # fresh
        (None, base + dt.timedelta(seconds=10)),         # blind=10
        (None, base + dt.timedelta(seconds=20)),         # blind=20  → reconnect 1
        (None, base + dt.timedelta(seconds=30)),         # blind=30  → reconnect 2
        (None, base + dt.timedelta(seconds=40)),         # blind=40  → reconnect 3 (cap)
        (None, base + dt.timedelta(seconds=50)),         # blind=50  → no more reconnects
        (None, base + dt.timedelta(seconds=60)),         # blind=60  → FLATTEN
        # Anything past here must NOT be processed:
        (4500.0, base + dt.timedelta(seconds=70)),
    ])

    outcome = monitor_stop(
        store,
        spread_id=sid, breakeven=5000.50, settings=settings, close_utc=close,
        tick_stream=stream, submit_close=submit_close,
        reconnect_fn=reconnect_fn,
    )

    assert outcome is StopOutcome.OUTAGE_FLATTEN
    rec = store.get_spread(sid)
    assert rec.status == "STOPPED"
    assert rec.exit_kind == "OUTAGE_FLATTEN"
    assert closes_called == [True]
    assert reconnect_calls[0] == 3  # capped at max_reconnect_attempts


def test_outage_recovers_within_grace_no_flatten(store: Store) -> None:
    """A short silent gap that recovers before grace_sec must NOT flatten and
    must NOT call reconnect_fn or emit quote_outage."""

    sid = _opened(store)
    settings = make_settings(
        monitoring_quote_grace_sec=15,
        monitoring_reconnect_max_attempts=3,
        monitoring_quote_max_blind_sec=60,
    )
    close = dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc)
    base = dt.datetime(2026, 4, 29, 15, 0, tzinfo=dt.timezone.utc)
    reconnects: list[int] = []

    stream = ticks([
        (5005.0, base),
        (None, base + dt.timedelta(seconds=5)),
        (None, base + dt.timedelta(seconds=10)),  # blind=10s, < grace
        (5006.0, base + dt.timedelta(seconds=12)),  # fresh again
    ])

    outcome = monitor_stop(
        store,
        spread_id=sid, breakeven=5000.50, settings=settings, close_utc=close,
        tick_stream=stream,
        submit_close=fake_submit_close_filled,
        reconnect_fn=lambda: reconnects.append(1),
    )

    # No FIRE either (5005/5006 ≥ breakeven=5000.50, so it would arm but never fire).
    assert outcome is StopOutcome.NEVER
    assert reconnects == []
    assert store.get_spread(sid).status == "OPEN"


def test_outage_recovers_after_grace_continues_normal_monitoring(store: Store) -> None:
    """Quotes go silent past the grace window, reconnect is invoked, then
    quotes recover before max_blind. Monitoring continues normally and the
    stop fires on the next breakeven cross."""

    sid = _opened(store)
    settings = make_settings(
        monitoring_quote_grace_sec=15,
        monitoring_reconnect_max_attempts=3,
        monitoring_quote_max_blind_sec=60,
    )
    close = dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc)
    base = dt.datetime(2026, 4, 29, 15, 0, tzinfo=dt.timezone.utc)
    reconnects: list[int] = []

    stream = ticks([
        (5005.0, base),                                # fresh, arms
        (None, base + dt.timedelta(seconds=10)),       # blind=10s
        (None, base + dt.timedelta(seconds=20)),       # blind=20s → reconnect 1
        (5006.0, base + dt.timedelta(seconds=30)),     # recovery
        (4999.0, base + dt.timedelta(seconds=40)),     # crosses breakeven
    ])

    outcome = monitor_stop(
        store,
        spread_id=sid, breakeven=5000.50, settings=settings, close_utc=close,
        tick_stream=stream,
        submit_close=fake_submit_close_filled,
        reconnect_fn=lambda: reconnects.append(1),
    )

    assert outcome is StopOutcome.FIRED
    assert reconnects == [1]
    rec = store.get_spread(sid)
    assert rec.status == "STOPPED"
    assert rec.exit_kind == "STOP"  # NOT OUTAGE_FLATTEN — the breakeven cross fired


def test_outage_flatten_bypasses_uneconomic_guard(store: Store) -> None:
    """Even if the close credit estimate is non-positive (which would normally
    suppress an optional stop fire), the data-outage emergency flatten MUST
    still fire — per I3 / I9: emergency-flatten paths are not gated by the
    uneconomic-credit guard."""

    sid = _opened(store)
    settings = make_settings(
        monitoring_quote_grace_sec=15,
        monitoring_reconnect_max_attempts=3,
        monitoring_quote_max_blind_sec=60,
    )
    close = dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc)
    base = dt.datetime(2026, 4, 29, 15, 0, tzinfo=dt.timezone.utc)
    closes_called: list[bool] = []

    def submit_close(**_: Any) -> FillReport:
        closes_called.append(True)
        return FillReport(filled=True, avg_fill_price=0.0)

    stream = ticks([
        (5005.0, base),
        (None, base + dt.timedelta(seconds=70)),  # blind from t=0 reference
    ])

    outcome = monitor_stop(
        store,
        spread_id=sid, breakeven=5000.50, settings=settings, close_utc=close,
        tick_stream=stream, submit_close=submit_close,
        estimate_close_credit=lambda: 0.0,
    )

    assert outcome is StopOutcome.OUTAGE_FLATTEN
    assert closes_called == [True]


def test_outage_silent_from_start_still_flattens(store: Store) -> None:
    """Cold-start scenario: no fresh tick ever arrives. After max_blind from
    the first observed (silent) tick, the bot must flatten."""

    sid = _opened(store)
    settings = make_settings(
        monitoring_quote_grace_sec=15,
        monitoring_reconnect_max_attempts=3,
        monitoring_quote_max_blind_sec=60,
    )
    close = dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc)
    base = dt.datetime(2026, 4, 29, 15, 0, tzinfo=dt.timezone.utc)
    closes_called: list[bool] = []

    def submit_close(**_: Any) -> FillReport:
        closes_called.append(True)
        return FillReport(filled=True, avg_fill_price=2.00)

    stream = ticks([
        (None, base),                              # baseline
        (None, base + dt.timedelta(seconds=30)),   # blind=30s
        (None, base + dt.timedelta(seconds=65)),   # blind=65s → FLATTEN
    ])

    outcome = monitor_stop(
        store,
        spread_id=sid, breakeven=5000.50, settings=settings, close_utc=close,
        tick_stream=stream, submit_close=submit_close,
    )

    assert outcome is StopOutcome.OUTAGE_FLATTEN
    assert closes_called == [True]


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
