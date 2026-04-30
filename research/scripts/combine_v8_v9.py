"""Combine v8 vol carry + v9 sector momentum into a 50/50 portfolio
and compare to each strategy alone and to SPY DCA baselines.

Aligns on monthly returns over the dataset-v1 60mo window
(2021-04-30 → 2026-04-30) — the only window all three measurements
share without extrapolation.

- v9 monthly returns: from v9-sector-momentum-60mo-ledger.csv
- v8 daily returns: from v8-vol-carry-full-ledger.csv, aggregated
  to month-end via compounding within the month
- SPY benchmark: from same v9 ledger's benchmark column

Produces metrics for:
- v8 alone (monthly)
- v9 alone (monthly)
- 50/50 v8+v9 combined
- SPY (passive)
- Correlation between v8 and v9 monthly returns

Usage::

    uv run python -m research.scripts.combine_v8_v9 \\
        --v9-ledger research/reports/v9-sector-momentum-60mo-ledger.csv \\
        --v8-ledger research/reports/v8-vol-carry-full-ledger.csv \\
        --report research/reports/v8-v9-combined-60mo.md \\
        --csv research/reports/v8-v9-combined-60mo-ledger.csv
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import sys
from pathlib import Path

import pandas as pd
from scipy import stats


def _aggregate_v8_to_monthly(daily: pd.DataFrame) -> pd.DataFrame:
    """Aggregate v8 daily strategy returns to monthly compounded."""
    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily["date"])
    daily = daily.sort_values("date").reset_index(drop=True)
    daily["month_end"] = daily["date"].dt.to_period("M").dt.to_timestamp("M")
    monthly = (
        daily.groupby("month_end")
        .apply(lambda g: pd.Series({
            "v8_return": (1 + g["strategy_return"]).prod() - 1,
            "spy_return_v8": (1 + g["spy_return"]).prod() - 1,
            "n_days": len(g),
        }), include_groups=False)
        .reset_index()
    )
    return monthly


def _v9_monthly(ledger_v9: pd.DataFrame) -> pd.DataFrame:
    """Extract traded monthly returns from v9 ledger."""
    df = ledger_v9.copy()
    df = df[~df["skipped"]].copy()
    df["month_end"] = pd.to_datetime(df["month_end"]).dt.to_period("M").dt.to_timestamp("M")
    return df[["month_end", "month_return", "benchmark_month_return"]].rename(
        columns={"month_return": "v9_return", "benchmark_month_return": "spy_return_v9"}
    )


def combine(
    *,
    v9_ledger: pd.DataFrame,
    v8_ledger: pd.DataFrame,
) -> pd.DataFrame:
    """Realistic 50/50 deployment:
    - v8 capital is deployed all 60 months
    - v9 capital is deployed only when v9 has sufficient lookback;
      during v9's warm-up months, that half sits in cash (0% return)
    - SPY return comes from v8's monthly aggregation (covers all 60mo)
    """
    v9_monthly = _v9_monthly(v9_ledger)
    v8_monthly = _aggregate_v8_to_monthly(v8_ledger)

    # Left-join on v8 (full 60mo window); v9 NaN → cash (0% return)
    merged = v8_monthly.merge(v9_monthly, on="month_end", how="left")
    merged["v9_return"] = merged["v9_return"].fillna(0.0)
    merged["v9_traded"] = merged["spy_return_v9"].notna()
    merged = merged.sort_values("month_end").reset_index(drop=True)

    # SPY benchmark: use v8's monthly-aggregated SPY which has all 60 months.
    merged["spy_return"] = merged["spy_return_v8"]

    # 50/50 portfolio: each month, half in v8 + half in v9 (or cash if v9
    # not traded that month).
    merged["combined_return"] = 0.5 * merged["v9_return"] + 0.5 * merged["v8_return"]

    # Build NAV curves
    merged["v8_nav"] = (1 + merged["v8_return"]).cumprod()
    merged["v9_nav"] = (1 + merged["v9_return"]).cumprod()
    merged["combined_nav"] = (1 + merged["combined_return"]).cumprod()
    merged["spy_nav"] = (1 + merged["spy_return"]).cumprod()

    return merged


def _aggregate(returns: pd.Series, name: str) -> dict[str, object]:
    n = len(returns)
    n_years = n / 12
    total = float((1 + returns).prod() - 1)
    cagr = (1 + total) ** (1 / n_years) - 1 if n_years > 0 else float("nan")
    mean = float(returns.mean())
    std = float(returns.std(ddof=1))
    sharpe = (mean / std * math.sqrt(12)) if std > 0 else float("nan")
    nav = (1 + returns).cumprod()
    peak = nav.cummax()
    dd = (nav - peak) / peak
    max_dd = float(dd.min())
    calmar = cagr / abs(max_dd) if max_dd < 0 else float("inf")
    return {
        f"{name}_total_return": total,
        f"{name}_cagr": cagr,
        f"{name}_sharpe": sharpe,
        f"{name}_max_dd": max_dd,
        f"{name}_calmar": calmar,
        f"{name}_worst_month": float(returns.min()),
        f"{name}_best_month": float(returns.max()),
        f"{name}_win_rate": float((returns > 0).mean()),
    }


def aggregate_all(*, combined_df: pd.DataFrame) -> dict[str, object]:
    n = len(combined_df)
    n_years = n / 12

    metrics: dict[str, object] = {"n_months": n, "n_years": n_years}

    metrics.update(_aggregate(combined_df["v8_return"], "v8"))
    metrics.update(_aggregate(combined_df["v9_return"], "v9"))
    metrics.update(_aggregate(combined_df["combined_return"], "combined"))
    metrics.update(_aggregate(combined_df["spy_return"], "spy"))

    # Correlation
    corr = combined_df[["v8_return", "v9_return"]].corr().iloc[0, 1]
    metrics["correlation_v8_v9"] = float(corr)

    # Combined vs SPY: spread t-stat
    spread = combined_df["combined_return"] - combined_df["spy_return"]
    sp_mean = float(spread.mean())
    sp_std = float(spread.std(ddof=1))
    sp_t = (sp_mean / (sp_std / math.sqrt(n))) if sp_std > 0 else float("nan")
    sp_p = float(2 * stats.t.sf(abs(sp_t), df=n - 1)) if sp_std > 0 else float("nan")
    metrics["combined_vs_spy_t_stat"] = sp_t
    metrics["combined_vs_spy_p_value"] = sp_p

    return metrics


def format_report(
    *,
    metrics: dict[str, object],
    code_revision: str,
    run_timestamp: dt.datetime,
    window_start: dt.date,
    window_end: dt.date,
) -> str:
    lines = [
        "# Combined v8 + v9 Portfolio (50/50 monthly rebalance)",
        "",
        f"**Window**: {window_start.isoformat()} → {window_end.isoformat()}",
        f"**n_months**: {metrics['n_months']}",
        "",
        "## Provenance",
        "",
        f"- code_revision: `{code_revision}`",
        f"- run_timestamp_utc: {run_timestamp.isoformat()}",
        "",
        "## Construction",
        "",
        "Each month, allocate 50% capital to v9 (sector momentum, top-3 SPDRs)",
        "and 50% to v8 (vol-carry SVXY contango gate). Realize each strategy's",
        "monthly return on its half. Combined monthly return = simple average",
        "of the two strategy returns. Rebalance to 50/50 at month-end.",
        "",
        "## Performance comparison",
        "",
        "| Metric | v8 alone | v9 alone | **Combined 50/50** | SPY |",
        "|---|---|---|---|---|",
        f"| Total return | {metrics['v8_total_return']:+.2%} | {metrics['v9_total_return']:+.2%} | **{metrics['combined_total_return']:+.2%}** | {metrics['spy_total_return']:+.2%} |",
        f"| CAGR | {metrics['v8_cagr']:+.2%} | {metrics['v9_cagr']:+.2%} | **{metrics['combined_cagr']:+.2%}** | {metrics['spy_cagr']:+.2%} |",
        f"| Sharpe annualized | {metrics['v8_sharpe']:+.2f} | {metrics['v9_sharpe']:+.2f} | **{metrics['combined_sharpe']:+.2f}** | {metrics['spy_sharpe']:+.2f} |",
        f"| Max drawdown | {metrics['v8_max_dd']:.2%} | {metrics['v9_max_dd']:.2%} | **{metrics['combined_max_dd']:.2%}** | {metrics['spy_max_dd']:.2%} |",
        f"| Calmar ratio | {metrics['v8_calmar']:+.2f} | {metrics['v9_calmar']:+.2f} | **{metrics['combined_calmar']:+.2f}** | {metrics['spy_calmar']:+.2f} |",
        f"| Worst month | {metrics['v8_worst_month']:.2%} | {metrics['v9_worst_month']:.2%} | **{metrics['combined_worst_month']:.2%}** | {metrics['spy_worst_month']:.2%} |",
        f"| Best month | {metrics['v8_best_month']:.2%} | {metrics['v9_best_month']:.2%} | {metrics['combined_best_month']:.2%} | {metrics['spy_best_month']:.2%} |",
        f"| Win rate (months > 0) | {metrics['v8_win_rate']:.1%} | {metrics['v9_win_rate']:.1%} | **{metrics['combined_win_rate']:.1%}** | {metrics['spy_win_rate']:.1%} |",
        "",
        "## Correlation",
        "",
        f"- **corr(v8, v9) = {metrics['correlation_v8_v9']:+.3f}**",
        "",
        "Low or negative correlation = bigger diversification benefit.",
        "Positive correlation > 0.5 = limited diversification.",
        "",
        "## Statistical significance (combined vs SPY)",
        "",
        f"- spread mean t-stat: {metrics['combined_vs_spy_t_stat']:+.2f}",
        f"- p-value: {metrics['combined_vs_spy_p_value']:.4f}",
        f"- verdict: {'significant' if metrics['combined_vs_spy_p_value'] < 0.05 else 'inconclusive'}",
        "",
        "## Reading the result",
        "",
        "If combined Sharpe > max(v8, v9), diversification helped. Otherwise",
        "the negative-return strategy diluted the positive one. With only two",
        "strategies, sign-of-correlation matters most:",
        "",
        "- corr ≈ 0  → maximal diversification (combined vol < average vol)",
        "- corr > 0  → limited benefit; combined Sharpe ≈ average Sharpe",
        "- corr < 0  → vol reduction *and* return preserved → ideal",
        "",
        "## Caveats",
        "",
        "- v9 was promoted per its frozen spec; v8 was KILLED per its frozen",
        "  spec (see `research/reports/v8-vol-carry-tvh-split.md`). This",
        "  combined analysis is **descriptive**, not a recommendation to deploy",
        "  v8+v9 — deploying a killed strategy violates the project's",
        "  discipline framework.",
        "- 60-month window only includes v9's traded months; first months are",
        "  v9-skipped due to insufficient lookback.",
        "- 50/50 weighting is naïve; risk-parity or vol-weighted allocation",
        "  would scale v8's higher volatility down.",
        "- No rebalancing costs modeled (would be small for monthly rebalance).",
        "- Combined Sharpe assumes monthly returns are stationary — vol",
        "  regimes can violate this.",
    ]

    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="combine_v8_v9")
    p.add_argument("--v9-ledger", type=Path,
                   default=Path("research/reports/v9-sector-momentum-60mo-ledger.csv"))
    p.add_argument("--v8-ledger", type=Path,
                   default=Path("research/reports/v8-vol-carry-full-ledger.csv"))
    p.add_argument("--window-start", type=dt.date.fromisoformat,
                   default=dt.date(2021, 4, 30))
    p.add_argument("--window-end", type=dt.date.fromisoformat,
                   default=dt.date(2026, 4, 30))
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--csv", type=Path, required=True)
    p.add_argument("--code-revision", default="HEAD")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    v9_ledger = pd.read_csv(args.v9_ledger)
    v8_ledger = pd.read_csv(args.v8_ledger)

    combined_df = combine(v9_ledger=v9_ledger, v8_ledger=v8_ledger)
    metrics = aggregate_all(combined_df=combined_df)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(format_report(
        metrics=metrics, code_revision=args.code_revision,
        run_timestamp=dt.datetime.now(tz=dt.timezone.utc),
        window_start=args.window_start, window_end=args.window_end,
    ))
    combined_df.to_csv(args.csv, index=False)

    print(f"window: {args.window_start} → {args.window_end}")
    print(f"n_months: {metrics['n_months']}")
    print(f"corr(v8, v9): {metrics['correlation_v8_v9']:+.3f}\n")
    print(f"{'metric':<22} {'v8':>10} {'v9':>10} {'combined':>10} {'SPY':>10}")
    print("-" * 64)
    for metric in ["total_return", "cagr", "sharpe", "max_dd", "calmar", "win_rate"]:
        v8 = metrics[f"v8_{metric}"]
        v9 = metrics[f"v9_{metric}"]
        co = metrics[f"combined_{metric}"]
        sp = metrics[f"spy_{metric}"]
        if metric in ("sharpe", "calmar"):
            fmt = "{:>10.2f}"
        else:
            fmt = "{:>10.2%}"
        print(f"{metric:<22} " + fmt.format(v8) + fmt.format(v9) + fmt.format(co) + fmt.format(sp))
    print(f"\ncombined vs SPY t-stat: {metrics['combined_vs_spy_t_stat']:+.2f}, p={metrics['combined_vs_spy_p_value']:.4f}")
    print(f"\nreport -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
