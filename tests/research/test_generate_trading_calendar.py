"""Tests for the dataset-v1 trading calendar generator.

The generator wraps ``pandas_market_calendars`` (NYSE) into a deterministic
Parquet artifact for the research dataset. We don't re-test the calendar
library itself — pandas_market_calendars is well-tested upstream — only:

1. The output schema matches the manifest contract.
2. Known-correct dates land on the right side of the trading-day /
   half-day flags.
3. Re-running the generator on the same inputs produces a
   byte-identical file (manifest's checksum field assumes this).
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import pandas as pd
import pytest

from research.scripts.generate_trading_calendar import generate


_EXPECTED_COLUMNS = (
    "date",
    "is_trading_day",
    "is_half_day",
    "session_open_utc",
    "session_close_utc",
)


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def test_generate_writes_parquet_with_expected_schema(tmp_path: Path) -> None:
    """Output file exists, columns match the manifest contract."""

    out = tmp_path / "trading_calendar.parquet"
    generate(
        start=dt.date(2025, 12, 1),
        end=dt.date(2025, 12, 31),
        output=out,
    )

    assert out.exists(), "generator did not write the output file"
    df = pd.read_parquet(out)
    assert tuple(df.columns) == _EXPECTED_COLUMNS, (
        f"schema drift — expected {_EXPECTED_COLUMNS}, got {tuple(df.columns)}"
    )
    # All December 2025 calendar dates (weekends + weekdays) are present.
    assert len(df) == 31, f"expected 31 December rows, got {len(df)}"


def test_generate_marks_known_dates_correctly(tmp_path: Path) -> None:
    """Spot-check that the calendar flags actually agree with reality on
    a handful of canonical dates (Christmas, Christmas Eve half-day,
    weekends, ordinary weekday). If any of these flip, the upstream
    calendar package is producing different data than we expect and we
    need to know before downstream phases consume the file."""

    out = tmp_path / "trading_calendar.parquet"
    generate(
        start=dt.date(2025, 12, 20),
        end=dt.date(2025, 12, 31),
        output=out,
    )
    df = pd.read_parquet(out)
    by_date = {row["date"]: row for _, row in df.iterrows()}

    # Christmas Day — closed.
    christmas = by_date[dt.date(2025, 12, 25)]
    assert christmas["is_trading_day"] is False
    assert christmas["is_half_day"] is False

    # Christmas Eve 2025 — NYSE early close (1:00 pm ET).
    eve = by_date[dt.date(2025, 12, 24)]
    assert eve["is_trading_day"] is True
    assert eve["is_half_day"] is True

    # Saturday — closed (not a trading day, not a half-day).
    saturday = by_date[dt.date(2025, 12, 27)]
    assert saturday["is_trading_day"] is False
    assert saturday["is_half_day"] is False

    # Ordinary weekday — Monday Dec 22 2025.
    monday = by_date[dt.date(2025, 12, 22)]
    assert monday["is_trading_day"] is True
    assert monday["is_half_day"] is False
    # Session times must be present and tz-aware UTC for trading days.
    assert pd.notna(monday["session_open_utc"])
    assert pd.notna(monday["session_close_utc"])
    assert monday["session_open_utc"] < monday["session_close_utc"]


def test_generate_is_deterministic(tmp_path: Path) -> None:
    """Re-running the generator with identical inputs must produce a
    byte-identical Parquet file. The manifest's ``checksum`` field
    presumes this — without determinism every commit would invalidate
    the prior holdout under §1.5.4 spec-freeze."""

    out_a = tmp_path / "a.parquet"
    out_b = tmp_path / "b.parquet"
    args = dict(
        start=dt.date(2025, 1, 1),
        end=dt.date(2025, 3, 31),
    )
    generate(output=out_a, **args)
    generate(output=out_b, **args)

    assert _sha256_of(out_a) == _sha256_of(out_b), (
        "non-deterministic Parquet output — re-running the generator "
        "produced different bytes; manifest checksum would drift on every "
        "regeneration"
    )


def test_generate_rejects_inverted_range(tmp_path: Path) -> None:
    """Sanity guard — start must be on or before end."""

    out = tmp_path / "x.parquet"
    with pytest.raises(ValueError, match="start"):
        generate(
            start=dt.date(2026, 4, 30),
            end=dt.date(2026, 1, 1),
            output=out,
        )
