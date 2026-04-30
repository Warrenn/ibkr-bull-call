"""Compute risk-adjusted financial metrics on a directional-edge ledger.

Inputs: a ledger CSV with columns ``date``, ``entered``,
``forward_return``. Output: markdown report with Sharpe / Calmar /
drawdown / tail metrics, plus a simple monthly-cap overlay
simulation.

The ledger represents UNDERLYING (ES) directional returns — not
options P&L. Real bull-call-spread P&L would depend on strike
placement, debit paid, and bid-ask spreads. This analysis tells us
about the *signal's* risk profile; the options structure adds
non-linear payoffs and microstructure costs not modeled here.

Usage::

    uv run python -m research.scripts.risk_adjusted_metrics \\
        --ledger research/reports/v2-rerun-60mo-ledger.csv \\
        --label "v2 60mo full-window" \\
        --report research/reports/v2-risk-adjusted-metrics.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class RiskMetrics:
    n_trades: int
    n_years: float
    trades_per_year: float

    mean_return: float
    median_return: float
    std_return: float
    win_rate: float

    sharpe_per_trade: float
    sharpe_annualized: float

    max_drawdown: float
    drawdown_duration_trades: int
    calmar_ratio: float

    max_loss: float
    max_win: float
    p05_return: float
    p95_return: float

    worst_month_return: float
    worst_year_return: float
    n_negative_months: int
    n_total_months: int

    # With $1000 monthly cap
    capped_total_return: float
    capped_n_skipped_due_to_cap: int
    capped_n_months_hit_cap: int


def compute_metrics(ledger: pd.DataFrame) -> RiskMetrics:
    entered = ledger[ledger["entered"] == True].copy()  # noqa: E712
    entered["date"] = pd.to_datetime(entered["date"]).dt.date
    entered = entered.sort_values("date").reset_index(drop=True)

    n = len(entered)
    fr = entered["forward_return"].astype(float)

    earliest = entered["date"].min()
    latest = entered["date"].max()
    years = (latest - earliest).days / 365.25
    trades_per_year = n / years if years > 0 else 0.0

    mean_return = float(fr.mean())
    median_return = float(fr.median())
    std_return = float(fr.std(ddof=1))
    win_rate = float((fr > 0).mean())

    sharpe_per_trade = mean_return / std_return if std_return > 0 else float("nan")
    sharpe_annualized = sharpe_per_trade * math.sqrt(trades_per_year)

    # Drawdown analysis on cumulative returns
    cumulative = fr.cumsum()
    running_peak = cumulative.cummax()
    drawdowns = cumulative - running_peak
    max_dd = float(drawdowns.min())

    # Drawdown duration: longest stretch from peak to recovery
    in_dd = drawdowns < 0
    dd_runs: list[int] = []
    cur_run = 0
    for v in in_dd:
        if v:
            cur_run += 1
        else:
            if cur_run > 0:
                dd_runs.append(cur_run)
            cur_run = 0
    if cur_run > 0:
        dd_runs.append(cur_run)
    dd_duration = max(dd_runs) if dd_runs else 0

    annual_return = mean_return * trades_per_year  # arithmetic average annualized
    calmar = annual_return / abs(max_dd) if max_dd < 0 else float("inf")

    max_loss = float(fr.min())
    max_win = float(fr.max())
    p05 = float(fr.quantile(0.05))
    p95 = float(fr.quantile(0.95))

    # Per-month aggregation
    monthly = entered.copy()
    monthly["ym"] = pd.to_datetime(monthly["date"]).dt.to_period("M")
    monthly_pnl = monthly.groupby("ym")["forward_return"].sum()
    n_total_months = int(len(monthly_pnl))
    n_neg_months = int((monthly_pnl < 0).sum())
    worst_month = float(monthly_pnl.min())

    # Per-year aggregation
    yearly = entered.copy()
    yearly["year"] = pd.to_datetime(yearly["date"]).dt.year
    yearly_pnl = yearly.groupby("year")["forward_return"].sum()
    worst_year = float(yearly_pnl.min())

    # Monthly-cap overlay: stop trading for the rest of the month if
    # cumulative monthly return drops below -1% (representative
    # account-level threshold; user's bot uses $1000 / capital % equivalent).
    # We simulate at -1.0% monthly threshold on the underlying signal's
    # cumulative trade returns.
    capped_total = 0.0
    capped_skipped = 0
    months_capped = 0
    by_month = entered.copy()
    by_month["ym"] = pd.to_datetime(by_month["date"]).dt.to_period("M")

    cap_threshold = -0.01  # -1% cumulative monthly trade-return
    for ym, group in by_month.groupby("ym"):
        cumulative_in_month = 0.0
        hit_cap = False
        for _, row in group.iterrows():
            if hit_cap:
                capped_skipped += 1
                continue
            cumulative_in_month += row["forward_return"]
            capped_total += row["forward_return"]
            if cumulative_in_month <= cap_threshold:
                hit_cap = True
        if hit_cap:
            months_capped += 1

    return RiskMetrics(
        n_trades=n,
        n_years=years,
        trades_per_year=trades_per_year,
        mean_return=mean_return,
        median_return=median_return,
        std_return=std_return,
        win_rate=win_rate,
        sharpe_per_trade=sharpe_per_trade,
        sharpe_annualized=sharpe_annualized,
        max_drawdown=max_dd,
        drawdown_duration_trades=dd_duration,
        calmar_ratio=calmar,
        max_loss=max_loss,
        max_win=max_win,
        p05_return=p05,
        p95_return=p95,
        worst_month_return=worst_month,
        worst_year_return=worst_year,
        n_negative_months=n_neg_months,
        n_total_months=n_total_months,
        capped_total_return=capped_total,
        capped_n_skipped_due_to_cap=capped_skipped,
        capped_n_months_hit_cap=months_capped,
    )


def format_report(
    *,
    metrics: RiskMetrics,
    label: str,
    ledger_path: Path,
    code_revision: str,
    run_timestamp: dt.datetime,
) -> str:
    m = metrics
    lines = [
        f"# Risk-Adjusted Metrics — {label}",
        "",
        "## Provenance",
        "",
        f"- code_revision: `{code_revision}`",
        f"- run_timestamp_utc: {run_timestamp.isoformat()}",
        f"- ledger: `{ledger_path.name}`",
        "",
        "## Sample",
        "",
        f"- n_trades: **{m.n_trades}**",
        f"- years_covered: {m.n_years:.2f}",
        f"- trades_per_year: {m.trades_per_year:.1f}",
        "",
        "## Per-trade distribution",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| mean_return | **{m.mean_return:+.4%}** |",
        f"| median_return | {m.median_return:+.4%} |",
        f"| std_return | {m.std_return:.4%} |",
        f"| win_rate | {m.win_rate:.1%} |",
        f"| max_loss (worst trade) | {m.max_loss:+.4%} |",
        f"| max_win (best trade) | {m.max_win:+.4%} |",
        f"| p05 (5th percentile) | {m.p05_return:+.4%} |",
        f"| p95 (95th percentile) | {m.p95_return:+.4%} |",
        "",
        "## Risk-adjusted ratios",
        "",
        "| Metric | Value | Interpretation |",
        "|---|---|---|",
        f"| Sharpe per trade | **{m.sharpe_per_trade:.3f}** | mean/std per trade |",
        f"| Sharpe annualized | **{m.sharpe_annualized:.2f}** | × √(trades/year) |",
        f"| Calmar ratio | **{m.calmar_ratio:.2f}** | annual_return / max_DD |",
        "",
        "**Sharpe interpretation**: > 1.0 is acceptable, > 2.0 is good, > 3.0 is",
        "exceptional. **Calmar interpretation**: > 0.5 is acceptable, > 1.0",
        "is good (recovers max-DD in less than a year of average performance).",
        "",
        "## Drawdown profile",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| max_drawdown (cumulative trade returns) | **{m.max_drawdown:+.4%}** |",
        f"| max_dd_duration (trades) | {m.drawdown_duration_trades} |",
        "",
        "## Calendar-period concentration",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| worst_month | **{m.worst_month_return:+.4%}** |",
        f"| worst_year | **{m.worst_year_return:+.4%}** |",
        f"| n_negative_months / n_total_months | {m.n_negative_months}/{m.n_total_months} ({m.n_negative_months/m.n_total_months:.0%}) |",
        "",
        "## Monthly-cap overlay (-1% cumulative monthly threshold)",
        "",
        "Simulates user's "
        "\"stop trading after $1000 monthly loss\" rule applied to "
        "underlying signal returns. Cap threshold is -1% cumulative trade-return per month.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| total_return WITH cap | **{m.capped_total_return:+.4%}** |",
        f"| total_return WITHOUT cap | {m.mean_return * m.n_trades:+.4%} |",
        f"| trades skipped due to cap | {m.capped_n_skipped_due_to_cap} |",
        f"| months that hit cap | {m.capped_n_months_hit_cap} of {m.n_total_months} |",
        "",
        "## What this tells us",
        "",
        "Sharpe annualized > 1.0 = **the strategy has acceptable risk-adjusted",
        "return** if it can be executed at scale.",
        "",
        "Sharpe annualized 0.5-1.0 = mediocre. Strategy may have some edge but",
        "not enough to justify the operational complexity.",
        "",
        "Sharpe annualized < 0.5 = strategy doesn't survive risk adjustment.",
        "The mean return is too small relative to the variance.",
        "",
        "**Critical caveat**: this analysis is on UNDERLYING (ES) returns, not",
        "options P&L. A bull-call-spread expression of this signal would have:",
        "",
        "- **Different P&L mechanics** — non-linear payoffs that may amplify",
        "  small wins (good) but also magnify variance via the leverage of",
        "  cheap OTM options.",
        "- **Bid-ask + commissions** — typically $20-50 per round-trip on a",
        "  retail bull call spread; a +0.22% underlying move that prices $5/contract",
        "  in option terms would barely cover that.",
        "- **Tail-day amplification** — on +9.05% days the OTM bull call spread",
        "  could pay 5-10x; on -1% days it expires worthless. Higher kurtosis.",
        "",
        "So the underlying-signal Sharpe is an **upper bound** on what the",
        "options strategy could achieve; reality would be lower after frictions.",
    ]
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="risk_adjusted_metrics")
    p.add_argument("--ledger", type=Path, required=True)
    p.add_argument("--label", required=True,
                   help="Human-readable label for the report header")
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--code-revision", default="HEAD")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    ledger = pd.read_csv(args.ledger)
    metrics = compute_metrics(ledger)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(format_report(
        metrics=metrics,
        label=args.label,
        ledger_path=args.ledger,
        code_revision=args.code_revision,
        run_timestamp=dt.datetime.now(tz=dt.timezone.utc),
    ))

    print(f"label: {args.label}")
    print(f"n_trades: {metrics.n_trades}")
    print(f"mean_return: {metrics.mean_return:+.4%}")
    print(f"sharpe_per_trade: {metrics.sharpe_per_trade:.3f}")
    print(f"sharpe_annualized: {metrics.sharpe_annualized:.2f}")
    print(f"calmar: {metrics.calmar_ratio:.2f}")
    print(f"max_drawdown: {metrics.max_drawdown:+.4%}")
    print(f"worst_month: {metrics.worst_month_return:+.4%}")
    print(f"worst_year: {metrics.worst_year_return:+.4%}")
    print(f"with -1% monthly cap: total return {metrics.capped_total_return:+.4%}, {metrics.capped_n_skipped_due_to_cap} trades skipped")
    print(f"\nreport -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
