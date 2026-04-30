# Contributing

This is a single-author experimental trading bot. The "contributing" model
is: future-me (or anyone resuming) can pick up the code without re-deriving
the conventions. This file captures the workflow expectations.

## Development setup

```bash
# Python 3.12 required.
brew install uv shellcheck cfn-lint   # macOS; see uv docs for Linux
uv sync --extra dev                    # creates .venv with all deps
```

Sanity check:

```bash
uv run pytest -q                                          # full test suite
uv run pytest --cov=src/bull_call --cov-fail-under=85    # same as CI
uv run mypy src                                           # type check
shellcheck infra/scripts/*.sh                             # shell linting
cfn-lint infra/cloudformation/*.yaml                      # CFN templates
```

Each of these maps 1:1 to a CI check. If they pass locally, CI passes.

Optional: install pre-commit hooks so the same checks run automatically
before each commit / push:

```bash
uv pip install pre-commit
pre-commit install                 # commit-time hooks (whitespace, yaml, toml)
pre-commit install --hook-type pre-push   # push-time hooks (pytest, mypy)
```

Hook config is in `.pre-commit-config.yaml`. CI is the authoritative
check; pre-commit just makes the feedback loop tight when iterating.

## Branching + PR workflow

1. **Always branch off `main`.** Never commit directly to main.
2. Branch naming follows the prefix-by-intent convention used in
   `git log`:
   - `feat/<feature>` — new behaviour
   - `fix/<thing>` — bug fix
   - `test/<module>` — test coverage additions
   - `docs/<area>` — pure documentation
   - `ci/<thing>` — CI / infra-script changes
3. Open a PR against `main`. CI runs:
   - `pytest + mypy` (with **85% coverage floor** — see
     `.github/workflows/ci.yml`)
   - `shellcheck` on `infra/scripts/*.sh`
   - `cfn-lint` on `infra/cloudformation/*.yaml`
   - `Sourcery` automated review (commented inline; rate-limited
     weekly so don't depend on it)
4. Address review feedback via additional commits on the same branch.
5. **Squash-merge** with `--delete-branch` once green:
   ```bash
   gh pr merge <n> --squash --delete-branch
   ```
6. **Stateful resources are retained on stack delete** — see
   `infra/cloudformation/data.yaml`. To actually wipe DDB / S3, run the
   explicit `aws dynamodb delete-table` / `aws s3 rb --force` after
   `deploy.sh destroy`.

## TDD workflow

For any change with testable behaviour:

1. Write the failing test FIRST (red).
2. Write the minimum code to pass it (green).
3. Refactor while staying green.
4. Update the strategy file `## Progress` checkbox if working from
   `STRATEGY.md` (deleted on this branch — strategy lives in
   `docs/strategy-review.md` now).

For shell scripts, CFN, or YAML — substitute validation in place of
unit tests:

- `shellcheck` for `*.sh`
- `cfn-lint` for CloudFormation YAML
- `tests/test_cfn_templates.py` already pins critical CFN invariants
  (DeletionPolicy: Retain, etc.) and runs as a regular pytest.

## Test conventions

- Place tests in `tests/test_<module>.py` mirroring `src/bull_call/<module>.py`.
- Use `pytest` parametrize over duplicate test bodies.
- For DDB-touching tests, use the existing `store` fixture from
  `tests/conftest.py` (moto-backed; no AWS network calls).
- For cpapi modules, build duck-typed `FakeClient` classes inline; use
  `_make_*_client` helpers when fakes get reused across tests.
- `caplog` is the right tool to verify structured event emission
  (`bull_call.events` logger) and operator-facing log lines.

## Operational-safety bias

Every PR that touches the trading hot path should ask:

- What happens on **SIGKILL mid-operation**? (Reconcile? Orphan order?
  Half-filled state?)
- What happens on **delayed market data**? (R23a outage flatten covers
  the open-position side; entry-side via `require_realtime` flag.)
- What happens on **a misconfigured SSM value**? (Cross-field validation
  in `Settings.load_settings` — extend it for new settings.)
- Does the change emit a **structured event** for CloudWatch Insights?
  (`events.emit("event_name", **fields)`.)

The bias is towards **fail loud** at startup (raise ValueError on
misconfig) and **fail safe** during operation (emergency flatten, never
silent drift).

## Strategy hypotheses vs. operational changes

`docs/strategy-review.md` enumerates rules R1–R30 and invariants I1–I9.
Any change that's a **strategy hypothesis** (regime gate, intraday
confirmation, score-and-select strikes, etc.) is **NOT a candidate for a
direct PR** — it requires backtest validation per
`docs/live-capital-go-no-go.md` Phase 1–9 first.

**Operational-safety changes** (new fail-safe, log line, validation,
test coverage) ship via the normal PR flow described above.

## Strategy file (STRATEGY.md)

- For multi-step work, create `STRATEGY.md` in the repo root with the
  goal, approach, numbered steps, test strategy, and a `## Progress`
  checklist.
- Update the checkbox immediately on each step completion (not batched).
- Delete `STRATEGY.md` after the work is shipped + merged.
- Keep `docs/strategy-review.md` and `docs/live-capital-go-no-go.md`
  intact — those are reference material, not session strategy.

## Work log

- Update `~/.claude/work-logs/YYYY-MM.md` at concrete milestones (end of
  task, before commit, end of session).
- One entry per logical task; `# / Project / Task / Hours / Detail / PRs`.
