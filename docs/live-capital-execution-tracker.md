# Live Capital Execution Tracker

This is the short operating version of
[`live-capital-go-no-go.md`](./live-capital-go-no-go.md).

Use this document to decide what to do now, what is next, what can wait,
and what should stop the project immediately.

Current posture: `NO-GO for live capital until the NOW block is cleared and
the first evidence runs exist.`

## Rules Of Use

- Do not add new strategy rules while any `NOW` item is still open.
- Do not start paper trading until the `NEXT` evidence block has real outputs.
- Do not move to live capital unless every promotion gate in
  [`live-capital-go-no-go.md`](./live-capital-go-no-go.md) is backed by a
  named artifact.
- If a `STOP` condition triggers, halt new work on promotion and either
  simplify or kill the strategy.

## Evidence Board

Track each item as `TODO`, `DOING`, `DONE`, `BLOCKED`, or `KILLED`.

| Item | Status | Evidence / Artifact | Owner | Notes |
| --- | --- | --- | --- | --- |
| Phase 0 repo audit | DONE | "Notes From Current Repo State" below + PRs #41 #42 | me | Every code-side §3 P0/P1 closed; the two strategy hypotheses (strikes / stop) are deferred to §5.A / §5.B ablation per CONTRIBUTING.md |
| Data inventory | DONE | `docs/data-inventory.md` | me | Verdict 2026-04-30: Phase 1 + 2 BLOCKED on data acquisition (3 of 4 manifest entries TBD); only `trading_calendar` populated. Code is not the bottleneck. |
| Strategy spec freeze | DONE | `docs/STRATEGY-SPEC-v1.md` | me | Frozen 2026-04-30 — sizing/overlays/pre-run checklist added; entry/exit window timestamps deferred to YAML pre-run pins |
| Directional edge test | DONE | `research/reports/directional-edge-v1.md` | me | 2026-04-30 — Simple verdict EDGE_PRESENT (mean +0.0742%) but t-stat=0.88, p=0.382, 95% CI includes zero. Nuanced verdict EDGE_INCONCLUSIVE. 2024 year-slice negative. Continue/kill decision pending. |
| Minimal post-cost control backtest | TODO | `artifacts/control-backtest-v1/` | me | Must be reproducible |
| Trade-expression comparison | TODO | `artifacts/expression-comparison-v1/` | me | Spread vs long call vs ES/MES |
| Ablation matrix | TODO | `artifacts/ablation-v1/` | me | Add only after control exists |
| Stress report | TODO | `artifacts/stress-v1/` | me | 1x / 2x / 3x plus adverse-fill |
| Forward paper tracker | TODO | `artifacts/paper-trading-v1/` | me | Only after prior evidence clears |

## NOW

These are the items worth doing immediately.

- [x] Re-audit `strategy-review.md` Section 3 against the repo and mark each item `fixed`, `open`, `strategy hypothesis`, or `needs test`.
  Output: see "Notes From Current Repo State" below — covers every §3
  item with its closure PR or its Phase 5 ablation slot.
  Exit: no uncertainty about which P0/P1 items are still real blockers.
  **Status: DONE — all code-side §3 P0/P1 closed in PRs #41 / #42; the
  two strategy hypotheses (strikes max-width, stop spot-cross) are
  deferred to `live-capital-go-no-go.md` §5.A / §5.B ablation per
  CONTRIBUTING.md.**

- [x] Create `docs/STRATEGY-SPEC-v1.md`.
  Include: entry window, strike-selection logic, exits, overlays, sizing,
  slippage assumptions, commissions, holdout window, and the exact version of
  the rules being tested.
  Exit: there is one frozen spec that later evidence can reference.
  **Status: DONE 2026-04-30 — spec is frozen in shape; entry/exit window
  timestamps and holdout date pin are deferred to the per-spec YAMLs and
  manifest under the new "Pre-Run Checklist" section. A run touching any
  unpinned input must be marked `status: PRE_FREEZE` and excluded from
  decision rules.**

- [x] Create `docs/data-inventory.md`.
  Include: what SPX, option-chain, ES, VIX, and calendar data you already
  have; source; timestamp resolution; date coverage; storage path; missing
  pieces.
  Exit: we know whether fast validation is blocked by missing data or just by
  missing code.
  **Status: DONE 2026-04-30 — only `trading_calendar` is populated;
  `spx_spot_intraday`, `es_or_mes_intraday`, and `spx_0dte_call_chain` are
  TBD. Verdict: Phase 1 (directional-edge) and Phase 2 (expression
  comparison) are BLOCKED on data acquisition, not on code. Vendor
  candidates listed without commitment; the buy decision lives in a
  separate `docs/data-acquisition-decision.md` (not yet written).**

- [x] Run the fastest falsification test first: directional edge without the
  options wrapper.
  Measure: SPX or ES/MES forward return distribution from the intended
  confirmation time to the intended exit window, sliced by regime if possible.
  Output: `research/reports/directional-edge-v1.md`.
  Exit: one of two outcomes is explicit:
  `EDGE PRESENT` or `NO EDGE`.
  **Status: DONE 2026-04-30 — Simple verdict `EDGE_PRESENT` (mean
  +0.0742% per trade, n=147 over 732 sessions, hit rate 61.9%) but
  the *nuanced* verdict is `EDGE_INCONCLUSIVE`: t-stat = 0.88,
  p = 0.382, 95% CI [-0.0931%, +0.2415%] includes zero. The 2024
  year-slice is negative (-0.0242%) so the apparent edge is also
  somewhat regime-dependent. Underlying is ES front-month (SPX TBD).
  See `research/reports/directional-edge-v1.md` for the full report
  with statistical context and per-year breakdown.**

- [x] Decide whether to continue based on the directional test before building
  more machinery.
  Exit:
  `NO EDGE` -> stop and simplify or kill the project.
  `EDGE PRESENT` -> move to `NEXT`.
  **Status: DONE 2026-04-30 — verdict NO_EDGE.**
  v1 returned `EDGE_INCONCLUSIVE` (mean +0.07%, t=0.88, CI includes
  zero). v1 grid sweep on TRAIN found no candidate with t≥2 at
  meaningful sample size. v2 (0.50%/10:30 + event filter) shaped on
  TRAIN showed promising signal (t=2.92) but VALIDATION (one-shot
  per spec discipline) returned t=1.09 with the +0.84% mean driven
  entirely by a single 2025-04-09 outlier (+9.05% — "tariff pause"
  rally day). Excluding that outlier, validation mean drops to
  ~+0.10% — same flat pattern as v1. v2 fails its own
  `validation_continue_if` rules; does NOT advance to holdout.
  See `docs/STRATEGY-SPEC-v2.md`,
  `research/reports/directional-edge-v2-validation.md`. The
  bullish-continuation hypothesis is killed. Project either pivots
  to a different signal family (v3) or stops.

  **v3 update (2026-04-30, PR #59 + #60)**: pivoted to v3 with VIX
  regime gate. v3 train sweep found high-VIX subset of v2 candidates
  showed t=+3.05 (sharper than v2's +2.92). Froze v3 spec at
  0.50%/10:30 + event filter + high-VIX (prior-day VIX ≥ 14.84
  median split on TRAIN). Validation result IDENTICAL to v2: all 12
  validation trades already had prior-day VIX ≥ 14.84 (validation
  period coincided with April 2025 tariff turmoil; VIX hit 52). New
  v3 outlier rule (`one_outlier_explains_more_than_75_pct_of_mean`)
  fires: 9.05% / 10.13% = 89% from 2025-04-09 alone. v3 KILLED by
  its own decision rules; holdout slot preserved (never touched).
  Three specs (v1, v2, v3) have all failed falsification on
  dataset-v1. Without 2025-04-09, all three return to "borderline
  flat" (~+0.10%). The bullish-intraday-continuation hypothesis is
  dead in three different ways. See `docs/STRATEGY-SPEC-v3.md`,
  `research/reports/directional-edge-v3-validation.md`.

## NEXT

Do these only if the `NOW` block clears.

- [ ] Build the minimal reproducible control backtest.
  Scope:
  fixed entry window, simple strikes, no profit-taking, realistic fees,
  realistic slippage, post-cost ledger.
  Reuse:
  [`src/bull_call/simulate.py`](/Users/warrennenslin/workbench/experiment/ibkr-bull-call/src/bull_call/simulate.py)
  only if helpful for exit-path logic; it is not a full harness.
  Output: `artifacts/control-backtest-v1/`.

- [ ] Implement the smallest useful cost model.
  Minimum required knobs:
  per-path slippage multipliers, commission assumptions, and capital overlay
  on/off.
  Output: config plus one sample run proving the knobs work.

- [ ] Compare trade expressions on the same directional signal.
  Required set:
  bull call spread, long call, and ES/MES proxy.
  Nice-to-have later:
  bull put spread and call butterfly.
  Output: `artifacts/expression-comparison-v1/summary.md`.

- [ ] Produce a single go/no-go checkpoint after the first comparison.
  Exit:
  if the bull call spread is not clearly better on post-cost,
  risk-adjusted terms, do not continue optimizing it.

## LATER

These matter, but they are not the fastest route to truth.

- [ ] Full ablation study across the **four hypothesis axes** named in
  `live-capital-go-no-go.md` Phase 5:
  - **§5.A strike-selection objective** — `max_width_passing` (current),
    `closest_to_target_ratio`, `max_entry_rr`, `max_pop_at_breakeven`,
    `max_exit_efficiency`.
  - **§5.B stop logic** — `no_stop` control, `spot_cross` (current),
    `spot_cross_with_buffer`, `mark_based_hard_stop`,
    `mark_based_with_time_stop`.
  - **§5.C profit-taking** — `no_pt` control, `fixed_pt_at_50_max_profit`,
    `time_gated_pt`, `trailing_pt_from_peak`.
  - **§5.D rolling policy** — `no_roll` control, `single_defensive_roll`,
    `mark_driven_salvage_roll`, `regime_gated_midday_roll`.
  Run **axis-by-axis vs control per §6.4**, not the full Cartesian
  (~500 cells); stops × PT must be tested as joint pairs because they
  interact mechanically. Plus regime / event / confirmation / liquidity
  filters from the original Phase 5 list.
- [ ] Full stress suite with 2x / 3x slippage and worst-path adverse-fill.
- [ ] Drawdown report with and without overlays.
- [ ] Extended benchmark set including bull put spread and call butterfly.
- [ ] Forward paper trading sample of at least 6 months or 60 filled spreads,
  whichever is later. **Paper variant cap: 2-3 max** — paper validates
  the backtest winner (or the 2-3 close rivals when CIs overlap), not
  the full ablation matrix. More than 3 concurrent variants fragments
  the trade pool below §6.5's bootstrap-CI floor and multiplies
  operational overhead. See `live-capital-go-no-go.md` §5.0.
- [ ] Tiered live staircase once paper trading confirms the backtest.

## STOP

If any of these becomes true, halt promotion work immediately.

- [ ] The directional signal is weak or negative after realistic costs.
- [ ] A simpler expression matches or beats the bull call spread.
- [ ] Results depend on midpoint-style fills or collapse under modest slippage.
- [ ] The strategy only looks acceptable when the monthly net-negative gate is
  enabled.
- [ ] Required data does not exist at the resolution needed to test the
  claimed edge.
- [ ] Reproducibility is not possible for a claimed result.
- [ ] A live-critical bug remains open in the actual execution path.

## Immediate Working Order

Use this sequence and do not skip ahead.

1. Re-audit repo blockers.
2. Freeze `STRATEGY-SPEC-v1`.
3. Inventory available data.
4. Run the standalone directional-edge test.
5. Decide continue vs kill.
6. If continue, build the minimal post-cost control backtest.
7. Compare bull call spread against simpler expressions.
8. If still promising, move into ablation and stress work.

## Notes From Current Repo State

After PRs #41 and #42, the `strategy-review.md` §3 audit is effectively
complete:

- `submit_close_market` `phase_timeout` bug — **fixed** (PR #3, line
  239 uses `timeout_s` correctly).
- `SPXW` reconcile guard + signed-cost invariants — **fixed** (PR #3 in
  `cpapi/reconcile.py`, lines 65 / 117-130 / 134).
- NaN P&L crash on unfilled MKT close — **fixed** (PR #42; both
  `monitor_stop` and `_emergency_flatten` check `fill.filled` first
  and emit `spread_close_incomplete` on unfilled, leaving the row OPEN
  for next-instance reconcile rather than corrupting DDB with NaN).
- IAM `dynamodb:Scan` for `monthly_pnl_total` — **fixed** (PR #42,
  added to `state-table-rw` policy).
- `STATE_TABLE` from CFN-written `bot.env` honored in deployed mode —
  **fixed** (PR #41, `load_settings_via_ssm` now merges `os.environ`
  as base; SSM still wins on collision).
- Settlement-on-shutdown corruption — **fixed** (PR #41,
  `_run_one_session` gates `_record_settlements` on
  `_sleep_until(close + 1m)` returning True; OPEN rows untouched on
  shutdown).
- Mid-session restart skipping the rest of the day — **fixed** (PR #41,
  `_next_entry_time` returns today's entry time when mid-session so
  reconcile + monitor + settlement resume).
- Settlement-spot sanity band — **mitigated** (PR #41, rejects values
  outside `[long_strike × 0.5, long_strike × 2.0]`; the deeper fix
  (CBOE SPX SET print) is tracked as Phase 1 deliverable).
- Stale OPEN rows orphaned forever — **fixed** (PR #42,
  `Store.load_stale_open_spreads` + scheduler session-start scan +
  `stale_open_spread` event; manual-settle runbook in README).
- `_parse_bool` typo silently False — **fixed** (PR #42, strict
  whitelist; typos like `STOP_ENABLED=treu` raise `ValueError`).
- Multi-symbol exposed but monitored serially — **locked down** (PR #42,
  `load_settings` rejects `len(symbols) > 1` until concurrent
  monitoring is implemented per `strategy-review.md` §3.8).
- Supply-chain pinning — **done** (PR #41,
  `voyz/ibeam:0.5.12@sha256:7ca5cf...` manifest digest pin;
  `https://astral.sh/uv/0.11.8/install.sh` versioned URL).
- Docs accuracy (`pop_threshold` default, test count, heartbeat
  language) — **done** (PR #42).

Two strategy hypotheses are deferred to backtest validation per
CONTRIBUTING.md (see `live-capital-go-no-go.md` §5.A and §5.B):

- `strikes.py` "widest passing" objective is structurally adverse
  (per `strategy-review.md` §2.1 / §3.5) — must be ablated against
  the four redesign objectives in §5.A.
- `stop.py` spot-cross with no confirmation is whipsaw-prone (per
  §2.4 / §3.9) — must be ablated against the four redesigns in §5.B.

Two more strategy hypotheses live in this same Phase 5 ablation
batch:

- Profit-taking policy — `no_pt` (current) vs the variants in §5.C.
- Rolling policy — `no_roll` (current) vs the variants in §5.D
  (ablate last; interacts with sizing).

There is a small replay helper in
[`src/bull_call/simulate.py`](/Users/warrennenslin/workbench/experiment/ibkr-bull-call/src/bull_call/simulate.py:1),
but it is only a stop-behaviour simulator, not a full research harness.
The harness contract (`live-capital-go-no-go.md` §1.5.2) is still
unbuilt.

**The fastest path is not more bot bug-fixing.** The remaining real
blockers are spec freeze (`STRATEGY-SPEC-v1`), data inventory, and the
directional-edge falsification test. The bot itself is in the best
shape it has been since the project started.
