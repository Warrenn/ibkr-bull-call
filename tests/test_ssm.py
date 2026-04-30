"""Tests for bull_call.ssm."""

from __future__ import annotations

import json

import boto3
import pytest
from botocore.stub import Stubber

from bull_call.ssm import (
    MissingParameterError,
    fetch_settings_overrides,
    load_settings_via_ssm,
)


@pytest.fixture
def ssm_client() -> object:
    return boto3.client("ssm", region_name="af-south-1")


def _stub_get_parameters_by_path(prefix: str, parameters: dict[str, str]) -> dict:
    return {
        "Parameters": [
            {
                "Name": f"{prefix}/{name}",
                "Type": "String" if name == "settings" else "SecureString",
                "Value": value,
            }
            for name, value in parameters.items()
        ]
    }


def test_fetch_settings_overrides_parses_json(ssm_client: object) -> None:
    settings_value = json.dumps({
        "tradingMode": "paper",
        "symbols": "SPX",
        "maxLossUsd": 250,
        "popThreshold": 0.55,
        "riskFreeRate": 0.05,
        "entryTimeEt": "10:30",
        "stopEnabled": True,
        "stopLatestSec": 30,
        "logLevel": "INFO",
        "minProfitToLossRatio": 0.10,
    })
    stubber = Stubber(ssm_client)  # type: ignore[arg-type]
    stubber.add_response(
        "get_parameter",
        {"Parameter": {"Name": "/dev/ibkr-bull-call/settings", "Type": "String", "Value": settings_value}},
        {"Name": "/dev/ibkr-bull-call/settings", "WithDecryption": False},
    )

    with stubber:
        overrides = fetch_settings_overrides(ssm_client, prefix="/dev/ibkr-bull-call")

    # tradingMode is intentionally NOT mapped (it's an IBeam concern, not a
    # bot strategy setting); the rest are env-shaped strings.
    assert overrides == {
        "MAX_LOSS_USD": "250",
        "SYMBOLS": "SPX",
        "POP_THRESHOLD": "0.55",
        "RISK_FREE_RATE": "0.05",
        "ENTRY_TIME_ET": "10:30",
        "STOP_ENABLED": "true",
        "STOP_LATEST_SEC": "30",
        "LOG_LEVEL": "INFO",
        "MIN_PROFIT_TO_LOSS_RATIO": "0.1",
    }


def test_fetch_settings_missing_param_raises(ssm_client: object) -> None:
    stubber = Stubber(ssm_client)  # type: ignore[arg-type]
    stubber.add_client_error(
        "get_parameter",
        service_error_code="ParameterNotFound",
        http_status_code=400,
    )
    with stubber, pytest.raises(MissingParameterError, match="settings"):
        fetch_settings_overrides(ssm_client, prefix="/dev/ibkr-bull-call")


def test_fetch_settings_invalid_json_raises(ssm_client: object) -> None:
    stubber = Stubber(ssm_client)  # type: ignore[arg-type]
    stubber.add_response(
        "get_parameter",
        {"Parameter": {"Name": "/dev/ibkr-bull-call/settings", "Type": "String", "Value": "not-json"}},
        {"Name": "/dev/ibkr-bull-call/settings", "WithDecryption": False},
    )
    with stubber, pytest.raises(ValueError, match="JSON"):
        fetch_settings_overrides(ssm_client, prefix="/dev/ibkr-bull-call")


def test_fetch_partial_settings_only_returns_provided_keys(ssm_client: object) -> None:
    settings_value = json.dumps({"maxLossUsd": 500, "popThreshold": 0.6})
    stubber = Stubber(ssm_client)  # type: ignore[arg-type]
    stubber.add_response(
        "get_parameter",
        {"Parameter": {"Name": "/dev/ibkr-bull-call/settings", "Type": "String", "Value": settings_value}},
        {"Name": "/dev/ibkr-bull-call/settings", "WithDecryption": False},
    )
    with stubber:
        overrides = fetch_settings_overrides(ssm_client, prefix="/dev/ibkr-bull-call")
    assert overrides == {"MAX_LOSS_USD": "500", "POP_THRESHOLD": "0.6"}


def test_fetch_monthly_capital_gate_key_maps(ssm_client: object) -> None:
    """The monthly capital-gate setting can be controlled via SSM JSON."""

    settings_value = json.dumps({
        "maxLossUsd": 250,
        "monthlyStopOnNegativePnl": False,
    })
    stubber = Stubber(ssm_client)  # type: ignore[arg-type]
    stubber.add_response(
        "get_parameter",
        {"Parameter": {"Name": "/dev/ibkr-bull-call/settings", "Type": "String", "Value": settings_value}},
        {"Name": "/dev/ibkr-bull-call/settings", "WithDecryption": False},
    )
    with stubber:
        overrides = fetch_settings_overrides(ssm_client, prefix="/dev/ibkr-bull-call")
    assert overrides["MONTHLY_STOP_ON_NEGATIVE_PNL"] == "false"


def test_fetch_monitoring_outage_keys_map(ssm_client: object) -> None:
    """The R23a data-outage settings can be controlled via SSM JSON."""

    settings_value = json.dumps({
        "maxLossUsd": 250,
        "monitoringQuoteGraceSec": 20,
        "monitoringReconnectMaxAttempts": 5,
        "monitoringQuoteMaxBlindSec": 90,
    })
    stubber = Stubber(ssm_client)  # type: ignore[arg-type]
    stubber.add_response(
        "get_parameter",
        {"Parameter": {"Name": "/dev/ibkr-bull-call/settings", "Type": "String", "Value": settings_value}},
        {"Name": "/dev/ibkr-bull-call/settings", "WithDecryption": False},
    )
    with stubber:
        overrides = fetch_settings_overrides(ssm_client, prefix="/dev/ibkr-bull-call")
    assert overrides["MONITORING_QUOTE_GRACE_SEC"] == "20"
    assert overrides["MONITORING_RECONNECT_MAX_ATTEMPTS"] == "5"
    assert overrides["MONITORING_QUOTE_MAX_BLIND_SEC"] == "90"


def test_fetch_skip_half_days_key_maps(ssm_client: object) -> None:
    """The half-day-skip toggle can be controlled via SSM JSON."""

    settings_value = json.dumps({
        "maxLossUsd": 250,
        "skipHalfDays": False,
    })
    stubber = Stubber(ssm_client)  # type: ignore[arg-type]
    stubber.add_response(
        "get_parameter",
        {"Parameter": {"Name": "/dev/ibkr-bull-call/settings", "Type": "String", "Value": settings_value}},
        {"Name": "/dev/ibkr-bull-call/settings", "WithDecryption": False},
    )
    with stubber:
        overrides = fetch_settings_overrides(ssm_client, prefix="/dev/ibkr-bull-call")
    assert overrides["SKIP_HALF_DAYS"] == "false"


def test_fetch_heartbeat_interval_key_maps(ssm_client: object) -> None:
    """The heartbeat-interval setting can be controlled via SSM JSON."""

    settings_value = json.dumps({
        "maxLossUsd": 250,
        "heartbeatIntervalSec": 120,
    })
    stubber = Stubber(ssm_client)  # type: ignore[arg-type]
    stubber.add_response(
        "get_parameter",
        {"Parameter": {"Name": "/dev/ibkr-bull-call/settings", "Type": "String", "Value": settings_value}},
        {"Name": "/dev/ibkr-bull-call/settings", "WithDecryption": False},
    )
    with stubber:
        overrides = fetch_settings_overrides(ssm_client, prefix="/dev/ibkr-bull-call")
    assert overrides["HEARTBEAT_INTERVAL_SEC"] == "120"


def test_fetch_unknown_keys_ignored(ssm_client: object) -> None:
    settings_value = json.dumps({"maxLossUsd": 200, "futureKnob": "ignored"})
    stubber = Stubber(ssm_client)  # type: ignore[arg-type]
    stubber.add_response(
        "get_parameter",
        {"Parameter": {"Name": "/dev/ibkr-bull-call/settings", "Type": "String", "Value": settings_value}},
        {"Name": "/dev/ibkr-bull-call/settings", "WithDecryption": False},
    )
    with stubber:
        overrides = fetch_settings_overrides(ssm_client, prefix="/dev/ibkr-bull-call")
    assert overrides == {"MAX_LOSS_USD": "200"}


# ---------- error / shape edge cases on fetch_settings_overrides -----------


def test_fetch_settings_reraises_unrelated_get_parameter_errors(
    ssm_client: object,
) -> None:
    """Anything other than ParameterNotFound (network error, IAM denial,
    throttling) propagates verbatim — caller decides what to do."""

    stubber = Stubber(ssm_client)  # type: ignore[arg-type]
    stubber.add_client_error(
        "get_parameter",
        service_error_code="AccessDeniedException",
        http_status_code=403,
    )
    with stubber, pytest.raises(Exception, match="AccessDenied"):
        fetch_settings_overrides(ssm_client, prefix="/dev/ibkr-bull-call")


def test_fetch_settings_rejects_non_object_json(ssm_client: object) -> None:
    """A JSON list / scalar at the top level is unusable — the loader
    expects a JSON object so each key can map to a Settings field."""

    settings_value = json.dumps([1, 2, 3])
    stubber = Stubber(ssm_client)  # type: ignore[arg-type]
    stubber.add_response(
        "get_parameter",
        {"Parameter": {"Name": "/dev/ibkr-bull-call/settings", "Type": "String", "Value": settings_value}},
        {"Name": "/dev/ibkr-bull-call/settings", "WithDecryption": False},
    )
    with stubber, pytest.raises(ValueError, match="JSON object"):
        fetch_settings_overrides(ssm_client, prefix="/dev/ibkr-bull-call")


def test_fetch_settings_format_value_handles_string_passthrough(
    ssm_client: object,
) -> None:
    """A JSON string value passes through to the env override unchanged
    (covers the fall-through branch in _format_value when value is
    neither bool nor numeric)."""

    settings_value = json.dumps({
        "maxLossUsd": 200,
        "logLevel": "DEBUG",  # string, exercises the str() fall-through
    })
    stubber = Stubber(ssm_client)  # type: ignore[arg-type]
    stubber.add_response(
        "get_parameter",
        {"Parameter": {"Name": "/dev/ibkr-bull-call/settings", "Type": "String", "Value": settings_value}},
        {"Name": "/dev/ibkr-bull-call/settings", "WithDecryption": False},
    )
    with stubber:
        overrides = fetch_settings_overrides(ssm_client, prefix="/dev/ibkr-bull-call")
    assert overrides["LOG_LEVEL"] == "DEBUG"


# ---------- fetch_credentials (the unguarded credential surface) ------------


def test_fetch_credentials_returns_userid_and_password(
    ssm_client: object,
) -> None:
    """The happy path: both SecureStrings come back, returned as a
    _Credentials wrapper whose .clear() can later wipe them."""

    from bull_call.ssm import fetch_credentials

    stubber = Stubber(ssm_client)  # type: ignore[arg-type]
    stubber.add_response(
        "get_parameters",
        {
            "Parameters": [
                {"Name": "/dev/ibkr-bull-call/tws_userid", "Value": "ibkr_user"},
                {"Name": "/dev/ibkr-bull-call/tws_password", "Value": "ibkr_pass"},
            ],
            # Stubber rejects an empty list here — omit when no invalids.
        },
        {
            "Names": [
                "/dev/ibkr-bull-call/tws_userid",
                "/dev/ibkr-bull-call/tws_password",
            ],
            "WithDecryption": True,
        },
    )
    with stubber:
        creds = fetch_credentials(ssm_client, prefix="/dev/ibkr-bull-call")
    assert creds.userid == "ibkr_user"
    assert creds.password == "ibkr_pass"


def test_fetch_credentials_strips_trailing_slash() -> None:
    """``prefix`` may or may not have a trailing slash; the function
    strips it so we don't generate ``/dev/ibkr-bull-call//tws_userid``."""

    captured: dict[str, Any] = {}

    class FakeSsm:
        def get_parameters(self, *, Names: list[str], WithDecryption: bool) -> dict:
            captured["Names"] = Names
            return {
                "Parameters": [
                    {"Name": Names[0], "Value": "u"},
                    {"Name": Names[1], "Value": "p"},
                ],
                "InvalidParameters": [],
            }

    from bull_call.ssm import fetch_credentials

    fetch_credentials(FakeSsm(), prefix="/dev/ibkr-bull-call/")
    assert captured["Names"] == [
        "/dev/ibkr-bull-call/tws_userid",
        "/dev/ibkr-bull-call/tws_password",
    ]


def test_fetch_credentials_raises_when_get_parameters_fails() -> None:
    """Network / IAM / throttling — wrap as MissingParameterError so
    callers can distinguish "didn't reach SSM" from "param missing"."""

    from bull_call.ssm import MissingParameterError, fetch_credentials

    class FakeSsm:
        def get_parameters(self, **_: Any) -> dict:
            raise RuntimeError("network blip")

    with pytest.raises(MissingParameterError, match="failed to fetch"):
        fetch_credentials(FakeSsm(), prefix="/dev/ibkr-bull-call")


def test_fetch_credentials_raises_when_params_invalid() -> None:
    """If SSM reports any of the names as invalid, refuse — handing
    IBeam half a credential pair would just fail later in a confusing
    way."""

    from bull_call.ssm import MissingParameterError, fetch_credentials

    class FakeSsm:
        def get_parameters(self, **_: Any) -> dict:
            return {
                "Parameters": [
                    {"Name": "/dev/ibkr-bull-call/tws_userid", "Value": "u"},
                ],
                "InvalidParameters": ["/dev/ibkr-bull-call/tws_password"],
            }

    with pytest.raises(MissingParameterError, match="missing or undecryptable"):
        fetch_credentials(FakeSsm(), prefix="/dev/ibkr-bull-call")


def test_load_settings_via_ssm_end_to_end(ssm_client: object) -> None:
    settings_value = json.dumps({
        "maxLossUsd": 200,
        "popThreshold": 0.55,
        "entryTimeEt": "11:00",
    })
    stubber = Stubber(ssm_client)  # type: ignore[arg-type]
    stubber.add_response(
        "get_parameter",
        {"Parameter": {"Name": "/dev/ibkr-bull-call/settings", "Type": "String", "Value": settings_value}},
        {"Name": "/dev/ibkr-bull-call/settings", "WithDecryption": False},
    )

    with stubber:
        settings = load_settings_via_ssm(
            prefix="/dev/ibkr-bull-call", client=ssm_client,
        )

    assert settings.max_loss_usd == 200.0
    assert settings.pop_threshold == 0.55
    assert settings.entry_time_et.isoformat(timespec="minutes") == "11:00"
    # Static, not in SSM:
    assert settings.ib_host == "ibgateway"
    assert settings.ib_port == 4002
