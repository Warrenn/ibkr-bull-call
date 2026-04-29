# STRATEGY: AWS deployment via CloudFormation

## Goal

Host the ibkr-bull-call bot on AWS in **us-east-1** (Cape Town). All credentials and runtime settings live in SSM Parameter Store; the Python code reads everything from SSM at runtime — **no credentials in any environment variable, file, or persistent location anywhere on the host**. CloudFormation provisions everything reproducibly. Daily 2FA-on-mobile is accepted as the unattended-operation cost.

## Pivot: IBKR Client Portal Web API + IBeam (with custom SSM secrets provider)

Original design used `ib_async` + IB Gateway + IBC (autologin). That approach requires `TWS_USERID`/`TWS_PASSWORD` to live in IBC's process env vars for IB Gateway's lifetime — unacceptable per the no-env-vars policy.

New design uses **IBKR's Client Portal Web API** via the `ibind` Python library, with **IBeam** managing the gateway login. IBeam supports custom Python secrets providers — we write one that fetches from SSM Parameter Store via boto3 at the moment of login.

### Components on the EC2

- **IBeam container** (`voyz/ibeam:latest`): runs IBKR's Client Portal Gateway (Java), Chromium, Selenium, and IBeam itself. Configured to use our custom secrets provider at login time. Listens on `127.0.0.1:5000`.
- **bull-call-bot** (native Python, systemd): connects to `127.0.0.1:5000` via `ibind`. Reads its own `settings` from SSM via boto3 at startup. Never sees IBKR credentials at all.
- **Custom IBeam SSM provider** (`bull_call.ibeam_ssm_provider`): a small Python class IBeam imports. When called, uses boto3 to fetch `tws_userid` + `tws_password` from SSM, returns them as local Python strings. They're consumed by Selenium WebDriver to type into the login form, then dereferenced.

### Credential lifecycle — what touches what, and for how long

| Subject | Sees credentials? | Where they live |
|---|---|---|
| EC2 user-data | ❌ never | — |
| systemd unit `ibeam.service` | ❌ never (no `Environment=`) | — |
| `bull-call-bot.service` | ❌ never | — |
| IBeam container env vars | ❌ never (`IBEAM_ACCOUNT`/`IBEAM_PASSWORD` are not set) | — |
| IBeam Python process | ✅ briefly during login | Python `str` from SSM, used in WebDriver `send_keys()`, then GC'd |
| Selenium / Chromium | ✅ briefly during login | DOM input field, posted via JS to IBKR's auth servers, then page navigates away |
| Bot process | ❌ never (it just talks to the localhost gateway via session cookie after IBeam logged in) | — |
| EC2 disk | ❌ never (no `.env`, no config file with creds, no shell history) | — |
| Daily 2FA push | ✅ user taps approve on phone | — |

Daily cadence: IBKR session expires every ~24h. IBeam detects, calls our SSM provider for fresh creds, types into a fresh login dialog, IBKR pushes 2FA, user taps approve. Bot reconnects and resumes.

## Approach

### Architecture

Single **EC2 t4g.small** in a new minimal VPC (one public subnet, no NAT). The instance runs the existing `docker-compose` stack (`ibgateway` + `bot`). State persists on the root EBS volume (`/var/lib/bull-call/state`), backed up via daily snapshot lifecycle.

Why EC2 + Compose, not ECS/Fargate:
- Cheapest by ~50% (Fargate has no free tier, t4g.small in us-east-1 is ~$16/mo)
- IB Gateway is a stateful Java app; auto-restart-on-replace via Fargate is hostile to its session model (it'd re-trigger 2FA flows on every redeploy, problematic for live mode)
- Existing Docker setup transfers verbatim — no task definition rewrite

### Parameter Store layout (matches existing convention)

Following the pattern from `/dev/auto-invest/ibeam_account` (SecureString) + `/dev/funding-arbitrage/settings` (String JSON):

| Parameter | Type | Value |
|---|---|---|
| `/dev/ibkr-bull-call/tws_userid` | `SecureString` | IBKR username |
| `/dev/ibkr-bull-call/tws_password` | `SecureString` | IBKR password |
| `/dev/ibkr-bull-call/settings` | `String` | JSON: `{"tradingMode":"paper","symbols":"SPX","maxLossUsd":200,"popThreshold":0.55,"riskFreeRate":0.05,"entryTimeEt":"10:30","stopEnabled":true,"stopLatestSec":30,"logLevel":"INFO"}` |

CloudFormation creates the three parameters with placeholder values; the user (or `seed-secrets.sh`) updates the secrets via `aws ssm put-parameter --overwrite` after the stack is up. **CloudFormation never holds the actual secret in template state.**

The `live/` env mirror is created by the same template using a `--parameter-overrides Env=live` invocation.

### Code changes

The bot's `Settings` dataclass stays the same. A new module `bull_call.ssm` fetches and parses parameters; `config.load_settings()` gains an SSM-backed mode triggered by `SSM_PREFIX` env var (the *only* env var allowed at runtime). When `SSM_PREFIX` is set, the loader reads:

1. `<prefix>/settings` → JSON, mapped onto Settings fields (camelCase → snake_case)
2. `IB_HOST` and `IB_PORT` use static defaults (`ibgateway` / `4002`) since they describe Docker-network topology, not user config

Local dev still works with `.env` (when `SSM_PREFIX` is unset). CI tests don't need AWS — they continue to use env-only loading.

**boto3 is a new dependency** (used only when `SSM_PREFIX` is set; lazy-imported so test runs without AWS creds don't fail).

### CloudFormation stacks

Three small templates (each independently updateable):

| Stack | File | Purpose |
|---|---|---|
| `bull-call-data-{env}` | `infra/cloudformation/data.yaml` | SSM parameters with placeholder values. Outputs the prefix. |
| `bull-call-network-{env}` | `infra/cloudformation/network.yaml` | VPC, public subnet, IGW, default SG (egress-only). Outputs subnet & SG IDs. |
| `bull-call-compute-{env}` | `infra/cloudformation/compute.yaml` | EC2 instance, IAM role + instance profile, EIP, EBS volume, user data. Imports outputs from the other two stacks. |

User data on first boot:

1. `dnf install -y docker git awscli` and enable docker daemon
2. Install docker-compose v2 plugin
3. Clone the project from GitHub
4. Install `bull-call.service` systemd unit which calls `start-bull-call`
5. Enable + start the service

`start-bull-call` (in `/usr/local/bin/`, mode 0750, no credentials in the script itself):

```bash
#!/bin/bash
set -euo pipefail
TWS_USERID=$(aws ssm get-parameter --region us-east-1 --name "${SSM_PREFIX}/tws_userid" --with-decryption --query Parameter.Value --output text)
TWS_PASSWORD=$(aws ssm get-parameter --region us-east-1 --name "${SSM_PREFIX}/tws_password" --with-decryption --query Parameter.Value --output text)
export TWS_USERID TWS_PASSWORD SSM_PREFIX
cd /opt/bull-call
exec docker compose -f docker/docker-compose.yml up
```

Credentials live only in process memory:
- They're in env vars of the systemd service's child processes (the ibgateway container, briefly the start-bull-call shell)
- They NEVER touch the EC2 root volume, never written to a `.env` file
- On reboot, systemd re-runs the script which re-fetches from SSM
- The bot itself does not even receive these env vars — only `SSM_PREFIX`, and uses its IAM role to read its own settings from SSM

### Code delivery — two options

**Option A (simpler, picked):** EC2 user data clones the GitHub repo at boot. Updates via `scripts/aws/update.sh` which SSH's in and runs `git pull && docker compose up -d --build`. Requires the repo to be public *or* an SSH deploy key in SSM.

**Option B (cleaner, deferred):** Build the bot image locally, push to ECR in us-east-1, EC2 pulls from ECR. Cleaner separation but adds an ECR repo to manage and a build/push step. Worth it for live mode but overkill for paper.

### Security

- Instance IAM role: `ssm:GetParameter*` scoped to `arn:aws:ssm:us-east-1:<acct>:parameter/dev/ibkr-bull-call/*` only; `kms:Decrypt` on the AWS-managed `alias/aws/ssm` key
- Security group: **no ingress** (zero open ports). Egress to `0.0.0.0/0` on 443/tcp (IBKR + GitHub + Docker Hub)
- SSH access: via Session Manager only (SSM agent is preinstalled on Amazon Linux 2023 AMIs). No public SSH keys, no port 22 open. Operator uses `aws ssm start-session --target i-xxx`
- Outbound IBKR ports: IBKR Gateway connects to `gdc1.ibllc.com` (and others) on TCP 4000/4001. Egress allows all TCP outbound for simplicity; could be tightened to specific IBKR hostnames later
- The `.env` file with TWS credentials lives at `/etc/bull-call/.env` mode `0600`, only readable by root and Docker. The bot itself never writes or reads it — only `ibgateway` consumes it

### Files

```
infra/cloudformation/
  data.yaml          # SSM params
  network.yaml       # VPC + subnet + SG
  compute.yaml       # EC2 + IAM + user data
infra/parameters/
  dev.json           # Stack parameters for `dev` env
  live.json          # Stack parameters for `live` env
infra/scripts/
  deploy.sh          # Orchestrates: validate → deploy 3 stacks in order
  seed-secrets.sh    # Interactive: prompts for IBKR creds, puts to SSM
  update-bot.sh      # SSM RunCommand to git-pull + compose up on the EC2
src/bull_call/
  ssm.py             # boto3-based parameter fetcher (lazy-imported)
  config.py          # Extended with load_settings_from_ssm()
docs/
  STRATEGY-aws-deploy.md  # this file
```

### Test strategy

- **`tests/test_ssm.py`** — unit tests for `bull_call.ssm` using `botocore.stub.Stubber`. Covers: settings JSON parse, missing param error, partial overrides, camelCase→snake_case mapping. No real AWS calls.
- **`tests/test_config.py`** — extend with `test_load_settings_from_ssm` using a fake fetcher (dependency injection)
- **CloudFormation validation** — `aws cloudformation validate-template` and `cfn-lint` on each yaml file. Run as part of `infra/scripts/deploy.sh` validate step.

### Risk & operational guards

- Stack updates are non-destructive: `data.yaml` updates won't replace SSM values (uses `Default` only on creation; runtime updates use `aws ssm put-parameter`)
- EC2 instance metadata uses IMDSv2 only (block IMDSv1)
- EBS volume encrypted with default `alias/aws/ebs` KMS key
- CloudWatch agent NOT installed initially (cost optimization); add later if needed
- Single-AZ deployment — IBKR connection is single-stream anyway, no benefit from multi-AZ for this workload
- Cost expected: **~$20/mo** in us-east-1 (t4g.small + 20GB gp3 + EIP)

---

## Implementation steps

1. Tests + impl `bull_call.ssm` (parameter fetcher, JSON settings parser)
2. Extend `bull_call.config` with `load_settings_from_ssm(prefix, region)`; modify `__main__` to detect `SSM_PREFIX`
3. Add `boto3` to optional `[ssm]` extra in pyproject; lazy-import in `ssm.py`
4. Write `infra/cloudformation/data.yaml` (SSM params with placeholders)
5. Write `infra/cloudformation/network.yaml` (VPC/subnet/SG)
6. Write `infra/cloudformation/compute.yaml` (EC2 + IAM + user data)
7. Write `infra/scripts/deploy.sh` (validate + deploy 3 stacks in order)
8. Write `infra/scripts/seed-secrets.sh` (interactive secret put)
9. Write `infra/scripts/update-bot.sh` (SSM RunCommand pull-and-restart)
10. Update `README.md` with AWS deploy section
11. Validate CloudFormation templates (`aws cloudformation validate-template` and `cfn-lint` if installed)
12. Deploy `data` stack (creates SSM params with placeholders) — verify in console
13. (Operator step) Run `seed-secrets.sh` to put real IBKR creds into SSM
14. Deploy `network` stack
15. Deploy `compute` stack — instance comes up, user data runs, compose stack starts
16. Verify via `aws ssm start-session` + `docker compose logs bot`

---

## Verification

| Check | Command | Expected |
|---|---|---|
| Templates valid | `infra/scripts/deploy.sh validate dev` | All 3 templates pass `aws cloudformation validate-template` |
| Unit tests | `uv run pytest -q` | All green incl. new `test_ssm.py` |
| Type check | `uv run mypy src` | Clean |
| Data stack up | `aws cloudformation describe-stacks --stack-name bull-call-data-dev --profile busyweb --region us-east-1` | `CREATE_COMPLETE` |
| Params present | `aws ssm get-parameters-by-path --path /dev/ibkr-bull-call --profile busyweb --region us-east-1` | 3 parameters returned |
| Secrets seeded | (after seed-secrets.sh) `aws ssm get-parameter --name /dev/ibkr-bull-call/tws_userid --with-decryption --profile busyweb --region us-east-1` | Real value (not placeholder) |
| Network stack up | `describe-stacks bull-call-network-dev` | `CREATE_COMPLETE` |
| Compute stack up | `describe-stacks bull-call-compute-dev` | `CREATE_COMPLETE` |
| Instance reachable | `aws ssm start-session --target <instance-id>` | Shell session opens |
| Containers running | (in SSM session) `docker ps` | `ibgateway` + `bot` both healthy |
| Bot reads SSM | (in SSM session) `docker logs bot 2>&1 \| grep "loaded settings"` | Confirms SSM-sourced settings |
| Bot dry-run | (in SSM session) `docker exec bot python -m bull_call --dry-run` | Logs proposed combo from live IBKR paper |

End-to-end success criterion: the compute stack creates an EC2 that, on first boot, fetches credentials from SSM, starts the compose stack, the bot reads its strategy settings from SSM (logs include the resolved Settings), connects to IBKR paper, and a `--dry-run` from inside the container produces a proposed spread.

---

## Progress

- [x] 1. Tests + impl `bull_call.ssm` — 2026-04-29
- [x] 2. `bull_call.ibeam_ssm_provider` (custom IBeam secrets provider) — 2026-04-29
- [x] 3. Add `boto3` + `ibind` optional dependencies (`[aws]` extra) — 2026-04-29
- [x] 4. CloudFormation `data.yaml` (validates clean) — 2026-04-29
- [x] 5. CloudFormation `network.yaml` (validates clean) — 2026-04-29
- [x] 6. CloudFormation `compute.yaml` (validates clean) — 2026-04-29
- [x] 7. `infra/scripts/deploy.sh` — 2026-04-29
- [x] 8. `infra/scripts/seed-secrets.sh` — 2026-04-29
- [x] 9. systemd units (`ibeam.service`, `bull-call-bot.service`) — 2026-04-29
- [x] 10. README — AWS deploy section — 2026-04-29
- [x] 11. Template validation pass (all 3 templates valid) — 2026-04-29
- [x] 12. Phase 2 — Replace `ib_async` chain/execution/ibkr with `cpapi/*` modules using ibind — 2026-04-29
- [x] 13. Phase 2 — Convert async `strategy`/`scheduler` to sync (ibind is sync) — 2026-04-29
- [x] 14. Phase 2 — Wire SSM-loaded settings + cpapi into `__main__` — 2026-04-29
- [ ] 15. Deploy `data` stack
- [ ] 16. Seed secrets (operator action — TWS_USERID + TWS_PASSWORD)
- [ ] 17. Deploy `network` stack
- [ ] 18. Deploy `compute` stack
- [ ] 19. End-to-end paper validation (single live session)

**Phase 1 (AWS infrastructure) and Phase 2 (CPAPI rewrite) are both complete and validated.** 101 tests passing. Ready to deploy.
