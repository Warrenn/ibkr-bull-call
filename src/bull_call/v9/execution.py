"""Stock market-order submission via cpapi for v9 paper trading.

The SPX bot's combo-order code in ``bull_call/cpapi/execution.py`` uses
``conidex`` for multi-leg spreads with question/answer dialog handling.
ETF orders are simpler: single-leg, MKT, identified by ``conid``
directly. This module is the v9-specific stock executor — kept
separate so changes don't risk the SPX path.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ibind import OrderRequest, QuestionType

log = logging.getLogger(__name__)


_VALID_ACTIONS = ("BUY", "SELL")


_DEFAULT_ANSWERS: dict[QuestionType, bool] = {
    QuestionType.PRICE_PERCENTAGE_CONSTRAINT: True,
    QuestionType.ORDER_VALUE_LIMIT: True,
    QuestionType.MISSING_MARKET_DATA: True,
}


@dataclass(frozen=True, slots=True)
class StockFillReport:
    """Result of submitting a stock market order."""

    filled: bool
    ticker: str
    action: str  # "BUY" | "SELL"
    quantity: int
    avg_fill_price: float
    order_id: str | None


def _extract_order_id(data: Any) -> str | None:
    if not data:
        return None
    if isinstance(data, list) and data:
        return str(data[0].get("order_id") or data[0].get("orderId") or "") or None
    if isinstance(data, dict):
        return str(data.get("order_id") or data.get("orderId") or "") or None
    return None


def _await_fill(
    client: Any,
    order_id: str | None,
    timeout_s: float,
    *,
    should_stop_fn: Callable[[], bool] = lambda: False,
) -> float | None:
    """Poll ``order_status`` until Filled / terminal / timeout / shutdown.

    Returns avg fill price on Filled, ``None`` otherwise. Mirrors the
    polling cadence of ``bull_call.cpapi.execution._await_fill``.
    """
    if order_id is None:
        return None
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if should_stop_fn():
            log.info("shutdown requested while awaiting fill on %s", order_id)
            return None
        try:
            status = client.order_status(order_id=order_id)
            data = status.data or {}
        except Exception as exc:
            log.warning("order_status %s raised %s; retrying", order_id, exc)
            time.sleep(0.5)
            continue
        order_status = (data.get("order_status") or data.get("status") or "").lower()
        if order_status == "filled":
            avg = (
                data.get("average_price")
                or data.get("avgPrice")
                or data.get("avg_price")
            )
            return float(avg) if avg is not None else math.nan
        if order_status in {"cancelled", "inactive", "rejected"}:
            return None
        time.sleep(0.5)
    return None


def submit_stock_market_order(
    client: Any,
    *,
    account_id: str,
    ticker: str,
    conid: int,
    action: str,
    quantity: int,
    timeout_s: float = 60.0,
    should_stop_fn: Callable[[], bool] = lambda: False,
) -> StockFillReport:
    """Submit a single-leg market order on a stock contract.

    Parameters
    ----------
    client:
        ibind ``IbkrClient`` (or compatible fake).
    account_id:
        The IBKR account placing the order.
    ticker:
        Symbol (e.g. "XLK"). Recorded on the FillReport for logging /
        reconciliation; not used for order routing (conid is).
    conid:
        IBKR contract ID for the stock. Look up via
        ``bull_call.v9.contracts.lookup_conids``.
    action:
        "BUY" or "SELL".
    quantity:
        Whole-share quantity. Must be > 0.
    timeout_s:
        How long to poll for a fill before cancelling.
    should_stop_fn:
        Optional shutdown probe. Checked between fill polls.
    """
    if action not in _VALID_ACTIONS:
        raise ValueError(
            f"action must be one of {_VALID_ACTIONS}; got {action!r}"
        )
    if quantity <= 0:
        raise ValueError(f"quantity must be positive; got {quantity!r}")

    order = OrderRequest(
        conid=conid,
        order_type="MKT",
        side=action,
        quantity=quantity,
        tif="DAY",
        acct_id=account_id,
    )

    log.info(
        "v9 stock %s: %s %d %s (conid=%d)",
        action.lower(), action, quantity, ticker, conid,
    )
    place_resp = client.place_order(
        order_request=order, answers=_DEFAULT_ANSWERS, account_id=account_id,
    )
    order_id = _extract_order_id(place_resp.data)

    fill_price = _await_fill(
        client, order_id, timeout_s, should_stop_fn=should_stop_fn,
    )
    if fill_price is not None:
        return StockFillReport(
            filled=True,
            ticker=ticker,
            action=action,
            quantity=quantity,
            avg_fill_price=fill_price,
            order_id=order_id,
        )

    # Did not fill — cancel any working order
    if order_id is not None:
        try:
            client.cancel_order(order_id=order_id, account_id=account_id)
        except Exception as exc:
            log.warning("cancel failed for %s: %s", order_id, exc)

    return StockFillReport(
        filled=False,
        ticker=ticker,
        action=action,
        quantity=quantity,
        avg_fill_price=math.nan,
        order_id=order_id,
    )
