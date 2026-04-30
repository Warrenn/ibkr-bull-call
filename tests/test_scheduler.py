"""Tests for bull_call.scheduler helpers that don't need a live gateway.

The full ``run_forever`` loop is integration-tested manually; here we cover
the small pure decisions (monthly capital gate) the scheduler makes before
delegating to strategy.attempt_until_filled.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from typing import Any

import pytest

from bull_call.config import Settings
from bull_call.scheduler import Scheduler, _heartbeat_loop
from bull_call.state import Store


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
        heartbeat_interval_sec=300,
    )
    base.update(overrides)
    return Settings(**base)


def _settle(store: Store, *, date: str, symbol: str, pnl: float) -> None:
    sid = store.record_open(
        date=date, symbol=symbol,
        long_strike=4995.0, short_strike=5005.0, debit=4.5,
        opened_at=f"{date}T14:30:00+00:00",
    )
    store.record_settlement(
        spread_id=sid, closed_at=f"{date}T20:00:00+00:00",
        settle_value=5000.0, pnl=pnl,
    )


def test_gate_inactive_when_month_pnl_positive(store: Store) -> None:
    _settle(store, date="2026-04-01", symbol="SPX", pnl=200.0)
    _settle(store, date="2026-04-15", symbol="SPX", pnl=-50.0)
    sched = Scheduler(_settings(monthly_stop_on_negative_pnl=True), store)
    assert sched._monthly_gate_active(dt.date(2026, 4, 29)) is False


def test_gate_inactive_when_month_pnl_zero(store: Store) -> None:
    """Zero MTD pnl is not negative — gate stays open."""

    _settle(store, date="2026-04-01", symbol="SPX", pnl=100.0)
    _settle(store, date="2026-04-15", symbol="SPX", pnl=-100.0)
    sched = Scheduler(_settings(monthly_stop_on_negative_pnl=True), store)
    assert sched._monthly_gate_active(dt.date(2026, 4, 29)) is False


def test_gate_active_when_month_pnl_negative(store: Store) -> None:
    _settle(store, date="2026-04-01", symbol="SPX", pnl=-200.0)
    _settle(store, date="2026-04-15", symbol="SPX", pnl=-100.0)
    sched = Scheduler(_settings(monthly_stop_on_negative_pnl=True), store)
    assert sched._monthly_gate_active(dt.date(2026, 4, 29)) is True


def test_gate_disabled_via_setting(store: Store) -> None:
    """Even with negative MTD pnl, ``monthly_stop_on_negative_pnl=False``
    keeps the gate open (back-compat / opt-out for backtests)."""

    _settle(store, date="2026-04-01", symbol="SPX", pnl=-1000.0)
    sched = Scheduler(_settings(monthly_stop_on_negative_pnl=False), store)
    assert sched._monthly_gate_active(dt.date(2026, 4, 29)) is False


def test_gate_resets_on_first_session_of_new_month(store: Store) -> None:
    """A bad April doesn't carry into May — gate is keyed on year-month."""

    _settle(store, date="2026-04-01", symbol="SPX", pnl=-1000.0)
    sched = Scheduler(_settings(monthly_stop_on_negative_pnl=True), store)
    assert sched._monthly_gate_active(dt.date(2026, 4, 29)) is True
    assert sched._monthly_gate_active(dt.date(2026, 5, 1)) is False


# ---------- heartbeat thread -------------------------------------------------


def test_heartbeat_emits_at_configured_cadence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The heartbeat thread should emit a structured 'heartbeat' event
    every ``interval_s`` seconds and exit promptly when stop is set."""

    stop_event = threading.Event()
    interval_s = 0.05
    caplog.set_level(logging.DEBUG, logger="bull_call.events")

    thread = threading.Thread(
        target=_heartbeat_loop,
        kwargs={"interval_s": interval_s, "stop_event": stop_event},
        daemon=True,
    )
    thread.start()
    try:
        # Give it time for ~3 emissions.
        time.sleep(interval_s * 3.5)
    finally:
        stop_event.set()
        thread.join(timeout=interval_s * 5)

    assert not thread.is_alive(), "heartbeat thread did not exit on stop_event"
    heartbeat_records = [
        r for r in caplog.records
        if r.name == "bull_call.events" and '"heartbeat"' in r.getMessage()
    ]
    assert len(heartbeat_records) >= 2, (
        f"expected ≥2 heartbeats over {interval_s * 3.5}s; got {len(heartbeat_records)}"
    )


def test_heartbeat_exits_immediately_when_stop_already_set() -> None:
    """If stop is already set when the thread starts, it should exit
    without blocking for ``interval_s`` and without emitting any event."""

    stop_event = threading.Event()
    stop_event.set()

    wall_before = time.monotonic()
    thread = threading.Thread(
        target=_heartbeat_loop,
        kwargs={"interval_s": 60.0, "stop_event": stop_event},
        daemon=True,
    )
    thread.start()
    thread.join(timeout=1.0)
    elapsed = time.monotonic() - wall_before

    assert not thread.is_alive(), "heartbeat thread did not exit"
    assert elapsed < 0.5, (
        f"heartbeat blocked for {elapsed:.2f}s instead of bailing out"
    )
