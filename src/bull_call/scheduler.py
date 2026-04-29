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
from bull_call.cpapi.execution import submit_close_market, submit_entry_lmt
from bull_call.cpapi.spot import open_ws, stream_ticks, subscribe_underlying
from bull_call.state import Store
from bull_call.strategy import (
    monitor_stop,
    open_spread,
    propose_trade,
    settlement_pnl,
)

log = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")


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
        self._client = connect()
        self._account_id = select_account_id(self._client)
        log.info("connected to gateway; account=%s", self._account_id)
        try:
            while not self._stop_event.is_set():
                self._run_one_session()
        finally:
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

        for symbol in self._settings.symbols:
            today_iso = today_et.isoformat()
            if self._store.today_already_opened(today_iso, symbol):
                log.info("%s already opened today; skipping", symbol)
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

    def _run_symbol(
        self, symbol: str, today_et: dt.date, today_iso: str, close_utc: dt.datetime,
    ) -> None:
        assert self._client is not None and self._account_id is not None

        chain = fetch_0dte_call_chain(self._client, symbol=symbol, today_et=today_et)
        if chain is None:
            log.warning("no chain available for %s", symbol)
            return
        now = dt.datetime.now(dt.timezone.utc)
        spread = propose_trade(
            chain, settings=self._settings, now_utc=now, close_utc=close_utc,
        )
        if spread is None:
            log.info("no viable spread for %s", symbol)
            return
        log.info(
            "proposed %s long=%s short=%s debit=%.2f pop=%.3f",
            symbol, spread.long_strike, spread.short_strike, spread.debit, spread.pop,
        )

        client = self._client
        account_id = self._account_id

        ratio = self._settings.min_profit_to_loss_ratio
        entry_timeout = float(self._settings.entry_timeout_sec)

        def submit_entry(**kw):  # type: ignore[no-untyped-def]
            return submit_entry_lmt(
                client,
                account_id=account_id,
                min_profit_to_loss_ratio=ratio,
                timeout_s=entry_timeout,
                **kw,
            )

        open_spread(
            self._store,
            chain=chain, spread=spread, now_utc=now, today_iso=today_iso,
            submit_entry=submit_entry,
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
        try:
            for rec in opens:
                chain = chain_cache.get(rec.symbol)
                if chain is None:
                    log.error("cannot rebuild chain for monitor; spread=%d", rec.id)
                    continue
                long_leg = chain.contracts.get(rec.long_strike)
                short_leg = chain.contracts.get(rec.short_strike)
                if not (long_leg and short_leg):
                    log.error("legs missing in chain for spread=%d", rec.id)
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
                        long_leg=long_leg, short_leg=short_leg, **kw,
                    )

                def estimate_credit() -> float | None:
                    return cp_estimate_close_credit(
                        client, long_leg=long_leg, short_leg=short_leg,
                    )

                outcome = monitor_stop(
                    self._store,
                    spread_id=rec.id, breakeven=breakeven,
                    settings=self._settings, close_utc=close_utc,
                    tick_stream=stream_ticks(accessor, close_utc=close_utc),
                    submit_close=submit_close,
                    estimate_close_credit=estimate_credit,
                )
                log.info("monitor for spread=%d ended: %s", rec.id, outcome.name)
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
            log.info("settled spread=%d settle=%.2f pnl=$%.2f", rec.id, spot, pnl)


def install_signal_handlers(scheduler: Scheduler) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: scheduler.request_shutdown())


def run_dry_run(settings: Settings, store_path: Path) -> int:
    """Connect, fetch chain, propose a spread, log it, and exit without submitting."""

    store = Store(store_path)
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
        store.close()
    return 0
