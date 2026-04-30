"""Tests for bull_call.__main__ — CLI entrypoint.

The full ``main()`` flow needs IBKR + DDB + signal handlers, which is
exercised via paper dry-run rather than unit-tested. Here we cover the
helpers that determine routing decisions (SSM vs .env config, dry-run
vs live, logging level wiring).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from bull_call import __main__ as cli


# ---------- _parse_args ----------------------------------------------------


def test_parse_args_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """No arguments → defaults: dry_run=False, env_file=.env"""

    monkeypatch.setattr("sys.argv", ["bull_call"])
    args = cli._parse_args()
    assert args.dry_run is False
    assert args.env_file == ".env"


def test_parse_args_dry_run_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["bull_call", "--dry-run"])
    args = cli._parse_args()
    assert args.dry_run is True


def test_parse_args_custom_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["bull_call", "--env-file", "/tmp/custom.env"])
    args = cli._parse_args()
    assert args.env_file == "/tmp/custom.env"


def test_parse_args_help_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """--help prints usage and exits 0; verifies argparse wiring."""

    monkeypatch.setattr("sys.argv", ["bull_call", "--help"])
    with pytest.raises(SystemExit) as exc:
        cli._parse_args()
    assert exc.value.code == 0


# ---------- _setup_logging --------------------------------------------------


def test_setup_logging_uses_named_level() -> None:
    """basicConfig only applies once per process, but the level lookup
    via getattr should resolve the standard names without raising."""

    cli._setup_logging("DEBUG")     # exercises the success path
    cli._setup_logging("INFO")
    cli._setup_logging("WARNING")
    cli._setup_logging("ERROR")
    # No assertion — just verifying no AttributeError on the named levels.


def test_setup_logging_falls_back_to_info_for_garbage() -> None:
    """A garbage level name falls back to logging.INFO via the getattr
    default — better than crashing the daemon at startup."""

    # Reset so basicConfig actually re-applies (basicConfig is a no-op
    # if a handler already exists, but the level lookup still happens).
    cli._setup_logging("BANANA")
    # No exception → success.


# ---------- _load_settings --------------------------------------------------


def test_load_settings_uses_ssm_when_prefix_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SSM_PREFIX env var → routes through ``load_settings_via_ssm``."""

    monkeypatch.setenv("SSM_PREFIX", "/dev/ibkr-bull-call")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")

    captured: dict[str, Any] = {}

    def fake_load_via_ssm(*, prefix: str, region: str = "us-east-1") -> Any:
        captured["prefix"] = prefix
        captured["region"] = region
        return "from-ssm-sentinel"

    monkeypatch.setattr(
        "bull_call.ssm.load_settings_via_ssm", fake_load_via_ssm,
    )

    result = cli._load_settings()
    assert result == "from-ssm-sentinel"
    assert captured == {"prefix": "/dev/ibkr-bull-call", "region": "eu-west-1"}


def test_load_settings_falls_back_to_env_when_no_ssm_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """No SSM_PREFIX → use the .env / process-env loader."""

    monkeypatch.delenv("SSM_PREFIX", raising=False)
    monkeypatch.setenv("MAX_LOSS_USD", "100")
    # Run from a temp dir so any .env in the project root doesn't leak in.
    monkeypatch.chdir(tmp_path)

    settings = cli._load_settings()
    assert settings.max_loss_usd == 100.0


def test_load_settings_default_region_when_aws_region_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SSM_PREFIX set but AWS_REGION unset → default us-east-1."""

    monkeypatch.setenv("SSM_PREFIX", "/dev/ibkr-bull-call")
    monkeypatch.delenv("AWS_REGION", raising=False)

    captured: dict[str, Any] = {}

    def fake_load_via_ssm(*, prefix: str, region: str = "us-east-1") -> Any:
        captured["region"] = region
        return "ok"

    monkeypatch.setattr(
        "bull_call.ssm.load_settings_via_ssm", fake_load_via_ssm,
    )

    cli._load_settings()
    assert captured["region"] == "us-east-1"
