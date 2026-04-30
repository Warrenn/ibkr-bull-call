"""v9 signal: 12-1 cross-sectional momentum on 11 SPDR sector ETFs.

Production code path (live paper trading) parallels the research
script ``research/scripts/sim_sector_momentum.py``. Both must produce
identical top-N selections on the same input — the cross-check test in
``tests/v9/test_shadow.py`` enforces this.

This module is pure Python: no I/O, no side effects, no IBKR dependency.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Mapping

import pandas as pd


SPDR_UNIVERSE: tuple[str, ...] = (
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP",
    "XLI", "XLB", "XLU", "XLRE", "XLC",
)


@dataclass(frozen=True)
class TargetPortfolio:
    """Frozen portfolio target produced by ``compute_target_portfolio``.

    Attributes
    ----------
    as_of_date:
        The date the target was computed for. Ranking uses month-end
        returns up to and including the PRIOR month-end (skipping the
        most-recent month per spec).
    holdings:
        Selected tickers in deterministic alphabetical order.
    weights:
        Equal-weight mapping ticker → weight; sums to 1.0.
    """

    as_of_date: dt.date
    holdings: tuple[str, ...]
    weights: Mapping[str, float] = field(default_factory=dict)


def _to_monthly_close(
    daily_prices: pd.DataFrame, tickers: tuple[str, ...]
) -> pd.DataFrame:
    """Resample daily closes to month-end last observed price."""
    df = daily_prices.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    available = [t for t in tickers if t in df.columns]
    monthly = df[available].resample("ME").last()
    return monthly


def _lookback_return(
    monthly_close: pd.DataFrame,
    *,
    as_of_date: dt.date,
    lookback_months: int,
    skip_recent_months: int,
) -> pd.Series | None:
    """Compound lookback return for ranking. Matches the research script
    ``research.scripts.sim_sector_momentum._lookback_return`` exactly so
    the v9 backtest audit trail (PR #71) carries forward to live trading.

    The math: build ``monthly_returns = monthly_close.pct_change().dropna()``,
    locate the row at the last month-end ≤ ``as_of_date``, then compound
    the slice ``iloc[end_idx - lookback : end_idx - skip]``. With
    ``lookback=12, skip=1`` this yields v(end-1)/v(end-12) - 1 — an
    11-month return ending two month-ends before ``as_of_date``.

    Returns a Series indexed by ticker (NaN where lookback contains a
    NaN), or ``None`` if the window cannot be formed.
    """
    monthly_returns = monthly_close.pct_change().dropna()
    if monthly_returns.empty:
        return None

    eligible = monthly_returns.index[
        monthly_returns.index <= pd.Timestamp(as_of_date)
    ]
    if len(eligible) == 0:
        return None
    end_idx = monthly_returns.index.get_loc(eligible[-1])
    if not isinstance(end_idx, int):
        return None

    start = end_idx - lookback_months
    end = end_idx - skip_recent_months
    if start < 0 or end <= start:
        return None

    window = monthly_returns.iloc[start:end]
    result = (1 + window).prod() - 1
    # pandas ``.prod()`` with default skipna=True turns all-NaN windows
    # into 0% return rather than NaN; force NaN for tickers whose
    # lookback contains any NaN (e.g., late-listed ETFs).
    result[window.isna().any(axis=0)] = float("nan")
    return result


def compute_target_portfolio(
    *,
    daily_prices: pd.DataFrame,
    as_of_date: dt.date,
    universe: tuple[str, ...] = SPDR_UNIVERSE,
    lookback_months: int = 12,
    skip_recent_months: int = 1,
    hold_top_n: int = 3,
) -> TargetPortfolio | None:
    """Compute the equal-weight top-N target portfolio for ``as_of_date``.

    Parameters
    ----------
    daily_prices:
        DataFrame with a ``date`` column and one column per ticker
        containing daily close prices.
    as_of_date:
        Reference date for the lookback window. The window ends at the
        last month-end strictly before ``as_of_date - skip_recent_months``
        months and spans ``lookback_months`` months.
    universe:
        Eligible tickers. Defaults to the 11 SPDR sector ETFs.
    lookback_months / skip_recent_months / hold_top_n:
        12-1 momentum, top-3 by spec defaults.

    Returns
    -------
    ``TargetPortfolio`` or ``None`` if there is insufficient lookback or
    fewer than ``hold_top_n`` tickers have valid lookback returns.
    """
    monthly_close = _to_monthly_close(daily_prices, universe)
    lookback = _lookback_return(
        monthly_close,
        as_of_date=as_of_date,
        lookback_months=lookback_months,
        skip_recent_months=skip_recent_months,
    )
    if lookback is None:
        return None

    valid = lookback.dropna()
    if len(valid) < hold_top_n:
        return None

    # Build (negative_return, ticker) tuples — sort puts highest return
    # first; alphabetical tie-break is built into the tuple compare.
    candidates: list[tuple[float, str]] = sorted(
        (-float(ret), str(ticker)) for ticker, ret in valid.items()
    )
    top_n_tickers: tuple[str, ...] = tuple(
        sorted(ticker for _, ticker in candidates[:hold_top_n])
    )

    weight = 1.0 / hold_top_n
    weights: dict[str, float] = {ticker: weight for ticker in top_n_tickers}

    return TargetPortfolio(
        as_of_date=as_of_date,
        holdings=top_n_tickers,
        weights=weights,
    )
