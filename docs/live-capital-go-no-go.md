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
- **strike-selection objective** — see §5.A below
- **stop logic** — see §5.B below
- **profit-taking policy** — see §5.C below
- **rolling policy** — see §5.D below
- time stop
- hard close rules

Rules that do not improve out-of-sample expectancy, drawdown, or robustness
under stress should be removed.

#### 5.0 Ablation cardinality and the role of paper trading

The four candidate axes (§5.A strikes, §5.B stops, §5.C profit-taking,
§5.D rolling) each carry 4–5 variants including their controls. A naïve
full Cartesian explores `5 × 5 × 5 × 4 = 500`-plus cells — operationally
impossible to backtest meaningfully and statistically infeasible to
paper-trade.

The §6.4 procedure is **authoritative** and caps this to ~30–50 ablations:

1. Establish a *control* configuration (minimal rules — current defaults
   on each axis or simplest variant).
2. For each candidate filter / exit rule independently, measure the
   marginal contribution by **adding it alone to the control** and
   re-running. That's `5 + 5 + 5 + 4 ≈ 19` cells.
3. Then test in pairs and triples — interactions matter (e.g. mark-based
   stop and trailing PT can compound or cancel). Pick a small set of
   interaction tests guided by §6.4 step 2 winners; do **not** Cartesian.
4. **Remove any rule that improves backtest appearance but not
   out-of-sample expectancy** under the §6.5 walk-forward methodology.
5. Prefer fewer rules. A 3-rule strategy that beats a 7-rule one out of
   sample is the keeper.

**Paper-trading variant cap.** Paper trading is the validation of
**one** frozen spec, not a parallel hypothesis-discovery channel. The
backtest harness (§1.5.2) is where the four axes get explored on years
of data; paper trades only the *winner* (or, if backtest produces 2
specs with overlapping CIs on ranked metrics, the **2-3 close rivals**
to break the tie). Paper-trading more than 3 variants concurrently
fragments the trade pool below §6.5's bootstrap-CI floor and multiplies
operational overhead (one bot instance, one DDB table, one CloudWatch
alarm set per variant) without producing additional statistical power.

#### 5.A Strike-selection objective ablation (mandatory)

The current production code in `src/bull_call/strikes.py` implements the
"widest passing short strike" objective that `strategy-review.md` §2.1 / §3.5
flags as **structurally adverse** — holding the long fixed and walking the
short out trades probability for nominal max-profit. This is a starting
hypothesis only. Before live capital, the ablation must compare it
head-to-head against the redesign objectives from `strategy-review.md` §4.6 /
§7 R16-R17:

| Variant | What it picks | Source |
|---|---|---|
| `max_width_passing` (current) | Widest short under POP + dollar caps. | strikes.py:60-96 |
| `closest_to_target_ratio` (redesign default) | (long, short) tuple whose `debit / width` is closest to the configured band midpoint. | strategy-review.md §7 R17 |
| `max_entry_rr` | Tuple maximising `(width − debit) / debit` (true entry max-profit / max-loss). | strategy-review.md §7 R17 |
| `max_pop_at_breakeven` | Tuple maximising BS `N(d2)` at the spread's breakeven. | strategy-review.md §7 R17 |
| `max_exit_efficiency` | Tuple with the highest `executable_credit_estimate / debit` ratio (a *liquidity / exitability* proxy — explicitly NOT an entry payoff measure). | strategy-review.md §7 R17 |

The ablation must additionally vary the **long candidate set**: a single
closest-to-target-delta long (today's behaviour, where the "long" is fixed
before the short search) vs. a target-delta band of 3–5 longs scored as a
full `(long_candidate × candidate_widths)` tuple set (`strategy-review.md` §7
R16). Locking the long before scoring bakes in an arbitrary entry constraint.

**Pass criterion**: an objective wins only if it beats `max_width_passing`
on **risk-adjusted, post-cost expectancy AND drawdown** on the holdout
under the frozen spec, AND survives the per-path 2× / 3× slippage stress
from §6.2. If no objective beats the control on the holdout, keep the
simplest one and treat the redesign as not-validated.

#### 5.B Stop logic ablation (mandatory)

The current production code in `src/bull_call/stop.py` implements a
spot-based one-cross stop: arms the first time spot ≥ breakeven, fires on
the first tick where spot < breakeven. `strategy-review.md` §2.4 / §3.9
flags two structural problems: (a) **spot is the wrong reference** — the
spread's mark / executable credit is what the bot would realise on close;
(b) **single-cross trigger creates whipsaw** — a one-tick print under
breakeven and immediately back is enough to fire. The redesign in §4.8
proposes a mark-based hard stop with confirmation. Before live capital,
the ablation must compare:

| Variant | Trigger | Source |
|---|---|---|
| `no_stop` (control) | Hold to settlement; never close early. Mandatory baseline per `strategy-review.md` §6.4. | n/a |
| `spot_cross` (current) | Spot < breakeven, single-tick. | stop.py:55 |
| `spot_cross_with_buffer` | Spot < breakeven − buffer, single-tick (where buffer is a small fraction of width). | strategy-review.md §3.9 |
| `mark_based_hard_stop` (redesign default) | `executable_credit` ≤ `entry_debit × stop_pct_of_debit` continuously for `stop_confirm_sec`. | strategy-review.md §4.8 / §7 R25 |
| `mark_based_with_time_stop` | `mark_based_hard_stop` PLUS at `time_stop_et`, close if `executable_credit < entry_debit × time_stop_pct_of_debit`. | strategy-review.md §7 R26 |

Each variant must be evaluated **with and without** profit-taking
(`pt_enabled` true / false per §7 R24) — stops and PT interact and cannot
be ablated in isolation.

**Pass criterion**: a stop variant wins only if it beats `no_stop` on the
holdout on **expectancy AND drawdown AND max single-trade loss** — a stop
that improves drawdown but worsens expectancy must show that the
drawdown reduction is materially valuable (§6.6 invalidation criteria).
If `no_stop` is competitive, ship the simpler design.

**Implementation note for both ablations**: the harness from §1.5.2 must
expose the strike-selection objective AND the stop variant as first-class
config knobs that the ablation runner can toggle. Without that, ablation
results are not reproducible and the spec-freeze rule (§1.5.4) cannot be
enforced.

#### 5.C Profit-taking policy ablation (mandatory)

The current production code has **no profit-taking** — spreads are held
to settlement (or a stop fires). `strategy-review.md` §2.5 / §4.9 flags
this as a candidate weakness — long 0DTE spreads are highly exposed to
late-day gamma reversal — but also explicitly warns that the canonical
"close at 50% of max profit" rule is associated with **short-premium**
strategies (selling iron condors, strangles) where premium decay drives
expectancy. Long debit spreads have a different convexity profile: you
have already *paid* for the gamma, so leaving early gives away the
upside you paid for. The right target — 25%, 50%, 75% of max profit, or
trailed from a peak — is an **empirical question, not a settled rule**.

Before live capital, the ablation must compare:

| Variant | Trigger | Source |
|---|---|---|
| `no_pt` (control) | Hold to settlement / stop / time-stop. Mandatory baseline per §6.4. | n/a |
| `fixed_pt_at_50_max_profit` | Close MKT when `executable_credit ≥ entry_debit + 0.50 × (width − entry_debit)`. **Starting hypothesis only**; 25 / 50 / 65 / 75% should each be tested as separate cells. | strategy-review.md §4.9 / §7 R24 |
| `time_gated_pt` | PT only fires *after* `pt_arm_time_et` (e.g. 13:00 ET) — the rationale being that early-session PT closes give away the post-entry drift that motivated the trade in the first place. | strategy-review.md §4.9 |
| `trailing_pt_from_peak` | Track the peak `executable_credit` since entry; close when current `executable_credit` ≤ peak × `trail_giveback_pct` (starting hypothesis 0.50 — once 75% of max profit is reached, lock in 50%). | strategy-review.md §4.9 |

Each PT variant **must be evaluated jointly with each stop variant from
§5.B** — stops and PT cannot be ablated in isolation because they
interact mechanically (a stop that fires on a downward move can preempt
a PT that would have fired on a recovery, and vice-versa). This is a
deliberate interaction-test pair — not a Cartesian — driven by the
§5.B winner.

**Pass criterion**: a PT variant wins only if it beats `no_pt` on the
holdout on **expectancy AND drawdown AND time-underwater**. A PT rule
that improves drawdown but materially reduces expectancy is rejected
unless the drawdown reduction crosses the §6.6 acceptability threshold —
debit spreads have already paid for the gamma, so giving up upside is
expensive.

#### 5.D Rolling policy ablation (mandatory)

The current production code has **no rolling** — once a position is
opened, the only exits are the stop, time stop, hard close, settlement,
leg-out flatten, or data-outage flatten. Rolling (closing the current
spread and opening a fresh one farther in time or at different strikes)
is an architecturally larger change than the other three axes:

- It changes the **trade-count and exposure profile** within a session
  — interacts with sizing (R8 weekly loss cap, R9 monthly net-negative
  gate) because each roll is two round-trip commissions on top of the
  original entry, and a roll into a new debit increases the day's
  cumulative risk.
- Whether a roll is even *possible* depends on liquidity at the new
  strikes and on time-to-close (rolling at 14:30 ET into a still-0DTE
  spread leaves no room for the new spread to work).
- The candidate set is naturally smaller because the operational risk
  is higher; a positive expectancy result must clear a higher bar.

This axis should be ablated **last**, after §5.A / §5.B / §5.C have
produced their winners, because rolling decisions consume the
strike-selection / stop / PT machinery and a rolling rule that wins
against a weak control may lose against a strong one.

| Variant | Trigger | Source |
|---|---|---|
| `no_roll` (control) | Existing exits only. Mandatory baseline per §6.4. | n/a |
| `single_defensive_roll_with_confirmation` | One roll per session, at most. Triggered when `executable_credit ≤ entry_debit × roll_threshold_pct` (starting hypothesis 0.50) AND held for `roll_confirm_sec` (starting 60s). New strikes selected via the §5.A winner. **Hard limit**: not after `latest_roll_et` (e.g. 14:00 ET) — rolling late leaves no holding time for the new spread. | new-territory hypothesis |
| `mark_driven_salvage_roll` | Same trigger as above but rolls into an OTM debit spread sized so the **combined** entry + roll cost still fits inside R8/R9 caps. Goal: salvage the position rather than realise the loss. | new-territory hypothesis |
| `regime_gated_midday_roll` | A roll fires only if (a) the §5.A trigger above is met AND (b) the regime conditions that justified the original entry **still hold** (per the regime filters). If the regime that motivated entry has degraded, accept the loss instead of doubling down. | new-territory hypothesis |

Each rolling variant must report, in addition to the standard §6.3
metrics, **roll-frequency** (rolls per filled spread), **post-roll
expectancy** (P&L attributable to the rolled spread, separated from the
original), and **interaction with §5.B stop**: a stop that fires
post-roll on the new strikes vs the original strikes is reported
separately.

**Pass criterion**: a rolling variant wins only if it beats `no_roll`
on **expectancy AND max single-day cumulative loss AND roll-frequency
operational tractability**. Roll frequency below 1-per-week is fine;
roll frequency above ~2-per-week likely indicates the rolling rule is
papering over a deeper edge problem (the entry signal is weak, not the
exit). If rolling beats no-roll only by recovering occasional outliers,
the result must survive §6.5's bootstrap-CI test before adoption.

**Implementation note for §5.C and §5.D**: the harness from §1.5.2
must expose the PT variant AND the rolling variant as first-class
config knobs (matching §5.A / §5.B), and the four axes must be
toggleable independently per the §5.0 cardinality discipline.

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
