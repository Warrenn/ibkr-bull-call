# Risk-Adjusted Metrics — v2 spec on full 60mo data

## Provenance

- code_revision: `30ac5c1c4dd3cf1b371d94097ceb5d08d548c412`
- run_timestamp_utc: 2026-04-30T18:51:45.400762+00:00
- ledger: `v2-rerun-60mo-ledger.csv`

## Sample

- n_trades: **93**
- years_covered: 4.89
- trades_per_year: 19.0

## Per-trade distribution

| Metric | Value |
|---|---|
| mean_return | **+0.3010%** |
| median_return | +0.2264% |
| std_return | 1.1723% |
| win_rate | 68.8% |
| max_loss (worst trade) | -2.0946% |
| max_win (best trade) | +9.0526% |
| p05 (5th percentile) | -1.0759% |
| p95 (95th percentile) | +1.3215% |

## Risk-adjusted ratios

| Metric | Value | Interpretation |
|---|---|---|
| Sharpe per trade | **0.257** | mean/std per trade |
| Sharpe annualized | **1.12** | × √(trades/year) |
| Calmar ratio | **1.31** | annual_return / max_DD |

**Sharpe interpretation**: > 1.0 is acceptable, > 2.0 is good, > 3.0 is
exceptional. **Calmar interpretation**: > 0.5 is acceptable, > 1.0
is good (recovers max-DD in less than a year of average performance).

## Drawdown profile

| Metric | Value |
|---|---|
| max_drawdown (cumulative trade returns) | **-4.3790%** |
| max_dd_duration (trades) | 11 |

## Calendar-period concentration

| Metric | Value |
|---|---|
| worst_month | **-1.8094%** |
| worst_year | **-0.6799%** |
| n_negative_months / n_total_months | 8/43 (19%) |

## Monthly-cap overlay (-1% cumulative monthly threshold)

Simulates user's "stop trading after $1000 monthly loss" rule applied to underlying signal returns. Cap threshold is -1% cumulative trade-return per month.

| Metric | Value |
|---|---|
| total_return WITH cap | **+20.9842%** |
| total_return WITHOUT cap | +27.9967% |
| trades skipped due to cap | 6 |
| months that hit cap | 6 of 43 |

## What this tells us

Sharpe annualized > 1.0 = **the strategy has acceptable risk-adjusted
return** if it can be executed at scale.

Sharpe annualized 0.5-1.0 = mediocre. Strategy may have some edge but
not enough to justify the operational complexity.

Sharpe annualized < 0.5 = strategy doesn't survive risk adjustment.
The mean return is too small relative to the variance.

**Critical caveat**: this analysis is on UNDERLYING (ES) returns, not
options P&L. A bull-call-spread expression of this signal would have:

- **Different P&L mechanics** — non-linear payoffs that may amplify
  small wins (good) but also magnify variance via the leverage of
  cheap OTM options.
- **Bid-ask + commissions** — typically $20-50 per round-trip on a
  retail bull call spread; a +0.22% underlying move that prices $5/contract
  in option terms would barely cover that.
- **Tail-day amplification** — on +9.05% days the OTM bull call spread
  could pay 5-10x; on -1% days it expires worthless. Higher kurtosis.

So the underlying-signal Sharpe is an **upper bound** on what the
options strategy could achieve; reality would be lower after frictions.