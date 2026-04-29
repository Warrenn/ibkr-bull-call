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
