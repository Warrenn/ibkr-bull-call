# STRATEGY: SPX 0DTE Bull Call Spread — Autonomous Trading Bot

## Goal

Autonomous Python bot that opens **same-day-expiry (0DTE) bull call spreads on SPX** every trading day at a configurable entry time, holds them to 4:00 pm cash settlement, and is sized so each spread's debit stays under a configurable max-loss dollar cap. Hard downside stop: combo MKT close when SPX spot crosses below breakeven after the trade was in profit territory.

Paper trading first; same code flips to live by changing IB Gateway port + credentials.

## Approach

- **Language:** Python 3.12 (uv-managed)
- **IBKR client:** `ib_async` (maintained `ib_insync` fork)
- **Runtime:** Cloud VM, Dockerized (`docker compose` with sibling `ibgateway` + `bot` services)
- **Scheduling:** single long-running daemon (no cron)
- **Underlying:** SPX (configurable list; starts with SPX only — cash-settled, European, no assignment)
- **Entry time:** configurable absolute ET time. **Smart default: 10:30 ET** (~1 hour after open) — past the opening-print noise, before midday lull
- **Sizing:** always 1 contract; the spread width is the lever sized to consume `MAX_LOSS_USD`
- **POP threshold:** configurable, default `0.70`
- **Exit:** hold to 4:00 pm SPX cash settlement, *with* a downside breakeven stop
- **Stop loss:** combo MKT close when SPX spot crosses below `breakeven` after being above it; suppressed in last 30s before close

### Parallelism Assessment

This build is **not parallelizable**. Modules form a strict dependency chain:
`config → pricing → strikes → state → calendar → stop → ibkr → chain → execution → strategy → scheduler → __main__`.

Tests for `pricing`, `strikes`, `state`, `calendar`, and `stop` are independent of IBKR, so they could in theory be written in parallel — but the value is small (each is a single Python file) and conflict risk on `pyproject.toml` exists. Single-track sequential build is correct.

### Strike-selection algorithm (user-specified)

**Long leg — descending walk:**
For each strike `K` from high to low, with the strike one band above it `K_up`:
- Compute `gap(K) = ask(K) − bid(K_up)`
- Continue descending while `gap(K) < strike_width`
- The **lowest** `K` satisfying `gap(K) < strike_width` is the **long strike**

**Short leg — ascending walk from `long_strike + 1`:**
For each candidate `K'`:
- `net_debit = ask(long) − bid(K')`
- `POP = N(d2)` at `breakeven = long_strike + net_debit`
- Continue ascending while `net_debit × 100 ≤ MAX_LOSS_USD` **and** `POP ≥ POP_THRESHOLD`
- The **highest** `K'` still satisfying both constraints is the **short strike**

If no viable pair, log and skip the day.

### Stop-loss rule

1. `breakeven = long_strike + net_debit_filled` once entry combo fills.
2. Subscribe to live SPX spot via `reqMktData` on `Index("SPX","CBOE")`.
3. **Arm** when spot ≥ breakeven post-entry (or immediately at entry if spot is already above).
4. Once armed, **fire** on first tick where spot < breakeven:
   - Cancel any working orders.
   - Submit a combo **MKT** close (sell long, buy short, atomic BAG).
   - Mark spread `STOPPED`.
5. **Pre-fire economic check** — before submitting the MKT close, estimate the close credit (conservative `bid(long) − ask(short)`). If credit ≤ 0, the realized loss would equal or exceed the max-loss-if-held; skip the stop and let the spread run to settlement (recorded as `UNECONOMIC` in the journal).
6. **Suppress** stop in the last `STOP_LATEST_SEC` (default 30s) before close — let cash settlement run.
7. Stop journal persisted in SQLite for restart recovery.

### POP calculation

```
T_years = seconds_to_4pm_ET / (365.25 × 86400)
d2 = (ln(spot / breakeven) + (r − σ²/2) × T) / (σ × √T)
POP = norm.cdf(d2)
```
where `σ` = ATM IV from IBKR option model (`genericTickList="106"`), `r` = `RISK_FREE_RATE` (default 0.05).

### Configuration (env-driven)

| Env var | Default | Purpose |
|---|---|---|
| `IB_HOST` | `ibgateway` | IB Gateway host (compose service name) |
| `IB_PORT` | `4002` | 4002 paper / 4001 live |
| `IB_CLIENT_ID` | `7` | ib_async client ID |
| `SYMBOLS` | `SPX` | Comma-separated underlying list |
| `MAX_LOSS_USD` | *required* | Hard cap on max loss per spread (debit × 100) |
| `POP_THRESHOLD` | `0.70` | Minimum probability of profit |
| `RISK_FREE_RATE` | `0.05` | For BS POP calc |
| `ENTRY_TIME_ET` | `10:30` | Absolute ET time to scan & open (one hour after open) |
| `STOP_ENABLED` | `true` | Master switch for the breakeven stop |
| `STOP_LATEST_SEC` | `30` | Suppress stop fire in last N seconds before close |
| `LOG_LEVEL` | `INFO` | |

### Project layout

```
src/bull_call/
  __init__.py
  __main__.py          # entrypoint
  config.py            # Settings dataclass, env loading
  calendar.py          # XCBO sessions, holiday skip
  ibkr.py              # ib_async connection helpers
  chain.py             # option chain + IV/quote snapshot
  pricing.py           # Black–Scholes POP
  strikes.py           # selection algorithm (pure)
  execution.py         # combo entry LMT + close MKT
  stop.py              # arm/fire/suppress state machine (pure)
  state.py             # SQLite: spreads + stop_journal
  strategy.py          # one cycle: open → monitor → settle
  scheduler.py         # day loop
tests/
  conftest.py
  test_strikes.py
  test_pricing.py
  test_stop.py
  test_state.py
  test_calendar.py
  test_strategy.py
docker/
  Dockerfile
  docker-compose.yml
scripts/
  deploy.sh
.env.example
pyproject.toml
```

### Test strategy (TDD)

Tests are written before implementation per CLAUDE.md. Critical coverage:
- **strikes** — synthetic chains covering tightest viable, no viable pair, POP-bind, debit-bind, boundary ties
- **pricing** — POP vs textbook BS, monotonicity in breakeven
- **stop** — entry below breakeven (no arm), arm on cross up, fire on cross down after arm, no fire on wiggle, suppressed in last 30s, restart restores armed state from journal
- **state** — round-trip persistence, `today_already_opened` semantics, stop journal events
- **calendar** — Christmas, Thanksgiving, half-day, weekend
- **strategy** — orchestration with `chain`, `execution`, tick stream all faked: happy path, stopped path, settled-without-stop path

Live IBKR not unit-tested. End-to-end uses `--dry-run` flag.

### Risk & operational guards

- One spread per (symbol, trade-date) — SQLite unique constraint
- Skip if NaN IV / wide bid-ask
- Skip on holidays / half-days
- Heartbeat log every 5 min; container healthcheck
- Graceful SIGTERM: cancel working orders, flush state, disconnect

---

## Implementation steps

1. Initialize Python project (`pyproject.toml`, uv, deps, `.env.example`)
2. Tests + impl `config.py`
3. Tests + impl `pricing.py` (POP `N(d2)`)
4. Tests + impl `strikes.py` (selection algorithm — pure)
5. Tests + impl `state.py` (SQLite + stop journal)
6. Tests + impl `calendar.py` (XCBO)
7. Tests + impl `stop.py` (arm/fire state machine — pure)
8. Impl `ibkr.py` (connection helpers; smoke-tested manually)
9. Impl `chain.py` (option chain fetch + IV)
10. Impl `execution.py` (combo entry LMT + close MKT)
11. Tests + impl `strategy.py` (orchestration with fakes)
12. Impl `scheduler.py` (day loop, sleep math, signal handling)
13. Impl `__main__.py` and `--dry-run` CLI
14. `docker/Dockerfile` and `docker-compose.yml`
15. `scripts/deploy.sh`
16. End-to-end paper dry-run (no submission)
17. End-to-end paper live run (one full session)

---

## Verification

| Check | Command | Expected |
|---|---|---|
| Unit tests | `uv run pytest -q` | All green |
| Type check | `uv run mypy src` | Clean |
| Strike algo | `uv run pytest tests/test_strikes.py -v` | All scenarios pass |
| Stop logic | `uv run pytest tests/test_stop.py -v` | Arm/fire/suppress/restart pass |
| Dry-run | `uv run python -m bull_call --dry-run --symbol SPX` | Logs proposed combo; no order |
| Container build | `docker compose -f docker/docker-compose.yml build` | Builds clean |
| Stack up | `docker compose -f docker/docker-compose.yml up -d` | Both services healthy |
| Live paper run | One full trading day | One spread opened at `ENTRY_TIME_ET`, monitor active, settled at 4:00 pm or stopped earlier; row in `state/spreads.db` |
| Restart safety | `docker compose restart bot` mid-session | No re-open; armed state restored from journal; reconciles at 4 pm |
| Holiday skip | Run on a known holiday | No trades; "market closed" log |

End-to-end success: bot runs unattended for one full week of paper trading, opens one SPX 0DTE spread per session at the configured time, holds to 4 pm (or stops on breakeven cross), records settlement P&L, and the state file shows exactly one row per trading day with `max_loss ≤ MAX_LOSS_USD`.

---

## Progress

- [x] 1. Initialize Python project (pyproject.toml, uv, deps, .env.example) — 2026-04-29
- [x] 2. Tests + impl `config.py` — 2026-04-29
- [x] 3. Tests + impl `pricing.py` — 2026-04-29
- [x] 4. Tests + impl `strikes.py` — 2026-04-29
- [x] 5. Tests + impl `state.py` — 2026-04-29
- [x] 6. Tests + impl `calendar.py` — 2026-04-29
- [x] 7. Tests + impl `stop.py` — 2026-04-29
- [x] 8. Impl `ibkr.py` — 2026-04-29
- [x] 9. Impl `chain.py` — 2026-04-29
- [x] 10. Impl `execution.py` — 2026-04-29
- [x] 11. Tests + impl `strategy.py` — 2026-04-29
- [x] 12. Impl `scheduler.py` — 2026-04-29
- [x] 13. Impl `__main__.py` + `--dry-run` — 2026-04-29
- [x] 14. Dockerfile + docker-compose.yml — 2026-04-29
- [x] 15. scripts/deploy.sh — 2026-04-29
- [ ] 16. End-to-end paper dry-run (requires IBKR paper credentials)
- [ ] 17. End-to-end paper live run (requires IBKR paper credentials)
