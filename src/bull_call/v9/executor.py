"""v9 rebalance planner — pure function, no IBKR.

Given current positions + target portfolio + account value + last-known
prices, produce an ordered list of orders (sells first to free capital,
then buys) with whole-share rounding.

Phase 3 will add the actual IBKR submission layer; this module only
plans the trades.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from bull_call.v9.signal import TargetPortfolio
from bull_call.v9.state import V9Position


@dataclass(frozen=True, slots=True)
class RebalanceOrder:
    ticker: str
    action: str  # "BUY" | "SELL"
    quantity: int  # whole shares only
    estimated_value: float


@dataclass(frozen=True, slots=True)
class RebalancePlan:
    target_holdings: tuple[str, ...]
    orders: tuple[RebalanceOrder, ...] = ()
    warnings: tuple[str, ...] = ()


def plan_rebalance(
    *,
    current_positions: Mapping[str, V9Position],
    target_portfolio: TargetPortfolio,
    account_value: float,
    prices: Mapping[str, float],
) -> RebalancePlan:
    """Compute the order list to move current → target portfolio.

    Parameters
    ----------
    current_positions:
        Ticker → V9Position mapping for currently held SPDRs. May be
        empty (first rebalance from cash).
    target_portfolio:
        Output of ``signal.compute_target_portfolio``. Equal-weight
        top-N selection.
    account_value:
        Total dollar account value (cash + market value of positions).
        Used to size new positions.
    prices:
        Ticker → last-known price. MUST contain a price for every
        target ticker AND every currently-held ticker. Missing prices
        for held tickers won't crash but will produce inaccurate
        SELL value estimates.

    Returns
    -------
    RebalancePlan with orders ordered SELLs-first, then BUYs.
    """
    # Validate every target ticker has a price (we cannot size BUYs
    # without it). KeyError tells the caller to retry signal/price
    # fetch rather than submit a partial plan.
    for ticker in target_portfolio.holdings:
        if ticker not in prices:
            raise KeyError(ticker)

    target_set = set(target_portfolio.holdings)
    sells: list[RebalanceOrder] = []
    buys: list[RebalanceOrder] = []
    warnings: list[str] = []

    # Compute target shares for each target ticker
    target_shares: dict[str, int] = {}
    for ticker, weight in target_portfolio.weights.items():
        target_dollars = account_value * weight
        price = prices[ticker]
        target_shares[ticker] = int(target_dollars // price) if price > 0 else 0

    # SELLs: tickers held but not in target (full exit), or held with
    # excess shares (partial reduce)
    sells_cash_freed = 0.0
    for ticker, position in current_positions.items():
        current_qty = int(position.shares)
        target_qty = target_shares.get(ticker, 0) if ticker in target_set else 0
        if current_qty > target_qty:
            qty = current_qty - target_qty
            price = prices.get(ticker, position.last_price)
            sells.append(RebalanceOrder(
                ticker=ticker, action="SELL", quantity=qty,
                estimated_value=qty * price,
            ))
            sells_cash_freed += qty * price

    # BUYs: tickers in target where current shares < target shares
    buys_cash_needed = 0.0
    for ticker in target_portfolio.holdings:
        current_qty = int(current_positions[ticker].shares) if ticker in current_positions else 0
        target_qty = target_shares[ticker]
        if target_qty > current_qty:
            qty = target_qty - current_qty
            price = prices[ticker]
            buys.append(RebalanceOrder(
                ticker=ticker, action="BUY", quantity=qty,
                estimated_value=qty * price,
            ))
            buys_cash_needed += qty * price

    # Sanity check: total BUY value should not exceed reported
    # account_value (modulo small float tolerance). If it does, the
    # upstream account_value is inconsistent with the prices passed
    # in — flag for caller review.
    if buys_cash_needed > account_value * 1.005 + 1e-6:
        warnings.append(
            f"BUY total ${buys_cash_needed:,.2f} exceeds reported "
            f"account_value ${account_value:,.2f}; inputs may be inconsistent"
        )

    orders = tuple(sells) + tuple(buys)
    return RebalancePlan(
        target_holdings=target_portfolio.holdings,
        orders=orders,
        warnings=tuple(warnings),
    )
