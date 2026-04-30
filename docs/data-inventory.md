# Data Inventory

Authoritative list of research datasets currently available to the
backtesting harness, with sources, resolutions, coverage, storage paths,
and the gap between what we have and what each phase of
[`minimum-viable-backtesting-program.md`](./minimum-viable-backtesting-program.md)
requires.

Scope: this document inventories what is *in the repo* (and therefore
reproducible per `STRATEGY-SPEC-v1.md`'s "frozen datasets only" rule).
Data that lives on a personal machine, an IBKR account, or a vendor
account but is not committed to `research/data/` does not count for
research purposes — it is invisible to the harness, the manifest, and
anyone replicating a result.

This is not a vendor-selection document. Vendor candidates are listed
without commitment in the "Acquisition Candidates" section; the actual
buy decision belongs in a separate `docs/data-acquisition-decision.md`.

## Status Verdict

- **Phase 1 (directional-edge test)**: PARTIALLY UNBLOCKED.
  `es_or_mes_intraday` is now populated (ES front-month continuous,
  GLBX.MDP3 OHLCV-1m via Databento, 2023-04-30 → 2026-04-30, 1.05M
  rows, $3.85). `spx_spot_intraday` is still TBD (planned via IBKR
  Historical at no incremental cost). Trading calendar in place. The
  test can run on ES alone for the directional-edge falsification.
- **Phase 2 (expression comparison)**: BLOCKED on data acquisition.
  Needs everything Phase 1 needs plus `spx_0dte_call_chain` with
  bid/ask snapshots covering entry and exit windows.
- **Phase 3+ (strike / exit / PT / rolling ablations)**: BLOCKED on
  the same Phase 1+2 acquisitions, with the additional caveat that
  Tier 1 1-minute data is not enough to reach credible conclusions
  about stop, profit-taking, or rolling — Tier 2 (1-second / tick)
  is required for those (see "Data Tiers" below).

Translation: the harness build is not the bottleneck. Data acquisition
is. Code-side work cannot meaningfully advance Phase 1 until at least
one intraday spot/futures series lands in the manifest.

## Inventory

Single source of truth: [`research/data/manifest.json`](../research/data/manifest.json).
Manifest version `1`, dataset version `dataset-v1`.

### Populated

#### `trading_calendar`

| Field | Value |
| --- | --- |
| Status | populated |
| Source | NYSE schedule via `pandas_market_calendars` |
| Vendor | pandas-market-calendars (open-source, MIT) |
| License | MIT |
| Acquired | 2026-04-30 |
| Path | `research/data/dataset-v1/trading_calendar.parquet` |
| Generator | `research/scripts/generate_trading_calendar.py` |
| Resolution | daily |
| Date range | 2022-01-01 → 2026-04-30 (52 months) |
| Rows | 1581 calendar days (1085 trading days, 9 half-days) |
| Schema | `date`, `is_trading_day`, `is_half_day`, `session_open_utc`, `session_close_utc` |
| Checksum | `sha256:486488b909f157cdced49817b9fa6ae93a15c16f5bab9087e3e89a4ced00651d` |
| Reproducibility | byte-identical re-runs verified via `tests/research/test_generate_trading_calendar.py::test_generate_is_deterministic` |
| Known limitations | event tags (FOMC / CPI / NFP / OPEX) not yet attached — defer to Tier 2; half-day detection compares NYSE close to 16:00 ET |

#### `es_or_mes_intraday`

| Field | Value |
| --- | --- |
| Status | populated |
| Source | GLBX.MDP3 OHLCV-1m via Databento |
| Vendor | Databento |
| License | Databento Standard self-serve (single-seat, research, no redistribution) |
| Acquired | 2026-04-30 |
| Path | `research/data/dataset-v1/es_intraday.parquet` |
| Generator | `research/scripts/download_databento.py` |
| Symbol | ES.c.0 (continuous front-month, calendar-roll) |
| stype_in | continuous |
| Resolution | 1-minute OHLCV |
| Date range | 2023-04-30 → 2026-04-30 (36 months) |
| Rows | 1,053,199 |
| Cost | $3.85 (covered by Databento free credit) |
| Schema | `ts_event`, `rtype`, `publisher_id`, `instrument_id`, `open`, `high`, `low`, `close`, `volume`, `symbol` |
| Checksum | `sha256:cf4567cd3303f7f45f70baa9d862715913c23852fb7ece220234c4d694cfc869` |
| Known limitations | Databento flagged 3+ days as "degraded": 2025-09-17, 2025-09-24, 2025-11-28 (warning truncated; query `metadata.get_dataset_condition` for full list). Calendar continuous (`c` rank 0) introduces small price-level gaps at roll boundaries. OHLCV-1m is bar-of-trades, not bar-of-quotes — for mid-price use, reconstruct from MBP-1 / CMBP-1 instead. |

### Required But Not Acquired

#### `spx_spot_intraday`

| Field | Value |
| --- | --- |
| Status | TBD |
| Required by | Phase 1 (directional-edge), Phase 2 (expression comparison), all later phases |
| Path (planned) | `research/data/dataset-v1/spx_spot_intraday.parquet` |
| Symbol | SPX |
| Tier 1 resolution | 1-minute |
| Tier 2 resolution | 1-second or tick |
| Tier 1 history | 24 months minimum, 36 months preferred |
| Schema (planned) | `ts_utc`, `symbol`, `price` (optional `bid`, `ask`) |
| Known limitations (anticipated) | 1-minute spot alone cannot validate fine-grained stop / PT behavior |

#### `spx_0dte_call_chain`

| Field | Value |
| --- | --- |
| Status | TBD |
| Required by | Phase 2 (E1 + E2), Phase 3 (strike selection), Phase 4+ |
| Path (planned) | `research/data/dataset-v1/spx_0dte_call_chain.parquet` |
| Symbol | SPX 0DTE calls (SPXW PM-settled) |
| Tier 1 resolution | entry-window + exit-window snapshot coverage |
| Tier 2 resolution | 1-5 second cadence with bid/ask both legs throughout the session |
| Schema (planned) | `ts_utc`, `underlying`, `expiry`, `right`, `strike`, `bid`, `ask` (optional `bid_size`, `ask_size`, `conid`) |
| Known limitations (anticipated) | Tier 1 chain data is sufficient for initial expression comparison but not for final stop/rolling conclusions |

### Not In Manifest (Future Phases)

The following data classes are referenced by later phases of the
program doc but are not yet declared in `manifest.json`. Each must be
added (and pinned) before the phase that needs it can run.

| Data class | First phase requiring it | Notes |
| --- | --- | --- |
| VIX series | Phase 5 — regime-filtered ablation variants | Used by §5.A `closest_to_target_ratio` and §5.B `mark_based_with_time_stop` regime gates per `live-capital-go-no-go.md` |
| Event calendar (FOMC / CPI / NFP / OPEX tags) | Phase 5 — event-filter ablation | Adds an `event_tags` column to the calendar; current generator does not emit it |
| CBOE SPX SET (settlement value) feed | Phase 1 deliverable per `live-capital-go-no-go.md` | Currently mitigated in code by the `[long_strike × 0.5, long_strike × 2.0]` sanity band on `_record_settlements`; deeper fix is the official CBOE SET print |
| Realized intraday volume / TOB depth | Phase 6 — adverse-fill stress | Needed for `2x` / `3x` slippage models grounded in real liquidity |

## Data Tiers

Per
[`minimum-viable-backtesting-program.md`](./minimum-viable-backtesting-program.md)
§ "Concrete Test Data Requirements":

### Tier 1 — Minimum viable

Sufficient for **Phases 1, 2, 3** (directional edge, expression
comparison, strike-selection ablation).

- SPX intraday spot: 1-minute
- ES or MES intraday: 1-minute
- SPX 0DTE call chain: entry-window + exit-window snapshots
- Trading calendar with half-day flag

Tier 1 is **not enough** for Phase 4 (stop logic), Phase 5 (PT,
rolling, regime), or Phase 6 (stress).

### Tier 2 — Promotion-grade

Required before any conclusion about stop, PT, rolling, or
live-readiness can be trusted.

- SPX spot: 1-second or tick
- ES: 1-second or tick
- SPX 0DTE option-chain snapshots at ~1-5 second cadence with bid/ask
  for both legs throughout the session
- Event calendar with FOMC / CPI / NFP / OPEX / half-day tags
- Authoritative settlement source or a documented reconciliation source

## Acquisition Candidates

Listed for reference only. **No vendor is selected here**; the buy
decision belongs in a separate document with prices, contract terms,
licensing, and historical-coverage comparisons.

| Vendor | Strengths | Caveats |
| --- | --- | --- |
| Polygon.io | Cheap, friendly API, intraday SPX + options chain | License terms restrict redistribution; verify research-use grant |
| CBOE DataShop | Authoritative SPX + SPX option-chain history | Per-month licensing; expensive at depth |
| Algoseek | Tick-level depth-of-book, NBBO history | Premium pricing |
| dxFeed | Tick + chain with strong cleansing | Subscription model, not flat per-month |
| IBKR Historical Data API | Already-licensed via the trading account | Rate-limited; unsuitable for bulk download of 24+ months at minute resolution |
| Free / yfinance | Daily SPX only | Insufficient resolution for any phase here |

## Storage Convention

- All datasets live under `research/data/<dataset-version>/<name>.parquet`.
- `research/data/manifest.json` is the single source of truth for
  source / vendor / license / date range / row count / checksum.
- The active version is `dataset-v1`. Any new acquisition that
  contradicts a previously-pinned `dataset-v1` entry (different source,
  different range, different schema) creates `dataset-v2`; v1 evidence
  remains valid against v1, never re-graded under v2.
- Parquet via `pyarrow` with explicit `compression="snappy"` and
  `index=False` so checksums are stable across pandas versions.
- Generators live under `research/scripts/`; each generator must be
  deterministic (same inputs → byte-identical output → same SHA256).

## Reproducibility Contract

Every backtest run records `dataset_version` from the manifest. On load,
the harness must verify each declared file's `sha256` matches the
manifest's pinned value; mismatch fails the run rather than silently
re-grading against drifted data. This is a Phase 0 invariant per
`minimum-viable-backtesting-program.md` and is non-negotiable for any
result that wants to count as evidence.

## Gap List With Priority

Sorted by which phase it unblocks (sooner = higher priority).

1. ~~**`spx_spot_intraday` OR `es_or_mes_intraday`** — Tier 1, blocks
   Phase 1.~~ **DONE 2026-04-30** — `es_or_mes_intraday` populated
   via Databento. `spx_spot_intraday` is the secondary path (IBKR,
   no incremental cost) and unblocks SPX-specific directional
   testing; ES alone is sufficient for the falsification step.
2. **`spx_0dte_call_chain`** — Tier 1, blocks Phase 2. Snapshot
   coverage of entry-window + exit-window is enough; full-session
   chain is Tier 2.
3. **VIX series** — blocks Phase 5 regime variants. Defer until
   Phase 1 + 2 conclude `EDGE PRESENT` and the bull call spread
   survives the expression comparison.
4. **Event calendar tags** — blocks Phase 5 event-filter variant.
   Same defer-until-Phase-1+2 logic.
5. **CBOE SPX SET feed** — improves settlement-spot accuracy.
   Already mitigated by code-side sanity band; not a hard blocker for
   Phase 1.
6. **Tier 2 promotion-grade data** — required only after Phase 1+2
   show edge and an expression survives. Acquiring it before that
   point is wasted spend.

## Exit Criterion

Per the tracker NOW item:

> Exit: we know whether fast validation is blocked by missing data or
> just by missing code.

**Answer (updated 2026-04-30)**: Phase 1 (directional-edge) is now
unblocked — `es_or_mes_intraday` is pinned in the manifest. Phase 2
(expression comparison) remains blocked on `spx_0dte_call_chain` (a
Databento PAYG decision per the data-acquisition-decision doc;
deferred until Phase 1 says EDGE PRESENT). The harness contract from
[`live-capital-go-no-go.md`](./live-capital-go-no-go.md) §1.5.2 can
now be implemented and run on real data.
