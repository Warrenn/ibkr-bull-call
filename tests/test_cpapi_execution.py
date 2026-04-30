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


# ---------- submit_entry_lmt orchestrator -----------------------------------


_LONG = None  # populated below; pytest fixtures would also work
_SHORT = None


def _legs() -> tuple[Any, Any]:
    """Build a fresh pair of OptionContract legs for entry tests."""

    from bull_call.chain import OptionContract
    long_leg = OptionContract(strike=4995.0, conid=111, right="C", expiry="20260429")
    short_leg = OptionContract(strike=5005.0, conid=222, right="C", expiry="20260429")
    return long_leg, short_leg


class _Resp2:
    def __init__(self, data: Any) -> None:
        self.data = data


def test_submit_entry_lmt_fills_in_phase_1(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: midpoint LMT fills inside the first phase budget."""

    from bull_call.cpapi import execution

    long_leg, short_leg = _legs()
    placed: list[Any] = []

    class FakeClient:
        def place_order(self, *, order_request: Any, answers: Any, account_id: str) -> _Resp2:
            placed.append(order_request)
            return _Resp2([{"order_id": "entry-1"}])

        def order_status(self, *, order_id: str) -> _Resp2:
            return _Resp2({"order_status": "Filled", "average_price": "5.00"})

        def modify_order(self, **_: Any) -> _Resp2:
            raise AssertionError("phase-1 fill — modify should not be called")

        def cancel_order(self, **_: Any) -> _Resp2:
            raise AssertionError("phase-1 fill — cancel should not be called")

    monkeypatch.setattr(execution.time, "sleep", lambda _s: None)

    fill = execution.submit_entry_lmt(
        FakeClient(),  # type: ignore[arg-type]
        account_id="A1",
        long_leg=long_leg,
        short_leg=short_leg,
        debit_mid=5.00,
        debit_max=5.20,
        timeout_s=10.0,
    )
    assert fill.filled is True
    assert fill.avg_fill_price == pytest.approx(5.00)
    assert fill.order_id == "entry-1"
    assert len(placed) == 1


def test_submit_entry_lmt_reprices_then_fills_in_phase_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 1 doesn't fill; bot reprices upward and phase 2 fills.

    The phase-1 ``_await_fill`` loop runs as fast as Python can with
    ``time.sleep`` stubbed, so we can't script statuses by call count.
    Instead we flip a flag inside ``modify_order`` (which only runs at
    the phase boundary) — every poll BEFORE that returns Submitted,
    every poll AFTER returns Filled.
    """

    from bull_call.cpapi import execution

    long_leg, short_leg = _legs()
    modified: list[Any] = []
    phase = {"in_phase_2": False}

    class FakeClient:
        def place_order(self, *, order_request: Any, answers: Any, account_id: str) -> _Resp2:
            return _Resp2([{"order_id": "entry-2"}])

        def order_status(self, *, order_id: str) -> _Resp2:
            if phase["in_phase_2"]:
                return _Resp2({"order_status": "Filled", "average_price": "5.10"})
            return _Resp2({"order_status": "Submitted"})

        def modify_order(self, **kw: Any) -> _Resp2:
            modified.append(kw)
            phase["in_phase_2"] = True
            return _Resp2({"ok": True})

        def cancel_order(self, **_: Any) -> _Resp2:
            raise AssertionError("phase-2 fill — cancel should not be called")

    monkeypatch.setattr(execution.time, "sleep", lambda _s: None)

    fill = execution.submit_entry_lmt(
        FakeClient(),  # type: ignore[arg-type]
        account_id="A1",
        long_leg=long_leg,
        short_leg=short_leg,
        debit_mid=5.00,
        debit_max=5.20,
        timeout_s=2.0,
    )
    assert fill.filled is True
    assert fill.avg_fill_price == pytest.approx(5.10)
    assert len(modified) == 1


def test_submit_entry_lmt_cancels_when_neither_phase_fills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both phases time out — order is cancelled and reported unfilled."""

    from bull_call.cpapi import execution

    long_leg, short_leg = _legs()
    cancel_calls: list[str] = []

    class FakeClient:
        def place_order(self, *, order_request: Any, answers: Any, account_id: str) -> _Resp2:
            return _Resp2([{"order_id": "entry-3"}])

        def order_status(self, *, order_id: str) -> _Resp2:
            return _Resp2({"order_status": "Submitted"})  # never fills

        def modify_order(self, **_: Any) -> _Resp2:
            return _Resp2({"ok": True})

        def cancel_order(self, *, order_id: str, account_id: str) -> _Resp2:
            cancel_calls.append(order_id)
            return _Resp2({"ok": True})

    monkeypatch.setattr(execution.time, "sleep", lambda _s: None)

    fill = execution.submit_entry_lmt(
        FakeClient(),  # type: ignore[arg-type]
        account_id="A1",
        long_leg=long_leg,
        short_leg=short_leg,
        debit_mid=5.00,
        debit_max=5.20,
        timeout_s=0.5,
    )
    assert fill.filled is False
    assert cancel_calls == ["entry-3"]


def test_submit_entry_lmt_shutdown_during_phase_1_cancels_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SIGTERM mid-phase-1 (between polls) — cancel order, exit without
    bothering with phase 2."""

    from bull_call.cpapi import execution

    long_leg, short_leg = _legs()
    cancel_calls: list[str] = []
    modify_calls: list[Any] = []

    class FakeClient:
        def place_order(self, *, order_request: Any, answers: Any, account_id: str) -> _Resp2:
            return _Resp2([{"order_id": "entry-4"}])

        def order_status(self, *, order_id: str) -> _Resp2:
            return _Resp2({"order_status": "Submitted"})

        def modify_order(self, **_: Any) -> _Resp2:
            modify_calls.append(_)
            return _Resp2({"ok": True})

        def cancel_order(self, *, order_id: str, account_id: str) -> _Resp2:
            cancel_calls.append(order_id)
            return _Resp2({"ok": True})

    monkeypatch.setattr(execution.time, "sleep", lambda _s: None)

    flag = {"stop": True}  # tripped immediately so phase-1 _await_fill exits
    fill = execution.submit_entry_lmt(
        FakeClient(),  # type: ignore[arg-type]
        account_id="A1",
        long_leg=long_leg,
        short_leg=short_leg,
        debit_mid=5.00,
        debit_max=5.20,
        timeout_s=10.0,
        should_stop_fn=lambda: flag["stop"],
    )
    assert fill.filled is False
    assert cancel_calls == ["entry-4"]
    assert modify_calls == []  # never reached phase 2


def test_submit_entry_lmt_swallows_cancel_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If cancel_order itself raises, the wrapper still returns an
    unfilled FillReport — failure to cancel must not crash the daemon."""

    from bull_call.cpapi import execution

    long_leg, short_leg = _legs()

    class FakeClient:
        def place_order(self, *, order_request: Any, answers: Any, account_id: str) -> _Resp2:
            return _Resp2([{"order_id": "entry-5"}])

        def order_status(self, *, order_id: str) -> _Resp2:
            return _Resp2({"order_status": "Submitted"})

        def modify_order(self, **_: Any) -> _Resp2:
            return _Resp2({"ok": True})

        def cancel_order(self, **_: Any) -> _Resp2:
            raise RuntimeError("simulated cancel failure")

    monkeypatch.setattr(execution.time, "sleep", lambda _s: None)

    fill = execution.submit_entry_lmt(
        FakeClient(),  # type: ignore[arg-type]
        account_id="A1",
        long_leg=long_leg,
        short_leg=short_leg,
        debit_mid=5.00,
        debit_max=5.20,
        timeout_s=0.5,
    )
    assert fill.filled is False  # graceful degradation


# ---------- flatten_unmatched_leg ------------------------------------------


def test_flatten_unmatched_leg_closes_long_when_short_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Long leg filled (+1) but short never showed — flatten the long
    via a single-leg MKT SELL."""

    from bull_call.chain import OptionContract
    from bull_call.cpapi import execution

    long_leg = OptionContract(strike=4995.0, conid=111, right="C", expiry="20260429")
    short_leg = OptionContract(strike=5005.0, conid=222, right="C", expiry="20260429")
    placed: list[Any] = []

    class FakeClient:
        def positions_by_conid(self, *, account_id: str, conid: str) -> _Resp2:
            if int(conid) == 111:
                return _Resp2([{"position": 1.0}])
            return _Resp2([])  # short flat

        def place_order(self, *, order_request: Any, answers: Any, account_id: str) -> _Resp2:
            placed.append(order_request)
            return _Resp2([{"order_id": "flatten-1"}])

    execution.flatten_unmatched_leg(
        FakeClient(),  # type: ignore[arg-type]
        account_id="A1",
        long_leg=long_leg,
        short_leg=short_leg,
    )
    assert len(placed) == 1
    order = placed[0]
    assert order.conid == "111"
    assert order.side == "SELL"
    assert order.quantity == 1
    assert order.order_type == "MKT"


def test_flatten_unmatched_leg_closes_short_when_long_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Short leg filled (-1) but long never showed — flatten the short
    via a single-leg MKT BUY."""

    from bull_call.chain import OptionContract
    from bull_call.cpapi import execution

    long_leg = OptionContract(strike=4995.0, conid=111, right="C", expiry="20260429")
    short_leg = OptionContract(strike=5005.0, conid=222, right="C", expiry="20260429")
    placed: list[Any] = []

    class FakeClient:
        def positions_by_conid(self, *, account_id: str, conid: str) -> _Resp2:
            if int(conid) == 222:
                return _Resp2([{"position": -1.0}])
            return _Resp2([])  # long flat

        def place_order(self, *, order_request: Any, answers: Any, account_id: str) -> _Resp2:
            placed.append(order_request)
            return _Resp2([{"order_id": "flatten-2"}])

    execution.flatten_unmatched_leg(
        FakeClient(),  # type: ignore[arg-type]
        account_id="A1",
        long_leg=long_leg,
        short_leg=short_leg,
    )
    assert len(placed) == 1
    order = placed[0]
    assert order.conid == "222"
    assert order.side == "BUY"  # short was negative; closing it is BUY
    assert order.quantity == 1


def test_flatten_unmatched_leg_no_op_when_balanced() -> None:
    """Both legs filled cleanly — no order to submit."""

    from bull_call.chain import OptionContract
    from bull_call.cpapi import execution

    long_leg = OptionContract(strike=4995.0, conid=111, right="C", expiry="20260429")
    short_leg = OptionContract(strike=5005.0, conid=222, right="C", expiry="20260429")
    placed: list[Any] = []

    class FakeClient:
        def positions_by_conid(self, *, account_id: str, conid: str) -> _Resp2:
            if int(conid) == 111:
                return _Resp2([{"position": 1.0}])
            return _Resp2([{"position": -1.0}])

        def place_order(self, **kw: Any) -> _Resp2:
            placed.append(kw)
            return _Resp2({})

    execution.flatten_unmatched_leg(
        FakeClient(),  # type: ignore[arg-type]
        account_id="A1",
        long_leg=long_leg,
        short_leg=short_leg,
    )
    assert placed == []


def test_flatten_unmatched_leg_no_op_when_both_flat() -> None:
    """Neither leg has a position — entry never made it through; no-op."""

    from bull_call.chain import OptionContract
    from bull_call.cpapi import execution

    long_leg = OptionContract(strike=4995.0, conid=111, right="C", expiry="20260429")
    short_leg = OptionContract(strike=5005.0, conid=222, right="C", expiry="20260429")
    placed: list[Any] = []

    class FakeClient:
        def positions_by_conid(self, *, account_id: str, conid: str) -> _Resp2:
            return _Resp2([])

        def place_order(self, **kw: Any) -> _Resp2:
            placed.append(kw)
            return _Resp2({})

    execution.flatten_unmatched_leg(
        FakeClient(),  # type: ignore[arg-type]
        account_id="A1",
        long_leg=long_leg,
        short_leg=short_leg,
    )
    assert placed == []


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


# ---------- SIGTERM-aware fill polling (PR #8) -------------------------------


def test_submit_close_market_short_circuits_on_should_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``should_stop_fn`` returns True between polls, the close path
    bails out without burning the full timeout budget. The order is left
    working at IBKR — the next instance's reconcile will pick it up.
    """

    from bull_call.chain import OptionContract
    from bull_call.cpapi import execution

    long_leg = OptionContract(strike=4995.0, conid=111, right="C", expiry="20260429")
    short_leg = OptionContract(strike=5005.0, conid=222, right="C", expiry="20260429")

    poll_count = [0]

    class _Resp:
        def __init__(self, data: Any) -> None:
            self.data = data

    class FakeClient:
        def place_order(self, *, order_request: Any, answers: Any, account_id: str) -> _Resp:
            return _Resp([{"order_id": "close-1"}])

        def order_status(self, *, order_id: str) -> _Resp:
            poll_count[0] += 1
            return _Resp({"order_status": "Submitted"})  # never Filled

    monkeypatch.setattr(execution.time, "sleep", lambda _s: None)

    flag = {"stop": False}
    def stop_fn() -> bool:
        if poll_count[0] >= 2:
            flag["stop"] = True
        return flag["stop"]

    import time as _time
    wall_before = _time.monotonic()
    fill = execution.submit_close_market(
        FakeClient(),  # type: ignore[arg-type]
        account_id="A1",
        long_leg=long_leg,
        short_leg=short_leg,
        timeout_s=300.0,                       # generous budget; should NOT be hit
        should_stop_fn=stop_fn,
    )
    elapsed = _time.monotonic() - wall_before

    assert fill.filled is False
    assert elapsed < 1.0, (
        f"submit_close_market did not respect should_stop_fn: "
        f"elapsed={elapsed:.2f}s with timeout_s=300"
    )
    assert poll_count[0] >= 2  # we did at least the polls before stop tripped


def test_verify_legs_balanced_short_circuits_on_should_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same shutdown semantics for the post-fill leg-balance polling: bail
    out promptly instead of running out the full leg_fill_timeout_sec."""

    from bull_call.chain import OptionContract
    from bull_call.cpapi import execution

    long_leg = OptionContract(strike=4995.0, conid=111, right="C", expiry="20260429")
    short_leg = OptionContract(strike=5005.0, conid=222, right="C", expiry="20260429")

    polls = [0]

    class _Resp:
        def __init__(self, data: Any) -> None:
            self.data = data

    class FakeClient:
        def positions_by_conid(self, *, account_id: str, conid: str) -> _Resp:
            polls[0] += 1
            # Long leg shows +1, short leg shows 0 (never balanced).
            if int(conid) == 111:
                return _Resp([{"conid": 111, "position": 1.0}])
            return _Resp([])

    monkeypatch.setattr(execution.time, "sleep", lambda _s: None)

    flag = {"stop": False}
    def stop_fn() -> bool:
        if polls[0] >= 2:
            flag["stop"] = True
        return flag["stop"]

    import time as _time
    wall_before = _time.monotonic()
    balanced = execution.verify_legs_balanced(
        FakeClient(),  # type: ignore[arg-type]
        account_id="A1",
        long_leg=long_leg,
        short_leg=short_leg,
        timeout_s=300.0,
        poll_interval_s=0.0,
        should_stop_fn=stop_fn,
    )
    elapsed = _time.monotonic() - wall_before

    assert balanced is False
    assert elapsed < 1.0
    assert polls[0] >= 2


# ---------- orphaned-order cleanup at session start --------------------------


def test_cancel_orphaned_combo_orders_cancels_only_working_bag_combos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At session start, before the bot has submitted anything today, any
    working combo orders are orphans from a prior crashed instance (TIF=DAY
    auto-cancelled yesterday's orders at 4 pm). Cancel each one to prevent
    a double-fill if the working order completes alongside a fresh entry."""

    from bull_call.cpapi import execution

    cancelled: list[str] = []

    class _Resp:
        def __init__(self, data: Any) -> None:
            self.data = data

    class FakeClient:
        def live_orders(self, *, filters: Any = None, force: bool = None,
                        account_id: str = None) -> _Resp:
            return _Resp({
                "orders": [
                    # Working bull-call combo from a prior crashed run — cancel.
                    {
                        "order_id": "1001", "orderId": "1001",
                        "secType": "BAG",
                        "conidex": "28812380;;;111/1,222/-1",
                        "status": "Submitted",
                    },
                    # Working but unrelated single-leg — leave alone.
                    {
                        "order_id": "1002", "orderId": "1002",
                        "secType": "OPT",
                        "conid": "999",
                        "status": "Submitted",
                    },
                    # Filled combo — already done, no need to cancel.
                    {
                        "order_id": "1003", "orderId": "1003",
                        "secType": "BAG",
                        "conidex": "28812380;;;333/1,444/-1",
                        "status": "Filled",
                    },
                    # Cancelled combo — terminal, no need.
                    {
                        "order_id": "1004", "orderId": "1004",
                        "secType": "BAG",
                        "conidex": "28812380;;;555/1,666/-1",
                        "status": "Cancelled",
                    },
                    # Combo with a foreign prefix (not our spread shape) —
                    # defensive: leave alone in case user has manual orders.
                    {
                        "order_id": "1005", "orderId": "1005",
                        "secType": "BAG",
                        "conidex": "12345678;;;777/1,888/-1",
                        "status": "Submitted",
                    },
                ],
            })

        def cancel_order(self, *, order_id: str, account_id: str) -> _Resp:
            cancelled.append(order_id)
            return _Resp({"msg": "ok"})

    n = execution.cancel_orphaned_combo_orders(
        FakeClient(),  # type: ignore[arg-type]
        account_id="A1",
    )

    assert cancelled == ["1001"]
    assert n == 1


def test_cancel_orphaned_combo_orders_handles_empty_response() -> None:
    """No live orders -> no-op, return 0."""

    from bull_call.cpapi import execution

    class _Resp:
        def __init__(self, data: Any) -> None:
            self.data = data

    class FakeClient:
        def live_orders(self, *, filters: Any = None, force: bool = None,
                        account_id: str = None) -> _Resp:
            return _Resp({"orders": []})

    assert execution.cancel_orphaned_combo_orders(
        FakeClient(),  # type: ignore[arg-type]
        account_id="A1",
    ) == 0


def test_cancel_orphaned_combo_orders_filter_aligns_with_working_statuses() -> None:
    """Sourcery PR #10: ``_WORKING_STATUSES`` and the ``live_orders``
    filters must agree so pending_submit / pending_cancel aren't silently
    dropped server-side before our detection runs."""

    from bull_call.cpapi import execution

    captured_filters: list[Any] = []

    class _Resp:
        def __init__(self, data: Any) -> None:
            self.data = data

    class FakeClient:
        def live_orders(self, *, filters: Any = None, force: bool = None,
                        account_id: str = None) -> _Resp:
            captured_filters.append(filters)
            return _Resp({"orders": []})

        def cancel_order(self, *, order_id: str, account_id: str) -> _Resp:
            return _Resp({})

    execution.cancel_orphaned_combo_orders(
        FakeClient(), account_id="A1",  # type: ignore[arg-type]
    )

    assert captured_filters, "live_orders was not called"
    requested = list(captured_filters[0])
    # Every status we'd recognise as "working" must be expressible via the
    # filter we send to IBKR, otherwise pending states get silently dropped
    # server-side. Normalize both ends to absorb capitalization and
    # underscoring differences.
    requested_normalized = {execution._normalize_status(t) for t in requested}
    assert execution._WORKING_STATUSES == requested_normalized


def test_cancel_orphaned_combo_orders_cancels_pending_states_too() -> None:
    """Pending states are working statuses too — the filter expansion must
    actually result in those orders being detected and cancelled."""

    from bull_call.cpapi import execution

    cancelled: list[str] = []

    class _Resp:
        def __init__(self, data: Any) -> None:
            self.data = data

    class FakeClient:
        def live_orders(self, *, filters: Any = None, force: bool = None,
                        account_id: str = None) -> _Resp:
            return _Resp({
                "orders": [
                    {"order_id": "P1", "secType": "BAG",
                     "conidex": "28812380;;;1/1,2/-1", "status": "PendingSubmit"},
                    {"order_id": "P2", "secType": "BAG",
                     "conidex": "28812380;;;3/1,4/-1", "status": "pending_cancel"},
                ],
            })

        def cancel_order(self, *, order_id: str, account_id: str) -> _Resp:
            cancelled.append(order_id)
            return _Resp({})

    n = execution.cancel_orphaned_combo_orders(
        FakeClient(), account_id="A1",  # type: ignore[arg-type]
    )
    assert sorted(cancelled) == ["P1", "P2"]
    assert n == 2


def test_cancel_orphaned_combo_orders_warns_on_unexpected_response_shape(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Sourcery PR #10: an unexpected response shape used to be silently
    treated as empty — making it impossible to diagnose. Now we log a
    WARNING so the deviation is visible in CloudWatch."""

    import logging

    from bull_call.cpapi import execution

    class _Resp:
        def __init__(self, data: Any) -> None:
            self.data = data

    class FakeClient:
        def live_orders(self, *, filters: Any = None, force: bool = None,
                        account_id: str = None) -> _Resp:
            # An unexpected shape — not a list, not a dict with 'orders'.
            return _Resp("totally unexpected string")

        def cancel_order(self, *, order_id: str, account_id: str) -> _Resp:
            return _Resp({})

    caplog.set_level(logging.WARNING, logger="bull_call.cpapi.execution")
    n = execution.cancel_orphaned_combo_orders(
        FakeClient(), account_id="A1",  # type: ignore[arg-type]
    )
    assert n == 0
    assert any(
        "unexpected" in r.getMessage().lower()
        or "unrecognised" in r.getMessage().lower()
        or "shape" in r.getMessage().lower()
        for r in caplog.records
    ), "expected a WARNING about the unexpected live_orders response shape"


def test_cancel_orphaned_combo_orders_swallows_per_order_cancel_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If cancel_order raises for one order, we still try the rest. The
    cleanup is best-effort — failures are logged but don't abort startup."""

    import logging

    from bull_call.cpapi import execution

    cancelled: list[str] = []

    class _Resp:
        def __init__(self, data: Any) -> None:
            self.data = data

    class FakeClient:
        def live_orders(self, *, filters: Any = None, force: bool = None,
                        account_id: str = None) -> _Resp:
            return _Resp({
                "orders": [
                    {"order_id": "A", "secType": "BAG",
                     "conidex": "28812380;;;1/1,2/-1", "status": "Submitted"},
                    {"order_id": "B", "secType": "BAG",
                     "conidex": "28812380;;;3/1,4/-1", "status": "Submitted"},
                ],
            })

        def cancel_order(self, *, order_id: str, account_id: str) -> _Resp:
            if order_id == "A":
                raise RuntimeError("simulated cancel failure")
            cancelled.append(order_id)
            return _Resp({"msg": "ok"})

    caplog.set_level(logging.WARNING, logger="bull_call.cpapi.execution")
    n = execution.cancel_orphaned_combo_orders(
        FakeClient(),  # type: ignore[arg-type]
        account_id="A1",
    )

    assert cancelled == ["B"]            # B still cancelled despite A failing
    assert n == 1                         # only counts successes
    assert any(
        "cancel" in r.getMessage().lower() and "A" in r.getMessage()
        for r in caplog.records
    ), "expected a warning about the failed cancel"


def test_submit_close_market_shutdown_logs_info_not_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the close path bails on shutdown, it should log INFO ("left
    working at IBKR; next instance will reconcile"), NOT the ERROR
    "did not fill within Xs" — that error is reserved for genuine timeouts.
    """

    import logging

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
            return _Resp({"order_status": "Submitted"})

    monkeypatch.setattr(execution.time, "sleep", lambda _s: None)
    caplog.set_level(logging.DEBUG, logger="bull_call.cpapi.execution")

    fill = execution.submit_close_market(
        FakeClient(),  # type: ignore[arg-type]
        account_id="A1",
        long_leg=long_leg,
        short_leg=short_leg,
        timeout_s=300.0,
        should_stop_fn=lambda: True,
    )

    assert fill.filled is False
    error_records = [
        r for r in caplog.records
        if r.levelno >= logging.ERROR and "did not fill within" in r.getMessage()
    ]
    assert error_records == [], (
        "submit_close_market logged a misleading 'did not fill within' "
        "ERROR even though shutdown was the cause; expected an INFO message."
    )
    info_records = [
        r for r in caplog.records
        if "graceful shutdown" in r.getMessage()
    ]
    assert info_records, "expected an INFO log mentioning graceful shutdown"
