"""Download a Databento dataset to a Parquet file under ``research/data/``.

Three guards run before the account is billed:

1. ``metadata.get_cost`` first; abort if estimated cost > ``--max-cost``.
2. Output path must not exist (refuse to overwrite a committed
   sha256-pinned artifact).
3. Inverted date range rejected before any API call.

Then the data is streamed via ``timeseries.get_range``, the DBN
response is converted to a pandas DataFrame, written to Parquet with
deterministic settings (``pyarrow`` engine, ``snappy`` compression,
``index=False``), and SHA256 of the output is computed. The result
prints a manifest snippet for the operator to paste into
``research/data/manifest.json``.

Usage::

    DATABENTO_API_KEY=db-... uv run python -m research.scripts.download_databento \\
        --dataset GLBX.MDP3 --schema ohlcv-1m \\
        --symbols ES.c.0 --stype-in continuous \\
        --start 2023-04-30 --end 2026-04-30 \\
        --output research/data/dataset-v1/es_intraday.parquet \\
        --max-cost 10.00

Run ``research.scripts.estimate_databento_cost`` first to know what
``--max-cost`` ceiling to set; the cap should be comfortably above
the estimated cost but below "oh no".
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DownloadResult:
    """Successful download summary — used to update the manifest."""

    cost: float
    row_count: int
    sha256: str
    output_path: Path


def download(
    *,
    dataset: str,
    schema: str,
    symbols: tuple[str, ...],
    stype_in: str,
    start: dt.date,
    end: dt.date,
    output: Path,
    max_cost: float,
) -> DownloadResult:
    """Download a single dataset/schema/symbol-set to Parquet.

    Raises in priority order:
    - ``ValueError`` if ``start > end``
    - ``FileExistsError`` if ``output`` already exists
    - ``RuntimeError`` if ``DATABENTO_API_KEY`` is unset
    - ``RuntimeError`` if estimated cost > ``max_cost``
    """

    if start > end:
        raise ValueError(
            f"start ({start}) must be on or before end ({end})",
        )
    if output.exists():
        raise FileExistsError(
            f"output exists: {output} (refusing to overwrite a pinned artifact; "
            "delete it explicitly first if a re-pull is intended)",
        )

    import databento as db

    api_key = os.environ.get("DATABENTO_API_KEY")
    if not api_key:
        raise RuntimeError(
            "DATABENTO_API_KEY environment variable not set. "
            "Generate a key at https://databento.com/portal/keys",
        )

    client = db.Historical(key=api_key)

    cost = float(client.metadata.get_cost(
        dataset=dataset,
        schema=schema,
        symbols=list(symbols),
        stype_in=stype_in,
        start=start.isoformat(),
        end=end.isoformat(),
    ))
    if cost > max_cost:
        raise RuntimeError(
            f"estimated cost ${cost:.2f} exceeds --max-cost ${max_cost:.2f}; "
            "raise the cap explicitly or narrow the query",
        )

    dbn = client.timeseries.get_range(
        dataset=dataset,
        schema=schema,
        symbols=list(symbols),
        stype_in=stype_in,
        start=start.isoformat(),
        end=end.isoformat(),
    )
    # DBN's ``to_df()`` puts the timestamp on the index. ``index=False``
    # below drops the index — so promote the timestamp to a regular
    # column first, otherwise the saved parquet has integer row numbers
    # instead of timestamps and is unusable for time-series joins.
    df = dbn.to_df().reset_index()

    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(
        output,
        engine="pyarrow",
        compression="snappy",
        index=False,
    )

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return DownloadResult(
        cost=cost,
        row_count=len(df),
        sha256=digest,
        output_path=output,
    )


_VALID_STYPES = ("raw_symbol", "instrument_id", "parent", "continuous")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="download_databento")
    p.add_argument("--dataset", required=True,
                   help="Databento dataset code, e.g. GLBX.MDP3 or OPRA.PILLAR")
    p.add_argument("--schema", required=True,
                   help="Schema, e.g. ohlcv-1m, cbbo-1m, trades")
    p.add_argument("--symbols", required=True, nargs="+",
                   help="One or more symbols (space-separated)")
    p.add_argument("--stype-in", required=True, choices=_VALID_STYPES,
                   help="Symbology type for --symbols")
    p.add_argument("--start", type=dt.date.fromisoformat, required=True,
                   help="Inclusive start date, ISO YYYY-MM-DD")
    p.add_argument("--end", type=dt.date.fromisoformat, required=True,
                   help="Inclusive end date, ISO YYYY-MM-DD")
    p.add_argument("--output", type=Path, required=True,
                   help="Output Parquet path")
    p.add_argument("--max-cost", type=float, required=True,
                   help="Hard ceiling in dollars — abort before download if "
                        "estimated cost exceeds this")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.start > args.end:
        raise ValueError(
            f"start ({args.start}) must be on or before end ({args.end})",
        )
    result = download(
        dataset=args.dataset,
        schema=args.schema,
        symbols=tuple(args.symbols),
        stype_in=args.stype_in,
        start=args.start,
        end=args.end,
        output=args.output,
        max_cost=args.max_cost,
    )
    print(f"wrote {result.row_count} rows -> {result.output_path}")
    print(f"cost: ${result.cost:.2f}")
    print(f"sha256: {result.sha256}")
    print()
    print("Manifest snippet (paste under the matching dataset entry):")
    print(f'  "date_range": {{"start": "{args.start.isoformat()}", '
          f'"end": "{args.end.isoformat()}"}},')
    print(f'  "row_count": {result.row_count},')
    print(f'  "checksum": "sha256:{result.sha256}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
