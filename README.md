# ibkr-bull-call

Autonomous SPX 0DTE bull call spread bot for Interactive Brokers.

Opens one same-day-expiry bull call spread on SPX every trading day at a
configurable time (default **10:30 ET**). Holds to 4:00 pm cash settlement,
with a downside breakeven stop loss and a layered set of operational-safety
fail-safes (data-outage emergency flatten, monthly capital gate, signal-aware
shutdown, session-level crash recovery, orphan-order cleanup, and more — see
[Operational safety](#operational-safety)).

Designed for single-tenant unattended operation on AWS, behind a self-healing
ASG with no public ingress. Talks to IBKR via the **Client Portal Web API**
(via [`ibind`](https://github.com/Voyz/ibind)); login is driven by
[**IBeam**](https://github.com/Voyz/ibeam) with a **custom SSM secrets
provider** so IBKR credentials never live in env vars, files, or git.

Strategy spec is hypothesis-grade — see
[`docs/strategy-review.md`](./docs/strategy-review.md) and
[`docs/live-capital-go-no-go.md`](./docs/live-capital-go-no-go.md) for the
honest assessment of what's known to work vs. what still needs backtest
validation before live capital.

## How it works

1. At `ENTRY_TIME_ET` each trading day, fetches the SPX 0DTE call chain via
   IBKR Client Portal Web API.
2. Picks strikes by walking down through the chain to find the long leg, then
   up to find the short, sized so the spread debit stays under `MAX_LOSS_USD`
   and the Black–Scholes probability of profit is at least `POP_THRESHOLD`.
   The limit price is also capped by `MIN_PROFIT_TO_LOSS_RATIO` if set.
3. Submits the spread as one combo (BAG) limit order at midpoint;
   re-prices once toward `debit_max` if unfilled; cancels and retries
   on a fresh chain after the per-attempt budget. Total entry budget
   `ENTRY_TIMEOUT_SEC`; entry retry continues until `ENTRY_DEADLINE_ET`.
4. Streams SPX spot ticks. If spot rises above breakeven and then crosses
   back below, submits a combo MKT close — but skips the optional stop if
   the realized loss would equal or exceed the max-loss-if-held (i.e. the
   spread is already worth ~$0). Emergency flatten if the feed goes dark.
5. Otherwise, holds to 4:00 pm cash settlement.
6. Records realized P&L in DynamoDB. Skips next-month entries if the
   month-to-date P&L turns negative (configurable).

## Operational safety

Every operational fail-safe is configurable via SSM (or env) and tested.

| Protection | Setting(s) | Default | What it guards against |
|---|---|---|---|
| Reconcile on startup | always on | — | DDB wiped / SIGKILL crash leaving an open IBKR position with no local record |
| Orphan-order cleanup at session start | always on | — | SIGKILL mid-entry leaving a working LMT at IBKR; next instance double-fills |
| Data-outage emergency flatten (R23a) | `monitoringQuoteGraceSec`, `monitoringReconnectMaxAttempts`, `monitoringQuoteMaxBlindSec` | 15 / 3 / 60 | WS feed goes silent on an open 0DTE; bot re-subscribes, then MKT-flattens after the blind window |
| Monthly net-negative capital gate (R9) | `monthlyStopOnNegativePnl` | `true` | Bad month bleeding into worse — no new entries until calendar rolls over |
| Half-day skip | `skipHalfDays` | `true` | NYSE early 1 pm close coincides with default deadline; zero execution headroom |
| SIGTERM-aware everything | always on | — | ASG/docker-stop blocked behind hung syscalls; daemon ignores graceful shutdown |
| Session-level crash recovery + circuit breaker | `sessionErrorBackoffSec`, `sessionErrorMaxConsecutive` | 300 / 5 | Transient blip kills the daemon; ASG respawns + IBeam re-auth + 2FA = expensive |
| Periodic heartbeat | `heartbeatIntervalSec` | 300 | CloudWatch can't tell quiet-by-design from frozen-process during long quiet stretches |
| Settings cross-field validation | always on | — | SSM misconfig (e.g. deadline before entry time, POP > 1) silently produces a do-nothing daemon |

Stateful resources (`StateTable`, `ArtifactsBucket`) carry
`DeletionPolicy: Retain` + `UpdateReplacePolicy: Retain`, so a stack
delete (intentional or accidental) never wipes trade history or release
artifacts.

## Prerequisites

- IBKR account (paper recommended for initial testing) with market data
  subscriptions for **OPRA US Options** + **US Securities Snapshot and
  Futures Value Bundle** (or the equivalent that gives you live SPX
  options + index quotes; delayed data breaks the strategy).
- AWS account with permission to create CloudFormation stacks in your
  chosen region (default `us-east-1`).
- Locally: Python 3.12 + [`uv`](https://docs.astral.sh/uv/).

## Local development

```bash
uv sync --extra dev          # creates .venv with all deps
uv run pytest -q             # runs the full test suite (no IBKR needed)
uv run mypy src              # type check
```

## Configuration

Strategy and operational settings are declared in SSM Parameter Store at
`/<env>/ibkr-bull-call/settings` as a JSON blob (camelCase keys). Each
key is also overridable via environment variable (UPPER_SNAKE_CASE) for
local dev. See [`infra/cloudformation/data.yaml`](./infra/cloudformation/data.yaml)
for the canonical default JSON.

Strategy-side settings:

| SSM key (env equivalent) | Default | Notes |
|---|---|---|
| `maxLossUsd` (`MAX_LOSS_USD`) | *required* | Hard cap on max loss per spread (debit × 100), > 0 |
| `symbols` (`SYMBOLS`) | `SPX` | Comma-separated underlyings |
| `popThreshold` (`POP_THRESHOLD`) | `0.55` | Minimum BS probability of profit, `[0, 1]` |
| `riskFreeRate` (`RISK_FREE_RATE`) | `0.05` | For BS POP calc |
| `minProfitToLossRatio` (`MIN_PROFIT_TO_LOSS_RATIO`) | `null` | If set, caps the limit at `width / (1 + ratio)` |
| `entryTimeEt` (`ENTRY_TIME_ET`) | `10:30` | When to start entry attempts (24h ET) |
| `entryDeadlineEt` (`ENTRY_DEADLINE_ET`) | `13:00` | Stop trying to enter after (24h ET); must be > `entryTimeEt` |
| `entryTimeoutSec` (`ENTRY_TIMEOUT_SEC`) | `300` | Total budget per entry attempt; split 50/50 across initial-price + reprice phases |
| `legFillTimeoutSec` (`LEG_FILL_TIMEOUT_SEC`) | `30` | Post-fill leg-balance verification timeout |
| `stopEnabled` (`STOP_ENABLED`) | `true` | Master switch for the breakeven stop |
| `stopLatestSec` (`STOP_LATEST_SEC`) | `30` | Suppress stop fire in last N seconds before close |

Operational-safety settings (see table above for what each guards):

| SSM key | Default |
|---|---|
| `monthlyStopOnNegativePnl` | `true` |
| `skipHalfDays` | `true` |
| `monitoringQuoteGraceSec` | `15` |
| `monitoringReconnectMaxAttempts` | `3` |
| `monitoringQuoteMaxBlindSec` | `60` |
| `heartbeatIntervalSec` | `300` |
| `sessionErrorBackoffSec` | `300` |
| `sessionErrorMaxConsecutive` | `5` |

> ⚠️ `popThreshold=0.70` is essentially unreachable for SPX 0DTE under
> normal IV (~18%) since the debit can never exceed strike width. The
> default is `0.55`; if the bot consistently logs `no viable spread`,
> consider lowering further or raising `maxLossUsd`.

## Running locally (against IBKR's Client Portal Gateway)

Requires the IBKR Client Portal Gateway running locally — the simplest
path is to run [Voyz/ibeam](https://github.com/Voyz/ibeam) in Docker
configured with your IBKR username / password. Confirm the gateway is
authenticated and reachable at `https://localhost:5000` before starting
the bot.

```bash
cp .env.example .env         # fill in MAX_LOSS_USD plus any overrides
uv run python -m bull_call --dry-run   # propose a trade, log it, exit
uv run python -m bull_call             # live run
```

## Deploy to AWS (us-east-1)

CloudFormation deploys an EC2 t4g.small inside a self-healing ASG (min
= max = desired = 1). The instance runs IBeam (manages IBKR's Client
Portal Gateway via Selenium login) and the Python bot natively as a
systemd service.

**No credentials ever live as env vars, in files, or anywhere
persistent on the host.** IBeam invokes `bull_call.ibeam_ssm_provider`
at login time; the bot reads its strategy settings from SSM via boto3.

```bash
# Validate templates
infra/scripts/deploy.sh validate dev

# Deploy all 3 stacks (data → network → compute)
infra/scripts/deploy.sh deploy dev

# Seed IBKR credentials into SSM (replaces the placeholder values)
infra/scripts/seed-secrets.sh dev

# Build + upload a release tarball (optional, for versioned rollouts)
infra/scripts/release.sh v0.1.0 dev

# Open a shell on the instance via SSM Session Manager (no SSH ports open)
aws ssm start-session --target <instance-id> --region us-east-1
```

**Daily operations**: IBKR sessions expire after ~24h. IBeam detects
expiry, re-fetches creds from SSM, posts a fresh login. IBKR sends a
2FA push to your IBKR Mobile app — tap approve. Bot resumes
automatically.

**Stack deletion is non-destructive.** `DeletionPolicy: Retain` on the
DynamoDB state table and S3 release bucket means a `cfn delete-stack`
leaves trade history and release tarballs intact. To actually delete
either, run `aws dynamodb delete-table` / `aws s3 rb --force` after the
stack is gone.

## Project layout

```
src/bull_call/
  config.py             # Settings dataclass + env / SSM loader (typed-parse helpers, cross-field validation)
  calendar.py           # NYSE sessions, holiday/half-day detection
  pricing.py            # Black–Scholes POP
  strikes.py            # descending/ascending strike selection (pure)
  state.py              # DynamoDB single-table store + stop journal + monthly P&L
  stop.py               # arm/fire/suppress state machine (pure)
  events.py             # structured JSON event logging for CloudWatch
  ssm.py                # SSM Parameter Store loader for the settings JSON
  ibeam_ssm_provider.py # SsmSecretsProvider (no creds on disk)
  cpapi/                # IBKR Client Portal Web API integration
    client.py           #   gateway connect + tickler + account selection
    chain.py            #   0DTE chain fetch + ATM IV + close-credit estimate
    execution.py        #   combo BAG entry LMT + close MKT + leg balance + orphan cleanup
    spot.py             #   WS spot tick stream with R23a silence sentinels
    reconcile.py        #   detect existing IBKR positions on startup
  strategy.py           # propose → open → monitor → settle (library-agnostic; injectable)
  scheduler.py          # day loop, signal handling, heartbeat, session-error circuit breaker
  __main__.py           # CLI (--dry-run + live)
infra/
  cloudformation/       # data / network / compute stacks
  scripts/              # deploy.sh, release.sh, seed-secrets.sh
tests/                  # 278 tests (no IBKR live coverage; live validation via --dry-run)
docs/                   # strategy-review.md + live-capital-go-no-go.md
```

## Tests

```bash
uv run pytest -q
```

Live IBKR is not unit-tested. End-to-end validation runs in two stages:

1. `--dry-run` against a paper Client Portal Gateway (proposes a trade,
   logs it, exits without submitting).
2. Live paper deployment for the duration specified in
   `docs/live-capital-go-no-go.md` Phase 8 before any live capital.

## Notes & caveats

- **Strategy is hypothesis-grade.** The Black–Scholes POP filter and the
  strike-selection walk are starting points, not validated edges. See
  `docs/strategy-review.md` for the redesign hypotheses (regime gate,
  intraday confirmation, score-and-select strikes) that should be
  backtested before live capital.
- **Paper-test before going live.** Phase 8 of
  `docs/live-capital-go-no-go.md` requires forward paper trading to
  confirm the backtest before any live capital is committed.
- **Market data subscriptions cost money.** Index and option data on
  IBKR isn't free; check subscriptions before deploying.
- **0DTE risk.** Max loss per spread is bounded by `MAX_LOSS_USD`, but
  daily compounding losses are real. Set `MAX_LOSS_USD` so a string of
  bad days is survivable, and trust the monthly capital gate to step in
  when the month-to-date turns negative.
- **Single instance only.** The state table is keyed by `(date, symbol)`,
  so two daemons against the same account would race on order
  submission. The CFN stack is sized at `ASG min=max=desired=1` to
  enforce this.
