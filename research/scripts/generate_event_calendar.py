"""Generate ``research/data/dataset-v1/event_calendar.parquet`` from
event date inputs.

Two classes of event are handled:

1. **Computable** events with deterministic schedules:

   - **NFP** (Non-Farm Payrolls): first Friday of each month, 08:30 ET.
   - **OPEX** (monthly equity options expiry): third Friday of each
     month, 16:00 ET (for SPX it's the morning settlement on that
     Friday — the AM-settled SET is what we'd actually trade against
     if we had Friday 0DTE positions, but for "exclude this day from
     entry" purposes the date itself is what matters).

2. **Released-on-published-schedule** events that are not algorithmically
   determinable from the calendar — must be supplied as input lists:

   - **FOMC** rate decisions: ~8 per year per Fed schedule.
   - **CPI** releases: 12 per year per BLS schedule (date drifts
     month-to-month, never the same weekday).

Schema of the output parquet:

- ``date`` (date, sorted ascending)
- ``event_type`` (str: NFP | OPEX | FOMC | CPI)

Same date can appear with multiple event_type rows (e.g. an FOMC
meeting that happens to coincide with a CPI release — rare but
possible).

Usage::

    uv run python -m research.scripts.generate_event_calendar \\
        --start 2023-04-30 --end 2026-04-30 \\
        --fomc-csv research/data/dataset-v1/fomc_dates.csv \\
        --cpi-csv research/data/dataset-v1/cpi_dates.csv \\
        --output research/data/dataset-v1/event_calendar.parquet
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import sys
from pathlib import Path

import pandas as pd


_FRIDAY = 4  # Monday=0, Sunday=6


def compute_nfp_dates(*, start: dt.date, end: dt.date) -> list[dt.date]:
    """First Friday of each month in ``[start, end]``."""

    out: list[dt.date] = []
    cursor = dt.date(start.year, start.month, 1)
    while cursor <= end:
        # First Friday of cursor's month
        offset = (_FRIDAY - cursor.weekday()) % 7
        first_friday = cursor + dt.timedelta(days=offset)
        if start <= first_friday <= end:
            out.append(first_friday)
        # Advance to next month
        if cursor.month == 12:
            cursor = dt.date(cursor.year + 1, 1, 1)
        else:
            cursor = dt.date(cursor.year, cursor.month + 1, 1)
    return out


def _opex_friday_for_month(year: int, month: int) -> dt.date:
    """Third Friday of the given month."""

    first_of_month = dt.date(year, month, 1)
    offset = (_FRIDAY - first_of_month.weekday()) % 7
    first_friday = first_of_month + dt.timedelta(days=offset)
    return first_friday + dt.timedelta(weeks=2)


def compute_opex_dates(*, start: dt.date, end: dt.date) -> list[dt.date]:
    """Third Friday of each month in ``[start, end]``."""

    out: list[dt.date] = []
    cursor = dt.date(start.year, start.month, 1)
    while cursor <= end:
        opex = _opex_friday_for_month(cursor.year, cursor.month)
        if start <= opex <= end:
            out.append(opex)
        if cursor.month == 12:
            cursor = dt.date(cursor.year + 1, 1, 1)
        else:
            cursor = dt.date(cursor.year, cursor.month + 1, 1)
    return out


def merge_event_calendar(
    *,
    fomc: list[dt.date],
    cpi: list[dt.date],
    nfp: list[dt.date],
    opex: list[dt.date],
) -> pd.DataFrame:
    """Merge all four event lists into a single sorted DataFrame.

    Columns: ``date``, ``event_type``. Same-date overlaps produce
    multiple rows (one per event_type) so a downstream "is this date
    an event?" filter can use ``date in set(events.date)``.
    """

    rows: list[dict[str, object]] = []
    for d in fomc:
        rows.append({"date": d, "event_type": "FOMC"})
    for d in cpi:
        rows.append({"date": d, "event_type": "CPI"})
    for d in nfp:
        rows.append({"date": d, "event_type": "NFP"})
    for d in opex:
        rows.append({"date": d, "event_type": "OPEX"})

    df = pd.DataFrame(rows, columns=["date", "event_type"])
    if len(df) == 0:
        return df
    return df.sort_values(
        ["date", "event_type"]
    ).reset_index(drop=True)


def generate(
    *,
    start: dt.date,
    end: dt.date,
    fomc: list[dt.date],
    cpi: list[dt.date],
    output: Path,
) -> Path:
    """Write the event calendar parquet covering ``[start, end]``.

    NFP + OPEX are computed from the calendar; FOMC + CPI are taken
    from the supplied lists (filtered to the range here so the caller
    can pass a superset).
    """

    if start > end:
        raise ValueError(
            f"start ({start}) must be on or before end ({end})",
        )

    fomc_in_range = sorted(d for d in fomc if start <= d <= end)
    cpi_in_range = sorted(d for d in cpi if start <= d <= end)
    nfp = compute_nfp_dates(start=start, end=end)
    opex = compute_opex_dates(start=start, end=end)

    df = merge_event_calendar(
        fomc=fomc_in_range, cpi=cpi_in_range, nfp=nfp, opex=opex,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output, engine="pyarrow", compression="snappy", index=False)
    return output


def _read_dates_csv(path: Path) -> list[dt.date]:
    """Read a one-column CSV of ISO dates (header optional)."""

    df = pd.read_csv(path, header=None)
    # If first row is a header (non-parseable as date), drop it.
    try:
        dt.date.fromisoformat(str(df.iloc[0, 0]).strip())
    except ValueError:
        df = df.iloc[1:]
    return sorted(
        dt.date.fromisoformat(str(v).strip())
        for v in df.iloc[:, 0]
        if str(v).strip() and str(v).strip().lower() != "nan"
    )


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="generate_event_calendar")
    p.add_argument("--start", type=dt.date.fromisoformat, required=True)
    p.add_argument("--end", type=dt.date.fromisoformat, required=True)
    p.add_argument(
        "--fomc-csv", type=Path, default=None,
        help="One-column CSV of FOMC announcement dates (ISO YYYY-MM-DD)",
    )
    p.add_argument(
        "--cpi-csv", type=Path, default=None,
        help="One-column CSV of CPI release dates (ISO YYYY-MM-DD)",
    )
    p.add_argument("--output", type=Path, required=True)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    fomc = _read_dates_csv(args.fomc_csv) if args.fomc_csv else []
    cpi = _read_dates_csv(args.cpi_csv) if args.cpi_csv else []
    output = generate(
        start=args.start, end=args.end,
        fomc=fomc, cpi=cpi, output=args.output,
    )
    digest = _sha256_of(output)
    df = pd.read_parquet(output)
    counts = df.groupby("event_type").size().to_dict()
    print(f"wrote {len(df)} rows -> {output}")
    print(f"sha256: {digest}")
    print(f"event_type counts: {counts}")
    print(f"date_range: {args.start.isoformat()} .. {args.end.isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
