"""Day-by-day session loop. Single long-running daemon owns the gateway session."""

from __future__ import annotations

import datetime as dt
import logging
import signal
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from ibind import IbkrClient

from bull_call.calendar import (
    is_trading_day,
    next_session_open_utc,
    session_times,
)
from bull_call.config import Settings
from bull_call.cpapi.chain import (
    estimate_close_credit as cp_estimate_close_credit,
)
from bull_call.cpapi.chain import fetch_0dte_call_chain, fetch_spot
from bull_call.cpapi.client import connect, disconnect, select_account_id
from bull_call import events
from bull_call.cpapi.execution import (
    cancel_orphaned_combo_orders,
    flatten_unmatched_leg as cp_flatten_unmatched_leg,
)
from bull_call.cpapi.execution import (
    submit_close_market,
    submit_entry_lmt,
    verify_legs_balanced as cp_verify_legs_balanced,
)
from bull_call.cpapi.reconcile import detect_existing_spreads
from bull_call.cpapi.spot import open_ws, stream_ticks, subscribe_underlying
from bull_call.state import Store
from bull_call.strategy import (
    attempt_until_filled,
    monitor_stop,
    open_spread,
    propose_trade,
    settlement_pnl,
)

log = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")


def _heartbeat_loop(*, interval_s: float, stop_event: threading.Event) -> None:
    """Emit a 'heartbeat' event every ``interval_s`` until ``stop_event`` is set.

    Intended to run in a daemon thread. ``Event.wait`` returns True the
    moment the event fires (so we never wait the full interval after
    shutdown), or False on timeout. The loop emits only on the False
    branch — meaning a stop_event that's already set when the thread
    starts produces zero emissions, exactly the "drain on shutdown"
    semantics we want.

    Each emission goes through ``events.emit`` so it lands as a
    structured JSON line in CloudWatch — operators can alarm on the
    absence of heartbeat events to detect a frozen daemon during long
    quiet stretches.
    """

    while not stop_event.wait(timeout=interval_s):
        events.emit("heartbeat")


class Scheduler:
    def __init__(self, settings: Settings, store: Store) -> None:
        self._settings = settings
        self._store = store
        self._stop_event = threading.Event()
        self._client: IbkrClient | None = None
        self._account_id: str | None = None

    def request_shutdown(self) -> None:
        log.info("shutdown requested")
        self._stop_event.set()

    def run_forever(self) -> None:
        self._client = connect(should_stop_fn=self._stop_event.is_set)
        self._account_id = select_account_id(self._client)
        log.info("connected to gateway; account=%s", self._account_id)

        # Periodic liveness signal so CloudWatch can alarm on a frozen
        # daemon during long quiet stretches (waiting for entry, holding
        # through quiet periods between stop-arm and fire). Daemon thread
        # so it doesn't block shutdown if ``run_forever`` exits abruptly.
        heartbeat_thread = threading.Thread(
            target=_heartbeat_loop,
            kwargs={
                "interval_s": float(self._settings.heartbeat_interval_sec),
                "stop_event": self._stop_event,
            },
            name="bull-call-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()

        # Crash-recovery counter for the inner loop: an unexpected
        # exception in ``_run_one_session`` shouldn't kill the daemon
        # outright — that forces ASG to respawn + IBeam to re-auth + 2FA,
        # an expensive sequence for what may be a transient blip. Instead
        # we log + emit an event + back off; only after
        # ``session_error_max_consecutive`` failures in a row do we exit
        # so ASG respawns with fresh state.
        consecutive_errors = 0
        backoff_s = float(self._settings.session_error_backoff_sec)
        max_consecutive = self._settings.session_error_max_consecutive

        try:
            while not self._stop_event.is_set():
                try:
                    self._run_one_session()
                except (KeyboardInterrupt, SystemExit):
                    # Operator signal — never swallow; let it propagate so
                    # the finally-block disconnects the gateway cleanly.
                    raise
                except Exception as exc:
                    consecutive_errors += 1
                    # Both the log call and the events.emit call below are
                    # *instrumentation*; if either of them raises (broken
                    # logger, full disk, network glitch on the events
                    # logger handler) we must NOT let that defeat the
                    # crash-recovery counter / backoff / circuit breaker.
                    # Wrap each side-effect in try/except.
                    try:
                        log.exception(
                            "_run_one_session raised %s (consecutive=%d/%d)",
                            type(exc).__name__,
                            consecutive_errors, max_consecutive,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        events.emit(
                            "session_error",
                            error_type=type(exc).__name__,
                            message=str(exc),
                            consecutive=consecutive_errors,
                            max_consecutive=max_consecutive,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    if consecutive_errors >= max_consecutive:
                        try:
                            events.emit(
                                "circuit_breaker_open",
                                consecutive_errors=consecutive_errors,
                                reason="session_error_max_consecutive_reached",
                            )
                        except Exception:  # noqa: BLE001
                            pass
                        try:
                            log.error(
                                "circuit breaker open after %d consecutive "
                                "session errors; exiting so ASG can respawn",
                                consecutive_errors,
                            )
                        except Exception:  # noqa: BLE001
                            pass
                        return
                    self._stop_event.wait(timeout=backoff_s)
                else:
                    consecutive_errors = 0
        finally:
            self._stop_event.set()
            heartbeat_thread.join(timeout=5.0)
            assert self._client is not None
            disconnect(self._client)

    def _run_one_session(self) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        entry_utc = self._next_entry_time(now)
        log.info("waiting until next entry: %s UTC", entry_utc.isoformat())
        if not self._sleep_until(entry_utc):
            return

        today_et = entry_utc.astimezone(_ET).date()
        sessions = session_times(today_et)
        if sessions is None:
            log.info("not a trading day; skipping")
            self._sleep_until(entry_utc + dt.timedelta(hours=1))
            return
        close_utc = sessions.close_utc

        # Reconcile against IBKR before entry — adopts any spread that's
        # already open on the account into the local store, so the retry
        # loop won't double-open if the state DB was wiped.
        self._reconcile_with_ibkr(today_et)

        # Belt-and-suspenders: cancel any working combo orders left over
        # from a prior crashed run. Reconcile only catches FILLED positions;
        # a SIGKILL mid-fill leaves the order working at IBKR with no DDB
        # record, and the next entry attempt could submit alongside it and
        # double-fill (exceeding max_loss_usd). Yesterday's TIF=DAY orders
        # auto-cancelled at the 4 pm close, so any working combo at session
        # start is necessarily an intra-day orphan from this same calendar
        # day.
        assert self._client is not None and self._account_id is not None
        try:
            n_cancelled = cancel_orphaned_combo_orders(
                self._client, account_id=self._account_id,
            )
            if n_cancelled:
                events.emit(
                    "orphaned_orders_cancelled",
                    count=n_cancelled,
                )
        except Exception:
            log.warning(
                "orphan-order cleanup raised; continuing into entry loop",
                exc_info=True,
            )

        gate_active = self._monthly_gate_active(today_et)
        if gate_active:
            year_month = today_et.strftime("%Y-%m")
            mtd_pnl = self._store.monthly_pnl_total(year_month)
            log.warning(
                "monthly capital gate ACTIVE for %s (mtd_pnl=%.2f); "
                "skipping new entries — existing positions still managed",
                year_month, mtd_pnl,
            )
            events.emit(
                "capital_gate",
                reason="month_negative",
                year_month=year_month,
                mtd_pnl=mtd_pnl,
            )

        for symbol in self._settings.symbols:
            today_iso = today_et.isoformat()
            if self._store.today_already_opened(today_iso, symbol):
                log.info("%s already opened today; skipping", symbol)
                continue
            if gate_active:
                continue
            self._run_symbol(symbol, today_et, today_iso, close_utc)

        self._monitor_open_spreads(today_et, close_utc)

        self._sleep_until(close_utc + dt.timedelta(minutes=1))
        self._record_settlements(today_et)

    def _next_entry_time(self, now_utc: dt.datetime) -> dt.datetime:
        today_et = now_utc.astimezone(_ET).date()
        candidate = self._entry_time_for(today_et)
        if candidate is not None and candidate > now_utc and is_trading_day(today_et):
            return candidate
        next_open = next_session_open_utc(now_utc)
        next_day_et = next_open.astimezone(_ET).date()
        nxt = self._entry_time_for(next_day_et)
        if nxt is None:
            raise RuntimeError(f"no trading day found near {now_utc}")
        return nxt

    def _entry_time_for(self, day_et: dt.date) -> dt.datetime | None:
        sessions = session_times(day_et)
        if sessions is None:
            return None
        entry_naive = dt.datetime.combine(day_et, self._settings.entry_time_et, _ET)
        return entry_naive.astimezone(dt.timezone.utc)

    def _sleep_until(self, target_utc: dt.datetime) -> bool:
        """Sleep until target. Returns False if shutdown was requested."""

        while not self._stop_event.is_set():
            now = dt.datetime.now(dt.timezone.utc)
            remaining = (target_utc - now).total_seconds()
            if remaining <= 0:
                return True
            if self._stop_event.wait(timeout=min(remaining, 60.0)):
                return False
        return False

    def _monthly_gate_active(self, today_et: dt.date) -> bool:
        """Return True if month-to-date realized PnL is negative AND the
        ``monthly_stop_on_negative_pnl`` setting is enabled.

        Strict negativity — a flat month (pnl == 0) does NOT trip the gate.
        Implements R9 from docs/strategy-review.md: a single bad month
        shouldn't be able to bleed into the next, but it should stop the
        bleeding inside the same month.
        """

        if not self._settings.monthly_stop_on_negative_pnl:
            return False
        year_month = today_et.strftime("%Y-%m")
        return self._store.monthly_pnl_total(year_month) < 0.0

    def _reconcile_with_ibkr(self, today_et: dt.date) -> None:
        """Detect any spreads already open on the IBKR account today and
        ``adopt`` them into the local store so the retry loop sees them."""

        assert self._client is not None and self._account_id is not None

        existing = detect_existing_spreads(
            self._client, account_id=self._account_id, today_et=today_et,
        )
        if not existing:
            return
        today_iso = today_et.isoformat()
        now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
        from bull_call.state import DuplicateSpreadError

        for spread in existing:
            if self._store.today_already_opened(today_iso, spread.symbol):
                continue
            try:
                self._store.adopt_existing_spread(
                    date=today_iso,
                    symbol=spread.symbol,
                    long_strike=spread.long_leg.strike,
                    short_strike=spread.short_leg.strike,
                    debit=spread.entry_debit,
                    opened_at=now_iso,
                )
            except DuplicateSpreadError:
                continue
            events.emit(
                "position_adopted",
                symbol=spread.symbol,
                long_strike=spread.long_leg.strike,
                short_strike=spread.short_leg.strike,
                entry_debit=spread.entry_debit,
            )
            log.warning(
                "adopted existing IBKR position into local store: %s long=%s short=%s debit=%.2f",
                spread.symbol, spread.long_leg.strike, spread.short_leg.strike,
                spread.entry_debit,
            )

    def _run_symbol(
        self, symbol: str, today_et: dt.date, today_iso: str, close_utc: dt.datetime,
    ) -> None:
        assert self._client is not None and self._account_id is not None

        client = self._client
        account_id = self._account_id

        ratio = self._settings.min_profit_to_loss_ratio
        entry_timeout = float(self._settings.entry_timeout_sec)
        leg_timeout = float(self._settings.leg_fill_timeout_sec)
        deadline_utc = dt.datetime.combine(
            today_et, self._settings.entry_deadline_et, _ET,
        ).astimezone(dt.timezone.utc)

        # §3.7 + PR #8: signal-aware sleep + signal-aware fill polling.
        # Event.wait responds to SIGTERM during soft retries; the same
        # is_set callable is threaded into the cpapi fill-polling loops so
        # SIGTERM mid-fill exits within one poll interval instead of
        # running out the full 150s phase budget.
        stop_event = self._stop_event

        def signal_aware_sleep(seconds: float) -> None:
            stop_event.wait(timeout=seconds)

        def fetch_chain():  # type: ignore[no-untyped-def]
            return fetch_0dte_call_chain(client, symbol=symbol, today_et=today_et)

        def submit_entry(**kw):  # type: ignore[no-untyped-def]
            return submit_entry_lmt(
                client,
                account_id=account_id,
                min_profit_to_loss_ratio=ratio,
                timeout_s=entry_timeout,
                should_stop_fn=stop_event.is_set,
                **kw,
            )

        def verify_legs_balanced(*, long_leg, short_leg):  # type: ignore[no-untyped-def]
            return cp_verify_legs_balanced(
                client, account_id=account_id,
                long_leg=long_leg, short_leg=short_leg,
                timeout_s=leg_timeout,
                should_stop_fn=stop_event.is_set,
            )

        def flatten_unmatched_leg(*, long_leg, short_leg):  # type: ignore[no-untyped-def]
            cp_flatten_unmatched_leg(
                client, account_id=account_id,
                long_leg=long_leg, short_leg=short_leg,
            )

        attempt_until_filled(
            self._store,
            symbol=symbol,
            today_iso=today_iso,
            close_utc=close_utc,
            deadline_utc=deadline_utc,
            settings=self._settings,
            fetch_chain=fetch_chain,
            submit_entry=submit_entry,
            verify_legs_balanced=verify_legs_balanced,
            flatten_unmatched_leg=flatten_unmatched_leg,
            sleep_fn=signal_aware_sleep,
            should_stop_fn=stop_event.is_set,
        )

    def _monitor_open_spreads(
        self, today_et: dt.date, close_utc: dt.datetime,
    ) -> None:
        assert self._client is not None and self._account_id is not None
        opens = self._store.load_open_spreads_for_today(today_et.isoformat())
        if not opens:
            self._sleep_until(close_utc)
            return

        # Re-fetch the chain ONCE so we can map strikes back to conids without
        # spamming /secdef/info per symbol mid-session.
        chain_cache = {
            rec.symbol: fetch_0dte_call_chain(
                self._client, symbol=rec.symbol, today_et=today_et,
            )
            for rec in opens
        }

        ws = open_ws(self._client, account_id=self._account_id)
        stop_event = self._stop_event
        try:
            for rec in opens:
                if stop_event.is_set():
                    log.info("shutdown requested before monitoring %s; exiting", rec.id)
                    return
                chain = chain_cache.get(rec.symbol)
                if chain is None:
                    log.error("cannot rebuild chain for monitor; spread=%s", rec.id)
                    continue
                long_leg = chain.contracts.get(rec.long_strike)
                short_leg = chain.contracts.get(rec.short_strike)
                if not (long_leg and short_leg):
                    log.error("legs missing in chain for spread=%s", rec.id)
                    continue
                # Find underlying conid from a fresh search; cheap, returns immediately.
                under_resp = self._client.search_contract_by_symbol(
                    symbol=rec.symbol, sec_type="IND",
                )
                under_conid = int(under_resp.data[0]["conid"])
                accessor = subscribe_underlying(ws, conid=under_conid)

                breakeven = rec.long_strike + rec.debit
                client = self._client
                account_id = self._account_id

                def submit_close(**kw):  # type: ignore[no-untyped-def]
                    return submit_close_market(
                        client, account_id=account_id,
                        long_leg=long_leg, short_leg=short_leg,
                        should_stop_fn=stop_event.is_set,
                        **kw,
                    )

                def estimate_credit() -> float | None:
                    return cp_estimate_close_credit(
                        client, long_leg=long_leg, short_leg=short_leg,
                    )

                # R23a — best-effort resubscribe on a stale feed. The WS
                # client retains its own connection; we re-issue the
                # underlying subscription so a silently dropped channel can
                # come back.
                ws_ref = ws
                under_conid_ref = under_conid

                def reconnect() -> None:
                    log.warning(
                        "resubscribing underlying conid=%s after quote outage",
                        under_conid_ref,
                    )
                    subscribe_underlying(ws_ref, conid=under_conid_ref)

                outcome = monitor_stop(
                    self._store,
                    spread_id=rec.id, breakeven=breakeven,
                    settings=self._settings, close_utc=close_utc,
                    tick_stream=stream_ticks(accessor, close_utc=close_utc),
                    submit_close=submit_close,
                    estimate_close_credit=estimate_credit,
                    armed_from_recovery=rec.adopted_from_ibkr,
                    should_stop_fn=stop_event.is_set,
                    reconnect_fn=reconnect,
                )
                log.info("monitor for spread=%s ended: %s", rec.id, outcome.name)
        finally:
            try:
                ws.shutdown()
            except Exception:
                log.warning("ws.shutdown failed", exc_info=True)

    def _record_settlements(self, today_et: dt.date) -> None:
        assert self._client is not None
        today_iso = today_et.isoformat()
        opens = self._store.load_open_spreads_for_today(today_iso)
        if not opens:
            return
        for rec in opens:
            under_resp = self._client.search_contract_by_symbol(
                symbol=rec.symbol, sec_type="IND",
            )
            under_conid = int(under_resp.data[0]["conid"])
            spot = fetch_spot(self._client, conid=under_conid)
            if spot is None:
                log.error("cannot fetch settle spot for %s", rec.symbol)
                continue
            pnl = settlement_pnl(
                entry_debit=rec.debit,
                long_strike=rec.long_strike,
                short_strike=rec.short_strike,
                settle_spot=spot,
            )
            self._store.record_settlement(
                spread_id=rec.id,
                closed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                settle_value=spot,
                pnl=pnl,
            )
            events.emit(
                "spread_settled",
                spread_id=rec.id,
                symbol=rec.symbol,
                long_strike=rec.long_strike,
                short_strike=rec.short_strike,
                debit=rec.debit,
                settle_value=spot,
                pnl=pnl,
            )
            log.info("settled spread=%s settle=%.2f pnl=$%.2f", rec.id, spot, pnl)


def install_signal_handlers(scheduler: Scheduler) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: scheduler.request_shutdown())


def run_dry_run(settings: Settings) -> int:
    """Connect, fetch chain, propose a spread, log it, and exit without submitting."""

    client = connect()
    try:
        today_et = dt.datetime.now(_ET).date()
        sessions = session_times(today_et)
        if sessions is None:
            log.error("not a trading day; nothing to do")
            return 1
        for symbol in settings.symbols:
            chain = fetch_0dte_call_chain(client, symbol=symbol, today_et=today_et)
            if chain is None:
                log.error("no chain for %s", symbol)
                continue
            now = dt.datetime.now(dt.timezone.utc)
            spread = propose_trade(
                chain, settings=settings, now_utc=now, close_utc=sessions.close_utc,
            )
            if spread is None:
                log.info("[dry-run] %s: no viable spread", symbol)
            else:
                log.info(
                    "[dry-run] %s: long=%s short=%s debit=%.2f pop=%.3f breakeven=%.2f",
                    symbol, spread.long_strike, spread.short_strike,
                    spread.debit, spread.pop, spread.long_strike + spread.debit,
                )
    finally:
        disconnect(client)
    return 0
