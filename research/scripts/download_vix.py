"""Download daily VIX from Yahoo Finance for v3's regime gate.

VIX (CBOE Volatility Index) is the standard "fear gauge" for SPX
implied volatility. Daily values are sufficient for a regime gate
(use the prior day's close — known at signal time, conventional
indicator). Tier 2 intraday VIX is available via Databento (OPRA)
but we don't need it yet; v3 starts with the cheap option.

Output schema (matches what the v3 sweep loader expects):

- ``date`` (date)
- ``open``, ``high``, ``low``, ``close`` (float, VIX index points)
- ``volume`` (int) — Yahoo always returns 0 for indices; included
  for schema-consistency but ignored downstream

Reproducibility caveat: Yahoo can revise data (rare for indices but
possible). The manifest pins the sha256 of the parquet at acquisition
time; if a re-pull produces a different sha256 we know the upstream
data drifted and the parquet is then a different ``dataset-v2``
input.

Usage::

    uv run python -m research.scripts.download_vix \\
        --start 2023-04-30 --end 2026-04-30 \\
        --output research/data/dataset-v1/vix_daily.parquet
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import sys
from pathlib import Path

import pandas as pd


def download(
    *,
    start: dt.date,
    end: dt.date,
    output: Path,
    ticker: str = "^VIX",
) -> pd.DataFrame:
    """Pull daily VIX bars from yfinance, normalize, save Parquet.

    Returns the saved DataFrame for tests / inspection.
    """

    if start > end:
        raise ValueError(
            f"start ({start}) must be on or before end ({end})",
        )
    if output.exists():
        raise FileExistsError(
            f"output exists: {output} (refusing to overwrite a "
            "sha256-pinned artifact)",
        )

    import yfinance as yf

    # yfinance ``end`` is exclusive — pad by one day so we include
    # the requested end date.
    raw = yf.Ticker(ticker).history(
        start=start.isoformat(),
        end=(end + dt.timedelta(days=1)).isoformat(),
        interval="1d",
        auto_adjust=False,
    )
    if raw.empty:
        raise RuntimeError(
            f"yfinance returned empty data for {ticker} {start} → {end}; "
            "check ticker symbol and network",
        )

    # raw.index is a tz-aware DatetimeIndex (US/Eastern). Convert to
    # date for our schema.
    df = raw.reset_index()
    # yfinance variant: column may be "Date" or "Datetime"
    date_col = next(c for c in df.columns if c in ("Date", "Datetime"))
    df["date"] = df[date_col].dt.tz_localize(None).dt.date
    df = df[["date", "Open", "High", "Low", "Close", "Volume"]].rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        },
    )
    # Trim to inclusive [start, end]
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    df = df.sort_values("date").reset_index(drop=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output, engine="pyarrow", compression="snappy", index=False)
    return df


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="download_vix")
    p.add_argument("--start", type=dt.date.fromisoformat, required=True)
    p.add_argument("--end", type=dt.date.fromisoformat, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--ticker", default="^VIX",
                   help="Yahoo ticker (default: ^VIX)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    df = download(
        start=args.start,
        end=args.end,
        output=args.output,
        ticker=args.ticker,
    )
    digest = _sha256_of(args.output)
    print(f"wrote {len(df)} rows -> {args.output}")
    print(f"sha256: {digest}")
    print(f"date_range: {df['date'].min()} .. {df['date'].max()}")
    print(f"VIX close range: {df['close'].min():.2f} .. {df['close'].max():.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
