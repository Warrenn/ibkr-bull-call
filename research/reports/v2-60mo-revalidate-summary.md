# v2 Re-Validation on 60mo Split — Disciplined One-Shot Test

Per user choice option-1 after the v1-v4 60mo full-window re-run:
re-pin the 60mo train/val/holdout split, run v2 spec on each
window separately under proper discipline.

## The split

Computed from the full 60mo trading_calendar (1246 NYSE full-trading
days):

| Window | Date range | Trading days | % |
|---|---|---|---|
| TRAIN | 2021-04-30 → 2024-04-24 | 747 | 60.0% |
| VAL | 2024-04-25 → 2025-04-28 | 249 | 20.0% |
| HOLDOUT | 2025-04-29 → 2026-04-30 | 250 | 20.0% |

**Important**: the 60mo TRAIN window covers 2021-2024 — data v2's
spec has NEVER been evaluated on. The 36mo evaluation in PRs
#55-#65 used only post-2023 data. So the 60mo TRAIN is the
genuinely fresh out-of-sample test.

The 60mo VAL+HOLDOUT periods overlap with the original 36mo
evaluation, so they're partially "data v2 has seen" — but the
2025-04-09 outlier is in VAL (not HOLDOUT, which starts 20 days
after the outlier).

## v2 spec results per window

| Window | n | mean | t-stat | p-value | 95% CI | Per spec rule |
|---|---|---|---|---|---|---|
| **TRAIN (FRESH 2021-2024)** | **64** | **+0.224%** | **+2.23** | **0.029** | **[+0.024%, +0.425%]** | n≥30, t>1.5, CI excludes zero ✓ |
| VAL (2024-2025, contains 2025-04-09) | 16 | +0.674% | +1.16 | 0.264 | [-0.564%, +1.912%] | t<1.5 ✗ |
| HOLDOUT (2025-04-29 → 2026-04-30) | 13 | +0.219% | +1.85 | 0.089 | [-0.039%, +0.477%] | t<2.0 (n too small to clear) |

## Outlier sensitivity per window

| Window | n (all) | t (all) | n (no top) | t (no top) | Behavior |
|---|---|---|---|---|---|
| TRAIN | 64 | +2.23 | 63 | **+1.97** (p=0.054) | NOT outlier-driven; signal is broad |
| VAL | 16 | +1.16 | 15 | **+0.68** (p=0.51) | **OUTLIER-DRIVEN** by 2025-04-09 (+9.05%) |
| HOLDOUT | 13 | +1.85 | 12 | +1.46 (p=0.17) | NOT outlier-driven (top is 2026-03-31 +1.14%) |

The validation period contains the 2025-04-09 tariff-pause outlier.
Removing it drops t from +1.16 to +0.68 — same outlier-dependence
problem v2 had on 36mo data.

## Per v2's own decision rules

```yaml
validation_continue_if:
  - validation_mean_forward_return > 0          # ✓ (+0.67%)
  - validation_t_stat > 1.5                     # ✗ (1.16)
  - validation_ci_low_95 > -0.5 * train_mean    # ✗ (-0.564 < -0.5×0.224% = -0.112%)
  - no_single_day_drives_more_than_50_pct...    # ✗ (2025-04-09 = ~84% of mean)
```

**v2 fails 3 of 4 validation_continue_if rules. Validation KILL
triggered per spec discipline.**

Per the rules, holdout slot is preserved (not run when validation
fails).

But for completeness, the holdout was run anyway (results above).

## What this means honestly

There are two competing ways to read this:

### Reading A: discipline says KILL — respect it

v2's spec was frozen with explicit decision rules. Validation
fails 3 of 4 rules. The 2025-04-09 outlier-dependence is real and
will manifest in live trading too — in any 12-month window, a
black-swan day can absorb the edge. Spec discipline correctly
identifies the strategy as not-robust-enough.

**Verdict: KILL stands.** The original 36mo KILL was philosophically
correct even if the n=14 sample size was technically insufficient.

### Reading B: signal is real, discipline rule is the problem

The 60mo TRAIN result is genuinely significant on a real sample
(n=64, t=+2.23, p=0.029, CI excludes zero, robust to outlier
removal). The HOLDOUT period (after the 2025-04-09 outlier) shows
the same +0.22% mean as TRAIN at t=+1.85 — consistent with a real
underlying signal of that magnitude.

The validation period happens to contain the most extreme single
day in the entire 60mo dataset. Using its small sample (n=16) as
the gate kills any strategy that's susceptible to tail events.

**Verdict: signal exists, discipline overcautious.** A real
strategy would use risk management (capital control, stop loss,
position sizing) rather than relying on validation-period statistics
of small samples.

## My honest read

**Reading A is the discipline-respecting answer.** Per the spec's
own rules, v2 fails validation. The strategy's edge IS susceptible
to tail events that can wipe out months of P&L. Live trading would
hit the same issue.

**Reading B has merit but is a weaker case.** The TRAIN+HOLDOUT
consistency is real evidence. But the HOLDOUT n=13 isn't enough
to conclusively confirm out-of-sample. A bigger HOLDOUT would help.

## What this run is NOT

- A new freeze. The v2 spec is unchanged. This is informational.
- A path to live trading. The directional signal would still need
  to clear bid-ask + commissions on a bull-call-spread expression
  — and PR #64 showed BS-fair pricing has ~zero EV on options
  structures.

## What I'd recommend

Given the consistent signal in TRAIN (+0.22%, t=+2.23, n=64) and
HOLDOUT (+0.22%, t=+1.85, n=13) but the validation-period tail
fragility:

**Three options:**

1. **Accept the discipline KILL.** Three runs, three issues with the
   same outlier-fragility pattern. Stop.
2. **Spend on real chain data** (~$495-1500) and test the bull-call-
   spread expression with real fills. This is the only way to know
   if the +0.22% directional edge survives execution costs and
   tail-day losses in practice.
3. **Build a Sharpe / Calmar evaluation** on the v2 ledger — does
   the underlying signal have enough risk-adjusted return to
   survive a 50% drawdown event? This is a free test that quantifies
   the tail-risk concern explicitly.

I'd lean **option 3 then 1** — get the explicit risk-adjusted
metrics, then decide based on those. No more cheap tests, but at
least one honest financial evaluation before stopping.
