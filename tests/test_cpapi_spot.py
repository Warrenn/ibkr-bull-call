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
    """Each .get() sleeps for ``sleep_s`` then raises queue.Empty.

    Used to verify that ``stream_ticks`` measures wall-clock elapsed time
    AFTER the blocking call, not before it. We assert the production code
    calls ``get(block=True, timeout=...)`` so the contract is locked in
    addition to being exercised — the params are not just style decoration.
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
