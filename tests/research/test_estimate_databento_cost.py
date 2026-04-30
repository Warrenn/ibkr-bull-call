"""Tests for ``research.scripts.estimate_databento_cost``."""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from research.scripts.estimate_databento_cost import (
    CostQuery,
    _format_results,
    _TIER_1_QUERIES,
    estimate,
    main,
)


def test_tier_1_queries_match_acquisition_decision_doc() -> None:
    """The queries pinned at module level must match Path A in
    ``docs/data-acquisition-decision.md``: SPXW.OPT parent symbol on
    OPRA.PILLAR cbbo-1m, and ES.c.0 continuous on GLBX.MDP3 ohlcv-1m.
    Drift here is silent — the script would still run, just price
    something we did not intend to download.
    """

    assert len(_TIER_1_QUERIES) == 2

    spxw, es = _TIER_1_QUERIES
    assert spxw.dataset == "OPRA.PILLAR"
    assert spxw.schema == "cbbo-1m"
    assert spxw.symbols == ("SPXW.OPT",)
    assert spxw.stype_in == "parent"

    assert es.dataset == "GLBX.MDP3"
    assert es.schema == "ohlcv-1m"
    assert es.symbols == ("ES.c.0",)
    assert es.stype_in == "continuous"


def test_estimate_passes_query_args_through_to_databento_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``estimate`` must forward each ``CostQuery`` field verbatim to
    ``client.metadata.get_cost`` — typos here silently price the wrong
    dataset and burn the free credit on a bad pull.
    """

    monkeypatch.setenv("DATABENTO_API_KEY", "db-test")

    mock_client = MagicMock()
    # Two queries → two return values, in order.
    mock_client.metadata.get_cost.side_effect = [10.5, 2.3]

    with patch("databento.Historical", return_value=mock_client) as ctor:
        results = estimate(
            start=dt.date(2024, 1, 1),
            end=dt.date(2024, 12, 31),
        )

    ctor.assert_called_once_with(key="db-test")
    assert [c for _, c in results] == [10.5, 2.3]

    spxw_call = mock_client.metadata.get_cost.call_args_list[0]
    assert spxw_call.kwargs == {
        "dataset": "OPRA.PILLAR",
        "schema": "cbbo-1m",
        "symbols": ["SPXW.OPT"],
        "stype_in": "parent",
        "start": "2024-01-01",
        "end": "2024-12-31",
    }
    es_call = mock_client.metadata.get_cost.call_args_list[1]
    assert es_call.kwargs == {
        "dataset": "GLBX.MDP3",
        "schema": "ohlcv-1m",
        "symbols": ["ES.c.0"],
        "stype_in": "continuous",
        "start": "2024-01-01",
        "end": "2024-12-31",
    }


def test_estimate_raises_when_api_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DATABENTO_API_KEY"):
        estimate(start=dt.date(2024, 1, 1), end=dt.date(2024, 12, 31))


def test_format_results_within_credit_shows_remaining() -> None:
    q = CostQuery("Test", "DS", "s", ("x",), "raw_symbol")
    out = _format_results(
        [(q, 50.0)],
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 12, 31),
        free_credit=125.0,
    )
    assert "[ok]" in out
    assert "Remaining after pull" in out
    assert "$75.00" in out


def test_format_results_over_credit_shows_overage() -> None:
    q = CostQuery("Test", "DS", "s", ("x",), "raw_symbol")
    out = _format_results(
        [(q, 200.0)],
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 12, 31),
        free_credit=125.0,
    )
    assert "[over budget]" in out
    assert "$75.00" in out


def test_main_rejects_inverted_date_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABENTO_API_KEY", "db-test")
    with pytest.raises(ValueError, match="must be on or before"):
        main(["--start", "2026-01-01", "--end", "2024-01-01"])
