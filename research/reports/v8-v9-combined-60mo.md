# Combined v8 + v9 Portfolio (50/50 monthly rebalance)

**Window**: 2021-04-30 → 2026-04-30
**n_months**: 61

## Provenance

- code_revision: `076e88186cffaad8ddfc85020b9178a9fed4fa0f`
- run_timestamp_utc: 2026-04-30T20:37:18.272618+00:00

## Construction

Each month, allocate 50% capital to v9 (sector momentum, top-3 SPDRs)
and 50% to v8 (vol-carry SVXY contango gate). Realize each strategy's
monthly return on its half. Combined monthly return = simple average
of the two strategy returns. Rebalance to 50/50 at month-end.

## Performance comparison

| Metric | v8 alone | v9 alone | **Combined 50/50** | SPY |
|---|---|---|---|---|
| Total return | -10.54% | +68.70% | **+27.04%** | +84.58% |
| CAGR | -2.17% | +10.84% | **+4.82%** | +12.81% |
| Sharpe annualized | +0.03 | +0.76 | **+0.37** | +0.85 |
| Max drawdown | -44.90% | -13.15% | **-20.77%** | -23.93% |
| Calmar ratio | -0.05 | +0.82 | **+0.23** | +0.54 |
| Worst month | -15.03% | -9.71% | **-10.04%** | -9.24% |
| Best month | 17.33% | 11.97% | 12.65% | 10.62% |
| Win rate (months > 0) | 49.2% | 52.5% | **50.8%** | 63.9% |

## Correlation

- **corr(v8, v9) = +0.390**

Low or negative correlation = bigger diversification benefit.
Positive correlation > 0.5 = limited diversification.

## Statistical significance (combined vs SPY)

- spread mean t-stat: -1.70
- p-value: 0.0952
- verdict: inconclusive

## Reading the result

If combined Sharpe > max(v8, v9), diversification helped. Otherwise
the negative-return strategy diluted the positive one. With only two
strategies, sign-of-correlation matters most:

- corr ≈ 0  → maximal diversification (combined vol < average vol)
- corr > 0  → limited benefit; combined Sharpe ≈ average Sharpe
- corr < 0  → vol reduction *and* return preserved → ideal

## Caveats

- v9 was promoted per its frozen spec; v8 was KILLED per its frozen
  spec (see `research/reports/v8-vol-carry-tvh-split.md`). This
  combined analysis is **descriptive**, not a recommendation to deploy
  v8+v9 — deploying a killed strategy violates the project's
  discipline framework.
- 60-month window only includes v9's traded months; first months are
  v9-skipped due to insufficient lookback.
- 50/50 weighting is naïve; risk-parity or vol-weighted allocation
  would scale v8's higher volatility down.
- No rebalancing costs modeled (would be small for monthly rebalance).
- Combined Sharpe assumes monthly returns are stationary — vol
  regimes can violate this.