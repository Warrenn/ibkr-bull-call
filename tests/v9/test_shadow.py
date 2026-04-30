"""Tests for ``bull_call.v9.shadow`` — wraps the research-script logic so
the production signal can be cross-checked against the same code that
produced v9's backtest verdict.

The cross-check test is the most important: it asserts that
``signal.compute_target_portfolio()`` and
``shadow.research_top_n()`` produce identical top-N picks on the same
input. If this ever fails, the production signal has drifted from
research and the backtest is no longer the authoritative reference.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from bull_call.v9.signal import SPDR_UNIVERSE, compute_target_portfolio
from bull_call.v9.shadow import research_top_n


def _make_synthetic_prices(
    *,
    start: dt.date,
    n_months: int,
    monthly_returns: dict[str, list[float]],
) -> pd.DataFrame:
    """Same helper as test_signal.py — build daily-close DataFrame."""
    month_ends = pd.date_range(start=start, periods=n_months + 1, freq="BME")
    rows: list[dict[str, object]] = []
    for ticker, returns in monthly_returns.items():
        if len(returns) != n_months:
            raise ValueError(
                f"{ticker}: expected {n_months} returns, got {len(returns)}"
            )
        price = 100.0
        for i, me in enumerate(month_ends[: n_months + 1]):
            if i > 0:
                price *= 1 + returns[i - 1]
            rows.append({"date": me.date(), "ticker": ticker, "close": price})
    long_df = pd.DataFrame(rows)
    wide = long_df.pivot(index="date", columns="ticker", values="close").reset_index()
    wide.columns.name = None
    return wide


def test_research_top_n_returns_three_tickers_for_sufficient_history() -> None:
    """Basic invocation; with a clear winner set, top-3 should be them."""
    monthly = {
        "XLK": [0.02] * 14,
        "XLF": [0.018] * 14,
        "XLE": [0.016] * 14,
        "XLV": [0.0] * 14,
        "XLY": [0.0] * 14,
        "XLP": [0.0] * 14,
        "XLI": [0.0] * 14,
        "XLB": [0.0] * 14,
        "XLU": [0.0] * 14,
        "XLRE": [0.0] * 14,
        "XLC": [0.0] * 14,
    }
    prices = _make_synthetic_prices(
        start=dt.date(2024, 1, 1), n_months=14, monthly_returns=monthly
    )

    top = research_top_n(daily_prices=prices, as_of_date=prices["date"].max())

    assert top is not None
    assert set(top) == {"XLK", "XLF", "XLE"}


def test_research_top_n_returns_none_for_insufficient_history() -> None:
    monthly = {ticker: [0.01] * 5 for ticker in SPDR_UNIVERSE}
    prices = _make_synthetic_prices(
        start=dt.date(2024, 1, 1), n_months=5, monthly_returns=monthly
    )

    top = research_top_n(daily_prices=prices, as_of_date=prices["date"].max())

    assert top is None


def test_production_signal_matches_research_on_random_inputs() -> None:
    """Cross-check: production ``compute_target_portfolio`` and research
    ``research_top_n`` must produce identical top-N picks on the same
    input. Fails immediately if production code diverges from research."""
    import random

    rng = random.Random(42)

    # Build several random scenarios and assert agreement on each.
    for trial in range(10):
        monthly = {
            ticker: [rng.uniform(-0.05, 0.05) for _ in range(15)]
            for ticker in SPDR_UNIVERSE
        }
        prices = _make_synthetic_prices(
            start=dt.date(2023, 1, 1), n_months=15, monthly_returns=monthly
        )
        as_of = prices["date"].max()

        prod = compute_target_portfolio(
            daily_prices=prices,
            as_of_date=as_of,
        )
        research = research_top_n(daily_prices=prices, as_of_date=as_of)

        assert prod is not None and research is not None, f"trial {trial}"
        assert set(prod.holdings) == set(research), (
            f"trial {trial}: prod={prod.holdings}, research={research}"
        )


def test_production_and_research_pick_same_top_n_with_real_window() -> None:
    """One representative case using a 'real' Aug 2019 → Jul 2023 train-window
    style date sequence. Smaller, deterministic check."""
    monthly = {
        "XLK": [0.05, 0.04, 0.03, 0.02, 0.01, 0.0, 0.05, 0.04, 0.03, 0.02, 0.01, 0.0, 0.0, 0.0],
        "XLY": [0.04, 0.03, 0.02, 0.01, 0.0, 0.05, 0.04, 0.03, 0.02, 0.01, 0.0, 0.05, 0.0, 0.0],
        "XLC": [0.03, 0.02, 0.01, 0.0, 0.05, 0.04, 0.03, 0.02, 0.01, 0.0, 0.05, 0.04, 0.0, 0.0],
        "XLF": [0.0] * 14,
        "XLE": [0.0] * 14,
        "XLV": [-0.005] * 14,
        "XLP": [-0.01] * 14,
        "XLI": [-0.005] * 14,
        "XLB": [-0.005] * 14,
        "XLU": [-0.005] * 14,
        "XLRE": [-0.005] * 14,
    }
    prices = _make_synthetic_prices(
        start=dt.date(2022, 1, 1), n_months=14, monthly_returns=monthly
    )
    as_of = prices["date"].max()

    prod = compute_target_portfolio(daily_prices=prices, as_of_date=as_of)
    research = research_top_n(daily_prices=prices, as_of_date=as_of)

    assert prod is not None and research is not None
    assert set(prod.holdings) == set(research)
    assert set(prod.holdings) == {"XLK", "XLY", "XLC"}
