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

- **Status**: ACTIVE (this branch)
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

- **Status**: QUEUED — NEXT after v7 (per user preference 2026-04-30)
- **Mechanic**: Rank S&P sector ETFs (XLK, XLF, XLE, XLV, XLY, XLP,
  XLI, XLB, XLU, XLRE, XLC) by 6-12 month returns. Hold top 3-5,
  rebalance monthly. Long-only.
- **Theoretical basis**: cross-sectional momentum is the
  most-replicated factor in finance (Jegadeesh & Titman 1993).
  Empirical Sharpe 0.6-1.0 over many decades.
- **Data**: yfinance daily ETF data, free.
- **Test cost**: free, ~1 hour.
- **Deploy cost**: trivial (5-10 ETF trades per month).
- **Caveat**: low-frequency, dull, may not be exciting enough.
  Edge has compressed slightly post-2010.

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

## Order of execution (per user preference 2026-04-30)

1. **v7** (ACTIVE) — short iron condor on SPX 0DTE
2. **v9** (NEXT) — sector ETF momentum (monthly rebalance)
3. **v8** — vol term structure carry (third)
4. **v11** — calendar spreads (fourth)
5. **v10** — pairs trading (fifth)

User preference recorded 2026-04-30: after v7 completes
(KILLED or PROMOTED), the next strategy is v9 sector momentum
— a deliberate pivot away from option structures toward simple
ETF rotation. The remaining three (v8 vol carry, v11 calendars,
v10 pairs) follow.

Each gets a fair shot under the same falsification framework:
shape → validate → holdout. Move to the next only after the prior is
either KILLED or PROMOTED.

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
