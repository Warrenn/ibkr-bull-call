"""Unit tests for bull_call.cpapi.spot.stream_ticks.

Covers the silence-sentinel emission used by R23a (data-outage flatten).
We bypass the real QueueAccessor by passing a small duck-typed fake whose
``get(block, timeout)`` either returns a parsed message or raises queue.Empty.
"""

from __future__ import annotations

import datetime as dt
import queue
import time
from collections.abc import Iterable
from typing import Any

import pytest

from bull_call.cpapi.spot import stream_ticks


class FakeAccessor:
    """Replays a scripted sequence of (delay, value) on each .get() call.

    A value of ``queue.Empty`` raises queue.Empty (simulates a poll timeout
    with no message). Any other value is returned as the WS message.
    """

    def __init__(self, script: Iterable[Any]) -> None:
        self._iter = iter(script)

    def get(self, *, block: bool, timeout: float) -> Any:
        try:
            value = next(self._iter)
        except StopIteration as exc:
            raise queue.Empty from exc
        if value is queue.Empty:
            raise queue.Empty
        return value


class SleepingEmptyAccessor:
    """Each .get() sleeps for ``sleep_s`` then raises queue.Empty — UP TO
    ``max_calls`` times, after which it raises immediately without sleeping.

    The post-cap fast-path is purely a runaway-loop safety guard: tests
    only need a handful of sleep-then-empty calls to verify timing
    behaviour, but ``stream_ticks`` is an infinite generator. Without the
    cap, a regression that loops more than expected would block the test
    process for ``sleep_s * forever``.

    We also assert the production code calls ``get(block=True, timeout=...)``
    so the contract is locked in addition to being exercised — the params
    are not just style decoration.
    """

    def __init__(self, sleep_s: float, max_calls: int) -> None:
        self._sleep_s = sleep_s
        self._max_calls = max_calls
        self._calls = 0
        self.observed_calls: list[tuple[bool, float]] = []

    def get(self, *, block: bool, timeout: float) -> Any:
        self.observed_calls.append((block, timeout))
        assert block is True, "stream_ticks must call get with block=True"
        assert timeout > 0, "stream_ticks must pass a positive poll timeout"
        self._calls += 1
        # Safety cap: after max_calls, raise immediately without sleeping
        # so a misbehaving generator can't block the test indefinitely.
        if self._calls > self._max_calls:
            raise queue.Empty
        time.sleep(self._sleep_s)
        raise queue.Empty


def _drain(stream: Any, n: int) -> list[tuple[float | None, dt.datetime]]:
    out: list[tuple[float | None, dt.datetime]] = []
    for item in stream:
        out.append(item)
        if len(out) >= n:
            break
    return out


def test_stream_yields_real_tick_when_message_has_price() -> None:
    accessor = FakeAccessor([{"31": "5005.0"}])
    close_utc = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)

    stream = stream_ticks(accessor, close_utc=close_utc, poll_timeout_s=0.001)
    out = _drain(stream, 1)
    assert out[0][0] == pytest.approx(5005.0)


def test_stream_yields_silence_sentinel_when_queue_empties() -> None:
    """When the queue keeps returning Empty, the stream emits None ticks at
    silence_emit_interval cadence."""

    accessor = FakeAccessor([queue.Empty, queue.Empty, queue.Empty])
    close_utc = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)

    stream = stream_ticks(
        accessor, close_utc=close_utc,
        poll_timeout_s=0.001, silence_emit_interval_s=0.0,
    )
    out = _drain(stream, 3)
    assert all(spot is None for spot, _ in out)


def test_stream_yields_silence_sentinel_for_junk_messages() -> None:
    """Heartbeat / non-price messages still drive silence emission."""

    accessor = FakeAccessor([
        {"foo": "bar"},
        {"baz": "qux"},
    ])
    close_utc = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)

    stream = stream_ticks(
        accessor, close_utc=close_utc,
        poll_timeout_s=0.001, silence_emit_interval_s=0.0,
    )
    out = _drain(stream, 2)
    assert all(spot is None for spot, _ in out)


def test_stream_real_tick_resets_silence_window() -> None:
    """A real price clears the silence baseline so the next None comes only
    after the configured interval — not on every poll."""

    accessor = FakeAccessor([
        {"31": "5005.0"},
        queue.Empty,
        queue.Empty,
    ])
    close_utc = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)

    # silence_emit_interval=999 means the second/third Empty should NOT yield
    # a None within this short test window.
    stream = stream_ticks(
        accessor, close_utc=close_utc,
        poll_timeout_s=0.001, silence_emit_interval_s=999.0,
    )
    out = _drain(stream, 1)
    assert out[0][0] == pytest.approx(5005.0)
    # The next two get() calls return Empty but silence_emit_interval is too
    # long for a sentinel to land — the stream blocks on subsequent polls
    # (we already broke out at n=1, so just verify the first yield was real).


def test_silence_sentinel_timestamp_reflects_post_block_wall_clock() -> None:
    """Regression: the yielded ``now`` must be sampled AFTER the blocking
    get() returns, not before it.

    With the old code, ``now`` was captured at the top of the loop iteration,
    before ``accessor.get(block=True, timeout=poll_timeout_s)`` blocked — so
    by the time the silence sentinel was yielded, the timestamp could be
    stale by up to ``poll_timeout_s``. The consumer (monitor_stop) then
    undercounts ``blind_sec`` by the same amount, delaying R23a's emergency
    flatten beyond the configured budget.

    Here we sleep 200ms inside ``get()`` and assert the sentinel's
    timestamp has moved forward by at least 50ms relative to wall-clock at
    the start of the call. The bug is binary — buggy code yields a
    timestamp within microseconds of ``wall_before`` (so ~0ms elapsed),
    fixed code yields ~200ms. The 50ms threshold gives ample margin for
    scheduler jitter on slow CI runners while still definitively
    distinguishing the two regimes.
    """

    sleep_s = 0.2
    threshold_s = 0.05  # generous margin: bug yields ~0ms, fix yields ~200ms
    accessor = SleepingEmptyAccessor(sleep_s=sleep_s, max_calls=5)
    close_utc = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)

    stream = stream_ticks(
        accessor, close_utc=close_utc,
        poll_timeout_s=sleep_s + 0.1, silence_emit_interval_s=0.0,
    )

    wall_before = dt.datetime.now(dt.timezone.utc)
    out = _drain(stream, 1)
    wall_after = dt.datetime.now(dt.timezone.utc)

    assert len(out) == 1
    spot, ts = out[0]
    assert spot is None
    elapsed_to_ts = (ts - wall_before).total_seconds()
    assert elapsed_to_ts >= threshold_s, (
        f"silence sentinel timestamp is stale: {elapsed_to_ts:.4f}s after "
        f"wall_before, but the get() blocked for ~{sleep_s}s before raising "
        f"Empty (threshold={threshold_s}s)"
    )
    assert ts <= wall_after, "sentinel timestamp must not be in the future"
    # Lock the contract: stream_ticks calls get(block=True, timeout>0).
    assert accessor.observed_calls, "stream_ticks did not call accessor.get"
    block, timeout_arg = accessor.observed_calls[0]
    assert block is True
    assert timeout_arg > 0


def test_stream_stops_at_close_utc() -> None:
    """The stream returns once the wall clock crosses close_utc."""

    accessor = FakeAccessor([queue.Empty] * 100)
    close_utc = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)

    stream = stream_ticks(accessor, close_utc=close_utc, poll_timeout_s=0.001)
    out = list(stream)
    assert out == []


# ---------- _spot_from_message + _to_float helpers --------------------------


def test_spot_from_message_dict_with_last() -> None:
    from bull_call.cpapi.spot import _spot_from_message

    assert _spot_from_message({"31": "5005.0"}) == pytest.approx(5005.0)


def test_spot_from_message_dict_with_no_last_uses_midpoint() -> None:
    from bull_call.cpapi.spot import _spot_from_message

    spot = _spot_from_message({"84": "5004.0", "86": "5006.0"})
    assert spot == pytest.approx(5005.0)  # mean of bid+ask


def test_spot_from_message_dict_with_only_bid() -> None:
    """Missing ask but valid bid — fall back to bid alone."""

    from bull_call.cpapi.spot import _spot_from_message

    spot = _spot_from_message({"84": "5004.50"})
    assert spot == pytest.approx(5004.50)


def test_spot_from_message_dict_with_no_usable_fields() -> None:
    from bull_call.cpapi.spot import _spot_from_message

    assert _spot_from_message({}) is None
    assert _spot_from_message({"99": "value"}) is None
    assert _spot_from_message({"31": "0", "84": "0", "86": "0"}) is None


def test_spot_from_message_decodes_json_string() -> None:
    """ibind sometimes delivers raw JSON strings instead of dicts —
    decode and treat the same as a dict."""

    from bull_call.cpapi.spot import _spot_from_message

    spot = _spot_from_message('{"31": "5005.5"}')
    assert spot == pytest.approx(5005.5)


def test_spot_from_message_decodes_json_bytes() -> None:
    from bull_call.cpapi.spot import _spot_from_message

    spot = _spot_from_message(b'{"31": "5005.5"}')
    assert spot == pytest.approx(5005.5)


def test_spot_from_message_returns_none_for_invalid_json_string() -> None:
    from bull_call.cpapi.spot import _spot_from_message

    assert _spot_from_message("not json at all") is None
    assert _spot_from_message(b"\xfe\xff invalid") is None


def test_spot_from_message_returns_none_for_non_dict_json() -> None:
    """A JSON list or scalar — not a dict — is unusable."""

    from bull_call.cpapi.spot import _spot_from_message

    assert _spot_from_message("[1, 2, 3]") is None
    assert _spot_from_message('"just a string"') is None
    assert _spot_from_message("42") is None


def test_spot_from_message_returns_none_for_unsupported_type() -> None:
    from bull_call.cpapi.spot import _spot_from_message

    assert _spot_from_message(None) is None
    assert _spot_from_message(123) is None
    assert _spot_from_message([1, 2, 3]) is None


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, "nan"),
        (5.5, 5.5),
        (5, 5.0),
        ("5005.0", 5005.0),
        ("5,005.50", 5005.50),    # comma-separated
        # IBKR's C/H/K/% suffixes (close / halt / thousands / percent)
        # all stripped via the same .rstrip("CHK%") call.
        ("5.5C", 5.5),
        ("5.5H", 5.5),
        ("5.5K", 5.5),
        ("5.5%", 5.5),
        ("nope", "nan"),           # non-numeric string
        (object(), "nan"),         # unsupported type
    ],
)
def test_to_float(value: Any, expected: Any) -> None:
    from bull_call.cpapi.spot import _to_float

    result = _to_float(value)
    if expected == "nan":
        import math
        assert math.isnan(result)
    else:
        assert result == pytest.approx(expected)


def test_stream_returns_without_blocking_when_close_already_passed() -> None:
    """Regression: the loop must check close_utc BEFORE the blocking get(),
    so a stream started after the session has ended doesn't waste one full
    poll_timeout_s blocked before noticing.

    With a 1s poll_timeout and a 5ms wall budget, only a pre-block check
    can satisfy the assertion."""

    accessor = SleepingEmptyAccessor(sleep_s=1.0, max_calls=5)
    close_utc = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)

    wall_before = time.monotonic()
    stream = stream_ticks(accessor, close_utc=close_utc, poll_timeout_s=1.0)
    out = list(stream)
    elapsed = time.monotonic() - wall_before

    assert out == []
    assert elapsed < 0.05, (
        f"stream blocked for {elapsed:.3f}s before noticing close_utc — "
        "expected < 50ms via pre-block check"
    )
    # Confirms we never even called get():
    assert accessor.observed_calls == []
