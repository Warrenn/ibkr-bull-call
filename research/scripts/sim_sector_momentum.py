"""v9 sector ETF momentum strategy simulator.

12-1 cross-sectional momentum on 11 SPDR sector ETFs:
- At each month-end rebalance date, compute total return over months
  t-12 to t-1 (skipping the most recent month).
- Rank ETFs by that lookback return.
- Hold top N (default 3) equal-weighted for the coming month.

Compute monthly portfolio returns, then:
- Sharpe annualized = mean_monthly / std_monthly × √12
- Calmar = annualized return / |max drawdown|
- Compare to SPY benchmark

Slippage: 10 bps round-trip per rebalance applied to portfolio NAV.
Commissions: 0 (IBKR retail ETF commission-free since 2019).

Usage::

    uv run python -m research.scripts.sim_sector_momentum \\
        --etfs research/data/dataset-v1/sector_etfs_daily.parquet \\
        --window-start 2018-06-19 --window-end 2026-04-30 \\
        --hold-top-n 3 --lookback-months 12 --skip-recent-months 1 \\
        --report research/reports/v9-sector-momentum-full.md \\
        --csv research/reports/v9-sector-momentum-full-ledger.csv
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from scipy import stats


_SECTOR_TICKERS: tuple[str, ...] = (
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP",
    "XLI", "XLB", "XLU", "XLRE", "XLC",
)


@dataclass(frozen=True)
class MomentumParams:
    lookback_months: int = 12
    skip_recent_months: int = 1
    hold_top_n: int = 3
    slippage_bps_per_rebalance: float = 10.0  # 10 bps round-trip
    benchmark_ticker: str = "SPY"
    weighting: str = "equal"  # only "equal" supported
    max_dd_kill_pct: float = -0.40  # informational; not enforced in sim


def _to_monthly_returns(daily: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """Resample daily prices to month-end last price; compute monthly returns.

    Returns DataFrame indexed by month-end date with one column per ticker.
    """

    daily = daily.set_index("date")
    daily.index = pd.to_datetime(daily.index)
    monthly_close = daily[tickers].resample("ME").last()
    monthly_returns = monthly_close.pct_change().dropna()
    return monthly_returns


def _lookback_return(
    monthly_returns: pd.DataFrame,
    *,
    end_idx: int,
    lookback: int,
    skip: int,
) -> pd.Series | None:
    """Compute compound return from end_idx-lookback to end_idx-skip (exclusive).

    Returns a Series indexed by ticker. ``None`` if insufficient history.
    """

    start = end_idx - lookback
    end = end_idx - skip
    if start < 0 or end <= start:
        return None
    window = monthly_returns.iloc[start:end]
    return (1 + window).prod() - 1


def simulate(
    *,
    etf_daily: pd.DataFrame,
    universe: list[str],
    params: MomentumParams,
) -> pd.DataFrame:
    """Run monthly momentum simulation. Returns a per-month ledger
    with portfolio + benchmark returns + holdings."""

    monthly_returns = _to_monthly_returns(etf_daily, universe + [params.benchmark_ticker])

    rows: list[dict[str, object]] = []
    portfolio_nav = 1.0
    benchmark_nav = 1.0
    peak_nav = 1.0
    max_dd = 0.0
    prior_holdings: list[str] = []

    for i in range(len(monthly_returns)):
        rebalance_date = monthly_returns.index[i]
        lookback = _lookback_return(
            monthly_returns[universe],
            end_idx=i, lookback=params.lookback_months,
            skip=params.skip_recent_months,
        )
        if lookback is None:
            # Skip months where we don't have enough lookback history
            rows.append({
                "month_end": rebalance_date,
                "portfolio_nav": portfolio_nav,
                "benchmark_nav": benchmark_nav,
                "skipped": True,
                "skip_reason": "insufficient_lookback",
                "holdings": "",
                "month_return": float("nan"),
                "benchmark_month_return": float("nan"),
            })
            continue

        # Drop tickers with NaN lookback (e.g. XLC pre-2018-06)
        lookback_clean = lookback.dropna()
        if len(lookback_clean) < params.hold_top_n:
            rows.append({
                "month_end": rebalance_date,
                "portfolio_nav": portfolio_nav,
                "benchmark_nav": benchmark_nav,
                "skipped": True,
                "skip_reason": "insufficient_universe",
                "holdings": "",
                "month_return": float("nan"),
                "benchmark_month_return": float("nan"),
            })
            continue

        top_n = lookback_clean.nlargest(params.hold_top_n).index.tolist()

        # Portfolio return for the upcoming month (use this month's returns)
        # We hold from this rebalance date through the end of the month
        # represented by index i. So returns_this_month = monthly_returns.iloc[i].
        # But that's already realized — for backtest accuracy we hold based
        # on rebalance at start of month for month i+1. Here index i is
        # the END of month i; for forward-fill returns we use index i+1.

        if i + 1 >= len(monthly_returns):
            # Last month: no forward return to apply
            rows.append({
                "month_end": rebalance_date,
                "portfolio_nav": portfolio_nav,
                "benchmark_nav": benchmark_nav,
                "skipped": True,
                "skip_reason": "no_forward_return",
                "holdings": ",".join(top_n),
                "month_return": float("nan"),
                "benchmark_month_return": float("nan"),
            })
            continue

        next_month_returns = monthly_returns[universe].iloc[i + 1]
        portfolio_return = float(next_month_returns[top_n].mean())  # equal weight

        # Slippage: apply only when holdings change (rebalance)
        holdings_changed = set(top_n) != set(prior_holdings)
        if holdings_changed:
            portfolio_return -= params.slippage_bps_per_rebalance / 10000

        portfolio_nav *= (1 + portfolio_return)
        benchmark_return = float(monthly_returns[params.benchmark_ticker].iloc[i + 1])
        benchmark_nav *= (1 + benchmark_return)

        peak_nav = max(peak_nav, portfolio_nav)
        current_dd = (portfolio_nav - peak_nav) / peak_nav
        max_dd = min(max_dd, current_dd)

        rows.append({
            "month_end": monthly_returns.index[i + 1],  # the month return realized
            "portfolio_nav": portfolio_nav,
            "benchmark_nav": benchmark_nav,
            "skipped": False,
            "skip_reason": "",
            "holdings": ",".join(top_n),
            "month_return": portfolio_return,
            "benchmark_month_return": benchmark_return,
        })

        prior_holdings = top_n

    return pd.DataFrame(rows)


def aggregate_metrics(*, ledger: pd.DataFrame) -> dict[str, object]:
    traded = ledger[~ledger["skipped"]]
    if traded.empty:
        return {"n_total_months": len(ledger), "n_traded": 0}

    p_returns = traded["month_return"].astype(float)
    b_returns = traded["benchmark_month_return"].astype(float)

    # Compound total return
    p_total = (1 + p_returns).prod() - 1
    b_total = (1 + b_returns).prod() - 1

    # Annualized return (geometric, monthly compounded → annual)
    n_years = len(p_returns) / 12
    p_cagr = (1 + p_total) ** (1 / n_years) - 1
    b_cagr = (1 + b_total) ** (1 / n_years) - 1

    # Sharpe (assuming 0% risk-free for simplicity; subtract rf if needed)
    p_mean = float(p_returns.mean())
    p_std = float(p_returns.std(ddof=1))
    p_sharpe = (p_mean / p_std * math.sqrt(12)) if p_std > 0 else float("nan")
    b_mean = float(b_returns.mean())
    b_std = float(b_returns.std(ddof=1))
    b_sharpe = (b_mean / b_std * math.sqrt(12)) if b_std > 0 else float("nan")

    # t-stat for portfolio mean vs zero
    p_t = (p_mean / (p_std / math.sqrt(len(p_returns)))) if p_std > 0 else float("nan")
    p_p = float(2 * stats.t.sf(abs(p_t), df=len(p_returns) - 1)) if p_std > 0 else float("nan")

    # t-stat for outperformance vs benchmark
    spread = p_returns - b_returns
    s_mean = float(spread.mean())
    s_std = float(spread.std(ddof=1))
    s_t = (s_mean / (s_std / math.sqrt(len(spread)))) if s_std > 0 else float("nan")
    s_p = float(2 * stats.t.sf(abs(s_t), df=len(spread) - 1)) if s_std > 0 else float("nan")

    # Drawdown analysis on portfolio NAV
    nav = traded["portfolio_nav"].astype(float).values
    peaks = []
    p = nav[0] if len(nav) > 0 else 1.0
    for v in nav:
        p = max(p, v)
        peaks.append(p)
    peaks_arr = pd.Series(peaks, index=traded.index)
    dd = (traded["portfolio_nav"] - peaks_arr) / peaks_arr
    max_dd = float(dd.min())

    # Calmar
    calmar = p_cagr / abs(max_dd) if max_dd < 0 else float("inf")

    # Win-rate per month
    win_rate = float((p_returns > 0).mean())
    win_rate_vs_bench = float((spread > 0).mean())
    n_negative_months = int((p_returns < 0).sum())

    # Worst single-month
    worst_month = float(p_returns.min())
    best_month = float(p_returns.max())

    return {
        "n_total_months": len(ledger),
        "n_traded": len(traded),
        "n_skipped": int(ledger["skipped"].sum()),
        "n_years": n_years,

        "portfolio_total_return": p_total,
        "portfolio_cagr": p_cagr,
        "portfolio_sharpe": p_sharpe,
        "portfolio_t_stat": p_t,
        "portfolio_p_value": p_p,
        "portfolio_max_dd": max_dd,
        "portfolio_calmar": calmar,
        "portfolio_win_rate": win_rate,
        "portfolio_worst_month": worst_month,
        "portfolio_best_month": best_month,

        "benchmark_total_return": b_total,
        "benchmark_cagr": b_cagr,
        "benchmark_sharpe": b_sharpe,

        "spread_total": p_total - b_total,
        "spread_cagr": p_cagr - b_cagr,
        "spread_t_stat": s_t,
        "spread_p_value": s_p,
        "spread_win_rate": win_rate_vs_bench,
        "n_negative_months": n_negative_months,
    }


def format_report(
    *,
    metrics: dict[str, object], params: MomentumParams,
    window_start: dt.date, window_end: dt.date,
    universe: list[str], code_revision: str, run_timestamp: dt.datetime,
) -> str:
    lines = [
        "# v9 Sector ETF Momentum (12-1, top-3, monthly rebalance)",
        "",
        f"**Window**: {window_start.isoformat()} → {window_end.isoformat()}",
        "",
        "## Strategy parameters",
        "",
        f"- universe: {', '.join(universe)} ({len(universe)} ETFs)",
        f"- benchmark: {params.benchmark_ticker}",
        f"- lookback_months: {params.lookback_months}",
        f"- skip_recent_months: {params.skip_recent_months}",
        f"- hold_top_n: {params.hold_top_n}",
        f"- weighting: {params.weighting}",
        f"- rebalance_frequency: monthly",
        f"- slippage_bps_per_rebalance: {params.slippage_bps_per_rebalance:.0f}",
        f"- max_dd_kill_pct: {params.max_dd_kill_pct:.0%} (informational)",
        "",
        "## Provenance",
        "",
        f"- code_revision: `{code_revision}`",
        f"- run_timestamp_utc: {run_timestamp.isoformat()}",
        "",
    ]

    if metrics.get("n_traded", 0) == 0:
        lines.append("**No traded months — insufficient data.**")
        return "\n".join(lines)

    lines.extend([
        "## Aggregate results",
        "",
        f"- n_total_months: {metrics['n_total_months']}",
        f"- n_traded: {metrics['n_traded']} (skipped {metrics['n_skipped']} for insufficient lookback / forward)",
        f"- n_years: {metrics['n_years']:.2f}",
        "",
        "## Performance vs benchmark",
        "",
        "| Metric | Portfolio | SPY | Spread |",
        "|---|---|---|---|",
        f"| Total return | **{metrics['portfolio_total_return']:.1%}** | {metrics['benchmark_total_return']:.1%} | {metrics['spread_total']:+.1%} |",
        f"| CAGR | **{metrics['portfolio_cagr']:.2%}** | {metrics['benchmark_cagr']:.2%} | {metrics['spread_cagr']:+.2%} |",
        f"| Sharpe (annualized) | **{metrics['portfolio_sharpe']:.2f}** | {metrics['benchmark_sharpe']:.2f} | — |",
        f"| Win rate (months > 0) | {metrics['portfolio_win_rate']:.1%} | — | beat SPY: {metrics['spread_win_rate']:.1%} |",
        f"| Worst month | **{metrics['portfolio_worst_month']:.2%}** | — | — |",
        f"| Best month | {metrics['portfolio_best_month']:.2%} | — | — |",
        f"| Negative months | {metrics['n_negative_months']} of {metrics['n_traded']} ({metrics['n_negative_months']/metrics['n_traded']:.0%}) | — | — |",
        f"| Max drawdown | **{metrics['portfolio_max_dd']:.2%}** | — | — |",
        f"| Calmar ratio | **{metrics['portfolio_calmar']:.2f}** | — | — |",
        "",
        "## Statistical significance",
        "",
        "| Hypothesis | t-stat | p-value | Verdict |",
        "|---|---|---|---|",
        f"| Portfolio mean > 0 | {metrics['portfolio_t_stat']:+.2f} | {metrics['portfolio_p_value']:.4f} | {'significant' if metrics['portfolio_p_value'] < 0.05 else 'inconclusive'} |",
        f"| Portfolio outperforms SPY | {metrics['spread_t_stat']:+.2f} | {metrics['spread_p_value']:.4f} | {'significant' if metrics['spread_p_value'] < 0.05 else 'inconclusive'} |",
        "",
        "## Reading the result",
        "",
        "- **Sharpe ≥ 1.0** = acceptable risk-adjusted return.",
        "- **Beats SPY (spread > 0) with t-stat ≥ 2** = momentum factor",
        "  premium present in this window net of slippage.",
        "- **Max DD ≥ -25%** = within v9 spec's drawdown tolerance.",
        "- **Calmar ≥ 0.5** = recovers max DD in less than 2 years of",
        "  average performance.",
        "",
        "## Caveats",
        "",
        "- 7.8 years is a short window for momentum (academic studies use",
        "  30+ years). This window includes 2020 COVID, 2022 bear, and the",
        "  2025 tariff regime — diverse but not exhaustive.",
        "- 10 bps round-trip slippage assumed; real fills on liquid SPDRs",
        "  may be tighter, but slippage at scale could be wider.",
        "- No commissions modeled (IBKR retail commission-free since 2019).",
        "- 12-1 is one of many momentum signal definitions; results may",
        "  vary with 6-1, 9-1, or no-skip variants.",
        "- Sample size is months not days — n=~85 monthly observations",
        "  means t-stats need to be interpreted with low-power caution.",
    ])
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="sim_sector_momentum")
    p.add_argument("--etfs", type=Path,
                   default=Path("research/data/dataset-v1/sector_etfs_daily.parquet"))
    p.add_argument("--window-start", type=dt.date.fromisoformat, required=True)
    p.add_argument("--window-end", type=dt.date.fromisoformat, required=True)
    p.add_argument("--lookback-months", type=int, default=12)
    p.add_argument("--skip-recent-months", type=int, default=1)
    p.add_argument("--hold-top-n", type=int, default=3)
    p.add_argument("--slippage-bps", type=float, default=10.0)
    p.add_argument("--benchmark", default="SPY")
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--csv", type=Path, required=True)
    p.add_argument("--code-revision", default="HEAD")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    daily = pd.read_parquet(args.etfs)
    daily = daily[
        (daily["date"] >= args.window_start) & (daily["date"] <= args.window_end)
    ].copy()

    universe = list(_SECTOR_TICKERS)
    params = MomentumParams(
        lookback_months=args.lookback_months,
        skip_recent_months=args.skip_recent_months,
        hold_top_n=args.hold_top_n,
        slippage_bps_per_rebalance=args.slippage_bps,
        benchmark_ticker=args.benchmark,
    )

    ledger = simulate(etf_daily=daily, universe=universe, params=params)
    metrics = aggregate_metrics(ledger=ledger)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(format_report(
        metrics=metrics, params=params,
        window_start=args.window_start, window_end=args.window_end,
        universe=universe,
        code_revision=args.code_revision,
        run_timestamp=dt.datetime.now(tz=dt.timezone.utc),
    ))
    ledger.to_csv(args.csv, index=False)

    print(f"window: {args.window_start} → {args.window_end}")
    if metrics.get("n_traded", 0) > 0:
        print(f"months traded: {metrics['n_traded']} (years: {metrics['n_years']:.2f})")
        print(f"portfolio total return: {metrics['portfolio_total_return']:.1%}")
        print(f"portfolio CAGR: {metrics['portfolio_cagr']:.2%}")
        print(f"portfolio Sharpe: {metrics['portfolio_sharpe']:.2f}")
        print(f"benchmark CAGR: {metrics['benchmark_cagr']:.2%}")
        print(f"spread (port - bench) CAGR: {metrics['spread_cagr']:+.2%}")
        print(f"max DD: {metrics['portfolio_max_dd']:.2%}")
        print(f"Calmar: {metrics['portfolio_calmar']:.2f}")
        print(f"win rate (months > 0): {metrics['portfolio_win_rate']:.1%}")
        print(f"win rate vs SPY: {metrics['spread_win_rate']:.1%}")
    print(f"\nreport -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
