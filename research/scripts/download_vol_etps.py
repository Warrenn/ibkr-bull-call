"""Download VXX, SVXY, ^VIX, ^VIX3M daily Close from yfinance.

Used by v8 vol term structure carry strategy.

Output schema: ``date`` + 4 columns (VXX, SVXY, VIX, VIX3M).

Note: ^VIX3M on yfinance covers data from ~2007 onward.

Usage::

    uv run python -m research.scripts.download_vol_etps \\
        --start 2018-01-01 --end 2026-04-30 \\
        --output research/data/dataset-v1/vol_etps_daily.parquet
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import sys
from pathlib import Path

import pandas as pd


_TICKERS = ("VXX", "SVXY", "^VIX", "^VIX3M")
_OUTPUT_COLS = ("VXX", "SVXY", "VIX", "VIX3M")


def download(
    *,
    start: dt.date,
    end: dt.date,
    output: Path,
) -> pd.DataFrame:
    if start > end:
        raise ValueError(f"start ({start}) must be on or before end ({end})")
    if output.exists():
        raise FileExistsError(
            f"output exists: {output} (refusing to overwrite a pinned artifact)",
        )

    import yfinance as yf

    raw = yf.download(
        list(_TICKERS),
        start=start.isoformat(),
        end=(end + dt.timedelta(days=1)).isoformat(),
        interval="1d",
        auto_adjust=True,
        progress=False,
    )
    closes = raw["Close"]
    if closes.empty:
        raise RuntimeError(f"yfinance returned empty for {_TICKERS}")

    closes = closes.reset_index()
    date_col = next(c for c in closes.columns if c in ("Date", "Datetime"))
    closes["date"] = (
        closes[date_col].dt.tz_localize(None).dt.date
        if hasattr(closes[date_col].dt, "tz_localize")
        else closes[date_col].dt.date
    )
    # Rename ^VIX → VIX, ^VIX3M → VIX3M
    rename_map = {"^VIX": "VIX", "^VIX3M": "VIX3M"}
    closes = closes.rename(columns=rename_map)
    closes = closes[["date"] + list(_OUTPUT_COLS)]
    closes = closes[(closes["date"] >= start) & (closes["date"] <= end)]
    closes = closes.sort_values("date").reset_index(drop=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    closes.to_parquet(output, engine="pyarrow", compression="snappy", index=False)
    return closes


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="download_vol_etps")
    p.add_argument("--start", type=dt.date.fromisoformat, required=True)
    p.add_argument("--end", type=dt.date.fromisoformat, required=True)
    p.add_argument("--output", type=Path, required=True)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    df = download(start=args.start, end=args.end, output=args.output)
    digest = _sha256_of(args.output)
    print(f"wrote {len(df)} rows -> {args.output}")
    print(f"sha256: {digest}")
    print(f"date_range: {df['date'].min()} .. {df['date'].max()}")
    print(f"tickers: {[c for c in df.columns if c != 'date']}")
    non_null = df.dropna()
    if not non_null.empty:
        print(f"all-tickers available from: {non_null['date'].min()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
