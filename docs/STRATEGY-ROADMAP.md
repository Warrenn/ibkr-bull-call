# Strategy Roadmap

After v1-v6 conclusively killed the original SPX 0DTE bull call spread
hypothesis (PR #68: stop loss converts 79% of trades into losses, total
60mo P&L -$63K), we have 5 candidate replacement strategies queued for
sequential testing.

**Rule**: test ONE strategy at a time. Complete the full
shape→validate→holdout discipline (or kill at any step) before moving
to the next. Don't sprawl.

## Status legend

- `ACTIVE` — currently being tested
- `QUEUED` — next in line
- `KILLED` — failed validation; documented in archived spec
- `PROMOTED` — survived holdout; candidate for paper trading
- `LIVE` — running in paper or capital trading

## Strategies

### v7 — Short SPX 0DTE Iron Condor (no stop, monthly cap)

- **Status**: KILLED-BY-DIAGNOSTIC (PR #69, 2026-04-30)
- **Mechanic**: sell wide-OTM call spread + wide-OTM put spread daily
  at 9:32 ET. Risk capped by wing width per side. NO breakeven stop
  (lesson from v6: stop converts ~80% of trades to losses on bull
  call spread; for iron condor the "other leg" rationale also
  doesn't hold). Monthly capital control as the ONLY risk gate.
- **Theoretical basis**: vol risk premium proven real (PR #63:
  implied = 2× realized, t=-45). Short premium structures harvest
  this premium directly. v5's symmetric ATM iron condor at BS-FAIR
  pricing showed ~zero EV; v7 adds skew + bid-ask + the user's
  strike walk to test if microstructure can flip it positive.
- **Data**: existing (ES + VIX + calendar + event calendar, all
  60mo). No additional spend.
- **Test cost**: free.
- **Deploy cost**: low (existing 0DTE bot infrastructure mostly
  reusable).
- **Why first**: directly tests "does removing the stop fix things"
  on a vol-premium-aligned structure. Cheapest. Highest fit with
  what we've built.

### v8 — Volatility Term Structure Carry (VXX / SVXY)

- **Status**: QUEUED
- **Mechanic**: When VIX futures curve is in contango (VIX < VIX3M),
  short VXX (or long SVXY); when in backwardation (stress regime,
  VIX > VIX3M), reverse or stand aside. Hold for days-to-weeks.
- **Theoretical basis**: VIX futures contango is a structural
  property — long-dated futures roll down toward spot. Best-
  documented "vol carry" trade in literature (Whaley, Eraker, etc.).
- **Data**: yfinance free for VXX, SVXY, VIX, VIX3M, VIX9D. No
  spend.
- **Test cost**: free, ~2 hours new backtest framework.
- **Deploy cost**: simple (1 ETF position, rebalance daily/weekly).
- **Caveat**: 2018 "Volmageddon" wiped out XIV in one day; tail risk
  is real. Position sizing + circuit breaker required.

### v9 — Sector ETF Momentum (monthly rebalance)

- **Status**: PROMOTED — paper-trading candidate (PRs #70, this PR, 2026-04-30)
- **Mechanic**: 12-1 cross-sectional momentum on 11 SPDR sector ETFs.
  Hold top 3 equal-weighted, monthly rebalance. Long-only. 10 bps
  round-trip slippage. Frozen as
  `research/specs/strategy-spec-v9-sector-momentum.yaml`.
- **Test result (81mo, 2018-06 → 2026-04)**:
  - Sharpe 1.01, CAGR 16.58%, max DD -16.94%, Calmar 0.98
  - Beats SPY full-window by +0.91% CAGR (n.s., t=0.22)
  - **Train/val/holdout split (60/20/20)**: PASSES all spec gates →
    PROMOTE per `holdout_continue_if`
- **Caveats** (recorded for paper-trading monitoring):
  - Holdout slice underperformed SPY by -4.26% — passes only via the
    \"OR within 5%\" tolerance clause
  - Cross-window check on dataset-v1 60mo window: v9 LOSES to SPY by
    -2.58% CAGR — apparent edge concentrated in 2019-2020
  - Sharpe degraded from 1.83 (val) → 0.86 (holdout)
  - Fails to beat passive SPY DCA on either window
- **Data**: `research/data/dataset-v1/sector_etfs_daily.parquet`
  (yfinance, sha256:03e85be2..., free).
- **Test cost**: free.
- **Deploy cost**: trivial (3 ETF trades per month).
- **Recommended next step**: paper-trade for at least 6 months
  monitoring monthly returns vs SPY before any live-capital decision.
  Regime sensitivity is a real concern.

### v10 — Pairs Trading on Cointegrated Equity Pairs

- **Status**: QUEUED
- **Mechanic**: Identify pairs of correlated stocks/ETFs
  (e.g. XLF/JPM, KO/PEP, MA/V) whose ratio is stationary. Long the
  cheap one + short the rich one when ratio diverges by >2σ from
  historical mean. Exit at mean reversion.
- **Theoretical basis**: classical statistical arbitrage.
  Engle-Granger cointegration → mean-reverting spread.
- **Data**: yfinance free.
- **Test cost**: free, ~2-3 hours.
- **Deploy cost**: medium (long/short equity, margin requirements).
- **Caveat**: edge has eroded substantially as quants competed it
  down. May be unprofitable after retail commissions.

### v11 — Calendar Spreads on SPX (sell front-week, buy 30-60 day)

- **Status**: QUEUED
- **Mechanic**: Sell ATM 0-7-DTE call/put + buy ATM 30-60 DTE same
  strike. Profit from accelerated theta on the short side.
- **Theoretical basis**: vol term structure premium — front-month
  IV typically higher than longer-dated when vol is calm.
  Theta-positive position with bounded risk; long back-month leg
  serves as protection (no breakeven stop needed).
- **Data**: synthetic for testing (BS multi-expiry); real chain data
  ($495+) for production-grade backtest.
- **Test cost**: ~3-4 hours new multi-expiry simulator (free).
- **Deploy cost**: medium (different position management, expiry
  rolls, multi-leg execution).
- **Caveat**: trade frequency is much lower (weekly/monthly cycle vs
  daily 0DTE). Results play out over weeks.

## Order of execution

1. ~~**v7** — short iron condor on SPX 0DTE~~ → KILLED-BY-DIAGNOSTIC (PR #69)
2. ~~**v9** — sector ETF momentum~~ → **PROMOTED** (paper-trading candidate)
3. **v8** (NEXT) — vol term structure carry
4. **v11** — calendar spreads
5. **v10** — pairs trading

v9 is the first strategy in the project to survive the falsification
framework. It is now a paper-trading candidate — NOT a live-capital
deployment. See v9 caveats above; regime sensitivity is a real concern.

Two paths forward (the user can run them in parallel or sequentially):

- **Path A — paper-trade v9** while continuing strategy research.
  Monthly rebalance is low-burden; can be run on the existing project
  infrastructure with minor adaptation away from the SPX 0DTE bot.
- **Path B — proceed to v8** in parallel. Each strategy gets a fair
  shot under the same shape → validate → holdout framework. Move to
  the next only after the prior is KILLED or PROMOTED.

## Lessons carried forward

From v1-v6, the design constraints we now apply to every new strategy:

- **Pin the spec FIRST** in `research/specs/strategy-spec-vN.yaml`
  before any data is touched. No moving thresholds after seeing
  results.
- **Test on full 60mo** with proper train/val/holdout split.
- **Outlier-resistance check** explicit in decision rules: no single
  day > 50% of cumulative mean.
- **Bonferroni-aware** when sweeping parameters (multiple-comparison
  correction on grid sizes > 10).
- **Risk-adjusted metrics required** before promotion (Sharpe ≥ 1.0
  on UNDERLYING signal; expect ≤0.5-1.0 after options frictions).
- **Strategy-faithful simulation required** (real strike-walk + real
  position management, not abstractions of the signal).
- **Microstructure caveats** acknowledged in every report (BS+skew vs
  real chain, bid-ask approximation, no commissions modeled).

## Total project capital so far

- Databento: **$14.12** of $125 free credit (~$110.88 remaining)
- AWS: ~$0.01 S3 storage
- Out-of-pocket: **$0**
- Time: ~65 hours of research + tooling

We have plenty of free credit for additional data acquisition if any
candidate gets promoted to paper trading.
