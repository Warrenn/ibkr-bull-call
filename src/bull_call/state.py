"""SQLite-backed persistence for spreads and the stop journal."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spreads (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    date         TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    long_strike  REAL    NOT NULL,
    short_strike REAL    NOT NULL,
    debit        REAL    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'OPEN',
    opened_at    TEXT    NOT NULL,
    closed_at    TEXT,
    exit_kind    TEXT,
    settle_value REAL,
    pnl          REAL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_spreads_date_symbol
    ON spreads (date, symbol);

CREATE TABLE IF NOT EXISTS stop_journal (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    spread_id INTEGER NOT NULL REFERENCES spreads(id),
    ts        TEXT    NOT NULL,
    event     TEXT    NOT NULL,
    spot      REAL    NOT NULL,
    breakeven REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_stop_journal_spread
    ON stop_journal (spread_id, id);
"""


class DuplicateSpreadError(RuntimeError):
    """Raised when a spread already exists for (date, symbol)."""


@dataclass(frozen=True, slots=True)
class SpreadRecord:
    id: int
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


@dataclass(frozen=True, slots=True)
class StopEvent:
    spread_id: int
    ts: str
    event: str
    spot: float
    breakeven: float


def _row_to_spread(row: sqlite3.Row) -> SpreadRecord:
    return SpreadRecord(
        id=row["id"],
        date=row["date"],
        symbol=row["symbol"],
        long_strike=row["long_strike"],
        short_strike=row["short_strike"],
        debit=row["debit"],
        status=row["status"],
        opened_at=row["opened_at"],
        closed_at=row["closed_at"],
        exit_kind=row["exit_kind"],
        settle_value=row["settle_value"],
        pnl=row["pnl"],
    )


def _row_to_stop_event(row: sqlite3.Row) -> StopEvent:
    return StopEvent(
        spread_id=row["spread_id"],
        ts=row["ts"],
        event=row["event"],
        spot=row["spot"],
        breakeven=row["breakeven"],
    )


class Store:
    """Thin sqlite3 wrapper for spreads + stop journal."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        # journal_mode=DELETE (default) instead of WAL, because the production
        # state directory is on EFS/NFS where WAL has known correctness issues.
        self._conn.execute("PRAGMA journal_mode = DELETE;")
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def record_open(
        self,
        *,
        date: str,
        symbol: str,
        long_strike: float,
        short_strike: float,
        debit: float,
        opened_at: str,
    ) -> int:
        try:
            cur = self._conn.execute(
                """
                INSERT INTO spreads (date, symbol, long_strike, short_strike, debit, opened_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (date, symbol, long_strike, short_strike, debit, opened_at),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateSpreadError(
                f"spread for {symbol} on {date} already exists"
            ) from exc
        assert cur.lastrowid is not None
        return cur.lastrowid

    def today_already_opened(self, date: str, symbol: str) -> bool:
        """True if any spread (regardless of status) exists for (date, symbol).

        This is the idempotency primitive that prevents double-opens after an
        EC2 replacement: as long as the SQLite file persists across instance
        replacement (via EFS), a freshly-launched bot sees the existing record
        and does not re-enter.
        """

        row = self._conn.execute(
            "SELECT 1 FROM spreads WHERE date = ? AND symbol = ? LIMIT 1",
            (date, symbol),
        ).fetchone()
        return row is not None

    def has_trade_today(self, date: str) -> bool:
        """True if at least one spread (any symbol, any status) exists for ``date``.

        Used as a positive assertion that the bot's daily entry cycle ran:
        after entry time has passed, this should be True for every trading day.
        """

        row = self._conn.execute(
            "SELECT 1 FROM spreads WHERE date = ? LIMIT 1",
            (date,),
        ).fetchone()
        return row is not None

    def get_spread(self, spread_id: int) -> SpreadRecord:
        row = self._conn.execute(
            "SELECT * FROM spreads WHERE id = ?", (spread_id,)
        ).fetchone()
        if row is None:
            raise KeyError(spread_id)
        return _row_to_spread(row)

    def load_open_spreads_for_today(self, date: str) -> list[SpreadRecord]:
        rows = self._conn.execute(
            "SELECT * FROM spreads WHERE date = ? AND status = 'OPEN' ORDER BY id",
            (date,),
        ).fetchall()
        return [_row_to_spread(r) for r in rows]

    def record_stop_event(
        self,
        *,
        spread_id: int,
        ts: str,
        event: str,
        spot: float,
        breakeven: float,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO stop_journal (spread_id, ts, event, spot, breakeven)
            VALUES (?, ?, ?, ?, ?)
            """,
            (spread_id, ts, event, spot, breakeven),
        )

    def stop_events(self, spread_id: int) -> list[StopEvent]:
        rows = self._conn.execute(
            "SELECT * FROM stop_journal WHERE spread_id = ? ORDER BY id",
            (spread_id,),
        ).fetchall()
        return [_row_to_stop_event(r) for r in rows]

    def record_close(
        self,
        *,
        spread_id: int,
        closed_at: str,
        exit_kind: str,
        pnl: float,
    ) -> None:
        status = "STOPPED" if exit_kind == "STOP" else "SETTLED"
        self._conn.execute(
            """
            UPDATE spreads
            SET status = ?, closed_at = ?, exit_kind = ?, pnl = ?
            WHERE id = ?
            """,
            (status, closed_at, exit_kind, pnl, spread_id),
        )

    def record_settlement(
        self,
        *,
        spread_id: int,
        closed_at: str,
        settle_value: float,
        pnl: float,
    ) -> None:
        self._conn.execute(
            """
            UPDATE spreads
            SET status = 'SETTLED', closed_at = ?, exit_kind = 'SETTLE',
                settle_value = ?, pnl = ?
            WHERE id = ?
            """,
            (closed_at, settle_value, pnl, spread_id),
        )
