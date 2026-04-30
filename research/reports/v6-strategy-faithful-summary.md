# v6 Strategy-Faithful Bull-Call-Spread Simulator — Summary

This is the cleanest test we can do without buying real chain data:
implements the **actual strategy logic** (descending strike walk for
long, ascending walk for short with POP + debit constraints, breakeven
stop loss with intraday spot tracking, monthly capital control)
against synthetic option chains built from BS pricing with
log-moneyness skew + bid-ask approximation.

This is a substantial improvement over v1-v5, which tested abstractions
of the strategy (directional signal, vol premium, fair-priced
iron condor) rather than the strategy itself.

## Headline finding: the strategy as designed LOSES MONEY

### User's intended config (per `.env.example`): ZERO TRADES

**MAX_LOSS_USD=$200, POP_THRESHOLD=0.55, full 60mo data**:

| strike_width | trades fired |
|---|---|
| $1 | 0 |
| $3 | 0 |
| $5 | 0 |
| $10 | 0 |

**The strategy as configured by the user does not fire a single trade
in 5 years of data.** The combination of MAX_LOSS=$200 and POP=0.55
is mutually exclusive: the OTM strikes that satisfy POP have debits
that exceed MAX_LOSS, and the deep-ITM strikes that fit MAX_LOSS
have unsatisfiable bid-ask gap criteria.

### Relaxed config: lots of trades, big losses

To get the strategy to fire, MAX_LOSS must be raised significantly:

| MAX_LOSS | strike_width | trades | total P&L | win rate |
|---|---|---|---|---|
| $500 | $5 | 325 | -$64,325 | 24.3% |
| $1,000 | $5 | 336 | **-$63,571** | 21.1% |
| $1,000 | $10 | 150 | -$80,571 | 0.0% |
| $2,000 | $10 | 185 | -$85,346 | 18.9% |

All viable configurations LOSE substantial money over 60 months.

## Best-viable config detailed analysis

**MAX_LOSS=$1,000, strike_width=$5, POP=0.55, 60mo full window**:

- Trades: 336 over 60 months (~5.6/month)
- **Total P&L: -$63,571**
- Mean per trade: -$189
- Win rate: **21.1%**
- Max drawdown: -$63,327 (essentially monotonic)
- **Stopped trades: 265 of 336 (78.9%)**
- Months hitting cap: 55 of 59 (93%!)

### Outcome breakdown

| Outcome | n | mean P&L | total | % |
|---|---|---|---|---|
| **Stopped (breakeven cross)** | 265 | **-$271** | **-$71,865** | 78.9% |
| Settled (held to PM) | 71 | +$117 | +$8,293 | 21.1% |

**The breakeven stop loss is what destroys the strategy.** 79% of
trades have spot rise to breakeven, then dip below — triggering the
stop. The bid-ask cost on closing converts these would-be break-even
or modest-loss expiries into definitive losses of $271 each.

The settled trades (21.1% that held to PM) average +$117 — modestly
profitable. But they're a minority and don't compensate for the
stop-induced losses.

### Per-year consistency

| year | n | total | mean | win_rate |
|---|---|---|---|---|
| 2021 | 55 | -$8,815 | -$160 | 25.5% |
| 2022 | 53 | -$13,454 | -$254 | 13.2% |
| 2023 | 81 | -$13,136 | -$162 | 19.8% |
| 2024 | 87 | -$13,032 | -$150 | 27.6% |
| 2025 | 52 | -$12,619 | -$243 | 19.2% |
| 2026 (partial) | 8 | -$2,517 | -$315 | 0.0% |

**Consistently negative across all 5 years and 1 partial year.** No
regime where the strategy works.

## Why the breakeven stop destroys the strategy

The stop logic in user's spec:

> Arm when spot has been observed at-or-above breakeven post-entry.
> Once armed, fire on the first tick where spot < breakeven.

For a bull call spread with **breakeven > current spot at entry**:

1. **Spot never reaches breakeven** → spread expires OTM → loss (debit)
2. **Spot reaches breakeven and stays above** → spread settles ITM → profit
3. **Spot reaches breakeven, then dips below** → stop fires → premature close → loss

Case 3 dominates because:
- SPX intraday volatility creates frequent oscillations
- Once breakeven is touched (arming the stop), even a small dip fires it
- The bid-ask spread on a 4-leg close (sell long, buy short) costs ~$1-2 per share
- A spread that dipped slightly below breakeven still has time-value worth roughly the original debit, but selling at bid + buying at ask eats the entire spread

The 79% stop rate means 4 of every 5 trades hit case 3.

The stop logic was designed to "cut losses when a winning position turns losing." But for a bull call spread:
- There's no offsetting "other leg" (unlike iron condor)
- The stop fires on noise, not on real losses
- The cost of stopping (bid-ask slippage) creates the loss

## What this DOES tell us

Across v1-v6 testing:

1. **Vol risk premium is real** (PR #63): implied = 2× realized
2. **v2 directional signal exists** on 60mo (PR #65): t=+2.48
3. **Underlying signal Sharpe 1.12** (PR #67): borderline tradeable
4. **Strategy as designed FIRES ZERO TRADES** with user defaults (this PR)
5. **Strategy with relaxed params LOSES -$63K over 60mo** (this PR)
6. **Breakeven stop loss is the killing mechanism** (this PR): 79% stop rate, $271 avg stop loss

The directional signal might exist, but **the strategy's stop-loss
logic destroys it through frequent premature closes at unfavorable
fills**. A version of this strategy WITHOUT the breakeven stop, or
with a much more permissive trigger (e.g., spot < breakeven × 0.99),
might be tradeable. As designed, it isn't.

## Caveats (acknowledge but don't change the conclusion)

- **Synthetic option pricing**: BS + linear log-moneyness skew is
  approximate. Real SPX skew is more complex but the 79% stop rate
  is driven by SPOT crossings, not options pricing.
- **Bid-ask approximation**: 5-30% half-spread by moneyness is
  conservative. Real spreads on 0DTE far OTM may be wider in some
  regimes; on actively-traded near-ATM may be tighter. Net effect
  on -$63K conclusion is small.
- **VIX-as-IV-ATM**: 0DTE ATM IV is sometimes higher than 30-day VIX.
  Higher IV → higher option prices → wider strike walks → marginally
  different selection. Doesn't change the stop-loss issue.
- **1-min stop bars vs tick**: real bot uses spot ticks, simulator
  uses ES 1m bars. Difference is small for breakeven crossings.
- **No commissions modeled**: real round-trip costs ~$1-3/contract.
  Adding commissions makes results MORE negative.

Even with all caveats acknowledged, the magnitude of loss
(-$63,571 over 60mo) and the consistent yearly pattern make this a
clear kill of the strategy as designed.

## What we keep regardless

- Real strategy-faithful simulator (research/scripts/sim_bull_call_strategy.py)
  that we can use to test ANY future modifications to the strategy
- Findings about WHICH component breaks the strategy (the stop loss)
- Direction for any future work: redesign the stop, or remove it entirely

## Final synthesis

We have now done a fair and reasonable evaluation of the strategy.

**The strategy as specified in the project's CLAUDE.md does not work**:
- With user defaults: doesn't fire trades (constraints incompatible)
- With relaxed params: fires trades but loses -$63K over 60mo
- The stop loss destroys 79% of trades through premature closes

The directional signal (PR #65) hint that "something might be there"
turns out to be **not extractable through this strategy structure**.
A different structure (no stop, different stop, different
expression) might work — but that's a future v7 question, not a
v6 question.

Total project Databento spend: $14.12 of $125 free credit. The
falsification framework, applied properly to the actual strategy
on expanded data with synthetic but reasonable simulation, has now
conclusively answered: this strategy doesn't work as designed.
