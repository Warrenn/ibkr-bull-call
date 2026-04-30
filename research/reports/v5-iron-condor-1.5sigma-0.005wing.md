# v5 Iron-Condor Simulator

**Window**: 2021-05-03 → 2026-04-29

## Configuration

- short_strike_distance_sigmas: **1.5**
- wing_width_pct: **0.50%**
- risk_free_rate: 5.00%
- contracts_per_trade: 1
- multiplier_per_contract: $100
- monthly_max_loss_usd: $1000
- pricing: Black-Scholes with VIX-as-IV (no real bid/ask)
- event filter: disabled

## Provenance

- code_revision: `fb078b364a8bb6517033f8ad5c8ea45208ddd764`
- run_timestamp_utc: 2026-04-30T18:26:09.961087+00:00
- ES dataset: `es_intraday.parquet`

## Aggregate Results

| Metric | Value |
|---|---|
| total days in window | 1224 |
| trades executed | 1065 |
| skipped (event filter) | 0 |
| skipped (monthly stop) | 159 |
| **total P&L** | **$-39353** |
| mean P&L per trade | $-36.95 |
| median P&L per trade | $0.31 |
| std P&L per trade | $278.95 |
| win rate | 97.3% |
| max single-trade win | $1 |
| max single-trade loss | $-3341 |
| max drawdown (cumulative) | $-39430 |
| months in window | 60 |
| winning months | 34 |
| months that hit stop | 14 |

## Per-Year Breakdown

| year | n | total_pnl | mean_pnl | win_rate |
|---|---|---|---|---|
| 2021 | 166 | $49 | $0.30 | 100.0% |
| 2022 | 206 | $-11889 | $-57.71 | 95.6% |
| 2023 | 244 | $-2651 | $-10.86 | 97.5% |
| 2024 | 182 | $-11228 | $-61.69 | 96.2% |
| 2025 | 187 | $-12718 | $-68.01 | 96.8% |
| 2026 | 80 | $-917 | $-11.46 | 98.8% |

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