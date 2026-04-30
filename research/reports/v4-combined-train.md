# Directional-Edge Sweep — TRAIN window

**Window**: 2023-05-01 → 2025-02-14

## Provenance

- code_revision: `ccb927cf6dccf952d4b0d47c1e8496477f7ead7a`
- run_timestamp_utc: 2026-04-30T17:27:28.802098+00:00
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
| 0.7500% | 10:30 | 15:55 | high | down | 2 | +0.3175% | +4.68 | 0.134 | [-0.5441%, +1.1790%] | 100.0% | +0.2497% | EDGE_PRESENT_CONTINUATION |
| 0.5000% | 11:00 | 15:55 | high | down | 8 | +0.1747% | +3.29 | 0.013 | [+0.0490%, +0.3004%] | 100.0% | +0.0622% | EDGE_PRESENT_CONTINUATION |
| 0.5000% | 10:30 | 15:55 | high | down | 6 | +0.1900% | +2.74 | 0.041 | [+0.0115%, +0.3685%] | 83.3% | +0.1425% | EDGE_PRESENT_CONTINUATION |
| 0.7500% | 11:00 | 15:55 | high | down | 3 | +0.1965% | +2.64 | 0.118 | [-0.1237%, +0.5167%] | 100.0% | +0.0622% | EDGE_PRESENT_CONTINUATION |
| 0.1000% | 10:00 | 15:55 | low | up | 31 | -0.1779% | -2.10 | 0.044 | [-0.3507%, -0.0051%] | 29.0% | -0.2612% | EDGE_PRESENT_MEAN_REVERSION |
| 0.7500% | 11:00 | 15:55 | high | up | 2 | -0.2511% | -1.85 | 0.316 | [-1.9777%, +1.4755%] | 0.0% | -0.2511% | NO_EDGE |
| 0.2500% | 10:30 | 15:55 | high | down | 19 | +0.1343% | +1.81 | 0.087 | [-0.0216%, +0.2902%] | 78.9% | +0.0657% | EDGE_INCONCLUSIVE |
| 0.5000% | 10:00 | 15:55 | high | up | 4 | +0.1565% | +1.71 | 0.186 | [-0.1346%, +0.4476%] | 75.0% | -0.0745% | EDGE_INCONCLUSIVE |
| 0.5000% | 10:30 | 15:55 | high | up | 5 | +0.2272% | +1.70 | 0.165 | [-0.1447%, +0.5992%] | 80.0% | -0.1448% | EDGE_INCONCLUSIVE |
| 0.1000% | 10:00 | 15:55 | low | down | 22 | +0.1002% | +1.12 | 0.276 | [-0.0861%, +0.2866%] | 68.2% | +0.0863% | EDGE_INCONCLUSIVE |
| 0.2500% | 11:00 | 15:55 | low | up | 13 | -0.1595% | -1.02 | 0.327 | [-0.4994%, +0.1804%] | 38.5% | -0.2609% | NO_EDGE |
| 0.2500% | 11:00 | 15:55 | high | down | 29 | -0.1002% | -0.98 | 0.336 | [-0.3098%, +0.1094%] | 62.1% | -0.1659% | NO_EDGE |
| 0.1000% | 10:30 | 15:55 | high | down | 33 | +0.0660% | +0.93 | 0.359 | [-0.0786%, +0.2105%] | 69.7% | +0.0460% | EDGE_INCONCLUSIVE |
| 0.1000% | 10:30 | 15:55 | low | down | 33 | +0.0430% | +0.68 | 0.500 | [-0.0853%, +0.1713%] | 54.5% | +0.0005% | EDGE_INCONCLUSIVE |
| 0.5000% | 11:00 | 15:55 | low | up | 2 | +0.3113% | +0.68 | 0.619 | [-5.4889%, +6.1115%] | 50.0% | -0.1452% | EDGE_INCONCLUSIVE |
| 0.2500% | 10:30 | 15:55 | high | up | 17 | +0.0724% | +0.67 | 0.514 | [-0.1573%, +0.3021%] | 58.8% | -0.1029% | EDGE_INCONCLUSIVE |
| 0.1000% | 10:00 | 15:55 | high | down | 31 | -0.0719% | -0.55 | 0.584 | [-0.3374%, +0.1935%] | 58.1% | -0.1499% | NO_EDGE |
| 0.5000% | 11:00 | 15:55 | low | down | 2 | -0.0760% | -0.52 | 0.695 | [-1.9387%, +1.7866%] | 50.0% | -0.2226% | NO_EDGE |
| 0.5000% | 11:00 | 15:55 | high | up | 11 | +0.0568% | +0.52 | 0.615 | [-0.1872%, +0.3008%] | 54.5% | -0.0664% | EDGE_INCONCLUSIVE |
| 0.1000% | 11:00 | 15:55 | high | down | 36 | -0.0399% | -0.46 | 0.650 | [-0.2167%, +0.1369%] | 63.9% | -0.0815% | NO_EDGE |
| 0.2500% | 10:00 | 15:55 | high | up | 12 | -0.0964% | -0.42 | 0.682 | [-0.6008%, +0.4080%] | 50.0% | -0.4165% | NO_EDGE |
| 0.1000% | 11:00 | 15:55 | low | down | 36 | +0.0192% | +0.36 | 0.723 | [-0.0900%, +0.1284%] | 55.6% | -0.0180% | EDGE_INCONCLUSIVE |
| 0.1000% | 11:00 | 15:55 | low | up | 35 | +0.0221% | +0.30 | 0.767 | [-0.1281%, +0.1722%] | 54.3% | -0.0006% | EDGE_INCONCLUSIVE |
| 0.1000% | 10:30 | 15:55 | high | up | 31 | +0.0228% | +0.28 | 0.785 | [-0.1466%, +0.1923%] | 54.8% | -0.1309% | EDGE_INCONCLUSIVE |
| 0.1000% | 10:00 | 15:55 | high | up | 26 | +0.0344% | +0.27 | 0.788 | [-0.2269%, +0.2957%] | 65.4% | -0.1964% | EDGE_INCONCLUSIVE |
| 0.1000% | 11:00 | 15:55 | high | up | 28 | -0.0217% | -0.25 | 0.805 | [-0.1998%, +0.1564%] | 53.6% | -0.1579% | NO_EDGE |
| 0.5000% | 10:30 | 15:55 | low | down | 2 | -0.0310% | -0.22 | 0.861 | [-1.8048%, +1.7428%] | 50.0% | -0.1706% | NO_EDGE |
| 0.1000% | 10:30 | 15:55 | low | up | 30 | +0.0183% | +0.21 | 0.833 | [-0.1579%, +0.1945%] | 53.3% | +0.0156% | EDGE_INCONCLUSIVE |
| 0.2500% | 10:00 | 15:55 | low | up | 5 | +0.0568% | +0.21 | 0.847 | [-0.7070%, +0.8206%] | 40.0% | -0.3899% | EDGE_INCONCLUSIVE |
| 0.2500% | 10:00 | 15:55 | high | down | 12 | -0.0455% | -0.18 | 0.864 | [-0.6153%, +0.5244%] | 66.7% | -0.2927% | NO_EDGE |
| 0.2500% | 11:00 | 15:55 | low | down | 14 | +0.0170% | +0.14 | 0.888 | [-0.2378%, +0.2718%] | 57.1% | -0.0262% | EDGE_INCONCLUSIVE |
| 0.2500% | 10:00 | 15:55 | low | down | 6 | +0.0394% | +0.13 | 0.898 | [-0.7132%, +0.7921%] | 50.0% | -0.0497% | EDGE_INCONCLUSIVE |
| 0.2500% | 10:30 | 15:55 | low | up | 13 | -0.0096% | -0.07 | 0.948 | [-0.3198%, +0.3007%] | 53.8% | -0.1344% | NO_EDGE |
| 0.2500% | 10:30 | 15:55 | low | down | 11 | +0.0098% | +0.06 | 0.952 | [-0.3438%, +0.3634%] | 45.5% | +0.0030% | EDGE_INCONCLUSIVE |
| 0.2500% | 11:00 | 15:55 | high | up | 23 | -0.0050% | -0.05 | 0.961 | [-0.2128%, +0.2028%] | 56.5% | -0.1579% | NO_EDGE |

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