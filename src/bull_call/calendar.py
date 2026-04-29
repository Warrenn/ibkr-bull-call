"""Market session helpers wrapping pandas_market_calendars."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from functools import lru_cache

import pandas as pd
import pandas_market_calendars as mcal

_CALENDAR_NAME = "NYSE"  # SPX 0DTE settles at the 4:00 pm ET equity close.


@dataclass(frozen=True, slots=True)
class SessionTimes:
    open_utc: dt.datetime
    close_utc: dt.datetime
    is_half_day: bool


@lru_cache(maxsize=1)
def _calendar():  # type: ignore[no-untyped-def]
    return mcal.get_calendar(_CALENDAR_NAME)


def _to_utc(ts: pd.Timestamp) -> dt.datetime:
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC").to_pydatetime()


def _schedule_for(date: dt.date) -> pd.Series | None:
    sched = _calendar().schedule(start_date=date, end_date=date)
    if sched.empty:
        return None
    return sched.iloc[0]


def is_trading_day(date: dt.date) -> bool:
    """True if the equity-index-options market is open on ``date``."""

    return _schedule_for(date) is not None


def session_times(date: dt.date) -> SessionTimes | None:
    """Open/close (UTC) for ``date``, or None on non-trading days."""

    row = _schedule_for(date)
    if row is None:
        return None
    open_utc = _to_utc(row["market_open"])
    close_utc = _to_utc(row["market_close"])
    # Half-day = close earlier than the typical 16:00 ET (= 21:00 UTC EST / 20:00 UTC EDT).
    # Use the close hour in ET to detect.
    et_close = close_utc.astimezone(_eastern())
    is_half = et_close.hour < 16
    return SessionTimes(open_utc=open_utc, close_utc=close_utc, is_half_day=is_half)


def is_half_day(date: dt.date) -> bool:
    s = session_times(date)
    return s is not None and s.is_half_day


def next_session_open_utc(now_utc: dt.datetime) -> dt.datetime:
    """First market open strictly after ``now_utc`` (UTC)."""

    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware (UTC)")

    # Look 14 days ahead to safely cross weekends + holiday clusters.
    start = now_utc.date()
    end = start + dt.timedelta(days=14)
    sched = _calendar().schedule(start_date=start, end_date=end)
    for _, row in sched.iterrows():
        open_utc = _to_utc(row["market_open"])
        if open_utc > now_utc:
            return open_utc
    raise RuntimeError(f"no session found in 14 days after {now_utc.isoformat()}")


def _eastern() -> dt.tzinfo:
    # pandas_market_calendars depends on pytz / zoneinfo; use stdlib zoneinfo.
    from zoneinfo import ZoneInfo

    return ZoneInfo("America/New_York")
