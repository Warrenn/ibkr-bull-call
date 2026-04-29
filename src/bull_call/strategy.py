"""Strategy orchestration: propose -> open -> monitor stop -> settle.

Library-agnostic.  The IBKR-specific submit/close/credit-estimate callables
are injected — production wires in ``bull_call.cpapi.*``; tests use fakes.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum, auto

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


@dataclass(frozen=True, slots=True)
class OpenResult:
    spread_id: int
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
        "opened %s %s/%s @ %.2f (sid=%d)",
        chain.symbol, spread.long_strike, spread.short_strike,
        fill.avg_fill_price, sid,
    )
    return OpenResult(spread_id=sid, fill_price=fill.avg_fill_price)


def monitor_stop(
    store: Store,
    *,
    spread_id: int,
    breakeven: float,
    settings: Settings,
    close_utc: dt.datetime,
    tick_stream: Iterator[tuple[float, dt.datetime]],
    submit_close: SubmitClose,
    estimate_close_credit: EstimateCredit | None = None,
) -> StopOutcome:
    """Drive the stop state machine over a stream of (spot, now_utc) ticks."""

    if not settings.stop_enabled:
        log.info("stop disabled — draining tick stream until close")
        for _ in tick_stream:
            pass
        return StopOutcome.NEVER

    journal = store.stop_events(spread_id)
    state: StopState = state_from_journal_events(
        breakeven=breakeven, events=[e.event for e in journal],
    )

    for spot, now in tick_stream:
        if now >= close_utc:
            return StopOutcome.NEVER
        new_state, action = advance(
            state, spot=spot, now=now, close_utc=close_utc,
            latest_sec=settings.stop_latest_sec,
        )

        if action is Action.ARM:
            store.record_stop_event(
                spread_id=spread_id, ts=now.isoformat(), event="armed",
                spot=spot, breakeven=breakeven,
            )
            log.info("stop armed (spread=%d, spot=%.2f, breakeven=%.2f)",
                     spread_id, spot, breakeven)
            state = new_state

        elif action is Action.SUPPRESS:
            store.record_stop_event(
                spread_id=spread_id, ts=now.isoformat(), event="suppressed",
                spot=spot, breakeven=breakeven,
            )
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
            log.warning("stop fired; close fill=%.2f pnl=$%.2f", fill.avg_fill_price, pnl)
            return StopOutcome.FIRED

    return StopOutcome.NEVER


def _stop_pnl(entry_debit: float, exit_credit: float) -> float:
    return round((exit_credit - entry_debit) * 100.0, 2)


def settlement_pnl(
    *, entry_debit: float, long_strike: float, short_strike: float, settle_spot: float,
) -> float:
    """P&L (per 1 contract) of a bull call spread held to cash settlement."""

    long_intrinsic = max(0.0, settle_spot - long_strike)
    short_intrinsic = max(0.0, settle_spot - short_strike)
    payoff = long_intrinsic - short_intrinsic - entry_debit
    return round(payoff * 100.0, 2)
