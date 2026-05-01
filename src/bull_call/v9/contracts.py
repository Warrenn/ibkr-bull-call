"""CONID lookup for v9's SPDR universe + SPY benchmark.

IBKR identifies tradable instruments by ``conid`` (contract ID). For
options the bot resolves conids per-strike per-day; for ETFs the
conid is stable, so we look up once and cache for the process lifetime.

Cache strategy: a module-level dict survives across `lookup_conids`
calls. Tests can pass an explicit ``cache`` dict to isolate.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol


class ConidLookupError(RuntimeError):
    """Raised when ``search_contract_by_symbol`` returns no STK match."""


class _ClientLike(Protocol):
    def search_contract_by_symbol(
        self, symbol: str, *, name: bool | None = ..., sec_type: str | None = ...
    ) -> Any: ...


SPDR_CONID_UNIVERSE: tuple[str, ...] = (
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP",
    "XLI", "XLB", "XLU", "XLRE", "XLC",
    "SPY",
)


# Module-level cache. Keyed by ticker → conid (int). Cleared explicitly
# in tests via ``contracts._MODULE_CACHE.clear()``.
_MODULE_CACHE: dict[str, int] = {}


def _extract_stk_conid(ticker: str, response_data: Any) -> int:
    """Return the first STK conid from a search_contract_by_symbol response."""
    if not response_data:
        raise ConidLookupError(f"no contracts returned for {ticker}")

    # Response may be a list of dicts or a dict; normalize
    if isinstance(response_data, dict):
        candidates = [response_data]
    elif isinstance(response_data, list):
        candidates = response_data
    else:
        raise ConidLookupError(
            f"unexpected response shape for {ticker}: {type(response_data).__name__}"
        )

    # Prefer entries explicitly tagged secType STK; fall back to first non-empty
    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        sec_type = entry.get("secType") or entry.get("sec_type") or ""
        conid = entry.get("conid")
        if str(sec_type).upper() == "STK" and conid is not None:
            return int(conid)

    # If none was tagged STK, take the first conid we see (the API
    # filter sec_type="STK" should have already narrowed it).
    for entry in candidates:
        if isinstance(entry, dict) and entry.get("conid") is not None:
            return int(entry["conid"])

    raise ConidLookupError(f"no STK match in response for {ticker}")


def lookup_conids(
    client: _ClientLike,
    *,
    tickers: Iterable[str],
    cache: dict[str, int] | None = None,
    use_cache: bool = True,
) -> dict[str, int]:
    """Resolve each ticker to its IBKR conid.

    Parameters
    ----------
    client:
        An ``ibind.IbkrClient`` (or fake) exposing ``search_contract_by_symbol``.
    tickers:
        Tickers to resolve.
    cache:
        Optional explicit cache dict (used for test isolation). If
        provided, it overrides the module-level cache.
    use_cache:
        When True (default), reuse cached values; when False, force a
        fresh API lookup for every ticker (still updates the cache).

    Returns
    -------
    Dict mapping ticker → conid for every input ticker.

    Raises
    ------
    ConidLookupError:
        If ``search_contract_by_symbol`` returns no STK match for a
        given ticker.
    """
    active_cache = cache if cache is not None else _MODULE_CACHE
    result: dict[str, int] = {}

    for ticker in tickers:
        if use_cache and ticker in active_cache:
            result[ticker] = active_cache[ticker]
            continue
        response = client.search_contract_by_symbol(
            ticker, sec_type="STK"
        )
        data = getattr(response, "data", response)
        conid = _extract_stk_conid(ticker, data)
        active_cache[ticker] = conid
        result[ticker] = conid

    return result
