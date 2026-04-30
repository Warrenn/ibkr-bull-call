"""Replay-style simulator for the breakeven stop.

Drives the same `monitor_stop` state machine the live bot uses, but against
synthetic or CSV-loaded tick streams. Useful for:

  - Confirming the stop fires when SPX touches/crosses below the breakeven.
  - Confirming the stop is suppressed inside the last STOP_LATEST_SEC window.
  - Confirming the uneconomic guard skips closing when the spread is worth ~0.
  - Replaying historical SPX 1-minute or 1-second prints from a CSV.

Usage:
  python -m bull_call.simulate --long 4995 --short 5005 --debit 5.50
  python -m bull_call.simulate --long 4995 --short 5005 --debit 5.50 --scenario suppress
  python -m bull_call.simulate --long 4995 --short 5005 --debit 5.50 \\
      --ticks-csv path/to/spx_intraday.csv

CSV format: two columns ``timestamp_utc, spot``. Header optional.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import logging
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

from bull_call.config import Settings
from bull_call.execution import FillReport
from bull_call.state import SpreadRecord, StopEvent
from bull_call.strategy import StopOutcome, monitor_stop


class _InMemoryStore:
    """A minimal in-process Store-compatible object for the simulator.

    Avoids spinning up DynamoDB (or moto) just to demonstrate state-machine
    behaviour from a script.
    """

    def __init__(self) -> None:
        self._spreads: dict[str, SpreadRecord] = {}
        self._events: dict[str, list[StopEvent]] = {}

    def record_open(
        self, *, date: str, symbol: str, long_strike: float, short_strike: float,
        debit: float, opened_at: str, **_kw: object,
    ) -> str:
        sid = f"{date}#{symbol}"
        self._spreads[sid] = SpreadRecord(
            id=sid, date=date, symbol=symbol,
            long_strike=long_strike, short_strike=short_strike, debit=debit,
            status="OPEN", opened_at=opened_at,
            closed_at=None, exit_kind=None, settle_value=None, pnl=None,
        )
        return sid

    def record_stop_event(
        self, *, spread_id: str, ts: str, event: str, spot: float, breakeven: float,
    ) -> None:
        self._events.setdefault(spread_id, []).append(
            StopEvent(spread_id=spread_id, ts=ts, event=event,
                      spot=spot, breakeven=breakeven),
        )

    def stop_events(self, spread_id: str) -> list[StopEvent]:
        return list(self._events.get(spread_id, []))

    def get_spread(self, spread_id: str) -> SpreadRecord:
        return self._spreads[spread_id]

    def record_close(
        self, *, spread_id: str, closed_at: str, exit_kind: str, pnl: float,
    ) -> None:
        rec = self._spreads[spread_id]
        status = "STOPPED" if exit_kind == "STOP" else "SETTLED"
        self._spreads[spread_id] = replace(
            rec, status=status, closed_at=closed_at, exit_kind=exit_kind, pnl=pnl,
        )

log = logging.getLogger(__name__)


def _settings(stop_latest_sec: int) -> Settings:
    return Settings(
        ib_host="-", ib_port=0, ib_client_id=0,
        symbols=("SPX",),
        max_loss_usd=10_000.0,
        pop_threshold=0.50,
        risk_free_rate=0.05,
        entry_time_et=dt.time(10, 30),
        stop_enabled=True,
        stop_latest_sec=stop_latest_sec,
        state_table="bull-call-test",
        log_level="INFO",
    )


def synthetic_ticks(
    breakeven: float, *, scenario: str, close_utc: dt.datetime,
) -> list[tuple[float, dt.datetime]]:
    """Build a known-shape tick series for one of the named scenarios."""

    open_utc = close_utc - dt.timedelta(hours=5, minutes=30)
    if scenario == "fire":
        # Spot rises above breakeven (arms), then crosses back down (FIRE).
        return [
            (breakeven - 1.50, open_utc + dt.timedelta(minutes=2)),
            (breakeven - 0.50, open_utc + dt.timedelta(minutes=5)),
            (breakeven + 0.10, open_utc + dt.timedelta(minutes=15)),  # ARM
            (breakeven + 1.20, open_utc + dt.timedelta(minutes=30)),
            (breakeven + 0.30, open_utc + dt.timedelta(minutes=90)),
            (breakeven - 0.20, open_utc + dt.timedelta(minutes=120)),  # FIRE
        ]
    if scenario == "touch":
        # Spot rises just to breakeven (arm), then drops by a single tick (FIRE).
        return [
            (breakeven - 0.50, open_utc + dt.timedelta(minutes=2)),
            (breakeven, open_utc + dt.timedelta(minutes=10)),  # ARM (>= breakeven)
            (breakeven - 0.05, open_utc + dt.timedelta(minutes=11)),  # FIRE (one tick below)
        ]
    if scenario == "never_arm":
        # Spot stays underwater the whole session — no arm, no fire.
        return [
            (breakeven - 5.0, open_utc + dt.timedelta(minutes=2)),
            (breakeven - 6.0, open_utc + dt.timedelta(minutes=60)),
            (breakeven - 7.0, open_utc + dt.timedelta(minutes=240)),
        ]
    if scenario == "suppress":
        # Cross up early, cross back down inside the last 30s — SUPPRESS.
        return [
            (breakeven + 1.0, open_utc + dt.timedelta(minutes=30)),  # ARM
            (breakeven - 0.50, close_utc - dt.timedelta(seconds=10)),  # SUPPRESS
        ]
    if scenario == "uneconomic":
        # Same shape as 'fire' but the close-credit estimator returns 0 — UNECONOMIC.
        return synthetic_ticks(breakeven, scenario="fire", close_utc=close_utc)
    raise ValueError(f"unknown scenario: {scenario}")


def ticks_from_csv(path: Path) -> list[tuple[float, dt.datetime]]:
    """Load ticks from a 2-column CSV (timestamp_utc, spot)."""

    out: list[tuple[float, dt.datetime]] = []
    with path.open("r", newline="") as fh:
        reader = csv.reader(fh)
        for row in reader:
            if not row or len(row) < 2:
                continue
            ts_raw, spot_raw = row[0].strip(), row[1].strip()
            try:
                spot = float(spot_raw)
            except ValueError:
                continue  # likely a header row
            try:
                ts = dt.datetime.fromisoformat(ts_raw)
            except ValueError:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=dt.timezone.utc)
            out.append((spot, ts))
    if not out:
        raise ValueError(f"no usable rows parsed from {path}")
    out.sort(key=lambda r: r[1])
    return out


def run(
    *,
    long_strike: float,
    short_strike: float,
    debit: float,
    exit_credit: float,
    ticks: list[tuple[float, dt.datetime]],
    close_utc: dt.datetime | None = None,
    stop_latest_sec: int = 30,
    estimate_zero_credit: bool = False,
) -> int:
    """Run one simulation; returns 0 on success."""

    breakeven = long_strike + debit
    # If close_utc isn't explicit (CSV mode), put it just past the last tick.
    if close_utc is None:
        close_utc = max(t for _, t in ticks) + dt.timedelta(minutes=5)
    settings = _settings(stop_latest_sec=stop_latest_sec)

    print(f"  scenario summary")
    print(f"    long={long_strike}  short={short_strike}  debit={debit:.2f}")
    print(f"    breakeven = long + debit = {breakeven:.2f}")
    print(f"    exit_credit (simulated MKT close fill) = {exit_credit:.2f}")
    print(f"    close_utc = {close_utc.isoformat()}  "
          f"(suppress window = last {stop_latest_sec}s)")
    print()

    store = _InMemoryStore()
    sid = store.record_open(
        date=ticks[0][1].date().isoformat(),
        symbol="SPX",
        long_strike=long_strike,
        short_strike=short_strike,
        debit=debit,
        opened_at=ticks[0][1].isoformat(),
    )

    observed: list[tuple[dt.datetime, float]] = []

    def tick_stream() -> Iterator[tuple[float, dt.datetime]]:
        for spot, t in ticks:
            observed.append((t, spot))
            yield spot, t

    def submit_close(**_: object) -> FillReport:
        return FillReport(filled=True, avg_fill_price=exit_credit)

    def estimate_credit() -> float:
        return 0.0 if estimate_zero_credit else exit_credit

    outcome = monitor_stop(
        store,                              # type: ignore[arg-type]  # _InMemoryStore is duck-compatible
        spread_id=sid,
        breakeven=breakeven,
        settings=settings,
        close_utc=close_utc,
        tick_stream=tick_stream(),
        submit_close=submit_close,
        estimate_close_credit=estimate_credit,
    )

    print("  ticks consumed:")
    for ts, spot in observed:
        rel = "above" if spot >= breakeven else "below"
        print(f"    {ts.isoformat()}  spot={spot:>9.2f}  ({rel} breakeven)")
    print()

    events = store.stop_events(sid)
    print(f"  stop journal: {len(events)} event(s)")
    for evt in events:
        print(f"    {evt.ts}  {evt.event:<12}  spot={evt.spot:.2f}")
    print()

    rec = store.get_spread(sid)
    print(f"  outcome           = {outcome.name}")
    print(f"  spread.status     = {rec.status}")
    print(f"  spread.exit_kind  = {rec.exit_kind}")
    if rec.pnl is not None:
        print(f"  realized P&L      = ${rec.pnl:+.2f}")
    else:
        print(f"  realized P&L      = (would be settled at 4pm)")
    max_held_loss = -debit * 100.0
    print(f"  max-loss-if-held  = ${max_held_loss:+.2f}  (a fully OTM expiry)")

    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bull_call.simulate")
    p.add_argument("--long", type=float, required=True, dest="long_strike")
    p.add_argument("--short", type=float, required=True, dest="short_strike")
    p.add_argument("--debit", type=float, required=True)
    p.add_argument("--exit-credit", type=float, default=0.50,
                   help="What a SELL combo MKT would fill at (drives P&L)")
    p.add_argument("--scenario",
                   choices=["fire", "touch", "never_arm", "suppress", "uneconomic"],
                   default="fire")
    p.add_argument("--ticks-csv", type=Path, help="CSV with timestamp_utc,spot rows")
    p.add_argument("--stop-latest-sec", type=int, default=30)
    args = p.parse_args(argv)

    explicit_close: dt.datetime | None = None
    if args.ticks_csv:
        ticks = ticks_from_csv(args.ticks_csv)
    else:
        breakeven = args.long_strike + args.debit
        # Use a fixed reference close for synthetic scenarios so timing is deterministic.
        explicit_close = dt.datetime(2026, 4, 29, 20, 0, tzinfo=dt.timezone.utc)
        ticks = synthetic_ticks(breakeven, scenario=args.scenario, close_utc=explicit_close)

    return run(
        long_strike=args.long_strike,
        short_strike=args.short_strike,
        debit=args.debit,
        exit_credit=args.exit_credit,
        ticks=ticks,
        close_utc=explicit_close,
        stop_latest_sec=args.stop_latest_sec,
        estimate_zero_credit=(args.scenario == "uneconomic"),
    )


if __name__ == "__main__":
    sys.exit(main())
