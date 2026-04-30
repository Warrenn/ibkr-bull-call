"""Download 11 SPDR sector ETFs + SPY benchmark via yfinance.

Used by v9 sector ETF momentum strategy. Only Close (auto-adjusted
for splits + dividends) is needed.

Universe (Select Sector SPDR family):
- XLK (Technology), XLF (Financials), XLE (Energy)
- XLV (Health Care), XLY (Consumer Discretionary), XLP (Consumer Staples)
- XLI (Industrials), XLB (Materials), XLU (Utilities)
- XLRE (Real Estate, listed 2015-10), XLC (Communication Services, listed 2018-06)
- SPY (S&P 500 benchmark)

Output schema: ``date`` + 12 columns (one per ticker, adjusted close).

Usage::

    uv run python -m research.scripts.download_sector_etfs \\
        --start 2016-01-01 --end 2026-04-30 \\
        --output research/data/dataset-v1/sector_etfs_daily.parquet
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import sys
from pathlib import Path

import pandas as pd


_DEFAULT_TICKERS = (
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP",
    "XLI", "XLB", "XLU", "XLRE", "XLC", "SPY",
)


def download(
    *,
    start: dt.date,
    end: dt.date,
    output: Path,
    tickers: tuple[str, ...] = _DEFAULT_TICKERS,
) -> pd.DataFrame:
    if start > end:
        raise ValueError(f"start ({start}) must be on or before end ({end})")
    if output.exists():
        raise FileExistsError(
            f"output exists: {output} (refusing to overwrite a pinned artifact)",
        )

    import yfinance as yf

    raw = yf.download(
        list(tickers),
        start=start.isoformat(),
        end=(end + dt.timedelta(days=1)).isoformat(),
        interval="1d",
        auto_adjust=True,
        progress=False,
    )
    closes = raw["Close"]
    if closes.empty:
        raise RuntimeError(f"yfinance returned empty for {tickers}")

    closes = closes.reset_index()
    # yfinance sometimes returns "Date" or "Datetime"; normalize to "date"
    date_col = next(c for c in closes.columns if c in ("Date", "Datetime"))
    closes["date"] = closes[date_col].dt.tz_localize(None).dt.date if hasattr(closes[date_col].dt, "tz_localize") else closes[date_col].dt.date
    closes = closes[["date"] + list(tickers)]
    closes = closes[(closes["date"] >= start) & (closes["date"] <= end)]
    closes = closes.sort_values("date").reset_index(drop=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    closes.to_parquet(output, engine="pyarrow", compression="snappy", index=False)
    return closes


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="download_sector_etfs")
    p.add_argument("--start", type=dt.date.fromisoformat, required=True)
    p.add_argument("--end", type=dt.date.fromisoformat, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--tickers", nargs="+", default=list(_DEFAULT_TICKERS))
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    df = download(
        start=args.start, end=args.end,
        output=args.output, tickers=tuple(args.tickers),
    )
    digest = _sha256_of(args.output)
    print(f"wrote {len(df)} rows -> {args.output}")
    print(f"sha256: {digest}")
    print(f"date_range: {df['date'].min()} .. {df['date'].max()}")
    print(f"tickers: {[c for c in df.columns if c != 'date']}")
    # Show first all-non-null date (when full universe is available)
    non_null = df.dropna()
    if not non_null.empty:
        print(f"all-tickers available from: {non_null['date'].min()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
