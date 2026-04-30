"""v5 iron-condor backtest simulator.

Simulates a short iron condor opened daily at 09:30 ET, closed
(at expiry) at 15:55 ET / final settle. Uses Black-Scholes with
VIX-as-IV approximation since we don't have real chain bid/ask
data.

Position structure (short iron condor, far-OTM):

- SELL call at ``short_call_K = S0 × (1 + sigmas × sigma_daily)``
- BUY call at  ``long_call_K  = short_call_K × (1 + wing_width)``
- SELL put  at ``short_put_K  = S0 × (1 - sigmas × sigma_daily)``
- BUY put   at ``long_put_K   = short_put_K  × (1 - wing_width)``

where ``sigma_daily = (prior_VIX / 100) / sqrt(252)``.

Net credit collected at open. Final P&L at close:

- ``call_spread_payoff = max(0, S - long_call_K) - max(0, S - short_call_K)``
- ``put_spread_payoff  = max(0, long_put_K - S) - max(0, short_put_K - S)``
- ``pnl_per_share = net_credit + call_spread_payoff + put_spread_payoff``
- Multiply by ``$100/contract × contracts``

Operational features:
- Monthly capital control: if cumulative monthly P&L hits
  ``-monthly_max_loss_usd``, skip rest of the month.
- Event-day skip: optional, drop FOMC/CPI/NFP/OPEX days from
  trading.
- Half-day skip: NYSE half-days excluded (15:55 ET past close).

Usage::

    uv run python -m research.scripts.sim_iron_condor \\
        --es research/data/dataset-v1/es_intraday.parquet \\
        --vix research/data/dataset-v1/vix_daily.parquet \\
        --calendar research/data/dataset-v1/trading_calendar.parquet \\
        --window-start 2021-05-03 --window-end 2026-04-29 \\
        --sigmas 1.0 --wing-width-pct 0.005 \\
        --monthly-max-loss-usd 1000 \\
        --report research/reports/v5-iron-condor-sim.md \\
        --csv research/reports/v5-iron-condor-sim-ledger.csv
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from scipy import stats

from research.scripts.run_directional_edge_v1 import _load_bars_parquet


_TRADING_DAYS_PER_YEAR = 252
_HOURS_PER_TRADING_SESSION = 6.5  # 9:30 → 16:00


@dataclass(frozen=True)
class IronCondorParams:
    short_strike_distance_sigmas: float = 1.0
    wing_width_pct: float = 0.005  # 0.5% of S0
    risk_free_rate: float = 0.05
    contracts_per_trade: int = 1
    multiplier_per_contract: float = 100.0  # SPX standard
    monthly_max_loss_usd: float = 1000.0
    open_time_hours_to_expiry: float = 6.5  # 9:30 ET to 16:00 ET


def _bs_call_price(
    S: float, K: float, T: float, r: float, sigma: float,
) -> float:
    """Black-Scholes call. Handles T==0 (expiry) and sigma==0 edge cases."""

    if T <= 0:
        return max(0.0, S - K)
    if sigma <= 0:
        return max(0.0, S - K * math.exp(-r * T))
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + sigma**2 / 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return float(
        S * stats.norm.cdf(d1) - K * math.exp(-r * T) * stats.norm.cdf(d2)
    )


def _bs_put_price(
    S: float, K: float, T: float, r: float, sigma: float,
) -> float:
    """Black-Scholes put."""

    if T <= 0:
        return max(0.0, K - S)
    if sigma <= 0:
        return max(0.0, K * math.exp(-r * T) - S)
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + sigma**2 / 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return float(
        K * math.exp(-r * T) * stats.norm.cdf(-d2) - S * stats.norm.cdf(-d1)
    )


def simulate_one_trade(
    *,
    S0: float,
    S_close: float,
    prior_vix: float,
    params: IronCondorParams,
) -> dict[str, float]:
    """Simulate one iron-condor trade open-to-close, no intraday stop."""

    sigma = prior_vix / 100  # VIX index → vol fraction
    T = params.open_time_hours_to_expiry / 24 / 365
    sigma_daily = sigma / math.sqrt(_TRADING_DAYS_PER_YEAR)
    move_dist_pct = params.short_strike_distance_sigmas * sigma_daily

    short_call_K = S0 * (1 + move_dist_pct)
    long_call_K = short_call_K * (1 + params.wing_width_pct)
    short_put_K = S0 * (1 - move_dist_pct)
    long_put_K = short_put_K * (1 - params.wing_width_pct)

    # BS prices at open
    sc_p = _bs_call_price(S0, short_call_K, T, params.risk_free_rate, sigma)
    lc_p = _bs_call_price(S0, long_call_K, T, params.risk_free_rate, sigma)
    sp_p = _bs_put_price(S0, short_put_K, T, params.risk_free_rate, sigma)
    lp_p = _bs_put_price(S0, long_put_K, T, params.risk_free_rate, sigma)

    net_credit = (sc_p - lc_p) + (sp_p - lp_p)

    # Final P&L at expiry
    # Call spread (we're SHORT short_call, LONG long_call):
    # payoff = max(0, S - long_call_K) - max(0, S - short_call_K)
    call_payoff = max(0.0, S_close - long_call_K) - max(0.0, S_close - short_call_K)
    # Put spread (we're SHORT short_put, LONG long_put):
    # payoff = max(0, long_put_K - S) - max(0, short_put_K - S)
    put_payoff = max(0.0, long_put_K - S_close) - max(0.0, short_put_K - S_close)

    pnl_per_share = net_credit + call_payoff + put_payoff
    pnl_per_contract = pnl_per_share * params.multiplier_per_contract
    pnl_total = pnl_per_contract * params.contracts_per_trade

    # Theoretical max loss per contract: wider of the two wings × multiplier
    # minus the net credit.
    max_wing_pts = max(
        long_call_K - short_call_K,
        short_put_K - long_put_K,
    )
    max_loss_per_contract = (
        max_wing_pts * params.multiplier_per_contract
        - net_credit * params.multiplier_per_contract
    )

    return {
        "S0": float(S0),
        "S_close": float(S_close),
        "prior_vix": float(prior_vix),
        "sigma_daily_pct": float(sigma_daily * 100),
        "short_call_K": float(short_call_K),
        "long_call_K": float(long_call_K),
        "short_put_K": float(short_put_K),
        "long_put_K": float(long_put_K),
        "wing_width_pts": float(long_call_K - short_call_K),
        "net_credit_per_share": float(net_credit),
        "max_loss_per_contract": float(max_loss_per_contract),
        "pnl_per_share": float(pnl_per_share),
        "pnl_per_contract": float(pnl_per_contract),
        "pnl_total": float(pnl_total),
        "winner": pnl_total > 0,
    }


def extract_daily_data(
    *,
    bars: pd.DataFrame,
    calendar: pd.DataFrame,
    vix: pd.DataFrame,
    et_tz: str = "America/New_York",
    open_time_et: str = "09:30",
    close_time_et: str = "15:55",
) -> pd.DataFrame:
    """Per-NYSE-full-trading-day open + close prices + prior-day VIX."""

    bars = bars.copy()
    bars["ts_et"] = bars["ts_utc"].dt.tz_convert(et_tz)
    bars["date_et"] = bars["ts_et"].dt.date
    bars["time_et"] = bars["ts_et"].dt.strftime("%H:%M:%S")

    full_trading = set(
        calendar[
            calendar["is_trading_day"] & ~calendar["is_half_day"]
        ]["date"].tolist()
    )
    bars = bars[bars["date_et"].isin(full_trading)]

    open_t = f"{open_time_et}:00"
    close_t = f"{close_time_et}:00"

    opens = (
        bars[bars["time_et"] == open_t][["date_et", "open"]]
        .copy().rename(columns={"open": "open_price"})
        .drop_duplicates(subset=["date_et"]).set_index("date_et")
    )
    closes = (
        bars[bars["time_et"] == close_t][["date_et", "open"]]
        .copy().rename(columns={"open": "close_price"})
        .drop_duplicates(subset=["date_et"]).set_index("date_et")
    )
    df = pd.concat([opens, closes], axis=1).dropna()
    df = df.reset_index().rename(columns={"date_et": "date"})

    vix_sorted = vix.sort_values("date").reset_index(drop=True)
    prior_vix_map: dict[dt.date, float] = {
        vix_sorted["date"].iloc[i]: float(vix_sorted["close"].iloc[i - 1])
        for i in range(1, len(vix_sorted))
    }
    df["prior_vix"] = df["date"].map(prior_vix_map)
    df = df.dropna(subset=["prior_vix"]).copy()
    return df.sort_values("date").reset_index(drop=True)


def run_simulation(
    *,
    daily_data: pd.DataFrame,
    params: IronCondorParams,
    excluded_dates: set[dt.date] | None = None,
) -> tuple[pd.DataFrame, dict[tuple[int, int], float]]:
    """Run iron-condor simulation per day with monthly capital control.

    Returns (per-trade DataFrame, monthly P&L dict).
    """

    results: list[dict[str, Any]] = []
    monthly_pnl: dict[tuple[int, int], float] = {}
    monthly_stopped: set[tuple[int, int]] = set()
    excluded = excluded_dates or set()

    for _, row in daily_data.iterrows():
        date = row["date"]
        ym = (date.year, date.month)

        if date in excluded:
            results.append({
                "date": date, "skipped": True,
                "skip_reason": "event_day",
                "pnl_total": 0.0, "winner": False,
            })
            continue

        if ym in monthly_stopped:
            results.append({
                "date": date, "skipped": True,
                "skip_reason": "monthly_stop",
                "pnl_total": 0.0, "winner": False,
            })
            continue

        trade = simulate_one_trade(
            S0=row["open_price"],
            S_close=row["close_price"],
            prior_vix=row["prior_vix"],
            params=params,
        )
        trade["date"] = date
        trade["skipped"] = False
        trade["skip_reason"] = ""
        results.append(trade)

        new_monthly_pnl = monthly_pnl.get(ym, 0.0) + trade["pnl_total"]
        monthly_pnl[ym] = new_monthly_pnl

        if new_monthly_pnl <= -params.monthly_max_loss_usd:
            monthly_stopped.add(ym)

    df = pd.DataFrame(results)
    return df, monthly_pnl


def aggregate_metrics(
    *,
    ledger: pd.DataFrame,
    monthly_pnl: dict[tuple[int, int], float],
    params: IronCondorParams,
) -> dict[str, Any]:
    traded = ledger[~ledger["skipped"]]
    n_total = len(ledger)
    n_traded = len(traded)
    n_skipped_event = (ledger["skip_reason"] == "event_day").sum()
    n_skipped_stop = (ledger["skip_reason"] == "monthly_stop").sum()

    pnls = traded["pnl_total"].astype(float)
    if len(pnls) > 0:
        total_pnl = float(pnls.sum())
        mean_pnl = float(pnls.mean())
        median_pnl = float(pnls.median())
        std_pnl = float(pnls.std(ddof=1)) if len(pnls) > 1 else float("nan")
        win_rate = float((pnls > 0).mean())
        max_win = float(pnls.max())
        max_loss = float(pnls.min())
    else:
        total_pnl = mean_pnl = median_pnl = std_pnl = float("nan")
        win_rate = max_win = max_loss = float("nan")

    # Equity curve and max drawdown
    equity = pnls.cumsum().tolist() if len(pnls) > 0 else [0.0]
    if equity:
        peak = equity[0]
        max_dd = 0.0
        for v in equity:
            peak = max(peak, v)
            max_dd = min(max_dd, v - peak)
    else:
        max_dd = 0.0

    monthly_pnls = list(monthly_pnl.values())
    n_months = len(monthly_pnls)
    n_winning_months = sum(1 for x in monthly_pnls if x > 0)
    n_stopped_months = n_skipped_stop > 0  # at least one monthly stop fired

    # Per-year P&L (calendar year)
    if n_traded > 0:
        traded_y = traded.assign(
            year=lambda d: pd.to_datetime(d["date"]).dt.year,
        )
        by_year = traded_y.groupby("year").agg(
            n=("pnl_total", "size"),
            total_pnl=("pnl_total", "sum"),
            mean_pnl=("pnl_total", "mean"),
            win_rate=("winner", "mean"),
        ).reset_index()
    else:
        by_year = pd.DataFrame()

    return {
        "n_total_days": n_total,
        "n_traded": n_traded,
        "n_skipped_event": int(n_skipped_event),
        "n_skipped_stop": int(n_skipped_stop),
        "total_pnl": total_pnl,
        "mean_pnl_per_trade": mean_pnl,
        "median_pnl_per_trade": median_pnl,
        "std_pnl_per_trade": std_pnl,
        "win_rate": win_rate,
        "max_win": max_win,
        "max_loss": max_loss,
        "max_drawdown": max_dd,
        "n_months": n_months,
        "n_winning_months": n_winning_months,
        "n_stopped_months": int(monthly_stopped_count(ledger)),
        "by_year": by_year,
    }


def monthly_stopped_count(ledger: pd.DataFrame) -> int:
    """Count distinct (year, month) pairs that hit monthly stop."""

    stopped = ledger[ledger["skip_reason"] == "monthly_stop"]
    if len(stopped) == 0:
        return 0
    ym = stopped["date"].apply(lambda d: (d.year, d.month))
    return int(ym.nunique())


def format_report(
    *,
    metrics: dict[str, Any],
    params: IronCondorParams,
    window_start: dt.date,
    window_end: dt.date,
    es_path: Path,
    code_revision: str,
    run_timestamp: dt.datetime,
    event_filter_active: bool,
) -> str:
    by_year = metrics["by_year"]

    lines = [
        "# v5 Iron-Condor Simulator",
        "",
        f"**Window**: {window_start.isoformat()} → {window_end.isoformat()}",
        "",
        "## Configuration",
        "",
        f"- short_strike_distance_sigmas: **{params.short_strike_distance_sigmas}**",
        f"- wing_width_pct: **{params.wing_width_pct:.2%}**",
        f"- risk_free_rate: {params.risk_free_rate:.2%}",
        f"- contracts_per_trade: {params.contracts_per_trade}",
        f"- multiplier_per_contract: ${params.multiplier_per_contract:.0f}",
        f"- monthly_max_loss_usd: ${params.monthly_max_loss_usd:.0f}",
        f"- pricing: Black-Scholes with VIX-as-IV (no real bid/ask)",
        f"- event filter: {'enabled' if event_filter_active else 'disabled'}",
        "",
        "## Provenance",
        "",
        f"- code_revision: `{code_revision}`",
        f"- run_timestamp_utc: {run_timestamp.isoformat()}",
        f"- ES dataset: `{es_path.name}`",
        "",
        "## Aggregate Results",
        "",
        f"| Metric | Value |",
        f"|---|---|",
        f"| total days in window | {metrics['n_total_days']} |",
        f"| trades executed | {metrics['n_traded']} |",
        f"| skipped (event filter) | {metrics['n_skipped_event']} |",
        f"| skipped (monthly stop) | {metrics['n_skipped_stop']} |",
        f"| **total P&L** | **${metrics['total_pnl']:.0f}** |",
        f"| mean P&L per trade | ${metrics['mean_pnl_per_trade']:.2f} |",
        f"| median P&L per trade | ${metrics['median_pnl_per_trade']:.2f} |",
        f"| std P&L per trade | ${metrics['std_pnl_per_trade']:.2f} |",
        f"| win rate | {metrics['win_rate']:.1%} |",
        f"| max single-trade win | ${metrics['max_win']:.0f} |",
        f"| max single-trade loss | ${metrics['max_loss']:.0f} |",
        f"| max drawdown (cumulative) | ${metrics['max_drawdown']:.0f} |",
        f"| months in window | {metrics['n_months']} |",
        f"| winning months | {metrics['n_winning_months']} |",
        f"| months that hit stop | {metrics['n_stopped_months']} |",
        "",
    ]

    if not by_year.empty:
        lines.extend([
            "## Per-Year Breakdown",
            "",
            "| year | n | total_pnl | mean_pnl | win_rate |",
            "|---|---|---|---|---|",
        ])
        for _, row in by_year.iterrows():
            lines.append(
                f"| {int(row['year'])} | {int(row['n'])} | "
                f"${row['total_pnl']:.0f} | "
                f"${row['mean_pnl']:.2f} | "
                f"{row['win_rate']:.1%} |"
            )

    lines.extend([
        "",
        "## Key caveats",
        "",
        "- **No bid/ask spread** in the simulation. BS-with-VIX-as-IV gives",
        "  mid-implied prices; real iron condors pay the bid-ask on 4 legs",
        "  per round-trip. For SPX 0DTE far-OTM, that's typically $0.05-$0.20",
        "  per contract per leg = $20-80 / round-trip cost not modeled here.",
        "- **No intraday stop loss**. Simulation holds to settlement; real",
        "  strategy would stop a tested side mid-day. This OVERSTATES",
        "  losses on big-move days (the actual strategy would close at the",
        "  stop, not let it run to max loss).",
        "- **VIX-as-IV approximation**. Real options trade at strike-",
        "  specific implied vols (the smile / skew). Far-OTM puts trade",
        "  at higher IV than ATM (skew); using flat VIX may UNDERESTIMATE",
        "  put credit and OVERESTIMATE call credit by a few percent.",
        "- **Monthly capital control** is applied but the threshold is a",
        "  hard parameter; real operators may use trailing limits.",
        "- **Pricing edge cases**: when both wings are deep OTM, BS prices",
        "  approach zero — real markets have minimum bid (~$0.05), so",
        "  far-OTM credits are actually slightly higher in reality.",
    ])
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="sim_iron_condor")
    p.add_argument("--es", type=Path,
                   default=Path("research/data/dataset-v1/es_intraday.parquet"))
    p.add_argument("--vix", type=Path,
                   default=Path("research/data/dataset-v1/vix_daily.parquet"))
    p.add_argument("--calendar", type=Path,
                   default=Path("research/data/dataset-v1/trading_calendar.parquet"))
    p.add_argument("--event-calendar", type=Path, default=None,
                   help="Optional event_calendar.parquet to skip event days")
    p.add_argument("--window-start", type=dt.date.fromisoformat, required=True)
    p.add_argument("--window-end", type=dt.date.fromisoformat, required=True)
    p.add_argument("--sigmas", type=float, default=1.0,
                   help="Short-strike distance from ATM in sigmas (default: 1.0)")
    p.add_argument("--wing-width-pct", type=float, default=0.005,
                   help="Wing width as fraction of S0 (default: 0.005 = 0.5%)")
    p.add_argument("--risk-free-rate", type=float, default=0.05)
    p.add_argument("--contracts", type=int, default=1)
    p.add_argument("--multiplier", type=float, default=100.0)
    p.add_argument("--monthly-max-loss-usd", type=float, default=1000.0)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--csv", type=Path, required=True)
    p.add_argument("--code-revision", default="HEAD")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    bars = _load_bars_parquet(args.es)
    cal = pd.read_parquet(args.calendar)
    vix = pd.read_parquet(args.vix)

    daily = extract_daily_data(bars=bars, calendar=cal, vix=vix)
    daily = daily[
        (daily["date"] >= args.window_start) & (daily["date"] <= args.window_end)
    ].copy()
    if daily.empty:
        raise RuntimeError(f"no data in window {args.window_start} → {args.window_end}")

    excluded_dates: set[dt.date] = set()
    event_filter_active = False
    if args.event_calendar is not None:
        events = pd.read_parquet(args.event_calendar)
        excluded_dates = set(events["date"].tolist())
        event_filter_active = True

    params = IronCondorParams(
        short_strike_distance_sigmas=args.sigmas,
        wing_width_pct=args.wing_width_pct,
        risk_free_rate=args.risk_free_rate,
        contracts_per_trade=args.contracts,
        multiplier_per_contract=args.multiplier,
        monthly_max_loss_usd=args.monthly_max_loss_usd,
    )

    ledger, monthly_pnl = run_simulation(
        daily_data=daily,
        params=params,
        excluded_dates=excluded_dates,
    )
    metrics = aggregate_metrics(
        ledger=ledger, monthly_pnl=monthly_pnl, params=params,
    )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(format_report(
        metrics=metrics, params=params,
        window_start=args.window_start, window_end=args.window_end,
        es_path=args.es,
        code_revision=args.code_revision,
        run_timestamp=dt.datetime.now(tz=dt.timezone.utc),
        event_filter_active=event_filter_active,
    ))
    ledger.to_csv(args.csv, index=False)

    print(f"window: {args.window_start} → {args.window_end}")
    print(f"trades: {metrics['n_traded']} (skipped {metrics['n_skipped_event']} event + {metrics['n_skipped_stop']} stop)")
    print(f"total P&L: ${metrics['total_pnl']:.0f}")
    print(f"mean per trade: ${metrics['mean_pnl_per_trade']:.2f}")
    print(f"win rate: {metrics['win_rate']:.1%}")
    print(f"max DD: ${metrics['max_drawdown']:.0f}")
    print(f"max single loss: ${metrics['max_loss']:.0f}")
    print(f"months stopped: {metrics['n_stopped_months']} of {metrics['n_months']}")
    print(f"\nreport -> {args.report}")
    print(f"csv    -> {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
