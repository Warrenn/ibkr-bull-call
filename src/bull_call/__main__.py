"""CLI entrypoint.

Production: ``SSM_PREFIX`` env var is required; settings load from SSM.
Local dev (laptop): if ``SSM_PREFIX`` is unset, fall back to ``.env`` / process
env (the original Settings loader path).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from bull_call.config import Settings, load_settings
from bull_call.scheduler import Scheduler, install_signal_handlers, run_dry_run
from bull_call.state import Store


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="bull_call")
    p.add_argument("--dry-run", action="store_true",
                   help="connect, propose a trade, log it, and exit (no submission)")
    p.add_argument("--env-file", default=".env",
                   help="path to env file (used only when SSM_PREFIX is unset)")
    return p.parse_args()


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _load_settings() -> Settings:
    prefix = os.environ.get("SSM_PREFIX")
    if prefix:
        from bull_call.ssm import load_settings_via_ssm

        region = os.environ.get("AWS_REGION", "us-east-1")
        return load_settings_via_ssm(prefix=prefix, region=region)

    if Path(".env").exists():
        load_dotenv(".env", override=False)
    return load_settings()


def main() -> None:
    args = _parse_args()
    if Path(args.env_file).exists():
        load_dotenv(args.env_file, override=False)

    settings = _load_settings()
    _setup_logging(settings.log_level)
    region = os.environ.get("AWS_REGION", "us-east-1")

    if args.dry_run:
        sys.exit(run_dry_run(settings))

    store = Store(settings.state_table, region=region)
    scheduler = Scheduler(settings, store)
    install_signal_handlers(scheduler)
    try:
        scheduler.run_forever()
    finally:
        store.close()


if __name__ == "__main__":
    main()
