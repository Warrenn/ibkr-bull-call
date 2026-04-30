# Directional Edge v4 — Phase 1 Falsification Test

**Simple verdict (mean > 0?): `EDGE_PRESENT`**

**Nuanced verdict: `EDGE_INCONCLUSIVE — positive mean but not significant`**

## Provenance

- strategy_spec_id: `v4`
- spec_id: `directional-edge-v4`
- dataset_version: `dataset-v1`
- code_revision: `4814195b075c0d3adc10cef1a3bf284e92dcd707`
- run_timestamp_utc: 2026-04-30T18:33:14.937356+00:00
- ES dataset: `es_intraday.parquet` sha256:`c4d8b3d2d75431b71a7a4635969cf8de8cec362704aa832242f79183019f792d`
- Calendar dataset: `trading_calendar.parquet` sha256:`96803a2e968723b48b4625922a4104bbe5b529bbb3d7eaab8d2cf6929d2b4140`
- Actual date range used: 2023-06-07 → 2026-01-12

## Setup

- underlying: es_front_month
- session_open_time_et: 09:30
- signal_time_et: 10:00 (single fixed)
- end_of_window_time_et: 15:55
- signal_threshold: 0.1000%

## Results

| Metric | Value |
|---|---|
| total_sessions | 104 |
| entered | 33 (31.7% of sessions) |
| skipped (no_signal) | 71 |
| mean_forward_return | 0.1514% |
| median_forward_return | 0.1686% |
| hit_rate (forward > 0) | 66.7% |
| left_tail_p05 | -0.5186% |

## Verdict Rationale

`mean_forward_return = 0.1514%` > 0 → **EDGE_PRESENT** (simple verdict)

## Statistical Significance

Honest read of the simple verdict above: a positive point estimate is
not the same as a real edge. With a small sample (~150 trades) and
high per-trade noise (~1% std), random variation can produce a
spurious-but-positive mean. Three numbers below decide whether the
edge is statistically distinguishable from zero:

| Statistic | Value |
|---|---|
| sample size | 33 |
| std (per trade) | 0.4685% |
| sem (mean's std error) | 0.0816% |
| **t-stat** vs zero | **1.86** |
| **p-value** (two-tailed) | **0.073** |
| 95% CI on mean | [-0.0148%, 0.3175%] |

**Interpretation rule:** `|t-stat| ≥ 2` (≈ p < 0.05) is the conventional
threshold for rejecting "the true mean is zero". A `t-stat < 2` means the data is *consistent*
with zero — the simple `mean > 0` verdict is unreliable.

## Per-Year Breakdown (Regime Concentration Check)

Spec rule `result_is_not_concentrated_in_one_small_regime_cluster`:
if the entire mean is driven by a single year and other years are
flat or negative, the "edge" is regime-dependent and not a
stable underlying property.

| year | n | mean | std | hit_rate |
|---|---|---|---|---|
| 2023 | 19 | 0.1253% | 0.5241% | 68.4% |
| 2024 | 12 | 0.2612% | 0.3791% | 75.0% |
| 2025 | 1 | -0.2276% | nan% | 0.0% |
| 2026 | 1 | -0.2929% | nan% | 0.0% |

## Decision Rules (per spec)

Continue if:
- standalone_directional_expectancy_is_positive
- result_is_not_concentrated_in_one_small_regime_cluster
- result_is_reproducible

Kill if:
- standalone_directional_expectancy_is_near_zero_or_negative
- behavior_is_obviously_regime_fragile_without_a_salvageable_base_signal

## Caveats

- Underlying is ES front-month continuous, not SPX (SPX 1m TBD per
  `docs/data-acquisition-decision.md` Path A; cross-validation
  against SPX is a follow-up if `EDGE_PRESENT`).
- Regime slices beyond per-year not computed — VIX not in the
  manifest yet.
- No transaction costs (Phase 1 evaluates the underlying view, not
  the trade — costs come in Phase 2 expression comparison).
- Reproducibility: same code_revision + dataset_version + spec_id
  must produce a byte-identical ledger CSV.