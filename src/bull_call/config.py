"""Environment-driven configuration for the bull-call bot."""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field

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
    """Build a Settings from process env (or an explicit mapping for tests)."""

    src = env if env is not None else os.environ

    raw_max_loss = src.get("MAX_LOSS_USD", "").strip()
    if not raw_max_loss:
        raise ValueError("MAX_LOSS_USD is required")

    try:
        max_loss = float(raw_max_loss)
    except ValueError as exc:
        raise ValueError(f"MAX_LOSS_USD must be a number; got {raw_max_loss!r}") from exc
    if max_loss <= 0:
        raise ValueError(
            f"MAX_LOSS_USD must be > 0; got {max_loss}. "
            "A non-positive cap makes no spread selectable."
        )

    raw_min_ratio = src.get("MIN_PROFIT_TO_LOSS_RATIO", "").strip()
    min_profit_to_loss_ratio: float | None = (
        float(raw_min_ratio) if raw_min_ratio else None
    )
    if min_profit_to_loss_ratio is not None and min_profit_to_loss_ratio < 0:
        raise ValueError(
            f"MIN_PROFIT_TO_LOSS_RATIO must be >= 0 (or unset); got "
            f"{min_profit_to_loss_ratio}. Use 0 / empty for 'no constraint'."
        )

    pop_threshold = float(src.get("POP_THRESHOLD", "0.70"))
    if not (0.0 <= pop_threshold <= 1.0):
        raise ValueError(
            f"POP_THRESHOLD must be in [0, 1]; got {pop_threshold}. "
            "POP is a probability."
        )

    entry_timeout_sec = int(src.get("ENTRY_TIMEOUT_SEC", "300"))
    if entry_timeout_sec <= 0:
        raise ValueError(
            f"ENTRY_TIMEOUT_SEC must be > 0; got {entry_timeout_sec}."
        )

    leg_fill_timeout_sec = int(src.get("LEG_FILL_TIMEOUT_SEC", "30"))
    if leg_fill_timeout_sec <= 0:
        raise ValueError(
            f"LEG_FILL_TIMEOUT_SEC must be > 0; got {leg_fill_timeout_sec}."
        )

    stop_latest_sec = int(src.get("STOP_LATEST_SEC", "30"))
    if stop_latest_sec < 0:
        raise ValueError(
            f"STOP_LATEST_SEC must be >= 0; got {stop_latest_sec}."
        )

    monitoring_reconnect_max_attempts = int(
        src.get("MONITORING_RECONNECT_MAX_ATTEMPTS", "3"),
    )
    if monitoring_reconnect_max_attempts < 0:
        raise ValueError(
            "MONITORING_RECONNECT_MAX_ATTEMPTS must be >= 0; got "
            f"{monitoring_reconnect_max_attempts}."
        )

    monitoring_quote_grace_sec = int(
        src.get("MONITORING_QUOTE_GRACE_SEC", "15"),
    )
    if monitoring_quote_grace_sec < 0:
        raise ValueError(
            f"MONITORING_QUOTE_GRACE_SEC must be >= 0; got "
            f"{monitoring_quote_grace_sec}."
        )

    monitoring_quote_max_blind_sec = int(
        src.get("MONITORING_QUOTE_MAX_BLIND_SEC", "60"),
    )
    if monitoring_quote_max_blind_sec < 0:
        raise ValueError(
            f"MONITORING_QUOTE_MAX_BLIND_SEC must be >= 0; got "
            f"{monitoring_quote_max_blind_sec}."
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
        ib_port=int(src.get("IB_PORT", "4002")),
        ib_client_id=int(src.get("IB_CLIENT_ID", "7")),
        symbols=_parse_symbols(src.get("SYMBOLS", "SPX")),
        max_loss_usd=max_loss,
        pop_threshold=pop_threshold,
        risk_free_rate=float(src.get("RISK_FREE_RATE", "0.05")),
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
