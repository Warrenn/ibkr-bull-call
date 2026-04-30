"""Client Portal Gateway connection wrapper.

Wraps ``ibind.IbkrClient`` with the lifecycle bits we care about: waiting for
the IBeam-managed gateway to be healthy, starting the auto-tickle keepalive,
and exposing the active account ID.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from ibind import IbkrClient

from bull_call.cpapi import ShutdownRequested

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    base_url: str = "https://localhost:5000/v1/api"
    cacert: str | bool = False  # IBeam uses a self-signed cert; False disables verification


def connect(
    config: GatewayConfig | None = None,
    *,
    ready_timeout_s: float = 120.0,
    should_stop_fn: Callable[[], bool] = lambda: False,
) -> IbkrClient:
    """Build an ``IbkrClient`` and block until the gateway reports authenticated.

    ``should_stop_fn`` is checked between auth polls so a SIGTERM during
    the up-to-120s startup wait exits promptly with a RuntimeError instead
    of blocking the daemon for the full window.
    """

    cfg = config or GatewayConfig()
    client = IbkrClient(url=cfg.base_url, cacert=cfg.cacert)

    deadline = time.monotonic() + ready_timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        if should_stop_fn():
            raise ShutdownRequested(
                "shutdown requested before gateway became ready"
            )
        try:
            status = client.check_auth_status()
            if status.data.get("authenticated") and status.data.get("connected"):
                log.info("gateway is authenticated and connected")
                client.start_tickler()
                return client
        except Exception as exc:
            last_err = exc
        time.sleep(2.0)

    raise RuntimeError(
        f"gateway did not become ready within {ready_timeout_s}s; last error: {last_err}"
    )


def disconnect(client: IbkrClient) -> None:
    try:
        client.stop_tickler()
    except Exception as exc:
        log.warning("stop_tickler failed: %s", exc)


def select_account_id(client: IbkrClient) -> str:
    """Return the first selected account ID. Most retail users have one."""

    response = client.portfolio_accounts()
    accounts = response.data
    if not accounts:
        raise RuntimeError("no IBKR accounts visible to the gateway")
    return str(accounts[0]["id"])
