"""Strategy-faithful simulator for the user's actual bull call spread design.

Implements the original strategy as specified in the project's
operational rules:

1. **Daily entry** at ENTRY_TIME_ET (default 10:30 ET — every trading
   day; no morning-move signal gate).
2. **Long leg — descending walk**: starting from the highest strikes
   above current spot, walk DOWN. For each strike K, compute
   ``gap(K) = ask(K) - bid(K_up)`` where K_up is one strike above.
   Continue while ``gap(K) < strike_width``. Pick the LOWEST K
   satisfying this.
3. **Short leg — ascending walk** from ``long_strike + 1 strike``:
   For each candidate K':
   - ``net_debit = ask(long_K) - bid(K')``
   - ``breakeven = long_K + net_debit``
   - ``POP = N(d2)`` evaluated at strike=breakeven
   Continue while ``net_debit × 100 ≤ max_loss_usd`` AND
   ``POP ≥ pop_threshold``. Pick the HIGHEST K' satisfying both.
4. **Stop loss**: spot crosses below ``breakeven`` after being at-or-
   above ``breakeven`` post-entry. Suppressed in the last
   STOP_LATEST_SEC seconds before close.
5. **Hold to PM cash settle** at 16:00 ET for spreads that didn't stop.

Synthetic option pricing (since we don't have real chain data):

- ``IV(K) = IV_ATM × (1 + skew_strength × log(K/S))``
  where IV_ATM = VIX/100 and skew_strength is configurable
  (default -0.5 for SPX, slight downward IV slope on call side).
- BS prices for the call leg of each strike.
- Bid-ask: ``half_spread = max(MIN_HALF_SPREAD, mid × half_spread_pct)``
  where half_spread_pct widens for far-OTM strikes.

Stop loss simulation uses ES 1-minute bars as the spot proxy. The
user's bot subscribes to live spot ticks; we use intraday bars.

Monthly capital control: stop trading for the rest of the month if
cumulative monthly P&L hits ``-monthly_max_loss_usd``.

Usage::

    uv run python -m research.scripts.sim_bull_call_strategy \\
        --window-start 2021-05-03 --window-end 2026-04-29 \\
        --entry-time 10:30 \\
        --max-loss-usd 200 --pop-threshold 0.55 \\
        --strike-width 1.0 --strike-spacing 5 \\
        --skew-strength -0.5 \\
        --monthly-max-loss-usd 1000 \\
        --report research/reports/v6-bull-call-strategy-sim.md \\
        --csv research/reports/v6-bull-call-strategy-sim-ledger.csv
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


@dataclass(frozen=True)
class StrategyParams:
    entry_time_et: str = "10:30"
    settle_time_et: str = "16:00"
    eow_time_et_for_stop_supp: str = "15:55"  # stop is suppressed in last 5 min

    pop_threshold: float = 0.55
    max_loss_usd: float = 200.0
    strike_width: float = 1.0  # gap threshold for long-walk (in dollars)
    strike_spacing: float = 5.0  # SPX strikes are 5-point spaced

    risk_free_rate: float = 0.05
    skew_strength: float = -0.5  # IV(K) = IV_ATM × (1 + skew × log(K/S))
    min_iv: float = 0.05  # floor on IV to avoid weird BS

    bid_ask_pct_atm: float = 0.05  # 5% half-spread at ATM
    bid_ask_pct_far: float = 0.30  # 30% half-spread far OTM
    bid_ask_min: float = 0.05  # $0.05 minimum half-spread

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


def _iv_at_strike(K: float, S: float, iv_atm: float, skew_strength: float, min_iv: float) -> float:
    """Linear log-moneyness skew model. SPX has slight downward call skew
    and stronger upward put skew; this model is monotone."""

    if S <= 0 or K <= 0 or iv_atm <= 0:
        return min_iv
    log_money = math.log(K / S)
    iv = iv_atm * (1.0 + skew_strength * log_money)
    return max(iv, min_iv)


def _half_spread(mid: float, K: float, S: float, params: StrategyParams) -> float:
    """Bid-ask half-spread as a fraction of mid, larger for far-OTM."""

    if S <= 0:
        return params.bid_ask_min
    moneyness = abs(math.log(K / S)) if K > 0 else 0.0
    # Scale: 0% moneyness → atm pct; 5% moneyness → far pct
    far_blend = min(moneyness / 0.05, 1.0)
    pct = params.bid_ask_pct_atm + (params.bid_ask_pct_far - params.bid_ask_pct_atm) * far_blend
    return max(params.bid_ask_min, mid * pct)


def synthetic_call_quote(
    *,
    K: float,
    S: float,
    T: float,
    iv_atm: float,
    params: StrategyParams,
) -> tuple[float, float, float, float]:
    """Return (bid, mid, ask, iv) for a call at strike K."""

    iv = _iv_at_strike(K, S, iv_atm, params.skew_strength, params.min_iv)
    mid = _bs_call_price(S, K, T, params.risk_free_rate, iv)
    half = _half_spread(mid, K, S, params)
    bid = max(0.0, mid - half)
    ask = mid + half
    return bid, mid, ask, iv


def _strike_grid(*, S: float, params: StrategyParams) -> list[float]:
    """Generate a list of strikes around current S spaced by
    ``strike_spacing`` (default 5 points for SPX)."""

    spacing = params.strike_spacing
    # Anchor at the nearest multiple of spacing
    anchor = round(S / spacing) * spacing
    # Build a window: -50 strikes below to +100 strikes above (asymmetric for bull spread)
    lows = [anchor - i * spacing for i in range(50, 0, -1)]
    highs = [anchor + i * spacing for i in range(0, 100)]
    return lows + highs


def pick_long_strike(
    *,
    chain_calls: pd.DataFrame,  # DataFrame[strike, bid, mid, ask]
    strike_width: float,
) -> float | None:
    """Walk DOWN strikes. For each K, find K_up = next-strike-up.
    gap(K) = ask(K) - bid(K_up). Continue while gap(K) < strike_width.
    Pick the LOWEST K satisfying the criterion.

    Note: 'walk down' means we iterate strikes from highest to lowest,
    and we keep walking as long as gap stays below threshold. The
    'lowest' satisfying K is the last one we accept before gap
    transitions ≥ threshold (or before we run out of strikes).
    """

    sorted_chain = chain_calls.sort_values("strike", ascending=False).reset_index(drop=True)
    last_satisfying: float | None = None
    for i in range(len(sorted_chain) - 1):
        K = float(sorted_chain["strike"].iloc[i + 1])  # the lower one of the pair
        K_up = float(sorted_chain["strike"].iloc[i])    # the upper one
        ask_K = float(sorted_chain["ask"].iloc[i + 1])
        bid_K_up = float(sorted_chain["bid"].iloc[i])
        gap = ask_K - bid_K_up
        if gap < strike_width:
            last_satisfying = K
        else:
            # Once gap exceeds threshold (typically as we approach ATM/ITM),
            # stop walking down — the long strike is fixed.
            if last_satisfying is not None:
                return last_satisfying
    return last_satisfying


def pick_short_strike(
    *,
    chain_calls: pd.DataFrame,
    long_K: float,
    long_ask: float,
    S: float,
    T: float,
    iv_atm: float,
    params: StrategyParams,
) -> tuple[float | None, float, float]:
    """Walk UP from long_K + 1 strike. For each candidate K':
    - net_debit = long_ask - bid(K')
    - breakeven = long_K + net_debit
    - POP = N(d2) at strike=breakeven, sigma=iv_atm, T
    Continue while net_debit × 100 ≤ max_loss_usd AND POP ≥ pop_threshold.
    Pick HIGHEST K' satisfying both.

    Returns (short_K, debit, pop) or (None, 0, 0) if no viable K'.
    """

    sorted_chain = chain_calls.sort_values("strike").reset_index(drop=True)
    # Strikes strictly above long_K
    candidates = sorted_chain[sorted_chain["strike"] > long_K]
    if len(candidates) == 0:
        return None, 0.0, 0.0

    last_satisfying: tuple[float, float, float] | None = None
    for _, row in candidates.iterrows():
        Kp = float(row["strike"])
        bid_Kp = float(row["bid"])
        debit = long_ask - bid_Kp
        if debit < 0:
            # Net credit — not a valid bull call spread (impossible in practice)
            continue
        if debit * params.multiplier_per_contract > params.max_loss_usd:
            break  # walk-up debit only grows, so further K' make this worse
        breakeven = long_K + debit
        # POP = probability S_T > breakeven
        if breakeven <= 0:
            continue
        sqrt_T = math.sqrt(T) if T > 0 else 0.0001
        sigma_be = max(_iv_at_strike(breakeven, S, iv_atm, params.skew_strength, params.min_iv), params.min_iv)
        if sigma_be <= 0 or sqrt_T <= 0:
            pop = 0.5
        else:
            d2 = (math.log(S / breakeven) + (params.risk_free_rate - sigma_be**2 / 2) * T) / (sigma_be * sqrt_T)
            pop = float(stats.norm.cdf(d2))
        if pop < params.pop_threshold:
            break  # POP only decreases as K' increases (breakeven grows)
        last_satisfying = (Kp, debit, pop)
    if last_satisfying is None:
        return None, 0.0, 0.0
    return last_satisfying


def simulate_one_day(
    *,
    intraday_bars: pd.DataFrame,
    S_open: float,
    S_settle: float,
    iv_atm: float,
    params: StrategyParams,
) -> dict[str, Any]:
    """Simulate one trading day's bull-call-spread trade.

    intraday_bars: ES 1m bars from entry_time to settle_time. Should
    have columns date_et (date), time_et (HH:MM:SS), open (price).
    """

    # Build synthetic chain at entry time
    T = 6.5 / 24 / 365  # entry-to-settle in years (approx; ignoring fractions)
    strikes = _strike_grid(S=S_open, params=params)
    chain_rows = []
    for K in strikes:
        bid, mid, ask, iv = synthetic_call_quote(
            K=K, S=S_open, T=T, iv_atm=iv_atm, params=params,
        )
        chain_rows.append({"strike": K, "bid": bid, "mid": mid, "ask": ask, "iv": iv})
    chain = pd.DataFrame(chain_rows)

    long_K = pick_long_strike(chain_calls=chain, strike_width=params.strike_width)
    if long_K is None:
        return {"skipped": True, "skip_reason": "no_viable_long",
                "pnl": 0.0}

    long_ask = float(chain.loc[chain["strike"] == long_K, "ask"].iloc[0])

    short_K, debit, pop = pick_short_strike(
        chain_calls=chain, long_K=long_K, long_ask=long_ask,
        S=S_open, T=T, iv_atm=iv_atm, params=params,
    )
    if short_K is None:
        return {"skipped": True, "skip_reason": "no_viable_short",
                "pnl": 0.0}

    breakeven = long_K + debit
    debit_usd = debit * params.multiplier_per_contract * params.contracts_per_trade

    # Stop loss simulation — track spot from entry to (settle - 5min)
    armed = (S_open >= breakeven)
    stop_fired_at: dt.time | None = None
    stop_spot: float | None = None

    bars_sorted = intraday_bars.sort_values("time_et").reset_index(drop=True)
    for _, row in bars_sorted.iterrows():
        time_et_str = str(row["time_et"])
        if time_et_str >= "15:55:00":  # suppress in last 5 minutes
            break
        spot = float(row["open"])
        if not armed and spot >= breakeven:
            armed = True
        if armed and spot < breakeven:
            stop_fired_at = time_et_str
            stop_spot = spot
            break

    if stop_fired_at is not None:
        # Close at MKT — we sell long at bid, buy short at ask, with current spot
        # Compute synthetic prices at the stop time
        # Time-to-expiry shrinks; T_remaining ≈ (16:00 - stop_time) / (24*365)
        # For simplicity, use a rough approximation
        h_remaining = max(0.1, 6.5 - 6.5 * 0.5)  # approximate as half-day remaining at typical stop
        T_remaining = h_remaining / 24 / 365
        bid_long_at_stop, _, _, _ = synthetic_call_quote(
            K=long_K, S=stop_spot, T=T_remaining, iv_atm=iv_atm, params=params,
        )
        _, _, ask_short_at_stop, _ = synthetic_call_quote(
            K=short_K, S=stop_spot, T=T_remaining, iv_atm=iv_atm, params=params,
        )
        # Closing the spread: sell long (receive bid), buy back short (pay ask)
        close_credit = bid_long_at_stop - ask_short_at_stop
        spread_pnl_per_share = close_credit - debit
        pnl = spread_pnl_per_share * params.multiplier_per_contract * params.contracts_per_trade
    else:
        # Hold to settle
        long_payoff = max(0.0, S_settle - long_K)
        short_payoff = max(0.0, S_settle - short_K)
        spread_payoff = long_payoff - short_payoff  # we're long the long, short the short
        pnl_per_share = spread_payoff - debit
        pnl = pnl_per_share * params.multiplier_per_contract * params.contracts_per_trade

    return {
        "skipped": False,
        "skip_reason": "",
        "S_open": float(S_open),
        "S_settle": float(S_settle),
        "iv_atm": float(iv_atm),
        "long_K": float(long_K),
        "short_K": float(short_K),
        "debit": float(debit),
        "debit_usd": float(debit_usd),
        "pop": float(pop),
        "breakeven": float(breakeven),
        "spread_width": float(short_K - long_K),
        "armed": armed,
        "stopped": stop_fired_at is not None,
        "stop_time": stop_fired_at or "",
        "stop_spot": float(stop_spot) if stop_spot is not None else None,
        "pnl": float(pnl),
        "winner": pnl > 0,
    }


def extract_per_day_data(
    *,
    bars: pd.DataFrame,
    calendar: pd.DataFrame,
    et_tz: str = "America/New_York",
    entry_time_et: str = "10:30",
    settle_time_et: str = "16:00",
) -> dict[dt.date, dict[str, Any]]:
    """Build a dict mapping each trading date to (S_open, S_settle,
    intraday_bars from entry to settle)."""

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

    # Per-day extraction: get entry_time_et open price, settle_time_et open
    # price (= price at 16:00), and bars in between
    by_date: dict[dt.date, dict[str, Any]] = {}
    entry_t = f"{entry_time_et}:00"
    settle_t = f"{settle_time_et}:00"

    for date_et, group in bars.groupby("date_et"):
        entry_match = group[group["time_et"] == entry_t]
        settle_match = group[group["time_et"] == settle_t]
        if len(entry_match) == 0 or len(settle_match) == 0:
            continue
        S_open = float(entry_match["open"].iloc[0])
        S_settle = float(settle_match["open"].iloc[0])
        intraday = group[
            (group["time_et"] >= entry_t) & (group["time_et"] <= settle_t)
        ][["date_et", "time_et", "open"]]
        by_date[date_et] = {
            "S_open": S_open,
            "S_settle": S_settle,
            "intraday_bars": intraday,
        }
    return by_date


def run_simulation(
    *,
    by_date: dict[dt.date, dict[str, Any]],
    vix: pd.DataFrame,
    params: StrategyParams,
    excluded_dates: set[dt.date] | None = None,
) -> tuple[pd.DataFrame, dict[tuple[int, int], float]]:
    """Run the strategy across all trading days."""

    # Prior-day VIX map for IV input
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

        iv_atm = prior_vix / 100
        d = by_date[date]
        trade = simulate_one_day(
            intraday_bars=d["intraday_bars"],
            S_open=d["S_open"], S_settle=d["S_settle"],
            iv_atm=iv_atm, params=params,
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


def aggregate_metrics(
    *,
    ledger: pd.DataFrame,
    monthly_pnl: dict[tuple[int, int], float],
) -> dict[str, Any]:
    traded = ledger[~ledger["skipped"]]
    n_total = len(ledger)
    n_traded = len(traded)
    skip_counts = ledger["skip_reason"].value_counts().to_dict()

    if n_traded == 0:
        return {
            "n_total": n_total, "n_traded": 0,
            "skip_counts": skip_counts,
            "total_pnl": 0.0,
            "mean_pnl": float("nan"),
            "median_pnl": float("nan"),
            "win_rate": float("nan"),
        }

    pnls = traded["pnl"].astype(float)
    total = float(pnls.sum())
    mean = float(pnls.mean())
    median = float(pnls.median())
    std = float(pnls.std(ddof=1)) if len(pnls) > 1 else float("nan")
    win_rate = float((pnls > 0).mean())

    # Drawdown
    cumulative = pnls.cumsum()
    peak = cumulative.cummax()
    dd = (cumulative - peak)
    max_dd = float(dd.min())

    n_stopped = int(traded["stopped"].sum()) if "stopped" in traded.columns else 0
    n_armed = int(traded["armed"].sum()) if "armed" in traded.columns else 0

    monthly_stopped_count = len({
        (d.year, d.month) for d, r in zip(ledger["date"], ledger["skip_reason"])
        if r == "monthly_stop"
    })

    return {
        "n_total": n_total,
        "n_traded": n_traded,
        "skip_counts": skip_counts,
        "n_stopped": n_stopped,
        "n_armed": n_armed,
        "monthly_stopped_count": monthly_stopped_count,
        "total_pnl": total,
        "mean_pnl": mean,
        "median_pnl": median,
        "std_pnl": std,
        "win_rate": win_rate,
        "max_dd": max_dd,
        "max_win": float(pnls.max()),
        "max_loss": float(pnls.min()),
        "n_winning_months": sum(1 for x in monthly_pnl.values() if x > 0),
        "n_total_months": len(monthly_pnl),
    }


def format_report(
    *,
    metrics: dict[str, Any],
    params: StrategyParams,
    window_start: dt.date,
    window_end: dt.date,
    code_revision: str,
    run_timestamp: dt.datetime,
) -> str:
    sk = metrics.get("skip_counts", {})
    lines = [
        "# v6 Bull-Call-Spread Strategy-Faithful Simulator",
        "",
        f"**Window**: {window_start.isoformat()} → {window_end.isoformat()}",
        "",
        "## Strategy parameters",
        "",
        f"- entry_time_et: {params.entry_time_et}",
        f"- settle_time_et: {params.settle_time_et}",
        f"- pop_threshold: {params.pop_threshold}",
        f"- max_loss_usd: ${params.max_loss_usd:.0f}",
        f"- strike_width (long-walk gap criterion, $): {params.strike_width}",
        f"- strike_spacing: {params.strike_spacing}",
        f"- skew_strength: {params.skew_strength}",
        f"- bid_ask_pct (atm/far): {params.bid_ask_pct_atm:.0%} / {params.bid_ask_pct_far:.0%}",
        f"- bid_ask_min: ${params.bid_ask_min:.2f}",
        f"- monthly_max_loss_usd: ${params.monthly_max_loss_usd:.0f}",
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
            f"| max drawdown (cumulative) | ${metrics['max_dd']:.2f} |",
            f"| trades that armed (spot ≥ breakeven post-entry) | {metrics['n_armed']} |",
            f"| trades that stopped (spot < breakeven after armed) | {metrics['n_stopped']} |",
            f"| months in window | {metrics['n_total_months']} |",
            f"| winning months | {metrics['n_winning_months']} |",
            f"| months that hit cap | {metrics['monthly_stopped_count']} |",
        ])

    lines.extend([
        "",
        "## Honest caveats (still apply)",
        "",
        "- **Synthetic option pricing**. Uses BS with a simple log-moneyness",
        "  skew model; not real chain bid/ask. Actual SPX 0DTE has more",
        "  complex skew (smile + smirk) that varies with vol regime.",
        "- **Bid-ask approximation**. Uses linear-in-moneyness model; real",
        "  far-OTM bid-ask is more variable and often wider on quiet days.",
        "- **VIX-as-IV-ATM**. Uses prior-day VIX as ATM IV input; real 0DTE",
        "  ATM IV is often different from 30-day VIX, especially around",
        "  events.",
        "- **Stop-loss timing**. Uses 1-min ES bars to detect breakeven",
        "  crosses; real bot uses spot ticks (faster). May miss intra-",
        "  minute crosses or fire late.",
        "- **No commissions**. Real round-trip on 2-leg spread is ~\\$1-3",
        "  per contract on IBKR retail. Subtract that from per-trade P&L",
        "  for a friction-included estimate.",
        "",
        "Despite caveats, this simulator implements the user's actual",
        "strike walk + POP + stop loss + monthly cap logic — much closer",
        "to the strategy than v1-v5 tested.",
    ])
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="sim_bull_call_strategy")
    p.add_argument("--es", type=Path,
                   default=Path("research/data/dataset-v1/es_intraday.parquet"))
    p.add_argument("--vix", type=Path,
                   default=Path("research/data/dataset-v1/vix_daily.parquet"))
    p.add_argument("--calendar", type=Path,
                   default=Path("research/data/dataset-v1/trading_calendar.parquet"))
    p.add_argument("--event-calendar", type=Path, default=None)
    p.add_argument("--window-start", type=dt.date.fromisoformat, required=True)
    p.add_argument("--window-end", type=dt.date.fromisoformat, required=True)
    p.add_argument("--entry-time", default="10:30")
    p.add_argument("--max-loss-usd", type=float, default=200.0)
    p.add_argument("--pop-threshold", type=float, default=0.55)
    p.add_argument("--strike-width", type=float, default=1.0,
                   help="Long-walk gap threshold in dollars")
    p.add_argument("--strike-spacing", type=float, default=5.0)
    p.add_argument("--skew-strength", type=float, default=-0.5)
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

    by_date = extract_per_day_data(
        bars=bars, calendar=cal,
        entry_time_et=args.entry_time,
    )
    by_date = {
        d: v for d, v in by_date.items()
        if args.window_start <= d <= args.window_end
    }
    if not by_date:
        raise RuntimeError(f"no data in {args.window_start} → {args.window_end}")

    excluded = set()
    if args.event_calendar is not None:
        events = pd.read_parquet(args.event_calendar)
        excluded = set(events["date"].tolist())

    params = StrategyParams(
        entry_time_et=args.entry_time,
        pop_threshold=args.pop_threshold,
        max_loss_usd=args.max_loss_usd,
        strike_width=args.strike_width,
        strike_spacing=args.strike_spacing,
        skew_strength=args.skew_strength,
        monthly_max_loss_usd=args.monthly_max_loss_usd,
    )

    ledger, monthly_pnl = run_simulation(
        by_date=by_date, vix=vix, params=params,
        excluded_dates=excluded,
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
        print(f"stopped trades: {metrics['n_stopped']} / {metrics['n_traded']}")
        print(f"months hit cap: {metrics['monthly_stopped_count']} / {metrics['n_total_months']}")
    print(f"\nreport -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
