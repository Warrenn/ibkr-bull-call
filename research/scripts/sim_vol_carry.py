"""v8 vol term structure carry strategy simulator.

Daily contango/backwardation gate on SVXY (-0.5× short-vol ETF):
- prior-day VIX/VIX3M < contango_threshold → long SVXY today
- prior-day VIX/VIX3M >= contango_threshold → flat (cash)

Risk overlays (per v8 spec):
- hard daily stop: not modeled (SVXY day is already realized
  at close; -0.5× leverage caps single-day loss at -50%)
- monthly_max_loss_pct: if intra-month NAV draws below
  -15% from month-start, suspend for rest of month
- always_kill_if cumulative_max_dd_pct < -40%: tracked but
  reported, not enforced (post-hoc kill check belongs to gate eval)

Slippage: 10 bps per regime flip (round-trip).
Commissions: 0 (IBKR retail commission-free).

Usage::

    uv run python -m research.scripts.sim_vol_carry \\
        --vol-data research/data/dataset-v1/vol_etps_daily.parquet \\
        --window-start 2021-04-30 --window-end 2026-04-30 \\
        --report research/reports/v8-vol-carry-full.md \\
        --csv research/reports/v8-vol-carry-full-ledger.csv
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


@dataclass(frozen=True)
class VolCarryParams:
    contango_threshold: float = 0.93
    backwardation_threshold: float = 1.00
    slippage_bps_per_flip: float = 10.0
    monthly_max_loss_pct: float = -0.15
    cumulative_dd_kill_pct: float = -0.40  # informational
    instrument: str = "SVXY"
    benchmark: str = "SPY"


def simulate(
    *,
    vol_daily: pd.DataFrame,
    spy_daily: pd.DataFrame,
    params: VolCarryParams,
) -> pd.DataFrame:
    """Run the daily vol-carry simulation.

    vol_daily: DataFrame with date + VXX/SVXY/VIX/VIX3M columns
    spy_daily: DataFrame with date + SPY column
    Returns daily ledger with: date, ratio, regime, in_position,
    flip, svxy_return, strategy_return, strategy_nav, spy_return,
    spy_nav, monthly_dd, suspended.
    """
    vol = vol_daily.copy()
    vol["date"] = pd.to_datetime(vol["date"])
    spy = spy_daily.rename(columns={params.benchmark: "spy_close"})[["date", "spy_close"]].copy()
    spy["date"] = pd.to_datetime(spy["date"])

    df = vol.merge(spy, on="date", how="inner").sort_values("date").reset_index(drop=True)
    df = df.dropna(subset=["VIX", "VIX3M", params.instrument, "spy_close"]).reset_index(drop=True)

    df["ratio"] = df["VIX"] / df["VIX3M"]
    df["svxy_return"] = df[params.instrument].pct_change()
    df["spy_return"] = df["spy_close"].pct_change()

    # Signal: prior-day ratio < threshold → long SVXY today
    df["prior_ratio"] = df["ratio"].shift(1)
    df["regime"] = df["prior_ratio"].apply(_classify_regime)
    df["target_in_position"] = df["regime"] == "contango"

    # Apply state machine: track in_position, suspended (monthly cap),
    # apply flips, slippage
    in_position = False
    suspended = False
    suspended_until_month: pd.Period | None = None
    month_start_nav = 1.0
    strategy_nav = 1.0
    spy_nav = 1.0
    rows: list[dict[str, object]] = []

    for i in range(len(df)):
        row = df.iloc[i]
        current_month = pd.Period(row["date"], freq="M")

        # New month: reset suspension, re-anchor month_start_nav
        if i == 0 or current_month != pd.Period(df.iloc[i - 1]["date"], freq="M"):
            month_start_nav = strategy_nav
            if suspended_until_month is not None and current_month > suspended_until_month:
                suspended = False
                suspended_until_month = None

        # Determine target position (suspended overrides)
        target = bool(row["target_in_position"]) and not suspended

        flip = target != in_position
        flip_cost = (params.slippage_bps_per_flip / 10000) if flip else 0.0

        # Compute realized return
        svxy_ret = row["svxy_return"]
        if pd.isna(svxy_ret):
            svxy_ret = 0.0
        strategy_return = (svxy_ret if in_position else 0.0) - flip_cost

        strategy_nav *= (1 + strategy_return)

        # SPY benchmark NAV (always invested)
        spy_ret = row["spy_return"]
        if pd.isna(spy_ret):
            spy_ret = 0.0
        spy_nav *= (1 + spy_ret)

        # Monthly DD check
        monthly_dd = (strategy_nav / month_start_nav) - 1
        if monthly_dd <= params.monthly_max_loss_pct and not suspended:
            suspended = True
            suspended_until_month = current_month
            # Exit position immediately at end-of-day on suspension trigger
            in_position = False
        else:
            in_position = target

        rows.append({
            "date": row["date"],
            "ratio": row["ratio"],
            "regime": row["regime"],
            "target_in_position": bool(row["target_in_position"]),
            "in_position": in_position,
            "suspended": suspended,
            "flip": flip,
            "svxy_return": svxy_ret,
            "strategy_return": strategy_return,
            "strategy_nav": strategy_nav,
            "spy_return": spy_ret,
            "spy_nav": spy_nav,
            "monthly_dd": monthly_dd,
        })

    return pd.DataFrame(rows)


def _classify_regime(ratio: float) -> str:
    if pd.isna(ratio):
        return "unknown"
    if ratio < 0.93:
        return "contango"
    if ratio < 1.00:
        return "gray"
    return "backwardation"


def aggregate_metrics(*, ledger: pd.DataFrame) -> dict[str, object]:
    if ledger.empty:
        return {"n_days": 0}

    s = ledger["strategy_return"].astype(float)
    b = ledger["spy_return"].astype(float)
    n_years = len(s) / 252

    s_total = float((1 + s).prod() - 1)
    b_total = float((1 + b).prod() - 1)
    s_cagr = (1 + s_total) ** (1 / n_years) - 1 if n_years > 0 else float("nan")
    b_cagr = (1 + b_total) ** (1 / n_years) - 1 if n_years > 0 else float("nan")

    s_mean = float(s.mean())
    s_std = float(s.std(ddof=1))
    s_sharpe = (s_mean / s_std * math.sqrt(252)) if s_std > 0 else float("nan")
    b_mean = float(b.mean())
    b_std = float(b.std(ddof=1))
    b_sharpe = (b_mean / b_std * math.sqrt(252)) if b_std > 0 else float("nan")

    # Drawdown analysis on strategy NAV
    nav = ledger["strategy_nav"].astype(float).values
    peaks = []
    p = nav[0] if len(nav) > 0 else 1.0
    for v in nav:
        p = max(p, v)
        peaks.append(p)
    peaks_arr = pd.Series(peaks, index=ledger.index)
    dd = (ledger["strategy_nav"] - peaks_arr) / peaks_arr
    max_dd = float(dd.min())

    calmar = s_cagr / abs(max_dd) if max_dd < 0 else float("inf")

    # t-stat for strategy mean > 0
    s_t = (s_mean / (s_std / math.sqrt(len(s)))) if s_std > 0 else float("nan")
    s_p = float(2 * stats.t.sf(abs(s_t), df=len(s) - 1)) if s_std > 0 else float("nan")

    # t-stat for outperformance vs SPY
    spread = s - b
    sp_mean = float(spread.mean())
    sp_std = float(spread.std(ddof=1))
    sp_t = (sp_mean / (sp_std / math.sqrt(len(spread)))) if sp_std > 0 else float("nan")
    sp_p = float(2 * stats.t.sf(abs(sp_t), df=len(spread) - 1)) if sp_std > 0 else float("nan")

    win_rate = float((s > 0).mean())
    win_rate_vs_bench = float((spread > 0).mean())

    worst_day = float(s.min())
    best_day = float(s.max())

    n_flips = int(ledger["flip"].sum())
    n_in_position = int(ledger["in_position"].sum())
    n_suspended_days = int(ledger["suspended"].sum())

    regime_counts = ledger["regime"].value_counts().to_dict()

    return {
        "n_days": len(ledger),
        "n_years": n_years,
        "n_in_position_days": n_in_position,
        "pct_time_in_position": n_in_position / len(ledger),
        "n_flips": n_flips,
        "n_suspended_days": n_suspended_days,
        "regime_counts": regime_counts,

        "strategy_total_return": s_total,
        "strategy_cagr": s_cagr,
        "strategy_sharpe": s_sharpe,
        "strategy_t_stat": s_t,
        "strategy_p_value": s_p,
        "strategy_max_dd": max_dd,
        "strategy_calmar": calmar,
        "strategy_win_rate": win_rate,
        "strategy_worst_day": worst_day,
        "strategy_best_day": best_day,

        "benchmark_total_return": b_total,
        "benchmark_cagr": b_cagr,
        "benchmark_sharpe": b_sharpe,

        "spread_total": s_total - b_total,
        "spread_cagr": s_cagr - b_cagr,
        "spread_t_stat": sp_t,
        "spread_p_value": sp_p,
        "spread_win_rate": win_rate_vs_bench,
    }


def format_report(
    *,
    metrics: dict[str, object],
    params: VolCarryParams,
    window_start: dt.date,
    window_end: dt.date,
    code_revision: str,
    run_timestamp: dt.datetime,
) -> str:
    if metrics.get("n_days", 0) == 0:
        return "# v8 Vol Carry — no data\n"

    lines = [
        "# v8 Vol Term Structure Carry (SVXY contango gate)",
        "",
        f"**Window**: {window_start.isoformat()} → {window_end.isoformat()}",
        "",
        "## Strategy parameters",
        "",
        f"- instrument: {params.instrument} (-0.5× short-vol ETF)",
        f"- contango_threshold: VIX/VIX3M < {params.contango_threshold:.2f} → long",
        f"- monthly_max_loss_pct: {params.monthly_max_loss_pct:.0%}",
        f"- slippage_bps_per_flip: {params.slippage_bps_per_flip:.0f}",
        f"- benchmark: {params.benchmark}",
        "",
        "## Provenance",
        "",
        f"- code_revision: `{code_revision}`",
        f"- run_timestamp_utc: {run_timestamp.isoformat()}",
        "",
        "## Aggregate results",
        "",
        f"- n_days: {metrics['n_days']}",
        f"- n_years: {metrics['n_years']:.2f}",
        f"- in-position days: {metrics['n_in_position_days']} ({metrics['pct_time_in_position']:.1%})",
        f"- regime flips (entries+exits): {metrics['n_flips']}",
        f"- suspended days (monthly cap fired): {metrics['n_suspended_days']}",
        f"- regime counts: {metrics['regime_counts']}",
        "",
        "## Performance vs benchmark",
        "",
        "| Metric | Strategy | SPY | Spread |",
        "|---|---|---|---|",
        f"| Total return | **{metrics['strategy_total_return']:+.2%}** | {metrics['benchmark_total_return']:+.2%} | {metrics['spread_total']:+.2%} |",
        f"| CAGR | **{metrics['strategy_cagr']:+.2%}** | {metrics['benchmark_cagr']:+.2%} | {metrics['spread_cagr']:+.2%} |",
        f"| Sharpe (annualized) | **{metrics['strategy_sharpe']:+.2f}** | {metrics['benchmark_sharpe']:+.2f} | — |",
        f"| Win rate (days > 0) | {metrics['strategy_win_rate']:.1%} | — | beat SPY: {metrics['spread_win_rate']:.1%} |",
        f"| Worst day | **{metrics['strategy_worst_day']:.2%}** | — | — |",
        f"| Best day | {metrics['strategy_best_day']:.2%} | — | — |",
        f"| Max drawdown | **{metrics['strategy_max_dd']:.2%}** | — | — |",
        f"| Calmar ratio | **{metrics['strategy_calmar']:.2f}** | — | — |",
        "",
        "## Statistical significance",
        "",
        "| Hypothesis | t-stat | p-value | Verdict |",
        "|---|---|---|---|",
        f"| Strategy mean > 0 | {metrics['strategy_t_stat']:+.2f} | {metrics['strategy_p_value']:.4f} | {'significant' if metrics['strategy_p_value'] < 0.05 else 'inconclusive'} |",
        f"| Strategy outperforms SPY | {metrics['spread_t_stat']:+.2f} | {metrics['spread_p_value']:.4f} | {'significant' if metrics['spread_p_value'] < 0.05 else 'inconclusive'} |",
        "",
        "## Caveats",
        "",
        "- Volmageddon (2018-02-05) is OUTSIDE the post-2021 dataset-v1",
        "  window. Tail-risk inference is theoretical. SVXY's -0.5×",
        "  leverage caps single-day loss at -50% but real fills could be",
        "  worse on stress gaps.",
        "- Daily-close pricing only; signal computed on prior-day close,",
        "  trade attribution to today's return. No intraday execution",
        "  modeled.",
        "- 10 bps slippage per regime flip; real fills on SVXY may be",
        "  wider (lower liquidity than SPY/major ETFs).",
        "- No commissions modeled.",
        "- The contango gate is a single threshold (0.93); more",
        "  sophisticated signal smoothing is deferred to v8a.",
    ]

    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="sim_vol_carry")
    p.add_argument("--vol-data", type=Path,
                   default=Path("research/data/dataset-v1/vol_etps_daily.parquet"))
    p.add_argument("--spy-data", type=Path,
                   default=Path("research/data/dataset-v1/sector_etfs_daily.parquet"))
    p.add_argument("--window-start", type=dt.date.fromisoformat, required=True)
    p.add_argument("--window-end", type=dt.date.fromisoformat, required=True)
    p.add_argument("--contango-threshold", type=float, default=0.93)
    p.add_argument("--monthly-max-loss-pct", type=float, default=-0.15)
    p.add_argument("--slippage-bps", type=float, default=10.0)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--csv", type=Path, required=True)
    p.add_argument("--code-revision", default="HEAD")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    vol_daily = pd.read_parquet(args.vol_data)
    spy_daily = pd.read_parquet(args.spy_data)
    # Filter to window
    vol_daily = vol_daily[
        (vol_daily["date"] >= args.window_start) & (vol_daily["date"] <= args.window_end)
    ].copy()
    spy_daily = spy_daily[
        (spy_daily["date"] >= args.window_start) & (spy_daily["date"] <= args.window_end)
    ].copy()

    params = VolCarryParams(
        contango_threshold=args.contango_threshold,
        monthly_max_loss_pct=args.monthly_max_loss_pct,
        slippage_bps_per_flip=args.slippage_bps,
    )

    ledger = simulate(vol_daily=vol_daily, spy_daily=spy_daily, params=params)
    metrics = aggregate_metrics(ledger=ledger)

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
    if metrics.get("n_days", 0) > 0:
        print(f"days: {metrics['n_days']} ({metrics['n_years']:.2f}y)")
        print(f"in position: {metrics['n_in_position_days']} ({metrics['pct_time_in_position']:.1%})")
        print(f"flips: {metrics['n_flips']}, suspended days: {metrics['n_suspended_days']}")
        print(f"regimes: {metrics['regime_counts']}")
        print(f"strategy total return: {metrics['strategy_total_return']:+.2%}")
        print(f"strategy CAGR: {metrics['strategy_cagr']:+.2%}")
        print(f"strategy Sharpe: {metrics['strategy_sharpe']:+.2f}")
        print(f"strategy max DD: {metrics['strategy_max_dd']:.2%}")
        print(f"strategy Calmar: {metrics['strategy_calmar']:+.2f}")
        print(f"benchmark CAGR: {metrics['benchmark_cagr']:+.2%}")
        print(f"spread (strat - bench) CAGR: {metrics['spread_cagr']:+.2%}")
        print(f"strategy worst/best day: {metrics['strategy_worst_day']:+.2%} / {metrics['strategy_best_day']:+.2%}")
    print(f"\nreport -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
