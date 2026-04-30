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

### Phase 1.5: Backtest harness + data spec

Phase 1 says "validate the data". This phase says "name the data, name the
harness, freeze the spec — then everyone (including future-you) can audit
what the backtest actually ran."

**Without this phase, every later phase is unfalsifiable**: a result like
"expectancy +$8 / spread post-cost" means nothing unless the data version,
slippage model, and code revision that produced it are pinned and
re-runnable.

#### 1.5.1 Data acquisition spec

Decide and document, **per resolution mode**:

| Item | HRM (required for R23a, R24, R25, R26, VIX-jump) | CRM (entry / regime ablation only) |
|---|---|---|
| SPX spot | 1-second or tick from CBOE DataShop / Polygon / Algoseek | 1-minute SPX bars |
| SPX 0DTE option chain | ≤5-second snapshots, full bid/ask/size per leg | end-of-day chain snapshots + CBOE 0DTE summaries |
| VIX, VIX3M, VVIX | ≤5-second intraday | daily |
| ES (lead-time future) | 1-second or tick | 1-minute |
| Calendar inputs | FOMC/CPI/NFP/PPI/PCE exact times; OPEX dates; NYSE half-days | same |
| Window | ≥36 months of history; most recent 6 months reserved as untouched holdout per §6.5 | same |
| Storage | Parquet / DuckDB partitioned by trade-date; checksum manifest | same |

For each dataset, record: vendor, license, acquisition date, raw byte
checksum, and a "resolution mode" tag (`HRM` or `CRM`). The harness MUST
refuse to run an HRM-only rule against CRM-tagged data.

#### 1.5.2 Backtest harness build (the per-path-slippage simulator)

The harness from `strategy-review.md` §6.2 — quoted in full so the
contract is in this document, not by reference:

> **All realised P&L metrics, gates, and invalidation checks must use
> net P&L after commissions, fees, and modelled slippage.** This
> includes the per-trade expectancy in §6.3 below, the monthly
> net-negative gate (R9), the weekly loss cap, and every invalidation
> criterion in §6.6. There is no "gross P&L" anywhere in the validation
> pipeline — every reported number is post-cost.

> A **single** per-trade slippage assumption is **not enough** for this
> strategy. Different exit paths have radically different fill behaviour;
> modelling them with one number can materially overstate edge. The
> validation pipeline must use a **per-path slippage model** with
> separate calibrations.

The six paths the harness must cost separately (the "1×" baseline that
"2× stress" and "3× stress" multiply against) are, per §6.2:

| Path | Default (1× baseline) | Adverse-fill stress |
|---|---|---|
| Entry — combo LMT ladder (R19) | mid + 0.5 tick (~$3 / contract) plus 1× combo bid-ask | mid + 1.5 ticks; full ladder + miss-rate stress |
| Optional stop / time-stop exit (R25, R26) | combo MKT crossing 1× spread bid-ask | crossing 2× the bid-ask width |
| Profit-taking exit (R24) | combo MKT at mid + 1 tick | combo MKT at mid |
| Hard close (R28) | combo MKT crossing 1× bid-ask | crossing 2–3× the bid-ask width |
| Leg-out flatten (R22) | single-leg MKT crossing 1× that leg's bid-ask | crossing 2× that leg's bid-ask |
| Data-outage emergency flatten (R23a) | combo MKT, worst of last-known + 5% adverse move on underlying | full combo bid-ask + an additional adverse move equal to the largest 1-second move observed in the prior 60 s |

Commission model, per §6.2: "$0.65 + ~$0.20 SPX options exchange/reg
fees per contract per leg, on entry AND exit; for a 2-leg combo that's
~$3.40 round-trip, plus any leg-out flatten fees."

The harness must therefore expose, as first-class config:

- a `slippage_model` object with a per-path multiplier (default `1.0`,
  stress runs `2.0`, `3.0`)
- a `commission_model` object stress-tested at $0.65, $1.00, $1.30 per
  leg (per §6.5)
- a `worst-path adverse-fill` toggle that worsens the single
  most-frequent loss path by 50% (per §6.2)
- a `capital_overlay` switch (`monthly_net_negative_gate` ON / OFF) so
  every result can be reported both ways (per §6.5 capital-overlay
  separation)

If the harness cannot toggle each of those independently, ablation
results from §6.4 will not be trustworthy.

#### 1.5.3 Reproducibility requirements

A backtest result is admissible as evidence in any later phase only if:

- the input-data fingerprint (per-dataset checksum manifest from 1.5.1)
  is recorded with the result
- the harness git revision is recorded with the result
- all RNG-touching code paths use a pinned seed; the seed is recorded
- inputs (chains, vol surfaces, calendars) are loaded from immutable
  fixtures, not refreshed live during a run
- a re-run of the same `(data fingerprint, code rev, seed, config)`
  reproduces the per-trade ledger byte-for-byte

Anything that fails these checks is exploration, not evidence.

#### 1.5.4 Spec-freeze enforcement (no peeking)

Before any holdout window is touched (per §6.5), the **full ruleset
must be frozen** — every threshold, every objective, every filter,
every kill switch — and committed to a versioned document
(`STRATEGY-SPEC-v{N}.md` or git tag `spec-vN`) with a change log.

The holdout is then evaluated **once**.

Any change to the strategy *after* holdout evaluation — even tightening
a threshold by a tick — creates a new version (`v{N+1}`), invalidates
the prior holdout, and requires either a fresh untouched holdout
window OR a fresh forward-paper period of full promotion-gate length
(per §5 of `strategy-review.md`).

The harness must refuse to score a holdout run against an unfrozen
spec — this is the only mechanical guard against silent peek-and-tune.

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

#### Phase 6 deliverables

Phase 6 has two jobs: stress the edge, and produce the evidence that
go/no-go criterion #5 ("Drawdowns must be acceptable for the return
earned") needs in order to be answered. The slippage / commission /
parameter stresses above answer the *robustness* half. The drawdown
deliverables below answer the *acceptability* half. Both must ship out
of this phase.

The "2× / 3× stress" terms above multiply against the per-path baselines
defined in `strategy-review.md` §6.2 and re-stated in Phase 1.5 — they
are not freestanding numbers.

For each surviving variant, the phase must produce, on the holdout
window evaluated under the frozen spec (per §6.5):

**Drawdown statistics** (the producing phase for go/no-go criterion #5):

- **Max drawdown — dollars.** Peak-to-trough P&L drop, post-cost.
- **Max drawdown — percent of NLV.** Same trough, expressed as a
  fraction of starting NLV at peak.
- **Drawdown duration distribution.** For every drawdown ≥1% of NLV,
  the time from peak to trough. Report median, 75th, 95th percentile.
- **Recovery time distribution.** For every drawdown ≥1% of NLV, the
  time from trough to a new equity peak. Report median, 75th, 95th
  percentile, plus the count of drawdowns not recovered within the
  holdout window.
- **Time-underwater fraction.** Share of the holdout window during
  which equity sat below its prior peak.
- **Worst rolling 1-month, 3-month, 12-month return.** Post-cost.
- **Max consecutive losers** and **max single-trade loss** (already
  required by §6.3 — re-listed here so the drawdown evidence is in one
  place).

**Comparison vs. simpler alternatives.** Each drawdown statistic above
must also be reported for the `strategy-review.md` §4.12 benchmarks,
evaluated on the same window with the same cost discipline:

- no-trade baseline (cash + 3-month T-bill)
- long-call-only on the same confirmation
- bull-put spread with the same regime filters
- call butterfly on the same view
- ES / MES directional proxy

If the bull-call spread does not beat all of those on **risk-adjusted
return per unit of drawdown** (Calmar or equivalent) on the holdout,
the complexity is not paying for itself — pick the simpler alternative.

**Capital-overlay separation.** Every drawdown statistic above must be
reported **both with and without** the monthly net-negative gate (R9)
enabled, per §6.5. The "without gate" run is what tests whether the
edge has acceptable drawdowns on its own; the "with gate" run is what
tests realised drawdown for the deployed configuration. If the
"without gate" max drawdown breaches the §6.6 invalidation threshold
(>15% post-cost), the strategy is dead regardless of how good the
"with gate" curve looks — the gate is doing the work.

**Stress-run drawdowns.** Every drawdown statistic above must also be
reported under the 2× and 3× per-path slippage stress and under the
worst-path 50% adverse-fill stress. A strategy whose drawdown
acceptability survives the 1× baseline but breaches the §6.6 cap under
2× stress is not robust enough for live capital.

These deliverables are what the criterion #5 reviewer (you, future-you,
or anyone resuming this work) reads to answer "are the drawdowns
acceptable for the return earned." Without them, criterion #5 has no
producer and the go/no-go decision cannot be made honestly.

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
2. **Spec the data and build the harness (Phase 1.5).** Pin vendors,
   checksums, resolution-mode tags; build the per-path slippage
   simulator from `strategy-review.md` §6.2; pin seeds, code revs, and
   fixtures so every later result is reproducible. Freeze the strategy
   spec (`STRATEGY-SPEC-v{N}.md`) before any holdout window is touched.
3. Build the simplest control backtest with realistic costs, on the
   validated data.
4. Test whether the bullish continuation signal has standalone directional edge.
5. Compare alternative trade expressions on the same signal (risk-adjusted, post-cost).
6. Add rules one at a time through ablation.
7. Stress-test fills, fees, and parameters; produce the Phase 6
   drawdown deliverables (max DD $/%, DD-duration and recovery-time
   distributions, vs. simpler-alternative comparison) — these are the
   evidence that go/no-go criterion #5 needs in order to be answered.
8. Run with and without capital overlays.
9. Paper trade the best surviving candidate (≥6 months OR ≥60 filled
   spreads, whichever later).
10. Climb the live deployment staircase (Phase 10), Tier 0 → Tier 1 →
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
