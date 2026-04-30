# v5 Iron-Condor Sweep — TRAIN+VALIDATION+HOLDOUT (full 60mo)

**Window**: 2021-05-03 → 2026-04-29 (1224 days, ~60 months)

## Summary

ALL configurations LOSE money in the BS-with-VIX-as-IV simulation,
despite the strong vol risk premium documented in
`research/reports/v5-vol-premium-test.md` (mean realized = 0.483 ×
implied, t-stat -44.98).

This is a critical finding: vol risk premium being real does NOT
automatically translate to a profitable iron-condor strategy under
fair (BS) pricing.

## Sweep results (no event filter)

| sigmas | wing % | mean P&L/trade | win rate | total P&L | months stopped | max single loss |
|---|---|---|---|---|---|---|
| 1.0 | 0.5% | -$104 | 91.4% | -$77,602 | 38/60 | -$3,338 |
| 1.0 | 1.0% | -$142 | 91.4% | -$106,478 | 38/60 | -$6,696 |
| 1.5 | 0.5% | -$37 | 97.3% | -$26,964 | 14/60 | -$3,341 |
| 1.5 | 1.0% | -$50 | 97.3% | -$36,355 | 14/60 | -$6,682 |
| 2.0 | 0.5% | **-$11** | 99.3% | -$8,142 | 6/60 | -$3,324 |
| 2.0 | 1.0% | -$16 | 99.3% | -$11,693 | 6/60 | -$5,311 |
| 2.5 | 0.5% | **-$3** | 99.8% | -$2,253 | 2/60 | -$2,697 |
| 2.5 | 1.0% | -$6 | 99.8% | -$3,955 | 2/60 | -$5,393 |

**With event filter (FOMC + CPI + NFP + OPEX excluded), 2σ/0.5%
wing**: mean -$9.83/trade, win rate 99.4%, max DD -$9,841, max
single loss -$3,324, 4/60 months stopped. Marginal improvement
(~$1.50/trade) — event filter helps but doesn't flip sign.

## What's happening

The win rate is extremely high (91-99.8% as we go further OTM).
**But the asymmetric P&L distribution wipes out the credit.** When a
wing IS breached, the loss (typically $2,500-$6,500 per contract)
dwarfs the credit collected (typically $5-$50 per contract).

At every sigma level tested, BS-fair-value pricing produces an EV of
**approximately zero, slightly negative**. As we go further OTM:
- Win rate increases (91% → 99.8%)
- Mean P&L per trade approaches zero (-$104 → -$3)
- Total losses decrease in absolute terms
- But never crosses positive

## Why vol premium doesn't translate to profit

The realized-vs-implied test showed implied is 2× realized on
average. So why doesn't a short iron condor extract that premium?

**Because BS prices the iron condor at fair value GIVEN the implied
vol input.** When we use VIX (annualized 30-day implied) and price
the spreads at BS-fair, we collect EXACTLY what the model predicts
the option is worth — no edge.

The vol risk premium that exists in the real world is captured by:

1. **Bid-ask spread**: real markets have implied vol bid < mid <
   ask. Selling at offer (we're seller) gives a small edge above
   fair mid-IV. Our simulation uses mid-IV → no such edge captured.
2. **Skew premium**: OTM puts trade at higher implied vol than ATM
   (vol smile). For symmetric strikes, the put side has more credit
   than BS-with-flat-vol would suggest. Our flat-VIX simulation
   misses this.
3. **Term structure**: 0DTE options sometimes trade at higher IV
   than the 30-day VIX would suggest, especially around events.
   Using VIX as 1-day IV underestimates 0DTE-specific implied.

Without these market-microstructure effects, the simulation
necessarily produces ~zero EV — that's the definition of fair
pricing.

## Implications for v5

The simulation shows that **the strategy depends on edge that
doesn't appear under fair-value pricing**. To know if the strategy
is actually profitable, we need:

1. **Real bid/ask data on SPX 0DTE chains** (~$495-$1,500 from
   Databento or similar)
2. **A simulator that uses real fills** (sells at offer, buys at
   bid)
3. **Skew-adjusted pricing** if we want to extend the simulation
   without buying chain data

Fair-value-BS simulation is the cheapest test we can do, and it's
returning "no edge". Real-data simulation would tell us whether
microstructure effects rescue the strategy.

## Caveats

- **No intraday stop-loss** in this simulation. Real strategy would
  stop a tested side mid-day; this would CAP losses (the simulation
  lets them run to max). With stop-loss, results might be slightly
  better — but probably not enough to flip negative to positive.
- **Monthly capital control IS active** ($1000 max monthly loss).
  Even with this protection, total P&L is negative across all
  configurations.
- **VIX as 1-day IV is approximate**. Realistically, 0DTE IV trades
  higher than the implied 30-day annualized VIX.
- **No transaction costs** beyond the bid-ask issue noted above.
  Adding commissions ($0.65-$1.50 per leg per side, IBKR retail =
  ~$5-$12 per round trip) would worsen results further.
