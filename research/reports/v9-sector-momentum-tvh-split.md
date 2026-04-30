# v9 Sector Momentum — Train/Validation/Holdout Split Evaluation

**Final verdict**: **PROMOTE** — terminated at `all_gates_passed`

Procedural application of the v9 spec's `decision_rules` to the
60/20/20 split of the 81 traded months from
`research/reports/v9-sector-momentum-full-ledger.csv`.

## Provenance

- code_revision: `3c34186f647270587a092aba938906c84c75517d`
- run_timestamp_utc: 2026-04-30T20:18:55.695070+00:00
- spec: `research/specs/strategy-spec-v9-sector-momentum.yaml`

## Slice metrics

| Slice | n | Date range | Total ret | CAGR | Sharpe | Max DD | SPY total | Spread |
|---|---|---|---|---|---|---|---|---|
| train | 48 | 2019-08-31 → 2023-07-31 | +71.39% | +14.42% | +0.83 | -16.94% | +64.23% | +7.16% |
| validation | 16 | 2023-08-31 → 2024-11-30 | +40.15% | +28.81% | +1.83 | -6.46% | +33.87% | +6.28% |
| holdout | 17 | 2024-12-31 → 2026-04-30 | +17.27% | +11.90% | +0.86 | -6.21% | +21.53% | -4.26% |

## Gate-by-gate evaluation

### `always_kill_if` on `train` → **CONTINUE**

| Rule | Status | Evidence |
|---|---|---|
| `cumulative_max_dd < -40%` | ✅ ok | cum_max_dd=-16.94% |
| `n_consecutive_losing_months > 18` | ✅ ok | max_streak=3 |

### `train_continue_if` on `train` → **CONTINUE**

| Rule | Status | Evidence |
|---|---|---|
| `train_total_return > spy_train_total_return` | ✅ pass | port_return=71.39% vs spy_return=64.23% (spread=+7.16%) |
| `train_sharpe >= 0.5` | ✅ pass | sharpe=0.83 |
| `train_max_dd >= -25%` | ✅ pass | max_dd=-16.94% |

### `always_kill_if` on `validation` → **CONTINUE**

| Rule | Status | Evidence |
|---|---|---|
| `cumulative_max_dd < -40%` | ✅ ok | cum_max_dd=-16.94% |
| `n_consecutive_losing_months > 18` | ✅ ok | max_streak=3 |

### `validation_continue_if` on `validation` → **CONTINUE**

| Rule | Status | Evidence |
|---|---|---|
| `validation_total_return > 0` | ✅ pass | total_return=40.15% |
| `validation_sharpe > 0.3` | ✅ pass | sharpe=1.83 |
| `validation_max_dd > -30%` | ✅ pass | max_dd=-6.46% |

### `always_kill_if` on `holdout` → **CONTINUE**

| Rule | Status | Evidence |
|---|---|---|
| `cumulative_max_dd < -40%` | ✅ ok | cum_max_dd=-16.94% |
| `n_consecutive_losing_months > 18` | ✅ ok | max_streak=2 |

### `holdout_continue_if` on `holdout` → **PROMOTE**

| Rule | Status | Evidence |
|---|---|---|
| `holdout_total_return > 0` | ✅ pass | total_return=17.27% |
| `holdout_sharpe > 0` | ✅ pass | sharpe=0.86 |
| `holdout_outperforms_spy_or_within_5pct` | ✅ pass | port_return=17.27% vs spy_return-5%=16.53% |

## Verdict reasoning

All gates passed per the frozen v9 spec. v9 is promoted from
informational to **paper-trading candidate** per the roadmap definition
of `PROMOTED` ("survived holdout; candidate for paper trading").

PROMOTE does **not** mean live-capital deployment. It means the
falsification framework did not kill v9 on its frozen 81mo window.

## Caveats and adjacent evidence

The procedural verdict is PROMOTE. The following adjacent evidence
should be considered before any live-capital decision:

**1. The holdout slice underperformed SPY by -4.26% total return.**
v9 holdout total return +17.27%, SPY +21.53% — port lost to SPY but the
spec's holdout rule is disjunctive: "outperforms SPY OR within 5% of SPY"
(SPY-5% = 16.53% in this case, port 17.27% just barely clears it). A
stricter "must beat SPY" rule would have killed.

**2. Cross-window fragility check (PR #70):** v9 re-run on the matched
60mo window 2021-04 → 2026-04 (the dataset-v1 window all other
strategies use) **loses to SPY by -2.58% CAGR**. The +0.91% full-window
edge collapses on the post-2021 sub-window — v9's apparent edge is
concentrated in the 2019-2020 COVID/stimulus regime (XLK/XLY
outperformance) and weakens substantially in recent years.

**3. Sharpe degradation across slices:** train 0.83 → val 1.83 → holdout
0.86. Direction of travel is concerning; the validation Sharpe of 1.83
appears regime-dependent (post-bear momentum continuation in 2024).

**4. Statistical significance of outperformance is weak.** Full-window
spread vs SPY t=0.22, p=0.83. Slice-level t-stats are not computed by
the spec but would be lower-power on n=16-17 month samples.

**5. v9 also fails to beat passive monthly DCA on either window** (PR #70):
- 60mo: v9 14.28% CAGR < SPY DCA IRR 17.14%
- 81mo: v9 16.58% CAGR > SPY DCA IRR 16.25% by only 0.33%

## Recommended next step

Per roadmap: v9 → PROMOTED → paper-trading candidate. Recommend
**paper-trade v9 for at least 6 months** before any live-capital
decision, monitoring monthly returns vs SPY. The cross-window evidence
suggests regime sensitivity that paper trading will quickly confirm or
refute on fresh data.

Do **not** retroactively tighten the kill rules — that would be moving
the goalposts post hoc. The spec ruled PROMOTE; honor it.