# STRATEGY-SPEC-v3

Status: KILLED 2026-04-30 — validation result identical to v2;
single-outlier rule fires.

Purpose: test whether the v2 candidate (0.50%/10:30 + event filter)
captures a real signal when restricted to high-VIX regime days, or
whether the v2 train finding was regime-agnostic noise.

## What changed from v2

| Element | v2 | v3 |
|---|---|---|
| `signal_threshold` | 0.0050 | unchanged |
| Event filter | FOMC + CPI + NFP + OPEX | unchanged |
| Time | 10:30 ET | unchanged |
| **VIX gate** | none | **prior-day VIX ≥ 14.84 (median of TRAIN)** |
| Required inputs | + `vix_daily` | |
| `validation_kill_if.outlier_rule` | none | **fires if one day > 75% of aggregate mean** (lessons-learned from v2's 2025-04-09 outlier) |

## Why v3 didn't improve on v2

The v3 train sweep showed the high-VIX subset of v2's 14 trades
produced t=+3.05 (vs v2's t=+2.92) — a sharper signal. The
hypothesis: maybe the v2 result wasn't noise but a high-VIX-only
phenomenon. Filter to that regime and we'd get a cleaner signal.

The validation result killed that hypothesis flatly:

- All 12 v2 validation trades had prior-day VIX ≥ 14.84 (lowest: 16.60)
- The VIX gate excluded ZERO trades
- v3 validation = v2 validation, byte-identical
- Same +0.84% mean, same t=1.09, same +9.05% 2025-04-09 outlier
- Same fail of `validation_t_stat > 1.5`
- The new `validation_kill_if.outlier_rule` fires:
  9.05% / 10.13% = **89% of aggregate mean comes from one day**

The validation period (2025-02-18 → 2025-09-22) coincided with the
April 2025 tariff turmoil — VIX hit 52. The whole period was a
high-VIX regime; the gate was effectively a no-op.

## Provenance

- **parent**: `STRATEGY-SPEC-v2` (`docs/STRATEGY-SPEC-v2.md`); v2
  was killed by the same single-outlier validation issue v3 now
  catches with an explicit rule.
- **train evidence**:
  `research/reports/directional-edge-sweep-train-v3.md` — high-VIX
  subset showed t=+3.05, n=11 on TRAIN.
- **validation evidence**:
  `research/reports/directional-edge-v3-validation.md` — n=12,
  mean +0.84% but 89% from one outlier; t=1.09; CI includes zero.
- **holdout evidence**: NEVER RUN. v3 fails validation; per the
  spec rules the holdout slot is preserved.

## What this tells us about the broader hypothesis

Three specs (v1, v2, v3) have all failed falsification on
dataset-v1:

- v1: borderline flat (t=0.88 across the full data)
- v2: train looked promising (t=2.92 on n=14) but validation was
  driven by a single outlier
- v3: "outlier-resistance" rule catches that v2's validation was
  one-day-dependent; the high-VIX gate adds nothing because the
  validation period was already entirely high-VIX

Across three specs, removing the 2025-04-09 outlier always returns
the underlying view to "borderline flat" (~+0.10% per trade). The
bullish-intraday-continuation hypothesis is dead in three specs in
three different ways.

## Lessons to encode for any v4 (or successor project)

If a v4 ever exists, it should:

1. NOT be a re-tuning of the same continuation hypothesis. v4 must
   be a fundamentally different signal family (mean reversion,
   different time window, cross-asset confirmation, etc.) — not
   "v2 with more parameters."
2. Pin an outlier-resistance rule from the spec freeze step, not
   add it after the first failure.
3. Have a clear plan for "what does NO_EDGE look like? at what
   point do we stop?" — three failed specs in a row is a strong
   signal the underlying view is wrong.

## Explicit Non-Claims

This spec does not claim:

- v3 has any edge (it does not)
- The bullish-continuation hypothesis can be salvaged with further
  v4 / v5 / vN tuning (the data does not support that)
- Live-capital readiness
- Validated stop logic, profit-taking, rolling, or regime filters
  beyond event + VIX

Those require either a fundamentally different hypothesis or
abandoning the project as currently scoped.
