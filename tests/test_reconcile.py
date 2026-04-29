"""Tests for bull_call.cpapi.reconcile.detect_existing_spreads."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from bull_call.cpapi.reconcile import detect_existing_spreads


class FakeResp:
    def __init__(self, data: Any) -> None:
        self.data = data


class FakeClient:
    """Minimal IbkrClient stand-in: returns a fixed positions list."""

    def __init__(self, positions: list[dict]) -> None:
        self._positions = positions

    def positions(self, *, account_id: str, page_id: int = 0) -> FakeResp:
        return FakeResp(self._positions)


TODAY = dt.date(2026, 4, 29)
TODAY_STR = "20260429"
OTHER_DAY = "20260430"


def _opt(strike: float, qty: float, conid: int, *, expiry: str = TODAY_STR,
         avg_cost: float = 0.0, ticker: str = "SPX",
         trading_class: str = "SPXW") -> dict:
    return {
        "ticker": ticker,
        "strike": strike,
        "position": qty,
        "conid": conid,
        "putOrCall": "C",
        "assetClass": "OPT",
        "lastTradingDay": expiry,
        "avgCost": avg_cost,
        "tradingClass": trading_class,
    }


def test_finds_a_clean_bull_call_pair() -> None:
    client = FakeClient([
        _opt(strike=4995.0, qty=1, conid=1, avg_cost=600.0),    # paid $6.00
        _opt(strike=5005.0, qty=-1, conid=2, avg_cost=-50.0),   # received $0.50
    ])
    out = detect_existing_spreads(client, account_id="A1", today_et=TODAY)
    assert len(out) == 1
    s = out[0]
    assert s.symbol == "SPX"
    assert s.long_leg.strike == 4995.0
    assert s.long_leg.conid == 1
    assert s.short_leg.strike == 5005.0
    assert s.short_leg.conid == 2
    # entry_debit = abs(600) - abs(-50) = 550 (in dollars)
    assert s.entry_debit == pytest.approx(550.0)


def test_returns_empty_when_no_positions() -> None:
    client = FakeClient([])
    assert detect_existing_spreads(client, account_id="A1", today_et=TODAY) == []


def test_filters_out_other_expiries() -> None:
    client = FakeClient([
        _opt(strike=4995.0, qty=1, conid=1, expiry=OTHER_DAY),
        _opt(strike=5005.0, qty=-1, conid=2, expiry=OTHER_DAY),
    ])
    assert detect_existing_spreads(client, account_id="A1", today_et=TODAY) == []


def test_filters_out_puts() -> None:
    pos = [
        _opt(strike=4995.0, qty=1, conid=1),
        _opt(strike=5005.0, qty=-1, conid=2),
    ]
    pos[0]["putOrCall"] = "P"
    client = FakeClient(pos)
    assert detect_existing_spreads(client, account_id="A1", today_et=TODAY) == []


def test_skips_inverted_strikes() -> None:
    """If the long strike is HIGHER than the short, it's not a bull call."""

    client = FakeClient([
        _opt(strike=5010.0, qty=1, conid=1),
        _opt(strike=5000.0, qty=-1, conid=2),
    ])
    assert detect_existing_spreads(client, account_id="A1", today_et=TODAY) == []


def test_skips_unbalanced_legs() -> None:
    """Two longs and one short isn't a clean bull call shape."""

    client = FakeClient([
        _opt(strike=4995.0, qty=1, conid=1),
        _opt(strike=5000.0, qty=1, conid=2),
        _opt(strike=5005.0, qty=-1, conid=3),
    ])
    assert detect_existing_spreads(client, account_id="A1", today_et=TODAY) == []


def test_groups_by_underlying_ticker() -> None:
    """A position on a different underlying doesn't pollute the SPX shape."""

    client = FakeClient([
        _opt(strike=4995.0, qty=1, conid=1, ticker="SPX", avg_cost=600.0),
        _opt(strike=5005.0, qty=-1, conid=2, ticker="SPX", avg_cost=-50.0),
        # Lone QQQ leg with positive cost, but tradingClass!=SPXW so excluded.
        _opt(strike=400.0, qty=1, conid=3, ticker="QQQ", avg_cost=200.0,
             trading_class="QQQ"),
    ])
    out = detect_existing_spreads(client, account_id="A1", today_et=TODAY)
    assert len(out) == 1
    assert out[0].symbol == "SPX"


def test_rejects_when_long_avg_cost_non_positive() -> None:
    """§3.6: long position's avgCost must be > 0 (premium paid). If the
    feed returns zero or negative for the long, refuse to adopt — the
    debit arithmetic would silently mis-compute under unsigned costs."""

    client = FakeClient([
        _opt(strike=4995.0, qty=1, conid=1, avg_cost=0.0),       # invalid
        _opt(strike=5005.0, qty=-1, conid=2, avg_cost=-50.0),
    ])
    assert detect_existing_spreads(client, account_id="A1", today_et=TODAY) == []

    client2 = FakeClient([
        _opt(strike=4995.0, qty=1, conid=1, avg_cost=-100.0),    # negative
        _opt(strike=5005.0, qty=-1, conid=2, avg_cost=-50.0),
    ])
    assert detect_existing_spreads(client2, account_id="A1", today_et=TODAY) == []


def test_rejects_when_short_avg_cost_non_negative() -> None:
    """§3.6: short position's avgCost must be < 0 (premium received).
    If the feed returns zero or positive for the short, refuse to adopt."""

    client = FakeClient([
        _opt(strike=4995.0, qty=1, conid=1, avg_cost=600.0),
        _opt(strike=5005.0, qty=-1, conid=2, avg_cost=0.0),      # invalid
    ])
    assert detect_existing_spreads(client, account_id="A1", today_et=TODAY) == []

    client2 = FakeClient([
        _opt(strike=4995.0, qty=1, conid=1, avg_cost=600.0),
        _opt(strike=5005.0, qty=-1, conid=2, avg_cost=50.0),     # positive
    ])
    assert detect_existing_spreads(client2, account_id="A1", today_et=TODAY) == []


def test_skips_non_spxw_trading_class() -> None:
    """SPX monthly (AM-settled) and other non-SPXW classes must be refused.

    The bot's monitor + settlement logic assumes SPXW PM-settlement at 4 pm ET.
    Adopting an SPX monthly position would silently mis-monitor it.
    """

    client = FakeClient([
        _opt(strike=4995.0, qty=1, conid=1, trading_class="SPX"),
        _opt(strike=5005.0, qty=-1, conid=2, trading_class="SPX"),
    ])
    assert detect_existing_spreads(client, account_id="A1", today_et=TODAY) == []


def test_skips_when_trading_class_missing_or_unknown() -> None:
    """If tradingClass isn't present (e.g. mocked feed), refuse to adopt."""

    client = FakeClient([
        _opt(strike=4995.0, qty=1, conid=1, trading_class=""),
        _opt(strike=5005.0, qty=-1, conid=2, trading_class=""),
    ])
    assert detect_existing_spreads(client, account_id="A1", today_et=TODAY) == []


def test_mixed_spxw_and_spx_only_adopts_spxw() -> None:
    """If the account has both SPXW and SPX positions, only SPXW is adopted."""

    client = FakeClient([
        # SPX monthly (rejected)
        _opt(strike=4995.0, qty=1, conid=1, trading_class="SPX"),
        _opt(strike=5005.0, qty=-1, conid=2, trading_class="SPX"),
        # SPXW (adopted)
        _opt(strike=4990.0, qty=1, conid=11, avg_cost=600.0, trading_class="SPXW"),
        _opt(strike=5000.0, qty=-1, conid=12, avg_cost=-50.0, trading_class="SPXW"),
    ])
    out = detect_existing_spreads(client, account_id="A1", today_et=TODAY)
    assert len(out) == 1
    assert out[0].long_leg.conid == 11
    assert out[0].short_leg.conid == 12


def test_handles_positions_call_failure_gracefully() -> None:
    class BrokenClient:
        def positions(self, *, account_id: str, page_id: int = 0) -> FakeResp:
            raise RuntimeError("network down")

    out = detect_existing_spreads(BrokenClient(), account_id="A1", today_et=TODAY)
    assert out == []
