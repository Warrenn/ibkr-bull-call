"""DynamoDB-backed persistence for spreads + stop journal.

Single-table design:

    pk                   sk                       attrs
    SPREAD#{date}        {symbol}                 long_strike, short_strike,
                                                  debit, status, opened_at,
                                                  closed_at, exit_kind,
                                                  settle_value, pnl,
                                                  adopted_from_ibkr (bool)
    STOP#{date}#{symbol} {ts}#{seq}               event, spot, breakeven

The "id" of a spread is the composite string ``{date}#{symbol}`` (the
table-level uniqueness constraint replaces SQLite's UNIQUE index).

boto3 is imported lazily so importing the module without AWS configured
works fine — only ``Store(...)`` triggers the resource construction.
"""

from __future__ import annotations

import itertools
import logging
import re
import threading
from dataclasses import dataclass
from typing import Any

_YEAR_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

log = logging.getLogger(__name__)


class DuplicateSpreadError(RuntimeError):
    """Raised when a spread already exists for (date, symbol)."""


@dataclass(frozen=True, slots=True)
class SpreadRecord:
    id: str                         # composite: "{date}#{symbol}"
    date: str
    symbol: str
    long_strike: float
    short_strike: float
    debit: float
    status: str
    opened_at: str
    closed_at: str | None
    exit_kind: str | None
    settle_value: float | None
    pnl: float | None
    adopted_from_ibkr: bool = False


@dataclass(frozen=True, slots=True)
class StopEvent:
    spread_id: str
    ts: str
    event: str
    spot: float
    breakeven: float


def _spread_id(date: str, symbol: str) -> str:
    return f"{date}#{symbol}"


def _spread_pk(date: str) -> str:
    return f"SPREAD#{date}"


def _stop_pk(spread_id: str) -> str:
    return f"STOP#{spread_id}"


def _to_decimal(value: float | int | None) -> Any:
    """boto3 / DynamoDB requires Decimal for numeric attributes."""

    if value is None:
        return None
    from decimal import Decimal

    return Decimal(str(value))


def _from_decimal(value: Any) -> float:
    if value is None:
        return None  # type: ignore[return-value]
    return float(value)


def _row_to_spread(row: dict[str, Any]) -> SpreadRecord:
    return SpreadRecord(
        id=_spread_id(row["date"], row["symbol"]),
        date=row["date"],
        symbol=row["symbol"],
        long_strike=_from_decimal(row["long_strike"]),
        short_strike=_from_decimal(row["short_strike"]),
        debit=_from_decimal(row["debit"]),
        status=row["status"],
        opened_at=row["opened_at"],
        closed_at=row.get("closed_at"),
        exit_kind=row.get("exit_kind"),
        settle_value=_from_decimal(row.get("settle_value")) if row.get("settle_value") is not None else None,
        pnl=_from_decimal(row.get("pnl")) if row.get("pnl") is not None else None,
        adopted_from_ibkr=bool(row.get("adopted_from_ibkr", False)),
    )


def _row_to_stop_event(row: dict[str, Any]) -> StopEvent:
    return StopEvent(
        spread_id=row["spread_id"],
        ts=row["ts"],
        event=row["event"],
        spot=_from_decimal(row["spot"]),
        breakeven=_from_decimal(row["breakeven"]),
    )


class Store:
    """Thin DynamoDB wrapper for spreads + stop journal."""

    def __init__(self, table_name: str, *, region: str = "us-east-1") -> None:
        import boto3

        self._table_name = table_name
        self._region = region
        self._dynamodb = boto3.resource("dynamodb", region_name=region)
        self._table = self._dynamodb.Table(table_name)
        # Per-Store ascending counter for the stop journal sort key, ensures
        # uniqueness even if two events arrive in the same millisecond.
        self._seq = itertools.count(1)
        self._seq_lock = threading.Lock()

    # ---- spread operations -------------------------------------------------

    def record_open(
        self,
        *,
        date: str,
        symbol: str,
        long_strike: float,
        short_strike: float,
        debit: float,
        opened_at: str,
        adopted_from_ibkr: bool = False,
    ) -> str:
        from botocore.exceptions import ClientError

        item = {
            "pk": _spread_pk(date),
            "sk": symbol,
            "date": date,
            "symbol": symbol,
            "long_strike": _to_decimal(long_strike),
            "short_strike": _to_decimal(short_strike),
            "debit": _to_decimal(debit),
            "status": "OPEN",
            "opened_at": opened_at,
            "adopted_from_ibkr": adopted_from_ibkr,
        }
        try:
            self._table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise DuplicateSpreadError(
                    f"spread for {symbol} on {date} already exists"
                ) from exc
            raise
        return _spread_id(date, symbol)

    def adopt_existing_spread(
        self,
        *,
        date: str,
        symbol: str,
        long_strike: float,
        short_strike: float,
        debit: float,
        opened_at: str,
    ) -> str:
        """Insert a spread reconstructed from an existing IBKR position.

        Same shape as ``record_open`` but flagged ``adopted_from_ibkr=True``
        so the monitor knows to default the stop to ``armed=True`` (we don't
        have a journal from the prior session, but the position is real).
        """

        return self.record_open(
            date=date,
            symbol=symbol,
            long_strike=long_strike,
            short_strike=short_strike,
            debit=debit,
            opened_at=opened_at,
            adopted_from_ibkr=True,
        )

    def today_already_opened(self, date: str, symbol: str) -> bool:
        resp = self._table.get_item(
            Key={"pk": _spread_pk(date), "sk": symbol},
        )
        return "Item" in resp

    def has_trade_today(self, date: str) -> bool:
        from boto3.dynamodb.conditions import Key

        resp = self._table.query(
            KeyConditionExpression=Key("pk").eq(_spread_pk(date)),
            Limit=1,
        )
        return bool(resp.get("Items"))

    def get_spread(self, spread_id: str) -> SpreadRecord:
        date, symbol = _split_spread_id(spread_id)
        resp = self._table.get_item(Key={"pk": _spread_pk(date), "sk": symbol})
        item = resp.get("Item")
        if item is None:
            raise KeyError(spread_id)
        return _row_to_spread(item)

    def load_open_spreads_for_today(self, date: str) -> list[SpreadRecord]:
        from boto3.dynamodb.conditions import Attr, Key

        resp = self._table.query(
            KeyConditionExpression=Key("pk").eq(_spread_pk(date)),
            FilterExpression=Attr("status").eq("OPEN"),
        )
        items = sorted(resp.get("Items", []), key=lambda r: r.get("sk", ""))
        return [_row_to_spread(item) for item in items]

    def record_close(
        self,
        *,
        spread_id: str,
        closed_at: str,
        exit_kind: str,
        pnl: float,
    ) -> None:
        date, symbol = _split_spread_id(spread_id)
        # All non-settle exits (STOP, OUTAGE_FLATTEN, LEGOUT_FLATTEN) map to
        # STOPPED. The exit_kind attribute is preserved verbatim for forensics.
        status = "SETTLED" if exit_kind == "SETTLE" else "STOPPED"
        self._table.update_item(
            Key={"pk": _spread_pk(date), "sk": symbol},
            UpdateExpression=(
                "SET #s = :status, closed_at = :closed_at, "
                "exit_kind = :exit_kind, pnl = :pnl"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": status,
                ":closed_at": closed_at,
                ":exit_kind": exit_kind,
                ":pnl": _to_decimal(pnl),
            },
        )

    def record_settlement(
        self,
        *,
        spread_id: str,
        closed_at: str,
        settle_value: float,
        pnl: float,
    ) -> None:
        date, symbol = _split_spread_id(spread_id)
        self._table.update_item(
            Key={"pk": _spread_pk(date), "sk": symbol},
            UpdateExpression=(
                "SET #s = :status, closed_at = :closed_at, "
                "exit_kind = :exit_kind, settle_value = :settle, pnl = :pnl"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": "SETTLED",
                ":closed_at": closed_at,
                ":exit_kind": "SETTLE",
                ":settle": _to_decimal(settle_value),
                ":pnl": _to_decimal(pnl),
            },
        )

    # ---- stop journal ------------------------------------------------------

    def record_stop_event(
        self,
        *,
        spread_id: str,
        ts: str,
        event: str,
        spot: float,
        breakeven: float,
    ) -> None:
        with self._seq_lock:
            seq = next(self._seq)
        sk = f"{ts}#{seq:06d}"
        self._table.put_item(
            Item={
                "pk": _stop_pk(spread_id),
                "sk": sk,
                "spread_id": spread_id,
                "ts": ts,
                "event": event,
                "spot": _to_decimal(spot),
                "breakeven": _to_decimal(breakeven),
            },
        )

    # ---- monthly capital gate ---------------------------------------------

    def monthly_pnl_total(self, year_month: str) -> float:
        """Sum realized pnl across all closed spreads in the given month.

        ``year_month`` is "YYYY-MM" (e.g. "2026-04"). Only STOPPED and
        SETTLED rows contribute — OPEN rows have no realized pnl yet.

        Used by the scheduler's monthly net-negative capital gate (R9).
        Implemented as a single Scan with a server-side filter; for our
        volume (~22 rows/month, ~250/year) this is cheap.
        """

        if not _YEAR_MONTH_RE.match(year_month):
            raise ValueError(
                f"year_month must be 'YYYY-MM' with 01..12; got {year_month!r}",
            )

        from boto3.dynamodb.conditions import Attr

        prefix = f"SPREAD#{year_month}-"
        filter_expr = (
            Attr("pk").begins_with(prefix)
            & Attr("status").is_in(["STOPPED", "SETTLED"])
            & Attr("pnl").exists()
        )

        total = 0.0
        last_key: dict[str, Any] | None = None
        while True:
            kwargs: dict[str, Any] = {"FilterExpression": filter_expr}
            if last_key is not None:
                kwargs["ExclusiveStartKey"] = last_key
            resp = self._table.scan(**kwargs)
            for item in resp.get("Items", []):
                pnl = item.get("pnl")
                if pnl is None:
                    continue
                total += float(pnl)
            last_key = resp.get("LastEvaluatedKey")
            if last_key is None:
                break
        return total

    def stop_events(self, spread_id: str) -> list[StopEvent]:
        from boto3.dynamodb.conditions import Key

        resp = self._table.query(
            KeyConditionExpression=Key("pk").eq(_stop_pk(spread_id)),
        )
        items = resp.get("Items", [])
        return [_row_to_stop_event(item) for item in items]

    # ---- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        # boto3 resources don't need explicit close; method kept for API
        # compatibility with the prior SQLite-based Store.
        pass


def _split_spread_id(spread_id: str) -> tuple[str, str]:
    date, _, symbol = spread_id.partition("#")
    if not symbol:
        raise ValueError(f"invalid spread_id: {spread_id!r}")
    return date, symbol
