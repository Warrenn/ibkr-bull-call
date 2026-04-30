"""Tests for ``bull_call.v9.signal`` — pure-Python target-portfolio
selection from daily SPDR closes.

Covers happy path, tie-handling, NaN handling, insufficient lookback,
insufficient universe, as-of-date cases, and equal-weight invariants.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from bull_call.v9.signal import (
    SPDR_UNIVERSE,
    TargetPortfolio,
    compute_target_portfolio,
)


def _make_synthetic_prices(
    *,
    start: dt.date,
    n_months: int,
    monthly_returns: dict[str, list[float]],
) -> pd.DataFrame:
    """Build a daily-close DataFrame from synthetic monthly returns.

    Each ticker gets ``n_months`` of compounded monthly returns starting
    from price 100; intra-month days hold the prior month-end price so
    month-end resampling produces the intended return sequence.
    """

    # Generate month-end dates using business-month-end ('BME')
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


def test_happy_path_identifies_top_3_by_momentum() -> None:
    """Three winners with high 12-month return + skip-1 month should be picked."""
    # 13 months of returns: months 0-11 are the 12-month lookback window;
    # month 12 is the skipped most-recent month.
    monthly = {
        # Winners: cumulative ~ +24% over 12mo
        "XLK": [0.02] * 12 + [-0.5],   # last month is huge negative — proves "skip 1" works
        "XLF": [0.018] * 12 + [-0.5],
        "XLE": [0.016] * 12 + [-0.5],
        # Losers: flat or negative
        "XLV": [0.0] * 12 + [+0.5],    # last month huge positive — should still rank low
        "XLY": [-0.005] * 12 + [+0.5],
        "XLP": [-0.01] * 12 + [+0.5],
        "XLI": [-0.005] * 12 + [+0.0],
        "XLB": [0.0] * 12 + [+0.0],
        "XLU": [0.005] * 12 + [+0.0],
        "XLRE": [0.005] * 12 + [+0.0],
        "XLC": [0.005] * 12 + [+0.0],
    }
    prices = _make_synthetic_prices(
        start=dt.date(2024, 1, 1), n_months=13, monthly_returns=monthly
    )

    target = compute_target_portfolio(
        daily_prices=prices,
        as_of_date=prices["date"].max(),
    )

    assert target is not None
    assert set(target.holdings) == {"XLK", "XLF", "XLE"}
    assert sum(target.weights.values()) == pytest.approx(1.0)
    assert all(w == pytest.approx(1 / 3) for w in target.weights.values())


def test_skip_recent_month_excludes_last_month_returns() -> None:
    """If month-12 (last) is the only differentiator, ranking should
    NOT respond to it (skip_recent_months=1 default)."""
    monthly = {
        # All identical for 12 months; last month makes "winner1" worst
        "XLK": [0.01] * 12 + [-0.99],
        "XLF": [0.01] * 12 + [-0.99],
        "XLE": [0.01] * 12 + [-0.99],
        # All identical for 12 months; last month makes "loser1" best
        "XLV": [0.0] * 12 + [+0.5],
        "XLY": [0.0] * 12 + [+0.5],
        "XLP": [0.0] * 12 + [+0.5],
        "XLI": [0.0] * 12 + [+0.0],
        "XLB": [0.0] * 12 + [+0.0],
        "XLU": [0.0] * 12 + [+0.0],
        "XLRE": [0.0] * 12 + [+0.0],
        "XLC": [0.0] * 12 + [+0.0],
    }
    prices = _make_synthetic_prices(
        start=dt.date(2024, 1, 1), n_months=13, monthly_returns=monthly
    )

    target = compute_target_portfolio(
        daily_prices=prices,
        as_of_date=prices["date"].max(),
    )

    assert target is not None
    # XLK/XLF/XLE had 0.01 over 12 lookback months → they should be top-3,
    # NOT the loser group with 0% return regardless of last-month boost.
    assert set(target.holdings) == {"XLK", "XLF", "XLE"}


def test_insufficient_lookback_returns_none() -> None:
    """Fewer than ``lookback_months + skip_recent_months`` months of
    history should yield None (caller decides what to do)."""
    monthly = {ticker: [0.01] * 5 for ticker in SPDR_UNIVERSE}  # only 5 months
    prices = _make_synthetic_prices(
        start=dt.date(2024, 1, 1), n_months=5, monthly_returns=monthly
    )

    target = compute_target_portfolio(
        daily_prices=prices,
        as_of_date=prices["date"].max(),
    )

    assert target is None


def test_late_listing_ticker_works_when_lookback_window_is_after_listing() -> None:
    """A late-listed ticker (NaN before some date, valid after) should
    not break the signal once the lookback window is entirely after the
    listing date.

    Note: matching the research-script ``_to_monthly_returns`` behavior,
    monthly rows with ANY NaN are dropped, so a late-listing ticker
    forces caller to ensure the lookback window is past the listing date.
    """
    # 24 months of data. XLC has data only from month 8 onward (mimics XLC
    # listing 2018-06 if start were ~2017-10). Use as_of_date so that
    # the lookback window iloc[end-12:end-1] is entirely in the post-
    # listing era.
    n_months = 24
    monthly = {
        ticker: [0.01] * n_months
        for ticker in SPDR_UNIVERSE
        if ticker != "XLC"
    }
    monthly["XLC"] = [0.0] * n_months  # placeholder, overwritten below
    prices = _make_synthetic_prices(
        start=dt.date(2022, 1, 1), n_months=n_months, monthly_returns=monthly
    )
    # NaN out XLC for the early rows; keep its later rows as-is (valid 100+1.0%)
    prices.loc[prices["date"] < pd.Timestamp("2022-09-01").date(), "XLC"] = float("nan")

    target = compute_target_portfolio(
        daily_prices=prices,
        as_of_date=prices["date"].max(),
    )

    # All non-XLC tickers have identical 1% returns; ranking is alphabetical.
    # XLC has 0% returns; it ranks below everyone else. Top 3 should be the
    # alphabetically-first three of {XLB, XLE, XLF, XLI, XLK, XLP, XLRE,
    # XLU, XLV, XLY} which is {XLB, XLE, XLF}.
    assert target is not None
    assert "XLC" not in target.holdings
    assert set(target.holdings) == {"XLB", "XLE", "XLF"}


def test_insufficient_universe_returns_none() -> None:
    """If fewer than ``hold_top_n`` tickers have valid lookback returns,
    the function returns None."""
    monthly = {
        "XLK": [0.02] * 12 + [+0.0],
        "XLF": [0.018] * 12 + [+0.0],
    }
    prices = _make_synthetic_prices(
        start=dt.date(2024, 1, 1), n_months=13, monthly_returns=monthly
    )
    # All other SPDR columns NaN
    for ticker in SPDR_UNIVERSE:
        if ticker not in monthly:
            prices[ticker] = float("nan")

    target = compute_target_portfolio(
        daily_prices=prices,
        as_of_date=prices["date"].max(),
    )

    # Only 2 valid; we want top-3 → None
    assert target is None


def test_equal_weights_sum_to_one_for_top_n() -> None:
    """Whatever the picks, weights should be exactly 1/N each summing to 1.0."""
    monthly = {ticker: [0.01 + 0.001 * i] * 12 + [0.0]
               for i, ticker in enumerate(SPDR_UNIVERSE)}
    prices = _make_synthetic_prices(
        start=dt.date(2024, 1, 1), n_months=13, monthly_returns=monthly
    )

    target = compute_target_portfolio(
        daily_prices=prices,
        as_of_date=prices["date"].max(),
        hold_top_n=3,
    )

    assert target is not None
    assert len(target.holdings) == 3
    assert len(target.weights) == 3
    assert sum(target.weights.values()) == pytest.approx(1.0)
    for ticker in target.holdings:
        assert target.weights[ticker] == pytest.approx(1 / 3)


def test_tie_handling_is_deterministic() -> None:
    """Two tickers with identical lookback returns should resolve in a
    stable order so downstream order generation is reproducible."""
    monthly = {ticker: [0.01] * 12 + [0.0] for ticker in SPDR_UNIVERSE}
    prices = _make_synthetic_prices(
        start=dt.date(2024, 1, 1), n_months=13, monthly_returns=monthly
    )

    target_a = compute_target_portfolio(
        daily_prices=prices,
        as_of_date=prices["date"].max(),
    )
    target_b = compute_target_portfolio(
        daily_prices=prices,
        as_of_date=prices["date"].max(),
    )

    assert target_a is not None and target_b is not None
    # Same input → same output (no random)
    assert target_a.holdings == target_b.holdings


def test_as_of_date_uses_prior_month_end_for_ranking() -> None:
    """Calling with as_of_date in the MIDDLE of a month should rank
    by the prior month's lookback (we don't use partial-month returns)."""
    monthly = {
        "XLK": [0.05] * 12 + [-0.99],
        "XLF": [0.04] * 12 + [-0.99],
        "XLE": [0.03] * 12 + [-0.99],
        "XLV": [0.0] * 12 + [+0.99],
        "XLY": [0.0] * 12 + [+0.99],
        "XLP": [0.0] * 12 + [+0.99],
        "XLI": [0.0] * 12 + [+0.0],
        "XLB": [0.0] * 12 + [+0.0],
        "XLU": [0.0] * 12 + [+0.0],
        "XLRE": [0.0] * 12 + [+0.0],
        "XLC": [0.0] * 12 + [+0.0],
    }
    prices = _make_synthetic_prices(
        start=dt.date(2024, 1, 1), n_months=13, monthly_returns=monthly
    )

    # mid-month as_of_date — should still pick XLK/XLF/XLE based on the
    # lookback ending at the PRIOR month-end (skipping the most recent month)
    last_date = prices["date"].max()
    mid_month_after = pd.Timestamp(last_date) + pd.Timedelta(days=10)
    # Add a few extra rows for the "mid month" days (use last close)
    extras = pd.DataFrame({
        "date": [(pd.Timestamp(last_date) + pd.Timedelta(days=d)).date()
                 for d in range(1, 11)],
        **{t: [prices[t].iloc[-1]] * 10 for t in SPDR_UNIVERSE},
    })
    extended = pd.concat([prices, extras], ignore_index=True)

    target = compute_target_portfolio(
        daily_prices=extended,
        as_of_date=mid_month_after.date(),
    )

    assert target is not None
    assert set(target.holdings) == {"XLK", "XLF", "XLE"}


def test_target_portfolio_is_immutable() -> None:
    """Returned ``TargetPortfolio`` should be hashable and frozen so
    callers cannot mutate weights or holdings."""
    tp = TargetPortfolio(
        as_of_date=dt.date(2024, 1, 31),
        holdings=("XLK", "XLF", "XLE"),
        weights={"XLK": 1 / 3, "XLF": 1 / 3, "XLE": 1 / 3},
    )
    with pytest.raises((AttributeError, TypeError)):
        tp.holdings = ("XLY",)  # type: ignore[misc]


def test_holdings_match_weights_keys() -> None:
    """The holdings tuple and weights dict must agree on tickers."""
    monthly = {ticker: [0.01 + 0.001 * i] * 12 + [0.0]
               for i, ticker in enumerate(SPDR_UNIVERSE)}
    prices = _make_synthetic_prices(
        start=dt.date(2024, 1, 1), n_months=13, monthly_returns=monthly
    )

    target = compute_target_portfolio(
        daily_prices=prices,
        as_of_date=prices["date"].max(),
        hold_top_n=3,
    )

    assert target is not None
    assert set(target.holdings) == set(target.weights.keys())
