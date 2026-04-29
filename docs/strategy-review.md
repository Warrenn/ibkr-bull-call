# Strategy Review — SPX 0DTE Bull Call Spread Bot

**Status:** working document, not committed product spec. All thresholds and
rules below are *hypotheses*, not validated truths.

> All exact thresholds in this redesign should be treated as hypotheses, not
> truths. The likely improvements are increased selectivity, better
> reward/risk discipline, removal of event-driven adverse regimes, and exits
> based on the economics of the spread rather than spot alone. But each
> filter, target, and stop must survive ablation testing, walk-forward
> validation, and slippage stress. If a rule does not improve out-of-sample
> expectancy or reduce drawdown robustly, it should be removed.

---

## Section 1 — Executive verdict

The current strategy is a clock-triggered bull-call entry filtered by a
Black-Scholes POP, which is mathematically the risk-neutral probability
already priced into the option. That filter does not contain edge
information by itself.

The strike-selection objective ("widest spread that still passes the cap and
POP") is structurally adverse: holding the long fixed and walking the short
out, you buy more nominal upside but less probability of capturing it, in
exchange for unchanged downside. This is the textbook setup for a
mediocre-expectancy "high probability" trade — small wins paid for by
occasional max-loss days that arrive more often than the BS-implied
distribution suggests.

The strategy is unconditional. A substantial share of trading sessions are
not bullish intraday, so a daily long-bias entry is structurally vulnerable.
**It behaves more like an unconditional daily directional wager than a
robust trading strategy.**

**Verdict.** The current alpha logic likely needs replacement or major
redesign; the infrastructure (DynamoDB state, ASG/CFN, IBeam auth, retry
loop, structured event log) is reusable. Do not put real capital behind the
current design without a hard redesign and ≥12 months of forward-paper data
showing positive expectancy after fees and slippage.

---

## Section 2 — Current strategy weaknesses (top 5)

### 2.1 Strike selection structurally favours bad reward/risk

The algorithm walks short strikes ascending and keeps the *widest* one
passing the cap and POP. Holding the long fixed, widening the short:

- **Max loss** stays approximately at the debit (still bounded by `MAX_LOSS_USD`)
- **Max profit** = `width − debit` grows with width — but in *raw dollars* only
- **Probability** of finishing above `short_strike` (= max-profit zone) shrinks as the short moves away from spot
- **Probability** of finishing below `long_strike` (= max-loss zone) is unchanged

So you are buying *more nominal upside* but *less probability* of capturing
it. The opposite of what you want. A more defensible target is **moderate
width with debit ≈ 0.30–0.45 of width**, where reward/risk is roughly
1.2:1 to 2.3:1 — but those exact numbers are hypotheses to be tested.

### 2.2 The POP filter is not a signal — it is a tautology

`POP = N(d₂)` from Black-Scholes is mathematically equivalent to the
risk-neutral probability of finishing above breakeven, which is exactly
what the option premium already encodes. Using POP ≥ 0.55 as a *filter*
contains no edge information.

To filter for edge you need a probability *different from* what the market
prices: historical-vol-vs-IV mismatches, microstructure signals, or
regime/macro overlays.

### 2.3 The strategy is unconditional and ignores regime

Same setup at 10:30 ET every day, regardless of VIX level, VIX term
structure, prior-day SPX trend, the week's macro calendar, or the session's
opening behaviour. A conditional version of this trade — e.g. only when the
market has demonstrated upward intent in the first 60 minutes, only outside
high-impact event windows — has a better *plausible* chance of carrying
expectancy. **The conditional version is a hypothesis, not a guarantee.**

### 2.4 The breakeven stop is the wrong stop

The stop fires when **spot** crosses below **breakeven**. Two problems:

**(a) Spot vs. mark is the wrong reference.** The trade's economic state is
the spread's *mark price*, not spot. When spot dips below breakeven, the
spread can be worth anything from $0 to most of its max profit — depending
on time-to-expiry, IV, and the path. A spot-crossing stop ignores that the
spread's delta changes with time and moneyness.

**(b) Single-cross trigger creates whipsaw.** A real-world price tick that
prints below breakeven and immediately ticks back is enough to fire the
stop. The "uneconomic" check helps but doesn't prevent the more common
case: a spread that's worth $0.50 takes a one-tick whipsaw, MKT closes at
$0.40 (round-trip fees on top), and the spread would have been worth $2.00
by 4 pm.

A defensible alternative is a **mark-based stop with a confirmation buffer**
(e.g., spread mid below X for ≥30 seconds). Whether this actually improves
expectancy requires backtesting.

### 2.5 The bot has no profit-taking rule

Long 0DTE spreads are highly exposed to late-day gamma reversal. A partial
or full profit-taking rule is worth testing for that reason. **However, the
optimal target for long debit spreads is an empirical question, not a
settled rule.**

The literature on "close at 50% of max profit" is most strongly associated
with **short-premium** strategies (selling iron condors, strangles, etc.),
where premium decay drives expectancy and you exit before negative gamma
becomes asymmetric. Long debit spreads have a different convexity profile:
you've already paid for the gamma, so leaving early gives away the upside
you paid for. The right target — 25%, 50%, 75% of max profit, or trailed
from a peak — is something the backtest must answer per regime.

---

## Section 3 — Code / implementation issues

### 3.1 `submit_close_market` will crash on first invocation (P0 BUG)

`src/bull_call/cpapi/execution.py:219`:

```python
fill = _await_fill(client, order_id, phase_timeout)   # NameError
```

`phase_timeout` is local to `submit_entry_lmt`. Should be `timeout_s`. No
test catches this because every test that invokes the close path injects a
fake `submit_close` callable. **First time the stop fires in production =
exception, position left open.** Fix: `_await_fill(client, order_id, timeout_s)`.

### 3.2 `flatten_unmatched_leg` and reconcile path

`flatten_unmatched_leg` side inversion is correct for the IBKR
signed-quantity convention. The risk is upstream in `reconcile.py`:
`OptionContract.expiry` is populated from `lastTradingDay` without
verifying SPX vs SPXW. SPX (AM-settled monthly) and SPXW (PM-settled
weekly/0DTE) have different settlement semantics. **Add a hard
`tradingClass == "SPXW"` check in reconcile**; refuse to adopt anything
else.

### 3.3 Settlement-time assumption hardcoded to 4 pm

`years_to_session_close` uses NYSE 4 pm close (correct for SPXW
PM-settlement). The brittleness is in `reconcile.py` — if it ever picks up
an SPX monthly, the assumption breaks silently. Also gated by 3.2.

### 3.4 The retry loop's strike re-selection is not time-adaptive

`attempt_until_filled` correctly re-fetches the chain on each retry, but
the strike-selection algorithm uses the same parameters at 10:30 as at
12:30. A breakeven 5 points OTM is reasonable for a 5-hour position; it is
aggressive for a 3.5-hour position. **Strike selection should be
time-adaptive** (target delta or debit/width as a function of remaining
session time). This is a strategy-level fix, not just a code fix.

### 3.5 The "highest short strike that still passes" objective is the wrong objective (also a §2.1 problem)

In `strikes.py:_find_short_index` the loop deliberately maximises width
subject to constraints:

```python
if debit_ok and pop_ok and viable:
    candidate = (j, net_debit, pop)   # always overwrites — keeps the LAST (widest)
else:
    break
```

Should select for **target reward/risk ratio** (or target delta) at a
specified band, not max width. Whether the specific bands chosen carry edge
is itself a hypothesis.

### 3.6 The reconcile path assumes IBKR `avgCost` convention

```python
entry_debit = abs(long_avg) - abs(short_avg)
```

Correct for IBKR's signed convention (long positive, short negative), but
the `abs(...)` calls hide bugs. If IBKR ever emits an unsigned cost,
`abs(short_avg) > 0` would *subtract* the short premium when it should
*add* it. **Add a runtime invariant that `long_avg > 0 AND short_avg < 0`,
log + skip on violation.**

### 3.7 `time.sleep` in `attempt_until_filled` blocks signal handling

`sleep_fn=time.sleep` means a 60-second soft retry will not respond to
SIGTERM during the sleep. The scheduler's `_sleep_until` correctly uses
`Event.wait` with the shutdown event. The retry loop should do the same.
Pass `_stop_event.wait` as the sleep function in production, mocking it
with `lambda _: None` in tests.

### 3.8 `_monitor_open_spreads` serialises monitor loops across symbols

With one symbol (SPX) this is fine. With multiple symbols, the second
symbol's stop monitor starts only when the first one ends. Flag for future
multi-symbol work.

### 3.9 `stop.advance` arms only on `spot >= breakeven` (no buffer)

Strict `>=` means a spot exactly at breakeven arms the stop, then the very
next tick below breakeven fires it. With penny-tick SPX index data this can
fire on the noise of the 250 ms WebSocket tick stream. Consider arming on
`spot >= breakeven + buffer`, where buffer is a small fraction of width.
Or arm on a time-weighted condition (spread mark > threshold for >X
seconds). Both are hypotheses to test.

### 3.10 Stop polling cadence is not appropriate for 0DTE risk management

A 30-second mark-poll is too slow for late-day gamma. Better: **event-driven
quote updates** (subscribe to bid/ask change events on each leg) when the
gateway/library supports it; otherwise poll every 1–5 seconds with
debounce/confirmation logic to avoid whipsaw.

### 3.11 Hardcoded thresholds throughout

Targets like delta 0.45, debit 35–55% of width, stop at 50% of debit,
14:30 ET time stop are all baked in. **Parameterise them**, expose via SSM,
and stress-test their values in the backtest. Treat the present numbers as
starting hypotheses for the ablation study, not final.

---

## Section 4 — Proposed redesigned strategy (hypothesis)

The redesign moves the bot from "I will be long every day" to "I have a
specific bullish setup; I trade it when it triggers, otherwise I sit out."
Frequency drops materially. **Whether this redesign actually carries
positive expectancy is a hypothesis to be validated by backtest before any
money is committed.**

### 4.0 Be explicit about which setup type is being implemented

The current bot conflates three different setups under one ruleset:

1. **Bullish continuation** (today's design intent — looking for upside continuation in an already-bullish tape)
2. **Bullish reversal** (entering a bull call after a downside move, betting on bounce)
3. **Event-driven trend day** (entering after FOMC/CPI in confirmed direction)

Each has a different statistical profile and different filter requirements.
Do not pretend one ruleset covers all three. Pick one, name it, and build
its filters specifically. The redesign below targets **bullish
continuation** explicitly.

### 4.1 Regime filter (no-trade if any fail) — hypotheses

| Filter | Starting threshold | Hypothesis (must validate) |
|---|---|---|
| `VIX` level | 14 ≤ VIX ≤ 25 | Outside this band, premiums or gamma make the structure uneconomic. |
| `VIX/VIX3M` term structure | < 1.0 (contango) | Backwardation indicates stress regime; bullish setups likely underperform. |
| Prior 5-day SPX return | > -3% | Prevents fading into a deteriorating tape. |

Some sensible candidates to test alongside or instead:

- VIX 5d change > +30% (rising-fear filter)
- VVIX above its rolling 6-month median
- SPX 30d realised vs. front-month IV ratio

These filters are sensible candidates that *may* improve expectancy by
removing adverse regimes, but they must be validated out of sample. The
specific bands above are starting points for ablation, not gospel.

### 4.2 Event filter (no-trade days) — hypothesis

Hard skip on these calendar days, looked up from a weekly-refreshed CSV:

- FOMC announcement days (the day, not just the hour)
- CPI, NFP, PPI, Core PCE release days
- OPEX Friday (third Friday): gamma-driven pin risk distorts 0DTE
- Trading day before Thanksgiving and the day after (low volume, abnormal slippage)
- US half-days

**Hypothesis**: these days have known fat-tailed distributions; skipping
them removes high-variance outcomes without giving up much expectancy.
Whether the gain materialises in your live conditions is empirical.

### 4.3 Intraday confirmation filter (replacing slow-moving filters)

Replace the previous draft's reliance on slow-moving filters (200d SMA,
prior-week trend) with intraday structure filters that match a same-day
trade:

| Filter | Hypothesis |
|---|---|
| **Spot above session VWAP** | Buyers in control intraday |
| **ES above its opening 30-minute high** | Confirmation in the lead-time future |
| **Cumulative tick or NYSE breadth positive at 10:30 ET** | Broad participation, not narrow leadership |
| **Opening range (9:30–10:00) directional** (close > open of OR) | First hour was a trend, not noise |
| **5m EMA > 20m EMA at confirmation** | Short-term trend up |

Recommendation: pick **at most 2–3** of these in any single variant; do not
stack them all (overfit risk). Run an ablation to find which combination
holds out of sample.

### 4.4 Realised-volatility / range filter (new)

Don't only filter on VIX (forward-looking IV); also filter on the session's
realised structure so far:

- **Opening range size relative to the 20-day average OR**: too small = sleepy day, no follow-through; too large = exhaustion / fade risk.
- **Realised 5-min volatility since 9:30**: above some threshold = chop, below = trend.
- **First-hour directionality**: if the high and low of the first hour are >0.5% apart with no clear close-near-the-high or close-near-the-low pattern, mean-reverting tape — skip.

These are intraday-realised analogues of regime filters; whether they add
expectancy on top of the daily regime filters needs ablation testing.

### 4.5 Liquidity-quality filter (new) — non-negotiable

Regardless of strategy edge, bad liquidity can ruin it. Reject the trade if:

- Bid-ask spread on either leg > some maximum cents (e.g. 0.20 for 5-wide)
- Bid-ask spread on the combo > some % of the proposed debit (e.g. 8%)
- Top-of-book size on either leg below a minimum (e.g. 5 contracts bid + 5 ask)
- Quote staleness >2 seconds, or crossed market (bid > ask), or wide-locked

These are not optional — failing any of them invalidates the price you'd be
trading at and turns even a positive-edge strategy into a slippage casino.

### 4.6 Strike selection (replacing current algorithm) — hypothesis

Replace "widest passing" with **target delta + reward/risk band**:

- **Long leg**: call closest to **delta ~0.45** (slightly ITM) — *starting hypothesis*
- **Short leg**: call at long_strike + width, where width is chosen so that **net debit lands in 35%–55% of width** — *starting hypothesis*
- **Hard floor**: reject if max_profit/max_loss < 0.70
- **Hard ceiling**: still enforce `debit × 100 ≤ MAX_LOSS_USD`. If the dollar cap binds before the ratio cap, *no trade today*.

All of these numbers (delta band, debit/width band, R:R floor) are
parameters in the ablation study, not fixed rules.

### 4.7 Entry pricing — hypothesis

A deterministic ladder rather than a single reprice:

- Limit at **mid − 1 tick**. Wait `dwell_sec` (starting 90 s).
- If unfilled, reprice to **mid**. Wait `dwell_sec`.
- If unfilled, reprice to **mid + 1 tick** (cross half the spread). Wait `dwell_sec`.
- If still unfilled, **cancel and skip** — do not chase past mid + 1 tick.
- After `entry_window_end_et`, do not initiate.

Whether crossing past mid is ever expectancy-positive is itself something
to test.

### 4.8 Stop logic — hypothesis (mark-based, with confirmation)

**Stop on spread mid, not spot.** The spread *is* the position; its mark is
what you'd realise on a close. Spot-vs-breakeven ignores time decay and IV
moves.

- **Hard mid stop**: close MKT if spread mid ≤ entry_debit × *stop_pct* for at least *stop_confirm_sec*. Starting hypothesis: 50% and 60 s.
- **Time stop**: if at *time_stop_et* (starting hypothesis: 14:30 ET) the spread is below entry_debit × *time_stop_pct* (starting hypothesis: 75%), close MKT.
- **No spot-crossing stop.**

The current breakeven-cross logic should be removed. Whether the *mid* stop
beats no-stop / different thresholds is a hypothesis. The ablation should
include a "no-stop" variant as a control.

### 4.9 Profit-taking — hypothesis only

Test, but do not assume. A partial or full profit-taking rule is worth
testing because long 0DTE spreads are highly exposed to late-day gamma
reversal. However, the optimal target for long debit spreads is an
empirical question, not a settled rule.

Variants to test:

- No profit-taking (control)
- Close at 50% of max profit
- Close at 75% of max profit
- Trail from peak (lock in 50% once 75% reached)
- Time-decay exit only (close at *time_stop_et* regardless of P&L)

Pick whichever wins out of sample. **Do not adopt the 50% MP rule by
default — that rule is associated with short-premium structures, not long
debit spreads.**

### 4.10 Final-bar handling — hypothesis to test

A hard close is reasonable as a risk-control candidate, but it should be
tested against holding to settlement. Test:

- Hard close at 15:45 ET
- Hard close at 15:50 ET
- Hold to SPXW cash settlement (4:00 pm print)

Choose based on realised expectancy and tail-risk tradeoff. SPXW
PM-settlement removes assignment risk but exposes you to the closing-print
move; a 15:45 close removes settlement-print uncertainty but pays exit
slippage.

### 4.11 Position sizing and kill switches (account-aware)

Replace `MAX_LOSS_USD` per-trade with account-size-aware sizing:

- `max_risk_per_trade = min(MAX_LOSS_USD, 1% × account_NLV)`
- **Weekly cumulative-loss cap**: 3% of NLV. If reached, no new trades that week.
- **Monthly drawdown cap** from peak NLV: 6%. If reached, no new trades for 30 days.
- **Monthly net-negative gate (capital overlay, not an edge mechanism)**: track realised net P&L for the current calendar month. If month-to-date realised P&L falls below $0 at any point, disable new entries for the remainder of that calendar month. Existing open positions continue to be managed and closed under the normal exit rules (R23–R28 in §7). Trading resumes automatically on the first trading day of the next calendar month, with the month-to-date realised P&L counter reset to 0.

  > **This gate is a capital-allocation rule, not evidence of edge.** It is
  > extremely path-dependent: a single small early-month loser can shut the
  > system down for weeks. To prevent confusing capital control with alpha,
  > **all backtests and walk-forward tests must be run both with and
  > without this gate enabled** (see §6.5). If the strategy looks profitable
  > only with the gate, the strategy itself does not have edge — the gate
  > is just limiting damage.

Why this gate is useful even with the drawdown cap above:

- The drawdown cap is *peak-relative* and triggers on a magnitude (6%) — it lets you stay in the market through a slow grind toward zero so long as no single window crosses the cap.
- The net-negative gate is *period-bounded* and triggers on *direction* (realised < 0) regardless of magnitude. If the month is a losing month, even by $50, you stop. The next month is a clean slate.
- Combined: the drawdown cap protects against catastrophic single-window losses; the monthly net-negative gate enforces "don't have losing months — and if you do, don't keep trading them." Either is sufficient on its own; together they bracket different failure modes.

These are kill switches, not edge mechanisms. They limit the cost of being
wrong — they don't make the strategy right.

### 4.12 Benchmark requirement (must address before deploy)

Any redesigned strategy must outperform simpler bullish intraday
implementations on a risk-adjusted, post-cost basis. Include in the backtest
suite:

| Benchmark | Description |
|---|---|
| **No-trade baseline** | Cash returns the equivalent buying power |
| **Long-call-only on uptrend days** | Same regime + confirmation filters but a single long ATM call (no spread) |
| **Bull put spread** | Selling premium with the same directional bias and same regime filters |
| **Call butterfly** | Same directional view with a higher-precision payoff |
| **ES micro futures directional proxy** | Same intraday confirmation filters, but trading MES contracts |

If the redesigned bull-call spread does not beat all of these on
risk-adjusted, post-cost expectancy, the complexity is not justified — pick
the simpler benchmark instead.

---

## Section 5 — Conservative / Balanced / Aggressive variants

All numbers are starting hypotheses. The only meaningful difference between
variants is how strict each filter is and how big the position is — they all
share §4.5 (liquidity), §4.8 (mid stop), §4.10 (final-bar to test), §4.11
(sizing), and §4.12 (benchmark requirement).

### 5.1 Conservative

- **Regime**: VIX 14–22 (tighter), VIX/VIX3M < 0.95, prior 10-day SPX > 0
- **Confirmation**: spot > VWAP AND spot > OR_high held for ≥15 min AND ES > opening 30m high
- **Strikes**: target delta 0.40 long, debit ≈ 50% of width (R:R ~1:1)
- **Entry**: limit at mid − 2 ticks; one reprice to mid − 1 tick; cancel
- **Stop**: 60% of debit, 60 s confirm
- **PT**: tested per §4.9; default to no PT, hold to time-stop
- **Frequency hypothesis**: 4–8 trades / month
- **Strengths**: minimum capital at risk, regime-aware, strict on entry quality
- **Failure modes**: misses long stretches of bullish trend (regime too narrow), commission drag if frequency drops to 3–4/month, overfit risk in the OR-hold-time and confirmation-stack
- **Setup type**: bullish continuation (strict)

### 5.2 Balanced (recommended starting point)

- **Regime**: §4.1 starting hypotheses
- **Confirmation**: spot > VWAP AND 5m EMA > 20m EMA
- **Strikes**: target delta 0.45 long, debit 35–50% of width
- **Entry**: §4.7 ladder
- **Stop**: 50% of debit, 60 s confirm
- **PT**: tested per §4.9
- **Frequency hypothesis**: 8–12 trades / month
- **Strengths**: enough trades for statistical significance, R:R closer to 1:1, full §4 ruleset
- **Failure modes**: cluster of 3–4 losers in a transitional regime where VIX is mid-range but realised vol is jumpy; commission drag if entry repeatedly cancels at mid − 1 tick
- **Setup type**: bullish continuation (relaxed)

### 5.3 Aggressive (disciplined)

- **Regime**: VIX 12–28 (widest), VIX/VIX3M < 1.05
- **Confirmation**: spot > VWAP (no momentum filter)
- **Strikes**: target delta 0.50 long, debit 30–45% of width (R:R 1.2–2.3:1)
- **Entry**: limit at mid; one reprice to mid + 1 tick; cancel
- **Stop**: 50% of debit, 60 s confirm; *additionally* close MKT if VIX rises >15% intraday from entry print
- **PT**: tested per §4.9 (lean toward 65% MP if PT helps; control = no PT)
- **Frequency hypothesis**: 12–16 trades / month
- **Strengths**: best $/trade if expectancy is real, more efficient capital deployment
- **Failure modes**: requires the highest hit-rate-vs-R:R balance — most fragile to overfit; deepest single-trade losses; needs the strictest discipline on event-day filter (one FOMC slip-through can erase a month)
- **Setup type**: bullish continuation (loose)

**Recommendation: start with Balanced.** Run paper trading for at least
the **promotion-gate sample** below, measure expectancy, then choose
direction based on data.

**Promotion-gate sample (replaces the earlier "3 months" rule):**

Three months is too short for a strategy that trades ~8–12 times per
month and is governed by a monthly net-negative gate (R9) that can
truncate a calendar month early. A 3-month sample can easily produce
fewer than 30 actually-filled trades, of which a single benign or
adverse regime can dominate the result.

The promotion gate must be **both** a time floor **and** a trade-count
floor:

| Variant (frequency) | Time floor | Filled-trade floor |
|---|---|---|
| Conservative (~4–8 / mo) | ≥12 months | ≥80 filled spreads |
| Balanced (~8–12 / mo) | ≥9 months | ≥80 filled spreads |
| Aggressive (~12–16 / mo) | ≥6 months | ≥80 filled spreads |

Whichever floor is hit *later* governs promotion. Skipped-trade days do
not count toward the trade floor. A "fast" month that happens to fill
100 spreads in 30 days does **not** waive the time floor — regimes
matter as much as sample size, and you need to see at least one full
calendar quarter rotation in the equity curve.

---

## Section 6 — Backtesting & validation plan

### 6.1 Data needed — two explicit research modes

The live rules now depend on **second-level dynamics**: 1-second monitoring
(`monitor_poll_sec=1`), a 15-second quote-grace window
(`monitoring_quote_grace_sec`), a 60-second blind-window flatten
(`monitoring_quote_max_blind_sec`), a 60-second stop confirmation
(`stop_confirm_sec`), and the aggressive variant's intraday VIX-jump rule.
Validating those at minute granularity would silently mis-model the most
risk-relevant behaviour. Therefore the validation pipeline runs in two
declared modes — and **a rule that lives in HRM-only territory cannot be
deployed live until it has been validated under HRM**.

#### 6.1.1 High-Resolution Research Mode (HRM) — required for full validation

- **SPX 1-second or tick-level spot data**, ≥36 months (CBOE DataShop,
  Polygon options + index, Algoseek, or equivalent; paid, expensive)
- **SPX 0DTE option chain snapshots at ≤5-second granularity** for the
  same window, including bid/ask/size on each leg so executable_credit
  (R23) is reconstructible
- **VIX, VIX3M, VVIX intraday at ≤5-second granularity** (especially
  required for the aggressive variant's VIX-jump rule)
- US economic-release calendar with exact times (FOMC, CPI, NFP, PPI, PCE)
- OPEX dates, NYSE trading calendar
- Realised vs. implied vol day-by-day

HRM is the only mode in which R23a (data-outage flatten), R24
executable-credit confirmation, R25 stop-on-executable, R26 time-stop on
executable, and the aggressive-variant VIX-jump rule can be honestly
backtested. Anything ablation-tested at HRM and shipped is grounded.

#### 6.1.2 Coarse Research Mode (CRM) — explicit fallback if HRM is prohibitive

If second-level intraday options/vol data is not feasible to procure,
research can proceed in CRM, **but with the explicit understanding that
the live rule cadence is slowed to match the available data**:

| Live rule (HRM) | CRM substitute |
|---|---|
| `monitor_poll_sec = 1` | `monitor_poll_sec = 60` |
| `monitoring_quote_grace_sec = 15` | `monitoring_quote_grace_sec = 300` (5 min) |
| `monitoring_quote_max_blind_sec = 60` | `monitoring_quote_max_blind_sec = 900` (15 min) |
| `stop_confirm_sec = 60` | `stop_confirm_sec = 300` (5 min) |
| Aggressive-variant VIX-jump rule | **disabled in CRM** |

CRM gives you a coarse expectancy signal on the entry/regime/sizing
filters, but it cannot validate the fast-feedback risk-management
machinery. Anything that depends on HRM behaviour (R23a, executable-credit
exits, VIX-jump) **must not be deployed live based on CRM-only evidence**;
it must either (a) be tested under HRM before going live, or (b) run live
on paper for the full promotion-gate sample defined in §5 with explicit logging of the events that would
have triggered, before being trusted with real capital.

Mixed approach: CRM for entry/filter ablation (cheap), HRM for the
post-entry monitor block (expensive but bounded — only need data when
the bot is actually in a position).

CRM data fall-back source: end-of-day chain snapshots augmented with
CBOE's published 0DTE summaries; minute SPX bars; daily VIX. Cheaper, but
explicitly partial.

### 6.2 Cost accounting (applies everywhere)

> **All realised P&L metrics, gates, and invalidation checks must use net
> P&L after commissions, fees, and modelled slippage.** This includes the
> per-trade expectancy in §6.3 below, the monthly net-negative gate (R9),
> the weekly loss cap, and every invalidation criterion in §6.6. There is
> no "gross P&L" anywhere in the validation pipeline — every reported
> number is post-cost.

A **single** per-trade slippage assumption is **not enough** for this
strategy. Different exit paths have radically different fill behaviour;
modelling them with one number can materially overstate edge. The
validation pipeline must use a **per-path slippage model** with separate
calibrations:

| Path | Default slippage assumption (starting hypothesis) | Adverse-fill stress |
|---|---|---|
| **Entry — combo LMT ladder** (R19) | mid → mid+1 tick → cancel; assume the average filled order is at mid + 0.5 tick (3 cents per spread, $3/contract) plus 1× combo bid-ask | mid + 1.5 ticks; full ladder + miss-rate stress |
| **Optional stop / time-stop exit** (R25, R26) | combo MKT crossing the spread by 1 full bid-ask width | crossing 2× the bid-ask width; spreads can widen during the move that triggered the stop |
| **Profit-taking exit** (R24) | combo MKT at mid + 1 tick (better fills when the market is moving for us) | combo MKT at mid (less favourable than typical PT fills) |
| **Hard close (R28)** | combo MKT crossing 1 bid-ask width | crossing 2–3× the bid-ask width; closing-print volatility regularly produces worse fills than mid-session |
| **Leg-out flatten (R22)** | single-leg MKT crossing 1× that leg's bid-ask | crossing 2× that leg's bid-ask; orphan legs are usually OTM/cheap and have wide quoted markets |
| **Data-outage emergency flatten (R23a)** | combo MKT but assume the *worst* of the last-known + a 5% adverse move on the underlying | full combo bid-ask + an additional adverse move equal to the largest 1-second move observed in the prior 60 s; this is the path most likely to produce ugly fills |

**Adverse-fill stress on the worst path.** Beyond the per-path stress
above, run a separate scenario where the *single most-frequent loss
path* is assumed to fill 50% worse than the default. For most variants
this is the optional stop (R25) or the hard close (R28). If the
strategy's edge survives a 50% worsening of fills on that single
critical path, you have a robustness margin; if it doesn't, the edge is
a fill-quality artefact.

**Commissions and exchange/regulatory fees** are added on top of
slippage, per leg, per direction. Default model: $0.65 + ~$0.20 SPX
options exchange/reg fees per contract per leg, on entry AND exit; for a
2-leg combo that's ~$3.40 round-trip, plus any leg-out flatten fees.

### 6.3 What to measure

Per trade and per period:

- Expectancy ($/trade, **post-cost using the per-path slippage model in §6.2**, not a single generic slippage figure — each path's realised fills are scored against the model defined for that path: entry-ladder, optional stop, PT, hard-close, leg-out flatten, data-outage emergency flatten)
- Hit rate
- R-multiple (P&L / risk per trade)
- Average winner / average loser
- Max consecutive losers
- Max single-trade loss
- Max drawdown from peak NLV
- Time underwater
- Sharpe, Sortino, Calmar

Per regime / segment:

- VIX bucket (low/mid/high)
- Day of week
- Macro release proximity
- Post-FOMC, pre-FOMC week
- OPEX week vs non-OPEX
- Realised-vol bucket (calm vs choppy)

### 6.4 Ablation study (most important addition)

> **Run an ablation study on every filter and exit rule. No rule stays
> unless it improves out-of-sample expectancy, drawdown, or robustness
> under slippage stress.**

Procedure:

1. Establish a *control* configuration: minimal rules (entry on time, fixed
   strikes, no stops, no PT).
2. For each candidate filter / exit rule independently, measure the
   marginal contribution by adding it to the control and re-running.
3. Then test in pairs and triples — interactions matter (e.g. VIX filter
   may help only when combined with intraday confirmation).
4. **Remove any rule that improves backtest appearance but not
   out-of-sample expectancy** under the walk-forward methodology in §6.5.
5. Prefer fewer rules. A 3-rule strategy that beats a 7-rule one out of
   sample is the keeper.

This is the single most important section of the validation plan. It is
what prevents the redesign from collapsing into elegant overfit.

### 6.5 Validation methodology

- **Walk-forward**: split data into rolling 12-month-train / 3-month-test windows. Strategy parameters tuned on train; evaluated on never-seen test window. Stitch the test windows for the equity curve.
- **Hold-out**: keep the most recent 6 months *completely out* until final acceptance.
- **Spec freeze (no peeking)**: before the hold-out is *ever* touched, the
  full ruleset must be frozen — every threshold, every objective, every
  filter, every kill switch. The frozen spec is committed to a versioned
  document (`STRATEGY-SPEC-v{N}.md` or git tag `spec-vN`) with a change
  log. The hold-out is then evaluated *once*. Any change to the strategy
  *after* hold-out evaluation — even tightening a threshold by a tick —
  creates a new version (`v{N+1}`), invalidates the prior hold-out, and
  requires either: (a) a fresh untouched hold-out window, or (b) a fresh
  forward-paper period of full promotion-gate length (per §5). Every
  iteration that "peeks" and tunes turns the hold-out into a tuning set;
  this rule prevents that silently happening.
- **Bootstrap CIs**: per-trade P&L resampled 10 000 times → 95% CI on annual return. If lower bound ≤ 0, edge unproven.
- **Slippage stress**: stress each *per-path* slippage model (§6.2) at 1×, 2×, 3×; PLUS the dedicated adverse-fill stress on the single most-frequent loss path (50% worse). Edge that vanishes under any of these isn't real.
- **Commission stress**: $0.65, $1.00, and $1.30 per leg. Some brokerages double over time.
- **Benchmark comparison**: §4.12 — the redesign must beat all listed benchmarks on the hold-out, not just on training data.
- **Capital-overlay separation**: every backtest and every walk-forward run must be reported **both with and without** the monthly net-negative gate (R9). If the strategy looks edge-positive only when the gate is on, the strategy itself is not edge — the gate is just truncating losing months. The "without gate" run is what tests alpha; the "with gate" run is what tests realised drawdown for the deployed configuration.

### 6.6 Invalidation criteria

The strategy is **dead** if any of these hold on the hold-out:

- Net expectancy ≤ $0/trade after fees, commissions, and modelled slippage
- Drawdown > 15% after costs
- Walk-forward annual return (post-cost) negative in any 1-year window
- VIX-bucket P&L concentrated in a single regime that's about to expire
- Bootstrap 95% CI on annual return crosses zero
- Risk-adjusted return ≤ **3-month T-bill** on the same window (the appropriate
  cash benchmark — this strategy is mostly *in cash*, so cash + a hurdle is
  the floor, not buy-and-hold equity)
- Worse risk-adjusted return than any of the §4.12 benchmarks (long-call-only,
  bull-put, fly, ES proxy) — these are the *same-buying-power, same-view*
  alternatives that justify the complexity

> **Note on SPX buy-and-hold.** Long-only equity is a *different exposure
> profile* (always-on, full beta), not a same-buying-power alternative to a
> mostly-cash intraday options strategy. Use SPX buy-and-hold as a
> *context* benchmark to understand opportunity cost — not as a hard
> disqualifier. The hard disqualifiers above are cash + the §4.12
> same-view alternatives.

---

## Section 7 — Final implementation-ready rule set

Numbered, testable, deterministic. **All thresholds are starting
hypotheses.** Implement these directly, but parameterise them via SSM and
treat the values as the first iteration of an ablation, not the answer.

```text
SETTINGS (SSM, all parameterised):
  variant                    = "balanced"   # conservative | balanced | aggressive
  account_nlv_usd            = <fetched daily from IBKR account summary>
  per_trade_risk_pct         = 0.01
  weekly_loss_cap_pct        = 0.03
  monthly_dd_cap_pct         = 0.06
  monthly_stop_on_negative_pnl = true
  monthly_reset_mode           = "calendar_month"

  # Regime — starting hypotheses
  vix_min, vix_max           = 14, 25
  vix_term_struct_max        = 1.0
  prior5d_spx_min            = -0.03
  required_above_session_vwap= true
  event_skip_days_csv        = "s3://.../event_calendar.csv"

  # Liquidity (NON-NEGOTIABLE per check, but rechecked each polling iteration)
  max_leg_bidask_cents          = 0.20
  max_combo_bidask_pct_debit    = 0.08
  min_top_of_book_size          = 5
  max_quote_age_sec             = 2
  persistent_illiquidity_minutes = 30   # only after this much continuous failure do we STOP for the day

  # Window
  entry_window_start_et      = "10:30"
  entry_window_end_et        = "13:00"
  no_new_orders_after_et     = "15:30"
  hard_close_at_et           = "15:50"   # hypothesis to test against settlement

  # Strikes — starting hypotheses (R16 / R17)
  long_target_delta          = 0.45
  long_delta_band            = 0.05   # search ±0.05 delta around target -> ~3-5 longs
  debit_to_width_min         = 0.35
  debit_to_width_max         = 0.55
  reward_to_risk_min         = 0.70
  candidate_widths           = [5, 10, 15, 20, 25, 30]   # SPX point widths to scan
  spread_selection_objective = "closest_to_target_ratio"
       # one of:
       #   "closest_to_target_ratio"  (default; debit/width near band midpoint)
       #   "max_entry_rr"             (true entry max-profit / max-loss)
       #   "max_pop_at_breakeven"     (BS POP at the spread's breakeven)
       #   "max_exit_efficiency"      (LIQUIDITY proxy, NOT entry payoff —
       #                               formerly mislabelled "max_executable_rr")

  # Entry pricing
  entry_ladder_steps         = ["mid-1tick", "mid", "mid+1tick"]
  entry_ladder_dwell_sec     = 90

  # Stop / time stop / PT (all hypotheses)
  stop_pct_of_debit          = 0.50
  stop_confirm_sec           = 60
  time_stop_et               = "14:30"
  time_stop_pct_of_debit     = 0.75
  pt_enabled                 = false       # ablation: test true/false separately
  pt_pct_of_max_profit       = 0.50

  # Monitoring
  monitor_poll_sec                  = 1     # event-driven preferred; 1–5s fallback
  # Open-position data-outage fail-safe (R23a)
  monitoring_quote_grace_sec        = 15    # after this stale, start reconnect attempts
  monitoring_reconnect_max_attempts = 3
  monitoring_quote_max_blind_sec    = 60    # total blind window before emergency flatten
```

```text
DAY-START PROCEDURE (~9:25 ET):
  R1.  Fetch account NLV, regime indicators (VIX, VIX3M, prior5d SPX).
  R2.  Look up event_calendar for today.
  R3.  If today is in event_skip_days: log event=regime_skip(reason="event"), STOP.
  R4.  If is_half_day(today): log event=regime_skip(reason="half_day"), STOP.
  R5.  If !(VIX in [vix_min, vix_max]): log event=regime_skip(reason="vix_bounds"), STOP.
  R6.  If VIX/VIX3M >= vix_term_struct_max: log event=regime_skip(reason="backwardation"), STOP.
  R7.  If prior_5d_spx_return < prior5d_spx_min: log event=regime_skip(reason="weakness"), STOP.
  R8.  Check kill switches (NEW ENTRIES ONLY — open positions still managed):
         IF rolling_weekly_loss_pct  >= weekly_loss_cap_pct:
            log event=capital_gate(reason="weekly_loss_cap"); STOP for the week.
         IF rolling_monthly_dd_pct   >= monthly_dd_cap_pct:
            log event=capital_gate(reason="monthly_drawdown");  STOP for 30 days.
  R9.  Monthly capital gate:
         IF monthly_stop_on_negative_pnl
            AND realized_month_to_date_net_pnl < 0:
              log event=capital_gate(reason="month_negative");
              disable all new entries until the first trading day of the
              next calendar month.
              (Open positions continue to be managed under R23–R28.)
         On the first trading day of each calendar month, reset
         realized_month_to_date_net_pnl to 0 (reset semantics governed by
         monthly_reset_mode = "calendar_month").

OPENING-RANGE OBSERVATION (9:30–10:00 ET):
  R10. Record OR_high, OR_low.
  R11. Record session_vwap_at_10am, ES_open_30m_high.
  R12. Record realised_5min_vol_since_open and OR_size vs 20d_avg.

CONFIRMATION (poll once per minute starting 10:30 ET, until 13:00 ET):
       Confirmation is **continuous, not one-shot**. A failed check at
       10:30 does not skip the day; the loop keeps polling until the
       window closes or confirmation triggers, whichever comes first.
  R13. If now > entry_window_end_et: log event=no_entry(reason="window_closed"), STOP.
  R14. Variant-specific confirmation:
         conservative:   spot > session_vwap AND spot > OR_high held >=15 min AND ES > ES_open_30m_high
         balanced:       spot > session_vwap AND ema5 > ema20
         aggressive:     spot > session_vwap
       If false: continue polling (do not STOP for the day).
       Stale-input guard: if any input required by the active variant
       (SPX, ES, VIX, breadth, EMA inputs) is stale (last update >
       max_quote_age_sec ago), unavailable, crossed/locked, or marked
       delayed by the data feed, **do not trade**. Log
       event=stale_input(field=<which>) and continue polling — a later
       fresh tick may still trigger entry inside the window.

LIQUIDITY PRE-CHECK (immediately on confirmation, blocking — but only
                     blocking *this iteration*, not the day):
  R15. Snapshot the candidate chain.
       - Reject any leg with bid-ask > max_leg_bidask_cents.
       - Reject if combo bid-ask / proposed debit > max_combo_bidask_pct_debit.
       - Reject if top-of-book size on either leg < min_top_of_book_size.
       - Reject if quote age > max_quote_age_sec or market crossed/locked.
       If rejected: log event=liquidity_skip(reason=<which>) and **return
       to the polling loop (R13)**. Liquidity check is per-iteration; a
       failed snapshot at 10:32 must NOT kill the day — intraday
       liquidity can improve materially between 10:30 and 11:30. Continue
       polling under R12-R14 and re-check liquidity each time
       confirmation triggers. Only at entry_window_end_et do we stop.
       If liquidity has been failing continuously for
       persistent_illiquidity_minutes (default 30 min, since first
       confirmation): log event=no_entry(reason="persistent_illiquidity"),
       STOP for the day. This protects against grinding the chain all
       afternoon when the underlying market is structurally bad.

STRIKE SELECTION:
  R16. **Build a band of long candidates**, do not lock the long to a
       single strike before the spread is evaluated.
       long_candidates = { all calls with delta in
         [long_target_delta - long_delta_band,
          long_target_delta + long_delta_band] }
       Default starting hypothesis: long_target_delta = 0.45,
       long_delta_band = 0.05 → delta in [0.40, 0.50] (≈ 3-5 strikes
       around target on a typical SPX chain).
       Reason: a neighbouring long strike can offer materially better
       liquidity, executable credit, or reward/risk than the single
       closest-to-target one. Locking the long before scoring the spread
       bakes in an arbitrary entry constraint. R17 scores the full
       (long, short) tuple over the cross-product of long_candidates ×
       candidate_widths.
  R17. **Score-and-select over the full spread tuple set.** Build the
       admissible set across (long_candidate × candidate_widths), then
       pick the highest-scoring tuple under a declared objective. This
       removes both the "first-acceptable width" ordering artefact AND
       the "fixed long" early-locking artefact.
       admissible_set = []
       For each long in long_candidates:
         For each width in candidate_widths:
           short_strike       = long.strike + width
           If short_strike not in chain or no quotes: skip.
           debit_conservative = ask(long) - bid(short)         # cross-the-spread cost
           executable_credit_estimate
                              = bid(long) - ask(short)         # what we'd realise on close
           ratio              = debit_conservative / width
           If debit_to_width_min <= ratio <= debit_to_width_max
              AND (width - debit_conservative) / debit_conservative >= reward_to_risk_min
              AND debit_conservative * 100 <= per_trade_risk_pct * account_nlv_usd
              AND executable_credit_estimate > 0
              AND R15-style liquidity check passes for both legs:
                 add (long, short, debit_conservative,
                      executable_credit_estimate, score)
                      to admissible_set.

       Scoring (objective is itself a parameter — ablate it):
         - "closest_to_target_ratio" (default starting hypothesis):
             target = (debit_to_width_min + debit_to_width_max) / 2
             score = -abs(ratio - target)
         - "max_entry_rr":
             # True entry reward/risk: max_profit / max_loss at entry.
             # This IS the entry-economics measure.
             score = (width - debit_conservative) / debit_conservative
         - "max_pop_at_breakeven":
             score = pop_fn(long.strike + debit_conservative)
         - "max_exit_efficiency":
             # NOT an entry payoff measure — a *liquidity / exitability*
             # proxy. High score = spread is easy to close at near-fair
             # value. Useful as a tiebreaker or in fast tape; do NOT
             # treat as reward/risk for entry sizing. Renamed from the
             # earlier "max_executable_rr" because the math (width minus
             # close-now credit, divided by close-now credit) measures
             # how cheap the spread is to exit *now*, not the entry's
             # max-profit-to-max-loss ratio.
             score = executable_credit_estimate / max(0.01, debit_conservative)

       Pick the highest-scoring admissible (long, short) tuple.
       Tie-break order:
         (a) higher executable_credit_estimate (better exitability),
         (b) narrower width (smaller leg-out exposure, tighter combo book),
         (c) long delta closer to long_target_delta (canonical setup).
       (candidate_widths and long_delta_band are set explicitly per §7
       settings — do not silently scan all strikes; the bands are part
       of the ablation.)
  R18. If admissible_set is empty (no (long, short) tuple passed all
       admissibility checks in R17): log
       event=no_entry(reason="no_viable_strikes"), STOP for the day.

ENTRY:
  R19. For step in entry_ladder_steps:
         submit BUY combo LMT at step price; wait entry_ladder_dwell_sec seconds.
         If filled: break and start MONITOR.
         Else cancel (or modify to next step).
  R20. If never filled: log event=entry_cancelled, STOP.
  R21. Record entry_debit, entry_time, breakeven, max_profit, max_loss.
       Emit event=spread_opened with full payload.

LEG-BALANCE VERIFICATION:
  R22. Within 30 s, verify positions show long_qty=+1 AND short_qty=-1.
       If not balanced: submit MKT close on the unmatched leg;
       emit event=legout_flattened; STOP for the day.

MONITOR:
  R23. Subscribe to leg quotes (event-driven WS preferred) or poll every
       monitor_poll_sec seconds. Maintain TWO marks at all times:
         - **monitoring_mark** = (mid(long) - mid(short))   [smoothed reference]
         - **executable_credit** = bid(long) - ask(short)   [worst-case fill on a SELL combo MKT now]
       monitoring_mark is for stable plotting/logging; **executable_credit
       is the value the bot is required to confirm decisions against,
       because that is what would actually fill if the bot acted right
       now.** Mid alone can lie: late in the day, or in fast tape, mid
       may show a healthy mark while executable credit has collapsed (or
       conversely, mid may flag a profit-take that isn't realisable).
  R23a. Open-position data-outage fail-safe.
       Track time-since-last-fresh-quote on each leg. While monitoring an
       open position:
         - If a leg quote stops updating for monitoring_quote_grace_sec
           (e.g. 15 s): attempt up to monitoring_reconnect_max_attempts
           reconnects (e.g. 3 attempts, 5 s exponential backoff between);
           emit event=quote_outage(leg=<conid>).
         - If quotes do not recover within monitoring_quote_max_blind_sec
           (e.g. 60 s) total since loss: submit a SELL combo MKT
           emergency-flatten regardless of state, regardless of the
           uneconomic-credit guard, and regardless of profit/loss state;
           emit event=spread_closed(reason="data_outage_flatten");
           STOP monitoring.
       Rationale: a stale-quote freeze on an open 0DTE position is one
       of the most expensive failure modes of the day. "Don't act on
       stale data" must NOT mean "do nothing while the position bleeds
       out." Be deliberately blind for at most monitoring_quote_max_blind_sec,
       then flatten.
  R24. (Optional, ablation-controlled) If pt_enabled
         AND monitoring_mark >= entry_debit + pt_pct_of_max_profit * (width - entry_debit)
         AND **executable_credit** >= entry_debit + pt_pct_of_max_profit * (width - entry_debit):
           submit SELL combo MKT (close); record close, pnl;
           emit event=spread_closed(reason="pt"). STOP monitoring.
       Both checks must confirm — never take a phantom profit on a
       midpoint that isn't realisable.
  R25. If **executable_credit** <= entry_debit * stop_pct_of_debit:
         start a stop_confirm_sec timer; if executable_credit stays <=
         threshold for the full duration:
           submit SELL combo MKT (close); record close, pnl;
           emit event=spread_closed(reason="stop"). STOP monitoring.
       If executable_credit recovers above threshold within the timer:
       cancel the timer, continue.
       Stops fire on **executable** credit, not mid — protective exits
       must respond to what the bot can actually realise on a close,
       not a smoothed mid that may lag a collapsed market.
  R26. If now >= time_stop_et
         AND **executable_credit** < entry_debit * time_stop_pct_of_debit:
         submit SELL combo MKT (close); record close, pnl;
         emit event=spread_closed(reason="time_stop"). STOP monitoring.
  R27. If now >= no_new_orders_after_et:
         disable new profit-taking actions (R24 PT no longer fires).
         **Protective exits — stop (R25), time-stop (R26), legout flatten
         (R22), hard-close (R28), and any emergency-flattening logic —
         REMAIN ACTIVE.**
         Rationale: between no_new_orders_after_et and hard_close_at_et a
         position can deteriorate fast on late-session gamma; the bot must
         not intentionally do nothing in that window.
  R28. If now >= hard_close_at_et:
         submit SELL combo MKT regardless of state and regardless of the
         uneconomic-credit guard; record close, pnl;
         emit event=spread_closed(reason="hard_close"). STOP monitoring.

POST-CLOSE / SETTLEMENT:
  R29. After 16:01 ET: for any spread still status=OPEN (rare; should be ~0 after R28),
       compute settlement P&L from SPX close print, record_settlement,
       emit event=spread_settled.
  R30. Update rolling weekly_loss_pct and monthly_dd_pct.
       Re-check kill-switch triggers for tomorrow.

INVARIANTS (assert at every transition):
  I1.  No more than one filled spread per (date, symbol).
  I2.  No order submitted when regime_skip or kill-switch is active.
  I3.  Uneconomic-credit guard (estimated executable close credit <= 0)
         applies ONLY to *optional* stop exits (R25).
         It does NOT block hard-close (R28), time-stop (R26), legout flatten
         (R22), data-outage flatten (R23a), or any other emergency-flattening
         logic.
  I4.  Spread must be SPXW (tradingClass check) — never SPX monthly.
  I5.  After R22 fails, no new entry attempts that day.
  I6.  All thresholds are configurable; none hardcoded; ablation-tested
         before any threshold is moved out of the "hypothesis" category.
  I7.  At most one *filled* spread per (date, symbol). If today's spread
         has been closed (for any reason — stop, time-stop, hard-close,
         settlement, leg-out flatten), no further entry attempts that day,
         regardless of P&L outcome.
  I8.  Data-integrity invariant (entry side): if any required market
         input (SPX, ES, VIX, VIX3M, breadth, EMA inputs, option chain
         quotes) is stale, crossed, locked, or marked delayed at decision
         time, do not initiate a trade.
  I9.  Data-integrity invariant (open-position side): a stale leg quote
         must NOT trigger an optional exit (R24 PT or R25 stop) — those
         require fresh executable_credit. But the data-outage fail-safe
         (R23a) MUST trigger emergency flatten if the blind window
         exceeds monitoring_quote_max_blind_sec. "Don't act on stale
         data" and "don't sit blind on an open position" must coexist:
         silent on optional decisions, loud on the outage-flatten path.
```

---

## Section 8 — Closing principle

> All exact thresholds in this redesign should be treated as hypotheses,
> not truths. The likely improvements are increased selectivity, better
> reward/risk discipline, removal of event-driven adverse regimes, and
> exits based on the economics of the spread rather than spot alone. But
> each filter, target, and stop must survive ablation testing,
> walk-forward validation, and slippage stress. **If a rule does not
> improve out-of-sample expectancy or reduce drawdown robustly, it should
> be removed.**

The redesign is a candidate, not a result. The ablation study (§6.4) is
the contract that turns the candidate into a result.
