"""Combo (BAG) order submission via CPAPI.

CPAPI encodes spreads via the ``conidex`` string ``"<spread_conid>;;;<l1>/r1,<l2>/r2"``
where:
  - spread_conid is ``28812380`` for USD-denominated combos
  - positive ratio = BUY leg, negative = SELL leg

The ``ibind.IbkrClient.place_order`` method handles the question-reply cycle
that CPAPI uses to confirm warnings (price-percentage constraint, market-data
availability, etc.).
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from ibind import IbkrClient, OrderRequest, QuestionType

from bull_call.chain import OptionContract
from bull_call.execution import FillReport

log = logging.getLogger(__name__)

# IBKR's USD-denominated combo (BAG) conid — same value used by ibind's example.
_USD_SPREAD_CONID = 28812380

# Auto-confirm the routine question-prompts that CPAPI emits when placing
# combo orders from an API client.
_DEFAULT_ANSWERS: dict[QuestionType, bool] = {
    QuestionType.PRICE_PERCENTAGE_CONSTRAINT: True,
    QuestionType.ORDER_VALUE_LIMIT: True,
    QuestionType.MISSING_MARKET_DATA: True,
    QuestionType.STOP_ORDER_RISKS: True,
    QuestionType.MANDATORY_CAP_PRICE: True,
}


def _conidex(long_leg: OptionContract, short_leg: OptionContract) -> str:
    return f"{_USD_SPREAD_CONID};;;{long_leg.conid}/1,{short_leg.conid}/-1"


def _round_tick(price: float, tick: float = 0.05) -> float:
    return round(round(price / tick) * tick, 2)


# Smallest profit per share we'll tolerate after a reprice up. One tick of
# profit means max_profit = $5 per contract — enough to keep the spread on
# the right side of zero even after rounding.
_MIN_PROFIT_PER_SHARE = 0.05


def _ratio_debit_ceiling(
    width: float, min_profit_to_loss_ratio: float | None,
) -> float:
    """Highest debit per share that still satisfies the user's profit floor.

    From  max_profit / max_loss >= r  with max_loss = D, max_profit = W - D:
        (W - D) / D >= r   <=>   D <= W / (1 + r)
    Returns +inf when the ratio is unset (no constraint).
    """

    if min_profit_to_loss_ratio is None or min_profit_to_loss_ratio <= 0:
        return float("inf")
    return width / (1.0 + min_profit_to_loss_ratio)


def _safe_debit_max(
    requested: float,
    *,
    long_strike: float,
    short_strike: float,
    min_profit_to_loss_ratio: float | None = None,
) -> float:
    """Clamp the reprice ceiling so a fill never violates two invariants:

    1. ``max_profit > 0``: a fill above ``width`` would be a guaranteed loss.
    2. ``max_profit / max_loss >= min_profit_to_loss_ratio`` (if set): a fill
       above ``width / (1 + r)`` would deliver less profit per unit risk than
       the user demanded.

    The smaller of the two ceilings wins.
    """

    width = short_strike - long_strike
    width_ceiling = max(0.0, width - _MIN_PROFIT_PER_SHARE)
    ratio_ceiling = _ratio_debit_ceiling(width, min_profit_to_loss_ratio)
    ceiling = min(width_ceiling, ratio_ceiling)
    if requested > ceiling:
        log.warning(
            "reprice cap %.2f exceeds safe ceiling %.2f "
            "(width=%.2f, min_profit_to_loss=%s); clamping",
            requested, ceiling, width, min_profit_to_loss_ratio,
        )
        return ceiling
    return requested


def submit_entry_lmt(
    client: IbkrClient,
    *,
    account_id: str,
    long_leg: OptionContract,
    short_leg: OptionContract,
    debit_mid: float,
    debit_max: float,
    min_profit_to_loss_ratio: float | None = None,
    timeout_s: float = 300.0,
    reprice_step: float = 0.05,
) -> FillReport:
    """Submit a BUY combo LMT at midpoint debit; reprice once toward debit_max
    if unfilled; cancel and report unfilled if still no go.

    ``timeout_s`` is the *total* budget; we split it 50/50 across the
    initial-price phase and the post-reprice phase, so by default the order
    sits at the original limit for 2.5 min, then bumps one tick and waits
    another 2.5 min.

    The reprice ceiling is clamped so a fill never violates the configured
    ``min_profit_to_loss_ratio`` (no surprise ratio degradation on a reprice).
    """

    phase_timeout = max(1.0, timeout_s / 2.0)

    conidex = _conidex(long_leg, short_leg)
    # Cap BOTH the initial limit and the reprice ceiling at the ratio floor:
    # we never offer a price that would deliver less profit margin than the
    # user demanded, even if the chain's midpoint is above that ceiling.
    capped_mid = _safe_debit_max(
        debit_mid,
        long_strike=long_leg.strike,
        short_strike=short_leg.strike,
        min_profit_to_loss_ratio=min_profit_to_loss_ratio,
    )
    initial_price = _round_tick(capped_mid)
    if capped_mid < debit_mid:
        log.warning(
            "midpoint debit %.2f exceeds ratio ceiling; submitting at %.2f "
            "(fill unlikely unless market moves in)",
            debit_mid, initial_price,
        )
    safe_debit_max = _safe_debit_max(
        debit_max,
        long_strike=long_leg.strike,
        short_strike=short_leg.strike,
        min_profit_to_loss_ratio=min_profit_to_loss_ratio,
    )

    order = OrderRequest(
        conid=None,
        conidex=conidex,
        order_type="LMT",
        side="BUY",
        price=initial_price,
        quantity=1,
        tif="DAY",
        acct_id=account_id,
    )

    log.info("entry LMT submitting at %.2f (conidex=%s)", initial_price, conidex)
    place_resp = client.place_order(order_request=order, answers=_DEFAULT_ANSWERS, account_id=account_id)
    order_id = _extract_order_id(place_resp.data)

    fill = _await_fill(client, order_id, phase_timeout)
    if fill is not None:
        return FillReport(filled=True, avg_fill_price=fill, order_id=order_id)

    new_price = min(safe_debit_max, _round_tick(initial_price + reprice_step))
    if new_price > initial_price and order_id is not None:
        log.info("entry LMT repricing to %.2f", new_price)
        client.modify_order(
            order_id=order_id,
            order_request=OrderRequest(
                conid=None, conidex=conidex, order_type="LMT", side="BUY",
                price=new_price, quantity=1, tif="DAY", acct_id=account_id,
            ),
            answers=_DEFAULT_ANSWERS,
            account_id=account_id,
        )
        fill = _await_fill(client, order_id, phase_timeout)
        if fill is not None:
            return FillReport(filled=True, avg_fill_price=fill, order_id=order_id)

    if order_id is not None:
        try:
            client.cancel_order(order_id=order_id, account_id=account_id)
        except Exception as exc:
            log.warning("cancel failed for %s: %s", order_id, exc)
    log.warning("entry LMT not filled within budget; cancelled")
    return FillReport(filled=False, avg_fill_price=math.nan, order_id=order_id)


def submit_close_market(
    client: IbkrClient,
    *,
    account_id: str,
    long_leg: OptionContract,
    short_leg: OptionContract,
    timeout_s: float = 15.0,
) -> FillReport:
    """Close the spread at market: SELL long, BUY back short, atomically."""

    conidex = _conidex(long_leg, short_leg)
    order = OrderRequest(
        conid=None,
        conidex=conidex,
        order_type="MKT",
        side="SELL",  # selling the spread = unwinding it
        quantity=1,
        tif="DAY",
        acct_id=account_id,
    )
    log.warning("close MKT submitting (conidex=%s)", conidex)
    place_resp = client.place_order(order_request=order, answers=_DEFAULT_ANSWERS, account_id=account_id)
    order_id = _extract_order_id(place_resp.data)

    fill = _await_fill(client, order_id, phase_timeout)
    if fill is not None:
        return FillReport(filled=True, avg_fill_price=fill, order_id=order_id)
    log.error("close MKT did not fill within %ds; order left working", timeout_s)
    return FillReport(filled=False, avg_fill_price=math.nan, order_id=order_id)


def _qty_for_conid(client: IbkrClient, *, account_id: str, conid: int) -> int:
    """Return the signed position quantity for ``conid`` on ``account_id``.

    0 if there's no open position. ibind exposes ``positions_by_conid`` which
    returns a list (one entry for the asked conid, or empty).
    """

    try:
        resp = client.positions_by_conid(account_id=account_id, conid=str(conid))
    except Exception as exc:
        log.warning("positions_by_conid(%d) failed: %s", conid, exc)
        return 0
    rows = resp.data or []
    total = 0
    for row in rows:
        # IBKR returns float, but option positions are always whole contracts.
        total += int(round(float(row.get("position", 0))))
    return total


def verify_legs_balanced(
    client: IbkrClient,
    *,
    account_id: str,
    long_leg: OptionContract,
    short_leg: OptionContract,
    timeout_s: float = 30.0,
    poll_interval_s: float = 2.0,
) -> bool:
    """Poll until both legs of the spread match (long: +1, short: -1).

    Returns True if balanced within the timeout, else False (caller should
    flatten the unmatched leg).
    """

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        long_qty = _qty_for_conid(client, account_id=account_id, conid=long_leg.conid)
        short_qty = _qty_for_conid(client, account_id=account_id, conid=short_leg.conid)
        if long_qty == 1 and short_qty == -1:
            return True
        time.sleep(poll_interval_s)
    return False


def flatten_unmatched_leg(
    client: IbkrClient,
    *,
    account_id: str,
    long_leg: OptionContract,
    short_leg: OptionContract,
) -> None:
    """Close any single-leg position left dangling after a leg-out at MKT.

    No-op if both legs are balanced or both are flat.  If only one leg has
    a non-zero position, submit a MARKET order on that single leg to close.
    """

    long_qty = _qty_for_conid(client, account_id=account_id, conid=long_leg.conid)
    short_qty = _qty_for_conid(client, account_id=account_id, conid=short_leg.conid)

    if long_qty != 0 and short_qty == 0:
        log.error(
            "leg-out: long conid=%d qty=%d filled but short conid=%d not filled; "
            "submitting MKT close for the long leg",
            long_leg.conid, long_qty, short_leg.conid,
        )
        order = OrderRequest(
            conid=str(long_leg.conid),
            side="SELL" if long_qty > 0 else "BUY",
            quantity=abs(long_qty),
            order_type="MKT",
            tif="DAY",
            acct_id=account_id,
        )
        client.place_order(order_request=order, answers=_DEFAULT_ANSWERS, account_id=account_id)
        return

    if short_qty != 0 and long_qty == 0:
        log.error(
            "leg-out: short conid=%d qty=%d filled but long conid=%d not filled; "
            "submitting MKT close for the short leg",
            short_leg.conid, short_qty, long_leg.conid,
        )
        order = OrderRequest(
            conid=str(short_leg.conid),
            side="BUY" if short_qty < 0 else "SELL",
            quantity=abs(short_qty),
            order_type="MKT",
            tif="DAY",
            acct_id=account_id,
        )
        client.place_order(order_request=order, answers=_DEFAULT_ANSWERS, account_id=account_id)
        return

    log.info(
        "no leg-out detected (long_qty=%d short_qty=%d); nothing to flatten",
        long_qty, short_qty,
    )


def _extract_order_id(data: Any) -> str | None:
    if not data:
        return None
    if isinstance(data, list) and data:
        return str(data[0].get("order_id") or data[0].get("orderId") or "") or None
    if isinstance(data, dict):
        return str(data.get("order_id") or data.get("orderId") or "") or None
    return None


def _await_fill(client: IbkrClient, order_id: str | None, timeout_s: float) -> float | None:
    if order_id is None:
        return None
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            status = client.order_status(order_id=order_id)
            data = status.data or {}
        except Exception as exc:
            log.warning("order_status %s raised %s; retrying", order_id, exc)
            time.sleep(0.5)
            continue
        order_status = (data.get("order_status") or data.get("status") or "").lower()
        if order_status in {"filled"}:
            avg = data.get("average_price") or data.get("avgPrice") or data.get("avg_price")
            return float(avg) if avg is not None else math.nan
        if order_status in {"cancelled", "inactive", "rejected"}:
            return None
        time.sleep(0.5)
    return None
