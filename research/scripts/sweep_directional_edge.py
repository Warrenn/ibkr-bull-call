"""Sweep directional-edge candidates over a single date window.

Used to **shape v2 candidates on the TRAIN window** of dataset-v1's
60/20/20 split per `STRATEGY-SPEC-v1.md`. After train-window shaping,
1-2 finalists go on to be confirmed on the validation window; only
then would a v2 spec be frozen and the holdout touched once.

This is a multi-candidate exploration tool — NOT a frozen-spec runner.
Its output is informational ("which candidates are worth taking to
validation?"), not v1 evidence.

Candidates swept (12 total):

- signal_threshold ∈ {0.10%, 0.25%, 0.50%, 0.75%}
- signal_time_et ∈ {10:00, 10:30, 11:00}
- end_of_window_time_et = 15:55 (held fixed; sweep separately later)

For each candidate, computes:

- n (entered count)
- mean_forward_return
- t-stat vs zero, p-value (two-tailed)
- 95% CI on mean
- hit_rate
- per-year mean (regime-stability check; reports min year mean)
- verdict_simple (mean > 0?)
- verdict_nuanced (significant at p<0.05?)

Output: ranked markdown table + ranked CSV. Sort key is t-stat
descending — most statistically significant first.

Usage::

    uv run python -m research.scripts.sweep_directional_edge \\
        --window-name train \\
        --window-start 2023-05-01 --window-end 2025-02-14 \\
        --report research/reports/directional-edge-sweep-train.md \\
        --csv research/reports/directional-edge-sweep-train.csv
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from research.scripts.run_directional_edge_v1 import (
    _load_bars_parquet,
    _statistical_context,
    aggregate_metrics,
    compute_ledger,
    extract_intraday_prices,
)


@dataclass(frozen=True)
class Candidate:
    threshold: float
    signal_time_et: str
    eow_time_et: str
    vix_band: str | None = None  # None | "low" | "high" — v3 regime gate

    @property
    def label(self) -> str:
        base = (
            f"thr={self.threshold:.4%} "
            f"signal={self.signal_time_et} "
            f"eow={self.eow_time_et}"
        )
        return f"{base} vix={self.vix_band}" if self.vix_band else base


_DEFAULT_GRID: tuple[Candidate, ...] = tuple(
    Candidate(threshold=t, signal_time_et=s, eow_time_et="15:55")
    for t in (0.0010, 0.0025, 0.0050, 0.0075)
    for s in ("10:00", "10:30", "11:00")
)


@dataclass(frozen=True)
class CandidateResult:
    candidate: Candidate
    n: int
    mean: float
    std: float
    t_stat: float
    p_value: float
    ci_low_95: float
    ci_high_95: float
    hit_rate: float
    by_year_min: float  # smallest per-year mean (regime stability)
    verdict_simple: str
    verdict_nuanced: str


def compute_prior_vix_by_date(vix_df: pd.DataFrame) -> dict[dt.date, float]:
    """Map each VIX-data date to the VIX close of the *previous* day.

    This is the conventional regime indicator: at signal time on
    day t (10:30 ET), the operator already knows VIX from t-1.
    Day 0 of the dataset has no prior-day value and is omitted.
    """

    sorted_df = vix_df.sort_values("date").reset_index(drop=True)
    out: dict[dt.date, float] = {}
    for i in range(1, len(sorted_df)):
        out[sorted_df["date"].iloc[i]] = float(sorted_df["close"].iloc[i - 1])
    return out


def median_prior_vix_in_window(
    *,
    prior_vix_by_date: dict[dt.date, float],
    start: dt.date,
    end: dt.date,
) -> float:
    """Median of prior-day VIX close across dates in ``[start, end]``.

    Used to define low/high bands by data-driven median split — no
    arbitrary thresholds.
    """

    in_window = [
        v for d, v in prior_vix_by_date.items() if start <= d <= end
    ]
    if not in_window:
        raise RuntimeError(
            f"no VIX dates in window {start} → {end}; "
            "check VIX data coverage",
        )
    return float(pd.Series(in_window).median())


def filter_calendar_by_vix_band(
    calendar: pd.DataFrame,
    *,
    prior_vix_by_date: dict[dt.date, float],
    band: str,  # "low" | "high"
    median: float,
) -> pd.DataFrame:
    """Drop rows whose prior-day VIX is outside the requested band."""

    def in_band(d: dt.date) -> bool:
        prior = prior_vix_by_date.get(d)
        if prior is None:
            return False
        return prior < median if band == "low" else prior >= median

    return calendar[calendar["date"].apply(in_band)].copy()


def filter_calendar_to_window(
    calendar: pd.DataFrame,
    *,
    start: dt.date,
    end: dt.date,
) -> pd.DataFrame:
    return calendar[
        (calendar["date"] >= start) & (calendar["date"] <= end)
    ].copy()


def filter_calendar_excluding_events(
    calendar: pd.DataFrame,
    *,
    excluded_dates: set[dt.date],
) -> pd.DataFrame:
    """Drop rows whose ``date`` is in ``excluded_dates``.

    Used by v2 to remove FOMC / CPI / NFP / OPEX days from the
    eligibility window. Empty ``excluded_dates`` is a no-op.
    """

    if not excluded_dates:
        return calendar
    return calendar[~calendar["date"].isin(excluded_dates)].copy()


def evaluate_candidate(
    *,
    bars: pd.DataFrame,
    calendar: pd.DataFrame,
    candidate: Candidate,
    session_open_time: str = "09:30",
) -> CandidateResult | None:
    """Evaluate one candidate on the given (bars, calendar) window.

    Returns ``None`` if no signals fired (the candidate is too tight
    or the window is empty).
    """

    prices = extract_intraday_prices(
        bars=bars,
        calendar=calendar,
        session_open_time=session_open_time,
        signal_time=candidate.signal_time_et,
        eow_time=candidate.eow_time_et,
    )
    if len(prices) == 0:
        return None

    ledger = compute_ledger(
        prices_per_day=prices,
        signal_threshold=candidate.threshold,
    )
    entered = ledger[ledger["entered"]]
    if len(entered) < 2:
        # Need at least 2 trades to compute meaningful stats.
        return None

    metrics = aggregate_metrics(ledger)
    sc = _statistical_context(entered)

    yearly = sc["yearly"]
    by_year_min = float(yearly["mean"].min()) if len(yearly) > 0 else float("nan")

    # v3-aware: a significantly NEGATIVE t-stat is mean-reversion edge,
    # not just NO_EDGE. Look at |t-stat| and the sign separately.
    if abs(sc["t_stat"]) >= 2.0:
        nuanced = (
            "EDGE_PRESENT_CONTINUATION"
            if sc["t_stat"] > 0
            else "EDGE_PRESENT_MEAN_REVERSION"
        )
    elif metrics.verdict == "EDGE_PRESENT":
        nuanced = "EDGE_INCONCLUSIVE"
    else:
        nuanced = "NO_EDGE"

    return CandidateResult(
        candidate=candidate,
        n=metrics.entered_count,
        mean=metrics.mean_forward_return,
        std=sc["std"],
        t_stat=sc["t_stat"],
        p_value=sc["p_value"],
        ci_low_95=sc["ci_low_95"],
        ci_high_95=sc["ci_high_95"],
        hit_rate=metrics.hit_rate,
        by_year_min=by_year_min,
        verdict_simple=metrics.verdict,
        verdict_nuanced=nuanced,
    )


def sweep(
    *,
    bars: pd.DataFrame,
    calendar: pd.DataFrame,
    candidates: tuple[Candidate, ...] = _DEFAULT_GRID,
    vix_filter: dict[str, dict[dt.date, float] | float] | None = None,
) -> list[CandidateResult]:
    """Run the candidate grid against ``calendar``.

    If ``vix_filter`` is provided, candidates whose ``vix_band`` is
    set will further restrict the calendar to dates whose prior-day
    VIX is in the requested band before evaluation.

    ``vix_filter`` shape::

        {"prior_vix_by_date": dict[date, float], "median": float}
    """

    results: list[CandidateResult] = []
    for c in candidates:
        if c.vix_band is not None and vix_filter is not None:
            cal_for_c = filter_calendar_by_vix_band(
                calendar,
                prior_vix_by_date=vix_filter["prior_vix_by_date"],  # type: ignore[arg-type]
                band=c.vix_band,
                median=vix_filter["median"],  # type: ignore[arg-type]
            )
        else:
            cal_for_c = calendar
        r = evaluate_candidate(bars=bars, calendar=cal_for_c, candidate=c)
        if r is not None:
            results.append(r)
    # Sort by absolute t-stat descending (catches both continuation
    # and mean-reversion edges in the same ranking).
    results.sort(key=lambda r: abs(r.t_stat), reverse=True)
    return results


def format_report(
    *,
    results: list[CandidateResult],
    window_name: str,
    window_start: dt.date,
    window_end: dt.date,
    es_path: Path,
    code_revision: str,
    run_timestamp: dt.datetime,
) -> str:
    lines = [
        f"# Directional-Edge Sweep — {window_name.upper()} window",
        "",
        f"**Window**: {window_start.isoformat()} → {window_end.isoformat()}",
        "",
        "## Provenance",
        "",
        f"- code_revision: `{code_revision}`",
        f"- run_timestamp_utc: {run_timestamp.isoformat()}",
        f"- ES dataset: `{es_path.name}`",
        "",
        "## Important caveat",
        "",
        f"This is a SHAPING sweep on the **{window_name.upper()}** window.",
        "It is not v1 evidence (v1 was already evaluated as `EDGE_INCONCLUSIVE`",
        "on the full dataset in PR #55) and it is not v2 evidence either —",
        "v2 evidence requires a frozen v2 spec evaluated on the **holdout**",
        "window after a one-shot validation pass.",
        "",
        "## Candidates ranked by |t-stat| (most significant first; sign indicates direction)",
        "",
        "| threshold | signal_time | eow_time | vix_band | n | mean | t-stat | p-value | 95% CI | hit_rate | by_year_min | verdict |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        c = r.candidate
        vix = c.vix_band or "—"
        lines.append(
            f"| {c.threshold:.4%} | {c.signal_time_et} | {c.eow_time_et} | "
            f"{vix} | {r.n} | {r.mean:+.4%} | {r.t_stat:+.2f} | {r.p_value:.3f} | "
            f"[{r.ci_low_95:+.4%}, {r.ci_high_95:+.4%}] | "
            f"{r.hit_rate:.1%} | {r.by_year_min:+.4%} | {r.verdict_nuanced} |"
        )
    lines.extend([
        "",
        "## Reading the table",
        "",
        "- **t-stat ≥ 2** ≈ p < 0.05 — the conventional bar for "
        "\"distinguishable from zero\".",
        "- **by_year_min**: smallest per-year mean. If this is meaningfully",
        "  negative while the aggregate mean is positive, the edge is",
        "  regime-dependent (per spec rule "
        "`result_is_not_concentrated_in_one_small_regime_cluster`).",
        "- **n** below ~30 means very low statistical power; treat any",
        "  verdict on those rows as suggestive at best.",
        "",
        "## Decision frame",
        "",
        "Take to **validation** only candidates that pass ALL of:",
        "- `verdict_nuanced == EDGE_PRESENT_AND_SIGNIFICANT` (t-stat ≥ 2)",
        "- `n` is large enough to be meaningful (≥ 30 trades)",
        "- `by_year_min` is not catastrophically negative",
        "- A coherent story can be told for *why* this combination would work",
        "  (avoid p-hacking from the grid).",
        "",
        "If no candidate passes those filters, the honest read is **NO_EDGE**:",
        "the directional view doesn't have standalone same-session edge",
        "robust enough to justify continuing.",
    ])
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="sweep_directional_edge")
    p.add_argument("--es", type=Path,
                   default=Path("research/data/dataset-v1/es_intraday.parquet"))
    p.add_argument("--calendar", type=Path,
                   default=Path("research/data/dataset-v1/trading_calendar.parquet"))
    p.add_argument("--window-name", required=True,
                   choices=["train", "validation", "holdout"],
                   help="Which split window to evaluate on")
    p.add_argument("--window-start", type=dt.date.fromisoformat, required=True)
    p.add_argument("--window-end", type=dt.date.fromisoformat, required=True)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--csv", type=Path, required=True)
    p.add_argument("--code-revision", default="HEAD")
    p.add_argument(
        "--event-calendar", type=Path, default=None,
        help="Optional event_calendar.parquet — dates listed here are "
             "excluded from eligibility (v2 event-filter). Schema: "
             "date, event_type.",
    )
    p.add_argument(
        "--vix-data", type=Path, default=None,
        help="Optional vix_daily.parquet — when present, the sweep "
             "duplicates each candidate into low/high VIX bands using "
             "a median split on prior-day VIX close within the window.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    bars = _load_bars_parquet(args.es)
    cal = pd.read_parquet(args.calendar)
    cal_window = filter_calendar_to_window(
        cal, start=args.window_start, end=args.window_end,
    )
    if len(cal_window) == 0:
        raise RuntimeError(
            f"calendar has zero rows in window {args.window_start} → {args.window_end}",
        )

    excluded_count = 0
    if args.event_calendar is not None:
        events = pd.read_parquet(args.event_calendar)
        excluded_dates = set(events["date"].tolist())
        before = len(cal_window)
        cal_window = filter_calendar_excluding_events(
            cal_window, excluded_dates=excluded_dates,
        )
        excluded_count = before - len(cal_window)

    candidates = _DEFAULT_GRID
    vix_filter: dict[str, object] | None = None
    vix_median: float | None = None
    if args.vix_data is not None:
        vix_df = pd.read_parquet(args.vix_data)
        prior_vix = compute_prior_vix_by_date(vix_df)
        vix_median = median_prior_vix_in_window(
            prior_vix_by_date=prior_vix,
            start=args.window_start,
            end=args.window_end,
        )
        vix_filter = {"prior_vix_by_date": prior_vix, "median": vix_median}
        candidates = tuple(
            Candidate(
                threshold=c.threshold,
                signal_time_et=c.signal_time_et,
                eow_time_et=c.eow_time_et,
                vix_band=band,
            )
            for c in _DEFAULT_GRID
            for band in ("low", "high")
        )

    results = sweep(
        bars=bars, calendar=cal_window,
        candidates=candidates, vix_filter=vix_filter,  # type: ignore[arg-type]
    )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.csv.parent.mkdir(parents=True, exist_ok=True)

    report = format_report(
        results=results,
        window_name=args.window_name,
        window_start=args.window_start,
        window_end=args.window_end,
        es_path=args.es,
        code_revision=args.code_revision,
        run_timestamp=dt.datetime.now(tz=dt.timezone.utc),
    )
    args.report.write_text(report)

    rows: list[dict[str, Any]] = []
    for r in results:
        rows.append({
            "threshold": r.candidate.threshold,
            "signal_time": r.candidate.signal_time_et,
            "eow_time": r.candidate.eow_time_et,
            "vix_band": r.candidate.vix_band or "",
            "n": r.n,
            "mean": r.mean,
            "std": r.std,
            "t_stat": r.t_stat,
            "p_value": r.p_value,
            "ci_low_95": r.ci_low_95,
            "ci_high_95": r.ci_high_95,
            "hit_rate": r.hit_rate,
            "by_year_min": r.by_year_min,
            "verdict_simple": r.verdict_simple,
            "verdict_nuanced": r.verdict_nuanced,
        })
    pd.DataFrame(rows).to_csv(args.csv, index=False)

    print(f"sweep window: {args.window_name} ({args.window_start} → {args.window_end})")
    if args.event_calendar is not None:
        print(f"event filter: excluded {excluded_count} event days from window")
    if args.vix_data is not None and vix_median is not None:
        print(f"VIX gate: median split at prior-day close = {vix_median:.2f}")
    print(f"candidates evaluated: {len(results)}")
    if results:
        top = results[0]
        print(
            f"top by t-stat: thr={top.candidate.threshold:.4%} "
            f"signal={top.candidate.signal_time_et} "
            f"t={top.t_stat:+.2f} p={top.p_value:.3f} "
            f"mean={top.mean:+.4%} n={top.n}",
        )
    print(f"report -> {args.report}")
    print(f"csv    -> {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
