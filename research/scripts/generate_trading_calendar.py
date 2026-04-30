"""Generate the dataset-v1 trading calendar from ``pandas_market_calendars``.

This is a one-shot research utility — the artifact it produces lives in
``research/data/dataset-v1/trading_calendar.parquet`` and is committed to the
repo so every backtest run pins the same calendar version. Re-running the
generator on the same inputs MUST produce a byte-identical Parquet file
(see ``test_generate_is_deterministic``); the manifest's ``checksum`` field
assumes that.

Usage:

    uv run python -m research.scripts.generate_trading_calendar \\
        --start 2022-01-01 --end 2026-04-30 \\
        --output research/data/dataset-v1/trading_calendar.parquet

The output schema matches the manifest entry's ``schema`` field exactly:

    date              (date)        — calendar day, ISO YYYY-MM-DD
    is_trading_day    (bool)        — NYSE session open at all that day
    is_half_day       (bool)        — NYSE early-close session (1 pm ET)
    session_open_utc  (Timestamp?)  — UTC tz-aware; None on non-trading days
    session_close_utc (Timestamp?)  — UTC tz-aware; None on non-trading days

NYSE half-days (1 pm ET close) are detected by comparing each session's
``market_close`` against the regular 4 pm ET close — anything earlier is a
half-day. This matches ``bull_call.calendar.session_times`` semantics so the
research data lines up with the bot's runtime calendar.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import sys
from pathlib import Path

import pandas as pd
import pandas_market_calendars as mcal


_SCHEMA_COLUMNS = (
    "date",
    "is_trading_day",
    "is_half_day",
    "session_open_utc",
    "session_close_utc",
)


def generate(
    *,
    start: dt.date,
    end: dt.date,
    output: Path,
) -> Path:
    """Write a Parquet calendar covering ``[start, end]`` inclusive.

    Returns the output path for convenience. Raises ``ValueError`` if
    ``start > end``.
    """

    if start > end:
        raise ValueError(
            f"start ({start}) must be on or before end ({end}); "
            "got an inverted range",
        )

    nyse = mcal.get_calendar("NYSE")
    schedule = nyse.schedule(start_date=start, end_date=end)
    # Schedule index is the trading-day Timestamp; columns include
    # ``market_open`` and ``market_close`` as tz-aware UTC Timestamps.
    by_trading_date: dict[dt.date, tuple[pd.Timestamp, pd.Timestamp]] = {
        idx.date(): (row["market_open"], row["market_close"])
        for idx, row in schedule.iterrows()
    }

    # Walk every calendar date from start to end (inclusive) so weekends and
    # holidays appear as is_trading_day=False rows. The MVB program doc's
    # schema requires presence-per-day so downstream filters can ``join`` on
    # date without reasoning about absence.
    rows: list[dict[str, object]] = []
    current = start
    one_day = dt.timedelta(days=1)
    while current <= end:
        sched = by_trading_date.get(current)
        if sched is None:
            rows.append({
                "date": current,
                "is_trading_day": False,
                "is_half_day": False,
                "session_open_utc": pd.NaT,
                "session_close_utc": pd.NaT,
            })
        else:
            open_utc, close_utc = sched
            # NYSE regular close is 4 pm ET. A session that closes earlier
            # than 4 pm ET (1 pm ET = 17:00 UTC during DST, 18:00 UTC EST)
            # is a half-day. Comparing the close's wall-clock hour in ET
            # is the unambiguous test (avoids DST aliasing).
            close_et_hour = close_utc.tz_convert("America/New_York").hour
            is_half_day = close_et_hour < 16
            rows.append({
                "date": current,
                "is_trading_day": True,
                "is_half_day": is_half_day,
                "session_open_utc": open_utc,
                "session_close_utc": close_utc,
            })
        current += one_day

    df = pd.DataFrame(rows, columns=list(_SCHEMA_COLUMNS))
    # Force tz-aware UTC dtype so re-reads from Parquet round-trip cleanly.
    df["session_open_utc"] = pd.to_datetime(df["session_open_utc"], utc=True)
    df["session_close_utc"] = pd.to_datetime(df["session_close_utc"], utc=True)
    # Sort by date for deterministic output (the schedule index is already
    # sorted, but the missing-day fill above relies on the while-loop
    # ordering — be explicit).
    df = df.sort_values("date").reset_index(drop=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    # ``pyarrow`` is the parquet backend (added to dev deps). Compression
    # default is snappy; explicit so the SHA256 doesn't drift if pandas
    # changes its default later.
    df.to_parquet(output, engine="pyarrow", compression="snappy", index=False)
    return output


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="generate_trading_calendar")
    p.add_argument("--start", type=dt.date.fromisoformat, required=True,
                   help="Inclusive start date, ISO YYYY-MM-DD")
    p.add_argument("--end", type=dt.date.fromisoformat, required=True,
                   help="Inclusive end date, ISO YYYY-MM-DD")
    p.add_argument("--output", type=Path, required=True,
                   help="Output Parquet path")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    output = generate(start=args.start, end=args.end, output=args.output)
    digest = _sha256_of(output)
    rows = len(pd.read_parquet(output))
    print(f"wrote {rows} rows -> {output}")
    print(f"sha256: {digest}")
    print(f"date_range: {args.start.isoformat()} .. {args.end.isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
