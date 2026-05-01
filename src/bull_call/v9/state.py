"""DynamoDB-backed v9 paper-trading state.

Schema (single-table; coexists with the SPX bot's SPREAD#/STOP# rows):

    pk                      sk             attrs
    V9#POSITION             {ticker}       shares, last_price, updated_at
    V9#FILL#{date}          {ts}#{seq}     ticker, action, quantity, price,
                                           slippage_bps
    V9#NAV                  {date}         nav_dollars, positions_dump
    V9#META                 PILOT          start_date, end_date,
                                           initial_capital, status,
                                           halt_reason

Pilot status state machine:

    AWAITING_START → ACTIVE → COMPLETED
                          ↘  HALTED  (catastrophic stop)

boto3 is imported lazily so importing the module without AWS configured
works fine — only ``V9Store(...)`` triggers the resource construction.
"""

from __future__ import annotations

import itertools
import json
import threading
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class V9PilotMetadata:
    initial_capital: float
    status: str  # "AWAITING_START" | "ACTIVE" | "HALTED" | "COMPLETED"
    start_date: str | None
    end_date: str | None
    halt_reason: str | None


@dataclass(frozen=True, slots=True)
class V9Position:
    ticker: str
    shares: float
    last_price: float
    updated_at: str


@dataclass(frozen=True, slots=True)
class V9Fill:
    rebalance_date: str
    ts: str
    ticker: str
    action: str  # "BUY" | "SELL"
    quantity: float
    price: float
    slippage_bps: float | None


@dataclass(frozen=True, slots=True)
class V9NavSnapshot:
    date: str
    nav_dollars: float
    positions_dump: dict[str, float]  # ticker → market_value


_PK_POSITION = "V9#POSITION"
_PK_NAV = "V9#NAV"
_PK_META = "V9#META"
_SK_META = "PILOT"


def _pk_fill(date: str) -> str:
    return f"V9#FILL#{date}"


def _to_decimal(value: float | int | None) -> Any:
    if value is None:
        return None
    from decimal import Decimal

    return Decimal(str(value))


def _from_decimal(value: Any) -> float:
    return float(value)


class V9Store:
    """DynamoDB wrapper for v9 paper-trading records."""

    def __init__(self, table_name: str, *, region: str = "us-east-1") -> None:
        import boto3

        self._table_name = table_name
        self._region = region
        self._dynamodb = boto3.resource("dynamodb", region_name=region)
        self._table = self._dynamodb.Table(table_name)
        self._fill_seq = itertools.count(1)
        self._fill_seq_lock = threading.Lock()

    # ---- pilot metadata ----------------------------------------------------

    def initialize_pilot(self, *, initial_capital: float) -> None:
        from botocore.exceptions import ClientError

        try:
            self._table.put_item(
                Item={
                    "pk": _PK_META,
                    "sk": _SK_META,
                    "initial_capital": _to_decimal(initial_capital),
                    "status": "AWAITING_START",
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise RuntimeError("pilot already initialized") from exc
            raise

    def get_pilot_metadata(self) -> V9PilotMetadata | None:
        resp = self._table.get_item(Key={"pk": _PK_META, "sk": _SK_META})
        item = resp.get("Item")
        if item is None:
            return None
        return V9PilotMetadata(
            initial_capital=_from_decimal(item["initial_capital"]),
            status=item["status"],
            start_date=item.get("start_date"),
            end_date=item.get("end_date"),
            halt_reason=item.get("halt_reason"),
        )

    def start_pilot(self, *, start_date: str, end_date: str) -> None:
        self._table.update_item(
            Key={"pk": _PK_META, "sk": _SK_META},
            UpdateExpression=(
                "SET #s = :status, start_date = :start, end_date = :end"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": "ACTIVE",
                ":start": start_date,
                ":end": end_date,
            },
        )

    def complete_pilot(self) -> None:
        self._table.update_item(
            Key={"pk": _PK_META, "sk": _SK_META},
            UpdateExpression="SET #s = :status",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":status": "COMPLETED"},
        )

    def halt_pilot(self, *, reason: str) -> None:
        self._table.update_item(
            Key={"pk": _PK_META, "sk": _SK_META},
            UpdateExpression="SET #s = :status, halt_reason = :reason",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": "HALTED",
                ":reason": reason,
            },
        )

    # ---- positions ---------------------------------------------------------

    def upsert_position(self, position: V9Position) -> None:
        self._table.put_item(Item={
            "pk": _PK_POSITION,
            "sk": position.ticker,
            "ticker": position.ticker,
            "shares": _to_decimal(position.shares),
            "last_price": _to_decimal(position.last_price),
            "updated_at": position.updated_at,
        })

    def list_positions(self) -> list[V9Position]:
        from boto3.dynamodb.conditions import Key

        resp = self._table.query(
            KeyConditionExpression=Key("pk").eq(_PK_POSITION),
        )
        items = sorted(resp.get("Items", []), key=lambda r: r["sk"])
        return [
            V9Position(
                ticker=item["ticker"],
                shares=_from_decimal(item["shares"]),
                last_price=_from_decimal(item["last_price"]),
                updated_at=item["updated_at"],
            )
            for item in items
        ]

    def delete_position(self, ticker: str) -> None:
        self._table.delete_item(Key={"pk": _PK_POSITION, "sk": ticker})

    # ---- fills -------------------------------------------------------------

    def record_fill(self, fill: V9Fill) -> None:
        with self._fill_seq_lock:
            seq = next(self._fill_seq)
        sk = f"{fill.ts}#{seq:06d}"
        self._table.put_item(Item={
            "pk": _pk_fill(fill.rebalance_date),
            "sk": sk,
            "rebalance_date": fill.rebalance_date,
            "ts": fill.ts,
            "ticker": fill.ticker,
            "action": fill.action,
            "quantity": _to_decimal(fill.quantity),
            "price": _to_decimal(fill.price),
            "slippage_bps": _to_decimal(fill.slippage_bps),
        })

    def list_fills_for_date(self, rebalance_date: str) -> list[V9Fill]:
        from boto3.dynamodb.conditions import Key

        resp = self._table.query(
            KeyConditionExpression=Key("pk").eq(_pk_fill(rebalance_date)),
        )
        items = resp.get("Items", [])
        return [
            V9Fill(
                rebalance_date=item["rebalance_date"],
                ts=item["ts"],
                ticker=item["ticker"],
                action=item["action"],
                quantity=_from_decimal(item["quantity"]),
                price=_from_decimal(item["price"]),
                slippage_bps=(
                    _from_decimal(item["slippage_bps"])
                    if item.get("slippage_bps") is not None
                    else None
                ),
            )
            for item in items
        ]

    # ---- NAV snapshots -----------------------------------------------------

    def record_nav_snapshot(self, snapshot: V9NavSnapshot) -> None:
        self._table.put_item(Item={
            "pk": _PK_NAV,
            "sk": snapshot.date,
            "date": snapshot.date,
            "nav_dollars": _to_decimal(snapshot.nav_dollars),
            "positions_dump": json.dumps(snapshot.positions_dump),
        })

    def get_nav_snapshot(self, date: str) -> V9NavSnapshot | None:
        resp = self._table.get_item(Key={"pk": _PK_NAV, "sk": date})
        item = resp.get("Item")
        if item is None:
            return None
        return V9NavSnapshot(
            date=item["date"],
            nav_dollars=_from_decimal(item["nav_dollars"]),
            positions_dump=json.loads(item["positions_dump"]),
        )

    def list_nav_snapshots(
        self, *, start_date: str, end_date: str
    ) -> list[V9NavSnapshot]:
        from boto3.dynamodb.conditions import Key

        resp = self._table.query(
            KeyConditionExpression=Key("pk").eq(_PK_NAV) & Key("sk").between(
                start_date, end_date
            ),
        )
        items = sorted(resp.get("Items", []), key=lambda r: r["sk"])
        return [
            V9NavSnapshot(
                date=item["date"],
                nav_dollars=_from_decimal(item["nav_dollars"]),
                positions_dump=json.loads(item["positions_dump"]),
            )
            for item in items
        ]
