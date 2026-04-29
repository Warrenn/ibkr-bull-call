"""Tests for the breakeven stop state machine."""

from __future__ import annotations

import datetime as dt

import pytest

from bull_call.stop import Action, StopState, advance


def at(seconds_before_close: int) -> dt.datetime:
    """Build a UTC ``now`` that's N seconds before a fixed reference close."""

    return CLOSE_UTC - dt.timedelta(seconds=seconds_before_close)


CLOSE_UTC = dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc)
LATEST_SEC = 30


def test_below_breakeven_at_entry_does_not_arm() -> None:
    state = StopState(breakeven=5000.0, armed=False)
    new_state, action = advance(state, spot=4998.0, now=at(3600),
                                 close_utc=CLOSE_UTC, latest_sec=LATEST_SEC)
    assert action is Action.NONE
    assert new_state == state


def test_first_tick_at_or_above_breakeven_arms() -> None:
    state = StopState(breakeven=5000.0, armed=False)
    new_state, action = advance(state, spot=5000.0, now=at(3600),
                                 close_utc=CLOSE_UTC, latest_sec=LATEST_SEC)
    assert action is Action.ARM
    assert new_state.armed is True


def test_armed_then_dip_below_fires() -> None:
    state = StopState(breakeven=5000.0, armed=True)
    new_state, action = advance(state, spot=4999.99, now=at(3600),
                                 close_utc=CLOSE_UTC, latest_sec=LATEST_SEC)
    assert action is Action.FIRE
    assert new_state.armed is True  # state unchanged on fire; caller marks fired separately


def test_armed_above_breakeven_no_fire() -> None:
    state = StopState(breakeven=5000.0, armed=True)
    _, action = advance(state, spot=5001.0, now=at(3600),
                        close_utc=CLOSE_UTC, latest_sec=LATEST_SEC)
    assert action is Action.NONE


def test_armed_dip_within_suppression_window() -> None:
    state = StopState(breakeven=5000.0, armed=True)
    _, action = advance(state, spot=4999.0, now=at(LATEST_SEC),
                        close_utc=CLOSE_UTC, latest_sec=LATEST_SEC)
    # exactly at the boundary: (close - now) == latest_sec → suppressed
    assert action is Action.SUPPRESS


def test_armed_dip_just_outside_suppression_window_fires() -> None:
    state = StopState(breakeven=5000.0, armed=True)
    _, action = advance(state, spot=4999.0, now=at(LATEST_SEC + 1),
                        close_utc=CLOSE_UTC, latest_sec=LATEST_SEC)
    assert action is Action.FIRE


def test_unarmed_dip_in_suppression_window_does_nothing() -> None:
    # Trade went underwater immediately and never armed — no fire, no suppress.
    state = StopState(breakeven=5000.0, armed=False)
    _, action = advance(state, spot=4990.0, now=at(10),
                        close_utc=CLOSE_UTC, latest_sec=LATEST_SEC)
    assert action is Action.NONE


def test_arm_blocked_within_suppression_window() -> None:
    # Even if spot crosses up in the last 30s, it's pointless to arm — there's no
    # time for a meaningful stop.  We don't arm in the suppression window.
    state = StopState(breakeven=5000.0, armed=False)
    new_state, action = advance(state, spot=5005.0, now=at(10),
                                 close_utc=CLOSE_UTC, latest_sec=LATEST_SEC)
    assert action is Action.NONE
    assert new_state.armed is False


def test_replay_tick_stream() -> None:
    """End-to-end replay: trade enters underwater, rises, dips → fires."""

    state = StopState(breakeven=5000.0, armed=False)
    actions = []

    ticks = [
        (4998.0, 3600),  # below — no arm
        (4999.5, 3500),  # still below — no arm
        (5000.5, 3400),  # crosses up — ARM
        (5001.2, 3300),  # above — none
        (4999.8, 3200),  # dips — FIRE (well outside suppression)
    ]
    for spot, secs_before in ticks:
        state, action = advance(state, spot=spot, now=at(secs_before),
                                close_utc=CLOSE_UTC, latest_sec=LATEST_SEC)
        actions.append(action)

    assert actions == [
        Action.NONE, Action.NONE, Action.ARM, Action.NONE, Action.FIRE,
    ]


def test_replay_with_suppression() -> None:
    state = StopState(breakeven=5000.0, armed=False)
    actions = []

    ticks = [
        (5000.5, 3400),  # ARM
        (5002.0, 1000),  # NONE
        (4998.0, 25),    # SUPPRESS (within last 30s)
    ]
    for spot, secs_before in ticks:
        state, action = advance(state, spot=spot, now=at(secs_before),
                                close_utc=CLOSE_UTC, latest_sec=LATEST_SEC)
        actions.append(action)

    assert actions == [Action.ARM, Action.NONE, Action.SUPPRESS]


@pytest.mark.parametrize("event,expected_armed", [
    ("armed", True),
    ("fired", True),
    ("suppressed", True),
])
def test_state_from_journal(event: str, expected_armed: bool) -> None:
    """Restart recovery: any 'armed' (or later) journal entry → resume armed."""

    from bull_call.stop import state_from_journal_events

    state = state_from_journal_events(breakeven=5000.0, events=[event])
    assert state.armed is expected_armed


def test_state_from_journal_empty_starts_disarmed() -> None:
    from bull_call.stop import state_from_journal_events

    state = state_from_journal_events(breakeven=5000.0, events=[])
    assert state.armed is False


def test_state_from_journal_armed_from_recovery_overrides_empty_journal() -> None:
    """When we adopt a position from IBKR (no journal exists), force
    ``armed=True`` so the next sub-breakeven tick fires the stop."""

    from bull_call.stop import state_from_journal_events

    state = state_from_journal_events(
        breakeven=5000.0, events=[], armed_from_recovery=True,
    )
    assert state.armed is True
