"""Tests for ``bull_call.v9.contracts`` — CONID lookup for SPDR + SPY
tickers via ibind's ``search_contract_by_symbol``.

Cache invariant: each ticker is looked up at most once per module
lifetime; the first call's result is reused for subsequent calls.
"""

from __future__ import annotations

from typing import Any

import pytest

from bull_call.v9.contracts import (
    SPDR_CONID_UNIVERSE,
    ConidLookupError,
    lookup_conids,
)


class _Resp:
    def __init__(self, data: Any) -> None:
        self.data = data


class _RecordingClient:
    """FakeClient that records every search invocation."""

    def __init__(self, conid_map: dict[str, int]) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self._map = conid_map

    def search_contract_by_symbol(
        self, symbol: str, *, name: bool | None = None, sec_type: str | None = None
    ) -> _Resp:
        self.calls.append((symbol, sec_type))
        if symbol not in self._map:
            return _Resp([])  # no contracts found
        return _Resp([{"conid": self._map[symbol], "symbol": symbol,
                       "secType": "STK"}])


def test_lookup_returns_conid_for_each_ticker() -> None:
    client = _RecordingClient({"XLK": 11111, "SPY": 22222})

    result = lookup_conids(client, tickers=("XLK", "SPY"))  # type: ignore[arg-type]

    assert result == {"XLK": 11111, "SPY": 22222}


def test_lookup_passes_stk_sec_type_filter() -> None:
    """Stock contracts only — never resolves to options/futures by accident."""
    client = _RecordingClient({"XLK": 11111})
    lookup_conids(client, tickers=("XLK",), use_cache=False)  # type: ignore[arg-type]

    assert ("XLK", "STK") in client.calls


def test_lookup_caches_after_first_call() -> None:
    """A second call for the same ticker should NOT re-hit the API."""
    client = _RecordingClient({"XLK": 11111})

    # Use a fresh cache each time so test isolation holds
    cache: dict[str, int] = {}
    lookup_conids(client, tickers=("XLK",), cache=cache)  # type: ignore[arg-type]
    lookup_conids(client, tickers=("XLK",), cache=cache)  # type: ignore[arg-type]

    assert client.calls == [("XLK", "STK")]
    assert cache == {"XLK": 11111}


def test_lookup_partial_cache_only_misses_call_through() -> None:
    """Cached tickers reuse cached values; missing tickers hit the API."""
    client = _RecordingClient({"XLK": 11111, "SPY": 22222})
    cache = {"XLK": 99999}  # pre-cached at a stale value (still respected)

    result = lookup_conids(
        client, tickers=("XLK", "SPY"), cache=cache,  # type: ignore[arg-type]
    )

    assert result == {"XLK": 99999, "SPY": 22222}
    # SPY only — XLK was already cached
    assert client.calls == [("SPY", "STK")]


def test_lookup_raises_when_no_match() -> None:
    """A ticker the API can't resolve is a hard error — caller must
    investigate (delisted? typo?) before any rebalance can run."""
    client = _RecordingClient({})  # no symbols match

    with pytest.raises(ConidLookupError, match="XLK"):
        lookup_conids(client, tickers=("XLK",), use_cache=False)  # type: ignore[arg-type]


def test_spdr_universe_constant_covers_v9_tickers_plus_spy() -> None:
    """The exposed universe should include all 11 SPDRs and SPY benchmark."""
    expected = {
        "XLK", "XLF", "XLE", "XLV", "XLY", "XLP",
        "XLI", "XLB", "XLU", "XLRE", "XLC", "SPY",
    }
    assert set(SPDR_CONID_UNIVERSE) == expected


def test_lookup_picks_first_stk_match_when_multiple_returned() -> None:
    """search_contract_by_symbol can return multiple records (ADRs etc.);
    pick the first STK record."""

    class MultiClient:
        def search_contract_by_symbol(self, symbol: str, **_: Any) -> _Resp:
            # Some weird response with two records
            return _Resp([
                {"conid": 99999, "symbol": symbol, "secType": "OPT"},
                {"conid": 11111, "symbol": symbol, "secType": "STK"},
            ])

    result = lookup_conids(MultiClient(), tickers=("XLK",), use_cache=False)  # type: ignore[arg-type]
    assert result == {"XLK": 11111}


def test_lookup_uses_module_cache_by_default_across_calls() -> None:
    """The module-level cache (use_cache=True default) survives across
    multiple lookup_conids() calls within a single Python session.
    Test by invoking twice and asserting the second is fast-path."""
    from bull_call.v9 import contracts

    contracts._MODULE_CACHE.clear()

    client_1 = _RecordingClient({"XLK": 11111})
    lookup_conids(client_1, tickers=("XLK",))  # type: ignore[arg-type]

    client_2 = _RecordingClient({"XLK": 22222})  # different value!
    result = lookup_conids(client_2, tickers=("XLK",))  # type: ignore[arg-type]

    assert client_2.calls == []  # cache hit
    assert result == {"XLK": 11111}  # original cache value
