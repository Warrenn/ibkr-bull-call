"""Environment-driven configuration for the bull-call bot."""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from typing import Mapping

_TRUTHY = frozenset({"true", "1", "yes", "on"})


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in _TRUTHY


def _parse_time(value: str, var: str) -> dt.time:
    try:
        return dt.time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{var} must be HH:MM (24-hour ET); got {value!r}") from exc


def _parse_symbols(value: str) -> tuple[str, ...]:
    return tuple(s.strip().upper() for s in value.split(",") if s.strip())


# ---------- typed-parse helpers --------------------------------------------
#
# Each helper:
#   1. Reads ``var`` from the env mapping (or uses ``default``).
#   2. Parses the string into the target type, raising a ValueError that
#      names the offending env var (so an operator misconfiguring SSM gets
#      a useful message in CloudWatch instead of "could not convert ...").
#   3. Validates the parsed value against the bound, with a tailored error.
#
# Helpers are private; they stay in this module rather than a shared utils
# package because their error messages talk about env-var conventions
# specific to this bot.


def _parse_int(src: Mapping[str, str], var: str, default: int) -> int:
    raw = src.get(var, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{var} must be an integer; got {raw!r}") from exc


def _parse_float(src: Mapping[str, str], var: str, default: float) -> float:
    raw = src.get(var, str(default))
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{var} must be a number; got {raw!r}") from exc


def _parse_positive_int(src: Mapping[str, str], var: str, default: int) -> int:
    value = _parse_int(src, var, default)
    if value <= 0:
        raise ValueError(f"{var} must be > 0; got {value}.")
    return value


def _parse_non_negative_int(src: Mapping[str, str], var: str, default: int) -> int:
    value = _parse_int(src, var, default)
    if value < 0:
        raise ValueError(f"{var} must be >= 0; got {value}.")
    return value


def _parse_bounded_float(
    src: Mapping[str, str], var: str, default: float,
    *, min_: float, max_: float, reason: str = "",
) -> float:
    value = _parse_float(src, var, default)
    if not (min_ <= value <= max_):
        suffix = f" {reason}" if reason else ""
        raise ValueError(
            f"{var} must be in [{min_}, {max_}]; got {value}.{suffix}"
        )
    return value


def _parse_positive_float(
    src: Mapping[str, str], var: str, default: float, *, reason: str = "",
) -> float:
    value = _parse_float(src, var, default)
    if value <= 0:
        suffix = f" {reason}" if reason else ""
        raise ValueError(f"{var} must be > 0; got {value}.{suffix}")
    return value


def _parse_optional_non_negative_float(
    src: Mapping[str, str], var: str,
) -> float | None:
    """For variables that treat empty/missing as 'no constraint' but reject
    negative numbers when set."""

    raw = src.get(var, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{var} must be a number; got {raw!r}") from exc
    if value < 0:
        raise ValueError(
            f"{var} must be >= 0 (or unset); got {value}. "
            "Use 0 / empty for 'no constraint'."
        )
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    ib_host: str
    ib_port: int
    ib_client_id: int
    symbols: tuple[str, ...]
    max_loss_usd: float
    pop_threshold: float
    risk_free_rate: float
    entry_time_et: dt.time
    stop_enabled: bool
    stop_latest_sec: int
    state_table: str
    log_level: str
    # Minimum (max_profit / max_loss) ratio. e.g. 0.10 means "for every $1000
    # of possible loss I require at least $100 of possible profit".
    min_profit_to_loss_ratio: float | None = None
    # Total budget (seconds) the entry limit order is allowed to work before
    # we cancel and walk away. Split 50/50 between the initial-price phase
    # and the one-tick reprice phase.
    entry_timeout_sec: int = 300
    # ET clock time after which we stop trying to enter today (no fill, no
    # viable spread, repeated cancels — give up). Default 13:00 ET leaves
    # roughly 3 hours for a filled spread to work toward 4 pm settlement.
    entry_deadline_et: dt.time = dt.time(13, 0)
    # After a combo fill, max seconds we'll wait for both legs to show up
    # in positions before treating it as a leg-out and flattening the orphan
    # leg at MKT.
    leg_fill_timeout_sec: int = 30
    # Monthly net-negative capital gate: if month-to-date realized PnL is
    # negative, skip new entries for the rest of the month. Existing positions
    # continue to be managed. Resets at the first session of the next month.
    monthly_stop_on_negative_pnl: bool = True
    # R23a — open-position data-outage fail-safe.
    # If the spot tick stream goes silent on an open position for this long,
    # start reconnect attempts (and emit a quote_outage event).
    monitoring_quote_grace_sec: int = 15
    # Up to this many WS reconnects before escalating to emergency flatten.
    monitoring_reconnect_max_attempts: int = 3
    # Total blind window since last fresh tick before emergency MKT flatten.
    # Must be >= monitoring_quote_grace_sec.
    monitoring_quote_max_blind_sec: int = 60


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build a Settings from process env (or an explicit mapping for tests).

    Each numeric setting is parsed via a typed helper that names the offending
    env var on bad input — so a misconfigured SSM key surfaces as
    ``ValueError: POP_THRESHOLD must be a number; got 'abc'`` rather than the
    bare ``could not convert string to float: 'abc'``.
    """

    src: Mapping[str, str] = env if env is not None else os.environ

    if not src.get("MAX_LOSS_USD", "").strip():
        raise ValueError("MAX_LOSS_USD is required")

    max_loss = _parse_positive_float(
        src, "MAX_LOSS_USD", default=0.0,
        reason="A non-positive cap makes no spread selectable.",
    )

    min_profit_to_loss_ratio = _parse_optional_non_negative_float(
        src, "MIN_PROFIT_TO_LOSS_RATIO",
    )

    pop_threshold = _parse_bounded_float(
        src, "POP_THRESHOLD", default=0.70,
        min_=0.0, max_=1.0, reason="POP is a probability.",
    )

    entry_timeout_sec = _parse_positive_int(src, "ENTRY_TIMEOUT_SEC", default=300)
    leg_fill_timeout_sec = _parse_positive_int(src, "LEG_FILL_TIMEOUT_SEC", default=30)
    stop_latest_sec = _parse_non_negative_int(src, "STOP_LATEST_SEC", default=30)

    monitoring_reconnect_max_attempts = _parse_non_negative_int(
        src, "MONITORING_RECONNECT_MAX_ATTEMPTS", default=3,
    )
    monitoring_quote_grace_sec = _parse_non_negative_int(
        src, "MONITORING_QUOTE_GRACE_SEC", default=15,
    )
    monitoring_quote_max_blind_sec = _parse_non_negative_int(
        src, "MONITORING_QUOTE_MAX_BLIND_SEC", default=60,
    )
    if monitoring_quote_max_blind_sec < monitoring_quote_grace_sec:
        raise ValueError(
            "MONITORING_QUOTE_MAX_BLIND_SEC "
            f"({monitoring_quote_max_blind_sec}) must be >= "
            f"MONITORING_QUOTE_GRACE_SEC ({monitoring_quote_grace_sec}); "
            "otherwise the bot would emergency-flatten before any "
            "reconnect attempt fires (R23a invariant)."
        )

    entry_time_et = _parse_time(src.get("ENTRY_TIME_ET", "10:30"), "ENTRY_TIME_ET")
    entry_deadline_et = _parse_time(
        src.get("ENTRY_DEADLINE_ET", "13:00"), "ENTRY_DEADLINE_ET",
    )
    if entry_deadline_et <= entry_time_et:
        raise ValueError(
            f"ENTRY_DEADLINE_ET ({entry_deadline_et}) must be strictly after "
            f"ENTRY_TIME_ET ({entry_time_et}); otherwise the deadline window "
            "is empty and the bot would never submit any entries."
        )

    return Settings(
        ib_host=src.get("IB_HOST", "ibgateway"),
        ib_port=_parse_int(src, "IB_PORT", default=4002),
        ib_client_id=_parse_int(src, "IB_CLIENT_ID", default=7),
        symbols=_parse_symbols(src.get("SYMBOLS", "SPX")),
        max_loss_usd=max_loss,
        pop_threshold=pop_threshold,
        risk_free_rate=_parse_float(src, "RISK_FREE_RATE", default=0.05),
        entry_time_et=entry_time_et,
        stop_enabled=_parse_bool(src.get("STOP_ENABLED", "true")),
        stop_latest_sec=stop_latest_sec,
        state_table=src.get("STATE_TABLE", "bull-call-dev-state"),
        log_level=src.get("LOG_LEVEL", "INFO").upper(),
        min_profit_to_loss_ratio=min_profit_to_loss_ratio,
        entry_timeout_sec=entry_timeout_sec,
        entry_deadline_et=entry_deadline_et,
        leg_fill_timeout_sec=leg_fill_timeout_sec,
        monthly_stop_on_negative_pnl=_parse_bool(
            src.get("MONTHLY_STOP_ON_NEGATIVE_PNL", "true"),
        ),
        monitoring_quote_grace_sec=monitoring_quote_grace_sec,
        monitoring_reconnect_max_attempts=monitoring_reconnect_max_attempts,
        monitoring_quote_max_blind_sec=monitoring_quote_max_blind_sec,
    )
