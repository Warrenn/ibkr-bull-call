"""v9 shadow: wrap the research script's signal logic so production code
can be cross-checked against the same code that produced v9's backtest
verdict (PR #71).

The single regression-guard purpose: make a future code change to
``signal.py`` immediately fail tests if the production signal diverges
from the research script's behavior.

This module imports from ``research.scripts.sim_sector_momentum``, so
``research/`` must be on sys.path (configured via ``pyproject.toml``
``[tool.pytest.ini_options]`` and the bot's runtime environment).
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from research.scripts.sim_sector_momentum import (
    _SECTOR_TICKERS,
    _lookback_return,
    _to_monthly_returns,
)


def research_top_n(
    *,
    daily_prices: pd.DataFrame,
    as_of_date: dt.date,
    universe: tuple[str, ...] = _SECTOR_TICKERS,
    lookback_months: int = 12,
    skip_recent_months: int = 1,
    hold_top_n: int = 3,
) -> tuple[str, ...] | None:
    """Compute the research-script top-N for ``as_of_date``.

    Returns a tuple of tickers in alphabetical order, or ``None`` if the
    research helpers cannot produce a ranking (insufficient lookback or
    too few valid tickers).
    """
    available_universe = [t for t in universe if t in daily_prices.columns]
    monthly_returns = _to_monthly_returns(
        daily_prices, list(available_universe)
    )
    if monthly_returns.empty:
        return None

    # Find the simulator's "i" index — the position in monthly_returns
    # whose index is the last month-end ≤ as_of_date. Research's
    # ``_lookback_return`` takes this directly (no skip adjustment).
    eligible = monthly_returns.index[
        monthly_returns.index <= pd.Timestamp(as_of_date)
    ]
    if len(eligible) == 0:
        return None
    end_idx = monthly_returns.index.get_loc(eligible[-1])
    if not isinstance(end_idx, int):
        return None
    lookback = _lookback_return(
        monthly_returns,
        end_idx=end_idx,
        lookback=lookback_months,
        skip=skip_recent_months,
    )
    if lookback is None:
        return None
    valid = lookback.dropna()
    if len(valid) < hold_top_n:
        return None

    # Same alphabetical tie-break as production
    candidates: list[tuple[float, str]] = sorted(
        (-float(ret), str(ticker)) for ticker, ret in valid.items()
    )
    top: tuple[str, ...] = tuple(
        sorted(ticker for _, ticker in candidates[:hold_top_n])
    )
    return top
