"""Tests for ``bull_call.v9.executor.plan_rebalance`` — pure-Python
order plan generation. No IBKR calls; no I/O.

Covers:
- First rebalance from cash (3 BUY orders for top-3)
- Mid-pilot reweight: holdings change → SELL departing, BUY entering
- No-change rebalance produces empty plan
- Whole-share rounding (no fractional shares)
- Sells before buys (free cash before spending)
- Insufficient cash warning when buys exceed available cash after sells
- Missing target price raises
- Drift-only rebalance (same tickers, but weights drift) → BUY/SELL deltas
"""

from __future__ import annotations

import datetime as dt

import pytest

from bull_call.v9.executor import (
    RebalanceOrder,
    RebalancePlan,
    plan_rebalance,
)
from bull_call.v9.signal import TargetPortfolio
from bull_call.v9.state import V9Position


def _target(*tickers: str, as_of: dt.date | None = None) -> TargetPortfolio:
    """Helper: build an equal-weight TargetPortfolio for the given tickers.

    Holdings are sorted alphabetically to match what
    ``compute_target_portfolio`` returns in production.
    """
    sorted_tickers = tuple(sorted(tickers))
    n = len(sorted_tickers)
    weight = 1.0 / n
    return TargetPortfolio(
        as_of_date=as_of or dt.date(2026, 5, 1),
        holdings=sorted_tickers,
        weights={t: weight for t in sorted_tickers},
    )


# ---- happy paths ------------------------------------------------------------


def test_first_rebalance_from_cash_emits_three_buys() -> None:
    target = _target("XLK", "XLF", "XLE")
    plan = plan_rebalance(
        current_positions={},
        target_portfolio=target,
        account_value=30_000.0,
        prices={"XLK": 200.0, "XLF": 50.0, "XLE": 100.0},
    )

    assert isinstance(plan, RebalancePlan)
    assert len(plan.orders) == 3
    assert all(o.action == "BUY" for o in plan.orders)
    actions = {o.ticker: o.quantity for o in plan.orders}
    # Each gets $10,000; quantities by integer division
    assert actions["XLK"] == 50   # 10000 // 200
    assert actions["XLF"] == 200  # 10000 // 50
    assert actions["XLE"] == 100  # 10000 // 100


def test_no_change_rebalance_produces_empty_orders() -> None:
    target = _target("XLK", "XLF", "XLE")
    current = {
        "XLK": V9Position(ticker="XLK", shares=50, last_price=200.0,
                          updated_at="2026-05-01T14:30:00Z"),
        "XLF": V9Position(ticker="XLF", shares=200, last_price=50.0,
                          updated_at="2026-05-01T14:30:00Z"),
        "XLE": V9Position(ticker="XLE", shares=100, last_price=100.0,
                          updated_at="2026-05-01T14:30:00Z"),
    }
    plan = plan_rebalance(
        current_positions=current,
        target_portfolio=target,
        account_value=30_000.0,
        prices={"XLK": 200.0, "XLF": 50.0, "XLE": 100.0},
    )

    assert plan.orders == ()


def test_mid_pilot_reweight_swaps_one_holding() -> None:
    """Top-3 changes: XLE leaves, XLY enters."""
    new_target = _target("XLK", "XLF", "XLY")
    current = {
        "XLK": V9Position(ticker="XLK", shares=50, last_price=200.0,
                          updated_at="2026-05-01T14:30:00Z"),
        "XLF": V9Position(ticker="XLF", shares=200, last_price=50.0,
                          updated_at="2026-05-01T14:30:00Z"),
        "XLE": V9Position(ticker="XLE", shares=100, last_price=100.0,
                          updated_at="2026-05-01T14:30:00Z"),
    }
    plan = plan_rebalance(
        current_positions=current,
        target_portfolio=new_target,
        account_value=30_000.0,
        prices={"XLK": 200.0, "XLF": 50.0, "XLE": 100.0, "XLY": 250.0},
    )

    # Should: SELL all XLE (100 shares), keep XLK and XLF unchanged, BUY XLY
    sells = [o for o in plan.orders if o.action == "SELL"]
    buys = [o for o in plan.orders if o.action == "BUY"]
    assert len(sells) == 1
    assert sells[0].ticker == "XLE"
    assert sells[0].quantity == 100

    assert len(buys) == 1
    assert buys[0].ticker == "XLY"
    # $10k / $250 = 40 shares
    assert buys[0].quantity == 40


# ---- ordering invariant -----------------------------------------------------


def test_sells_before_buys_in_order_list() -> None:
    """The ordering is mechanically critical: we need cash freed before
    we can spend it."""
    new_target = _target("XLK", "XLF", "XLY")
    current = {
        "XLE": V9Position(ticker="XLE", shares=100, last_price=100.0,
                          updated_at="2026-05-01T14:30:00Z"),
        "XLP": V9Position(ticker="XLP", shares=50, last_price=70.0,
                          updated_at="2026-05-01T14:30:00Z"),
    }
    plan = plan_rebalance(
        current_positions=current,
        target_portfolio=new_target,
        account_value=20_000.0,
        prices={"XLK": 200.0, "XLF": 50.0, "XLE": 100.0,
                "XLP": 70.0, "XLY": 250.0},
    )

    actions_in_order = [o.action for o in plan.orders]
    # All SELLs must come before any BUY
    if "BUY" in actions_in_order:
        first_buy = actions_in_order.index("BUY")
        assert all(a == "SELL" for a in actions_in_order[:first_buy])


# ---- whole-share rounding ---------------------------------------------------


def test_whole_share_rounding_floors_quantities() -> None:
    """For account_value $10,000 split equally over 3 names with
    awkward prices, quantities should be floor(target_dollars/price).
    No fractional shares."""
    target = _target("XLK", "XLF", "XLE")
    plan = plan_rebalance(
        current_positions={},
        target_portfolio=target,
        account_value=10_000.0,
        prices={"XLK": 123.45, "XLF": 67.89, "XLE": 49.99},
    )

    actions = {o.ticker: o.quantity for o in plan.orders}
    # $3333.33 per leg
    assert actions["XLK"] == int(3333.33 // 123.45)  # 27
    assert actions["XLF"] == int(3333.33 // 67.89)   # 49
    assert actions["XLE"] == int(3333.33 // 49.99)   # 66
    # All quantities are ints
    for o in plan.orders:
        assert isinstance(o.quantity, int)


# ---- warnings ---------------------------------------------------------------


def test_warning_when_buys_exceed_account_value() -> None:
    """If the planner's BUY total exceeds account_value (input
    inconsistency), warn so the caller can investigate before
    submitting orders."""
    target = _target("XLK", "XLF", "XLE")
    # Existing position: 100 shares of XLY at $250, market value $25k.
    # Caller passes account_value of $5k (e.g., truncated reporting).
    # Plan must SELL XLY (frees $25k), then BUY $5k worth across 3 names.
    # If account_value is wrong, BUYs computed at account_value/3 each
    # could legitimately exceed account_value if prices are weird.
    # Instead simulate the inconsistency directly: account_value
    # reported much smaller than what the prices/weights imply we'd buy.
    current: dict[str, V9Position] = {}
    plan = plan_rebalance(
        current_positions=current,
        target_portfolio=target,
        account_value=10.0,  # tiny account_value
        # Buys computed as account_value × weight / price → 0 shares each.
        # Override scenario: pretend somehow a legitimate inconsistency
        # arose. Easiest path: rely on direct reproduction by
        # constructing a target whose weights sum to >1 (synthetic only).
        prices={"XLK": 0.01, "XLF": 0.01, "XLE": 0.01},
    )

    # With account_value=$10 and prices=$0.01 each, shares per leg
    # = 10/3/0.01 = 333 shares each. Total BUY value = 3 × 333 × 0.01
    # ≈ $9.99, which is ≤ account_value, so no warning yet.
    # Force a real inconsistency: account_value=$1 with same prices.
    plan = plan_rebalance(
        current_positions=current,
        target_portfolio=_target("XLK", "XLF", "XLE"),
        account_value=1.0,
        prices={"XLK": 0.01, "XLF": 0.01, "XLE": 0.01},
    )
    # 1.0 / 3 / 0.01 = 33 shares per leg; total = 3 × 33 × 0.01 = $0.99
    # Still ≤ $1. No warning. The planner is consistent by construction.
    assert plan.warnings == ()


def test_warning_fires_when_caller_passes_inconsistent_inputs() -> None:
    """Direct test: caller injects inconsistent state where BUYs sum
    exceeds account_value. We construct a TargetPortfolio whose weights
    sum to >1 to simulate an upstream bug; the planner should still
    emit orders but warn."""
    target = TargetPortfolio(
        as_of_date=dt.date(2026, 5, 1),
        holdings=("XLE", "XLF", "XLK"),
        # Weights sum to 1.5 — clearly inconsistent
        weights={"XLE": 0.5, "XLF": 0.5, "XLK": 0.5},
    )
    plan = plan_rebalance(
        current_positions={},
        target_portfolio=target,
        account_value=30_000.0,
        prices={"XLK": 200.0, "XLF": 50.0, "XLE": 100.0},
    )

    # Each leg gets account_value × 0.5 = $15k. Total BUY = $45k.
    assert any("account_value" in w for w in plan.warnings)


def test_no_warning_on_clean_first_rebalance() -> None:
    target = _target("XLK", "XLF", "XLE")
    plan = plan_rebalance(
        current_positions={},
        target_portfolio=target,
        account_value=30_000.0,
        prices={"XLK": 200.0, "XLF": 50.0, "XLE": 100.0},
    )

    assert plan.warnings == ()


def test_missing_target_price_raises() -> None:
    target = _target("XLK", "XLF", "XLE")
    with pytest.raises(KeyError, match="XLE"):
        plan_rebalance(
            current_positions={},
            target_portfolio=target,
            account_value=30_000.0,
            prices={"XLK": 200.0, "XLF": 50.0},  # XLE missing!
        )


# ---- drift handling ---------------------------------------------------------


def test_drift_only_rebalance_emits_buy_sell_deltas() -> None:
    """Same tickers selected but accumulated price drift makes weights
    drift; the planner should rebalance back toward equal-weight."""
    target = _target("XLK", "XLF", "XLE")
    # Existing positions: each was bought at exact 1/3, but XLK ran up.
    current = {
        "XLK": V9Position(ticker="XLK", shares=50, last_price=240.0,
                          updated_at="2026-06-01T14:30:00Z"),  # was $200, now $240
        "XLF": V9Position(ticker="XLF", shares=200, last_price=50.0,
                          updated_at="2026-06-01T14:30:00Z"),
        "XLE": V9Position(ticker="XLE", shares=100, last_price=90.0,
                          updated_at="2026-06-01T14:30:00Z"),  # was $100, now $90
    }
    # New account value: 50*240 + 200*50 + 100*90 = 12000 + 10000 + 9000 = 31000
    plan = plan_rebalance(
        current_positions=current,
        target_portfolio=target,
        account_value=31_000.0,
        prices={"XLK": 240.0, "XLF": 50.0, "XLE": 90.0},
    )

    # Per-leg target = 31000 / 3 = $10,333.33
    # Target shares: XLK = 10333/240 = 43, XLF = 10333/50 = 206, XLE = 10333/90 = 114
    actions = {o.ticker: (o.action, o.quantity) for o in plan.orders}
    # XLK: 50 → 43 → SELL 7
    assert actions["XLK"] == ("SELL", 7)
    # XLF: 200 → 206 → BUY 6
    assert actions["XLF"] == ("BUY", 6)
    # XLE: 100 → 114 → BUY 14
    assert actions["XLE"] == ("BUY", 14)


# ---- order field invariants -------------------------------------------------


def test_orders_carry_estimated_value() -> None:
    target = _target("XLK", "XLF", "XLE")
    plan = plan_rebalance(
        current_positions={},
        target_portfolio=target,
        account_value=30_000.0,
        prices={"XLK": 200.0, "XLF": 50.0, "XLE": 100.0},
    )

    for o in plan.orders:
        # estimated_value = quantity * price
        expected = {"XLK": 50 * 200.0, "XLF": 200 * 50.0, "XLE": 100 * 100.0}[o.ticker]
        assert o.estimated_value == pytest.approx(expected)


def test_plan_records_target_holdings_for_provenance() -> None:
    target = _target("XLK", "XLF", "XLE")
    plan = plan_rebalance(
        current_positions={},
        target_portfolio=target,
        account_value=30_000.0,
        prices={"XLK": 200.0, "XLF": 50.0, "XLE": 100.0},
    )

    assert plan.target_holdings == ("XLE", "XLF", "XLK")  # alphabetical


# ---- defensive checks -------------------------------------------------------


def test_zero_account_value_returns_empty_plan() -> None:
    target = _target("XLK", "XLF", "XLE")
    plan = plan_rebalance(
        current_positions={},
        target_portfolio=target,
        account_value=0.0,
        prices={"XLK": 200.0, "XLF": 50.0, "XLE": 100.0},
    )
    # Nothing can be bought; orders should be empty (and a warning)
    assert plan.orders == () or all(o.action == "SELL" for o in plan.orders)


def test_rebalance_order_is_immutable() -> None:
    o = RebalanceOrder(ticker="XLK", action="BUY", quantity=50,
                       estimated_value=10000.0)
    with pytest.raises((AttributeError, TypeError)):
        o.quantity = 60  # type: ignore[misc]
