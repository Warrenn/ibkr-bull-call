# STRATEGY-SPEC-v4

Status: KILLED 2026-04-30 — validation produced n=1 trade, lost
money. The 4-dimensional candidate's regime is too narrow to
generalize out-of-sample.

Purpose: test whether the v4-combined TRAIN sweep's
mean-reversion candidate (0.10% threshold @ 10:00 ET, low-VIX days,
bonds-up days, fade direction) was a real edge or a
multiple-comparison artifact.

Outcome: confirmed as artifact.

## Pivot from v1-v3

This was the first spec where we changed direction (from continuation
to FADE). v1, v2, v3 all assumed bullish-continuation; v4 tested
mean reversion as the primary hypothesis. The pivot was justified
by the v4-combined TRAIN sweep finding (0.10%/10:00/lowVIX/bondsUp
showed n=31, t=-2.10, p=0.044, EDGE_PRESENT_MEAN_REVERSION).

## Why v4 didn't survive

The validation window (2025-02-18 → 2025-09-22, 146 trading days)
coincided with the **April 2025 tariff turmoil** that pushed VIX to
52. Most of the window had prior-day VIX well above 14.84 (the
TRAIN-pinned threshold for "low VIX"). The v4 candidate requires:

- Event filter (drops ~18% of days)
- Low-VIX (drops most of validation period — VIX was elevated
  almost continuously)
- Bonds-up (drops ~50% of remaining days)
- Threshold ≥ 0.10% (drops days with no early-morning move)
- Signal time exactly 10:00 ET

After all five filters: **1 day** fired across the 7-month
validation window. That one day was a fade LOSS (-0.23%).

Per v4's own decision rules:

- `validation_continue_if` requires mean > 0 (fade direction): FAIL
- `validation_kill_if.validation_mean_forward_return <= 0`: TRIGGERED
- The "no single day > 60%" rule fails trivially: n=1 means 100%

## What this tells us about the broader research

Four specs, four failures, on dataset-v1:

- v1 (continuation, no filters): borderline flat (t=0.88)
- v2 (continuation + event filter): train looked good but validation
  was 100% driven by a single April 2025 outlier
- v3 (continuation + event + high-VIX): identical to v2 (validation
  period was high-VIX so the gate was a no-op)
- v4 (FADE + event + low-VIX + bonds-up): regime too narrow to
  generalize; n=1 in validation

**The pattern across all four**: when we add filters to find a
TRAIN-significant candidate, those filters define a regime that
either (a) is the same as the unfiltered set during validation
(v3), (b) is dominated by single outliers in validation (v2), or
(c) doesn't occur in validation at all (v4).

This is the textbook description of overfitting in a small-data
research project. The v3 and v4 specs each added 1-2 dimensions of
filtering on top of v1; each yielded TRAIN candidates with t ≥ 2;
each failed validation in a different way.

## Multiple-comparison context

The v4 candidate emerged from a 35-trial sweep. Bonferroni-adjusted
TRAIN p-value was ~1.5. The validation result fully aligns with a
"random-trial-of-35-found-one-by-chance" interpretation.

## Holdout slot

Holdout slot is **preserved** (never touched). v4 fails validation
clearly; the spec rules forbid running on holdout when validation
fails.

## Lessons for any v5 or successor

1. The data does NOT support a directional edge at v1-v4
   resolutions (5h holding window from morning signal to close)
   regardless of which combination of filters is tested.
2. Going to higher dimensions (4+ filters) creates regimes too
   narrow to generalize. Future specs must justify each filter
   dimension a priori, not after the fact.
3. The 2024-2025 data range had extreme regime variation (VIX
   spanned 11.86 to 52.33). Any candidate filter sensitive to
   regime needs to test multiple full regime cycles, not pick the
   one that worked on TRAIN.
4. The honest read after v1-v4 is: **the bullish/bearish
   intraday continuation/reversion hypothesis is not extractable
   from this data with the methods we have.** A v5 must be a
   genuinely different research framing — different timeframe,
   different asset, different observation window — not "v4 with
   one more parameter."

## Provenance

- **parent**: STRATEGY-SPEC-v3 (PR #60).
- **train evidence**:
  `research/reports/v4-combined-train.md` — the candidate emerged
  from a 35-trial sweep (event + VIX × bonds × thresholds × times).
- **validation evidence**:
  `research/reports/directional-edge-v4-validation.md` — n=1, lost
  money, KILLED.
- **holdout evidence**: NEVER RUN.

## Explicit Non-Claims

This spec does not claim:

- v4 has any edge (validation proves it does not at n=1)
- The mean-reversion hypothesis can be salvaged with more filters
  (the data does not support that direction either)
- v5+ tuning of the same general approach will work (four failures
  is enough evidence to abandon this approach)
- Live-capital readiness (NEVER)

## Total spend across v1-v4

- Databento: $7.70 (from $125 free credit; balance ~$117 remaining)
- AWS: ~$0.01 of S3 storage
- Out-of-pocket: $0
- Hours: ~50 hours of research and tooling

The falsification framework worked. We learned that the strategy
hypothesis as scoped doesn't have edge in this data, for ~$7.70 of
real spend. That's exactly what the framework is designed to do.
