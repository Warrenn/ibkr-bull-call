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
from bull_call.scheduler import Scheduler, _ET, _heartbeat_loop
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
        skip_half_days=True,
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


# ---------- entry-time computation -----------------------------------------


def test_entry_time_for_returns_none_on_non_trading_day(store: Store) -> None:
    """Saturday (NYSE closed) returns None — caller knows to skip ahead
    to the next trading day."""

    sched = Scheduler(_settings(entry_time_et=dt.time(10, 30)), store)
    saturday = dt.date(2026, 5, 2)  # 2026-05-02 is a Saturday
    assert sched._entry_time_for(saturday) is None


def test_entry_time_for_returns_utc_on_trading_day(store: Store) -> None:
    """Wed Apr 29 2026 + ENTRY_TIME_ET=10:30 → UTC equivalent during DST
    is 14:30 UTC."""

    sched = Scheduler(_settings(entry_time_et=dt.time(10, 30)), store)
    weekday = dt.date(2026, 4, 29)  # trading day
    result = sched._entry_time_for(weekday)
    assert result is not None
    # Eastern is EDT (UTC-4) on this date; 10:30 ET = 14:30 UTC.
    assert result.tzinfo == dt.timezone.utc
    assert result.hour == 14
    assert result.minute == 30


def test_next_entry_time_returns_today_when_pre_entry(store: Store) -> None:
    """Now is BEFORE today's entry time and today is a trading day —
    today's entry time is the next opportunity."""

    sched = Scheduler(_settings(entry_time_et=dt.time(10, 30)), store)
    # 2026-04-29 09:00 UTC ≈ 05:00 ET — well before the 10:30 ET entry.
    now = dt.datetime(2026, 4, 29, 9, 0, tzinfo=dt.timezone.utc)
    next_entry = sched._next_entry_time(now)
    assert next_entry.astimezone(_ET).date() == dt.date(2026, 4, 29)
    assert next_entry.astimezone(_ET).time() == dt.time(10, 30)


def test_next_entry_time_skips_to_tomorrow_when_past_entry(store: Store) -> None:
    """Now is AFTER today's entry time → roll forward to next trading
    day's entry time."""

    sched = Scheduler(_settings(entry_time_et=dt.time(10, 30)), store)
    # 2026-04-29 20:00 UTC = 16:00 ET, after entry has passed
    now = dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc)
    next_entry = sched._next_entry_time(now)
    next_et_date = next_entry.astimezone(_ET).date()
    # Should land on 2026-04-30 (Thursday).
    assert next_et_date == dt.date(2026, 4, 30)
    assert next_entry.astimezone(_ET).time() == dt.time(10, 30)


def test_next_entry_time_skips_weekend(store: Store) -> None:
    """Friday late afternoon → next entry rolls to Monday, not Saturday."""

    sched = Scheduler(_settings(entry_time_et=dt.time(10, 30)), store)
    friday_pm = dt.datetime(2026, 5, 1, 22, 0, tzinfo=dt.timezone.utc)
    next_entry = sched._next_entry_time(friday_pm)
    next_et_date = next_entry.astimezone(_ET).date()
    # 2026-05-04 is Monday.
    assert next_et_date == dt.date(2026, 5, 4)


# ---------- sleep_until ------------------------------------------------------


def test_sleep_until_returns_true_when_target_already_passed(store: Store) -> None:
    """If the target is in the past, _sleep_until returns True immediately
    without ever waiting."""

    sched = Scheduler(_settings(), store)
    past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)

    wall_before = time.monotonic()
    result = sched._sleep_until(past)
    elapsed = time.monotonic() - wall_before

    assert result is True
    assert elapsed < 0.1  # didn't actually sleep


def test_sleep_until_returns_false_when_stop_already_set(store: Store) -> None:
    """If _stop_event is already set when called, returns False without
    sleeping."""

    sched = Scheduler(_settings(), store)
    sched._stop_event.set()
    far_future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)

    wall_before = time.monotonic()
    result = sched._sleep_until(far_future)
    elapsed = time.monotonic() - wall_before

    assert result is False
    assert elapsed < 0.1


def test_sleep_until_returns_false_when_stop_set_during_sleep(store: Store) -> None:
    """If shutdown is signalled while we're waiting, return False as
    soon as Event.wait returns. Use a thread to set the event after a
    short delay."""

    sched = Scheduler(_settings(), store)
    target = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=10)

    def signal_stop_soon() -> None:
        time.sleep(0.05)
        sched._stop_event.set()

    t = threading.Thread(target=signal_stop_soon, daemon=True)
    t.start()

    wall_before = time.monotonic()
    result = sched._sleep_until(target)
    elapsed = time.monotonic() - wall_before
    t.join(timeout=1.0)

    assert result is False
    assert elapsed < 1.0  # well under the 10s target


# ---------- reconcile -------------------------------------------------------


def test_reconcile_adopts_detected_spreads(
    store: Store, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When detect_existing_spreads returns a spread, the scheduler adopts
    it into the local store with adopted_from_ibkr=True."""

    from bull_call.chain import OptionContract
    from bull_call.cpapi.reconcile import ExistingSpread

    sched = Scheduler(_settings(), store)
    sched._client = object()  # type: ignore[assignment]
    sched._account_id = "A1"

    detected = [
        ExistingSpread(
            symbol="SPX",
            long_leg=OptionContract(strike=4995.0, conid=111, right="C", expiry="20260429"),
            short_leg=OptionContract(strike=5005.0, conid=222, right="C", expiry="20260429"),
            entry_debit=550.0,
        ),
    ]
    monkeypatch.setattr(
        "bull_call.scheduler.detect_existing_spreads",
        lambda _client, *, account_id, today_et: detected,
    )

    sched._reconcile_with_ibkr(dt.date(2026, 4, 29))

    rec = store.get_spread("2026-04-29#SPX")
    assert rec.long_strike == 4995.0
    assert rec.short_strike == 5005.0
    assert rec.adopted_from_ibkr is True


def test_reconcile_no_op_when_nothing_detected(
    store: Store, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty list → no DDB writes, no events."""

    sched = Scheduler(_settings(), store)
    sched._client = object()  # type: ignore[assignment]
    sched._account_id = "A1"

    monkeypatch.setattr(
        "bull_call.scheduler.detect_existing_spreads",
        lambda _client, *, account_id, today_et: [],
    )

    sched._reconcile_with_ibkr(dt.date(2026, 4, 29))
    assert not store.has_trade_today("2026-04-29")


def test_reconcile_skips_already_opened_today(
    store: Store, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the local store already has a row for today's symbol (e.g.
    bot just opened it before reconcile somehow ran again), skip the
    adoption silently — the existing row wins."""

    from bull_call.chain import OptionContract
    from bull_call.cpapi.reconcile import ExistingSpread

    sched = Scheduler(_settings(), store)
    sched._client = object()  # type: ignore[assignment]
    sched._account_id = "A1"

    # Pre-populate the store as if the bot already opened today's spread
    # via the normal entry path.
    store.record_open(
        date="2026-04-29", symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.50,
        opened_at="2026-04-29T14:30:00+00:00",
    )

    detected = [
        ExistingSpread(
            symbol="SPX",
            long_leg=OptionContract(strike=4995.0, conid=111, right="C", expiry="20260429"),
            short_leg=OptionContract(strike=5005.0, conid=222, right="C", expiry="20260429"),
            entry_debit=550.0,
        ),
    ]
    monkeypatch.setattr(
        "bull_call.scheduler.detect_existing_spreads",
        lambda _client, *, account_id, today_et: detected,
    )

    # Should be a no-op — the existing record stays untouched.
    sched._reconcile_with_ibkr(dt.date(2026, 4, 29))
    rec = store.get_spread("2026-04-29#SPX")
    assert rec.adopted_from_ibkr is False  # original record, NOT adopted


# ---------- _record_settlements --------------------------------------------


class _SchedRespStub:
    """Minimal ``Result``-shaped object: just exposes ``.data``."""

    def __init__(self, data: Any) -> None:
        self.data = data


class _UnderlyingLookupClient:
    """Fake IbkrClient that satisfies the one method ``_record_settlements``
    needs (``search_contract_by_symbol``). Extracted so each settlement test
    doesn't redefine the same class. Returns a fixed conid for any symbol —
    tests that care about per-symbol routing can subclass."""

    UNDERLYING_CONID = 416904

    def search_contract_by_symbol(
        self, *, symbol: str, sec_type: str,
    ) -> _SchedRespStub:
        return _SchedRespStub([{"conid": self.UNDERLYING_CONID}])


def test_record_settlements_no_op_when_no_open_spreads(
    store: Store, caplog: pytest.LogCaptureFixture,
) -> None:
    """No open rows in DDB → no API calls, no DDB writes, no logs."""

    sched = Scheduler(_settings(), store)
    sched._client = object()  # type: ignore[assignment]  # asserted non-None
    caplog.set_level(logging.DEBUG, logger="bull_call.scheduler")
    caplog.set_level(logging.DEBUG, logger="bull_call.events")

    # Snapshot store state before (empty by construction).
    before = store.load_open_spreads_for_today("2026-04-29")
    assert before == []

    sched._record_settlements(dt.date(2026, 4, 29))

    # No state changes: still empty.
    assert store.load_open_spreads_for_today("2026-04-29") == []
    # And no scheduler / events log records emitted at all.
    assert [r for r in caplog.records if r.name in (
        "bull_call.scheduler", "bull_call.events",
    )] == []


def test_record_settlements_records_settle_for_open_spread(
    store: Store, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Open spread + valid settle spot → DDB row updated to SETTLED with
    pnl and settle_value populated."""

    sched = Scheduler(_settings(), store)
    sched._client = _UnderlyingLookupClient()  # type: ignore[assignment]

    store.record_open(
        date="2026-04-29", symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.50,
        opened_at="2026-04-29T14:30:00+00:00",
    )

    monkeypatch.setattr(
        "bull_call.scheduler.fetch_spot",
        lambda _client, *, conid: 5008.21,
    )

    sched._record_settlements(dt.date(2026, 4, 29))

    rec = store.get_spread("2026-04-29#SPX")
    assert rec.status == "SETTLED"
    assert rec.exit_kind == "SETTLE"
    assert rec.settle_value == pytest.approx(5008.21)
    # P&L per 1 contract: payoff = max(0, 5008.21-4995) - max(0, 5008.21-5005) - 5.50
    # = 13.21 - 3.21 - 5.50 = 4.50, *100 = $450.00
    assert rec.pnl == pytest.approx(450.0)


def test_record_settlements_skips_when_spot_unavailable(
    store: Store, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """fetch_spot returns None — leave the row OPEN (operator can rerun
    or manual-settle later) and log loudly so it's visible in CW."""

    sched = Scheduler(_settings(), store)
    sched._client = _UnderlyingLookupClient()  # type: ignore[assignment]

    store.record_open(
        date="2026-04-29", symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.50,
        opened_at="2026-04-29T14:30:00+00:00",
    )
    monkeypatch.setattr(
        "bull_call.scheduler.fetch_spot",
        lambda _client, *, conid: None,
    )
    caplog.set_level(logging.ERROR, logger="bull_call.scheduler")

    sched._record_settlements(dt.date(2026, 4, 29))

    # Row stays OPEN — was NOT marked SETTLED.
    rec = store.get_spread("2026-04-29#SPX")
    assert rec.status == "OPEN"
    assert any("cannot fetch settle spot" in r.getMessage() for r in caplog.records)


# ---------- _monitor_open_spreads error branches ---------------------------


def test_monitor_open_spreads_no_op_when_no_open_rows(
    store: Store, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no open spreads, _monitor_open_spreads sleeps until close
    (so the scheduler still pauses for the rest of the session) and
    makes zero IBKR calls."""

    sched = Scheduler(_settings(), store)
    sched._client = object()  # type: ignore[assignment]
    sched._account_id = "A1"

    # Stop_event already set → _sleep_until returns False immediately.
    sched._stop_event.set()

    # Trip a guard if any IBKR-side function gets called.
    monkeypatch.setattr(
        "bull_call.scheduler.fetch_0dte_call_chain",
        lambda *a, **kw: pytest.fail("should not fetch chain when no open spreads"),
    )
    monkeypatch.setattr(
        "bull_call.scheduler.open_ws",
        lambda *a, **kw: pytest.fail("should not open WS when no open spreads"),
    )

    close_utc = dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc)
    sched._monitor_open_spreads(dt.date(2026, 4, 29), close_utc)


def test_monitor_open_spreads_skips_spread_when_chain_unavailable(
    store: Store, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Open spread exists but ``fetch_0dte_call_chain`` returns None
    (e.g. delayed quotes mid-session) — log error, skip, don't crash."""

    sched = Scheduler(_settings(), store)
    sched._client = object()  # type: ignore[assignment]
    sched._account_id = "A1"

    store.record_open(
        date="2026-04-29", symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.50,
        opened_at="2026-04-29T14:30:00+00:00",
    )

    monkeypatch.setattr(
        "bull_call.scheduler.fetch_0dte_call_chain",
        lambda *a, **kw: None,
    )

    class _StubWs:
        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(
        "bull_call.scheduler.open_ws", lambda *a, **kw: _StubWs(),
    )

    caplog.set_level(logging.ERROR, logger="bull_call.scheduler")
    close_utc = dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc)
    sched._monitor_open_spreads(dt.date(2026, 4, 29), close_utc)

    assert any(
        "cannot rebuild chain" in r.getMessage()
        for r in caplog.records
    )


def test_monitor_open_spreads_short_circuits_on_shutdown(
    store: Store, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``_stop_event`` is already set when the per-spread loop
    iteration starts, monitor returns without subscribing to ticks."""

    sched = Scheduler(_settings(), store)
    sched._client = object()  # type: ignore[assignment]
    sched._account_id = "A1"

    store.record_open(
        date="2026-04-29", symbol="SPX",
        long_strike=4995.0, short_strike=5005.0, debit=5.50,
        opened_at="2026-04-29T14:30:00+00:00",
    )

    # Chain returns valid data so we'd otherwise enter monitor_stop.
    from bull_call.chain import ChainSnapshot, OptionContract
    from bull_call.strikes import OptionQuote

    chain = ChainSnapshot(
        symbol="SPX", expiry="20260429", spot=5000.0, atm_iv=0.18,
        quotes=(OptionQuote(strike=4995.0, bid=5.0, ask=5.2),),
        contracts={
            4995.0: OptionContract(strike=4995.0, conid=111, right="C", expiry="20260429"),
            5005.0: OptionContract(strike=5005.0, conid=222, right="C", expiry="20260429"),
        },
    )
    monkeypatch.setattr(
        "bull_call.scheduler.fetch_0dte_call_chain",
        lambda *a, **kw: chain,
    )

    class _StubWs:
        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(
        "bull_call.scheduler.open_ws", lambda *a, **kw: _StubWs(),
    )

    # subscribe_underlying must NOT be called: shutdown short-circuits
    # before that point.
    monkeypatch.setattr(
        "bull_call.scheduler.subscribe_underlying",
        lambda *a, **kw: pytest.fail("subscribe should not be called after shutdown"),
    )

    sched._stop_event.set()

    close_utc = dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc)
    sched._monitor_open_spreads(dt.date(2026, 4, 29), close_utc)


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
