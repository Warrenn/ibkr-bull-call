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
         avg_cost: float = 0.0, ticker: str = "SPX") -> dict:
    return {
        "ticker": ticker,
        "strike": strike,
        "position": qty,
        "conid": conid,
        "putOrCall": "C",
        "assetClass": "OPT",
        "lastTradingDay": expiry,
        "avgCost": avg_cost,
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
        _opt(strike=4995.0, qty=1, conid=1, ticker="SPX"),
        _opt(strike=5005.0, qty=-1, conid=2, ticker="SPX"),
        _opt(strike=400.0, qty=1, conid=3, ticker="QQQ"),  # lone QQQ leg, ignored
    ])
    out = detect_existing_spreads(client, account_id="A1", today_et=TODAY)
    assert len(out) == 1
    assert out[0].symbol == "SPX"


def test_handles_positions_call_failure_gracefully() -> None:
    class BrokenClient:
        def positions(self, *, account_id: str, page_id: int = 0) -> FakeResp:
            raise RuntimeError("network down")

    out = detect_existing_spreads(BrokenClient(), account_id="A1", today_et=TODAY)
    assert out == []
