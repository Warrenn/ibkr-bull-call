"""Unit tests for bull_call.cpapi.chain helpers + small API wrappers.

The big one — ``fetch_0dte_call_chain`` — orchestrates 4 IBKR calls
across an option chain and is exercised live during dry-run / paper.
This module focuses on the pieces unit-testable without a gateway:

  - ``_month_token`` / ``_expiry_yyyymmdd`` — pure date formatting
  - ``_is_realtime`` — quote-availability marker parsing
  - ``_safe_float`` — robust string-to-float for IBKR's varied formats
  - ``_spot_from_row`` — multi-field spot extraction
  - ``fetch_spot`` — one-shot wrapper around ``live_marketdata_snapshot``
  - ``estimate_close_credit`` — bid(long) - ask(short) with both legs
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

import pytest

from bull_call.chain import OptionContract
from bull_call.cpapi import chain as cpapi_chain


# ---------- pure helpers ----------------------------------------------------


def test_month_token() -> None:
    assert cpapi_chain._month_token(dt.date(2026, 4, 29)) == "APR26"
    assert cpapi_chain._month_token(dt.date(2025, 1, 3)) == "JAN25"
    assert cpapi_chain._month_token(dt.date(2026, 12, 31)) == "DEC26"


def test_expiry_yyyymmdd() -> None:
    assert cpapi_chain._expiry_yyyymmdd(dt.date(2026, 4, 29)) == "20260429"
    assert cpapi_chain._expiry_yyyymmdd(dt.date(2025, 1, 3)) == "20250103"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("R", True),
        ("RT", True),       # any string starting with R
        ("D", False),       # delayed
        ("Z", False),       # frozen
        ("Y", False),
        ("N", False),
        ("", False),        # empty
        (None, False),
    ],
)
def test_is_realtime(value: str | None, expected: bool) -> None:
    assert cpapi_chain._is_realtime(value) is expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, math.nan),
        ("", math.nan),
        ("nope", math.nan),
        ("4995.0", 4995.0),
        ("4,995.50", 4995.50),  # comma-separated
        ("4.95C", 4.95),         # IBKR sometimes appends C/H/K/% suffixes
        ("4.95H", 4.95),
        ("100%", 100.0),
        (4995.0, 4995.0),       # already a float
        (5, 5.0),               # int
        (object(), math.nan),   # unsupported type
    ],
)
def test_safe_float(value: Any, expected: float) -> None:
    result = cpapi_chain._safe_float(value)
    if math.isnan(expected):
        assert math.isnan(result)
    else:
        assert result == pytest.approx(expected)


# ---------- _spot_from_row --------------------------------------------------


def test_spot_from_row_prefers_last_price() -> None:
    """When all three are available, _spot_from_row picks last (field 31)
    before falling back to bid (84) then ask (86)."""

    row = {"31": "5005.50", "84": "5005.00", "86": "5005.75"}
    assert cpapi_chain._spot_from_row(row) == pytest.approx(5005.50)


def test_spot_from_row_falls_through_to_bid_then_ask() -> None:
    """If last is missing/zero, fall back to bid; then ask."""

    bid_only = {"84": "5005.00"}
    assert cpapi_chain._spot_from_row(bid_only) == pytest.approx(5005.00)

    ask_only = {"86": "5005.75"}
    assert cpapi_chain._spot_from_row(ask_only) == pytest.approx(5005.75)


def test_spot_from_row_returns_none_when_no_usable_field() -> None:
    """Missing, empty, zero, or non-numeric values all skip — return None."""

    for row in (
        {},
        {"31": ""},
        {"31": "0"},        # zero is not a usable spot
        {"31": "nope"},     # non-numeric
        {"31": "-5.0"},     # negative is not usable
        {"99": "5000"},     # wrong field id
    ):
        assert cpapi_chain._spot_from_row(row) is None, row


# ---------- fetch_spot ------------------------------------------------------


class _Resp:
    def __init__(self, data: Any) -> None:
        self.data = data


def test_fetch_spot_returns_first_row_spot() -> None:
    captured: dict[str, Any] = {}

    class FakeClient:
        def live_marketdata_snapshot(self, *, conids: str, fields: str) -> _Resp:
            captured["conids"] = conids
            captured["fields"] = fields
            return _Resp([{"31": "5005.50"}])

    spot = cpapi_chain.fetch_spot(FakeClient(), conid=12345)  # type: ignore[arg-type]
    assert spot == pytest.approx(5005.50)
    # Should have asked for the right conid and the right fields.
    assert captured["conids"] == "12345"
    assert "31" in captured["fields"]   # last price
    assert "84" in captured["fields"]   # bid
    assert "86" in captured["fields"]   # ask


def test_fetch_spot_returns_none_when_no_rows() -> None:
    class FakeClient:
        def live_marketdata_snapshot(self, *, conids: str, fields: str) -> _Resp:
            return _Resp([])

    assert cpapi_chain.fetch_spot(FakeClient(), conid=1) is None  # type: ignore[arg-type]


def test_fetch_spot_returns_none_when_data_is_none() -> None:
    class FakeClient:
        def live_marketdata_snapshot(self, *, conids: str, fields: str) -> _Resp:
            return _Resp(None)

    assert cpapi_chain.fetch_spot(FakeClient(), conid=1) is None  # type: ignore[arg-type]


# ---------- estimate_close_credit -------------------------------------------


def test_estimate_close_credit_uses_bid_long_minus_ask_short() -> None:
    """Conservative crossing-the-spread estimate: bid(long) - ask(short)
    is the worst likely fill on a SELL combo MKT now."""

    long_leg = OptionContract(strike=4995.0, conid=111, right="C", expiry="20260429")
    short_leg = OptionContract(strike=5005.0, conid=222, right="C", expiry="20260429")

    class FakeClient:
        def live_marketdata_snapshot(self, *, conids: str, fields: str) -> _Resp:
            return _Resp([
                {"conid": 111, "84": "3.00", "86": "3.20"},  # long: bid 3.00
                {"conid": 222, "84": "0.45", "86": "0.55"},  # short: ask 0.55
            ])

    credit = cpapi_chain.estimate_close_credit(
        FakeClient(),  # type: ignore[arg-type]
        long_leg=long_leg,
        short_leg=short_leg,
    )
    assert credit == pytest.approx(3.00 - 0.55)


def test_estimate_close_credit_returns_none_when_either_leg_missing() -> None:
    long_leg = OptionContract(strike=4995.0, conid=111, right="C", expiry="20260429")
    short_leg = OptionContract(strike=5005.0, conid=222, right="C", expiry="20260429")

    class FakeClient:
        def live_marketdata_snapshot(self, *, conids: str, fields: str) -> _Resp:
            # Missing the short leg.
            return _Resp([{"conid": 111, "84": "3.00", "86": "3.20"}])

    assert cpapi_chain.estimate_close_credit(
        FakeClient(),  # type: ignore[arg-type]
        long_leg=long_leg,
        short_leg=short_leg,
    ) is None


def test_estimate_close_credit_returns_none_when_quote_unparseable() -> None:
    long_leg = OptionContract(strike=4995.0, conid=111, right="C", expiry="20260429")
    short_leg = OptionContract(strike=5005.0, conid=222, right="C", expiry="20260429")

    class FakeClient:
        def live_marketdata_snapshot(self, *, conids: str, fields: str) -> _Resp:
            return _Resp([
                {"conid": 111, "84": "n/a"},      # bid unparseable
                {"conid": 222, "86": "0.55"},
            ])

    assert cpapi_chain.estimate_close_credit(
        FakeClient(),  # type: ignore[arg-type]
        long_leg=long_leg,
        short_leg=short_leg,
    ) is None


def test_estimate_close_credit_handles_negative_credit() -> None:
    """A negative credit (long bid < short ask) is a real signal — the
    spread is upside-down. Pass it through; the caller (uneconomic guard)
    decides what to do."""

    long_leg = OptionContract(strike=4995.0, conid=111, right="C", expiry="20260429")
    short_leg = OptionContract(strike=5005.0, conid=222, right="C", expiry="20260429")

    class FakeClient:
        def live_marketdata_snapshot(self, *, conids: str, fields: str) -> _Resp:
            return _Resp([
                {"conid": 111, "84": "0.50"},
                {"conid": 222, "86": "0.80"},
            ])

    credit = cpapi_chain.estimate_close_credit(
        FakeClient(),  # type: ignore[arg-type]
        long_leg=long_leg,
        short_leg=short_leg,
    )
    assert credit == pytest.approx(0.50 - 0.80)
    assert credit is not None and credit < 0


# ---------- fetch_0dte_call_chain orchestrator ------------------------------
#
# Big function, many error branches. We build a configurable fake
# ``IbkrClient`` that returns scripted responses for each of the four
# methods this orchestrator calls. Each test overrides only the parts
# of the script it needs to flip a specific branch.


_TODAY = dt.date(2026, 4, 29)
_EXPIRY = "20260429"
_MONTH = "APR26"
_UNDERLYING_CONID = 416904
_REALTIME_AVAIL = "RT"


def _spot_row(*, last: str = "5000.0", iv: str = "0.18", avail: str = _REALTIME_AVAIL) -> dict:
    return {
        "31": last,                  # last
        "84": "5000.0",              # bid
        "86": "5000.5",              # ask
        "7283": iv,                  # underlying ATM IV
        "6509": avail,               # market data availability
    }


def _opt_row(conid: int, *, bid: str = "5.00", ask: str = "5.20",
             iv: str = "0.18", avail: str = _REALTIME_AVAIL) -> dict:
    return {
        "conid": conid,
        "84": bid,
        "86": ask,
        "7633": iv,                  # per-strike IV
        "6509": avail,
    }


def _make_fake_client(
    *,
    underlying_match: list[dict] | None = None,
    spot_row_override: dict | None = None,
    call_strikes: list[float] | None = None,
    secdef_info_per_strike: dict[float, list[dict]] | None = None,
    option_rows: list[dict] | None = None,
):  # type: ignore[no-untyped-def]
    """Build a duck-typed IbkrClient with scripted method responses.

    Defaults form a happy-path setup with one viable strike (4995 →
    short-target 5005) on a single underlying (SPX).
    """

    if underlying_match is None:
        underlying_match = [{
            "conid": _UNDERLYING_CONID,
            "sections": [{"secType": "OPT", "months": _MONTH + ";MAY26;JUN26"}],
        }]
    if call_strikes is None:
        call_strikes = [float(s) for s in range(4900, 5101, 5)]  # ~40 strikes
    if secdef_info_per_strike is None:
        secdef_info_per_strike = {
            s: [{"maturityDate": _EXPIRY, "conid": int(s) * 10}]
            for s in call_strikes
        }
    if option_rows is None:
        option_rows = [
            _opt_row(conid=int(s) * 10, bid="5.00", ask="5.20")
            for s in call_strikes
        ]

    spot_default = _spot_row()
    if spot_row_override:
        spot_default = {**spot_default, **spot_row_override}

    snapshot_calls: list[dict[str, Any]] = []

    class FakeClient:
        def search_contract_by_symbol(self, *, symbol: str, sec_type: str) -> _Resp:
            return _Resp(underlying_match)

        def live_marketdata_snapshot(self, *, conids: str, fields: str) -> _Resp:
            snapshot_calls.append({"conids": conids, "fields": fields})
            # First call is the underlying spot; later calls are the option chain.
            if conids == str(_UNDERLYING_CONID):
                return _Resp([spot_default])
            return _Resp(option_rows)

        def search_strikes_by_conid(
            self, *, conid: str, sec_type: str, month: str,
        ) -> _Resp:
            return _Resp({"call": list(call_strikes), "put": []})

        def search_secdef_info_by_conid(
            self, *, conid: str, sec_type: str, month: str,
            strike: float, right: str,
        ) -> _Resp:
            return _Resp(secdef_info_per_strike.get(strike, []))

    client = FakeClient()
    client.snapshot_calls = snapshot_calls  # type: ignore[attr-defined]
    return client


def test_fetch_chain_happy_path() -> None:
    client = _make_fake_client()
    snap = cpapi_chain.fetch_0dte_call_chain(
        client, symbol="SPX", today_et=_TODAY,  # type: ignore[arg-type]
    )
    assert snap is not None
    assert snap.symbol == "SPX"
    assert snap.expiry == _EXPIRY
    assert snap.spot == pytest.approx(5000.0)
    assert snap.atm_iv == pytest.approx(0.18)
    assert len(snap.quotes) > 0
    # All quotes have positive bid/ask.
    for q in snap.quotes:
        assert q.bid > 0
        assert q.ask > 0


def test_fetch_chain_returns_none_when_no_underlying_match() -> None:
    client = _make_fake_client(underlying_match=[])
    assert cpapi_chain.fetch_0dte_call_chain(
        client, symbol="SPX", today_et=_TODAY,  # type: ignore[arg-type]
    ) is None


def test_fetch_chain_returns_none_when_no_OPT_section() -> None:
    client = _make_fake_client(underlying_match=[{
        "conid": _UNDERLYING_CONID,
        "sections": [{"secType": "STK", "months": "APR26"}],  # wrong sec_type
    }])
    assert cpapi_chain.fetch_0dte_call_chain(
        client, symbol="SPX", today_et=_TODAY,  # type: ignore[arg-type]
    ) is None


def test_fetch_chain_returns_none_when_month_unavailable() -> None:
    """Today's month isn't in the listed months — e.g. expired or wrong cal."""

    client = _make_fake_client(underlying_match=[{
        "conid": _UNDERLYING_CONID,
        "sections": [{"secType": "OPT", "months": "MAY26;JUN26"}],
    }])
    assert cpapi_chain.fetch_0dte_call_chain(
        client, symbol="SPX", today_et=_TODAY,  # type: ignore[arg-type]
    ) is None


def test_fetch_chain_returns_none_when_spot_fetch_fails() -> None:
    """All three spot fields zero / non-numeric → spot is None → bail."""

    client = _make_fake_client(
        spot_row_override={"31": "0", "84": "0", "86": "0"},
    )
    assert cpapi_chain.fetch_0dte_call_chain(
        client, symbol="SPX", today_et=_TODAY,  # type: ignore[arg-type]
    ) is None


def test_fetch_chain_rejects_delayed_market_data() -> None:
    """Default require_realtime=True: a delayed-availability marker bails
    before any quotes are computed."""

    client = _make_fake_client(spot_row_override={"6509": "D"})  # delayed
    assert cpapi_chain.fetch_0dte_call_chain(
        client, symbol="SPX", today_et=_TODAY,  # type: ignore[arg-type]
    ) is None


def test_fetch_chain_accepts_delayed_when_require_realtime_false() -> None:
    """Backtest / dev mode: an explicit opt-out lets delayed quotes through."""

    client = _make_fake_client(spot_row_override={"6509": "D"})
    snap = cpapi_chain.fetch_0dte_call_chain(
        client, symbol="SPX", today_et=_TODAY,    # type: ignore[arg-type]
        require_realtime=False,
    )
    # The strike-level rows still default to RT, so they're accepted; spot
    # is now allowed despite being marked delayed.
    assert snap is not None
    assert snap.spot == pytest.approx(5000.0)


def test_fetch_chain_returns_none_when_no_call_strikes() -> None:
    client = _make_fake_client(call_strikes=[])
    assert cpapi_chain.fetch_0dte_call_chain(
        client, symbol="SPX", today_et=_TODAY,  # type: ignore[arg-type]
    ) is None


def test_fetch_chain_returns_none_when_no_zero_dte_contracts() -> None:
    """secdef_info returns entries whose maturity != today's expiry — i.e.
    no actual 0DTE leg exists for any strike."""

    strikes = [4995.0, 5000.0, 5005.0]
    client = _make_fake_client(
        call_strikes=strikes,
        secdef_info_per_strike={
            s: [{"maturityDate": "20260501", "conid": int(s) * 10}]  # not today
            for s in strikes
        },
    )
    assert cpapi_chain.fetch_0dte_call_chain(
        client, symbol="SPX", today_et=_TODAY,  # type: ignore[arg-type]
    ) is None


def test_fetch_chain_returns_none_when_all_quotes_unparseable() -> None:
    """Snapshot returns rows but every bid/ask is zero / non-numeric — no
    usable quotes after filtering."""

    strikes = [4995.0, 5000.0, 5005.0]
    client = _make_fake_client(
        call_strikes=strikes,
        option_rows=[
            _opt_row(conid=int(s) * 10, bid="0", ask="0") for s in strikes
        ],
    )
    assert cpapi_chain.fetch_0dte_call_chain(
        client, symbol="SPX", today_et=_TODAY,  # type: ignore[arg-type]
    ) is None


def test_fetch_chain_falls_back_to_underlying_iv_when_per_strike_iv_missing() -> None:
    """Per-strike IV at the ATM target is unavailable — orchestrator
    falls back to the underlying-level IV from the spot row."""

    strikes = [4995.0, 5000.0, 5005.0]
    client = _make_fake_client(
        call_strikes=strikes,
        # Per-strike IV is empty/missing on every option row.
        option_rows=[
            _opt_row(conid=int(s) * 10, bid="5.00", ask="5.20", iv="")
            for s in strikes
        ],
    )
    snap = cpapi_chain.fetch_0dte_call_chain(
        client, symbol="SPX", today_et=_TODAY,  # type: ignore[arg-type]
    )
    assert snap is not None
    # Used the underlying IV from spot_row (default 0.18).
    assert snap.atm_iv == pytest.approx(0.18)


def test_fetch_chain_returns_none_when_no_iv_anywhere() -> None:
    """Underlying-level IV missing AND per-strike IV missing — can't
    compute POP, refuse to trade."""

    strikes = [4995.0, 5000.0, 5005.0]
    client = _make_fake_client(
        spot_row_override={"7283": ""},          # no underlying IV
        call_strikes=strikes,
        option_rows=[
            _opt_row(conid=int(s) * 10, bid="5.00", ask="5.20", iv="")
            for s in strikes
        ],
    )
    assert cpapi_chain.fetch_0dte_call_chain(
        client, symbol="SPX", today_et=_TODAY,  # type: ignore[arg-type]
    ) is None


def test_fetch_chain_uses_IND_sectype_for_index_symbols() -> None:
    """SPX should be queried as IND, not STK — different IBKR API paths."""

    captured: dict[str, str] = {}

    class FakeClient:
        def search_contract_by_symbol(self, *, symbol: str, sec_type: str) -> _Resp:
            captured["sec_type"] = sec_type
            return _Resp([])  # empty triggers the early None return

    cpapi_chain.fetch_0dte_call_chain(
        FakeClient(), symbol="SPX", today_et=_TODAY,  # type: ignore[arg-type]
    )
    assert captured["sec_type"] == "IND"


def test_fetch_chain_uses_STK_sectype_for_non_index_symbols() -> None:
    """A non-index ticker (e.g. AAPL) should be queried as STK."""

    captured: dict[str, str] = {}

    class FakeClient:
        def search_contract_by_symbol(self, *, symbol: str, sec_type: str) -> _Resp:
            captured["sec_type"] = sec_type
            return _Resp([])

    cpapi_chain.fetch_0dte_call_chain(
        FakeClient(), symbol="AAPL", today_et=_TODAY,  # type: ignore[arg-type]
    )
    assert captured["sec_type"] == "STK"
