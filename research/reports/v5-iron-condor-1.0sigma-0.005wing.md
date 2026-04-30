# v5 Iron-Condor Simulator

**Window**: 2021-05-03 → 2026-04-29

## Configuration

- short_strike_distance_sigmas: **1.0**
- wing_width_pct: **0.50%**
- risk_free_rate: 5.00%
- contracts_per_trade: 1
- multiplier_per_contract: $100
- monthly_max_loss_usd: $1000
- pricing: Black-Scholes with VIX-as-IV (no real bid/ask)
- event filter: disabled

## Provenance

- code_revision: `fb078b364a8bb6517033f8ad5c8ea45208ddd764`
- run_timestamp_utc: 2026-04-30T18:25:58.326715+00:00
- ES dataset: `es_intraday.parquet`

## Aggregate Results

| Metric | Value |
|---|---|
| total days in window | 1224 |
| trades executed | 747 |
| skipped (event filter) | 0 |
| skipped (monthly stop) | 477 |
| **total P&L** | **$-77602** |
| mean P&L per trade | $-103.88 |
| median P&L per trade | $15.43 |
| std P&L per trade | $484.82 |
| win rate | 91.4% |
| max single-trade win | $33 |
| max single-trade loss | $-3338 |
| max drawdown (cumulative) | $-78987 |
| months in window | 60 |
| winning months | 15 |
| months that hit stop | 38 |

## Per-Year Breakdown

| year | n | total_pnl | mean_pnl | win_rate |
|---|---|---|---|---|
| 2021 | 146 | $-689 | $-4.72 | 95.9% |
| 2022 | 99 | $-22802 | $-230.32 | 82.8% |
| 2023 | 149 | $-18208 | $-122.20 | 88.6% |
| 2024 | 134 | $-17960 | $-134.03 | 91.8% |
| 2025 | 153 | $-18292 | $-119.56 | 92.8% |
| 2026 | 66 | $350 | $5.30 | 97.0% |

## Key caveats

- **No bid/ask spread** in the simulation. BS-with-VIX-as-IV gives
  mid-implied prices; real iron condors pay the bid-ask on 4 legs
  per round-trip. For SPX 0DTE far-OTM, that's typically $0.05-$0.20
  per contract per leg = $20-80 / round-trip cost not modeled here.
- **No intraday stop loss**. Simulation holds to settlement; real
  strategy would stop a tested side mid-day. This OVERSTATES
  losses on big-move days (the actual strategy would close at the
  stop, not let it run to max loss).
- **VIX-as-IV approximation**. Real options trade at strike-
  specific implied vols (the smile / skew). Far-OTM puts trade
  at higher IV than ATM (skew); using flat VIX may UNDERESTIMATE
  put credit and OVERESTIMATE call credit by a few percent.
- **Monthly capital control** is applied but the threshold is a
  hard parameter; real operators may use trailing limits.
- **Pricing edge cases**: when both wings are deep OTM, BS prices
  approach zero — real markets have minimum bid (~$0.05), so
  far-OTM credits are actually slightly higher in reality.