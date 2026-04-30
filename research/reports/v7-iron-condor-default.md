# v7 Short SPX 0DTE Iron Condor (no stop, monthly cap only)

**Window**: 2021-05-03 → 2026-04-29

## Strategy parameters

- entry_time_et: 10:30
- pop_threshold: 0.55
- max_loss_usd_per_side: $500
- strike_width (gap criterion, $): 5.0
- strike_spacing: 5.0
- skew_strength_calls / puts: -0.3 / 0.5
- bid_ask_pct atm/far: 5% / 30%
- monthly_max_loss_usd: $1000
- stop_loss: DISABLED (per v6 finding that breakeven stop destroys edge)

## Provenance

- code_revision: `f4db07bc0a308fd286480037eb1910de1d334b86`
- run_timestamp_utc: 2026-04-30T19:37:54.316172+00:00

## Results

| Metric | Value |
|---|---|
| total days in window | 1224 |
| trades executed | 0 |
| skipped (pop_below_threshold) | 1221 |
| skipped (no_viable_long_strike) | 3 |