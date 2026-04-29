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
    state_dir: str
    log_level: str
    min_loss_profit_ratio: float | None = None


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

    raw_min_ratio = src.get("MIN_LOSS_PROFIT_RATIO", "").strip()
    min_loss_profit_ratio: float | None = float(raw_min_ratio) if raw_min_ratio else None

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
        state_dir=src.get("STATE_DIR", "./state"),
        log_level=src.get("LOG_LEVEL", "INFO").upper(),
        min_loss_profit_ratio=min_loss_profit_ratio,
    )
