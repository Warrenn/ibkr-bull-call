# Directional-Edge Sweep — TRAIN window

**Window**: 2023-05-01 → 2025-02-14

## Provenance

- code_revision: `ccb927cf6dccf952d4b0d47c1e8496477f7ead7a`
- run_timestamp_utc: 2026-04-30T17:20:57.374566+00:00
- ES dataset: `es_intraday.parquet`

## Important caveat

This is a SHAPING sweep on the **TRAIN** window.
It is not v1 evidence (v1 was already evaluated as `EDGE_INCONCLUSIVE`
on the full dataset in PR #55) and it is not v2 evidence either —
v2 evidence requires a frozen v2 spec evaluated on the **holdout**
window after a one-shot validation pass.

## Candidates ranked by |t-stat| (most significant first; sign indicates direction)

| threshold | signal_time | eow_time | vix | bonds | n | mean | t-stat | p-value | 95% CI | hit_rate | by_year_min | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.7500% | 10:30 | 15:55 | — | down | 2 | +0.3175% | +4.68 | 0.134 | [-0.5441%, +1.1790%] | 100.0% | +0.2497% | EDGE_PRESENT_CONTINUATION |
| 0.7500% | 11:00 | 15:55 | — | down | 3 | +0.1965% | +2.64 | 0.118 | [-0.1237%, +0.5167%] | 100.0% | +0.0622% | EDGE_PRESENT_CONTINUATION |
| 0.5000% | 10:30 | 15:55 | — | up | 6 | +0.3144% | +2.25 | 0.075 | [-0.0452%, +0.6741%] | 83.3% | -0.1448% | EDGE_PRESENT_CONTINUATION |
| 0.5000% | 11:00 | 15:55 | — | down | 10 | +0.1245% | +2.15 | 0.060 | [-0.0064%, +0.2555%] | 90.0% | +0.0622% | EDGE_PRESENT_CONTINUATION |
| 0.5000% | 10:30 | 15:55 | — | down | 8 | +0.1348% | +1.99 | 0.087 | [-0.0254%, +0.2949%] | 75.0% | +0.0799% | EDGE_INCONCLUSIVE |
| 0.7500% | 11:00 | 15:55 | — | up | 2 | -0.2511% | -1.85 | 0.316 | [-1.9777%, +1.4755%] | 0.0% | -0.2511% | NO_EDGE |
| 0.5000% | 10:00 | 15:55 | — | up | 5 | +0.3109% | +1.83 | 0.141 | [-0.1608%, +0.7826%] | 80.0% | -0.0745% | EDGE_INCONCLUSIVE |
| 0.2500% | 10:30 | 15:55 | — | down | 30 | +0.0887% | +1.20 | 0.240 | [-0.0626%, +0.2400%] | 66.7% | +0.0657% | EDGE_INCONCLUSIVE |
| 0.1000% | 10:30 | 15:55 | — | down | 66 | +0.0545% | +1.16 | 0.251 | [-0.0396%, +0.1486%] | 62.1% | +0.0342% | EDGE_INCONCLUSIVE |
| 0.1000% | 10:00 | 15:55 | — | up | 57 | -0.0811% | -1.09 | 0.282 | [-0.2305%, +0.0684%] | 45.6% | -0.1964% | NO_EDGE |
| 0.5000% | 11:00 | 15:55 | — | up | 13 | +0.0959% | +0.88 | 0.395 | [-0.1410%, +0.3329%] | 53.8% | -0.0795% | EDGE_INCONCLUSIVE |
| 0.2500% | 11:00 | 15:55 | — | down | 43 | -0.0620% | -0.79 | 0.434 | [-0.2207%, +0.0966%] | 60.5% | -0.1214% | NO_EDGE |
| 0.2500% | 11:00 | 15:55 | — | up | 36 | -0.0608% | -0.72 | 0.479 | [-0.2331%, +0.1115%] | 50.0% | -0.1579% | NO_EDGE |
| 0.2500% | 10:30 | 15:55 | — | up | 30 | +0.0369% | +0.43 | 0.671 | [-0.1386%, +0.2124%] | 56.7% | -0.1174% | EDGE_INCONCLUSIVE |
| 0.1000% | 10:30 | 15:55 | — | up | 61 | +0.0206% | +0.35 | 0.729 | [-0.0980%, +0.1392%] | 54.1% | -0.1309% | EDGE_INCONCLUSIVE |
| 0.2500% | 10:00 | 15:55 | — | up | 17 | -0.0513% | -0.29 | 0.776 | [-0.4267%, +0.3240%] | 47.1% | -0.4127% | NO_EDGE |
| 0.1000% | 11:00 | 15:55 | — | down | 72 | -0.0103% | -0.20 | 0.840 | [-0.1119%, +0.0912%] | 59.7% | -0.0415% | NO_EDGE |
| 0.2500% | 10:00 | 15:55 | — | down | 18 | -0.0172% | -0.09 | 0.930 | [-0.4253%, +0.3909%] | 61.1% | -0.1253% | NO_EDGE |
| 0.1000% | 11:00 | 15:55 | — | up | 63 | +0.0026% | +0.05 | 0.963 | [-0.1092%, +0.1144%] | 54.0% | -0.1579% | EDGE_INCONCLUSIVE |
| 0.1000% | 10:00 | 15:55 | — | down | 53 | -0.0005% | -0.01 | 0.996 | [-0.1706%, +0.1696%] | 62.3% | -0.1499% | NO_EDGE |

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