"""Estimate Databento download cost for Tier 1 ``dataset-v1`` acquisitions.

This is the **dry-run gate** that must pass before any actual batch
download is submitted. ``metadata.get_cost`` is itself free; running
this script does NOT charge the account or burn credits. The output
shows what each planned pull would cost in dollars (against
uncompressed DBN bytes) so we can:

1. Catch schema / symbol typos before they burn the $125 free credit
   on a misconfigured pull.
2. Confirm Tier 1 fits inside the free credit envelope.
3. Choose between 24-month and 36-month coverage based on actual
   delta cost rather than guessing.

Usage:

    DATABENTO_API_KEY=db-... uv run python -m research.scripts.estimate_databento_cost \\
        --start 2024-04-01 --end 2026-04-30

Tier 1 queries are pinned in ``_TIER_1_QUERIES`` below and match the
acquisition plan in ``docs/data-acquisition-decision.md`` Path A:

- SPXW 0DTE chain via OPRA.PILLAR ``cbbo-1m`` (consolidated 1-min NBBO)
- ES front-month continuous via GLBX.MDP3 ``ohlcv-1m``

SPX index 1m is intentionally NOT a Databento dataset (CGIF PCAP is
$750/mo institutional-only); that data comes from IBKR per the
acquisition decision doc.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class CostQuery:
    """One ``metadata.get_cost`` query — a planned download to price."""

    label: str
    dataset: str
    schema: str
    symbols: tuple[str, ...]
    stype_in: str


_TIER_1_QUERIES: tuple[CostQuery, ...] = (
    CostQuery(
        label="SPXW 0DTE chain (OPRA.PILLAR cbbo-1m)",
        dataset="OPRA.PILLAR",
        schema="cbbo-1m",
        symbols=("SPXW.OPT",),
        stype_in="parent",
    ),
    CostQuery(
        label="ES front-month (GLBX.MDP3 ohlcv-1m)",
        dataset="GLBX.MDP3",
        schema="ohlcv-1m",
        symbols=("ES.c.0",),
        stype_in="continuous",
    ),
)


def estimate(
    *,
    start: dt.date,
    end: dt.date,
    queries: Sequence[CostQuery] = _TIER_1_QUERIES,
) -> list[tuple[CostQuery, float]]:
    """Return ``[(query, dollars)]`` for each query.

    Raises ``RuntimeError`` if ``DATABENTO_API_KEY`` is not set.
    Network calls are billed-free under Databento's pricing model.
    """

    import databento as db

    api_key = os.environ.get("DATABENTO_API_KEY")
    if not api_key:
        raise RuntimeError(
            "DATABENTO_API_KEY environment variable not set. "
            "Generate one at https://databento.com/portal/keys",
        )

    client = db.Historical(key=api_key)
    out: list[tuple[CostQuery, float]] = []
    for q in queries:
        cost = client.metadata.get_cost(
            dataset=q.dataset,
            schema=q.schema,
            symbols=list(q.symbols),
            stype_in=q.stype_in,
            start=start.isoformat(),
            end=end.isoformat(),
        )
        out.append((q, float(cost)))
    return out


def _format_results(
    results: list[tuple[CostQuery, float]],
    *,
    start: dt.date,
    end: dt.date,
    free_credit: float,
) -> str:
    total = sum(c for _, c in results)
    label_width = max(len(q.label) for q, _ in results) + 2
    lines: list[str] = []
    lines.append(f"Date range: {start.isoformat()} -> {end.isoformat()} "
                 f"({(end - start).days} days)")
    lines.append(f"Free credit on a new Databento account: ${free_credit:.2f}")
    lines.append("")
    lines.append(f"{'Query':<{label_width}} {'Cost':>10}")
    lines.append("-" * (label_width + 12))
    for q, c in results:
        lines.append(f"{q.label:<{label_width}} ${c:>8.2f}")
    lines.append("-" * (label_width + 12))
    lines.append(f"{'TOTAL':<{label_width}} ${total:>8.2f}")
    lines.append("")
    remaining = free_credit - total
    if remaining >= 0:
        lines.append(
            f"[ok] Within free credit. Remaining after pull: ${remaining:.2f}",
        )
    else:
        lines.append(
            f"[over budget] Exceeds free credit by ${-remaining:.2f}",
        )
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="estimate_databento_cost")
    p.add_argument(
        "--start", type=dt.date.fromisoformat, required=True,
        help="Inclusive start date, ISO YYYY-MM-DD",
    )
    p.add_argument(
        "--end", type=dt.date.fromisoformat, required=True,
        help="Inclusive end date, ISO YYYY-MM-DD",
    )
    p.add_argument(
        "--free-credit", type=float, default=125.0,
        help="Free credit balance to compare against (default: $125 new-account credit)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.start > args.end:
        raise ValueError(
            f"start ({args.start}) must be on or before end ({args.end})",
        )
    results = estimate(start=args.start, end=args.end)
    print(_format_results(
        results,
        start=args.start,
        end=args.end,
        free_credit=args.free_credit,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
