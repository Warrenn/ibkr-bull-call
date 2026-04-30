# Directional Edge v2 — Phase 1 Falsification Test

**Simple verdict (mean > 0?): `EDGE_PRESENT`**

**Nuanced verdict: `EDGE_INCONCLUSIVE — positive mean but not significant`**

## Provenance

- strategy_spec_id: `v2`
- spec_id: `directional-edge-v2`
- dataset_version: `dataset-v1`
- code_revision: `74e8d1af3c7bc1a9321539024ef97ffc8e9b2dc7`
- run_timestamp_utc: 2026-04-30T18:43:14.049773+00:00
- ES dataset: `es_intraday.parquet` sha256:`c4d8b3d2d75431b71a7a4635969cf8de8cec362704aa832242f79183019f792d`
- Calendar dataset: `trading_calendar.parquet` sha256:`96803a2e968723b48b4625922a4104bbe5b529bbb3d7eaab8d2cf6929d2b4140`
- Actual date range used: 2025-04-29 → 2026-04-28

## Setup

- underlying: es_front_month
- session_open_time_et: 09:30
- signal_time_et: 10:30 (single fixed)
- end_of_window_time_et: 15:55
- signal_threshold: 0.5000%

## Results

| Metric | Value |
|---|---|
| total_sessions | 207 |
| entered | 13 (6.3% of sessions) |
| skipped (no_signal) | 194 |
| mean_forward_return | 0.2194% |
| median_forward_return | 0.2129% |
| hit_rate (forward > 0) | 69.2% |
| left_tail_p05 | -0.3577% |

## Verdict Rationale

`mean_forward_return = 0.2194%` > 0 → **EDGE_PRESENT** (simple verdict)

## Statistical Significance

Honest read of the simple verdict above: a positive point estimate is
not the same as a real edge. With a small sample (~150 trades) and
high per-trade noise (~1% std), random variation can produce a
spurious-but-positive mean. Three numbers below decide whether the
edge is statistically distinguishable from zero:

| Statistic | Value |
|---|---|
| sample size | 13 |
| std (per trade) | 0.4267% |
| sem (mean's std error) | 0.1183% |
| **t-stat** vs zero | **1.85** |
| **p-value** (two-tailed) | **0.089** |
| 95% CI on mean | [-0.0385%, 0.4772%] |

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
| 2025 | 6 | 0.0482% | 0.3451% | 66.7% |
| 2026 | 7 | 0.3661% | 0.4587% | 71.4% |

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