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
        session_error_backoff_sec=300,
        session_error_max_consecutive=5,
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


def test_heartbeat_exits_immediately_when_stop_already_set(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If stop is already set when the thread starts, it should exit
    without blocking for ``interval_s`` AND without emitting any event."""

    stop_event = threading.Event()
    stop_event.set()
    caplog.set_level(logging.DEBUG, logger="bull_call.events")

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
    # The "drain on shutdown" semantics: zero emissions when stop is
    # set before the first wait() call.
    heartbeat_records = [
        r for r in caplog.records
        if r.name == "bull_call.events" and '"heartbeat"' in r.getMessage()
    ]
    assert heartbeat_records == [], (
        f"expected zero heartbeats when stop is already set; "
        f"got {len(heartbeat_records)}"
    )


# ---------- session-level crash recovery ------------------------------------


def _scheduler_with_session_results(
    store: Store, settings: Settings, results: list[Any],
) -> tuple[Scheduler, list[int]]:
    """Build a Scheduler whose `_run_one_session` is replaced by a stub
    that yields the next item in ``results`` per call. An item that is an
    Exception instance is raised; anything else is treated as success.

    The stub requests shutdown right after popping the LAST scripted
    result (whether success or exception) so the loop exits cleanly
    after the script is exhausted — without a phantom extra call.
    """

    sched = Scheduler(settings, store)
    calls: list[int] = []
    remaining = list(results)

    def stub() -> None:
        calls.append(len(calls) + 1)
        if not remaining:  # safety net: should be unreachable
            sched.request_shutdown()
            return
        outcome = remaining.pop(0)
        if not remaining:
            sched.request_shutdown()
        if isinstance(outcome, BaseException):
            raise outcome

    sched._run_one_session = stub  # type: ignore[method-assign]
    return sched, calls


@pytest.fixture
def stub_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the gateway-lifecycle calls so run_forever can be exercised
    without an actual IBKR gateway. Used by every session-recovery test."""

    monkeypatch.setattr("bull_call.scheduler.connect", lambda **kw: object())
    monkeypatch.setattr("bull_call.scheduler.select_account_id", lambda _c: "A1")
    monkeypatch.setattr("bull_call.scheduler.disconnect", lambda _c: None)


def _record_wait_timeouts(
    monkeypatch: pytest.MonkeyPatch, sched: Scheduler,
) -> list[float | None]:
    """Replace ``sched._stop_event.wait`` with a recorder that returns
    True (so the wait completes immediately) once shutdown is requested
    and False otherwise — preserves loop semantics while exposing the
    timeout values the recovery code is requesting."""

    timeouts: list[float | None] = []
    stop_event = sched._stop_event
    real_is_set = stop_event.is_set

    def fake_wait(timeout: float | None = None) -> bool:
        timeouts.append(timeout)
        return real_is_set()

    monkeypatch.setattr(stop_event, "wait", fake_wait)
    return timeouts


def test_run_forever_recovers_from_transient_session_error(
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    stub_gateway: None,
) -> None:
    """A single transient exception is caught, the recovery code emits a
    ``session_error`` event, sleeps for the configured backoff, and the
    loop continues to the next session."""

    settings = _settings(
        session_error_backoff_sec=300,
        session_error_max_consecutive=5,
    )
    sched, calls = _scheduler_with_session_results(
        store, settings, [RuntimeError("transient blip"), None],
    )
    timeouts = _record_wait_timeouts(monkeypatch, sched)
    caplog.set_level(logging.INFO, logger="bull_call.events")

    sched.run_forever()

    assert len(calls) == 2, "loop did not retry after transient exception"
    # Backoff was invoked with the configured timeout at least once
    # (between the failure and the success).
    assert 300.0 in timeouts, (
        f"expected wait(timeout=300.0) during recovery; got {timeouts}"
    )
    # And a session_error event was emitted with consecutive=1.
    session_error_records = [
        r for r in caplog.records
        if r.name == "bull_call.events" and '"session_error"' in r.getMessage()
    ]
    assert len(session_error_records) == 1
    assert '"consecutive": 1' in session_error_records[0].getMessage()


def test_run_forever_circuit_breaker_opens_after_max_consecutive_errors(
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    stub_gateway: None,
) -> None:
    """N consecutive failures trip the circuit breaker; loop exits so ASG
    can respawn with fresh state. Each failing session emits its own
    session_error event with the matching consecutive counter."""

    settings = _settings(
        session_error_backoff_sec=300,
        session_error_max_consecutive=3,
    )
    sched, calls = _scheduler_with_session_results(
        store, settings,
        [RuntimeError("boom 1"), RuntimeError("boom 2"), RuntimeError("boom 3"),
         RuntimeError("boom 4")],  # 4th would never run if circuit opens at 3
    )
    _record_wait_timeouts(monkeypatch, sched)
    caplog.set_level(logging.INFO, logger="bull_call.events")

    sched.run_forever()

    assert len(calls) == 3, (
        f"circuit breaker should have stopped after 3 consecutive errors; "
        f"saw {len(calls)} calls"
    )

    # One session_error per failing session, in order, with the right counter.
    session_error_records = [
        r for r in caplog.records
        if r.name == "bull_call.events" and '"session_error"' in r.getMessage()
    ]
    assert len(session_error_records) == 3
    for idx, record in enumerate(session_error_records, start=1):
        assert f'"consecutive": {idx}' in record.getMessage()
        assert '"max_consecutive": 3' in record.getMessage()

    # Followed by exactly one circuit_breaker_open.
    breaker_records = [
        r for r in caplog.records
        if r.name == "bull_call.events" and '"circuit_breaker_open"' in r.getMessage()
    ]
    assert len(breaker_records) == 1


def test_run_forever_resets_consecutive_counter_on_success(
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    stub_gateway: None,
) -> None:
    """A successful session should reset the consecutive-error counter,
    so the circuit breaker only fires on UNINTERRUPTED runs of failures.
    Asserts no ``circuit_breaker_open`` event leaks for this mixed
    success/failure pattern."""

    settings = _settings(
        session_error_backoff_sec=300,
        session_error_max_consecutive=3,
    )
    # Pattern: fail, fail, succeed, fail, fail, succeed (counter resets after each success).
    # Total 6 sessions; circuit breaker would have fired at 3 if it didn't reset.
    sched, calls = _scheduler_with_session_results(
        store, settings,
        [
            RuntimeError("blip 1"),
            RuntimeError("blip 2"),
            None,                   # success -> resets counter
            RuntimeError("blip 3"),
            RuntimeError("blip 4"),
            None,                   # success again -> resets again
        ],
    )
    _record_wait_timeouts(monkeypatch, sched)
    caplog.set_level(logging.INFO, logger="bull_call.events")

    sched.run_forever()

    assert len(calls) == 6, (
        f"counter should reset on success; circuit didn't open prematurely. "
        f"got {len(calls)} calls"
    )
    # Strict negative: no circuit-breaker event for this mixed pattern.
    breaker_records = [
        r for r in caplog.records
        if r.name == "bull_call.events" and '"circuit_breaker_open"' in r.getMessage()
    ]
    assert breaker_records == [], (
        "circuit breaker fired despite successful sessions resetting the counter"
    )


@pytest.mark.parametrize(
    "raised",
    [KeyboardInterrupt(), SystemExit(0)],
    ids=["KeyboardInterrupt", "SystemExit"],
)
def test_run_forever_propagates_signal_exceptions(
    store: Store,
    raised: BaseException,
    stub_gateway: None,
) -> None:
    """KeyboardInterrupt and SystemExit MUST both escape the recovery
    wrapper — swallowing them would mean Ctrl+C / docker-stop / forced
    interpreter shutdown are silently ignored."""

    settings = _settings()
    sched, calls = _scheduler_with_session_results(
        store, settings, [raised],
    )

    with pytest.raises(type(raised)):
        sched.run_forever()
    assert len(calls) == 1
