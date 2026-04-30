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

    raw_min_ratio = src.get("MIN_PROFIT_TO_LOSS_RATIO", "").strip()
    min_profit_to_loss_ratio: float | None = (
        float(raw_min_ratio) if raw_min_ratio else None
    )

    return Settings(
        ib_host=src.get("IB_HOST", "ibgateway"),
        ib_port=int(src.get("IB_PORT", "4002")),
        ib_client_id=int(src.get("IB_CLIENT_ID", "7")),
        symbols=_parse_symbols(src.get("SYMBOLS", "SPX")),
        max_loss_usd=max_loss,
        pop_threshold=float(src.get("POP_THRESHOLD", "0.70")),
        risk_free_rate=float(src.get("RISK_FREE_RATE", "0.05")),
        entry_time_et=_parse_time(src.get("ENTRY_TIME_ET", "10:30"), "ENTRY_TIME_ET"),
        stop_enabled=_parse_bool(src.get("STOP_ENABLED", "true")),
        stop_latest_sec=int(src.get("STOP_LATEST_SEC", "30")),
        state_table=src.get("STATE_TABLE", "bull-call-dev-state"),
        log_level=src.get("LOG_LEVEL", "INFO").upper(),
        min_profit_to_loss_ratio=min_profit_to_loss_ratio,
        entry_timeout_sec=int(src.get("ENTRY_TIMEOUT_SEC", "300")),
        entry_deadline_et=_parse_time(
            src.get("ENTRY_DEADLINE_ET", "13:00"), "ENTRY_DEADLINE_ET",
        ),
        leg_fill_timeout_sec=int(src.get("LEG_FILL_TIMEOUT_SEC", "30")),
        monthly_stop_on_negative_pnl=_parse_bool(
            src.get("MONTHLY_STOP_ON_NEGATIVE_PNL", "true"),
        ),
        monitoring_quote_grace_sec=int(
            src.get("MONITORING_QUOTE_GRACE_SEC", "15"),
        ),
        monitoring_reconnect_max_attempts=int(
            src.get("MONITORING_RECONNECT_MAX_ATTEMPTS", "3"),
        ),
        monitoring_quote_max_blind_sec=int(
            src.get("MONITORING_QUOTE_MAX_BLIND_SEC", "60"),
        ),
    )
