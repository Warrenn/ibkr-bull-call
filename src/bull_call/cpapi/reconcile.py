"""Detect existing IBKR positions on startup so the bot doesn't double-open.

If the state DB is empty (fresh deploy, table wiped, etc.) but IBKR still
has a position from before, the entry-retry loop would otherwise try to
open a SECOND spread.  ``detect_existing_spreads`` queries the IBKR
account, finds any 0DTE bull-call shapes (one long call + one short call
on the same underlying with long_strike < short_strike), and returns
them so the scheduler can adopt them into the local Store.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

from ibind import IbkrClient

from bull_call.chain import OptionContract

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ExistingSpread:
    symbol: str
    long_leg: OptionContract
    short_leg: OptionContract
    entry_debit: float


def detect_existing_spreads(
    client: IbkrClient,
    *,
    account_id: str,
    today_et: dt.date,
) -> list[ExistingSpread]:
    """Return all 0DTE bull call spreads currently open on ``account_id``.

    Looks for matched (long, short) pairs of CALL options on the same
    underlying, with maturity == today, and long_strike < short_strike.
    """

    today_str = today_et.strftime("%Y%m%d")

    try:
        resp = client.positions(account_id=account_id, page_id=0)
    except Exception as exc:
        log.warning("positions() failed during reconcile: %s", exc)
        return []
    raw = resp.data or []

    # Filter to today's SPXW CALL options. Hard-gate on tradingClass to refuse
    # SPX (AM-settled monthly) — those have different settlement semantics
    # and the bot's monitor / settlement logic assumes SPXW (PM-settled,
    # 4 pm ET). See strategy-review.md §3.2.
    candidates: list[dict[str, Any]] = []
    for row in raw:
        if (row.get("assetClass") or "").upper() != "OPT":
            continue
        if (row.get("putOrCall") or "").upper() != "C":
            continue
        trading_class = (row.get("tradingClass") or row.get("contractClass") or "").upper()
        if trading_class != "SPXW":
            log.warning(
                "skipping non-SPXW position during reconcile: ticker=%s tradingClass=%r",
                row.get("ticker"), trading_class,
            )
            continue
        # IBKR exposes maturity under different keys depending on version;
        # try the common ones.
        maturity = (
            row.get("lastTradingDay")
            or row.get("expirationDate")
            or row.get("maturityDate")
            or ""
        )
        if str(maturity)[:8] != today_str:
            continue
        candidates.append(row)

    # Group by underlying ticker.
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        ticker = (row.get("ticker") or row.get("underlying") or "").upper()
        if not ticker:
            continue
        by_ticker.setdefault(ticker, []).append(row)

    spreads: list[ExistingSpread] = []
    for ticker, rows in by_ticker.items():
        longs = [r for r in rows if _signed_qty(r) > 0]
        shorts = [r for r in rows if _signed_qty(r) < 0]
        if len(longs) != 1 or len(shorts) != 1:
            log.info(
                "skipping reconcile for %s: %d long(s), %d short(s) — not a bull call shape",
                ticker, len(longs), len(shorts),
            )
            continue
        long_p, short_p = longs[0], shorts[0]
        long_strike = float(long_p["strike"])
        short_strike = float(short_p["strike"])
        if long_strike >= short_strike:
            log.info(
                "skipping reconcile for %s: long_strike %s >= short_strike %s",
                ticker, long_strike, short_strike,
            )
            continue
        long_avg = float(long_p.get("avgCost") or 0.0)
        short_avg = float(short_p.get("avgCost") or 0.0)
        # IBKR signed-cost invariant: long position's avgCost is positive
        # (premium paid), short position's avgCost is negative (premium
        # received). If the API ever emits unsigned costs, our debit
        # arithmetic would silently mis-compute. Refuse to adopt rather
        # than guess. See strategy-review.md §3.6.
        if long_avg <= 0:
            log.error(
                "reconcile: rejecting %s — long leg conid=%d has non-positive "
                "avgCost=%.4f (expected > 0). Refusing to adopt with ambiguous cost basis.",
                ticker, int(long_p["conid"]), long_avg,
            )
            continue
        if short_avg >= 0:
            log.error(
                "reconcile: rejecting %s — short leg conid=%d has non-negative "
                "avgCost=%.4f (expected < 0). Refusing to adopt with ambiguous cost basis.",
                ticker, int(short_p["conid"]), short_avg,
            )
            continue
        # Net entry debit per share = long_avg + short_avg (short already
        # carries its negative sign). This works correctly only when the
        # signed-cost invariant above holds.
        entry_debit = long_avg + short_avg
        spreads.append(ExistingSpread(
            symbol=ticker,
            long_leg=OptionContract(
                strike=long_strike,
                conid=int(long_p["conid"]),
                right="C",
                expiry=today_str,
            ),
            short_leg=OptionContract(
                strike=short_strike,
                conid=int(short_p["conid"]),
                right="C",
                expiry=today_str,
            ),
            entry_debit=entry_debit,
        ))
        log.warning(
            "found existing IBKR spread for %s: long=%s short=%s debit=%.2f",
            ticker, long_strike, short_strike, entry_debit,
        )
    return spreads


def _signed_qty(row: dict[str, Any]) -> float:
    """Return the position quantity with sign (long > 0, short < 0)."""

    return float(row.get("position") or 0.0)
