"""Tests for the cpapi.execution helpers that don't require a live gateway."""

from __future__ import annotations

import inspect

import pytest

from bull_call.cpapi.execution import _MIN_PROFIT_PER_SHARE, _safe_debit_max, submit_entry_lmt


# ---------- width-only ceiling (no ratio configured) -----------------------


def test_passthrough_when_request_is_safe() -> None:
    # 5-wide, requested 4.75, width-ceiling = 4.95 -> passthrough
    assert _safe_debit_max(4.75, long_strike=4995.0, short_strike=5000.0) == 4.75


def test_clamps_when_request_is_above_width() -> None:
    safe = _safe_debit_max(5.05, long_strike=4995.0, short_strike=5000.0)
    assert safe == pytest.approx(4.95)


def test_clamps_when_request_is_at_width() -> None:
    safe = _safe_debit_max(5.0, long_strike=4995.0, short_strike=5000.0)
    assert safe == pytest.approx(5.0 - _MIN_PROFIT_PER_SHARE)


def test_clamps_negative_to_zero_for_inverted_strikes() -> None:
    assert _safe_debit_max(1.00, long_strike=5005.0, short_strike=5000.0) == 0.0


# ---------- ratio-aware ceiling (the user's actual constraint) -------------


def test_ratio_ceiling_caps_below_width_for_10_percent_floor() -> None:
    """min_profit_to_loss_ratio=0.10 means debit <= W/(1+0.10) = W/1.10.

    For a $5-wide spread: ceiling = 5/1.10 = 4.545. The width-only ceiling
    (4.95) is looser, so the ratio-aware ceiling wins.
    """

    safe = _safe_debit_max(
        4.80,                                  # caller wants up to 4.80
        long_strike=4995.0, short_strike=5000.0,
        min_profit_to_loss_ratio=0.10,
    )
    assert safe == pytest.approx(5.0 / 1.10)   # ≈ 4.5454


def test_ratio_ceiling_caps_at_more_demanding_20_percent_floor() -> None:
    """min_profit_to_loss_ratio=0.20 -> ceiling = 5/1.20 ≈ 4.167.
    More restrictive than 0.10."""

    safe = _safe_debit_max(
        4.80,
        long_strike=4995.0, short_strike=5000.0,
        min_profit_to_loss_ratio=0.20,
    )
    assert safe == pytest.approx(5.0 / 1.20)   # ≈ 4.1667


def test_ratio_passthrough_when_request_already_meets_ratio() -> None:
    """If the requested cap is already below the ratio ceiling, no clamping."""

    # ratio 0.10 => ceiling 4.545. Request 4.40 is already safer.
    safe = _safe_debit_max(
        4.40,
        long_strike=4995.0, short_strike=5000.0,
        min_profit_to_loss_ratio=0.10,
    )
    assert safe == 4.40


def test_zero_or_negative_ratio_disables_constraint() -> None:
    """A ratio of 0 (or negative) means 'no constraint' — same as None."""

    safe = _safe_debit_max(
        4.95,
        long_strike=4995.0, short_strike=5000.0,
        min_profit_to_loss_ratio=0.0,
    )
    # Falls through to width-only ceiling (4.95 == ceiling).
    assert safe == 4.95


def test_ratio_takes_precedence_over_width_when_more_strict() -> None:
    """The smaller of the two ceilings wins."""

    # 5-wide, request 4.95.
    # width ceiling = 4.95.  ratio (0.10) ceiling = 4.545.  -> 4.545 wins.
    safe = _safe_debit_max(
        4.95,
        long_strike=4995.0, short_strike=5000.0,
        min_profit_to_loss_ratio=0.10,
    )
    assert safe == pytest.approx(5.0 / 1.10)


def test_user_floor_holds_when_filled_at_ceiling() -> None:
    """Sanity: a fill at the ratio ceiling delivers exactly the requested
    profit margin (within rounding)."""

    width = 5.0
    ratio = 0.10  # require at least 10% profit per unit risk
    safe = _safe_debit_max(
        4.95,
        long_strike=4995.0, short_strike=4995.0 + width,
        min_profit_to_loss_ratio=ratio,
    )
    realized_ratio = (width - safe) / safe
    assert realized_ratio == pytest.approx(ratio)


# ---------- initial limit price is also capped (not just reprice) ----------


def test_initial_price_capped_when_midpoint_violates_ratio() -> None:
    """If the chain's midpoint debit exceeds the ratio ceiling, the initial
    limit price is the ceiling — not the midpoint. Fill becomes unlikely
    but the user's floor is preserved if it does fill."""

    # 5-wide spread, midpoint $4.80, ratio 0.10 -> ceiling 5/1.10 = 4.545.
    capped = _safe_debit_max(
        4.80,
        long_strike=4995.0, short_strike=5000.0,
        min_profit_to_loss_ratio=0.10,
    )
    assert capped == pytest.approx(5.0 / 1.10)


def test_initial_price_passthrough_when_midpoint_satisfies_ratio() -> None:
    """If midpoint already satisfies the ratio, no capping. We submit at
    midpoint and the realized ratio is BETTER than the configured floor."""

    # 5-wide, midpoint $4.40, ratio 0.10 -> ceiling 4.545. 4.40 < 4.545 ok.
    capped = _safe_debit_max(
        4.40,
        long_strike=4995.0, short_strike=5000.0,
        min_profit_to_loss_ratio=0.10,
    )
    assert capped == 4.40
    realized_ratio = (5.0 - 4.40) / 4.40
    assert realized_ratio > 0.10  # strictly better than the floor


# ---------- entry timeout default ------------------------------------------


def test_entry_timeout_default_is_5_minutes() -> None:
    """Regression: total budget for the entry order is 5 minutes by default;
    if you want a tighter window, pass ``timeout_s`` explicitly."""

    sig = inspect.signature(submit_entry_lmt)
    assert sig.parameters["timeout_s"].default == 300.0


# ---------- submit_close_market regression -----------------------------------


def test_submit_close_market_does_not_NameError(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression for strategy-review §3.1: the close path used to reference
    `phase_timeout` (a name local to submit_entry_lmt) which would have raised
    NameError on the first stop fire in production. Drive the function with a
    fake client that returns a Filled status; the test passes if the call
    completes without raising.
    """

    from bull_call.chain import OptionContract
    from bull_call.cpapi import execution

    long_leg = OptionContract(strike=4995.0, conid=111, right="C", expiry="20260429")
    short_leg = OptionContract(strike=5005.0, conid=222, right="C", expiry="20260429")

    place_calls: list[Any] = []
    status_calls: list[str] = []

    class _Resp:
        def __init__(self, data: Any) -> None:
            self.data = data

    class FakeClient:
        def place_order(self, *, order_request: Any, answers: Any, account_id: str) -> _Resp:
            place_calls.append(order_request)
            return _Resp([{"order_id": "close-1"}])

        def order_status(self, *, order_id: str) -> _Resp:
            status_calls.append(order_id)
            return _Resp({"order_status": "Filled", "average_price": "1.20"})

    fake_client = FakeClient()

    # Don't actually sleep through a 15s timeout if the test is slow.
    monkeypatch.setattr(execution.time, "sleep", lambda _s: None)

    fill = execution.submit_close_market(
        fake_client,                           # type: ignore[arg-type]
        account_id="A1",
        long_leg=long_leg,
        short_leg=short_leg,
        timeout_s=2.0,
    )

    assert fill.filled is True
    assert fill.avg_fill_price == pytest.approx(1.20)
    assert place_calls and status_calls       # both API methods were invoked


def test_submit_close_market_unfilled_returns_filled_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """If order_status never reports Filled, submit_close_market returns
    filled=False (and the order is left working — caller decides what to do).
    Confirms the timeout-arg path also doesn't NameError."""

    from bull_call.chain import OptionContract
    from bull_call.cpapi import execution

    long_leg = OptionContract(strike=4995.0, conid=111, right="C", expiry="20260429")
    short_leg = OptionContract(strike=5005.0, conid=222, right="C", expiry="20260429")

    class _Resp:
        def __init__(self, data: Any) -> None:
            self.data = data

    class FakeClient:
        def place_order(self, *, order_request: Any, answers: Any, account_id: str) -> _Resp:
            return _Resp([{"order_id": "close-1"}])

        def order_status(self, *, order_id: str) -> _Resp:
            return _Resp({"order_status": "Submitted"})  # never Filled

    monkeypatch.setattr(execution.time, "sleep", lambda _s: None)

    fill = execution.submit_close_market(
        FakeClient(),                          # type: ignore[arg-type]
        account_id="A1",
        long_leg=long_leg,
        short_leg=short_leg,
        timeout_s=0.5,
    )

    assert fill.filled is False
