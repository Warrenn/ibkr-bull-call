"""Pull SPX 1-minute TRADES history from IBKR Client Portal Web API.

Walks backward from ``--end`` in ``--chunk-period`` chunks (default
``2w``), pacing requests at ``--pace-sec`` to stay under IBKR's
60-req / 10-min rolling rate limit. Stops when:

1. A response returns 0 bars (depth exhausted upstream), OR
2. The cursor walks past ``--start``.

Output: Parquet with ``ts_utc`` + OHLCV columns. SHA256 printed for
manifest pinning.

Note: indices on IBKR use ``whatToShow=TRADES`` (not BID/ASK).
``BID_ASK`` doubles the pacing budget; ``TRADES`` does not. Practical
historical depth for SPX 1-min is roughly 1-2 years per community
reports (the agent estimate of 36mo may not fully fill).

Usage::

    uv run python -m research.scripts.download_ibkr_spx \\
        --start 2023-04-30 --end 2026-04-30 \\
        --output research/data/dataset-v1/spx_spot_intraday.parquet

Requires:

- IBeam / Client Portal Gateway running locally on
  ``https://localhost:5000`` and authenticated (2FA approved).
- CME S&P Indexes Bundle L1 market-data subscription (~$10/mo).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import logging
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScrapeResult:
    row_count: int
    actual_start: dt.datetime
    actual_end: dt.datetime
    sha256: str
    output_path: Path


def _bars_to_df(bars: list[dict[str, Any]]) -> pd.DataFrame:
    """Convert IBKR ``data`` list of bar dicts to a clean DataFrame.

    IBKR returns ``t`` (epoch ms), ``o``, ``h``, ``l``, ``c``, ``v``;
    rename to a downstream-friendly schema with explicit UTC.
    """

    if not bars:
        return pd.DataFrame(
            columns=["ts_utc", "open", "high", "low", "close", "volume"],
        )
    df = pd.DataFrame(bars)
    df["ts_utc"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    return df[["ts_utc", "o", "h", "l", "c", "v"]].rename(
        columns={
            "o": "open",
            "h": "high",
            "l": "low",
            "c": "close",
            "v": "volume",
        },
    )


def _find_spx_conid(client: Any) -> int:
    """Resolve the SPX index conid via ``search_contract_by_symbol``.

    SPX is an index (``secType=IND``), not a stock. Mirrors the lookup
    in ``bull_call.cpapi.chain``.
    """

    search = client.search_contract_by_symbol(symbol="SPX", sec_type="IND")
    matches = search.data
    if not matches:
        raise RuntimeError(
            "no SPX contract match returned by gateway — "
            "is CME S&P Indexes Bundle L1 subscribed?",
        )
    return int(matches[0]["conid"])


def scrape(
    *,
    client: Any,
    start: dt.date,
    end: dt.date,
    output: Path,
    chunk_period: str = "2w",
    pace_sec: float = 12.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> ScrapeResult:
    """Walk backward chunked-by-``chunk_period`` from ``end`` to ``start``.

    Raises in priority order:
    - ``ValueError`` if ``start > end``
    - ``FileExistsError`` if ``output`` already exists
    - ``RuntimeError`` if no bars are retrieved at all (gateway issue,
      missing subscription, etc.)
    """

    if start > end:
        raise ValueError(
            f"start ({start}) must be on or before end ({end})",
        )
    if output.exists():
        raise FileExistsError(
            f"output exists: {output} (refusing to overwrite a "
            "sha256-pinned artifact; delete it first if a re-pull is intended)",
        )

    conid = _find_spx_conid(client)
    log.info("resolved SPX conid: %d", conid)

    chunks: list[pd.DataFrame] = []
    cursor = dt.datetime.combine(end, dt.time(23, 59), tzinfo=dt.timezone.utc)
    start_dt = dt.datetime.combine(start, dt.time(0, 0), tzinfo=dt.timezone.utc)

    request_count = 0
    while cursor > start_dt:
        if request_count > 0:
            sleep_fn(pace_sec)
        request_count += 1
        log.info(
            "request %d: chunk ending %s (period=%s)",
            request_count, cursor.isoformat(), chunk_period,
        )

        resp = client.marketdata_history_by_conid(
            conid=str(conid),
            bar="1min",
            period=chunk_period,
            outside_rth=False,
            start_time=cursor,
        )
        bars = resp.data.get("data", [])
        if not bars:
            log.info(
                "empty response — IBKR depth exhausted at %s",
                cursor.isoformat(),
            )
            break

        df = _bars_to_df(bars)
        chunks.append(df)
        log.info(
            "chunk %d: %d bars, range %s -> %s",
            request_count, len(df),
            df["ts_utc"].min().isoformat(),
            df["ts_utc"].max().isoformat(),
        )

        # Walk cursor to just before this chunk's earliest bar so the
        # next request fetches the prior window. ``min - 1m`` defends
        # against off-by-one duplicate bars on chunk boundaries; the
        # later sort + dedupe is the belt that catches anything left.
        oldest_ts = df["ts_utc"].min()
        cursor = (oldest_ts - pd.Timedelta(minutes=1)).to_pydatetime()

    if not chunks:
        raise RuntimeError(
            "no data retrieved from IBKR — gateway returned empty on the "
            "first request. Check authentication, subscription, and that "
            "the SPX ticker is correctly resolved.",
        )

    combined = (
        pd.concat(chunks, ignore_index=True)
        .drop_duplicates(subset=["ts_utc"])
        .sort_values("ts_utc")
        .reset_index(drop=True)
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(
        output,
        engine="pyarrow",
        compression="snappy",
        index=False,
    )

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return ScrapeResult(
        row_count=len(combined),
        actual_start=combined["ts_utc"].min().to_pydatetime(),
        actual_end=combined["ts_utc"].max().to_pydatetime(),
        sha256=digest,
        output_path=output,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="download_ibkr_spx")
    p.add_argument(
        "--start", type=dt.date.fromisoformat, required=True,
        help="Inclusive start date (best-effort; IBKR depth limits may truncate)",
    )
    p.add_argument(
        "--end", type=dt.date.fromisoformat, required=True,
        help="Inclusive end date",
    )
    p.add_argument(
        "--output", type=Path, required=True,
        help="Output Parquet path",
    )
    p.add_argument(
        "--chunk-period", default="2w",
        help="IBKR period per request (default: 2w; smaller = more requests, "
             "fewer dropped bars on chunk-boundary)",
    )
    p.add_argument(
        "--pace-sec", type=float, default=12.0,
        help="Seconds between requests (default: 12 = 50 req/10min, under the "
             "60/10min IBKR cap)",
    )
    p.add_argument(
        "--gateway-url", default="https://localhost:5000/v1/api",
        help="IBeam gateway base URL",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.start > args.end:
        raise ValueError(
            f"start ({args.start}) must be on or before end ({args.end})",
        )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    from bull_call.cpapi.client import GatewayConfig, connect, disconnect

    config = GatewayConfig(base_url=args.gateway_url)
    client = connect(config)
    try:
        result = scrape(
            client=client,
            start=args.start,
            end=args.end,
            output=args.output,
            chunk_period=args.chunk_period,
            pace_sec=args.pace_sec,
        )
    finally:
        disconnect(client)

    print(f"wrote {result.row_count} rows -> {result.output_path}")
    print(
        f"actual range: {result.actual_start.isoformat()} -> "
        f"{result.actual_end.isoformat()}",
    )
    print(f"sha256: {result.sha256}")
    print()
    print("Manifest snippet (paste under spx_spot_intraday):")
    print(f'  "date_range": {{"start": "{result.actual_start.date().isoformat()}", '
          f'"end": "{result.actual_end.date().isoformat()}"}},')
    print(f'  "row_count": {result.row_count},')
    print(f'  "checksum": "sha256:{result.sha256}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
