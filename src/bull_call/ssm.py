"""SSM Parameter Store integration.

Two responsibilities:

1. Fetch the strategy ``settings`` JSON parameter and turn it into a Settings
   object (camelCase JSON keys → snake_case Settings fields).
2. Provide a Credentials helper that fetches IBKR username + password from SSM
   on demand (used only by the IBeam custom secrets provider; never by the bot
   itself).

boto3 is imported lazily so the test suite runs without AWS at all.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass
from typing import Any

from bull_call.config import Settings, load_settings

log = logging.getLogger(__name__)


_SETTINGS_KEY_MAP: dict[str, str] = {
    # tradingMode is intentionally NOT mapped — it's an IBeam/Gateway concern,
    # not a bot strategy setting; if present in the JSON it's silently ignored.
    "symbols": "SYMBOLS",
    "maxLossUsd": "MAX_LOSS_USD",
    "popThreshold": "POP_THRESHOLD",
    "riskFreeRate": "RISK_FREE_RATE",
    "entryTimeEt": "ENTRY_TIME_ET",
    "stopEnabled": "STOP_ENABLED",
    "stopLatestSec": "STOP_LATEST_SEC",
    "logLevel": "LOG_LEVEL",
    "ibHost": "IB_HOST",
    "ibPort": "IB_PORT",
    "ibClientId": "IB_CLIENT_ID",
    "stateTable": "STATE_TABLE",
    "minProfitToLossRatio": "MIN_PROFIT_TO_LOSS_RATIO",
    "entryTimeoutSec": "ENTRY_TIMEOUT_SEC",
    "entryDeadlineEt": "ENTRY_DEADLINE_ET",
    "legFillTimeoutSec": "LEG_FILL_TIMEOUT_SEC",
}


class MissingParameterError(RuntimeError):
    """A required SSM parameter was not found."""


def _format_value(value: Any) -> str:
    """Convert a JSON-decoded value into the env-var string the Settings loader expects."""

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        # Drop trailing .0 from int-valued floats for cleaner round-trips.
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    return str(value)


def fetch_settings_overrides(
    ssm_client: Any, *, prefix: str
) -> dict[str, str]:
    """Fetch ``<prefix>/settings`` and return a dict of env-var-shaped overrides.

    Unknown JSON keys are dropped (forward-compat). Returns env-shaped strings
    so the existing :func:`bull_call.config.load_settings` logic can consume
    them unchanged.
    """

    name = f"{prefix.rstrip('/')}/settings"
    try:
        resp = ssm_client.get_parameter(Name=name, WithDecryption=False)
    except Exception as exc:
        if "ParameterNotFound" in type(exc).__name__ or "ParameterNotFound" in str(exc):
            raise MissingParameterError(f"SSM parameter not found: {name}") from exc
        raise

    raw_value = resp["Parameter"]["Value"]
    try:
        decoded = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} is not valid JSON: {exc}") from exc

    if not isinstance(decoded, dict):
        raise ValueError(f"{name} must be a JSON object, got {type(decoded).__name__}")

    overrides: dict[str, str] = {}
    for k, v in decoded.items():
        env_key = _SETTINGS_KEY_MAP.get(k)
        if env_key is None:
            log.debug("ignoring unknown settings key: %s", k)
            continue
        overrides[env_key] = _format_value(v)
    return overrides


def load_settings_via_ssm(
    *,
    prefix: str,
    region: str = "us-east-1",
    client: Any | None = None,
) -> Settings:
    """Build a :class:`Settings` from the SSM ``<prefix>/settings`` parameter.

    Static fields not exposed via SSM (``IB_HOST``, ``IB_PORT``) keep their
    Settings defaults. ``client`` is injectable for tests; production passes
    a real ``boto3.client('ssm')``.
    """

    if client is None:
        import boto3

        client = boto3.client("ssm", region_name=region)

    overrides = fetch_settings_overrides(client, prefix=prefix)
    log.info("loaded %d settings keys from %s", len(overrides), prefix)
    return load_settings(env=overrides)


@dataclass
class _Credentials:
    """Mutable credential holder. Call ``clear()`` to drop references."""

    userid: str | None
    password: str | None

    def clear(self) -> None:
        # Replace with the empty string then None so any caller that copied the
        # reference can't keep using it after we've handed it off.
        self.userid = None
        self.password = None


def fetch_credentials(
    ssm_client: Any, *, prefix: str
) -> _Credentials:
    """Fetch ``tws_userid`` + ``tws_password`` SecureStrings.

    The returned object is the only place credentials live; callers should
    call ``.clear()`` as soon as they're done using them.
    """

    base = prefix.rstrip("/")
    try:
        resp = ssm_client.get_parameters(
            Names=[f"{base}/tws_userid", f"{base}/tws_password"],
            WithDecryption=True,
        )
    except Exception as exc:
        raise MissingParameterError(
            f"failed to fetch credentials under {base}: {exc}"
        ) from exc

    found = {p["Name"]: p["Value"] for p in resp.get("Parameters", [])}
    invalid = resp.get("InvalidParameters", [])
    if invalid:
        raise MissingParameterError(f"missing or undecryptable: {invalid}")

    return _Credentials(
        userid=found[f"{base}/tws_userid"],
        password=found[f"{base}/tws_password"],
    )
