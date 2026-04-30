# Directional-Edge Sweep — TRAIN window

**Window**: 2023-05-01 → 2025-02-14

## Provenance

- code_revision: `776229014c88327b10bffa9776e4c50aead443aa`
- run_timestamp_utc: 2026-04-30T16:28:25.725276+00:00
- ES dataset: `es_intraday.parquet`

## Important caveat

This is a SHAPING sweep on the **TRAIN** window.
It is not v1 evidence (v1 was already evaluated as `EDGE_INCONCLUSIVE`
on the full dataset in PR #55) and it is not v2 evidence either —
v2 evidence requires a frozen v2 spec evaluated on the **holdout**
window after a one-shot validation pass.

## Candidates ranked by t-stat (most significant first)

| threshold | signal_time | eow_time | n | mean | t-stat | p-value | 95% CI | hit_rate | by_year_min | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.7500% | 10:30 | 15:55 | 2 | +0.3175% | +4.68 | 0.134 | [-0.5441%, +1.1790%] | 100.0% | +0.2497% | EDGE_PRESENT_AND_SIGNIFICANT |
| 0.5000% | 11:00 | 15:55 | 31 | +0.0987% | +1.65 | 0.109 | [-0.0234%, +0.2209%] | 67.7% | -0.0237% | EDGE_INCONCLUSIVE |
| 0.1000% | 10:30 | 15:55 | 161 | +0.0430% | +1.12 | 0.263 | [-0.0326%, +0.1186%] | 57.8% | +0.0138% | EDGE_INCONCLUSIVE |
| 0.2500% | 10:30 | 15:55 | 72 | +0.0467% | +0.89 | 0.379 | [-0.0585%, +0.1518%] | 61.1% | -0.0242% | EDGE_INCONCLUSIVE |
| 0.5000% | 10:30 | 15:55 | 17 | +0.0595% | +0.53 | 0.600 | [-0.1765%, +0.2956%] | 64.7% | +0.0306% | EDGE_INCONCLUSIVE |
| 0.5000% | 10:00 | 15:55 | 6 | +0.0590% | +0.21 | 0.846 | [-0.6802%, +0.7982%] | 66.7% | -0.1360% | EDGE_INCONCLUSIVE |
| 0.7500% | 11:00 | 15:55 | 5 | +0.0175% | +0.14 | 0.895 | [-0.3285%, +0.3635%] | 60.0% | -0.0610% | EDGE_INCONCLUSIVE |
| 0.1000% | 11:00 | 15:55 | 176 | +0.0050% | +0.14 | 0.892 | [-0.0672%, +0.0771%] | 58.5% | -0.0369% | EDGE_INCONCLUSIVE |
| 0.2500% | 10:00 | 15:55 | 46 | -0.0375% | -0.36 | 0.722 | [-0.2483%, +0.1734%] | 54.3% | -0.2146% | NO_EDGE |
| 0.2500% | 11:00 | 15:55 | 103 | -0.0535% | -0.97 | 0.334 | [-0.1629%, +0.0559%] | 56.3% | -0.1428% | NO_EDGE |
| 0.1000% | 10:00 | 15:55 | 140 | -0.0711% | -1.39 | 0.168 | [-0.1726%, +0.0303%] | 51.4% | -0.1835% | NO_EDGE |

## Reading the table

- **t-stat ≥ 2** ≈ p < 0.05 — the conventional bar for "distinguishable from zero".
- **by_year_min**: smallest per-year mean. If this is meaningfully
  negative while the aggregate mean is positive, the edge is
  regime-dependent (per spec rule `result_is_not_concentrated_in_one_small_regime_cluster`).
- **n** below ~30 means very low statistical power; treat any
  verdict on those rows as suggestive at best.

## Decision frame

Take to **validation** only candidates that pass ALL of:
- `verdict_nuanced == EDGE_PRESENT_AND_SIGNIFICANT` (t-stat ≥ 2)
- `n` is large enough to be meaningful (≥ 30 trades)
- `by_year_min` is not catastrophically negative
- A coherent story can be told for *why* this combination would work
  (avoid p-hacking from the grid).

If no candidate passes those filters, the honest read is **NO_EDGE**:
the directional view doesn't have standalone same-session edge
robust enough to justify continuing.