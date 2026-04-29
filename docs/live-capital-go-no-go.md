# Live Capital Go / No-Go Checklist

## Purpose

This document defines the minimum evidence required before moving the SPX 0DTE
bull call spread strategy from paper trading to small live capital.

This is intentionally blunt. If the strategy cannot pass these tests, it should
not be traded live.

---

## Go / No-Go Checklist

### 1. Net expectancy after costs must be clearly positive

**Go only if:**

- Out-of-sample expectancy is meaningfully above zero after commissions, fees,
  and realistic slippage.
- Profitability does not rely on midpoint-only fill assumptions.
- Profitability survives at least a 2x slippage stress test.

**No-go if:**

- Expectancy is near zero.
- Expectancy flips negative under more realistic execution assumptions.
- Results depend on optimistic fills that are not achievable in practice.

### 2. The edge must survive out of sample

**Go only if:**

- Walk-forward results remain positive across multiple test windows.
- The most recent holdout period is profitable, or at least consistent with
  the broader edge.

**No-go if:**

- Results are strong only in-sample.
- The latest holdout is weak or negative.
- A single favorable regime explains most of the returns.

### 3. The rule stack must survive ablation

**Go only if:**

- The final ruleset beats a simpler control configuration.
- Key filters improve expectancy, drawdown, or robustness out of sample.
- Small parameter changes do not destroy the strategy.

**No-go if:**

- The strategy only works as a fragile pile of filters.
- One or two threshold tweaks collapse the result.
- The design appears obviously overfit.

### 4. The strategy must beat simpler alternatives

**Go only if:**

- It beats no-trade / cash (3-month T-bill is the appropriate hurdle —
  this strategy is mostly *in cash*, so the cash benchmark is the floor,
  not buy-and-hold equity).
- It beats at least most simpler same-view alternatives on risk-adjusted,
  post-cost return.
- The complexity of the bull call spread is justified by results.

**No-go if:**

- Long calls, MES, or even no trade perform as well or better.
- The spread is not clearly the best expression of the directional view.

### 5. Drawdowns must be acceptable for the return earned

**Go only if:**

- Max drawdown is tolerable in both dollars and percent of NLV.
- Losing streaks are survivable psychologically and financially.
- Capital overlays improve safety but are not the only reason the equity curve
  looks acceptable.

**No-go if:**

- Drawdowns are too severe relative to expected return.
- One bad regime erases months of gains.
- Kill switches are doing all the work.

### 6. The strategy must survive realistic execution

**Go only if:**

- Paper/live-simulated fills are close to modeled assumptions.
- Liquidity filters do not reduce trade count so much that the strategy
  disappears.
- Execution, stale-data, and recovery paths are operationally sound.

**No-go if:**

- Real fills are materially worse than modeled.
- Liquidity is too inconsistent.
- Operational failures change the economics of the strategy.

### 7. The monthly stop-trading gate must not be mistaken for alpha

**Go only if:**

- The strategy remains positive without the monthly net-negative gate.
- The gate improves capital control rather than manufacturing the appearance of
  profitability.

**No-go if:**

- The strategy only looks good because losing months are truncated.
- Without the gate, the edge disappears.

### 8. Paper trading must confirm the backtest

**Go only if:**

- Forward paper trading over a **meaningful sample** broadly matches the backtest.
  *Meaningful sample = ≥6 months of forward paper OR ≥60 actually-filled
  spreads, whichever comes later. Skipped-trade days do not count.*
- Live timing, skipped trades, and fills do not materially degrade outcomes.

**No-go if:**

- Paper trading diverges materially from backtest expectations.
- Setups occur less often than expected.
- Live behavior exposes hidden assumptions.

### 9. Small live capital should begin only after all major evidence gates pass

**Go only if all are true:**

- Positive out-of-sample expectancy after stressed costs.
- Robust walk-forward performance.
- Ablation supports the retained rules.
- Beats simpler alternatives.
- Paper trading confirms feasibility.
- No unresolved execution or data-integrity risks remain.

**No-go if any one of these is missing.**

### 10. Even after "go", live deployment is still an experiment

**Required live posture:**

- **Smallest practical size**, defined concretely: 10% of the eventual
  target capital allocation, with `MAX_LOSS_USD = 200` per spread
  regardless of NLV, and `weekly_loss_cap_pct = 0.01` (1% of NLV — half
  the production setting). These tighten further if losses appear before
  the next staircase tier (see "Live Deployment Staircase" below).
- Explicit daily / weekly / monthly loss limits (per `strategy-review.md`
  §4.11 and R8/R9).
- Predefined shutdown criteria (see "Live Deployment Staircase" below
  for concrete thresholds, not vague "if it degrades, stop").
- Ongoing review versus backtest and paper-trading assumptions.

If performance degrades quickly in live trading, stop.

---

## The Three-Sentence Test

Do not move to live capital unless all three statements are true:

1. It makes money after real costs.
2. It keeps making money outside the sample used to design it.
3. It beats simpler ways of expressing the same view.

If all three cannot be stated confidently, it is a no-go.

---

## Best Investigation Approach

The best way to get confident answers is not to jump straight into more rule
design. It is to run a staged investigation that separates:

- alpha
- execution
- robustness
- capital overlays

The goal is to avoid confusing "less bad" with "good."

### Phase 0: Fix all known P0 implementation defects

Before any backtest or research run, fix the known defects identified in
`strategy-review.md` §3. A backtest of broken code is research-grade only
— if the code path under test will crash in live operation, the backtest
results are incomparable to the deployed system. Specific items:

- `cpapi/execution.py:219` — `submit_close_market` references undefined
  `phase_timeout` (NameError on the first stop fire). Fix to `timeout_s`.
- `reconcile.py` — add hard `tradingClass == "SPXW"` guard; refuse to
  adopt any SPX monthly position.
- Other items per `strategy-review.md` §3 (signed-cost invariants in
  reconcile, time-adaptive strike re-selection, signal-aware sleep in
  the retry loop, etc.).

The backtest must model the *fixed* code, not the broken one. Otherwise
live behaviour will diverge from research in the most expensive direction
— failed exits.

### Phase 1: Validate the data before validating the strategy

Before trusting any backtest result, confirm:

- the option chain timestamps line up with SPX / ES / VIX timestamps
- quote staleness and crossed markets are handled consistently
- slippage assumptions match actual executable prices, not midpoints
- PM-settled SPXW contracts are isolated correctly
- both data resolution modes from `strategy-review.md` §6.1 are honoured
  (HRM for fast-feedback rule validation; CRM for entry/regime ablation
  with explicit cadence slowdown)

Bad data will create fake edge faster than bad ideas. **Build nothing on
top until this phase passes.**

### Phase 2: Define the control strategy on validated data

Build a deliberately simple baseline:

- fixed entry window
- simple long/short strike rule
- no profit-taking
- no complex regime stack
- realistic fees and slippage

This gives you a clean control on data you trust. Every additional rule
must beat this control, not just the prior draft.

### Phase 3: Test the raw directional idea first

Answer this before everything else:

> Does bullish intraday continuation in the chosen window have enough edge to
> justify any options structure at all?

Test the directional signal independently using:

- MES / ES proxy returns after confirmation
- SPX move distribution from entry time to exit time
- regime segmentation by VIX, event day, and opening structure

If the directional edge is weak, the options wrapper will not save it.

### Phase 4: Compare trade expressions

Once the directional setup shows promise, compare:

- bull call spread
- long call
- call butterfly
- bull put spread
- MES / ES directional trade

Do this on the same confirmed signal, same sample, same cost discipline,
and **on a risk-adjusted, post-cost basis** (consistent with
`strategy-review.md` §6.6 — Sharpe / Sortino / Calmar, not raw P&L).

This answers whether the bull call spread is actually the right expression of
the view.

### Phase 5: Run ablation on every filter and exit rule

Do not tune the whole strategy at once.

Test the marginal impact of:

- regime filters
- event filters
- confirmation filters
- liquidity filters
- strike-selection objective
- stop logic
- time stop
- profit-taking
- hard close rules

Rules that do not improve out-of-sample expectancy, drawdown, or robustness
under stress should be removed.

### Phase 6: Stress everything

For every promising variant, run:

- 2x and 3x slippage stress
- higher commission assumptions
- worse fill assumptions on entry and exit
- parameter perturbation around each threshold
- regime-specific performance slicing

If a slight worsening of assumptions destroys the edge, the edge is not robust
enough for live capital.

### Phase 7: Separate alpha from capital overlays

Report every result:

- with the monthly net-negative gate
- without the monthly net-negative gate

Do the same for any weekly stop or drawdown overlay.

That tells you whether the strategy itself has edge, or whether overlays are
merely truncating damage.

### Phase 8: Forward paper trading is mandatory

Run the best candidate live in paper mode long enough to answer:

- do fills resemble modeled assumptions?
- do signals trigger as often as expected?
- do outages, stale quotes, and edge cases change behavior?
- does realized P&L resemble backtested expectations?

Paper trading is the bridge between "backtest idea" and "live risk."

### Phase 9: Promote to smallest live size only if the evidence is boring

The right signal before going live is not excitement. It is boredom.

You want:

- repeatable evidence
- no unexplained jumps in performance
- no dependence on one lucky regime
- no unresolved operational questions

If the results still look surprising, they are not ready.

### Phase 10: Live deployment staircase

After Phase 9 clears, do not ramp straight to target capital. Climb a
staircase, with each tier requiring the prior to be cleared on named
criteria. Numbers are starting hypotheses and must be calibrated to your
own NLV and pain tolerance — the structural commitment is "named tiers
and named criteria", not "smallest size, then full size".

**Tier 0 — initial:**
- `MAX_LOSS_USD = 200`, `weekly_loss_cap_pct = 0.01` (1% of NLV).
- Hold for ≥30 actually-filled spreads AND ≥2 calendar months. No tier
  change inside that window regardless of P&L.
- Manual review of the first 10 trades (entry, fill, monitor decisions,
  exit). Look for divergence from backtest behaviour, not just P&L.

**Tier 1 — half-target:**
- `MAX_LOSS_USD` and `weekly_loss_cap_pct` to 50% of target.
- Promotion criteria from Tier 0:
  - Realised post-cost expectancy within ±20% of the backtest
    expectancy from `strategy-review.md` §6.5 (HRM mode).
  - Zero unresolved operational incidents (data-outage flattens,
    leg-out flattens, NameError-class crashes).
  - Monthly net-negative gate (R9) has not fired.
- Hold for ≥60 spreads AND ≥3 calendar months before next tier.

**Tier 2 — target:**
- `MAX_LOSS_USD` and `weekly_loss_cap_pct` at intended production levels.
- Same promotion criteria as Tier 0→Tier 1, evaluated over Tier 1's
  evaluation window.

**Shutdown criteria** (any one halts new entries; existing positions are
managed under the normal exit rules; do not cancel exits to "save" a
position):

- Monthly net-negative gate fires (per `strategy-review.md` R9).
- 2× the backtest's max consecutive losers observed live.
- Realised post-cost expectancy diverges from backtest expectancy by >50%
  over a tier's evaluation window.
- Any P0 operational incident (data-outage flatten, leg-out flatten,
  NameError-class crash) — full halt pending root-cause review, not just
  gate-disable. Resume only after the root cause is fixed AND a fresh
  forward-paper sample (Tier 0 length) clears.

The staircase removes the deployment-day temptation to "go bigger now,
the system is working" without named evidence. Each tier is its own
mini-Phase-9.

---

## Practical Order Of Work

If proceeding from here, the highest-value order is:

0. **Fix all known P0 implementation defects** identified in
   `strategy-review.md` §3 (the `phase_timeout` NameError, the SPXW
   tradingClass guard, etc.). Backtests of broken code are not research.
1. **Validate the timestamp and executable-price data.** Confirm HRM/CRM
   data resolution per `strategy-review.md` §6.1 before building anything
   on top.
2. Build the simplest control backtest with realistic costs, on the
   validated data.
3. Test whether the bullish continuation signal has standalone directional edge.
4. Compare alternative trade expressions on the same signal (risk-adjusted, post-cost).
5. Add rules one at a time through ablation.
6. Stress-test fills, fees, and parameters.
7. Run with and without capital overlays.
8. Paper trade the best surviving candidate (≥6 months OR ≥60 filled
   spreads, whichever later).
9. Climb the live deployment staircase (Phase 10), Tier 0 → Tier 1 →
   Tier 2, with named promotion criteria at each step.

This sequence is the best way to get confident answers quickly without
deceiving yourself.

---

## Bottom Line

The best investigation path is skeptical and subtractive:

- prove the directional edge first
- prove the options structure is the best expression of that edge
- prove the filters actually help
- prove the edge survives costs and worse assumptions
- prove the live paper behavior matches the research

If any of those proofs fails, stop and simplify rather than adding more rules.
