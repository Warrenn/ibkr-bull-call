"""Strategy orchestration: propose -> open -> monitor stop -> settle.

Library-agnostic.  The IBKR-specific submit/close/credit-estimate callables
are injected — production wires in ``bull_call.cpapi.*``; tests use fakes.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum, auto

from bull_call import events
from bull_call.chain import ChainSnapshot
from bull_call.config import Settings
from bull_call.execution import FillReport
from bull_call.pricing import pop_bs, years_to_session_close
from bull_call.state import Store
from bull_call.stop import Action, StopState, advance, state_from_journal_events
from bull_call.strikes import Spread, select_spread

log = logging.getLogger(__name__)


class StopOutcome(Enum):
    NEVER = auto()        # session ended without a stop event after entry
    FIRED = auto()        # stop fired and close order was submitted
    SUPPRESSED = auto()   # stop would have fired but in suppression window
    UNECONOMIC = auto()   # stop would have fired but realized loss would exceed
                          # the max-loss-if-held; let it ride to expiry instead
    OUTAGE_FLATTEN = auto()  # R23a: data feed went silent past max-blind
                             # window; emergency MKT flatten regardless of
                             # state (bypasses the uneconomic-credit guard)


@dataclass(frozen=True, slots=True)
class OpenResult:
    spread_id: str  # composite "{date}#{symbol}" — see state._spread_id
    fill_price: float


SubmitEntry = Callable[..., FillReport]
SubmitClose = Callable[..., FillReport]
EstimateCredit = Callable[..., "float | None"]


def propose_trade(
    chain: ChainSnapshot,
    *,
    settings: Settings,
    now_utc: dt.datetime,
    close_utc: dt.datetime,
) -> Spread | None:
    """Pure: pick a spread from a chain snapshot using the configured rules."""

    t_years = years_to_session_close(now_utc, close_utc)

    def pop_fn(breakeven: float) -> float:
        return pop_bs(
            spot=chain.spot,
            breakeven=breakeven,
            iv=chain.atm_iv,
            time_years=t_years,
            r=settings.risk_free_rate,
        )

    return select_spread(
        chain.quotes,
        max_loss_usd=settings.max_loss_usd,
        pop_fn=pop_fn,
        pop_threshold=settings.pop_threshold,
    )


FetchChain = Callable[[], "ChainSnapshot | None"]
VerifyLegsBalanced = Callable[..., bool]
FlattenUnmatchedLeg = Callable[..., None]


def attempt_until_filled(
    store: Store,
    *,
    symbol: str,
    today_iso: str,
    close_utc: dt.datetime,
    deadline_utc: dt.datetime,
    settings: Settings,
    fetch_chain: FetchChain,
    submit_entry: SubmitEntry,
    verify_legs_balanced: VerifyLegsBalanced,
    flatten_unmatched_leg: FlattenUnmatchedLeg,
    now_fn: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc),
    sleep_fn: Callable[[float], None] = time.sleep,
    should_stop_fn: Callable[[], bool] = lambda: False,
    soft_retry_delay_s: float = 60.0,
) -> OpenResult | None:
    """Keep submitting entry orders until one fills, deadline passes, or a
    leg-out forces us to flatten.

    Each iteration re-fetches the chain (quotes have moved during the prior
    5-min limit cycle) and re-runs the strike selector — selection may pick
    different strikes if IV / spot have drifted.

    A *soft* retry (no chain available, or no viable spread today) waits
    ``soft_retry_delay_s`` (default 60s) so we don't hammer the gateway.
    A *hard* retry (limit order cancelled by us after the 5-min budget)
    proceeds immediately — the cancel cycle is itself the delay.

    ``sleep_fn`` and ``should_stop_fn`` together support clean shutdown:
    in production, scheduler wires ``sleep_fn = stop_event.wait`` (returns
    early on signal) and ``should_stop_fn = stop_event.is_set`` (checked
    each iteration). The retry loop honours SIGTERM mid-soft-retry instead
    of blocking up to ``soft_retry_delay_s`` seconds before noticing.

    Stops:
      - filled and legs balanced -> return OpenResult (only outcome that
        creates a SQLite spread row)
      - filled but legs not balanced within ``settings.leg_fill_timeout_sec``
        -> flatten the orphan leg at MKT, return None, do NOT retry today
      - now >= deadline_utc -> return None (no trade today)
      - should_stop_fn() returns True -> return None (graceful shutdown)
    """

    while True:
        if should_stop_fn():
            log.info("shutdown requested for %s entry loop; exiting", symbol)
            return None

        now = now_fn()
        if now >= deadline_utc:
            log.info("entry deadline reached for %s; no trade today", symbol)
            return None

        chain = fetch_chain()
        if chain is None:
            log.info("no chain for %s; soft-retrying in %.0fs", symbol, soft_retry_delay_s)
            sleep_fn(soft_retry_delay_s)
            if should_stop_fn():
                return None
            continue

        spread = propose_trade(chain, settings=settings, now_utc=now, close_utc=close_utc)
        if spread is None:
            log.info("no viable spread for %s; soft-retrying in %.0fs",
                     symbol, soft_retry_delay_s)
            sleep_fn(soft_retry_delay_s)
            if should_stop_fn():
                return None
            continue

        long_leg = chain.contracts[spread.long_strike]
        short_leg = chain.contracts[spread.short_strike]
        log.info(
            "attempting %s long=%s short=%s debit=%.2f pop=%.3f",
            symbol, spread.long_strike, spread.short_strike, spread.debit, spread.pop,
        )
        fill = submit_entry(
            long_leg=long_leg,
            short_leg=short_leg,
            debit_mid=spread.debit,
            debit_max=spread.debit + 0.20,
        )
        if not fill.filled:
            log.info("attempt for %s did not fill within budget; immediate retry", symbol)
            continue

        if not verify_legs_balanced(long_leg=long_leg, short_leg=short_leg):
            log.error(
                "leg-out detected for %s after fill report; flattening any open leg",
                symbol,
            )
            flatten_unmatched_leg(long_leg=long_leg, short_leg=short_leg)
            return None  # do NOT retry; treat the symbol as done for the day

        sid = store.record_open(
            date=today_iso,
            symbol=symbol,
            long_strike=spread.long_strike,
            short_strike=spread.short_strike,
            debit=fill.avg_fill_price,
            opened_at=now.isoformat(),
        )
        events.emit(
            "spread_opened",
            spread_id=sid,
            symbol=symbol,
            long_strike=spread.long_strike,
            short_strike=spread.short_strike,
            debit=fill.avg_fill_price,
            breakeven=spread.long_strike + fill.avg_fill_price,
            pop=spread.pop,
        )
        log.info(
            "filled %s %s/%s @ %.2f (sid=%s)",
            symbol, spread.long_strike, spread.short_strike,
            fill.avg_fill_price, sid,
        )
        return OpenResult(spread_id=sid, fill_price=fill.avg_fill_price)


def open_spread(
    store: Store,
    *,
    chain: ChainSnapshot,
    spread: Spread,
    now_utc: dt.datetime,
    today_iso: str,
    submit_entry: SubmitEntry,
) -> OpenResult | None:
    """Submit the entry combo and persist."""

    long_leg = chain.contracts[spread.long_strike]
    short_leg = chain.contracts[spread.short_strike]

    fill = submit_entry(
        long_leg=long_leg,
        short_leg=short_leg,
        debit_mid=spread.debit,
        debit_max=spread.debit + 0.20,
    )
    if not fill.filled:
        log.warning("entry combo for %s not filled", chain.symbol)
        return None

    sid = store.record_open(
        date=today_iso,
        symbol=chain.symbol,
        long_strike=spread.long_strike,
        short_strike=spread.short_strike,
        debit=fill.avg_fill_price,
        opened_at=now_utc.isoformat(),
    )
    log.info(
        "opened %s %s/%s @ %.2f (sid=%s)",
        chain.symbol, spread.long_strike, spread.short_strike,
        fill.avg_fill_price, sid,
    )
    return OpenResult(spread_id=sid, fill_price=fill.avg_fill_price)


def monitor_stop(
    store: Store,
    *,
    spread_id: str,
    breakeven: float,
    settings: Settings,
    close_utc: dt.datetime,
    tick_stream: Iterator[tuple[float | None, dt.datetime]],
    submit_close: SubmitClose,
    estimate_close_credit: EstimateCredit | None = None,
    armed_from_recovery: bool = False,
    should_stop_fn: Callable[[], bool] = lambda: False,
    reconnect_fn: Callable[[], None] | None = None,
) -> StopOutcome:
    """Drive the stop state machine over a stream of (spot, now_utc) ticks.

    ``should_stop_fn`` is checked at the top of every tick iteration so a
    SIGTERM/SIGINT received mid-session can short-circuit the monitor loop
    promptly — without it, the loop blocks until ``close_utc`` (potentially
    hours) and the daemon refuses to exit. Returns ``StopOutcome.NEVER`` on
    shutdown so the caller treats it as "no stop fired this session."

    The stream may yield ``(None, now_utc)`` "silence" sentinels emitted by
    the polling layer when no fresh tick arrived within the poll window.
    These drive R23a (data-outage emergency flatten):

      - If silence persists for ``monitoring_quote_grace_sec`` total, emit
        ``quote_outage`` once and (if provided) call ``reconnect_fn`` —
        retried at multiples of grace_sec, capped at
        ``monitoring_reconnect_max_attempts``.
      - If silence persists for ``monitoring_quote_max_blind_sec`` total,
        submit a SELL combo MKT emergency flatten, record the close with
        ``exit_kind='OUTAGE_FLATTEN'``, and return ``StopOutcome.OUTAGE_FLATTEN``.

    The emergency flatten path bypasses the uneconomic-credit guard
    (per invariants I3 / I9 in docs/strategy-review.md) — sitting blind
    on an open 0DTE position is more expensive than a bad MKT fill.
    """

    if not settings.stop_enabled:
        log.info("stop disabled — draining tick stream until close")
        for _ in tick_stream:
            if should_stop_fn():
                return StopOutcome.NEVER
        return StopOutcome.NEVER

    journal = store.stop_events(spread_id)
    state: StopState = state_from_journal_events(
        breakeven=breakeven,
        events=[e.event for e in journal],
        armed_from_recovery=armed_from_recovery,
    )

    grace_sec = float(settings.monitoring_quote_grace_sec)
    max_blind_sec = float(settings.monitoring_quote_max_blind_sec)
    max_reconnects = settings.monitoring_reconnect_max_attempts
    last_fresh_now: dt.datetime | None = None
    outage_event_emitted = False
    reconnects_done = 0

    for spot, now in tick_stream:
        if should_stop_fn():
            log.info("shutdown requested for spread=%s monitor; exiting", spread_id)
            return StopOutcome.NEVER
        if now >= close_utc:
            return StopOutcome.NEVER

        if spot is None:
            # Silent sentinel from the poller. Track the outage.
            if last_fresh_now is None:
                last_fresh_now = now
                continue
            blind_sec = (now - last_fresh_now).total_seconds()
            if blind_sec >= max_blind_sec:
                return _emergency_flatten(
                    store, spread_id=spread_id, now=now,
                    breakeven=breakeven, blind_sec=blind_sec,
                    submit_close=submit_close,
                )
            if blind_sec >= grace_sec * (reconnects_done + 1) \
                    and reconnects_done < max_reconnects:
                if not outage_event_emitted:
                    outage_event_emitted = True
                    events.emit(
                        "quote_outage",
                        spread_id=spread_id, blind_sec=blind_sec,
                        breakeven=breakeven,
                    )
                    log.warning(
                        "quote outage on spread=%s; blind_sec=%.1f; "
                        "starting reconnect attempts",
                        spread_id, blind_sec,
                    )
                if reconnect_fn is not None:
                    try:
                        reconnect_fn()
                    except Exception:
                        log.warning(
                            "reconnect attempt failed for spread=%s",
                            spread_id, exc_info=True,
                        )
                reconnects_done += 1
            continue

        # Fresh tick — reset outage tracking.
        last_fresh_now = now
        outage_event_emitted = False
        reconnects_done = 0

        new_state, action = advance(
            state, spot=spot, now=now, close_utc=close_utc,
            latest_sec=settings.stop_latest_sec,
        )

        if action is Action.ARM:
            store.record_stop_event(
                spread_id=spread_id, ts=now.isoformat(), event="armed",
                spot=spot, breakeven=breakeven,
            )
            events.emit("stop_armed", spread_id=spread_id, spot=spot, breakeven=breakeven)
            log.info("stop armed (spread=%s, spot=%.2f, breakeven=%.2f)",
                     spread_id, spot, breakeven)
            state = new_state

        elif action is Action.SUPPRESS:
            store.record_stop_event(
                spread_id=spread_id, ts=now.isoformat(), event="suppressed",
                spot=spot, breakeven=breakeven,
            )
            events.emit("stop_suppressed", spread_id=spread_id, spot=spot, breakeven=breakeven)
            log.warning("stop suppressed near close; letting settlement run")
            return StopOutcome.SUPPRESSED

        elif action is Action.FIRE:
            estimated_credit: float | None = None
            if estimate_close_credit is not None:
                estimated_credit = estimate_close_credit()
            if estimated_credit is not None and estimated_credit <= 0:
                store.record_stop_event(
                    spread_id=spread_id, ts=now.isoformat(), event="uneconomic",
                    spot=spot, breakeven=breakeven,
                )
                events.emit(
                    "stop_uneconomic",
                    spread_id=spread_id, spot=spot, breakeven=breakeven,
                    estimated_close_credit=estimated_credit,
                )
                log.warning(
                    "stop fire skipped — estimated close credit %.2f ≤ 0; "
                    "would realize loss greater than (or equal to) max-held loss",
                    estimated_credit,
                )
                return StopOutcome.UNECONOMIC

            store.record_stop_event(
                spread_id=spread_id, ts=now.isoformat(), event="fired",
                spot=spot, breakeven=breakeven,
            )
            fill = submit_close()
            pnl = _stop_pnl(store.get_spread(spread_id).debit, fill.avg_fill_price)
            store.record_close(
                spread_id=spread_id, closed_at=now.isoformat(),
                exit_kind="STOP", pnl=pnl,
            )
            events.emit(
                "spread_closed",
                spread_id=spread_id, exit_kind="STOP",
                close_fill=fill.avg_fill_price, pnl=pnl,
                spot=spot, breakeven=breakeven,
            )
            log.warning("stop fired; close fill=%.2f pnl=$%.2f", fill.avg_fill_price, pnl)
            return StopOutcome.FIRED

    return StopOutcome.NEVER


def _stop_pnl(entry_debit: float, exit_credit: float) -> float:
    return round((exit_credit - entry_debit) * 100.0, 2)


def _emergency_flatten(
    store: Store,
    *,
    spread_id: str,
    now: dt.datetime,
    breakeven: float,
    blind_sec: float,
    submit_close: SubmitClose,
) -> StopOutcome:
    """R23a — emergency MKT flatten triggered by a data-feed outage.

    Bypasses the uneconomic-credit guard. Records the close with
    ``exit_kind='OUTAGE_FLATTEN'`` (mapped to ``status='STOPPED'``).
    """

    log.error(
        "data-outage emergency flatten on spread=%s; blind_sec=%.1f",
        spread_id, blind_sec,
    )
    fill = submit_close()
    pnl = _stop_pnl(store.get_spread(spread_id).debit, fill.avg_fill_price)
    store.record_close(
        spread_id=spread_id, closed_at=now.isoformat(),
        exit_kind="OUTAGE_FLATTEN", pnl=pnl,
    )
    events.emit(
        "spread_closed",
        spread_id=spread_id, exit_kind="OUTAGE_FLATTEN",
        reason="data_outage_flatten", blind_sec=blind_sec,
        close_fill=fill.avg_fill_price, pnl=pnl, breakeven=breakeven,
    )
    return StopOutcome.OUTAGE_FLATTEN


def settlement_pnl(
    *, entry_debit: float, long_strike: float, short_strike: float, settle_spot: float,
) -> float:
    """P&L (per 1 contract) of a bull call spread held to cash settlement."""

    long_intrinsic = max(0.0, settle_spot - long_strike)
    short_intrinsic = max(0.0, settle_spot - short_strike)
    payoff = long_intrinsic - short_intrinsic - entry_debit
    return round(payoff * 100.0, 2)
