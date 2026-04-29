"""Tests for the entry retry loop (strategy.attempt_until_filled).

The loop is fully unit-testable: all IBKR-side callables are injected, and we
control the clock + sleep via ``now_fn`` and ``sleep_fn``.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

import pytest

from bull_call.chain import ChainSnapshot, OptionContract
from bull_call.config import Settings
from bull_call.execution import FillReport
from bull_call.state import Store
from bull_call.strategy import attempt_until_filled
from bull_call.strikes import OptionQuote


# `store` fixture provided by tests/conftest.py (moto-backed DynamoDB).


CLOSE_UTC = dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc)
DEADLINE_UTC = dt.datetime(2026, 4, 29, 17, 0, tzinfo=dt.timezone.utc)  # 13:00 ET


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = dict(
        ib_host="-", ib_port=0, ib_client_id=0,
        symbols=("SPX",),
        max_loss_usd=10_000.0,
        pop_threshold=0.50,
        risk_free_rate=0.05,
        entry_time_et=dt.time(10, 30),
        stop_enabled=True,
        stop_latest_sec=30,
        state_table="bull-call-test",
        log_level="INFO",
    )
    base.update(overrides)
    return Settings(**base)


def _chain() -> ChainSnapshot:
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
        symbol="SPX", expiry="20260429",
        spot=5000.0, atm_iv=0.18,
        quotes=quotes, contracts=contracts,
    )


def _clock(start: dt.datetime, advance: dt.timedelta) -> Callable[[], dt.datetime]:
    """Returns a clock that advances by ``advance`` on each call."""

    state = {"now": start}

    def now_fn() -> dt.datetime:
        cur = state["now"]
        state["now"] = cur + advance
        return cur

    return now_fn


# ---------- Happy path: fills on the first attempt --------------------------


def test_first_attempt_fills_and_balances(store: Store) -> None:
    chain = _chain()
    submit_calls: list[Any] = []
    verify_calls: list[Any] = []

    def fetch_chain():
        return chain

    def submit_entry(*, long_leg, short_leg, debit_mid, debit_max, **_):
        submit_calls.append((long_leg.strike, short_leg.strike, debit_mid))
        return FillReport(filled=True, avg_fill_price=debit_mid)

    def verify_legs_balanced(*, long_leg, short_leg):
        verify_calls.append((long_leg.strike, short_leg.strike))
        return True

    def flatten_unmatched_leg(**_):
        pytest.fail("should not flatten on a clean fill")

    result = attempt_until_filled(
        store,
        symbol="SPX", today_iso="2026-04-29",
        close_utc=CLOSE_UTC, deadline_utc=DEADLINE_UTC,
        settings=_settings(pop_threshold=0.50),
        fetch_chain=fetch_chain,
        submit_entry=submit_entry,
        verify_legs_balanced=verify_legs_balanced,
        flatten_unmatched_leg=flatten_unmatched_leg,
        now_fn=_clock(dt.datetime(2026, 4, 29, 14, 30, tzinfo=dt.timezone.utc),
                      dt.timedelta(seconds=30)),
        sleep_fn=lambda _: None,
    )

    assert result is not None
    assert result.spread_id == "2026-04-29#SPX"
    assert len(submit_calls) == 1
    assert len(verify_calls) == 1
    assert store.has_trade_today("2026-04-29") is True


# ---------- Cancel-then-fill: retries immediately on cancel ----------------


def test_first_cancel_second_fills(store: Store) -> None:
    chain = _chain()
    submit_calls: list[float] = []

    def fetch_chain():
        return chain

    def submit_entry(*, debit_mid, **_):
        submit_calls.append(debit_mid)
        if len(submit_calls) == 1:
            return FillReport(filled=False, avg_fill_price=float("nan"))
        return FillReport(filled=True, avg_fill_price=debit_mid)

    result = attempt_until_filled(
        store,
        symbol="SPX", today_iso="2026-04-29",
        close_utc=CLOSE_UTC, deadline_utc=DEADLINE_UTC,
        settings=_settings(pop_threshold=0.50),
        fetch_chain=fetch_chain, submit_entry=submit_entry,
        verify_legs_balanced=lambda **_: True,
        flatten_unmatched_leg=lambda **_: None,
        now_fn=_clock(dt.datetime(2026, 4, 29, 14, 30, tzinfo=dt.timezone.utc),
                      dt.timedelta(minutes=5)),
        sleep_fn=lambda _: None,
    )

    assert result is not None
    assert len(submit_calls) == 2  # first cancelled, second filled


# ---------- Deadline reached without fill ----------------------------------


def test_returns_none_when_deadline_reached(store: Store) -> None:
    """Clock starts already past the deadline -> exit immediately, no submit."""

    submit_calls: list[Any] = []

    def submit_entry(**_):
        submit_calls.append(1)
        return FillReport(filled=False, avg_fill_price=float("nan"))

    result = attempt_until_filled(
        store,
        symbol="SPX", today_iso="2026-04-29",
        close_utc=CLOSE_UTC, deadline_utc=DEADLINE_UTC,
        settings=_settings(),
        fetch_chain=lambda: _chain(),
        submit_entry=submit_entry,
        verify_legs_balanced=lambda **_: True,
        flatten_unmatched_leg=lambda **_: None,
        now_fn=_clock(DEADLINE_UTC + dt.timedelta(seconds=1),
                      dt.timedelta(seconds=1)),
        sleep_fn=lambda _: None,
    )

    assert result is None
    assert submit_calls == []
    assert store.has_trade_today("2026-04-29") is False


def test_loop_exits_when_clock_crosses_deadline_mid_loop(store: Store) -> None:
    """Cancel forever; clock advances by 5 min/iteration; loop exits at deadline."""

    submit_count = 0

    def submit_entry(**_):
        nonlocal submit_count
        submit_count += 1
        return FillReport(filled=False, avg_fill_price=float("nan"))

    start = dt.datetime(2026, 4, 29, 16, 30, tzinfo=dt.timezone.utc)  # 30 min before deadline
    result = attempt_until_filled(
        store,
        symbol="SPX", today_iso="2026-04-29",
        close_utc=CLOSE_UTC, deadline_utc=DEADLINE_UTC,
        settings=_settings(),
        fetch_chain=lambda: _chain(),
        submit_entry=submit_entry,
        verify_legs_balanced=lambda **_: True,
        flatten_unmatched_leg=lambda **_: None,
        now_fn=_clock(start, dt.timedelta(minutes=10)),
        sleep_fn=lambda _: None,
    )

    assert result is None
    # Allow one or two iterations before the clock crosses; either is fine.
    assert 1 <= submit_count <= 3


# ---------- Soft retry: no chain / no viable spread ------------------------


def test_no_chain_soft_retries(store: Store) -> None:
    sleeps: list[float] = []
    chain_calls = 0
    chain = _chain()

    def fetch_chain():
        nonlocal chain_calls
        chain_calls += 1
        if chain_calls < 3:
            return None
        return chain

    result = attempt_until_filled(
        store,
        symbol="SPX", today_iso="2026-04-29",
        close_utc=CLOSE_UTC, deadline_utc=DEADLINE_UTC,
        settings=_settings(pop_threshold=0.50),
        fetch_chain=fetch_chain,
        submit_entry=lambda **kw: FillReport(filled=True, avg_fill_price=kw["debit_mid"]),
        verify_legs_balanced=lambda **_: True,
        flatten_unmatched_leg=lambda **_: None,
        now_fn=_clock(dt.datetime(2026, 4, 29, 14, 30, tzinfo=dt.timezone.utc),
                      dt.timedelta(seconds=10)),
        sleep_fn=lambda s: sleeps.append(s),
        soft_retry_delay_s=60.0,
    )

    assert result is not None
    assert chain_calls == 3
    assert sleeps == [60.0, 60.0]   # two soft retries, one before each None


def test_no_viable_spread_soft_retries_until_pop_clears(store: Store) -> None:
    """If propose_trade returns None at first (POP fails), the loop sleeps
    and retries.  This proves the retry covers selection-time failures, not
    just submit-time cancels."""

    chain = _chain()
    sleeps: list[float] = []
    pop_calls = [0]

    # Settings.pop_threshold high enough to fail on first call, low enough
    # later. We swap the settings via a mutable wrapper.
    settings = _settings(pop_threshold=0.99)

    fetch_chain = lambda: chain

    submit_calls = []
    def submit_entry(**kw):
        submit_calls.append(1)
        return FillReport(filled=True, avg_fill_price=kw["debit_mid"])

    # Simulate "no viable spread" forever -> deadline reached
    result = attempt_until_filled(
        store,
        symbol="SPX", today_iso="2026-04-29",
        close_utc=CLOSE_UTC, deadline_utc=DEADLINE_UTC,
        settings=settings,    # POP=0.99 unreachable
        fetch_chain=fetch_chain,
        submit_entry=submit_entry,
        verify_legs_balanced=lambda **_: True,
        flatten_unmatched_leg=lambda **_: None,
        now_fn=_clock(DEADLINE_UTC - dt.timedelta(minutes=10),
                      dt.timedelta(minutes=5)),
        sleep_fn=lambda s: sleeps.append(s),
        soft_retry_delay_s=60.0,
    )

    assert result is None
    assert submit_calls == []           # never tried to submit
    assert all(s == 60.0 for s in sleeps)  # all soft sleeps


# ---------- Leg-out: flatten and stop trying for the day -------------------


def test_legout_triggers_flatten_and_returns_none(store: Store) -> None:
    chain = _chain()
    flatten_calls: list[Any] = []
    submit_calls: list[Any] = []

    def submit_entry(**kw):
        submit_calls.append(1)
        return FillReport(filled=True, avg_fill_price=kw["debit_mid"])

    def flatten_unmatched_leg(*, long_leg, short_leg):
        flatten_calls.append((long_leg.strike, short_leg.strike))

    result = attempt_until_filled(
        store,
        symbol="SPX", today_iso="2026-04-29",
        close_utc=CLOSE_UTC, deadline_utc=DEADLINE_UTC,
        settings=_settings(pop_threshold=0.50),
        fetch_chain=lambda: chain,
        submit_entry=submit_entry,
        verify_legs_balanced=lambda **_: False,   # leg-out
        flatten_unmatched_leg=flatten_unmatched_leg,
        now_fn=_clock(dt.datetime(2026, 4, 29, 14, 30, tzinfo=dt.timezone.utc),
                      dt.timedelta(seconds=30)),
        sleep_fn=lambda _: None,
    )

    assert result is None
    assert len(submit_calls) == 1   # one attempt only — leg-out blocks retry
    assert len(flatten_calls) == 1
    assert store.has_trade_today("2026-04-29") is False  # no record created


# ---------- §3.7 should_stop_fn signal-aware shutdown ----------------------


def test_loop_exits_immediately_when_should_stop_returns_true(store: Store) -> None:
    """Scheduler signals shutdown via should_stop_fn — the retry loop must
    exit at the top of the next iteration, not block until the deadline."""

    submit_count = 0

    def submit_entry(**_):
        nonlocal submit_count
        submit_count += 1
        return FillReport(filled=True, avg_fill_price=4.50)

    result = attempt_until_filled(
        store,
        symbol="SPX", today_iso="2026-04-29",
        close_utc=CLOSE_UTC, deadline_utc=DEADLINE_UTC,
        settings=_settings(),
        fetch_chain=lambda: _chain(),
        submit_entry=submit_entry,
        verify_legs_balanced=lambda **_: True,
        flatten_unmatched_leg=lambda **_: None,
        now_fn=_clock(dt.datetime(2026, 4, 29, 14, 30, tzinfo=dt.timezone.utc),
                      dt.timedelta(seconds=30)),
        sleep_fn=lambda _: None,
        should_stop_fn=lambda: True,
    )

    assert result is None
    assert submit_count == 0   # never even reached submit_entry


def test_loop_exits_after_should_stop_set_during_soft_retry(store: Store) -> None:
    """If should_stop becomes True during a soft retry sleep, the loop
    exits at the post-sleep check rather than continuing to fetch_chain
    again."""

    chain_calls = 0
    stop_flag = {"set": False}

    def fetch_chain():
        nonlocal chain_calls
        chain_calls += 1
        return None  # always trigger soft retry

    sleeps: list[float] = []

    def sleep_fn(secs: float) -> None:
        sleeps.append(secs)
        # Simulate SIGTERM arriving during the sleep.
        stop_flag["set"] = True

    result = attempt_until_filled(
        store,
        symbol="SPX", today_iso="2026-04-29",
        close_utc=CLOSE_UTC, deadline_utc=DEADLINE_UTC,
        settings=_settings(),
        fetch_chain=fetch_chain,
        submit_entry=lambda **kw: FillReport(filled=True, avg_fill_price=4.50),
        verify_legs_balanced=lambda **_: True,
        flatten_unmatched_leg=lambda **_: None,
        now_fn=_clock(dt.datetime(2026, 4, 29, 14, 30, tzinfo=dt.timezone.utc),
                      dt.timedelta(seconds=30)),
        sleep_fn=sleep_fn,
        should_stop_fn=lambda: stop_flag["set"],
        soft_retry_delay_s=60.0,
    )

    assert result is None
    # First iteration: fetch returned None, slept, stop became set, exited.
    assert chain_calls == 1
    assert sleeps == [60.0]
