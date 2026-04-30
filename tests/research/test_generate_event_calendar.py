"""Tests for ``research.scripts.generate_event_calendar``."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from research.scripts.generate_event_calendar import (
    _opex_friday_for_month,
    compute_nfp_dates,
    compute_opex_dates,
    generate,
    merge_event_calendar,
)


def test_compute_nfp_dates_returns_first_fridays() -> None:
    """NFP is released on the first Friday of each month at 08:30 ET."""

    dates = compute_nfp_dates(
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 6, 30),
    )
    # First Fridays of Jan-June 2024:
    expected = [
        dt.date(2024, 1, 5),
        dt.date(2024, 2, 2),
        dt.date(2024, 3, 1),
        dt.date(2024, 4, 5),
        dt.date(2024, 5, 3),
        dt.date(2024, 6, 7),
    ]
    assert dates == expected


def test_compute_nfp_dates_handles_first_day_being_friday() -> None:
    """Edge case: month starts on a Friday → that's the first Friday."""

    # 2024-03-01 is a Friday; 2024-11-01 is a Friday.
    dates = compute_nfp_dates(
        start=dt.date(2024, 3, 1),
        end=dt.date(2024, 3, 31),
    )
    assert dates == [dt.date(2024, 3, 1)]


def test_opex_friday_helper_returns_third_friday() -> None:
    """OPEX is the third Friday of each month."""

    # June 2024: Fridays are 7, 14, 21, 28 → third is 21
    assert _opex_friday_for_month(2024, 6) == dt.date(2024, 6, 21)
    # July 2024: Fridays are 5, 12, 19, 26 → third is 19
    assert _opex_friday_for_month(2024, 7) == dt.date(2024, 7, 19)


def test_compute_opex_dates_spans_partial_months_at_boundaries() -> None:
    """A start mid-month should include that month's OPEX only if it's
    after start. Same for end."""

    # Start mid-June (after the 21st) → June OPEX excluded; July OPEX included
    dates = compute_opex_dates(
        start=dt.date(2024, 6, 22),
        end=dt.date(2024, 7, 31),
    )
    assert dates == [dt.date(2024, 7, 19)]


def test_merge_event_calendar_sorts_and_deduplicates_per_date() -> None:
    """If FOMC + CPI fell on the same day, both event_type values would
    appear as separate rows. The output should be sorted by date and
    contain (date, event_type) pairs without duplicates."""

    fomc = [dt.date(2024, 6, 12)]
    cpi = [dt.date(2024, 6, 12)]  # rare overlap
    nfp = [dt.date(2024, 6, 7)]
    opex = [dt.date(2024, 6, 21)]

    df = merge_event_calendar(fomc=fomc, cpi=cpi, nfp=nfp, opex=opex)

    assert list(df.columns) == ["date", "event_type"]
    # 4 rows: NFP, FOMC, CPI, OPEX (sorted by date; FOMC and CPI on
    # same day are two separate rows).
    assert len(df) == 4
    assert list(df["date"]) == [
        dt.date(2024, 6, 7),
        dt.date(2024, 6, 12),
        dt.date(2024, 6, 12),
        dt.date(2024, 6, 21),
    ]


def test_merge_event_calendar_handles_empty_fomc_and_cpi_lists() -> None:
    """v2a uses only computable events (NFP + OPEX). The merge must
    handle empty FOMC + CPI lists without crashing."""

    df = merge_event_calendar(
        fomc=[], cpi=[],
        nfp=[dt.date(2024, 6, 7)],
        opex=[dt.date(2024, 6, 21)],
    )
    assert len(df) == 2
    assert set(df["event_type"]) == {"NFP", "OPEX"}


def test_generate_writes_parquet_with_expected_schema(tmp_path: Path) -> None:
    output = tmp_path / "event_calendar.parquet"
    generate(
        start=dt.date(2024, 6, 1),
        end=dt.date(2024, 6, 30),
        fomc=[dt.date(2024, 6, 12)],
        cpi=[dt.date(2024, 6, 12)],
        output=output,
    )
    df = pd.read_parquet(output)
    assert list(df.columns) == ["date", "event_type"]
    # June 2024: NFP=6/7, FOMC=6/12, CPI=6/12, OPEX=6/21 → 4 rows
    assert len(df) == 4


def test_generate_is_deterministic(tmp_path: Path) -> None:
    """Same inputs → byte-identical parquet → reproducible sha256."""

    import hashlib

    out_a = tmp_path / "a.parquet"
    out_b = tmp_path / "b.parquet"
    args = dict(
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 12, 31),
        fomc=[dt.date(2024, 1, 31), dt.date(2024, 3, 20)],
        cpi=[dt.date(2024, 1, 11), dt.date(2024, 2, 13)],
    )
    generate(output=out_a, **args)
    generate(output=out_b, **args)
    assert (
        hashlib.sha256(out_a.read_bytes()).hexdigest()
        == hashlib.sha256(out_b.read_bytes()).hexdigest()
    )


def test_generate_rejects_inverted_range(tmp_path: Path) -> None:
    output = tmp_path / "out.parquet"
    with pytest.raises(ValueError, match="must be on or before"):
        generate(
            start=dt.date(2024, 6, 30),
            end=dt.date(2024, 6, 1),
            fomc=[],
            cpi=[],
            output=output,
        )
