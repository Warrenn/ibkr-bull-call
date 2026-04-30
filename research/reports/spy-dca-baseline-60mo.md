# SPY Dollar-Cost-Averaging Baseline (dataset-v1 60mo window)

**Window**: 2021-04-30 → 2026-04-30

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

- code_revision: `01f412ad6a34a139059ddafc84ec6fa8b54ec063`
- run_timestamp_utc: 2026-04-30T19:57:31.396161+00:00
- data: research/data/dataset-v1/sector_etfs_daily.parquet (SPY column)

## Results

| Metric | Lump sum | Monthly DCA | Daily DCA |
|---|---|---|---|
| Total contributed | $61,000.00 | $61,000.00 | $61,000.00 |
| Terminal market value | $112,591.84 | $94,149.57 | $93,839.96 |
| Profit ($) | $51,591.84 | $33,149.57 | $32,839.96 |
| Profit (%) | +84.58% | +54.34% | +53.84% |
| IRR (annualized) | +13.04% | +17.14% | +17.25% |
| Max market-value DD | -24.50% | -16.11% | -16.52% |
| Max P&L drawdown ($) | $-17,734.59 | $-12,571.58 | $-12,399.29 |
| Max P&L drawdown (% of contributed) | -29.07% | -20.61% | -20.33% |

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