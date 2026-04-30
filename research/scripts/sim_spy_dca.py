"""SPY dollar-cost-averaging baseline.

Compare three contribution schedules against the dataset-v1 60mo
window so v9 (and future strategies) can be benchmarked against the
returns a passive investor would have realized over the same period.

Modes (same total $ contributed in each):
- ``lump_sum``      : invest ``contribution_per_month * n_months`` on day 1
- ``monthly_dca``   : invest ``contribution_per_month`` on the first
                       trading day of each month
- ``daily_dca``     : within each month, distribute the same monthly
                       amount across that month's trading days
                       (i.e. ``contribution / n_trading_days_in_month``
                       per trading day)

Metrics (per mode):
- total contributed
- terminal market value
- $ profit, profit %
- annualized money-weighted return (XIRR)
- max drawdown of market value (peak-to-trough %)
- max drawdown of unrealized P&L (= mv − contributed; relevant for DCA
  where contributions distort raw value DD)

Usage::

    uv run python -m research.scripts.sim_spy_dca \\
        --etfs research/data/dataset-v1/sector_etfs_daily.parquet \\
        --window-start 2021-04-30 --window-end 2026-04-30 \\
        --contribution-per-month 1000 \\
        --report research/reports/spy-dca-baseline-60mo.md \\
        --csv research/reports/spy-dca-baseline-60mo-ledger.csv
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from scipy import optimize


@dataclass(frozen=True)
class DCAParams:
    contribution_per_month: float = 1000.0
    ticker: str = "SPY"


def _xirr(cashflows: list[tuple[dt.date, float]]) -> float:
    """Annualized money-weighted return.

    cashflows: list of (date, amount). Contributions are negative
    (money out); terminal market value is positive (money in).
    Returns the annualized rate r such that NPV = 0.
    """
    if len(cashflows) < 2:
        return float("nan")
    d0 = cashflows[0][0]
    times = [(d - d0).days / 365.25 for d, _ in cashflows]
    amounts = [a for _, a in cashflows]

    def npv(r: float) -> float:
        return sum(a / (1 + r) ** t for a, t in zip(amounts, times))

    try:
        return float(optimize.brentq(npv, -0.999, 100.0, maxiter=500))
    except (ValueError, RuntimeError):
        return float("nan")


def _max_dd(series: pd.Series) -> float:
    """Max peak-to-trough drawdown as a fraction (negative)."""
    if series.empty:
        return 0.0
    peak = series.cummax()
    dd = (series - peak) / peak.replace(0, pd.NA)
    return float(dd.min(skipna=True))


def _max_pnl_dd(pnl: pd.Series) -> float:
    """Max drawdown on unrealized P&L in dollars (negative).

    For DCA, P&L can cross zero — a percent-of-peak doesn't behave well
    when the peak is small or negative. Report the dollar drop from
    peak P&L instead.
    """
    if pnl.empty:
        return 0.0
    peak = pnl.cummax()
    dd = pnl - peak
    return float(dd.min())


def _simulate_mode(
    *,
    daily: pd.DataFrame,
    mode: str,
    params: DCAParams,
) -> pd.DataFrame:
    """Run one DCA mode and return a per-day ledger.

    daily: DataFrame with ``date`` and the SPY close column.
    Returns DataFrame with date, close, contribution, shares_bought,
    cum_contributed, cum_shares, market_value, pnl_$.
    """
    df = daily[["date", params.ticker]].rename(columns={params.ticker: "close"}).copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    n_months = (df["date"].dt.to_period("M").nunique())
    monthly_amount = params.contribution_per_month

    contributions = pd.Series(0.0, index=df.index)
    if mode == "lump_sum":
        contributions.iloc[0] = monthly_amount * n_months
    elif mode == "monthly_dca":
        first_of_month_mask = df["date"].dt.to_period("M") != df["date"].shift(1).dt.to_period("M")
        contributions[first_of_month_mask] = monthly_amount
    elif mode == "daily_dca":
        period = df["date"].dt.to_period("M")
        days_per_month = period.map(period.value_counts())
        contributions = monthly_amount / days_per_month
    else:
        raise ValueError(f"unknown mode: {mode}")

    df["contribution"] = contributions.values
    df["shares_bought"] = df["contribution"] / df["close"]
    df["cum_contributed"] = df["contribution"].cumsum()
    df["cum_shares"] = df["shares_bought"].cumsum()
    df["market_value"] = df["cum_shares"] * df["close"]
    df["pnl_dollars"] = df["market_value"] - df["cum_contributed"]
    df["mode"] = mode
    return df


def _aggregate_mode(*, ledger: pd.DataFrame) -> dict[str, object]:
    if ledger.empty:
        return {}
    total_contributed = float(ledger["cum_contributed"].iloc[-1])
    terminal_mv = float(ledger["market_value"].iloc[-1])
    profit = terminal_mv - total_contributed
    profit_pct = profit / total_contributed if total_contributed > 0 else float("nan")

    # Build cashflow series for XIRR: each non-zero contribution is a negative,
    # terminal value is a single positive on the last date.
    contributions = ledger[ledger["contribution"] > 0][["date", "contribution"]]
    cf: list[tuple[dt.date, float]] = [
        (row["date"].date(), -float(row["contribution"]))
        for _, row in contributions.iterrows()
    ]
    cf.append((ledger["date"].iloc[-1].date(), terminal_mv))
    irr = _xirr(cf)

    value_dd = _max_dd(ledger["market_value"])
    pnl_dd_dollars = _max_pnl_dd(ledger["pnl_dollars"])
    pnl_dd_pct = pnl_dd_dollars / total_contributed if total_contributed > 0 else float("nan")

    n_years = (ledger["date"].iloc[-1] - ledger["date"].iloc[0]).days / 365.25

    return {
        "mode": ledger["mode"].iloc[0],
        "n_days": len(ledger),
        "n_years": n_years,
        "total_contributed": total_contributed,
        "terminal_market_value": terminal_mv,
        "profit_dollars": profit,
        "profit_pct": profit_pct,
        "irr_annualized": irr,
        "max_value_dd_pct": value_dd,
        "max_pnl_dd_dollars": pnl_dd_dollars,
        "max_pnl_dd_pct_of_contributed": pnl_dd_pct,
    }


def simulate(
    *,
    daily: pd.DataFrame,
    params: DCAParams,
) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    """Run all three modes. Returns (combined_ledger, list_of_metrics)."""
    modes = ["lump_sum", "monthly_dca", "daily_dca"]
    ledgers = [_simulate_mode(daily=daily, mode=m, params=params) for m in modes]
    metrics = [_aggregate_mode(ledger=lg) for lg in ledgers]
    combined = pd.concat(ledgers, ignore_index=True)
    return combined, metrics


def format_report(
    *,
    metrics: list[dict[str, object]],
    params: DCAParams,
    window_start: dt.date,
    window_end: dt.date,
    code_revision: str,
    run_timestamp: dt.datetime,
) -> str:
    by_mode = {m["mode"]: m for m in metrics if m}
    lines = [
        "# SPY Dollar-Cost-Averaging Baseline (dataset-v1 60mo window)",
        "",
        f"**Window**: {window_start.isoformat()} → {window_end.isoformat()}",
        "",
        "## Setup",
        "",
        f"- ticker: {params.ticker}",
        f"- contribution_per_month: ${params.contribution_per_month:,.2f}",
        "- modes:",
        "    - **lump_sum**: invest the full window's monthly total on day 1",
        "    - **monthly_dca**: invest one month's amount on the first trading day of each month",
        "    - **daily_dca**: distribute one month's amount evenly across that month's trading days",
        "- All three modes contribute the **same total $** over the window —",
        "  only the timing differs.",
        "",
        "## Provenance",
        "",
        f"- code_revision: `{code_revision}`",
        f"- run_timestamp_utc: {run_timestamp.isoformat()}",
        f"- data: research/data/dataset-v1/sector_etfs_daily.parquet (SPY column)",
        "",
        "## Results",
        "",
        "| Metric | Lump sum | Monthly DCA | Daily DCA |",
        "|---|---|---|---|",
    ]

    def cell(mode: str, key: str, fmt: str) -> str:
        v = by_mode.get(mode, {}).get(key)
        if v is None:
            return "—"
        if isinstance(v, float) and math.isnan(v):
            return "—"
        return fmt.format(v)

    rows = [
        ("Total contributed", "total_contributed", "${:,.2f}"),
        ("Terminal market value", "terminal_market_value", "${:,.2f}"),
        ("Profit ($)", "profit_dollars", "${:,.2f}"),
        ("Profit (%)", "profit_pct", "{:+.2%}"),
        ("IRR (annualized)", "irr_annualized", "{:+.2%}"),
        ("Max market-value DD", "max_value_dd_pct", "{:.2%}"),
        ("Max P&L drawdown ($)", "max_pnl_dd_dollars", "${:,.2f}"),
        ("Max P&L drawdown (% of contributed)", "max_pnl_dd_pct_of_contributed", "{:.2%}"),
    ]
    for label, key, fmt in rows:
        lines.append(
            f"| {label} | {cell('lump_sum', key, fmt)} | "
            f"{cell('monthly_dca', key, fmt)} | {cell('daily_dca', key, fmt)} |"
        )

    lines.extend([
        "",
        "## Reading the result",
        "",
        "- **IRR** is the apples-to-apples comparison: it accounts for",
        "  the timing of contributions. Lump-sum has all capital at",
        "  work for the full window; DCA modes only have late",
        "  contributions exposed for a short time.",
        "- **Max market-value DD** treats the position like an equity",
        "  curve: the largest peak-to-trough drop. Less meaningful for",
        "  DCA because new contributions push the value up and mask",
        "  underlying SPY drawdowns.",
        "- **Max P&L drawdown** tracks the worst paper loss from peak.",
        "  This is the number a DCA investor would actually feel.",
        "",
        "## Caveats",
        "",
        "- Fills are at daily close (no intraday slippage modeled).",
        "- No transaction costs (IBKR retail SPY commission-free since 2019).",
        "- Fractional shares assumed (DCA buys ``contribution / close`` shares).",
        "- Reinvested dividends already baked into auto-adjusted close.",
        "- IRR via Brent root-finding on annualized rate; may report NaN",
        "  if cashflows don't bracket a sign change (shouldn't happen for",
        "  a profitable position over this window).",
    ])
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="sim_spy_dca")
    p.add_argument("--etfs", type=Path,
                   default=Path("research/data/dataset-v1/sector_etfs_daily.parquet"))
    p.add_argument("--window-start", type=dt.date.fromisoformat, required=True)
    p.add_argument("--window-end", type=dt.date.fromisoformat, required=True)
    p.add_argument("--contribution-per-month", type=float, default=1000.0)
    p.add_argument("--ticker", default="SPY")
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--csv", type=Path, required=True)
    p.add_argument("--code-revision", default="HEAD")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    daily = pd.read_parquet(args.etfs)
    daily = daily[
        (daily["date"] >= args.window_start) & (daily["date"] <= args.window_end)
    ].dropna(subset=[args.ticker]).copy()

    params = DCAParams(
        contribution_per_month=args.contribution_per_month,
        ticker=args.ticker,
    )

    ledger, metrics = simulate(daily=daily, params=params)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(format_report(
        metrics=metrics, params=params,
        window_start=args.window_start, window_end=args.window_end,
        code_revision=args.code_revision,
        run_timestamp=dt.datetime.now(tz=dt.timezone.utc),
    ))
    ledger.to_csv(args.csv, index=False)

    print(f"window: {args.window_start} → {args.window_end}")
    print(f"contribution: ${params.contribution_per_month:,.2f}/month, ticker: {params.ticker}\n")
    for m in metrics:
        if not m:
            continue
        print(f"[{m['mode']}]")
        print(f"  contributed:  ${m['total_contributed']:>12,.2f}")
        print(f"  terminal MV:  ${m['terminal_market_value']:>12,.2f}")
        print(f"  profit:       ${m['profit_dollars']:>12,.2f} ({m['profit_pct']:+.2%})")
        print(f"  IRR:          {m['irr_annualized']:+.2%}")
        print(f"  max value DD: {m['max_value_dd_pct']:.2%}")
        print(f"  max P&L DD:   ${m['max_pnl_dd_dollars']:,.2f} ({m['max_pnl_dd_pct_of_contributed']:.2%} of contributed)")
        print()
    print(f"report -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
