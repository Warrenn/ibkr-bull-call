# Directional-Edge Sweep — TRAIN window

**Window**: 2023-05-01 → 2025-02-14

## Provenance

- code_revision: `ccb927cf6dccf952d4b0d47c1e8496477f7ead7a`
- run_timestamp_utc: 2026-04-30T17:17:31.336989+00:00
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
| 0.7500% | 14:30 | 15:55 | — | 26 | -0.0982% | -1.68 | 0.106 | [-0.2187%, +0.0223%] | 42.3% | -0.2307% | NO_EDGE |
| 0.5000% | 14:00 | 15:55 | — | 52 | -0.0528% | -1.35 | 0.182 | [-0.1312%, +0.0255%] | 48.1% | -0.1085% | NO_EDGE |
| 0.7500% | 14:00 | 15:55 | — | 24 | -0.0792% | -1.25 | 0.224 | [-0.2103%, +0.0519%] | 41.7% | -0.1556% | NO_EDGE |
| 0.2500% | 14:00 | 15:55 | — | 111 | -0.0216% | -0.94 | 0.352 | [-0.0672%, +0.0241%] | 54.1% | -0.0656% | NO_EDGE |
| 0.5000% | 14:30 | 15:55 | — | 52 | -0.0313% | -0.90 | 0.370 | [-0.1006%, +0.0381%] | 51.9% | -0.0705% | NO_EDGE |
| 0.2500% | 14:30 | 15:55 | — | 110 | -0.0166% | -0.82 | 0.412 | [-0.0567%, +0.0234%] | 51.8% | -0.0394% | NO_EDGE |
| 0.1000% | 14:00 | 15:55 | — | 155 | -0.0098% | -0.48 | 0.635 | [-0.0506%, +0.0309%] | 52.9% | -0.0382% | NO_EDGE |
| 0.1000% | 14:30 | 15:55 | — | 159 | -0.0076% | -0.44 | 0.661 | [-0.0418%, +0.0266%] | 52.2% | -0.0104% | NO_EDGE |

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