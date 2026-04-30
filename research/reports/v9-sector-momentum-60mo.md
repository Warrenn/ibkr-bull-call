# v9 Sector ETF Momentum (12-1, top-3, monthly rebalance)

**Window**: 2021-04-30 → 2026-04-30

## Strategy parameters

- universe: XLK, XLF, XLE, XLV, XLY, XLP, XLI, XLB, XLU, XLRE, XLC (11 ETFs)
- benchmark: SPY
- lookback_months: 12
- skip_recent_months: 1
- hold_top_n: 3
- weighting: equal
- rebalance_frequency: monthly
- slippage_bps_per_rebalance: 10
- max_dd_kill_pct: -40% (informational)

## Provenance

- code_revision: `2fd808601cd229d03627954323cd27e2654ec202`
- run_timestamp_utc: 2026-04-30T20:07:03.134451+00:00

## Aggregate results

- n_total_months: 60
- n_traded: 47 (skipped 13 for insufficient lookback / forward)
- n_years: 3.92

## Performance vs benchmark

| Metric | Portfolio | SPY | Spread |
|---|---|---|---|
| Total return | **68.7%** | 84.1% | -15.4% |
| CAGR | **14.28%** | 16.86% | -2.58% |
| Sharpe (annualized) | **0.87** | 1.07 | — |
| Win rate (months > 0) | 68.1% | — | beat SPY: 40.4% |
| Worst month | **-9.71%** | — | — |
| Best month | 11.97% | — | — |
| Negative months | 15 of 47 (32%) | — | — |
| Max drawdown | **-9.66%** | — | — |
| Calmar ratio | **1.48** | — | — |

## Statistical significance

| Hypothesis | t-stat | p-value | Verdict |
|---|---|---|---|
| Portfolio mean > 0 | +1.73 | 0.0901 | inconclusive |
| Portfolio outperforms SPY | -0.54 | 0.5945 | inconclusive |

## Reading the result

- **Sharpe ≥ 1.0** = acceptable risk-adjusted return.
- **Beats SPY (spread > 0) with t-stat ≥ 2** = momentum factor
  premium present in this window net of slippage.
- **Max DD ≥ -25%** = within v9 spec's drawdown tolerance.
- **Calmar ≥ 0.5** = recovers max DD in less than 2 years of
  average performance.

## Caveats

- 7.8 years is a short window for momentum (academic studies use
  30+ years). This window includes 2020 COVID, 2022 bear, and the
  2025 tariff regime — diverse but not exhaustive.
- 10 bps round-trip slippage assumed; real fills on liquid SPDRs
  may be tighter, but slippage at scale could be wider.
- No commissions modeled (IBKR retail commission-free since 2019).
- 12-1 is one of many momentum signal definitions; results may
  vary with 6-1, 9-1, or no-skip variants.
- Sample size is months not days — n=~85 monthly observations
  means t-stats need to be interpreted with low-power caution.