# SPY Dollar-Cost-Averaging Baseline (dataset-v1 60mo window)

**Window**: 2018-06-19 → 2026-04-30

## Setup

- ticker: SPY
- contribution_per_month: $1,000.00
- modes:
    - **lump_sum**: invest the full window's monthly total on day 1
    - **monthly_dca**: invest one month's amount on the first trading day of each month
    - **daily_dca**: distribute one month's amount evenly across that month's trading days
- All three modes contribute the **same total $** over the window —
  only the timing differs.

## Provenance

- code_revision: `2fd808601cd229d03627954323cd27e2654ec202`
- run_timestamp_utc: 2026-04-30T20:07:09.773701+00:00
- data: research/data/dataset-v1/sector_etfs_daily.parquet (SPY column)

## Results

| Metric | Lump sum | Monthly DCA | Daily DCA |
|---|---|---|---|
| Total contributed | $95,000.00 | $95,000.00 | $95,000.00 |
| Terminal market value | $279,460.39 | $183,748.79 | $182,817.41 |
| Profit ($) | $184,460.39 | $88,748.79 | $87,817.41 |
| Profit (%) | +194.17% | +93.42% | +92.44% |
| IRR (annualized) | +14.71% | +16.25% | +16.27% |
| Max market-value DD | -33.72% | -30.83% | -30.16% |
| Max P&L drawdown ($) | $-44,018.42 | $-26,684.55 | $-26,414.32 |
| Max P&L drawdown (% of contributed) | -46.34% | -28.09% | -27.80% |

## Reading the result

- **IRR** is the apples-to-apples comparison: it accounts for
  the timing of contributions. Lump-sum has all capital at
  work for the full window; DCA modes only have late
  contributions exposed for a short time.
- **Max market-value DD** treats the position like an equity
  curve: the largest peak-to-trough drop. Less meaningful for
  DCA because new contributions push the value up and mask
  underlying SPY drawdowns.
- **Max P&L drawdown** tracks the worst paper loss from peak.
  This is the number a DCA investor would actually feel.

## Caveats

- Fills are at daily close (no intraday slippage modeled).
- No transaction costs (IBKR retail SPY commission-free since 2019).
- Fractional shares assumed (DCA buys ``contribution / close`` shares).
- Reinvested dividends already baked into auto-adjusted close.
- IRR via Brent root-finding on annualized rate; may report NaN
  if cashflows don't bracket a sign change (shouldn't happen for
  a profitable position over this window).