"""Tests for bull_call.cpapi.client.

Cover the shutdown-aware gateway-connect path; the rest of the module is
glue around ``ibind.IbkrClient`` and is exercised live via paper/dry-run.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from bull_call.cpapi import client as cpapi_client


class _Resp:
    def __init__(self, data: Any) -> None:
        self.data = data


class FakeClient:
    """Stand-in for ibind.IbkrClient that lets tests script the auth dance."""

    def __init__(self, scripted_statuses: list[dict[str, Any]]) -> None:
        self._statuses = scripted_statuses
        self._idx = 0
        self.start_tickler_called = False

    def check_auth_status(self) -> _Resp:
        if self._idx < len(self._statuses):
            data = self._statuses[self._idx]
            self._idx += 1
            return _Resp(data)
        return _Resp({"authenticated": False, "connected": False})

    def start_tickler(self) -> None:
        self.start_tickler_called = True


def test_connect_returns_when_should_stop_set_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: SIGTERM during connect's auth-poll loop must not block
    for the full ready_timeout_s. With should_stop_fn=True at start, the
    function must raise RuntimeError within milliseconds rather than waiting
    up to 120s for the gateway."""

    fake = FakeClient([{"authenticated": False, "connected": False}])
    monkeypatch.setattr(cpapi_client, "IbkrClient", lambda **_kw: fake)
    monkeypatch.setattr(cpapi_client.time, "sleep", lambda _s: None)

    wall_before = time.monotonic()
    with pytest.raises(RuntimeError, match="shutdown requested"):
        cpapi_client.connect(
            ready_timeout_s=300.0,
            should_stop_fn=lambda: True,
        )
    elapsed = time.monotonic() - wall_before

    assert elapsed < 1.0, f"connect did not honour should_stop_fn (elapsed={elapsed:.2f}s)"
    # We never even got to start_tickler since the gateway never reported ready.
    assert fake.start_tickler_called is False


def test_connect_returns_client_when_gateway_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity: when auth succeeds, connect returns the client and starts
    the tickler — the should_stop_fn parameter doesn't perturb the happy
    path."""

    fake = FakeClient([{"authenticated": True, "connected": True}])
    monkeypatch.setattr(cpapi_client, "IbkrClient", lambda **_kw: fake)
    monkeypatch.setattr(cpapi_client.time, "sleep", lambda _s: None)

    result = cpapi_client.connect(ready_timeout_s=10.0)
    assert result is fake
    assert fake.start_tickler_called is True


def test_connect_short_circuits_after_some_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """should_stop_fn flips to True after a couple of polls — connect must
    bail at the next iteration, not run the full timeout."""

    fake = FakeClient([
        {"authenticated": False, "connected": False},
        {"authenticated": False, "connected": False},
    ])
    monkeypatch.setattr(cpapi_client, "IbkrClient", lambda **_kw: fake)
    monkeypatch.setattr(cpapi_client.time, "sleep", lambda _s: None)

    flag = {"stop": False}
    polls = [0]

    def stop_fn() -> bool:
        polls[0] += 1
        if polls[0] >= 3:
            flag["stop"] = True
        return flag["stop"]

    with pytest.raises(RuntimeError, match="shutdown requested"):
        cpapi_client.connect(
            ready_timeout_s=300.0,
            should_stop_fn=stop_fn,
        )
    assert polls[0] >= 3
