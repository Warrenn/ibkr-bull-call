# v8 Vol Carry — Train/Validation/Holdout Split Evaluation

**Final verdict**: **KILL** — terminated at `train_continue_if`

## Provenance

- code_revision: `076e88186cffaad8ddfc85020b9178a9fed4fa0f`
- run_timestamp_utc: 2026-04-30T20:35:10.744822+00:00
- spec: `research/specs/strategy-spec-v8-vol-carry.yaml`

## Slice metrics

| Slice | n | Date range | Total ret | CAGR | Sharpe | Max DD | Worst day | SPY total | Spread |
|---|---|---|---|---|---|---|---|---|---|
| train | 753 | 2021-04-30 → 2024-04-26 | +23.90% | +7.43% | +0.41 | -32.41% | -12.79% | +27.31% | -3.41% |
| validation | 251 | 2024-04-29 → 2025-04-29 | -36.25% | -36.36% | -1.87 | -43.31% | -10.13% | +10.45% | -46.70% |
| holdout | 252 | 2025-04-30 → 2026-04-30 | +13.27% | +13.27% | +0.68 | -14.47% | -6.35% | +31.27% | -18.00% |

## Gate-by-gate evaluation

### `always_kill_if` on `train` → **CONTINUE**

| Rule | Status | Evidence |
|---|---|---|
| `cumulative_max_dd < -40%` | ✅ ok | cum_max_dd=-32.41% |
| `max_single_day_loss < -25%` | ✅ ok | worst_day=-12.79% |

### `train_continue_if` on `train` → **KILL**

| Rule | Status | Evidence |
|---|---|---|
| `train_total_return > 0` | ✅ pass | total_return=23.90% |
| `train_sharpe >= 0.8` | 🔴 fail | sharpe=0.41 |
| `train_max_dd >= -25%` | 🔴 fail | max_dd=-32.41% |
| `train_max_single_day_loss >= -15%` | ✅ pass | worst_day=-12.79% |

## Verdict reasoning

Terminated at `train_continue_if`. v8 is KILLED.

Per spec freeze, no parameter retuning. Move to next strategy or
create v8a with explicit acknowledgment that this v8 holdout is consumed.