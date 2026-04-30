# Minimum Viable Backtesting Program

This document turns the research intent from
[`strategy-review.md`](./strategy-review.md) and
[`live-capital-go-no-go.md`](./live-capital-go-no-go.md) into an executable
program.

The goal is not to build a giant research platform. The goal is to build the
smallest backtesting program that can answer the important questions honestly,
kill weak ideas quickly, and promote only credible candidates to paper
trading.

This file is intended to be the **single execution document** for the first
backtesting build. If a decision, artifact, or phase is not described here, it
is out of scope for the first iteration.

## Purpose

This program must answer five questions:

1. Does the underlying directional idea have standalone edge?
2. Is the bull call spread a better expression of that view than simpler
   alternatives?
3. Which strike-selection rule is best under realistic costs?
4. Which exit logic is best under realistic costs?
5. Does any surviving strategy remain attractive after stressed execution
   assumptions?

If the program cannot answer those questions with reproducible evidence, it is
not sufficient.

## What This Program Must Decide

Each stage must end in one of three outcomes:

- `CONTINUE`
- `REDESIGN`
- `KILL`

This is not a scorecard whose goal is to keep every idea alive. The purpose is
to remove weak ideas quickly and keep only candidates that survive realistic
testing.

## Ground Rules

- Every reported result is **post-cost**.
- Every run is tied to a frozen dataset version, code revision, and strategy
  spec.
- Every candidate is compared against a simpler control.
- Every result is reported both:
  - without overlays
  - with overlays, when overlays exist
- Any candidate that fails a phase is removed, not endlessly tuned.
- Any candidate promoted to the next phase must beat a simpler control, not
  just the previous draft.

## Research Methodology

Use a **stage-gated methodology**. Each phase answers exactly one question,
uses a frozen candidate set, and has explicit pass/fail rules.

The order matters:

1. prove directional edge first
2. compare expressions second
3. optimize spread construction third
4. optimize exit logic fourth
5. test profit-taking fifth
6. apply stress last

Do not start with profit-taking, rolling, or overlays. Those are late-stage
 refinements, not foundations.

## Experiment Control

Every run must record:

- `strategy_spec_id`
- `dataset_version`
- `code_revision`
- `cost_model_version`
- `overlay_mode`
- `run_timestamp`
- `seed`, if any random process is involved

No result counts as evidence unless rerunning the same inputs produces the same
ledger.

## Non-Goals

Do not build these in the first iteration:

- distributed compute
- exhaustive parameter sweeps
- auto-tuning / optimization loops
- complex dashboards
- full production-failure simulation
- rolling logic before the base strategy survives

## Required Inputs

The minimum useful data inputs are:

- SPX intraday spot data
- ES or MES intraday data for directional proxy analysis
- SPX 0DTE call-chain snapshots with bid/ask
- trading-calendar tags:
  - date
  - full day vs half day
  - major event day if available

The minimum data contract is:

- immutable data files
- one manifest file with source, date range, and checksum
- one named dataset version, e.g. `dataset-v1`

## Concrete Test Data Requirements

There are two acceptable data tiers for this program.

### Tier 1: Minimum viable data

Enough to answer the first three questions:

- SPX intraday spot series
- ES or MES intraday series
- SPX 0DTE call-chain snapshots with bid/ask
- trading calendar:
  - trading day flag
  - half-day flag
  - session open / close

Minimum useful resolution:

- SPX spot: 1-minute
- ES or MES: 1-minute
- option chain: entry-window and exit-window snapshot coverage

Minimum useful history:

- 24 months minimum
- 36 months preferred
- reserve the most recent 6 months as untouched holdout if possible

Tier 1 is enough to begin:

- directional-edge testing
- expression comparison
- strike-selection comparison

Tier 1 is **not enough** to settle stop logic, rolling, or fine-grained exit
economics credibly.

### Tier 2: Promotion-grade data

Needed before you trust stop logic, execution claims, or live-readiness
conclusions.

Required:

- SPX spot: 1-second or tick
- ES: 1-second or tick
- SPX 0DTE option-chain snapshots at roughly 1-5 second cadence
- bid/ask for both legs throughout the session
- event calendar:
  - FOMC
  - CPI
  - NFP
  - OPEX
  - half-days
- authoritative settlement source or a documented reconciliation source

Tier 2 is needed for:

- realistic stop testing
- profit-taking evaluation
- rolling evaluation
- stronger slippage modeling
- promotion toward paper trading

## Required Data Schema

### Spot / futures rows

- `ts_utc`
- `symbol`
- `price`
- optional `bid`
- optional `ask`

### Option-chain rows

- `ts_utc`
- `underlying`
- `expiry`
- `right`
- `strike`
- `bid`
- `ask`
- optional `bid_size`
- optional `ask_size`
- optional `contract_id` or `conid`

### Calendar rows

- `date`
- `is_trading_day`
- `is_half_day`
- `session_open_utc`
- `session_close_utc`
- optional `event_tags`

## Data Quality Checks

Before trusting any result, verify:

- timestamps are normalized to UTC
- timestamps are monotonic within each source
- no duplicate contract rows exist for the same timestamp/strike/right/expiry
- crossed or locked markets are handled explicitly
- missing quotes are counted and reported
- delayed data is flagged
- contract identity is consistent enough to isolate SPXW-like same-day options
- known data limitations are recorded in the manifest

## Required Outputs

Every run must produce:

- a per-trade ledger
- a summary report
- the strategy spec identifier
- the dataset identifier
- the code revision

Minimum ledger columns:

- `date`
- `symbol`
- `signal_ts`
- `entered`
- `skip_reason`
- `long_strike`
- `short_strike`
- `entry_debit`
- `exit_kind`
- `exit_value`
- `gross_pnl`
- `fees`
- `slippage`
- `net_pnl`

## Required Metrics

For every candidate run, report:

- trade count
- entered vs skipped count
- win rate
- average win
- average loss
- total net pnl
- net expectancy per trade
- max drawdown in dollars
- max drawdown as percent
- worst month
- max consecutive losers
- average slippage burden
- exit-type distribution

For expression comparisons also report:

- return per unit drawdown
- fill feasibility / skipped-trade rate
- whether the simpler alternative is equal or better

## Minimal System Design

Build only these pieces:

### 1. Dataset layer

Responsibilities:

- load immutable market data
- expose one dataset version at a time
- refuse to run if required inputs are missing

Suggested paths:

- `research/data/manifest.json`
- `research/data/dataset-v1/...`

### 2. Strategy spec runner

Responsibilities:

- read one frozen strategy spec
- run it over one frozen dataset
- produce one reproducible ledger

Suggested paths:

- `research/specs/*.yaml`
- `research/backtest.py`

### 3. Cost model

Minimum required paths:

- entry limit order path
- protective exit path
- hold-to-settlement path

Minimum toggles:

- baseline slippage
- 2x slippage stress
- baseline commission
- higher commission stress
- overlays on/off

Minimum standard:

- no gross-P&L-only reporting
- every summary number is net of fees and modeled slippage

Suggested paths:

- `research/models/costs.py`

### 4. Candidate comparison harness

Responsibilities:

- run several named candidates under the same dataset and cost assumptions
- emit a side-by-side summary

Suggested paths:

- `research/compare.py`
- `research/reports/*.md`

### 5. Report generator

Required metrics:

- trade count
- win rate
- expectancy net of costs
- total net pnl
- max drawdown
- worst month
- average slippage burden
- benchmark-relative comparison

## Execution Order

Run phases in this exact order.

Do not skip ahead.

## Phase 0: Freeze Inputs

### Question

Can we make results reproducible enough to trust at all?

### Required artifacts

- `research/data/manifest.json`
- `docs/STRATEGY-SPEC-v1.md`

### Minimum done criteria

- one dataset version exists
- one frozen strategy spec exists
- rerunning the same config produces the same ledger

### Required split policy

At minimum, keep these partitions:

- `train`: oldest ~60%
- `validation`: next ~20%
- `holdout`: most recent ~20%

Rules:

- use `train` to shape candidates
- use `validation` to select among candidates
- touch `holdout` once per frozen spec
- any meaningful rule change creates a new spec version and invalidates prior
  holdout conclusions

### Stop condition

- if the data or spec is not frozen, no later result counts as evidence

## Phase 1: Directional Edge Only

### Question

Does bullish intraday continuation have standalone edge before options
structure is involved?

### Method

Use SPX or ES/MES proxy returns from signal time to exit window.

Required computations:

- mean forward return
- median forward return
- hit rate
- left-tail loss
- regime slices if available

### Candidate set

- `D1`: baseline continuation signal
- optional `D2`: tighter confirmation
- optional `D3`: regime-filtered continuation

### Required artifact

- `research/reports/directional-edge-v1.md`

### Minimum done criteria

- one signal definition tested over the full dataset
- return distribution reported
- regime slices reported if available
- one explicit conclusion:
  - `EDGE PRESENT`
  - `NO EDGE`

### Stop condition

- if no standalone directional edge exists, stop the program

## Phase 2: Expression Comparison

### Question

Is the bull call spread the right expression of the view?

### Required candidate set

- `E1`: bull call spread
- `E2`: long call
- `E3`: ES/MES proxy
- optional `E4`: no-trade / cash baseline

### Required artifact

- `research/reports/expression-comparison-v1.md`

### Minimum done criteria

- all candidates run on identical dates and signal definitions
- all candidates reported net of costs
- one explicit ranking exists

Required comparisons:

- bull call spread vs long call
- bull call spread vs ES/MES proxy
- optional bull call spread vs cash / no-trade baseline

### Stop condition

- if the bull call spread is not clearly competitive on post-cost,
  risk-adjusted terms, stop optimizing it

## Phase 3: Strike Selection Comparison

### Question

Which strike-selection rule is best?

### Required candidate set

- `S1`: current widest-valid rule
- `S2`: target debit-as-fraction-of-width rule
- `S3`: target delta-band rule

### Required artifact

- `research/reports/strike-selection-v1.md`

### Minimum done criteria

- all three candidates tested under same signal and cost model
- one winner selected
- the winner beats `S1` or `S1` remains as control

Required conclusion:

- either keep `S1`
- or promote one explicit replacement candidate

### Stop condition

- if no strike rule produces acceptable post-cost behavior, stop

## Phase 4: Exit Logic Comparison

### Question

Which exit logic is best under realistic costs?

### Required candidate set

- `T0`: hold to settlement
- `T1`: current breakeven spot-cross stop
- `T2`: buffered and confirmed spot-cross stop
- `T3`: mark-aware or hybrid stop, if data supports it

### Required artifact

- `research/reports/exit-logic-v1.md`

### Minimum done criteria

- each exit tested on the same base candidate
- results reported post-cost
- one winner selected or no-stop retained

Required comparison set:

- no stop
- current stop
- one improved stop candidate

### Stop condition

- if every active exit worsens results versus simpler alternatives, remove them

## Phase 5: Profit-Taking Comparison

### Question

Does profit-taking help debit-spread behavior, or does it just cut upside?

### Required candidate set

- `P0`: no profit-taking
- `P1`: fixed threshold profit-taking
- `P2`: trailing or time-gated profit-taking

### Required artifact

- `research/reports/profit-taking-v1.md`

### Minimum done criteria

- `P0` included as control
- one explicit conclusion:
  - keep no PT
  - adopt PT candidate

### Stop condition

- if PT only improves appearance in-sample or under overlays, reject it

## Phase 6: Stress Test

### Question

Does the surviving candidate remain attractive after realistic execution
degradation?

### Required scenarios

- baseline costs
- 2x slippage
- higher commission assumption
- overlays off
- overlays on

Optional later:

- 3x slippage
- worst-path adverse-fill

### Required artifact

- `research/reports/stress-v1.md`

### Minimum done criteria

- expectancy, drawdown, and trade count reported under each stress
- one explicit conclusion:
  - `ROBUST ENOUGH FOR PAPER`
  - `NOT ROBUST`

### Stop condition

- if the strategy collapses under modest stress, stop

## Candidate Matrix

Use this initial matrix. Keep it small.

### Signal candidates

- `D1`: bullish continuation baseline
- `D2`: bullish continuation with stronger confirmation

### Expression candidates

- `E1`: bull call spread
- `E2`: long call
- `E3`: ES/MES proxy

### Strike candidates

- `S1`: widest valid
- `S2`: target debit/width band
- `S3`: target delta band

### Exit candidates

- `T0`: hold to settlement
- `T1`: current stop
- `T2`: buffered + confirmed stop

### Profit-taking candidates

- `P0`: none
- `P1`: fixed threshold

### Rolling candidates

Do not include in MVP unless the strategy already survives Phases 1–6.

## Run Structure

For each report, include:

- question being answered
- strategy candidates included
- dataset version
- cost assumptions
- overlays on/off
- result table
- decision
- next action

## Immediate Artifacts To Create

Before writing a full backtester, create these files:

- `docs/STRATEGY-SPEC-v1.md`
- `research/data/manifest.json`
- `research/specs/directional-edge-v1.yaml`
- `research/specs/expression-comparison-v1.yaml`

Those files should pin:

- the directional signal definition
- the entry and exit window
- the candidate list
- the cost assumptions
- the train / validation / holdout split

## Immediate Build Order

Implement only in this sequence:

1. dataset loader
2. directional-edge runner
3. expression comparison runner
4. baseline bull-call ledger generator
5. strike-selection comparison
6. exit comparison
7. stress reruns

If Phase 1 or Phase 2 fails, stop before building the rest.

## Promotion Rule

A strategy may be promoted to paper trading only if:

- directional edge exists
- the chosen expression beats simpler alternatives
- the chosen strike rule beats or matches the baseline honestly
- the chosen exit logic survives post-cost comparison
- the candidate survives 2x slippage stress
- the candidate's results are reproducible

If any one of those is missing, the strategy is not ready for paper trading.

## First Deliverables

The first two deliverables to build are:

1. `research/reports/directional-edge-v1.md`
2. `research/reports/expression-comparison-v1.md`

That is the highest-value starting point because it answers:

- whether there is any edge to express
- whether the bull call spread deserves optimization at all

If either fails, stop before building more complexity.

## Success Criterion

This MVP succeeds if it can do four things:

1. run one frozen strategy spec over one frozen dataset reproducibly
2. compare several named candidates on net-of-cost results
3. stress those candidates under worse assumptions
4. produce a decision to continue, redesign, or kill

If it does that, it is worth building.

## The First Concrete Question To Execute

Begin here:

**Can a frozen bullish-continuation signal produce positive forward
directional expectancy, and if so, does the bull call spread beat long calls
and ES/MES as the expression of that view after realistic costs?**

That single question should drive the first implementation cycle.
