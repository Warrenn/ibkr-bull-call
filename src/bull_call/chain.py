"""Option-chain dataclasses (library-neutral)."""

from __future__ import annotations

from dataclasses import dataclass

from bull_call.strikes import OptionQuote


@dataclass(frozen=True, slots=True)
class OptionContract:
    """Minimal identifier for one listed option leg, library-agnostic."""

    strike: float
    conid: int
    right: str  # "C" call / "P" put
    expiry: str  # YYYYMMDD


@dataclass(frozen=True, slots=True)
class ChainSnapshot:
    """Point-in-time snapshot of the 0DTE call chain plus spot + ATM IV."""

    symbol: str
    expiry: str  # YYYYMMDD
    spot: float
    atm_iv: float
    quotes: tuple[OptionQuote, ...]
    contracts: dict[float, OptionContract]
