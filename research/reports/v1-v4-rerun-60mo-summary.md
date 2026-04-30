# v1-v4 Re-run on Expanded 60mo Data — Sanity Check

**Window**: 2021-05-03 → 2026-04-29 (60 months, 1224 NYSE full-trading
days). Specs unchanged from their original frozen yamls; only the
data window expanded.

## Important caveat (read first)

This is a **full-window run**, not a properly out-of-sample test.
The v1-v4 specs were originally validated and killed using a 36mo
TRAIN/VALIDATION/HOLDOUT discipline. Re-running on the full 60mo
mixes train+validation+holdout into one window — useful as a
sanity check on whether the original conclusions hold given more
data, but NOT a clean re-freeze of any spec.

A proper re-evaluation would require pinning a new 60mo
TRAIN/VALIDATION/HOLDOUT split, re-shaping each candidate on TRAIN,
testing once on VALIDATION, then once on HOLDOUT. We did not do
that here.

## Results

| Spec | n | mean | t-stat | p-value | 95% CI | Interpretation |
|---|---|---|---|---|---|---|
| v1 (0.25%/10:30, no filters) | 300 | +0.085% | +1.54 | 0.125 | [-0.024%, +0.193%] | **STILL INCONCLUSIVE** |
| v2 (0.50%/10:30 + event filter) | 93 | **+0.301%** | **+2.48** | **0.015** | **[+0.060%, +0.543%]** | **SIGNIFICANT — different from 36mo verdict** |
| v3 (v2 + high-VIX gate) | 90 | +0.303% | +2.42 | 0.018 | [+0.054%, +0.553%] | Same as v2 (VIX gate ≈ no-op) |
| v4 (low-VIX + bonds-up + fade) | 33 | +0.151% (fade) | +1.86 | 0.073 | [-0.015%, +0.318%] | INCONCLUSIVE |

## v2 outlier sensitivity (the key finding)

The original v2 was killed in PR #58 because validation was 100%
driven by the 2025-04-09 tariff-pause outlier. On the expanded
60mo window, v2 is **robust to outlier removal**:

| Configuration | n | mean | t-stat | p-value |
|---|---|---|---|---|
| All entered trades | 93 | +0.301% | +2.48 | 0.015 |
| Drop top 1 outlier (2025-04-09) | 92 | +0.206% | **+2.69** | **0.009** |
| Drop top 3 outliers | 90 | +0.199% | **+3.00** | **0.004** |

**Removing the outlier IMPROVES the t-stat** because variance drops
faster than mean. The underlying signal is real and not concentrated
on tail events.

## v2 per-year breakdown

| year | n | mean | median |
|---|---|---|---|
| 2021 (partial) | 12 | -0.057% | +0.025% |
| 2022 (bear) | 39 | +0.294% | +0.395% |
| 2023 | 11 | +0.321% | +0.315% |
| 2024 | 8 | +0.128% | +0.144% |
| 2025 | 16 | +0.631% | +0.248% |
| 2026 (partial) | 7 | +0.366% | +0.225% |

**5 of 6 years POSITIVE**. The "edge" is not concentrated in any
single year. Even the worst year (2021 partial, n=12) has median
+0.025%.

## Why is the 60mo result different from 36mo?

The original v1-v4 KILL was based on:

1. **Small TRAIN samples**. v2 had n=14 on 36mo TRAIN. The signal
   was real (t=+2.92) but n was below the discipline threshold.
2. **Validation slot dominated by 2025 chaos**. The 7-month
   validation window (Feb-Sep 2025) coincided with the April 2025
   tariff-pause regime. v2's validation mean was +0.84% but
   driven 100% by 2025-04-09. Outlier-driven kill triggered.
3. **Holdout slot also chaos-period**. The 7-month holdout was
   Sep 2025 - Apr 2026, also high-vol regime.

The 36mo dataset gave the 2025 chaos disproportionate weight
relative to the calmer 2022 bear market and 2023-2024 environments.
Adding 24 months of 2021-2023 data dilutes the chaos period and
reveals what may be a real underlying signal.

## What this DOES say

- The 36mo v2 KILL conclusion was likely a **small-sample artifact**.
- On 60mo full-window data, the v2 spec (0.50%/10:30 + event filter)
  shows **statistically significant positive mean + outlier-robust
  + multi-year-consistent**.
- v3's VIX gate adds nothing on 60mo (n=93 → n=90, basically same).
- v4's fade-direction hypothesis still doesn't pan out on 60mo.

## What this DOES NOT say

- That v2 is now CONFIRMED. Full-window evaluation is data leakage
  vs. the spec freeze rule. A proper re-evaluation needs:
  1. Fresh 60mo TRAIN/VAL/HOLDOUT split
  2. Re-shape v2 candidate on TRAIN (might tweak the threshold)
  3. Validate ONCE on VAL
  4. Test ONCE on HOLDOUT
- That we have a profitable strategy. v2 is a directional
  hypothesis. To trade it you'd need an options structure (the
  original bull call spread) — and the v5 simulator showed that
  fair-pricing of options structures has ~zero EV. The directional
  edge needs to clear bid-ask + commissions.
- That a Bonferroni-adjusted multiple-comparison p-value would
  pass. v1-v4 are 4 candidates; 0.015 × 4 = 0.06 — just BARELY
  fails the Bonferroni-adjusted significance bar.

## Implications

The combination of these two findings is the most interesting
result of the whole project so far:

1. **Realized vol < implied vol consistently** (PR #63: ratio 0.483,
   t=-45). Vol risk premium is real.
2. **v2 directional signal positive on 60mo** (this PR: t=+2.48
   on n=93, robust). Continuation edge after 0.50% morning move +
   event filter may be real.
3. **BS-fair-value iron condor has ~zero EV** (PR #64). Vol premium
   needs market microstructure edge to extract.

A natural next direction (if any) would be:

- Re-pin 60mo split, re-shape v2 with proper discipline, test on
  60mo holdout. Free; tells us if v2 holds out-of-sample under
  expanded data.
- If v2 holds out-of-sample → spend on real SPX 0DTE chain data
  (~$495-1500) to test the bull-call-spread expression with real
  bid/ask. THAT is the proper Phase 2.

OR accept these findings as informative and stop committing time/
money to a strategy with weakly-positive but uncertain edge.
