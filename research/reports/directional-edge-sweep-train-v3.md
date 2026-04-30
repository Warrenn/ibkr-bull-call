# Directional-Edge Sweep — TRAIN window

**Window**: 2023-05-01 → 2025-02-14

## Provenance

- code_revision: `cef94cb603b9c9c655f448856306b8945d345101`
- run_timestamp_utc: 2026-04-30T16:57:07.335341+00:00
- ES dataset: `es_intraday.parquet`

## Important caveat

This is a SHAPING sweep on the **TRAIN** window.
It is not v1 evidence (v1 was already evaluated as `EDGE_INCONCLUSIVE`
on the full dataset in PR #55) and it is not v2 evidence either —
v2 evidence requires a frozen v2 spec evaluated on the **holdout**
window after a one-shot validation pass.

## Candidates ranked by |t-stat| (most significant first; sign indicates direction)

| threshold | signal_time | eow_time | vix_band | n | mean | t-stat | p-value | 95% CI | hit_rate | by_year_min | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.7500% | 10:30 | 15:55 | high | 2 | +0.3175% | +4.68 | 0.134 | [-0.5441%, +1.1790%] | 100.0% | +0.2497% | EDGE_PRESENT_CONTINUATION |
| 0.5000% | 10:30 | 15:55 | high | 11 | +0.2069% | +3.05 | 0.012 | [+0.0556%, +0.3582%] | 81.8% | +0.0877% | EDGE_PRESENT_CONTINUATION |
| 0.5000% | 10:00 | 15:55 | high | 4 | +0.1565% | +1.71 | 0.186 | [-0.1346%, +0.4476%] | 75.0% | -0.0745% | EDGE_INCONCLUSIVE |
| 0.2500% | 10:30 | 15:55 | high | 36 | +0.1051% | +1.65 | 0.108 | [-0.0242%, +0.2344%] | 69.4% | +0.0203% | EDGE_INCONCLUSIVE |
| 0.5000% | 11:00 | 15:55 | high | 19 | +0.1064% | +1.59 | 0.130 | [-0.0346%, +0.2475%] | 73.7% | +0.0193% | EDGE_INCONCLUSIVE |
| 0.1000% | 10:00 | 15:55 | low | 54 | -0.0684% | -1.08 | 0.285 | [-0.1953%, +0.0586%] | 44.4% | -0.1200% | NO_EDGE |
| 0.2500% | 11:00 | 15:55 | low | 28 | -0.0813% | -0.87 | 0.394 | [-0.2741%, +0.1115%] | 46.4% | -0.1695% | NO_EDGE |
| 0.5000% | 10:30 | 15:55 | low | 3 | +0.2295% | +0.84 | 0.489 | [-0.9439%, +1.4029%] | 66.7% | -0.1706% | EDGE_INCONCLUSIVE |
| 0.1000% | 10:30 | 15:55 | high | 64 | +0.0451% | +0.84 | 0.407 | [-0.0628%, +0.1530%] | 62.5% | -0.0425% | EDGE_INCONCLUSIVE |
| 0.2500% | 11:00 | 15:55 | high | 52 | -0.0581% | -0.81 | 0.423 | [-0.2024%, +0.0862%] | 59.6% | -0.1182% | NO_EDGE |
| 0.5000% | 11:00 | 15:55 | low | 4 | +0.1176% | +0.52 | 0.638 | [-0.5998%, +0.8350%] | 50.0% | -0.1839% | EDGE_INCONCLUSIVE |
| 0.1000% | 11:00 | 15:55 | high | 64 | -0.0319% | -0.52 | 0.606 | [-0.1549%, +0.0910%] | 59.4% | -0.0653% | NO_EDGE |
| 0.1000% | 10:30 | 15:55 | low | 64 | +0.0253% | +0.49 | 0.627 | [-0.0782%, +0.1287%] | 53.1% | -0.0009% | EDGE_INCONCLUSIVE |
| 0.2500% | 10:00 | 15:55 | high | 24 | -0.0709% | -0.42 | 0.679 | [-0.4209%, +0.2790%] | 58.3% | -0.3603% | NO_EDGE |
| 0.1000% | 11:00 | 15:55 | low | 72 | +0.0142% | +0.32 | 0.753 | [-0.0755%, +0.1039%] | 54.2% | -0.0209% | EDGE_INCONCLUSIVE |
| 0.1000% | 10:00 | 15:55 | high | 57 | -0.0234% | -0.26 | 0.797 | [-0.2054%, +0.1585%] | 61.4% | -0.1674% | NO_EDGE |
| 0.7500% | 11:00 | 15:55 | high | 5 | +0.0175% | +0.14 | 0.895 | [-0.3285%, +0.3635%] | 60.0% | -0.0610% | EDGE_INCONCLUSIVE |
| 0.2500% | 10:00 | 15:55 | low | 12 | +0.0114% | +0.06 | 0.950 | [-0.3838%, +0.4067%] | 41.7% | -0.0728% | EDGE_INCONCLUSIVE |
| 0.2500% | 10:30 | 15:55 | low | 24 | -0.0007% | -0.01 | 0.995 | [-0.2152%, +0.2138%] | 50.0% | -0.0719% | NO_EDGE |

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