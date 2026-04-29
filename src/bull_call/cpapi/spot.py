"""SPX spot tick streaming via IBKR's WebSocket market-data channel.

The CPAPI WebSocket subscribes via channel ``md+{conid}`` with a JSON body
containing the field IDs we want.  ``ibind.IbkrWsClient`` wraps that and
delivers messages onto a queue accessor we drive from the main thread.

This module exposes a synchronous generator ``stream_ticks(...)`` that yields
``(spot_price, now_utc)`` tuples until the session close time passes or the
caller decides to stop.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import queue
from collections.abc import Iterator

from ibind import IbkrClient, IbkrWsClient, IbkrWsKey, QueueAccessor

log = logging.getLogger(__name__)

_FIELD_LAST = "31"
_FIELD_BID = "84"
_FIELD_ASK = "86"

_TICK_FIELDS = [_FIELD_LAST, _FIELD_BID, _FIELD_ASK]


def open_ws(client: IbkrClient, *, account_id: str) -> IbkrWsClient:
    """Create and start an IbkrWsClient bound to the given account."""

    ws = IbkrWsClient(account_id=account_id, ibkr_client=client)
    ws.start()
    return ws


def subscribe_underlying(
    ws: IbkrWsClient, *, conid: int,
) -> QueueAccessor:
    """Subscribe to streaming top-of-book for one underlying.

    Returns a queue accessor the caller can drive with .get(timeout=...).
    """

    accessor = ws.new_queue_accessor(IbkrWsKey.MARKET_DATA)
    ws.subscribe(
        channel=f"md+{conid}",
        data={"fields": _TICK_FIELDS},
    )
    return accessor


def stream_ticks(
    accessor: QueueAccessor,
    *,
    close_utc: dt.datetime,
    poll_timeout_s: float = 1.0,
) -> Iterator[tuple[float, dt.datetime]]:
    """Yield (spot, now_utc) for each tick until ``close_utc`` is reached.

    Skips heartbeat/system messages; only yields when a usable price is parsed.
    Times out the queue every ``poll_timeout_s`` so the caller's loop can also
    react to wall-clock conditions (e.g. session close).
    """

    while True:
        now = dt.datetime.now(dt.timezone.utc)
        if now >= close_utc:
            return
        try:
            raw = accessor.get(block=True, timeout=poll_timeout_s)
        except queue.Empty:
            continue
        spot = _spot_from_message(raw)
        if spot is None:
            continue
        yield spot, dt.datetime.now(dt.timezone.utc)


def _spot_from_message(message: object) -> float | None:
    """Extract a usable price from one WS message.

    Messages are typically dicts; some channels send raw bytes/str the wrapper
    didn't decode.  We check the standard fields in order: last, midpoint, bid.
    """

    data: dict | None = None
    if isinstance(message, dict):
        data = message
    elif isinstance(message, (bytes, str)):
        try:
            decoded = json.loads(message)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        data = decoded if isinstance(decoded, dict) else None
    if data is None:
        return None

    last = _to_float(data.get(_FIELD_LAST))
    if math.isfinite(last) and last > 0:
        return last
    bid = _to_float(data.get(_FIELD_BID))
    ask = _to_float(data.get(_FIELD_ASK))
    if math.isfinite(bid) and math.isfinite(ask) and bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    if math.isfinite(bid) and bid > 0:
        return bid
    return None


def _to_float(value: object) -> float:
    if value is None:
        return math.nan
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").rstrip("CHK%")
        try:
            return float(cleaned)
        except ValueError:
            return math.nan
    return math.nan
