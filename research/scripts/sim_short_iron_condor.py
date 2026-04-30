"""v7 short SPX 0DTE iron condor simulator (no stop, monthly cap only).

Implements the iron condor as a mirror of the user's bull-call-spread
strike walk:

- **Short call leg**: walk DOWN strikes from highest. For each K,
  ``gap(K) = ask(K) - bid(K_up)`` where K_up is one strike higher.
  Continue while gap < ``strike_width``. Pick the LOWEST K
  satisfying — this is the SHORT call (closest to ATM that meets
  the gap criterion).
- **Long call leg**: walk UP from short_call_K + spacing. For each K':
  per-side max loss = wing_width × 100 - call_credit × 100.
  Continue while per-side max loss ≤ max_loss_usd_per_side AND
  combined POP ≥ pop_threshold. Pick HIGHEST K' satisfying both.
- **Short/Long put legs**: mirror the call walks below ATM.

Synthetic option pricing:

- ``IV(K) = IV_ATM × (1 + skew_strength × log(K/S))``
  (separate skew_strength for calls and puts to capture SPX vol smirk)
- IV_ATM = prior-day VIX / 100
- Bid-ask: 5% half-spread at ATM, scaling to 30% far-OTM, $0.05 floor.

NO STOP LOSS (the lesson from PR #68: stop on a vertical spread
converts ~80% of trades to losses via bid-ask cost on premature
close). The wing widths bound per-trade loss; the monthly cap
bounds cumulative loss.

POP for the iron condor = probability of expiring profitable =
P(short_put_breakeven ≤ S_T ≤ short_call_breakeven). Computed via
N(d2_call) - N(d2_put) at the breakeven strikes.

Usage::

    uv run python -m research.scripts.sim_short_iron_condor \\
        --window-start 2021-05-03 --window-end 2026-04-29 \\
        --max-loss-per-side 500 --pop-threshold 0.55 \\
        --strike-width 5.0 --strike-spacing 5.0 \\
        --skew-strength-calls -0.3 --skew-strength-puts 0.5 \\
        --monthly-max-loss-usd 1000 \\
        --report research/reports/v7-iron-condor-full.md \\
        --csv research/reports/v7-iron-condor-full-ledger.csv
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


@dataclass(frozen=True)
class IronCondorParams:
    entry_time_et: str = "10:30"
    settle_time_et: str = "16:00"

    pop_threshold: float = 0.55
    max_loss_usd_per_side: float = 500.0
    strike_width: float = 5.0  # gap criterion threshold (in $)
    strike_spacing: float = 5.0  # SPX standard

    risk_free_rate: float = 0.05
    skew_strength_calls: float = -0.3
    skew_strength_puts: float = 0.5
    min_iv: float = 0.05

    bid_ask_pct_atm: float = 0.05
    bid_ask_pct_far: float = 0.30
    bid_ask_min: float = 0.05

    contracts_per_trade: int = 1
    multiplier_per_contract: float = 100.0
    monthly_max_loss_usd: float = 1000.0


def _bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0:
        return max(0.0, S - K)
    if sigma <= 0:
        return max(0.0, S - K * math.exp(-r * T))
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + sigma**2 / 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return float(S * stats.norm.cdf(d1) - K * math.exp(-r * T) * stats.norm.cdf(d2))


def _bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0:
        return max(0.0, K - S)
    if sigma <= 0:
        return max(0.0, K * math.exp(-r * T) - S)
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + sigma**2 / 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return float(K * math.exp(-r * T) * stats.norm.cdf(-d2) - S * stats.norm.cdf(-d1))


def _iv_at_strike(K: float, S: float, iv_atm: float, skew: float, min_iv: float) -> float:
    if S <= 0 or K <= 0 or iv_atm <= 0:
        return min_iv
    log_money = math.log(K / S)
    iv = iv_atm * (1.0 + skew * log_money)
    return max(iv, min_iv)


def _half_spread(mid: float, K: float, S: float, params: IronCondorParams) -> float:
    if S <= 0:
        return params.bid_ask_min
    moneyness = abs(math.log(K / S)) if K > 0 else 0.0
    far_blend = min(moneyness / 0.05, 1.0)
    pct = params.bid_ask_pct_atm + (params.bid_ask_pct_far - params.bid_ask_pct_atm) * far_blend
    return max(params.bid_ask_min, mid * pct)


def synthetic_quote(
    *,
    K: float,
    S: float,
    T: float,
    iv_atm: float,
    is_call: bool,
    params: IronCondorParams,
) -> tuple[float, float, float]:
    """Return (bid, mid, ask) for a call or put at strike K."""

    skew = params.skew_strength_calls if is_call else params.skew_strength_puts
    iv = _iv_at_strike(K, S, iv_atm, skew, params.min_iv)
    if is_call:
        mid = _bs_call_price(S, K, T, params.risk_free_rate, iv)
    else:
        mid = _bs_put_price(S, K, T, params.risk_free_rate, iv)
    half = _half_spread(mid, K, S, params)
    bid = max(0.0, mid - half)
    ask = mid + half
    return bid, mid, ask


def _strike_grid(*, S: float, params: IronCondorParams) -> list[float]:
    spacing = params.strike_spacing
    anchor = round(S / spacing) * spacing
    lows = [anchor - i * spacing for i in range(50, 0, -1)]
    highs = [anchor + i * spacing for i in range(0, 100)]
    return lows + highs


def pick_short_call_strike(
    *,
    chain_calls: pd.DataFrame,
    S: float,
    strike_width: float,
) -> float | None:
    """Walk DOWN call strikes. Pick lowest K above S where gap < width.

    Limit walk to K > S (we want OTM short call).
    """

    candidates = chain_calls[chain_calls["strike"] > S].sort_values("strike", ascending=False).reset_index(drop=True)
    last_satisfying: float | None = None
    for i in range(len(candidates) - 1):
        K = float(candidates["strike"].iloc[i + 1])
        K_up = float(candidates["strike"].iloc[i])
        ask_K = float(candidates["ask"].iloc[i + 1])
        bid_K_up = float(candidates["bid"].iloc[i])
        gap = ask_K - bid_K_up
        if gap < strike_width:
            last_satisfying = K
        else:
            if last_satisfying is not None:
                return last_satisfying
    return last_satisfying


def pick_short_put_strike(
    *,
    chain_puts: pd.DataFrame,
    S: float,
    strike_width: float,
) -> float | None:
    """Walk UP put strikes (mirror). Pick highest K below S where gap < width.

    For puts, we walk from the lowest strikes UPWARD. gap(K) = ask(K)
    - bid(K_down) where K_down = K - spacing.
    """

    candidates = chain_puts[chain_puts["strike"] < S].sort_values("strike").reset_index(drop=True)
    last_satisfying: float | None = None
    for i in range(len(candidates) - 1):
        K = float(candidates["strike"].iloc[i + 1])     # the higher one
        K_down = float(candidates["strike"].iloc[i])    # the lower one
        ask_K = float(candidates["ask"].iloc[i + 1])
        bid_K_down = float(candidates["bid"].iloc[i])
        gap = ask_K - bid_K_down
        if gap < strike_width:
            last_satisfying = K
        else:
            if last_satisfying is not None:
                return last_satisfying
    return last_satisfying


def pick_long_call_strike(
    *,
    chain_calls: pd.DataFrame,
    short_call_K: float,
    short_call_bid: float,
    params: IronCondorParams,
) -> tuple[float | None, float]:
    """Walk UP from short_call_K + spacing. Pick highest K' where
    per-side max loss ≤ max_loss_usd_per_side. Returns (K', credit)."""

    candidates = chain_calls[chain_calls["strike"] > short_call_K].sort_values("strike").reset_index(drop=True)
    if len(candidates) == 0:
        return None, 0.0

    last_satisfying: tuple[float, float] | None = None
    for _, row in candidates.iterrows():
        Kp = float(row["strike"])
        ask_Kp = float(row["ask"])
        credit_per_share = short_call_bid - ask_Kp  # we receive this per share
        if credit_per_share < 0:
            continue
        wing_width = Kp - short_call_K
        max_loss_per_side = (wing_width - credit_per_share) * params.multiplier_per_contract
        if max_loss_per_side > params.max_loss_usd_per_side:
            break
        last_satisfying = (Kp, credit_per_share)
    if last_satisfying is None:
        return None, 0.0
    return last_satisfying


def pick_long_put_strike(
    *,
    chain_puts: pd.DataFrame,
    short_put_K: float,
    short_put_bid: float,
    params: IronCondorParams,
) -> tuple[float | None, float]:
    """Walk DOWN from short_put_K - spacing. Mirror of long call walk."""

    candidates = chain_puts[chain_puts["strike"] < short_put_K].sort_values("strike", ascending=False).reset_index(drop=True)
    if len(candidates) == 0:
        return None, 0.0

    last_satisfying: tuple[float, float] | None = None
    for _, row in candidates.iterrows():
        Kp = float(row["strike"])
        ask_Kp = float(row["ask"])
        credit_per_share = short_put_bid - ask_Kp
        if credit_per_share < 0:
            continue
        wing_width = short_put_K - Kp
        max_loss_per_side = (wing_width - credit_per_share) * params.multiplier_per_contract
        if max_loss_per_side > params.max_loss_usd_per_side:
            break
        last_satisfying = (Kp, credit_per_share)
    if last_satisfying is None:
        return None, 0.0
    return last_satisfying


def compute_pop(
    *,
    short_call_K: float,
    short_put_K: float,
    net_credit: float,
    S: float,
    T: float,
    iv_atm: float,
    params: IronCondorParams,
) -> float:
    """POP for an iron condor = P(short_put_breakeven ≤ S_T ≤ short_call_breakeven).

    Computed as N(d2_call_be) - N(d2_put_be) using BS with VIX-as-IV.
    """

    sc_be = short_call_K + net_credit
    sp_be = short_put_K - net_credit

    if T <= 0 or iv_atm <= 0:
        return 0.5

    sqrt_T = math.sqrt(T)
    sigma_call = max(_iv_at_strike(sc_be, S, iv_atm, params.skew_strength_calls, params.min_iv), params.min_iv)
    sigma_put = max(_iv_at_strike(sp_be, S, iv_atm, params.skew_strength_puts, params.min_iv), params.min_iv)

    # d2 evaluated at K → N(d2) = risk-neutral P(S_T > K). We want
    # P(sp_be < S_T < sc_be) = N(d2 at sp_be) - N(d2 at sc_be)
    # (probability of being above the lower breakeven minus probability of
    # being above the upper breakeven).
    d2_at_sc = (math.log(S / sc_be) + (params.risk_free_rate - sigma_call**2 / 2) * T) / (sigma_call * sqrt_T)
    d2_at_sp = (math.log(S / sp_be) + (params.risk_free_rate - sigma_put**2 / 2) * T) / (sigma_put * sqrt_T)
    p_above_sc = float(stats.norm.cdf(d2_at_sc))
    p_above_sp = float(stats.norm.cdf(d2_at_sp))

    return max(0.0, p_above_sp - p_above_sc)


def simulate_one_day(
    *,
    S_open: float,
    S_settle: float,
    iv_atm: float,
    params: IronCondorParams,
) -> dict[str, Any]:
    T = 6.5 / 24 / 365

    strikes = _strike_grid(S=S_open, params=params)
    rows = []
    for K in strikes:
        c_bid, c_mid, c_ask = synthetic_quote(K=K, S=S_open, T=T, iv_atm=iv_atm, is_call=True, params=params)
        p_bid, p_mid, p_ask = synthetic_quote(K=K, S=S_open, T=T, iv_atm=iv_atm, is_call=False, params=params)
        rows.append({"strike": K, "c_bid": c_bid, "c_mid": c_mid, "c_ask": c_ask,
                     "p_bid": p_bid, "p_mid": p_mid, "p_ask": p_ask})
    chain = pd.DataFrame(rows)
    chain_calls = chain[["strike", "c_bid", "c_mid", "c_ask"]].rename(columns={"c_bid": "bid", "c_mid": "mid", "c_ask": "ask"})
    chain_puts = chain[["strike", "p_bid", "p_mid", "p_ask"]].rename(columns={"p_bid": "bid", "p_mid": "mid", "p_ask": "ask"})

    short_call_K = pick_short_call_strike(chain_calls=chain_calls, S=S_open, strike_width=params.strike_width)
    short_put_K = pick_short_put_strike(chain_puts=chain_puts, S=S_open, strike_width=params.strike_width)
    if short_call_K is None or short_put_K is None:
        return {"skipped": True, "skip_reason": "no_viable_short_strike", "pnl": 0.0}

    short_call_bid = float(chain_calls.loc[chain_calls["strike"] == short_call_K, "bid"].iloc[0])
    short_put_bid = float(chain_puts.loc[chain_puts["strike"] == short_put_K, "bid"].iloc[0])

    long_call_K, call_credit = pick_long_call_strike(
        chain_calls=chain_calls, short_call_K=short_call_K,
        short_call_bid=short_call_bid, params=params,
    )
    long_put_K, put_credit = pick_long_put_strike(
        chain_puts=chain_puts, short_put_K=short_put_K,
        short_put_bid=short_put_bid, params=params,
    )
    if long_call_K is None or long_put_K is None:
        return {"skipped": True, "skip_reason": "no_viable_long_strike", "pnl": 0.0}

    net_credit = call_credit + put_credit
    pop = compute_pop(
        short_call_K=short_call_K, short_put_K=short_put_K,
        net_credit=net_credit, S=S_open, T=T,
        iv_atm=iv_atm, params=params,
    )
    if pop < params.pop_threshold:
        return {"skipped": True, "skip_reason": "pop_below_threshold", "pnl": 0.0,
                "pop_computed": float(pop)}

    # P&L at PM settle
    # Short call: -max(0, S - short_call_K)
    # Long call: +max(0, S - long_call_K)
    # Short put: -max(0, short_put_K - S)
    # Long put: +max(0, long_put_K - S)
    call_payoff = max(0.0, S_settle - long_call_K) - max(0.0, S_settle - short_call_K)
    put_payoff = max(0.0, long_put_K - S_settle) - max(0.0, short_put_K - S_settle)
    pnl_per_share = net_credit + call_payoff + put_payoff
    pnl = pnl_per_share * params.multiplier_per_contract * params.contracts_per_trade

    return {
        "skipped": False, "skip_reason": "",
        "S_open": float(S_open), "S_settle": float(S_settle), "iv_atm": float(iv_atm),
        "short_call_K": float(short_call_K), "long_call_K": float(long_call_K),
        "short_put_K": float(short_put_K), "long_put_K": float(long_put_K),
        "call_credit": float(call_credit), "put_credit": float(put_credit),
        "net_credit": float(net_credit), "pop": float(pop),
        "wing_width_call": float(long_call_K - short_call_K),
        "wing_width_put": float(short_put_K - long_put_K),
        "pnl_per_share": float(pnl_per_share),
        "pnl": float(pnl), "winner": pnl > 0,
    }


def extract_per_day_data(
    *,
    bars: pd.DataFrame,
    calendar: pd.DataFrame,
    et_tz: str = "America/New_York",
    entry_time_et: str = "10:30",
    settle_time_et: str = "16:00",
) -> dict[dt.date, dict[str, Any]]:
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

    by_date: dict[dt.date, dict[str, Any]] = {}
    entry_t = f"{entry_time_et}:00"
    settle_t = f"{settle_time_et}:00"

    for date_et, group in bars.groupby("date_et"):
        em = group[group["time_et"] == entry_t]
        sm = group[group["time_et"] == settle_t]
        if len(em) == 0 or len(sm) == 0:
            continue
        by_date[date_et] = {
            "S_open": float(em["open"].iloc[0]),
            "S_settle": float(sm["open"].iloc[0]),
        }
    return by_date


def run_simulation(
    *,
    by_date: dict[dt.date, dict[str, Any]],
    vix: pd.DataFrame,
    params: IronCondorParams,
    excluded_dates: set[dt.date] | None = None,
) -> tuple[pd.DataFrame, dict[tuple[int, int], float]]:
    vix_sorted = vix.sort_values("date").reset_index(drop=True)
    prior_vix_map: dict[dt.date, float] = {
        vix_sorted["date"].iloc[i]: float(vix_sorted["close"].iloc[i - 1])
        for i in range(1, len(vix_sorted))
    }

    excluded = excluded_dates or set()
    monthly_pnl: dict[tuple[int, int], float] = {}
    monthly_stopped: set[tuple[int, int]] = set()
    results: list[dict[str, Any]] = []

    for date in sorted(by_date.keys()):
        ym = (date.year, date.month)
        if date in excluded:
            results.append({"date": date, "skipped": True,
                            "skip_reason": "event_day", "pnl": 0.0})
            continue
        if ym in monthly_stopped:
            results.append({"date": date, "skipped": True,
                            "skip_reason": "monthly_stop", "pnl": 0.0})
            continue
        prior_vix = prior_vix_map.get(date)
        if prior_vix is None:
            results.append({"date": date, "skipped": True,
                            "skip_reason": "no_prior_vix", "pnl": 0.0})
            continue
        d = by_date[date]
        trade = simulate_one_day(
            S_open=d["S_open"], S_settle=d["S_settle"],
            iv_atm=prior_vix / 100, params=params,
        )
        trade["date"] = date
        results.append(trade)
        if not trade.get("skipped"):
            new_monthly = monthly_pnl.get(ym, 0.0) + trade["pnl"]
            monthly_pnl[ym] = new_monthly
            if new_monthly <= -params.monthly_max_loss_usd:
                monthly_stopped.add(ym)

    df = pd.DataFrame(results)
    return df, monthly_pnl


def aggregate_metrics(*, ledger: pd.DataFrame, monthly_pnl: dict[tuple[int, int], float]) -> dict[str, Any]:
    traded = ledger[~ledger["skipped"]]
    skip_counts = ledger["skip_reason"].value_counts().to_dict()
    if len(traded) == 0:
        return {"n_total": len(ledger), "n_traded": 0, "skip_counts": skip_counts,
                "total_pnl": 0.0, "mean_pnl": float("nan")}

    pnls = traded["pnl"].astype(float)
    total = float(pnls.sum())
    mean = float(pnls.mean())
    median = float(pnls.median())
    std = float(pnls.std(ddof=1)) if len(pnls) > 1 else float("nan")
    win_rate = float((pnls > 0).mean())

    cumulative = pnls.cumsum()
    peak = cumulative.cummax()
    dd = (cumulative - peak)
    max_dd = float(dd.min())

    monthly_stopped_count = len({
        (d.year, d.month) for d, r in zip(ledger["date"], ledger["skip_reason"])
        if r == "monthly_stop"
    })
    return {
        "n_total": len(ledger), "n_traded": len(traded), "skip_counts": skip_counts,
        "total_pnl": total, "mean_pnl": mean, "median_pnl": median, "std_pnl": std,
        "win_rate": win_rate, "max_dd": max_dd,
        "max_win": float(pnls.max()), "max_loss": float(pnls.min()),
        "n_winning_months": sum(1 for x in monthly_pnl.values() if x > 0),
        "n_total_months": len(monthly_pnl),
        "monthly_stopped_count": monthly_stopped_count,
    }


def format_report(
    *, metrics: dict[str, Any], params: IronCondorParams,
    window_start: dt.date, window_end: dt.date,
    code_revision: str, run_timestamp: dt.datetime,
) -> str:
    sk = metrics.get("skip_counts", {})
    lines = [
        "# v7 Short SPX 0DTE Iron Condor (no stop, monthly cap only)",
        "",
        f"**Window**: {window_start.isoformat()} → {window_end.isoformat()}",
        "",
        "## Strategy parameters",
        "",
        f"- entry_time_et: {params.entry_time_et}",
        f"- pop_threshold: {params.pop_threshold}",
        f"- max_loss_usd_per_side: ${params.max_loss_usd_per_side:.0f}",
        f"- strike_width (gap criterion, $): {params.strike_width}",
        f"- strike_spacing: {params.strike_spacing}",
        f"- skew_strength_calls / puts: {params.skew_strength_calls} / {params.skew_strength_puts}",
        f"- bid_ask_pct atm/far: {params.bid_ask_pct_atm:.0%} / {params.bid_ask_pct_far:.0%}",
        f"- monthly_max_loss_usd: ${params.monthly_max_loss_usd:.0f}",
        f"- stop_loss: DISABLED (per v6 finding that breakeven stop destroys edge)",
        "",
        "## Provenance",
        "",
        f"- code_revision: `{code_revision}`",
        f"- run_timestamp_utc: {run_timestamp.isoformat()}",
        "",
        "## Results",
        "",
        f"| Metric | Value |",
        f"|---|---|",
        f"| total days in window | {metrics['n_total']} |",
        f"| trades executed | {metrics['n_traded']} |",
    ]
    for reason, count in sk.items():
        if reason and reason != "":
            lines.append(f"| skipped ({reason}) | {count} |")

    if metrics["n_traded"] > 0:
        lines.extend([
            f"| **total P&L** | **${metrics['total_pnl']:.2f}** |",
            f"| mean P&L per trade | ${metrics['mean_pnl']:.2f} |",
            f"| median P&L per trade | ${metrics['median_pnl']:.2f} |",
            f"| std P&L per trade | ${metrics['std_pnl']:.2f} |",
            f"| win rate | {metrics['win_rate']:.1%} |",
            f"| max single-trade win | ${metrics['max_win']:.2f} |",
            f"| max single-trade loss | ${metrics['max_loss']:.2f} |",
            f"| max drawdown | ${metrics['max_dd']:.2f} |",
            f"| months in window | {metrics['n_total_months']} |",
            f"| winning months | {metrics['n_winning_months']} |",
            f"| months that hit cap | {metrics['monthly_stopped_count']} |",
        ])
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="sim_short_iron_condor")
    p.add_argument("--es", type=Path, default=Path("research/data/dataset-v1/es_intraday.parquet"))
    p.add_argument("--vix", type=Path, default=Path("research/data/dataset-v1/vix_daily.parquet"))
    p.add_argument("--calendar", type=Path, default=Path("research/data/dataset-v1/trading_calendar.parquet"))
    p.add_argument("--event-calendar", type=Path, default=None)
    p.add_argument("--window-start", type=dt.date.fromisoformat, required=True)
    p.add_argument("--window-end", type=dt.date.fromisoformat, required=True)
    p.add_argument("--entry-time", default="10:30")
    p.add_argument("--max-loss-per-side", type=float, default=500.0)
    p.add_argument("--pop-threshold", type=float, default=0.55)
    p.add_argument("--strike-width", type=float, default=5.0)
    p.add_argument("--strike-spacing", type=float, default=5.0)
    p.add_argument("--skew-strength-calls", type=float, default=-0.3)
    p.add_argument("--skew-strength-puts", type=float, default=0.5)
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

    by_date = extract_per_day_data(bars=bars, calendar=cal, entry_time_et=args.entry_time)
    by_date = {d: v for d, v in by_date.items() if args.window_start <= d <= args.window_end}
    if not by_date:
        raise RuntimeError(f"no data in {args.window_start} → {args.window_end}")

    excluded = set()
    if args.event_calendar is not None:
        events = pd.read_parquet(args.event_calendar)
        excluded = set(events["date"].tolist())

    params = IronCondorParams(
        entry_time_et=args.entry_time, pop_threshold=args.pop_threshold,
        max_loss_usd_per_side=args.max_loss_per_side,
        strike_width=args.strike_width, strike_spacing=args.strike_spacing,
        skew_strength_calls=args.skew_strength_calls,
        skew_strength_puts=args.skew_strength_puts,
        monthly_max_loss_usd=args.monthly_max_loss_usd,
    )

    ledger, monthly_pnl = run_simulation(
        by_date=by_date, vix=vix, params=params, excluded_dates=excluded,
    )
    metrics = aggregate_metrics(ledger=ledger, monthly_pnl=monthly_pnl)

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
    print(f"days: {metrics['n_total']}, trades: {metrics['n_traded']}")
    if metrics["n_traded"] > 0:
        print(f"total P&L: ${metrics['total_pnl']:.2f}")
        print(f"mean per trade: ${metrics['mean_pnl']:.2f}")
        print(f"win rate: {metrics['win_rate']:.1%}")
        print(f"max DD: ${metrics['max_dd']:.2f}")
        print(f"months hit cap: {metrics['monthly_stopped_count']} of {metrics['n_total_months']}")
    print(f"report -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
