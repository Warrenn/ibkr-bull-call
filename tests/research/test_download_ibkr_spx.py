"""Tests for ``research.scripts.download_ibkr_spx``.

The scraper holds a long IBKR session open and paces requests to the
60-req / 10-min rolling rate limit. Tests must NOT make real API
calls; ``IbkrClient`` is fully mocked.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest

from research.scripts.download_ibkr_spx import (
    _bars_to_df,
    scrape,
)


def _make_bars(start_ts: pd.Timestamp, n: int) -> list[dict[str, Any]]:
    """One bar per minute starting at ``start_ts``, prices monotonic so dedupe
    tests can spot rows by index."""

    return [
        {
            "t": int((start_ts + pd.Timedelta(minutes=i)).timestamp() * 1000),
            "o": 100.0 + i,
            "h": 100.5 + i,
            "l": 99.5 + i,
            "c": 100.2 + i,
            "v": 1000 + i,
        }
        for i in range(n)
    ]


def _make_client_with_chunks(
    chunks: list[list[dict[str, Any]]],
) -> MagicMock:
    """Mock client that returns each chunk in order, then empty (terminates the loop)."""

    client = MagicMock()
    search_resp = MagicMock()
    search_resp.data = [{"conid": 416904}]  # SPX conid (illustrative)
    client.search_contract_by_symbol.return_value = search_resp

    responses: list[MagicMock] = []
    for chunk in [*chunks, []]:
        r = MagicMock()
        r.data = {"data": chunk}
        responses.append(r)
    client.marketdata_history_by_conid.side_effect = responses
    return client


def test_bars_to_df_handles_empty_response() -> None:
    df = _bars_to_df([])
    assert len(df) == 0
    assert "ts_utc" in df.columns


def test_bars_to_df_renames_ibkr_columns_to_clean_names() -> None:
    """IBKR returns t/o/h/l/c/v; we rename to ts_utc/open/high/low/close/volume."""

    bars = _make_bars(pd.Timestamp("2024-01-01", tz="UTC"), 3)
    df = _bars_to_df(bars)
    assert list(df.columns) == [
        "ts_utc", "open", "high", "low", "close", "volume",
    ]
    assert len(df) == 3
    # tz-aware UTC datetime; resolution may be ms or ns depending on pandas
    # version — the property we care about is "monotonic UTC timestamps",
    # not the unit precision.
    assert str(df["ts_utc"].dtype).endswith("UTC]")


def test_scrape_walks_backward_in_chunks(tmp_path: Path) -> None:
    output = tmp_path / "out.parquet"
    chunks = [
        _make_bars(pd.Timestamp("2024-01-15", tz="UTC"), 100),
        _make_bars(pd.Timestamp("2024-01-01", tz="UTC"), 100),
    ]
    client = _make_client_with_chunks(chunks)

    result = scrape(
        client=client,
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 1, 31),
        output=output,
        sleep_fn=lambda _x: None,
    )

    assert result.row_count == 200
    assert output.exists()
    assert client.marketdata_history_by_conid.call_count >= 2


def test_scrape_stops_on_empty_response_depth_exhausted(
    tmp_path: Path,
) -> None:
    """An empty response means IBKR has no further history; stop walking
    back rather than spinning forever."""

    output = tmp_path / "out.parquet"
    chunks = [_make_bars(pd.Timestamp("2024-01-15", tz="UTC"), 50)]
    client = _make_client_with_chunks(chunks)

    result = scrape(
        client=client,
        start=dt.date(2020, 1, 1),  # asking for 4 yr
        end=dt.date(2024, 1, 31),
        output=output,
        sleep_fn=lambda _x: None,
    )

    assert result.row_count == 50


def test_scrape_paces_requests_between_calls_not_before_first(
    tmp_path: Path,
) -> None:
    """Pacing protects against the 60-req / 10-min IBKR limit. Sleep
    happens between requests, NOT before the first one (would just
    add latency)."""

    output = tmp_path / "out.parquet"
    chunks = [
        _make_bars(pd.Timestamp("2024-01-15", tz="UTC"), 50),
        _make_bars(pd.Timestamp("2024-01-01", tz="UTC"), 50),
    ]
    client = _make_client_with_chunks(chunks)
    sleep_calls: list[float] = []

    scrape(
        client=client,
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 1, 31),
        output=output,
        pace_sec=2.5,
        sleep_fn=lambda s: sleep_calls.append(s),
    )

    # 2 chunks consumed → cursor walks past --start before the empty
    # terminator is reached, so only 1 inter-request sleep fires.
    # Generally: N chunks consumed = N-1 sleeps.
    assert sleep_calls == [2.5]


def test_scrape_dedupes_overlapping_bars(tmp_path: Path) -> None:
    """Adjacent IBKR chunks can overlap by a minute when the cursor walk
    doesn't perfectly align with bar boundaries; sort+dedupe on ts_utc
    keeps the output clean."""

    output = tmp_path / "out.parquet"
    overlap_ts = pd.Timestamp("2024-01-10", tz="UTC")
    chunks = [
        _make_bars(overlap_ts, 100),
        _make_bars(overlap_ts, 100),  # exact duplicates
    ]
    client = _make_client_with_chunks(chunks)

    result = scrape(
        client=client,
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 1, 31),
        output=output,
        sleep_fn=lambda _x: None,
    )

    assert result.row_count == 100


def test_scrape_refuses_to_overwrite_existing_output(
    tmp_path: Path,
) -> None:
    output = tmp_path / "out.parquet"
    output.write_bytes(b"existing")

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        scrape(
            client=MagicMock(),
            start=dt.date(2024, 1, 1),
            end=dt.date(2024, 1, 31),
            output=output,
            sleep_fn=lambda _x: None,
        )


def test_scrape_rejects_inverted_range(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be on or before"):
        scrape(
            client=MagicMock(),
            start=dt.date(2026, 1, 1),
            end=dt.date(2024, 1, 1),
            output=tmp_path / "out.parquet",
            sleep_fn=lambda _x: None,
        )


def test_scrape_raises_when_zero_bars_returned(tmp_path: Path) -> None:
    """Empty response on the very first request — IBKR gateway returned
    nothing. Surface as RuntimeError rather than write an empty parquet."""

    output = tmp_path / "out.parquet"
    client = _make_client_with_chunks([])  # immediately empty

    with pytest.raises(RuntimeError, match="no data retrieved"):
        scrape(
            client=client,
            start=dt.date(2024, 1, 1),
            end=dt.date(2024, 1, 31),
            output=output,
            sleep_fn=lambda _x: None,
        )


def test_scrape_output_parquet_has_expected_schema(
    tmp_path: Path,
) -> None:
    """The committed parquet must round-trip with the schema the
    manifest documents (ts_utc + ohlcv) so downstream code can join
    without inspecting nominal vs index."""

    output = tmp_path / "out.parquet"
    chunks = [_make_bars(pd.Timestamp("2024-01-15", tz="UTC"), 10)]
    client = _make_client_with_chunks(chunks)

    scrape(
        client=client,
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 1, 31),
        output=output,
        sleep_fn=lambda _x: None,
    )

    df = pd.read_parquet(output)
    assert list(df.columns) == [
        "ts_utc", "open", "high", "low", "close", "volume",
    ]
    assert df["ts_utc"].is_monotonic_increasing
