"""Shared pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip env vars our config reads so each test starts clean."""

    for key in list(os.environ):
        if key.startswith(("IB_", "TWS_", "TRADING_MODE", "SYMBOLS", "MAX_LOSS",
                           "POP_THRESHOLD", "RISK_FREE_RATE", "ENTRY_TIME_ET",
                           "STOP_", "STATE_DIR", "LOG_LEVEL")):
            monkeypatch.delenv(key, raising=False)
    yield
