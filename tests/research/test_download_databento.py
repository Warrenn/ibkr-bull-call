"""Tests for ``research.scripts.download_databento``.

The downloader actually charges the Databento account when run live;
these tests must NOT make a real network call. ``databento.Historical``
is mocked in every test that touches the network surface.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from research.scripts.download_databento import (
    DownloadResult,
    download,
    main,
)


def _make_mock_client(*, cost: float, df_rows: int = 5) -> MagicMock:
    """Mock a Databento ``Historical`` client.

    The DataFrame returned by ``DBNStore.to_df()`` puts the
    ``ts_event`` timestamp on the index, not as a column — the
    download path must promote it before saving. This mock matches
    that real-world shape.
    """

    client = MagicMock()
    client.metadata.get_cost.return_value = cost
    df = pd.DataFrame(
        {
            "open": [100.0] * df_rows,
            "high": [101.0] * df_rows,
            "low": [99.0] * df_rows,
            "close": [100.5] * df_rows,
            "volume": [1000] * df_rows,
            "symbol": ["ES.c.0"] * df_rows,
        },
        index=pd.date_range(
            "2024-01-01", periods=df_rows, freq="1min", tz="UTC",
            name="ts_event",
        ),
    )
    mock_dbn = MagicMock()
    mock_dbn.to_df.return_value = df
    client.timeseries.get_range.return_value = mock_dbn
    return client


def test_download_aborts_when_cost_exceeds_max(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The whole point of ``--max-cost`` is to prevent burning the
    free credit on a misconfigured query. The cost-cap MUST be checked
    before ``timeseries.get_range`` is called; once that fires the
    account is billed.
    """

    monkeypatch.setenv("DATABENTO_API_KEY", "db-test")
    output = tmp_path / "out.parquet"

    client = _make_mock_client(cost=100.0)
    with patch("databento.Historical", return_value=client):
        with pytest.raises(RuntimeError, match="exceeds --max-cost"):
            download(
                dataset="X", schema="s",
                symbols=("a",), stype_in="raw_symbol",
                start=dt.date(2024, 1, 1), end=dt.date(2024, 1, 31),
                output=output, max_cost=10.0,
            )

    client.timeseries.get_range.assert_not_called()
    assert not output.exists()


def test_download_refuses_to_overwrite_existing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Committed dataset artifacts are sha256-pinned in the manifest;
    silently re-writing one would invalidate the pin without surfacing
    the change. Refuse to overwrite — the operator must delete first.
    """

    monkeypatch.setenv("DATABENTO_API_KEY", "db-test")
    output = tmp_path / "out.parquet"
    output.write_bytes(b"existing")

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        download(
            dataset="X", schema="s",
            symbols=("a",), stype_in="raw_symbol",
            start=dt.date(2024, 1, 1), end=dt.date(2024, 1, 31),
            output=output, max_cost=10.0,
        )


def test_download_rejects_inverted_range(tmp_path: Path) -> None:
    output = tmp_path / "out.parquet"
    with pytest.raises(ValueError, match="must be on or before"):
        download(
            dataset="X", schema="s",
            symbols=("a",), stype_in="raw_symbol",
            start=dt.date(2026, 1, 1), end=dt.date(2024, 1, 1),
            output=output, max_cost=10.0,
        )


def test_download_writes_parquet_and_returns_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test")
    output = tmp_path / "subdir" / "out.parquet"

    client = _make_mock_client(cost=2.50, df_rows=5)
    with patch("databento.Historical", return_value=client):
        result = download(
            dataset="GLBX.MDP3", schema="ohlcv-1m",
            symbols=("ES.c.0",), stype_in="continuous",
            start=dt.date(2024, 1, 1), end=dt.date(2024, 1, 31),
            output=output, max_cost=10.0,
        )

    assert isinstance(result, DownloadResult)
    assert result.cost == 2.50
    assert result.row_count == 5
    assert result.output_path == output
    assert output.exists()
    assert len(result.sha256) == 64

    df = pd.read_parquet(output)
    assert len(df) == 5

    expected = hashlib.sha256(output.read_bytes()).hexdigest()
    assert result.sha256 == expected


def test_download_preserves_timestamp_column_in_saved_parquet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Regression: DBN's ``to_df()`` returns the timestamp on the
    index. Saving with ``index=False`` (required for determinism) drops
    it unless we ``reset_index()`` first. Without the timestamp the
    parquet is unusable for time-series joins downstream.
    """

    monkeypatch.setenv("DATABENTO_API_KEY", "db-test")
    output = tmp_path / "out.parquet"

    client = _make_mock_client(cost=1.0, df_rows=5)
    with patch("databento.Historical", return_value=client):
        download(
            dataset="GLBX.MDP3", schema="ohlcv-1m",
            symbols=("ES.c.0",), stype_in="continuous",
            start=dt.date(2024, 1, 1), end=dt.date(2024, 1, 31),
            output=output, max_cost=10.0,
        )

    df = pd.read_parquet(output)
    assert "ts_event" in df.columns, (
        "timestamp column missing — DBN puts ts_event on the index; "
        "download must reset_index() before saving"
    )
    assert len(df) == 5


def test_download_forwards_query_args_to_get_range(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test")
    output = tmp_path / "out.parquet"

    client = _make_mock_client(cost=1.0)
    with patch("databento.Historical", return_value=client):
        download(
            dataset="GLBX.MDP3", schema="ohlcv-1m",
            symbols=("ES.c.0",), stype_in="continuous",
            start=dt.date(2024, 1, 1), end=dt.date(2024, 1, 31),
            output=output, max_cost=10.0,
        )

    call = client.timeseries.get_range.call_args
    assert call.kwargs == {
        "dataset": "GLBX.MDP3",
        "schema": "ohlcv-1m",
        "symbols": ["ES.c.0"],
        "stype_in": "continuous",
        "start": "2024-01-01",
        "end": "2024-01-31",
    }


def test_download_raises_when_api_key_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    output = tmp_path / "out.parquet"
    with pytest.raises(RuntimeError, match="DATABENTO_API_KEY"):
        download(
            dataset="X", schema="s",
            symbols=("a",), stype_in="raw_symbol",
            start=dt.date(2024, 1, 1), end=dt.date(2024, 1, 31),
            output=output, max_cost=10.0,
        )


def test_main_rejects_inverted_range(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test")
    output = tmp_path / "out.parquet"
    with pytest.raises(ValueError, match="must be on or before"):
        main([
            "--dataset", "X", "--schema", "s",
            "--symbols", "a", "--stype-in", "raw_symbol",
            "--start", "2026-01-01", "--end", "2024-01-01",
            "--output", str(output), "--max-cost", "10",
        ])
