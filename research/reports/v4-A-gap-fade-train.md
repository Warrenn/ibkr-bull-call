# v4-A Gap-Fade Sweep — TRAIN window

**Window**: 2023-05-01 → 2025-02-14
**Days in window**: 438; after event filter: 366.
**Event filter**: enabled

## Provenance

- code_revision: `ccb927cf6dccf952d4b0d47c1e8496477f7ead7a`
- run_timestamp_utc: 2026-04-30T17:16:14.107521+00:00
- ES dataset: `es_intraday.parquet`

## Mechanic

Per-day signal: ``gap = today_open(09:30 ET) / prior_close(15:55 ET, prior trading day) - 1``. Fire a fade trade when ``|gap| >= threshold``: short if gap > 0, long if gap < 0. ``fade_pnl = -sign(gap) * forward_return`` where ``forward_return = today_close(15:55) / today_open(09:30) - 1``.

## Important caveat

This is a SHAPING sweep on the **TRAIN** window.
It is not v4 evidence — v4 evidence requires a frozen v4 spec
evaluated on validation (one-shot) and holdout (one-shot).

## Candidates ranked by |t-stat|

| threshold | n | n_up | n_down | mean | t-stat | p-value | 95% CI | hit_rate | by_year_min | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.1000% | 285 | 172 | 113 | +0.0258% | +0.74 | 0.461 | [-0.0430%, +0.0946%] | 53.3% | -0.0113% | EDGE_INCONCLUSIVE |
| 1.0000% | 13 | 7 | 6 | +0.1016% | +0.65 | 0.525 | [-0.2365%, +0.4398%] | 53.8% | -0.3137% | EDGE_INCONCLUSIVE |
| 0.3000% | 164 | 100 | 64 | +0.0156% | +0.31 | 0.754 | [-0.0826%, +0.1138%] | 50.0% | -0.0172% | EDGE_INCONCLUSIVE |
| 0.7500% | 42 | 25 | 17 | -0.0242% | -0.20 | 0.843 | [-0.2690%, +0.2207%] | 45.2% | -0.1037% | NO_EDGE |
| 0.2000% | 213 | 130 | 83 | +0.0063% | +0.15 | 0.881 | [-0.0768%, +0.0894%] | 50.7% | -0.0635% | EDGE_INCONCLUSIVE |
| 0.5000% | 90 | 59 | 31 | +0.0043% | +0.06 | 0.953 | [-0.1419%, +0.1505%] | 48.9% | -0.0370% | EDGE_INCONCLUSIVE |

## Reading the table

- **EDGE_PRESENT_FADE_WORKS** (t ≥ +2): the fade trade is
  significantly profitable.
- **EDGE_PRESENT_FADE_BACKFIRES** (t ≤ -2): gaps are significantly
  more likely to *continue* than fade — a momentum edge in the
  opposite direction.
- **n_up / n_down**: count of gap-up vs gap-down days that fired
  the candidate. Asymmetric ratios may indicate direction-specific
  behavior worth a follow-up sweep.