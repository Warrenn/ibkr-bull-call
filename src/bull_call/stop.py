"""Pure state machine for the breakeven stop loss.

Rules (downside-only stop):
  - **Arm** the first time spot is observed at or above ``breakeven``,
    *unless* we're already inside the suppression window before close
    (no point arming if there's no time to fire).
  - Once armed, **fire** on the first tick where spot < breakeven, *unless*
    we're inside the suppression window — in which case **suppress**
    and let cash settlement run instead.
  - The state itself only flips ``armed`` on ARM; FIRE/SUPPRESS are signals
    to the caller, who is responsible for submitting the close order and
    journalling.  This keeps `advance` pure.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum, auto


class Action(Enum):
    NONE = auto()
    ARM = auto()
    FIRE = auto()
    SUPPRESS = auto()


@dataclass(frozen=True, slots=True)
class StopState:
    breakeven: float
    armed: bool


def advance(
    state: StopState,
    *,
    spot: float,
    now: dt.datetime,
    close_utc: dt.datetime,
    latest_sec: int,
) -> tuple[StopState, Action]:
    """Process one spot tick; return (new_state, action_signal)."""

    seconds_to_close = (close_utc - now).total_seconds()
    in_suppression = seconds_to_close <= latest_sec

    if not state.armed:
        if spot >= state.breakeven and not in_suppression:
            return (StopState(breakeven=state.breakeven, armed=True), Action.ARM)
        return (state, Action.NONE)

    # armed
    if spot < state.breakeven:
        if in_suppression:
            return (state, Action.SUPPRESS)
        return (state, Action.FIRE)

    return (state, Action.NONE)


def state_from_journal_events(
    *, breakeven: float, events: Iterable[str]
) -> StopState:
    """Rebuild stop state from the persisted journal on process restart.

    Any journal entry implies the stop was armed at some point during the
    session — so we resume armed.
    """

    armed = any(events)  # truthy iff at least one event recorded
    return StopState(breakeven=breakeven, armed=armed)
