"""Tests for ``research.scripts.run_directional_edge_v1``.

Phase 1 falsification test logic. Every numeric assertion uses
synthetic data where the answer is known by inspection — no real
ES bars are touched.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from research.scripts.run_directional_edge_v1 import (
    DirectionalEdgeMetrics,
    _load_bars_parquet,
    aggregate_metrics,
    compute_ledger,
    extract_intraday_prices,
)


def _make_calendar(
    dates: list[dt.date],
    half_days: list[dt.date] | None = None,
) -> pd.DataFrame:
    half_set = set(half_days or [])
    return pd.DataFrame({
        "date": dates,
        "is_trading_day": [True] * len(dates),
        "is_half_day": [d in half_set for d in dates],
        "session_open_utc": pd.NaT,
        "session_close_utc": pd.NaT,
    })


def _make_bars(rows: list[tuple[str, float]]) -> pd.DataFrame:
    """``rows`` = list of (UTC-ISO string, open price). Other OHLCV is filler
    — only ``ts_utc`` and ``open`` matter for these tests."""

    return pd.DataFrame({
        "ts_utc": pd.to_datetime([r[0] for r in rows], utc=True),
        "open": [r[1] for r in rows],
        "high": [r[1] + 0.1 for r in rows],
        "low": [r[1] - 0.1 for r in rows],
        "close": [r[1] for r in rows],
        "volume": [100] * len(rows),
        "symbol": ["ES.c.0"] * len(rows),
    })


def test_extract_intraday_prices_picks_three_bars_per_trading_day() -> None:
    """For each NYSE full-trading day, the runner picks the OPEN of the
    1-min bars at 09:30, 10:30, and 15:55 ET. EDT in June = UTC-4, so
    09:30 ET = 13:30 UTC, 10:30 ET = 14:30 UTC, 15:55 ET = 19:55 UTC.
    """

    bars = _make_bars([
        ("2024-06-03 13:30+00:00", 100.0),
        ("2024-06-03 14:30+00:00", 101.0),
        ("2024-06-03 19:55+00:00", 102.0),
        ("2024-06-04 13:30+00:00", 200.0),
        ("2024-06-04 14:30+00:00", 201.0),
        ("2024-06-04 19:55+00:00", 202.0),
    ])
    cal = _make_calendar([dt.date(2024, 6, 3), dt.date(2024, 6, 4)])

    result = extract_intraday_prices(bars=bars, calendar=cal)

    assert len(result) == 2
    assert list(result["date"]) == [dt.date(2024, 6, 3), dt.date(2024, 6, 4)]
    assert result["session_open_price"].tolist() == [100.0, 200.0]
    assert result["signal_price"].tolist() == [101.0, 201.0]
    assert result["eow_price"].tolist() == [102.0, 202.0]


def test_extract_intraday_prices_excludes_half_days() -> None:
    """NYSE half-days close at 13:00 ET — 15:55 ET is past close so the
    end-of-window price doesn't exist. Skip these days entirely."""

    bars = _make_bars([
        ("2024-06-03 13:30+00:00", 100.0),
        ("2024-06-03 14:30+00:00", 101.0),
        ("2024-06-03 19:55+00:00", 102.0),
    ])
    cal = _make_calendar(
        [dt.date(2024, 6, 3)],
        half_days=[dt.date(2024, 6, 3)],
    )

    result = extract_intraday_prices(bars=bars, calendar=cal)
    assert len(result) == 0


def test_extract_intraday_prices_drops_days_with_missing_target_bar() -> None:
    """If a day is missing any of the three target bars (e.g. data gap),
    drop it from the analysis rather than imputing or extrapolating."""

    bars = _make_bars([
        ("2024-06-03 13:30+00:00", 100.0),
        ("2024-06-03 14:30+00:00", 101.0),
        # missing 15:55
    ])
    cal = _make_calendar([dt.date(2024, 6, 3)])

    result = extract_intraday_prices(bars=bars, calendar=cal)
    assert len(result) == 0


def test_extract_intraday_prices_handles_winter_dst_offset() -> None:
    """In winter (EST = UTC-5), 09:30 ET = 14:30 UTC, 10:30 ET = 15:30 UTC,
    15:55 ET = 20:55 UTC. The tz_convert must adjust automatically."""

    bars = _make_bars([
        ("2024-12-03 14:30+00:00", 100.0),  # 09:30 EST
        ("2024-12-03 15:30+00:00", 101.0),  # 10:30 EST
        ("2024-12-03 20:55+00:00", 102.0),  # 15:55 EST
    ])
    cal = _make_calendar([dt.date(2024, 12, 3)])

    result = extract_intraday_prices(bars=bars, calendar=cal)
    assert len(result) == 1
    assert result["signal_price"].iloc[0] == 101.0


def test_compute_ledger_marks_entered_correctly() -> None:
    """Confirmation return >= threshold → entered. Below → skip with
    skip_reason='no_signal'."""

    prices = pd.DataFrame({
        "date": [dt.date(2024, 6, 3), dt.date(2024, 6, 4), dt.date(2024, 6, 5)],
        "session_open_price": [100.0, 100.0, 100.0],
        "signal_price": [100.5, 100.1, 101.0],  # +0.5%, +0.1%, +1%
        "eow_price": [101.0, 100.5, 99.0],
    })

    ledger = compute_ledger(prices_per_day=prices, signal_threshold=0.0025)

    assert ledger["entered"].tolist() == [True, False, True]
    assert ledger.loc[~ledger["entered"], "skip_reason"].iloc[0] == "no_signal"

    assert ledger["confirmation_return"].tolist() == pytest.approx(
        [0.005, 0.001, 0.01], rel=1e-9,
    )
    fr = ledger["forward_return"].tolist()
    assert fr[0] == pytest.approx((101.0 / 100.5) - 1, rel=1e-9)
    assert pd.isna(fr[1])
    assert fr[2] == pytest.approx((99.0 / 101.0) - 1, rel=1e-9)


def test_aggregate_metrics_computes_mean_median_hit_rate_and_left_tail() -> None:
    ledger = pd.DataFrame({
        "date": [dt.date(2024, 6, d) for d in [3, 4, 5, 6]],
        "entered": [True, False, True, True],
        "forward_return": [0.01, float("nan"), -0.02, 0.005],
        "skip_reason": ["", "no_signal", "", ""],
    })

    m = aggregate_metrics(ledger)

    assert m.trade_count == 3
    assert m.entered_count == 3
    assert m.skipped_count == 1
    assert m.total_sessions == 4
    # mean of [0.01, -0.02, 0.005] = -0.00166...
    assert m.mean_forward_return == pytest.approx(-0.0016667, abs=1e-6)
    # median of those three = 0.005
    assert m.median_forward_return == pytest.approx(0.005, abs=1e-6)
    # 2 of 3 are positive
    assert m.hit_rate == pytest.approx(2 / 3, abs=1e-6)


def test_aggregate_metrics_verdict_edge_present_on_positive_mean() -> None:
    ledger = pd.DataFrame({
        "entered": [True, True],
        "forward_return": [0.01, 0.005],
        "skip_reason": ["", ""],
    })
    m = aggregate_metrics(ledger)
    assert m.verdict == "EDGE_PRESENT"


def test_aggregate_metrics_verdict_no_edge_on_zero_or_negative_mean() -> None:
    ledger = pd.DataFrame({
        "entered": [True, True],
        "forward_return": [-0.01, -0.005],
        "skip_reason": ["", ""],
    })
    m = aggregate_metrics(ledger)
    assert m.verdict == "NO_EDGE"


def test_aggregate_metrics_raises_when_no_signals_fired() -> None:
    """If signal_threshold is too tight and no day fires, surface a
    RuntimeError rather than crashing on an empty mean()."""

    ledger = pd.DataFrame({
        "entered": [False, False, False],
        "forward_return": [float("nan")] * 3,
        "skip_reason": ["no_signal"] * 3,
    })
    with pytest.raises(RuntimeError, match="no signals fired"):
        aggregate_metrics(ledger)


def test_load_bars_parquet_renames_ts_event_to_ts_utc(tmp_path: Path) -> None:
    """Databento writes ``ts_event``; IBKR writes ``ts_utc``. The loader
    canonicalizes to ``ts_utc`` so downstream code doesn't branch."""

    path = tmp_path / "es.parquet"
    df = pd.DataFrame({
        "ts_event": pd.to_datetime(
            ["2024-06-03 13:30:00+00:00"], utc=True,
        ),
        "open": [100.0],
    })
    df.to_parquet(path, engine="pyarrow", compression="snappy", index=False)

    loaded = _load_bars_parquet(path)
    assert "ts_utc" in loaded.columns
    assert "ts_event" not in loaded.columns


def test_load_bars_parquet_passes_through_ts_utc_unchanged(
    tmp_path: Path,
) -> None:
    path = tmp_path / "spx.parquet"
    df = pd.DataFrame({
        "ts_utc": pd.to_datetime(
            ["2024-06-03 13:30:00+00:00"], utc=True,
        ),
        "open": [100.0],
    })
    df.to_parquet(path, engine="pyarrow", compression="snappy", index=False)

    loaded = _load_bars_parquet(path)
    assert "ts_utc" in loaded.columns


def test_full_pipeline_produces_expected_metrics() -> None:
    """End-to-end smoke: 3 trading days, 2 fire the signal, known answer."""

    bars = _make_bars([
        # Day 1: open 100 → 10:30 = 100.5 (+0.5%, entered) → 15:55 = 101.0 (+0.498%)
        ("2024-06-03 13:30+00:00", 100.0),
        ("2024-06-03 14:30+00:00", 100.5),
        ("2024-06-03 19:55+00:00", 101.0),
        # Day 2: open 100 → 10:30 = 100.1 (+0.1%, NOT entered)
        ("2024-06-04 13:30+00:00", 100.0),
        ("2024-06-04 14:30+00:00", 100.1),
        ("2024-06-04 19:55+00:00", 100.2),
        # Day 3: open 100 → 10:30 = 101.0 (+1%, entered) → 15:55 = 99.0 (-1.98%)
        ("2024-06-05 13:30+00:00", 100.0),
        ("2024-06-05 14:30+00:00", 101.0),
        ("2024-06-05 19:55+00:00", 99.0),
    ])
    cal = _make_calendar([
        dt.date(2024, 6, 3), dt.date(2024, 6, 4), dt.date(2024, 6, 5),
    ])

    prices = extract_intraday_prices(bars=bars, calendar=cal)
    ledger = compute_ledger(prices_per_day=prices, signal_threshold=0.0025)
    metrics = aggregate_metrics(ledger)

    assert metrics.trade_count == 2
    assert metrics.skipped_count == 1
    # forward returns of entered days: [(101.0/100.5)-1, (99.0/101.0)-1]
    expected_mean = ((101.0 / 100.5 - 1) + (99.0 / 101.0 - 1)) / 2
    assert metrics.mean_forward_return == pytest.approx(expected_mean, rel=1e-6)
    assert metrics.verdict == "NO_EDGE"  # mean < 0
    assert metrics.hit_rate == pytest.approx(0.5, abs=1e-6)


def test_dataclass_metrics_is_immutable() -> None:
    """Frozen dataclass — defensive against accidental mutation in
    downstream code that hands the metrics object around."""

    m = DirectionalEdgeMetrics(
        trade_count=1, entered_count=1, skipped_count=0, total_sessions=1,
        mean_forward_return=0.0, median_forward_return=0.0,
        hit_rate=0.0, left_tail_p05=0.0, verdict="NO_EDGE",
    )
    with pytest.raises(Exception):  # FrozenInstanceError
        m.trade_count = 2  # type: ignore[misc]
