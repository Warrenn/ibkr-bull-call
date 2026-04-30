# Data Acquisition Decision Framework

Comparison of vendor options for acquiring the three TBD datasets in
[`research/data/manifest.json`](../research/data/manifest.json) — `spx_spot_intraday`,
`es_or_mes_intraday`, and `spx_0dte_call_chain` — with concrete cost
implications.

**Scope.** This doc surfaces options and tradeoffs. It identifies the
cheapest path that meets Tier 1 requirements and the realistic costs of
Tier 2 promotion-grade data. It does **not** select a vendor; the buy
decision is yours, and is gated on budget signals you have that this
doc does not.

**Snapshot date.** All prices below were last seen on vendor pages
between 2026-04-29 and 2026-04-30. Vendor pricing changes; reconfirm
on the vendor's pricing page before signing or invoicing.

## TL;DR

The cheapest path that satisfies Tier 1 (Phases 1-3) is roughly
**$0-$200 first month, ~$15/month ongoing** if you combine free /
already-licensed sources cleverly. The most expensive realistic Tier 1
path is **~$1,500-$5,000 one-time** (CBOE direct) plus a separate
futures vendor.

Tier 2 (Phases 4-6 — stop / PT / rolling / stress) is **at least
$2,500-$5,000 incremental**, more typically **$5,000-$15,000+**, and
**should not be bought until Phase 1+2 conclude `EDGE PRESENT` and
the bull call spread survives the expression comparison**. Buying tick
data before that point is wasted spend.

There is **no free path** for historical SPX 0DTE option-chain bid/ask
at any depth. IBKR's `reqHistoricalData` does not honor `includeExpired`
for options, and every free / nearly-free source either doesn't sell
SPX index options at all or returns only the live chain. This forces a
paid vendor for the chain.

## Cost Summary

Tier 1 = 1-minute resolution, 24-36 months, sufficient for Phases 1, 2, 3.
Tier 2 = 1-second / tick resolution, sufficient for Phases 4, 5, 6.

| Vendor | Tier 1 cost (24mo, single-seat) | Tier 2 cost (24mo, single-seat) | Covers SPX index | Covers SPXW 0DTE NBBO | Covers ES/MES | Notes |
|---|---|---|---|---|---|---|
| **Databento (PAYG)** | **~$150-$500** *(usually free via $125 credit + ~1 mo Standard $199)* | ~$2,500-$8,000+ depending on schema | ❌ (CGIF PCAP $750/mo only) | ✅ (OPRA.PILLAR) | ✅ (GLBX.MDP3) | Pay-as-you-go priced on uncompressed DBN bytes; verify with `metadata.get_cost` before download. ES history >15 yr; SPXW NBBO back to Apr 2013. |
| **Polygon / Massive** | **~$108/mo** flat ($79 Options Advanced + $29 Stocks/Indices Starter) | $199/mo Advanced+ adds real-time OPRA + tick flat files | ✅ (paid Indices sub) | ✅ (OPRA flat files; Advanced tier required for quotes) | ⚠️ Sales-quote only (futures launched mid-2025) | Cancellable monthly; flat files via S3-compatible endpoint; no Java terminal. Rebrand: polygon.io URLs redirect to massive.com; legal entity is Massive. |
| **ThetaData** | **~$80/mo** Options Standard | ~$160/mo Options Pro (full tick + 12 yr) | ✅ (1-second venue-native, since 2017) | ✅ (tick NBBO from OPRA, 8 yr Standard / 12 yr Pro) | ❌ Q3 2026 roadmap | Java terminal mandatory; Standard tier may need a Greeks-derived workaround for SPX spot ticks. SPX 1m fits cleanly into Options Standard. |
| **CBOE DataShop** | **~$1,500-$5,000** one-time, request quote | ~$5,000-$15,000+ one-time, request quote | ⚠️ SPX bid/ask requires CGI license at $1k/month | ✅ Authoritative | ❌ (CME, not CBOE) | No public rate card; per-product cart, sales conversation. SPXW 1-min coverage from Mar 2020; M-F 0DTE coverage from mid-2022. Academic discount is institution-only. |
| **IBKR Historical** | **~$11.50-$16.50/mo** existing market-data subs | Same | ✅ (with CME S&P Indexes Bundle) | ❌ **NO** (no `includeExpired` for options) | ✅ (`includeExpired=True`, 2 yr post-expiry) | Fine for SPX/ES 1m OHLCV (~3-6 hr polite scrape per 24 mo). **Disqualifying gap on the 0DTE chain.** |
| **Free / nearly-free** | $0 | $0 | yfinance daily; intraday capped 7 days | ❌ None covers SPXW 0DTE history | yfinance front-month only; no roll history | Tradier sandbox (forward-record only, 15 min delayed), Stooq, Alpha Vantage, EODHD. Sanity-check tier only. |

## Considered Paths

Three realistic ways to satisfy the Tier 1 contract. All of them
combine vendors because no single retail-priced vendor covers SPX index
+ SPXW chain + ES/MES futures alone (the closest is Databento, missing
SPX index; ThetaData, missing futures; or Polygon, with futures behind
a sales conversation).

### Path A — IBKR + Databento (cheapest first month)

| Dataset | Source | Cost |
|---|---|---|
| SPX 1m spot (24-36 mo) | IBKR Historical Data API (`whatToShow=TRADES`, with CME S&P Indexes Bundle) | ~$15/mo IBKR sub (already partially paid for paper trading) |
| ES 1m | IBKR Historical (`includeExpired=True`) **or** Databento GLBX.MDP3 OHLCV-1m | $0 incremental on IBKR; <$50 incremental on Databento PAYG |
| SPX 0DTE chain (entry+exit window snapshots) | Databento OPRA.PILLAR CBBO-1m, SPXW symbol filter | ~$100-$400 PAYG; **likely $0** if covered by the $125 first-account credit |

**First-month total: ~$0-$50 incremental over current IBKR cost.**
Recurring after acquisition: ~$15/mo IBKR while the bot is paper trading.

Pros: dramatically cheapest. Reuses IBKR sub. Lets you sample Databento's
DBN format and `databento-python` client before scaling. ES is
authoritative GLBX.MDP3 (CME direct) on Databento.

Cons: Three-vendor stitch. SPX 1m via IBKR requires polite scraping
(~3-6 hr) and is depth-limited (~1-2 yr practical). The $125 free credit
is one-shot — re-pulls or schema mistakes burn it.

### Path B — Polygon / Massive only (single-vendor simplicity)

| Dataset | Source | Cost |
|---|---|---|
| SPX 1m spot | Polygon Stocks/Indices Starter ($29/mo) | $29/mo |
| ES 1m | Polygon Futures (mid-2025 launch) | **request quote** — likely $50-$200/mo bundled |
| SPX 0DTE chain | Polygon Options Advanced ($79/mo), OPRA flat files | $79/mo |

**Monthly total: ~$108/mo plus the futures quote. Estimate $150-$300/mo.**

Pros: One vendor, one billing relationship, one client library, one
license document. Flat files via S3 are excellent for backtesting.
Cancellable monthly. No Java terminal. No PAYG metering surprises.

Cons: $1.3K-$3.6K/year; futures pricing is still sales-quote-only (a
real friction point — you can't budget without a sales conversation
first). Minute aggregates are bar-of-trades, not bar-of-quotes — for
mid-price-at-bar-close you must reconstruct from the flat-file OPRA
quotes.

### Path C — ThetaData + (IBKR or Databento) for futures

| Dataset | Source | Cost |
|---|---|---|
| SPX 1m spot | ThetaData Options Standard (bundled, 1-second venue-native) | $80/mo |
| ES 1m | IBKR (free with sub) or Databento PAYG (<$50) | $0-$50 |
| SPX 0DTE chain (8-yr depth NBBO) | ThetaData Options Standard | included in $80/mo |

**Monthly total: ~$80-$130/mo.**

Pros: 8 years of SPXW NBBO history at Standard tier — deeper than
Polygon's 4-year quote depth. SPX index covered without an extra
license fee. Greeks included.

Cons: Java terminal mandatory (operational overhead — local JVM
process). ES/MES not sold by ThetaData, so you stitch a second vendor
either way. Standard tier may require a Greeks-derived workaround for
SPX spot ticks per a community report — verify with their support
before committing.

## Why CBOE DataShop is not the leading recommendation

It is the authoritative source. It is also four-figures one-time for
Tier 1 and five-figures one-time for Tier 2, with no rate card and a
sales conversation gate. For a single-seat retail research project
that is still in the falsification phase, the marginal value of CBOE
provenance over Polygon/ThetaData/Databento OPRA is negligible — they
all source from OPRA / CBOE feeds upstream — but the price delta is
10-20×. CBOE is the right answer once a strategy has earned its way
to live capital and provenance audit-trails matter; it is not the
right answer for a directional-edge falsification test that costs
nothing if it returns `NO EDGE`.

## What's NOT in any of these paths

The following data classes are needed by later phases but are not part
of any of the three Tier 1 paths above. Each is a separate
acquisition, deferred until the phase that needs it.

| Data class | First phase needing it | Acquisition cost (estimate) |
|---|---|---|
| VIX series | Phase 5 — regime-filtered ablation | Free via `^VIX` on yfinance daily; bundled in Polygon/ThetaData index subs at intraday |
| Event calendar (FOMC / CPI / NFP / OPEX tags) | Phase 5 — event-filter ablation | Free — manually curated CSV from public Fed/BLS calendars |
| CBOE SPX SET (settlement value) | Phase 1 deliverable per [`live-capital-go-no-go.md`](./live-capital-go-no-go.md) | Free (CBOE publishes daily); currently mitigated in code by `[long_strike × 0.5, long_strike × 2.0]` sanity band |
| Realized intraday volume / TOB depth | Phase 6 — adverse-fill stress | Bundled in Tier 2 OPRA depth feeds (Databento MBP-10, Polygon real-time, etc.) — not a separate purchase |

## Buy-Decision Rubric

Score each candidate path against your constraints. A "no" on any
hard-constraint row eliminates the path; soft constraints are
tradeoffs.

| Constraint | Path A (IBKR + Databento) | Path B (Polygon) | Path C (ThetaData + IBKR/Databento) |
|---|---|---|---|
| **Tier 1 cost ≤ $300 first month** (hard) | ✅ ~$0-50 | ❌ $108-300/mo | ✅ $80-130 |
| **Tier 1 cost ≤ $50/mo ongoing** (hard) | ✅ ~$15/mo (IBKR) | ❌ ~$108-300 | ❌ $80-130 |
| **Single-vendor billing** (soft) | ❌ 3 vendors | ✅ 1 vendor | ❌ 2 vendors |
| **No Java/local terminal** (soft) | ✅ | ✅ | ❌ Theta Terminal required |
| **Cancellable monthly** (soft) | ✅ | ✅ | ✅ |
| **Tier 2 path exists from same vendor** (soft) | ✅ Databento PAYG scales | ✅ Polygon Advanced+ | ✅ ThetaData Pro |
| **OPRA NBBO history depth** | 13 yr (Databento) | 4 yr (Polygon Advanced) | 8 yr (ThetaData Standard) / 12 yr (Pro) |
| **ES/MES authoritative source** | ✅ GLBX.MDP3 (Databento) or IBKR | ⚠️ Polygon Futures (request quote) | ❌ via IBKR/Databento separately |

## Open Questions Before Committing

These are the things this doc cannot answer for you. Each affects the
path choice materially.

1. **Budget envelope.** Is the ceiling for Tier 1 acquisition $0?
   $200/mo? $500/mo? $5,000 one-time? Path choice changes
   substantially across these bands.
2. **One-shot vs ongoing.** If the directional-edge test concludes
   `NO EDGE`, are you OK paying for one month of data and walking
   away, or do you want a path with no ongoing commitment?
3. **Java terminal tolerance.** ThetaData's local Theta Terminal is
   a real operational dependency on top of the bot. Acceptable?
4. **Database provenance preference.** Do you require CBOE-direct
   provenance for the chain (a few specific institutional contexts
   need this), or is OPRA-via-Polygon/ThetaData/Databento equivalent
   for your purposes?
5. **Time-to-data preference.** Path A (IBKR + Databento) is
   cheapest but slowest — IBKR scraping takes hours, Databento
   batch download takes minutes-to-hours per dataset. Path B
   (Polygon) is API-instant. If you want to be running Phase 1 in
   24 hours, that's worth a lot.

## Recommended Sequence

If the answers above produce no strong constraint, the lowest-risk
sequence that still gets to Phase 1 quickly is:

1. **Sign up for Databento now.** Free. Use the $125 credit to
   download SPXW 0DTE OHLCV-1m + CBBO-1m + ES OHLCV-1m for the most
   recent 24 months. Verify the data shape and DBN client work before
   scaling.
2. **In parallel, configure IBKR Historical data subs** if you don't
   already have them, and write the SPX 1m TRADES scraper. ~$15/mo
   ongoing, no incremental cost beyond what you already pay for paper
   trading.
3. **If the $125 credit covers Tier 1 fully, stop here and run
   Phase 1.** If it doesn't (the chain pull may exceed it depending
   on the exact symbol filter and history depth), buy one month of
   Databento Standard ($199) to finish the pull, then cancel.
4. **Defer Tier 2 (tick data) until Phase 1+2 show edge.** Tier 2
   is $2.5K-$15K depending on path; do not spend that before knowing
   the strategy has standalone directional edge and survives
   expression comparison.

This sequence has the property that **a `NO EDGE` outcome at Phase 1
costs you ~$0-$200 total**, which is the right blast radius for a
falsification test.

## Per-Vendor Detailed Writeups

### Databento

- Pricing model: hybrid pay-as-you-go and subscription. PAYG metered
  on uncompressed DBN bytes. Subscription tiers: Standard $199/mo
  (1 yr L1 / 1 mo L2-L3 history included), Plus $1,399/mo, Unlimited
  $3,500/mo. New accounts get **$125 free historical credits**,
  expiring 6 months after signup, one set per team.
- SPX index: **not relicensed via normalized feed.** CGIF PCAPs
  ($750/mo) are the only Databento path to the SPX value, and that's
  raw multicast — overkill for a 1-min OHLCV need. Source SPX 1m
  elsewhere (IBKR / Polygon / Yahoo).
- SPXW options: OPRA.PILLAR dataset, full coverage. Trades + OHLCV-1s/1m
  + CBBO-1m + statistics + definitions back to **April 1, 2013**.
  CMBP-1 / TCBBO / CBBO-1s back to **March 28, 2023**.
- ES/MES: GLBX.MDP3 dataset, full Globex MDP3 capture, ~15+ yr
  history, all schemas including OHLCV-1m, MBP-1, MBP-10. Continuous
  contract symbology supported.
- Format: DBN binary; official Python (`databento-python`), C++, Rust
  clients. `to_df()`, `to_ndarray()`, `to_parquet()` built in.
- License: Standard self-serve = internal/research, single-seat, no
  redistribution. External distribution requires Plus/Unlimited.
- Gotchas: $/GB rate is **not published** — hit `metadata.get_cost`
  before downloading. Full OPRA tape is ~7 TB compressed/day raw;
  you must filter by SPXW symbol root server-side or you'll burn the
  credit instantly. MBP-1 → CMBP-1 migration as of 2025-05-27 broke
  legacy code paths. Live OPRA usage-based pricing was retired
  2025-06-03 (historical PAYG remains).

### Polygon / Massive

- Pricing model: flat monthly subscription per asset class. Stocks
  Starter $29, Stocks Developer $79, Stocks Advanced $199. Options
  Developer $29, Options Advanced $79 (with quotes), Advanced+ ~$199
  (with real-time OPRA). Indices gated to paid index sub. Futures
  launched mid-2025; pricing is **sales-quote-only**.
- SPX index: yes via `I:SPX` ticker; minute aggregates + WS values;
  paid Indices plan required.
- SPXW options: yes via `O:SPXW...` tickers. Bid/ask included on
  Options Advanced ($79) and above; Developer tier is last-trade
  only. **2 yr (Developer) / 4 yr (Advanced) on REST**, but flat
  files extend further (~10 yr OPRA back to ~2014).
- ES/MES: covered via Futures product; CME Globex through CBOT/
  NYMEX/COMEX. **Pricing not public.**
- Format: REST (JSON), WebSocket (real-time), CSV flat files via
  S3-compatible endpoint (boto3, rclone, mc). No native parquet —
  users convert. Files post ~11:00 ET T+1.
- License: Individual ToS is non-commercial, no redistribution, no
  sublicense, no derived-data sharing — fits research-only single
  seat exactly.
- Gotchas: Minute aggregates are bar-of-trades, not bar-of-quotes —
  for mid-price-at-bar-close you must reconstruct from OPRA quotes
  flat file. 15-min delayed by default on Developer tier; "real-time"
  OPRA only on Advanced+. polygon.io URLs redirect to massive.com;
  legal entity is now Massive.

### ThetaData

- Pricing model: flat monthly. **Options Value $40, Options Standard
  $80, Options Pro $160.** Free EOD-only sandbox tier exists.
  Commercial-use plans separate, request quote.
- SPX index: yes, bundled into the options tier — Standard/Pro at
  ~1-second venue-native; Value at 15-minute delayed. SPX history
  back to **2017-01-01**.
- SPXW options: 100% US index/equity options coverage. Standard =
  tick-level NBBO + trades from OPRA back to 2016-01-01; Pro reaches
  2012-06-01. **Greeks (1st/2nd/3rd order + IV) included.** OI updates
  T+1 morning.
- ES/MES: **not sold today.** CME Futures is on the roadmap for
  Q3 2026.
- Format: REST + WebSocket via the local Theta Terminal (Java 21+
  process). No native flat-file/Parquet bulk export; bulk pulls are
  programmatic. QuantConnect LEAN integration exists.
- License: retail tiers are single-seat / non-redistributing.
  Commercial = separate quote.
- Gotchas: Java terminal mandatory (operational overhead). Standard
  tier may not fetch SPX/VIX underlying prints directly — community
  workaround derives spot from option Greeks. Verify with their
  support before committing if SPX 1-second spot is critical.
  Multi-call patterns (chain + quotes + trades require parallel API
  calls + manual stitching). Free tier rate-limited to 30 req/min.

### CBOE DataShop / LiveVol

- Pricing model: per-product, configured-in-cart. **No public rate
  card** — every product page shows $0 until you select symbols /
  dates / intervals / frequency. One-time historical or monthly/
  annual subscription. Delivery via SFTP or Snowflake.
- SPX index 1-min: SPX bid/ask redistribution requires CGI / CGIF
  license at **$1,000/month** — overkill for a single-seat
  research project. For SPX OHLCV use a different vendor.
- SPXW options 24-36 mo: realistic order-of-magnitude bands from
  reseller-licensed feeds: **$1.5K-$5K one-time for 1-min NBBO**;
  **$5K-$15K+ for tick.** Quote-on-config from CBOE; no public
  rate card.
- Right product for backtesting: DataShop → Option Quotes (1-min
  interval) or Option Trades (tick). LiveVol Pro ($420/mo) is an
  analytics GUI, not a bulk-download tool.
- ES/MES: not on CBOE — CME, separate vendor.
- Historical depth: SPX options EOD from Jan 2010, 1-min interval
  quotes from Jan 2012. SPXW interval data from March 2020. M-F
  daily SPX 0DTE coverage starts mid-2022 (Tue/Thu expiries listed
  Apr 18 / May 11 2022).
- License: single-organization research/internal use; no
  redistribution. Index bid/ask redistribution adds CGI sub.
- Gotchas: No free trial. Sample files behind login. Academic
  discount is institution-affiliated only — independent retail
  researchers don't qualify. Retail individuals can buy via credit
  card; the barrier is cost, not access.

### IBKR Historical (and free fallbacks)

- IBKR pricing model: free with appropriate market-data subs.
  Realistic monthly: US Securities Snapshot + Futures Value Bundle
  ($10) + OPRA ($1.50, waived ≥$20 commissions) + CME S&P Indexes
  Bundle L1 (needed for SPX). Total ~$11.50-$16.50/mo. Paper
  inherits live subs.
- SPX 1m spot: yes, via `whatToShow=TRADES` (not BID/ASK on
  indices). Practical depth ~1-2 yr.
- ES/MES 1m: yes, with `includeExpired=True` for 2 yr post-expiry —
  iterate front months.
- **SPXW 0DTE chain history: NOT available.** `reqHistoricalData`
  works only for non-expired option contracts; there is no
  `includeExpired` for options. **Disqualifying gap for the chain.**
- Rate limits: 60 reqs / 10-min rolling, 6 reqs / 2 sec same
  contract, BID_ASK doubles your pacing budget. Each response capped
  at "a few thousand bars."
- Time-to-acquire 24 mo of 1-min: SPX ≈ 197k bars / ~3,000 per req
  / 60 per 10 min ≈ 12 min raw, 3-6 hr realistic with retries.
  Same for ES.
- Free / nearly-free alternatives: **all sanity-check tier only.**
  yfinance 1-min capped at 7 days; Alpha Vantage 25 reqs/day on
  free tier; Stooq 1m intraday limited to ~1 mo; EODHD does not
  cover SPX index options (CGI gate); Tradier sandbox is forward-
  record only with 15-min delay. None deliver 24 mo of historical
  SPX 0DTE bid/ask.

## Sources

Cited inline where relevant; consolidated below.

### Polygon / Massive
- [Pricing | Massive](https://massive.com/pricing)
- [Options Market Data API](https://polygon.io/options)
- [Indices API | Massive](https://massive.com/indices)
- [Futures Data API | Massive](https://massive.com/futures)
- [Options Flat Files Overview](https://polygon.io/docs/flat-files/options/overview)
- [Polygon for Individuals ToS](https://polygon.io/legal/individuals-terms-of-service)
- [Release Notes — June 2025 (futures launch)](https://massive.com/blog/release-notes-june-2025)

### ThetaData
- [ThetaData Pricing](https://www.thetadata.net/pricing)
- [Subscriptions tier matrix (docs)](https://docs.thetadata.us/Articles/Getting-Started/Subscriptions.html)
- [Product Roadmap — CME Futures Q3 2026](https://www.thetadata.net/roadmap)
- [Indices API launch post](https://www.thetadata.net/post/theta-data-launches-new-indices-api)

### CBOE DataShop
- [Cboe DataShop home](https://datashop.cboe.com/)
- [Option Quotes product](https://datashop.cboe.com/option-quote-intervals)
- [Option Trades product](https://datashop.cboe.com/option-trades)
- [SIP Fees (OPRA + index)](https://datashop.cboe.com/sip-fees)
- [DataShop FAQs](https://datashop.cboe.com/faqs)
- [Academic Discount](https://datashop.cboe.com/academic-discount)
- [SPXW Tue/Thu expiries announcement](https://www.prnewswire.com/news-releases/cboe-to-add-tuesday-and-thursday-expirations-for-spx-weeklys-options-301524687.html)

### Databento
- [Databento Pricing](https://databento.com/pricing)
- [Introducing new OPRA pricing plans](https://databento.com/blog/introducing-new-opra-pricing-plans)
- [OPRA improvements coming soon](https://databento.com/blog/opra-improvements-coming-soon)
- [CGIF PCAPs now available](https://databento.com/blog/cboe-global-indices-feed-cgif-pcaps)
- [OPRA dataset page](https://databento.com/datasets/OPRA.PILLAR)
- [GLBX.MDP3 dataset page](https://databento.com/datasets/GLBX.MDP3)

### IBKR Historical
- [TWS API: Historical Data Limitations](https://interactivebrokers.github.io/tws-api/historical_limitations.html)
- [TWS API: Historical Bar Data](https://interactivebrokers.github.io/tws-api/historical_bars.html)
- [Client Portal API Documentation](https://interactivebrokers.github.io/cpwebapi/)
- [IBKR Market Data Pricing](https://www.interactivebrokers.com/en/pricing/market-data-pricing.php)
- [ib_insync Discussion #446 — Historical option data for non-expired contracts](https://github.com/erdewit/ib_insync/discussions/446)

### Free / nearly-free
- [yfinance issue #1510 — Intraday data cannot extend last 60 days](https://github.com/ranaroussi/yfinance/issues/1510)
- [Alpha Vantage Premium](https://www.alphavantage.co/premium/)
- [QuantStart — An Introduction to Stooq Pricing Data](https://www.quantstart.com/articles/an-introduction-to-stooq-pricing-data/)
- [EODHD pricing](https://eodhd.com/pricing)
- [Tradier API — Get Time & Sales](https://docs.tradier.com/reference/brokerage-api-markets-get-timesales)
