# v8 Vol Term Structure Carry (SVXY contango gate)

**Window**: 2021-04-30 → 2026-04-30

## Strategy parameters

- instrument: SVXY (-0.5× short-vol ETF)
- contango_threshold: VIX/VIX3M < 0.93 → long
- monthly_max_loss_pct: -15%
- slippage_bps_per_flip: 10
- benchmark: SPY

## Provenance

- code_revision: `076e88186cffaad8ddfc85020b9178a9fed4fa0f`
- run_timestamp_utc: 2026-04-30T20:33:44.822774+00:00

## Aggregate results

- n_days: 1256
- n_years: 4.98
- in-position days: 901 (71.7%)
- regime flips (entries+exits): 122
- suspended days (monthly cap fired): 1
- regime counts: {'contango': 902, 'gray': 288, 'backwardation': 65, 'unknown': 1}

## Performance vs benchmark

| Metric | Strategy | SPY | Spread |
|---|---|---|---|
| Total return | **-10.54%** | +84.58% | -95.11% |
| CAGR | **-2.21%** | +13.08% | -15.29% |
| Sharpe (annualized) | **+0.03** | +0.81 | — |
| Win rate (days > 0) | 40.9% | — | beat SPY: 51.4% |
| Worst day | **-12.79%** | — | — |
| Best day | 7.03% | — | — |
| Max drawdown | **-47.67%** | — | — |
| Calmar ratio | **-0.05** | — | — |

## Statistical significance

| Hypothesis | t-stat | p-value | Verdict |
|---|---|---|---|
| Strategy mean > 0 | +0.07 | 0.9412 | inconclusive |
| Strategy outperforms SPY | -1.33 | 0.1844 | inconclusive |

## Caveats

- Volmageddon (2018-02-05) is OUTSIDE the post-2021 dataset-v1
  window. Tail-risk inference is theoretical. SVXY's -0.5×
  leverage caps single-day loss at -50% but real fills could be
  worse on stress gaps.
- Daily-close pricing only; signal computed on prior-day close,
  trade attribution to today's return. No intraday execution
  modeled.
- 10 bps slippage per regime flip; real fills on SVXY may be
  wider (lower liquidity than SPY/major ETFs).
- No commissions modeled.
- The contango gate is a single threshold (0.93); more
  sophisticated signal smoothing is deferred to v8a.