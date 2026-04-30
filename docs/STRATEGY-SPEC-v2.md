# STRATEGY-SPEC-v2

Status: frozen research spec for the second minimum viable backtesting cycle.

Purpose: re-test the bullish intraday continuation hypothesis with a
**tightened threshold (0.50% instead of 0.25%) and an explicit event-day
exclusion filter** after v1 returned `EDGE_INCONCLUSIVE` and the v1 sweep
returned `NO_EDGE` across all viable threshold/time combinations.

This spec advances if and only if:

1. v2 evaluated on the **validation window** (one-shot) confirms the
   train-window finding (mean > 0, t > 1.5, CI not catastrophically
   below TRAIN mean).
2. v2 evaluated on the **holdout window** (one-shot, after validation
   confirms) returns `mean > 0, t ≥ 2, CI excludes zero`.

If either step fails, the bullish-continuation hypothesis is **killed**
and the project either pivots to a different signal family (v3) or
stops.

## What changed from v1

| Element | v1 | v2 | Why |
|---|---|---|---|
| `signal_threshold` | 0.0025 (0.25%) | **0.0050 (0.50%)** | v2 train sweep found 0.50%/10:30 was the only candidate where event-filtered data showed t ≥ 2 with positive per-year stability |
| Event filter | none | **FOMC + CPI + NFP + OPEX excluded** | v1 train sweep with event filter showed the 3 dropped event days for 0.50%/10:30 averaged ~-0.65% per day — the worst tail of the distribution |
| Required inputs | + `event_calendar` | | Pinned in `research/data/manifest.json` (sha256:030c9004...) |
| Per-year stability | 2024 was negative | **positive every year** in event-filtered TRAIN | by_year_min flipped from +0.031% to +0.088% |

Everything else (setup type, signal time at 10:30 ET, end of window at
15:55 ET, sizing, overlays, no costs in Phase 1) is **unchanged from v1**.

## Setup Definition

Setup type:

- bullish intraday continuation, **non-event days only**

Working hypothesis (refined from v1):

- after a defined bullish confirmation event of meaningful magnitude
  (≥ 0.50% by 10:30 ET, on a non-event day), the market has enough
  remaining same-session upward drift to justify further study of
  options-based expressions

Out of scope for `v2`:

- reversal setups
- rolling
- profit-taking
- regime filters beyond the event calendar
- complex multi-asset confirmation

## Signal Definition

Primary signal candidate:

- `D2`

Definition:

- evaluate one bullish continuation signal per eligible session
  (NYSE full-trading day, NOT in the event calendar)
- the exact confirmation logic is encoded in
  `research/specs/directional-edge-v2.yaml`
- if no confirmation occurs that day, record `entered = false` and
  `skip_reason = no_signal`
- if the day is excluded by the event filter, record `entered = false`
  and `skip_reason = event_day`

Rules:

- one signal opportunity per session
- no same-day re-entry
- no discretionary overrides

## Code Revision Anchor

- the runner is `research/scripts/run_directional_edge_v1.py`
  (extended with `--window-start`, `--window-end`, `--event-calendar`
  flags; v1 evidence reproducibility preserved when those flags are
  unused). The exact code revision is recorded in each run's
  metadata. Any change to `extract_intraday_prices`,
  `compute_ledger`, or `aggregate_metrics` invalidates prior v2
  evidence and requires `STRATEGY-SPEC-v3`.

## Sizing

Same as v1:

- one contract per signal for `E1` (bull call spread)
- one contract per signal for `E2` (long call)
- one contract per signal for `E3` (ES or MES proxy)

## Exit Policy For First Cycle

Same as v1: simple end-of-window exit at 15:55 ET. Stop loss / PT /
rolling are out of scope.

## Overlays

Same as v1: none for the directional-edge phase.

## Costs

Same as v1: post-cost reporting required, but Phase 1 (directional-edge)
itself does not use options structure so commissions/slippage do not
apply yet. Cost model attaches in Phase 2 (expression comparison).

## Data Policy

Use frozen datasets only.

Required inputs (all pinned in `research/data/manifest.json`):

- `es_intraday` (Databento OHLCV-1m, sha256:cf4567cd...)
- `trading_calendar` (pandas_market_calendars, sha256:486488b9...)
- `event_calendar` **(NEW for v2)** — Federal Reserve FOMC + BLS CPI +
  computed NFP + computed OPEX, sha256:030c9004...

Known limitation:

- still ES-only; SPX cross-validation is deferred to a follow-up if
  v2 holdout passes

## Dataset Split Policy

Same 60/20/20 split pinned in `research/data/manifest.json` (split_policy
section). Concrete dates:

- `train`: 2023-05-01 → 2025-02-14 (439 trading days)
- `validation`: 2025-02-18 → 2025-09-22 (146 days)
- `holdout`: 2025-09-23 → 2026-04-29 (147 days)

Rules:

- v2 was **shaped on TRAIN** with the event filter (PR #57)
- v2 will be **evaluated once on VALIDATION** under this spec
- v2 will be **evaluated once on HOLDOUT** if and only if validation
  confirms (per `decision_rules.validation_continue_if` in the YAML)
- the v1 holdout slot is consumed (PR #55 ran v1 against full data
  before split was pinned); v2 has its own untouched slot

## Pre-Run Checklist

For the validation run:

- v2 spec YAML pinned (this commit) — done
- 60/20/20 split dates pinned in manifest (PR #57) — done
- event_calendar pinned in manifest (PR #57) — done
- ES + trading_calendar pinned in manifest — done

For the holdout run (only if validation confirms):

- frozen v2 spec is unchanged from validation run
- code_revision is unchanged from validation run

## Decision Rules

### After validation run

`validation_continue_if`:

- `validation_mean_forward_return > 0`
- `validation_t_stat > 1.5` (relaxed from 2.0 because validation has
  fewer trades than train)
- `validation_ci_low_95 > -0.5 × train_mean` (CI lower bound not
  catastrophically below TRAIN result)
- result not concentrated in a single 2-week period

`validation_kill_if`:

- `validation_mean_forward_return <= 0`
- `validation_t_stat < 0` (negative t — clearly no edge)
- `validation_ci` is statistically incompatible with `train_ci`

### After holdout run

`holdout_continue_if`:

- `holdout_mean_forward_return > 0`
- `holdout_t_stat >= 2.0`
- `holdout_ci_low_95 > 0` (CI excludes zero — confirmed edge)

`holdout_kill_if`:

- `holdout_mean_forward_return <= 0`
- `holdout_t_stat < 1.5`

## Explicit Non-Claims

This spec does not claim:

- live-capital readiness
- validated stop logic
- validated profit-taking
- validated rolling
- validated regime filters beyond the event calendar
- v2 holds up on SPX (cross-validation deferred)

Those require later specs and later phases.

## Provenance

- **parent**: `STRATEGY-SPEC-v1` (`docs/STRATEGY-SPEC-v1.md`); v1 was
  killed at the falsification step.
- **train evidence**: `research/reports/directional-edge-sweep-train-v2events.md`
  — TRAIN-window sweep showing 0.50%/10:30 event-filtered as the
  only candidate with t ≥ 2.
- **validation evidence**: TBD (this spec drives that run).
- **holdout evidence**: TBD (gated on validation).
