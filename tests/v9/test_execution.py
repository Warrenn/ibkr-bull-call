"""Tests for ``bull_call.v9.execution`` — stock-market-order submission
via cpapi. Unlike the SPX bot's options/combo path, ETF orders are
single-leg, market-tif-DAY, and use ``conid`` directly (no conidex).

All tests use ``FakeClient`` instances that mimic ibind's IbkrClient
contract; no live gateway involved.
"""

from __future__ import annotations

from typing import Any

import pytest

from bull_call.v9.execution import StockFillReport, submit_stock_market_order


class _Resp:
    def __init__(self, data: Any) -> None:
        self.data = data


class _BaseClient:
    """FakeClient default: place succeeds, fill on first poll."""

    def __init__(self) -> None:
        self.placed: list[Any] = []
        self.cancelled: list[str] = []

    def place_order(
        self, *, order_request: Any, answers: Any, account_id: str
    ) -> _Resp:
        self.placed.append(order_request)
        return _Resp([{"order_id": "ord-1"}])

    def order_status(self, *, order_id: str) -> _Resp:
        return _Resp({
            "order_status": "Filled",
            "average_price": "200.00",
            "filled_quantity": "50",
        })

    def cancel_order(self, *, order_id: str, account_id: str) -> _Resp:
        self.cancelled.append(order_id)
        return _Resp({})


def test_submit_buy_market_returns_fill_on_immediate_fill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bull_call.v9 import execution

    client = _BaseClient()
    monkeypatch.setattr(execution.time, "sleep", lambda _s: None)

    fill = submit_stock_market_order(
        client,  # type: ignore[arg-type]
        account_id="A1",
        ticker="XLK",
        conid=11111,
        action="BUY",
        quantity=50,
        timeout_s=10.0,
    )

    assert isinstance(fill, StockFillReport)
    assert fill.filled is True
    assert fill.ticker == "XLK"
    assert fill.action == "BUY"
    assert fill.quantity == 50
    assert fill.avg_fill_price == pytest.approx(200.0)
    assert fill.order_id == "ord-1"
    assert len(client.placed) == 1


def test_submit_sell_market_returns_fill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bull_call.v9 import execution

    client = _BaseClient()
    monkeypatch.setattr(execution.time, "sleep", lambda _s: None)

    fill = submit_stock_market_order(
        client,  # type: ignore[arg-type]
        account_id="A1",
        ticker="XLE",
        conid=22222,
        action="SELL",
        quantity=100,
        timeout_s=10.0,
    )

    assert fill.filled is True
    assert fill.action == "SELL"


def test_order_request_uses_conid_not_conidex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ETF orders identify the contract via ``conid`` directly; combos
    use ``conidex`` instead. Make sure we don't mix them up."""
    from bull_call.v9 import execution

    client = _BaseClient()
    monkeypatch.setattr(execution.time, "sleep", lambda _s: None)

    submit_stock_market_order(
        client,  # type: ignore[arg-type]
        account_id="A1", ticker="XLK", conid=11111,
        action="BUY", quantity=50, timeout_s=10.0,
    )

    placed = client.placed[0]
    # OrderRequest from ibind exposes attrs; we just verify the conid
    # was passed through. ``conidex`` should be absent / empty.
    assert getattr(placed, "conid", None) == 11111 or "conid=11111" in repr(placed)


def test_returns_unfilled_when_order_status_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bull_call.v9 import execution

    class CancelledClient(_BaseClient):
        def order_status(self, *, order_id: str) -> _Resp:
            return _Resp({"order_status": "Cancelled"})

    client = CancelledClient()
    monkeypatch.setattr(execution.time, "sleep", lambda _s: None)

    fill = submit_stock_market_order(
        client,  # type: ignore[arg-type]
        account_id="A1", ticker="XLK", conid=11111,
        action="BUY", quantity=50, timeout_s=10.0,
    )

    assert fill.filled is False
    assert fill.avg_fill_price != fill.avg_fill_price  # NaN
    assert fill.order_id == "ord-1"


def test_returns_unfilled_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If order never reports Filled within budget, returns unfilled +
    cancels the working order at IBKR."""
    from bull_call.v9 import execution

    class StuckClient(_BaseClient):
        def order_status(self, *, order_id: str) -> _Resp:
            return _Resp({"order_status": "Submitted"})

    client = StuckClient()
    monkeypatch.setattr(execution.time, "sleep", lambda _s: None)

    fill = submit_stock_market_order(
        client,  # type: ignore[arg-type]
        account_id="A1", ticker="XLK", conid=11111,
        action="BUY", quantity=50, timeout_s=0.1,
    )

    assert fill.filled is False
    assert client.cancelled == ["ord-1"]


def test_shutdown_aborts_fill_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bull_call.v9 import execution

    class StuckClient(_BaseClient):
        def order_status(self, *, order_id: str) -> _Resp:
            return _Resp({"order_status": "Submitted"})

    client = StuckClient()
    monkeypatch.setattr(execution.time, "sleep", lambda _s: None)

    # should_stop_fn always returns True → exits before any fill check
    fill = submit_stock_market_order(
        client,  # type: ignore[arg-type]
        account_id="A1", ticker="XLK", conid=11111,
        action="BUY", quantity=50, timeout_s=10.0,
        should_stop_fn=lambda: True,
    )

    assert fill.filled is False


def test_invalid_action_raises() -> None:
    from bull_call.v9 import execution

    class _DoNotCallClient:
        def place_order(self, **_: Any) -> _Resp:
            raise AssertionError("invalid action — should never reach client")

    client = _DoNotCallClient()
    with pytest.raises(ValueError, match="action"):
        submit_stock_market_order(
            client,  # type: ignore[arg-type]
            account_id="A1", ticker="XLK", conid=11111,
            action="HOLD", quantity=50, timeout_s=1.0,  # invalid
        )


def test_zero_quantity_raises() -> None:
    from bull_call.v9 import execution

    class _DoNotCallClient:
        def place_order(self, **_: Any) -> _Resp:
            raise AssertionError("zero qty — should never reach client")

    with pytest.raises(ValueError, match="quantity"):
        submit_stock_market_order(
            _DoNotCallClient(),  # type: ignore[arg-type]
            account_id="A1", ticker="XLK", conid=11111,
            action="BUY", quantity=0, timeout_s=1.0,
        )
