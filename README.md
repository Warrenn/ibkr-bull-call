# ibkr-bull-call

Autonomous SPX 0DTE bull call spread bot for Interactive Brokers.

Opens one same-day-expiry bull call spread on SPX every trading day at a configurable time (default **10:30 ET**). Holds to 4:00 pm cash settlement, with a downside breakeven stop loss. Runs unattended in Docker.

## How it works

1. At `ENTRY_TIME_ET` each trading day, fetches the SPX 0DTE call chain via IBKR.
2. Picks strikes by walking down through the chain to find the long leg, then up to find the short, sized so the spread debit stays under `MAX_LOSS_USD` and the Black–Scholes probability of profit is at least `POP_THRESHOLD`.
3. Submits the spread as one combo (BAG) limit order at midpoint.
4. Streams SPX spot ticks. If spot rises above breakeven and then crosses back below, submits a combo MKT close — but skips the stop if the realized loss would equal or exceed the max-loss-if-held (i.e. the spread is already worth ~$0).
5. Otherwise, holds to 4:00 pm cash settlement.

The full strategy spec lives in [`STRATEGY.md`](./STRATEGY.md).

## Prerequisites

- IBKR account (paper recommended for initial testing) with market data subscriptions for **OPRA US Securities Snapshot and Futures Value Bundle** (or at minimum US options + index data)
- Docker + Docker Compose v2 on whatever box runs the bot

## Local development

```bash
uv sync --extra dev          # creates .venv with all deps
uv run pytest -q             # runs the full test suite (no IBKR needed)
uv run mypy src              # type check
```

## Configuration

All runtime config is via env vars (loaded from `.env` if present). See [`.env.example`](./.env.example) for the full list.

| Variable | Default | Notes |
|---|---|---|
| `MAX_LOSS_USD` | *required* | Hard cap on max loss per spread (debit × 100) |
| `SYMBOLS` | `SPX` | Comma-separated underlyings |
| `POP_THRESHOLD` | `0.70` | Minimum BS probability of profit |
| `RISK_FREE_RATE` | `0.05` | For BS POP calc |
| `ENTRY_TIME_ET` | `10:30` | When to scan & open (24h ET) |
| `STOP_ENABLED` | `true` | Master switch for the breakeven stop |
| `STOP_LATEST_SEC` | `30` | Suppress stop fire in last N seconds before close |

> ⚠️ `POP_THRESHOLD=0.70` may be unreachable for SPX 0DTE under normal IV (~18%). If the bot consistently logs `no viable spread`, lower the threshold (e.g. `0.55`) or raise `MAX_LOSS_USD`.

## Running locally (against IBKR's Client Portal Gateway)

1. Download IBKR's Client Portal Gateway and IBeam (or run them via Docker locally — see [Voyz/ibeam](https://github.com/Voyz/ibeam)). Configure with your IBKR credentials so the gateway is authenticated and reachable at `https://localhost:5000`.
2. `cp .env.example .env` and fill in `MAX_LOSS_USD` plus any overrides.
3. Dry-run (proposes a trade, logs it, exits without submitting):

   ```bash
   uv run python -m bull_call --dry-run
   ```

4. Live run (will submit orders):

   ```bash
   uv run python -m bull_call
   ```

## Deploy to AWS (us-east-1)

CloudFormation deploys an EC2 t4g.small running IBeam (manages IBKR's Client Portal Gateway via Selenium login) + the Python bot natively as a systemd service. **No credentials ever live as env vars, in files, or anywhere persistent on the host** — IBeam invokes a custom SSM secrets provider (`bull_call.ibeam_ssm_provider`) at login time; the bot reads its strategy settings from SSM via boto3.

See [`docs/STRATEGY-aws-deploy.md`](docs/STRATEGY-aws-deploy.md) for the full design rationale and credential-lifecycle audit.

```bash
# Validate templates only
infra/scripts/deploy.sh validate dev

# Deploy all 3 stacks (data → network → compute)
infra/scripts/deploy.sh deploy dev

# Seed real IBKR credentials into SSM (replaces the placeholder values)
infra/scripts/seed-secrets.sh dev

# Open a shell on the instance via SSM Session Manager (no SSH ports open)
aws ssm start-session --target <instance-id> --profile busyweb --region us-east-1
```

**Daily operations**: IBKR sessions expire after ~24h. IBeam detects, re-fetches creds from SSM, posts a fresh login. IBKR sends a 2FA push to your IBKR Mobile app — tap approve. Bot resumes.

**Required IBKR market-data subscriptions**: OPRA US Options (~$1.50/mo) + US Securities Snapshot and Futures Value Bundle (~$10/mo). Delayed data breaks the strategy.

## Project layout

```
src/bull_call/
  config.py     # env loading (Settings dataclass)
  calendar.py   # NYSE sessions, holiday/half-day skip
  pricing.py    # Black–Scholes POP
  strikes.py    # descending/ascending strike selection (pure)
  state.py      # SQLite store + stop journal
  stop.py       # arm/fire/suppress state machine (pure)
  ibkr.py       # ib_async connection helpers
  chain.py      # 0DTE chain fetch + spot stream + close-credit estimate
  execution.py  # combo BAG entry LMT + close MKT
  strategy.py   # propose → open → monitor → settle
  scheduler.py  # day loop
  __main__.py   # CLI
docker/         # Dockerfile + compose
scripts/        # deploy.sh
tests/          # pytest suite (81+ tests)
```

## Tests

```bash
uv run pytest -q
```

Live IBKR is not unit-tested. End-to-end validation uses `--dry-run` against a paper Gateway.

## Notes & caveats

- **Paper-test before going live.** The strategy is mechanical but the realized P&L distribution depends heavily on IV regime — paper-trade for at least a month before flipping `TRADING_MODE=live`.
- **Market data subscriptions cost money.** Index and option data on IBKR isn't free; check your subscriptions before deploying live.
- **0DTE risk.** Max loss per spread is bounded by `MAX_LOSS_USD`, but daily compounding losses are real. Set `MAX_LOSS_USD` so a string of bad days is survivable.
- **Single instance only.** SQLite is local; running two bots against one account would double-open. Don't.
