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


def _stub_settings(client: object, json_body: str) -> Stubber:
    stubber = Stubber(client)  # type: ignore[arg-type]
    stubber.add_response(
        "get_parameter",
        {"Parameter": {"Name": "/dev/ibkr-bull-call/settings", "Type": "String", "Value": json_body}},
        {"Name": "/dev/ibkr-bull-call/settings", "WithDecryption": False},
    )
    return stubber


def test_load_settings_via_ssm_uses_state_table_from_env_when_absent_from_ssm(
    ssm_client: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STATE_TABLE comes from infra (CFN-written bot.env) and is NOT a strategy
    parameter — SSM JSON never sets it. The deployed loader must fall through
    to the process env so the right DDB table is used in production."""

    monkeypatch.setenv("STATE_TABLE", "bull-call-live-state")
    monkeypatch.setenv("MAX_LOSS_USD", "999")  # would-be override; SSM should win
    settings_value = json.dumps({
        "maxLossUsd": 250,
        "popThreshold": 0.55,
    })

    with _stub_settings(ssm_client, settings_value):
        settings = load_settings_via_ssm(
            prefix="/dev/ibkr-bull-call", client=ssm_client,
        )

    assert settings.state_table == "bull-call-live-state"


def test_load_settings_via_ssm_ssm_wins_on_collision_with_env(
    ssm_client: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For keys present in BOTH env and SSM, SSM wins in deployed mode. SSM
    is the canonical place to express deployed strategy parameters; env only
    fills gaps for infra-set keys."""

    monkeypatch.setenv("MAX_LOSS_USD", "100")
    monkeypatch.setenv("POP_THRESHOLD", "0.30")
    settings_value = json.dumps({
        "maxLossUsd": 250,
        "popThreshold": 0.55,
    })

    with _stub_settings(ssm_client, settings_value):
        settings = load_settings_via_ssm(
            prefix="/dev/ibkr-bull-call", client=ssm_client,
        )

    assert settings.max_loss_usd == 250.0
    assert settings.pop_threshold == 0.55


def test_load_settings_via_ssm_env_only_keys_pass_through(
    ssm_client: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keys in env but not in SSM (e.g. STATE_TABLE, AWS_REGION, IB_HOST
    overrides) flow through unchanged."""

    monkeypatch.setenv("STATE_TABLE", "bull-call-live-state")
    monkeypatch.setenv("IB_HOST", "127.0.0.1")
    monkeypatch.setenv("IB_PORT", "5000")
    settings_value = json.dumps({"maxLossUsd": 250})

    with _stub_settings(ssm_client, settings_value):
        settings = load_settings_via_ssm(
            prefix="/dev/ibkr-bull-call", client=ssm_client,
        )

    assert settings.state_table == "bull-call-live-state"
    assert settings.ib_host == "127.0.0.1"
    assert settings.ib_port == 5000


def test_load_settings_via_ssm_falls_back_to_dataclass_defaults_when_unset(
    ssm_client: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If neither env nor SSM sets STATE_TABLE, the dataclass default
    (``bull-call-dev-state``) wins — the historical behaviour is preserved
    for local-test runs that hit no env or SSM at all."""

    monkeypatch.delenv("STATE_TABLE", raising=False)
    settings_value = json.dumps({"maxLossUsd": 250})

    with _stub_settings(ssm_client, settings_value):
        settings = load_settings_via_ssm(
            prefix="/dev/ibkr-bull-call", client=ssm_client,
        )

    assert settings.state_table == "bull-call-dev-state"
