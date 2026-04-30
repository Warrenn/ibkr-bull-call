# STRATEGY-SPEC-v1

Status: frozen research spec for the first minimum viable backtesting cycle.

Purpose: define the exact first strategy and experiment assumptions to test.

This is not a production strategy spec. It is the first frozen research spec
used to answer:

1. Does the bullish continuation idea have standalone directional edge?
2. Is the bull call spread a better expression than simpler alternatives?

If this spec changes materially, create `STRATEGY-SPEC-v2.md` and treat all
holdout conclusions from `v1` as non-transferable.

## Setup Definition

Setup type:

- bullish intraday continuation

Working hypothesis:

- after a defined bullish confirmation event, the market has enough remaining
  same-session upward drift to justify further study of options-based
  expressions

Out of scope for `v1`:

- reversal setups
- event-driven special handling
- rolling
- profit-taking
- complex regime filters

## Signal Definition

Primary signal candidate:

- `D1`

Definition:

- evaluate one bullish continuation signal per eligible session
- the exact confirmation logic must be encoded in
  `research/specs/directional-edge-v1.yaml`
- if no confirmation occurs that day, record `entered = false` and
  `skip_reason = no_signal`

Rules:

- one signal opportunity per session
- no same-day re-entry
- no discretionary overrides

## Expression Candidates

The first expression comparison must test:

- `E1`: bull call spread
- `E2`: long call
- `E3`: ES or MES directional proxy

Optional:

- `E4`: cash / no-trade baseline

## Bull Call Spread Baseline

The bull call spread baseline for `v1` is intentionally simple.

Long side:

- same-day expiry SPX call

Short side:

- same-day expiry SPX call above the long strike

Selection rule:

- `S1`: current widest-valid rule from the live bot, preserved initially as
  the control candidate

Code revision anchor:

- `S1` is defined as the strike-selection logic in `bull_call.strikes` at
  the commit recorded as `code_revision` in each run's metadata. Any change
  to that module's selection objective invalidates prior `v1` evidence and
  requires `STRATEGY-SPEC-v2`.

This spec does not declare a replacement strike rule yet. Those belong in the
later strike-selection comparison phase.

## Sizing

For `v1` use a fixed sizing convention across all expression candidates so
post-cost results are comparable across `E1`, `E2`, and `E3`.

Convention:

- one contract per signal for `E1` (bull call spread)
- one contract per signal for `E2` (long call)
- one contract per signal for `E3` (ES or MES proxy), with proxy notional
  size frozen in `research/specs/expression-comparison-v1.yaml` before
  first run

Reason:

- the first comparison should answer whether the view has edge and which
  expression is best per unit of structural exposure, not which expression
  benefits most from leverage
- variable sizing is a later optimization, gated on a surviving expression

## Exit Policy For First Cycle

Use separate exit policies by phase:

- directional-edge phase:
  - not applicable to options structure
- expression comparison phase:
  - use a simple, comparable end-of-window exit policy across all candidates
- do not introduce stop loss, PT, or rolling into the first expression test

Reason:

- the first comparison should answer whether the view has edge and whether the
  spread is worth optimizing at all

## Overlays

For `v1` no overlays are active.

Out of scope for the first cycle:

- profit-taking
- rolling
- monthly net-negative capital gate
- regime filters
- event filters

Reason:

- overlays change which trades exist, not just which trades win or lose
- the first cycle must answer whether the underlying view has edge in the
  raw, no-overlay case before any overlay is justified
- the program doc requires overlay-on / overlay-off reporting in later
  phases; for `v1` both modes are equivalent because no overlay is defined

## Costs

All results must be post-cost.

Baseline cost model for `v1`:

- commissions and fees included
- slippage included
- one baseline scenario
- one 2x slippage stress scenario

The exact parameter values belong in the YAML experiment specs and cost model
versioning.

## Data Policy

Use frozen datasets only.

Minimum acceptable input tier for `v1`:

- SPX 1-minute spot
- ES or MES 1-minute series
- SPX 0DTE call-chain snapshots sufficient for entry-window and exit-window
  analysis
- trading calendar with half-day awareness

Known limitation:

- this data tier is sufficient for directional-edge and initial expression
  comparison, but not final stop/rolling conclusions

## Dataset Split Policy

Use this minimum split:

- `train`: oldest ~60%
- `validation`: next ~20%
- `holdout`: most recent ~20%

Rules:

- the percentages compute against the actual frozen `dataset-v1` date
  range, not against an arbitrary calendar window
- the exact `holdout_start_utc` and `holdout_end_utc` timestamps must be
  pinned in `research/data/manifest.json` once the `spx_spot_intraday`
  and `spx_0dte_call_chain` datasets land; until that pin exists no run
  counts as a `v1` holdout evaluation
- shape candidates on `train`
- select candidates on `validation`
- evaluate the frozen spec once on `holdout`
- any rule change after the holdout has been touched creates `v2` and
  invalidates prior holdout conclusions

## Required Outputs

Every run under this spec must produce:

- per-trade ledger
- summary report
- dataset version
- code revision
- strategy spec ID: `v1`

## Pre-Run Checklist

This spec is frozen in shape, but several measurable inputs must be pinned
in their own artifacts before any run counts as `v1` evidence. A run that
touches one of these while the corresponding artifact is still TODO must be
marked `status: PRE_FREEZE` in its report and excluded from any decision
rule below.

Required pins before first directional-edge run (`Phase 1`):

- `research/specs/directional-edge-v1.yaml`
  - `signal.implementation_todo`: confirmation logic, earliest signal
    time, latest signal time
  - `horizon.implementation_todo`: end-of-window timestamp
- `research/data/manifest.json`
  - `spx_spot_intraday`: source, vendor, license, date range, checksum
  - `es_or_mes_intraday`: source, vendor, license, date range, checksum
  - `holdout_start_utc`, `holdout_end_utc` derived from the above

Required pins before first expression-comparison run (`Phase 2`):

- `research/specs/expression-comparison-v1.yaml`
  - `E2.implementation_todo`: long-call strike-selection rule
  - `E3.implementation_todo`: ES or MES proxy sizing convention
- `research/data/manifest.json`
  - `spx_0dte_call_chain`: source, vendor, license, date range, checksum
- `research/models/costs.py` exists and pins
  - commission table
  - baseline and 2x slippage scenarios
  - `cost_model_version: cost-model-v1`

## Decision Rules

This spec advances only if all are true:

- standalone directional edge is positive enough to justify further testing
- bull call spread is competitive with simpler expressions after costs
- results survive baseline realism checks
- results are reproducible

If any one is false, do not promote `v1` to later optimization phases.

## Explicit Non-Claims

This spec does not claim:

- live-capital readiness
- validated stop logic
- validated profit-taking
- validated rolling
- validated regime filters

Those require later specs and later phases.
