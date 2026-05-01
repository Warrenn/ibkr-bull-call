# v9 Sector Momentum — Paper Trading Pilot

## Goal

Run v9 sector momentum (12-1 cross-sectional, top-3 SPDRs, monthly rebalance, equal-weight) in fully-automated paper trading on the existing IBKR paper account for **exactly 90 trading days**, observe live mechanics + tracking error against the backtest simulator, and produce a fixed end-of-pilot report.

## Pre-committed parameters (no scope creep)

| Parameter | Value | Rationale |
|---|---|---|
| Strategy | v9 sector momentum (frozen spec) | Per `research/specs/strategy-spec-v9-sector-momentum.yaml` |
| Pilot length | **90 trading days** (~4.5 months, 4 monthly rebalances) | Pre-committed; do NOT extend or shorten based on intermediate results |
| Account | Existing IBKR paper account from SPX bot | Reuse credentials + IBeam + cpapi stack |
| Initial capital | **$30,000 USD** | $10k / position fits liquid SPDRs cleanly; small enough to be exploratory |
| Allocation | 33.3% / 33.3% / 33.3% across top-3 SPDRs | Equal-weight per spec |
| Rebalance schedule | First trading day of each month, **10:00 ET** | After open volatility settles |
| Signal data | yfinance daily close (free, same as backtest) | Match the backtest's data source for tracking-error fairness |
| Order type | Market orders | Match backtest's `market_orders_at_open` execution model |
| Slippage budget | 10 bps round-trip per rebalance | Match backtest assumption; alert if real fills exceed |
| Stop conditions | (1) cumulative DD < -25% → halt + alert (2) single-day loss > -10% → emergency flatten + alert | Conservative; never auto-resume |
| End-of-pilot date | Start date + 90 trading days | Hard stop; no extension |

## Approach

**Architecture decision**: standalone v9 module within existing `bull_call` package (`bull_call/v9/`), reusing connection/credentials/state infrastructure. NOT a strategy plugin refactor — premature for one new strategy.

**Parallelism assessment**: The work decomposes into 3 file-disjoint units (signal+shadow / state+executor / scheduling+infra), but they share a small set of integration points (`__main__.py` dispatch, CFN compute.yaml, IAM). Worth parallelizing once tests exist; for the initial build I'll run serially given each phase's tests gate the next phase's IBKR calls. If we end up needing a second strategy, that triggers the plugin refactor.

**Reuse from existing SPX bot infrastructure**:
- `bull_call/cpapi/client.py` — IBKR Client Portal Web API session
- `bull_call/ssm.py` + `bull_call/ibeam_ssm_provider.py` — credentials
- `bull_call/calendar.py` — NYSE trading-day calendar
- `bull_call/state.py` — DynamoDB single-table store (extended with v9 records)
- AWS CFN `infra/data.yaml` (DynamoDB) + `infra/network.yaml` (VPC) — unchanged
- AWS CFN `infra/compute.yaml` — extended with v9 entry-point env var

**Net new modules** (in `src/bull_call/v9/`):
- `signal.py` — pull SPDR closes from yfinance, compute 12-1 momentum, return `TargetPortfolio` (top-3 tickers + equal weights). Pure function, no I/O on a passed-in DataFrame.
- `contracts.py` — IBKR CONID lookup for 11 SPDR tickers + SPY benchmark. Cached on first call.
- `executor.py` — given `current_positions`, `target_portfolio`, `account_value`, compute `OrderPlan` (list of BUY/SELL market orders with quantities). Submit via cpapi. Poll fills.
- `state.py` — wraps `bull_call.state.Store` with v9-specific item types: `v9_position`, `v9_fill`, `v9_nav_snapshot`, `v9_pilot_metadata`. Auto-discovery of pilot start/end dates.
- `shadow.py` — runs `research.scripts.sim_sector_momentum` on the same data window we used at rebalance time; produces `expected_top_3` for tracking-error reporting.
- `scheduler.py` — three jobs: monthly_rebalance, daily_nav_snapshot, weekly_summary. End-of-pilot self-disable.
- `__main__.py` — entry point dispatching to v9 if `STRATEGY=v9` env var set; else falls through to existing SPX scheduler.

## Key decisions (with rationale)

1. **yfinance for signal, IBKR for execution.** Free signal source matches backtest exactly. Risk: yfinance can return stale/NaN data on rebalance morning; mitigation: retry 3× over 5 min, fail-soft (skip rebalance, alert) if still bad.

2. **Reuse SPX paper account.** Faster setup, leverages working IBeam infra. Risk: any SPX bot residual orders or positions interfere; mitigation: explicit pre-rebalance check that no SPX positions exist, and use a v9-specific subaccount tag in DynamoDB so state never collides.

3. **Standalone v9 module, not plugin refactor.** Only one new strategy on deck; refactoring for hypothetical N strategies is YAGNI. If v11/v10 reach paper trading, then refactor.

4. **End-of-pilot is fixed date, not metric-conditional.** Per anti-cherry-picking discipline. Re-decide after the report; not during.

5. **Pre-committed stop conditions are catastrophic-only.** -25% cumulative DD is much wider than the backtest max (-16.94%); a stop within backtest range would be ad-hoc tightening. Single-day -10% emergency flatten guards against gap risk we didn't model.

6. **Shadow simulator runs on same data, same time.** Tracking error vs sim is the headline pilot metric. If real fills consistently underperform sim by 50+ bps on the rebalance day, we have a known-microstructure-cost overhead to record for the next decision.

7. **Signal computation lives in `bull_call/v9/signal.py`, NOT in `research/scripts/`.** Production code path is independent of research scripts. Identical math, separate ownership; tests verify they match on a fixed input.

## Implementation steps

### Phase 1 — Signal + shadow (no IBKR; pure-Python)

1. Write tests for `signal.compute_target_portfolio()` covering: known-input cases (rank ties, NaN handling, insufficient lookback returning None, correct top-3 selection).
2. Implement `bull_call/v9/signal.py` to pass tests.
3. Write tests for `shadow.shadow_v9_predictions()` — runs the existing simulator on a date range, returns expected positions for a given rebalance date.
4. Implement `bull_call/v9/shadow.py` to pass tests.
5. Unit-test cross-check: `signal.compute_target_portfolio()` and `shadow.shadow_v9_predictions()` produce identical top-3 on the same input — guards against drift between production and research code.

### Phase 2 — State + rebalance plan (no IBKR; pure-Python)

6. Extend `bull_call/state.py` with v9 record types (or add `bull_call/v9/state.py` wrapper). Tests: round-trip `v9_position`, `v9_fill`, `v9_nav_snapshot`, `v9_pilot_metadata`.
7. Implement `bull_call/v9/state.py` to pass tests.
8. Write tests for `executor.plan_rebalance(current, target, account_value, prices)` — given current positions + target weights + total account value + last-known prices, produce an ordered list of orders (sells first to free capital, then buys). Whole-share rounding. Tests: first rebalance from cash, mid-pilot reweight, no-change rebalance produces empty plan, insufficient cash for a buy emits warning.
9. Implement the planner (no IBKR call yet) to pass tests.

### Phase 3 — IBKR integration (paper account, dry-run first)

10. Implement `bull_call/v9/contracts.py` — CONID lookup for all 11 SPDRs + SPY. Cached on first call. Test against mocked IBKR /iserver/secdef/search responses.
11. Extend `bull_call/cpapi/execution.py` (or add `bull_call/v9/execution.py`) with stock-market-order submission. Existing code is options-only; ETF orders need the secType=STK path and a different `iserver/account/{accountId}/orders` payload. Tests against mocked IBKR responses.
12. **Dry-run end-to-end smoke test**: connect to paper account, look up CONIDs, plan a rebalance, print the order list, do NOT submit. Manually verify against expected output.
13. **Live paper smoke test**: with $0 worth of fake-target weights (e.g., 1 share of XLK), submit one round-trip BUY then SELL through cpapi, confirm fills, write to state. Hand-validate.

### Phase 4 — Scheduling + monitoring + halt

14. Implement `bull_call/v9/scheduler.py` with three jobs. Tests for sleep math, end-of-pilot detection, stop-condition firing.
15. Daily NAV snapshot: pull `iserver/portfolio/{accountId}/positions` + last-trade prices, compute NAV, persist.
16. Weekly summary: read NAV snapshots + simulator shadow + fills, write a Markdown report to S3.
17. Stop-condition checks: run before every order submission; halt on trip and alert via existing CloudWatch heartbeat path.
18. End-of-pilot self-disable: after 90 trading days from `v9_pilot_metadata.start_date`, scheduler exits cleanly without rebalancing.

### Phase 5 — Deploy + smoke + start clock

19. Update `infra/compute.yaml`: add v9 env vars (STRATEGY=v9, V9_INITIAL_CAPITAL=30000, V9_PILOT_DAYS=90), keep SPX disabled. Update IAM for any new DynamoDB partitions.
20. Build + push container, ASG self-replaces.
21. Live paper smoke: trigger rebalance manually, confirm 3 fills, NAV snapshot persists.
22. Set `v9_pilot_metadata.start_date = first trading day after smoke validation passed`. Pilot clock begins.

### Phase 6 — Operate (90 trading days)

23. Weekly summary review (light touch — only read the report, never tune mid-pilot).
24. End-of-pilot report (~Day 91): pilot vs sim tracking error, slippage realized vs assumed, stop-condition events, decide-or-extend (the latter requires creating v9-paper-pilot-2 with prior result frozen).

## Test strategy

| Layer | Framework | Coverage target | Notes |
|---|---|---|---|
| Pure functions (signal, planner) | pytest | 100% branches | No mocks needed |
| State store (v9 records) | pytest + moto for DynamoDB | 100% | Round-trip serialization |
| IBKR cpapi adapters | pytest + responses | All branches; happy + 4 failure modes | No live calls in CI |
| Scheduler day loop | pytest with frozen-time | Sleep math, end-of-pilot, stop firing | |
| Cross-check vs research script | pytest | One golden case | Production signal = research signal on same input |
| End-to-end dry-run | manual | Once before deploy | Paper-account smoke |
| Live paper smoke | manual | Once after deploy | Real round-trip with 1-share orders |

CI gate: 85% coverage (existing project standard) + mypy + shellcheck on infra changes.

## Risks + mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| yfinance returns stale/NaN on rebalance morning | Medium | Retry 3× over 5 min; fail-soft (skip rebalance, alert) |
| IBKR paper API behaves differently from production for ETFs | Low | Live smoke test in Phase 3 catches this |
| ETF order doesn't fill at expected price (illiquid open) | Low (SPDRs are liquid) | Slippage budget alert at 25 bps |
| Existing SPX bot artifacts (orphan orders, residual state) collide | Low | Pre-rebalance assertion: no non-v9 positions exist |
| Cosmic-ray crash mid-rebalance leaves partial fills | Low | Reconcile path on next startup: read positions, reconcile vs intended target, alert if mismatch |
| User pulls the plug ("but it just lost money for 60 days!") | High | Pre-committed pilot length is the rule; results review at Day 91 only |

## Out of scope (explicitly)

- Multi-strategy plug-in framework (deferred until ≥2 strategies want paper trading)
- Live capital deployment (separate decision after pilot)
- Real-time market data subscription on IBKR (not needed; daily-close yfinance is sufficient)
- Intraday rebalancing or stop-loss orders during a position (monthly cadence only)
- Dividend reinvestment (track in shadow as info; ignore in execution since SPY/SPDRs typically rebalance through them)
- Tax-aware harvesting (not relevant for paper)

## Progress

- [x] Phase 1: signal + shadow (5 steps) — completed 2026-04-30 (PR #73 merged)
- [x] Phase 2: state + rebalance plan (4 steps) — completed 2026-05-01
- [x] Phase 3a: IBKR integration offline (contracts + stock execution) — completed 2026-05-01
- [ ] Phase 3b: dry-run end-to-end smoke + live paper smoke (interactive, requires IBKR paper account)
- [ ] Phase 4: scheduling + monitoring + halt (5 steps)
- [ ] Phase 5: deploy + smoke + start pilot clock (4 steps)
- [ ] Phase 6: operate 90 trading days (2 steps)

## Open questions

None — all 5 clarifying questions answered 2026-04-30. Plan is complete pending review and approval.
