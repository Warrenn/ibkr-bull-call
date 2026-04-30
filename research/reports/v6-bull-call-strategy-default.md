# v6 Bull-Call-Spread Strategy-Faithful Simulator

**Window**: 2021-05-03 → 2026-04-29

## Strategy parameters

- entry_time_et: 10:30
- settle_time_et: 16:00
- pop_threshold: 0.55
- max_loss_usd: $200
- strike_width (long-walk gap criterion, $): 1.0
- strike_spacing: 5.0
- skew_strength: -0.5
- bid_ask_pct (atm/far): 5% / 30%
- bid_ask_min: $0.05
- monthly_max_loss_usd: $1000

## Provenance

- code_revision: `369ff6e2698ec1e51c6faf1c237203da008c0ba1`
- run_timestamp_utc: 2026-04-30T19:03:05.415210+00:00

## Results

| Metric | Value |
|---|---|
| total days in window | 1224 |
| trades executed | 0 |
| skipped (no_viable_short) | 1224 |

## Honest caveats (still apply)

- **Synthetic option pricing**. Uses BS with a simple log-moneyness
  skew model; not real chain bid/ask. Actual SPX 0DTE has more
  complex skew (smile + smirk) that varies with vol regime.
- **Bid-ask approximation**. Uses linear-in-moneyness model; real
  far-OTM bid-ask is more variable and often wider on quiet days.
- **VIX-as-IV-ATM**. Uses prior-day VIX as ATM IV input; real 0DTE
  ATM IV is often different from 30-day VIX, especially around
  events.
- **Stop-loss timing**. Uses 1-min ES bars to detect breakeven
  crosses; real bot uses spot ticks (faster). May miss intra-
  minute crosses or fire late.
- **No commissions**. Real round-trip on 2-leg spread is ~\$1-3
  per contract on IBKR retail. Subtract that from per-trade P&L
  for a friction-included estimate.

Despite caveats, this simulator implements the user's actual
strike walk + POP + stop loss + monthly cap logic — much closer
to the strategy than v1-v5 tested.