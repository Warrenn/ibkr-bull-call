# v5 Phase 1 — Realized vs Implied Intraday Vol Test

**Verdict: `VOL_PREMIUM_EXISTS`**

## Mechanic

Per-NYSE-full-trading-day, compute:

- ``realized_pct`` = |ES_close(15:55) - ES_open(09:30)| / ES_open
- ``implied_pct`` = prior-day VIX close / 100 / √252  (VIX-implied 1-σ daily return)
- ``ratio`` = realized / implied

If mean(ratio) < 1 with statistical significance, vol risk premium
exists — short-vol strategies (e.g. far-OTM short iron condor) have
a fundamental tailwind to harvest.

## Provenance

- code_revision: `d412fda8eebb467a29b15bcacebada073128b849`
- run_timestamp_utc: 2026-04-30T18:09:19.936844+00:00
- ES dataset: `es_intraday.parquet` sha256:`c4d8b3d2d75431b71a7a4635969cf8de8cec362704aa832242f79183019f792d`
- VIX dataset: `vix_daily.parquet` sha256:`d965ae998c7051cc307fd711ff9429416243aa5f75daa205c98ec733d3f5b71e`
- Calendar: `trading_calendar.parquet` sha256:`96803a2e968723b48b4625922a4104bbe5b529bbb3d7eaab8d2cf6929d2b4140`
- Date range: 2021-05-03 → 2026-04-29
- Days analyzed: 1224

## Aggregate Distribution

| Metric | realized_pct | implied_pct | ratio (realized/implied) |
|---|---|---|---|
| n | 1224 | 1224 | 1224 |
| mean | 0.6132% | 1.2143% | 0.483 |
| median | 0.4567% | 1.1336% | 0.408 |
| std | 0.6407% | 0.3322% | 0.402 |
| p05 | 0.0359% | 0.8149% | 0.032 |
| p25 | 0.1935% | 0.9797% | 0.174 |
| p75 | 0.8401% | 1.3748% | 0.683 |
| p95 | 1.7095% | 1.8972% | 1.233 |

## Statistical Test (H0: mean(ratio) == 1)

- t-stat vs 1.0: **-44.98**
- p-value (two-tailed): **0.0000**
- 95% CI on mean ratio: [0.460, 0.505]

## Per-Year Breakdown

| year | n | realized | implied | ratio_mean | ratio_median |
|---|---|---|---|---|---|
| 2021 | 166 | 0.4624% | 1.1757% | 0.376 | 0.312 |
| 2022 | 246 | 0.9877% | 1.6130% | 0.610 | 0.541 |
| 2023 | 244 | 0.5294% | 1.0682% | 0.490 | 0.431 |
| 2024 | 245 | 0.4311% | 0.9815% | 0.439 | 0.351 |
| 2025 | 243 | 0.6271% | 1.1983% | 0.482 | 0.408 |
| 2026 | 80 | 0.5447% | 1.2763% | 0.425 | 0.369 |

## Per-VIX-Tercile Breakdown

VIX terciles (prior-day close): low ≤ 16.36, mid (16.36, 20.30], high > 20.30

| vix_band | n | realized | implied | ratio_mean | ratio_median |
|---|---|---|---|---|---|
| low | 408 | 0.3833% | 0.9095% | 0.421 | 0.328 |
| mid | 408 | 0.5262% | 1.1389% | 0.459 | 0.407 |
| high | 408 | 0.9300% | 1.5946% | 0.568 | 0.515 |

## Reading the Verdict

- **VOL_PREMIUM_EXISTS** (mean ratio < 1 AND t-stat < -2): implied vol systematically overprices realized vol. Short-vol
structures (sell premium) have positive expectation before
transaction costs.
- **VOL_PREMIUM_LIKELY** (mean ratio < 1 but |t| < 2): directionally
favorable but not statistically significant.
- **NO_VOL_PREMIUM** (mean ratio >= 1): realized vol matches or
exceeds implied — short-premium strategies have negative or zero
expectation. Long-vol structures (buy premium) would be the
natural pivot.

## Caveats

- This is a 1-σ comparison. Far-OTM iron condors profit not from
  the average move but from the TAIL of the realized distribution
  — the 95th / 99th percentile move events. Even if mean(ratio) <
  1, a few large-move days can wipe out months of small premium
  collection. Tail behavior matters as much as mean behavior.
- Daily VIX is annualized 30-day vol; using it as a 1-day implied
  approximation is a common but imperfect convention.
- This test does not include transaction costs. A real far-OTM
  iron condor pays bid-ask spreads on 4 legs; the realized edge
  must clear those costs before any net P&L.
- Half-day sessions are excluded.