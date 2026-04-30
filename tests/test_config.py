"""Tests for bull_call.config."""

from __future__ import annotations

import datetime as dt

import pytest

from bull_call.config import Settings, load_settings


def test_load_with_defaults_and_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_LOSS_USD", "200")

    s = load_settings()

    assert s.ib_host == "ibgateway"
    assert s.ib_port == 4002
    assert s.ib_client_id == 7
    assert s.symbols == ("SPX",)
    assert s.max_loss_usd == 200.0
    assert s.pop_threshold == 0.70
    assert s.risk_free_rate == 0.05
    assert s.entry_time_et == dt.time(10, 30)
    assert s.stop_enabled is True
    assert s.stop_latest_sec == 30
    assert s.log_level == "INFO"
    assert s.min_profit_to_loss_ratio is None  # default = no constraint
    assert s.entry_timeout_sec == 300           # default = 5 min total budget
    assert s.entry_deadline_et == dt.time(13, 0)  # default = stop at 1 pm ET
    assert s.leg_fill_timeout_sec == 30         # default = 30s leg-out grace
    assert s.monthly_stop_on_negative_pnl is True  # default = capital gate ON
    # R23a data-outage fail-safe defaults (per strategy-review.md §7):
    assert s.monitoring_quote_grace_sec == 15
    assert s.monitoring_reconnect_max_attempts == 3
    assert s.monitoring_quote_max_blind_sec == 60


def test_min_profit_to_loss_ratio_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_LOSS_USD", "500")
    monkeypatch.setenv("MIN_PROFIT_TO_LOSS_RATIO", "0.10")
    s = load_settings()
    assert s.min_profit_to_loss_ratio == 0.10


def test_min_profit_to_loss_ratio_empty_means_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_LOSS_USD", "500")
    monkeypatch.setenv("MIN_PROFIT_TO_LOSS_RATIO", "")
    s = load_settings()
    assert s.min_profit_to_loss_ratio is None


def test_entry_timeout_sec_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_LOSS_USD", "500")
    monkeypatch.setenv("ENTRY_TIMEOUT_SEC", "120")
    s = load_settings()
    assert s.entry_timeout_sec == 120


def test_missing_max_loss_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="MAX_LOSS_USD"):
        load_settings()


def test_overrides_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_LOSS_USD", "500")
    monkeypatch.setenv("IB_HOST", "127.0.0.1")
    monkeypatch.setenv("IB_PORT", "7497")
    monkeypatch.setenv("IB_CLIENT_ID", "42")
    monkeypatch.setenv("SYMBOLS", "SPX,XSP")
    monkeypatch.setenv("POP_THRESHOLD", "0.85")
    monkeypatch.setenv("RISK_FREE_RATE", "0.04")
    monkeypatch.setenv("ENTRY_TIME_ET", "11:15")
    monkeypatch.setenv("STOP_ENABLED", "false")
    monkeypatch.setenv("STOP_LATEST_SEC", "60")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    s = load_settings()

    assert s.ib_host == "127.0.0.1"
    assert s.ib_port == 7497
    assert s.ib_client_id == 42
    assert s.symbols == ("SPX", "XSP")
    assert s.max_loss_usd == 500.0
    assert s.pop_threshold == 0.85
    assert s.risk_free_rate == 0.04
    assert s.entry_time_et == dt.time(11, 15)
    assert s.stop_enabled is False
    assert s.stop_latest_sec == 60
    assert s.log_level == "DEBUG"


@pytest.mark.parametrize("value", ["true", "True", "1", "yes", "YES", "on"])
def test_stop_enabled_truthy_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("MAX_LOSS_USD", "200")
    monkeypatch.setenv("STOP_ENABLED", value)
    assert load_settings().stop_enabled is True


@pytest.mark.parametrize("value", ["false", "False", "0", "no", "off", ""])
def test_stop_enabled_falsy_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("MAX_LOSS_USD", "200")
    monkeypatch.setenv("STOP_ENABLED", value)
    assert load_settings().stop_enabled is False


def test_symbols_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_LOSS_USD", "200")
    monkeypatch.setenv("SYMBOLS", " SPX , XSP ,QQQ ")
    assert load_settings().symbols == ("SPX", "XSP", "QQQ")


def test_invalid_entry_time_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_LOSS_USD", "200")
    monkeypatch.setenv("ENTRY_TIME_ET", "25:00")
    with pytest.raises(ValueError, match="ENTRY_TIME_ET"):
        load_settings()


@pytest.mark.parametrize("value", ["false", "False", "0", "no", "off", ""])
def test_monthly_stop_on_negative_pnl_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    monkeypatch.setenv("MAX_LOSS_USD", "200")
    monkeypatch.setenv("MONTHLY_STOP_ON_NEGATIVE_PNL", value)
    assert load_settings().monthly_stop_on_negative_pnl is False


@pytest.mark.parametrize("value", ["true", "True", "1", "yes", "on"])
def test_monthly_stop_on_negative_pnl_truthy(
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    monkeypatch.setenv("MAX_LOSS_USD", "200")
    monkeypatch.setenv("MONTHLY_STOP_ON_NEGATIVE_PNL", value)
    assert load_settings().monthly_stop_on_negative_pnl is True


def test_monitoring_quote_grace_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_LOSS_USD", "200")
    monkeypatch.setenv("MONITORING_QUOTE_GRACE_SEC", "30")
    monkeypatch.setenv("MONITORING_RECONNECT_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("MONITORING_QUOTE_MAX_BLIND_SEC", "120")
    s = load_settings()
    assert s.monitoring_quote_grace_sec == 30
    assert s.monitoring_reconnect_max_attempts == 5
    assert s.monitoring_quote_max_blind_sec == 120


def test_settings_is_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_LOSS_USD", "200")
    s = load_settings()
    with pytest.raises(Exception):
        s.ib_host = "other"  # type: ignore[misc]
