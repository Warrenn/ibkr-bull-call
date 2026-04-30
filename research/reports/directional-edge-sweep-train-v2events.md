# Directional-Edge Sweep — TRAIN window

**Window**: 2023-05-01 → 2025-02-14

## Provenance

- code_revision: `787de24286a23abf763ddd722721074fd5054a87`
- run_timestamp_utc: 2026-04-30T16:39:07.181074+00:00
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
| 0.5000% | 10:30 | 15:55 | 14 | +0.2118% | +2.92 | 0.012 | [+0.0553%, +0.3682%] | 78.6% | +0.0877% | EDGE_PRESENT_AND_SIGNIFICANT |
| 0.5000% | 10:00 | 15:55 | 5 | +0.3109% | +1.83 | 0.141 | [-0.1608%, +0.7826%] | 80.0% | -0.0745% | EDGE_INCONCLUSIVE |
| 0.5000% | 11:00 | 15:55 | 23 | +0.1084% | +1.66 | 0.111 | [-0.0269%, +0.2436%] | 69.6% | -0.0213% | EDGE_INCONCLUSIVE |
| 0.2500% | 10:30 | 15:55 | 60 | +0.0628% | +1.12 | 0.269 | [-0.0498%, +0.1754%] | 61.7% | -0.0159% | EDGE_INCONCLUSIVE |
| 0.1000% | 10:30 | 15:55 | 129 | +0.0345% | +0.93 | 0.353 | [-0.0387%, +0.1076%] | 57.4% | -0.0425% | EDGE_INCONCLUSIVE |
| 0.7500% | 11:00 | 15:55 | 5 | +0.0175% | +0.14 | 0.895 | [-0.3285%, +0.3635%] | 60.0% | -0.0610% | EDGE_INCONCLUSIVE |
| 0.1000% | 11:00 | 15:55 | 137 | -0.0096% | -0.26 | 0.797 | [-0.0831%, +0.0640%] | 56.2% | -0.0425% | NO_EDGE |
| 0.2500% | 10:00 | 15:55 | 36 | -0.0435% | -0.34 | 0.733 | [-0.2999%, +0.2130%] | 52.8% | -0.2588% | NO_EDGE |
| 0.1000% | 10:00 | 15:55 | 112 | -0.0462% | -0.84 | 0.405 | [-0.1555%, +0.0632%] | 52.7% | -0.1674% | NO_EDGE |
| 0.2500% | 11:00 | 15:55 | 81 | -0.0690% | -1.23 | 0.223 | [-0.1808%, +0.0427%] | 54.3% | -0.1378% | NO_EDGE |

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