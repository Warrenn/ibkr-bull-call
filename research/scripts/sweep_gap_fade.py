"""Sweep overnight-gap-fade candidates.

Mechanic: each trading day t has a "gap" = today_open / prior_close - 1.
The fade hypothesis (Cliff & Cliff 2002 et al.): large overnight gaps
mean-revert intraday more than they continue. So:

- if gap > threshold (gap up): SHORT at today_open, exit at today_eow.
  forward_return = today_eow/today_open - 1, fade_pnl = -forward_return
- if gap < -threshold (gap down): LONG at today_open, exit at today_eow.
  fade_pnl = +forward_return

Equivalently: fade_pnl = -sign(gap) × forward_return for any day where
|gap| >= threshold.

Output schema (markdown report + CSV):

- threshold: minimum |gap| that triggers a fade trade
- n: number of fades fired
- mean_pnl, std, t-stat, p-value, 95% CI
- hit_rate (fade_pnl > 0)
- by_year_min (regime stability)
- verdict_nuanced (CONTINUATION / MEAN_REVERSION / INCONCLUSIVE / NO_EDGE)

Re-uses helpers (``_load_bars_parquet``, ``_statistical_context``)
from ``run_directional_edge_v1`` so a single source of truth governs
both v1-v3 spec runs and v4 mechanic exploration.
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
)


@dataclass(frozen=True)
class GapCandidate:
    threshold: float  # minimum |gap| to fade

    @property
    def label(self) -> str:
        return f"|gap|>={self.threshold:.4%}"


@dataclass(frozen=True)
class GapCandidateResult:
    candidate: GapCandidate
    n: int
    mean: float
    std: float
    t_stat: float
    p_value: float
    ci_low_95: float
    ci_high_95: float
    hit_rate: float
    by_year_min: float
    verdict_simple: str
    verdict_nuanced: str
    n_gap_up: int  # subset that gapped up
    n_gap_down: int  # subset that gapped down


_DEFAULT_GAP_THRESHOLDS: tuple[float, ...] = (
    0.001,   # 0.10%
    0.002,   # 0.20%
    0.003,   # 0.30%
    0.005,   # 0.50%
    0.0075,  # 0.75%
    0.010,   # 1.00%
)


def extract_gap_data(
    *,
    bars: pd.DataFrame,
    calendar: pd.DataFrame,
    et_tz: str = "America/New_York",
    open_time_et: str = "09:30",
    eow_time_et: str = "15:55",
) -> pd.DataFrame:
    """Return one row per full NYSE trading day with prior-close /
    today-open / today-eow / gap / forward_return / fade_pnl.

    Columns: ``date``, ``prior_close``, ``today_open``, ``today_eow``,
    ``gap``, ``forward_return``, ``fade_pnl``. Days that lack any of
    the three target prices, or that are NYSE holidays / half-days,
    are excluded.

    ``fade_pnl = -sign(gap) × forward_return`` — positive means the
    fade trade was profitable that day.
    """

    bars = bars.copy()
    bars["ts_et"] = bars["ts_utc"].dt.tz_convert(et_tz)
    bars["date_et"] = bars["ts_et"].dt.date
    bars["time_et"] = bars["ts_et"].dt.strftime("%H:%M:%S")

    full_trading = set(
        calendar[
            calendar["is_trading_day"] & ~calendar["is_half_day"]
        ]["date"].tolist()
    )
    bars = bars[bars["date_et"].isin(full_trading)]

    open_t = f"{open_time_et}:00"
    eow_t = f"{eow_time_et}:00"

    opens = (
        bars[bars["time_et"] == open_t][["date_et", "open"]]
        .copy().rename(columns={"open": "today_open"})
        .drop_duplicates(subset=["date_et"]).set_index("date_et")
    )
    eows = (
        bars[bars["time_et"] == eow_t][["date_et", "open"]]
        .copy().rename(columns={"open": "today_eow"})
        .drop_duplicates(subset=["date_et"]).set_index("date_et")
    )
    daily = pd.concat([opens, eows], axis=1).dropna()
    daily = daily.reset_index().rename(columns={"date_et": "date"})
    daily = daily.sort_values("date").reset_index(drop=True)

    # prior_close = the prior trading day's eow
    daily["prior_close"] = daily["today_eow"].shift(1)
    daily = daily.dropna(subset=["prior_close"])

    daily["gap"] = daily["today_open"] / daily["prior_close"] - 1
    daily["forward_return"] = daily["today_eow"] / daily["today_open"] - 1
    # fade_pnl: positive = fade was profitable (gap up & market went down,
    # or gap down & market went up).
    daily["fade_pnl"] = -daily["gap"].apply(
        lambda g: 1 if g > 0 else (-1 if g < 0 else 0),
    ) * daily["forward_return"]

    return daily.reset_index(drop=True)


def evaluate_gap_candidate(
    *,
    gap_data: pd.DataFrame,
    candidate: GapCandidate,
) -> GapCandidateResult | None:
    """Filter to days where |gap| >= threshold; aggregate fade_pnl."""

    fired = gap_data[gap_data["gap"].abs() >= candidate.threshold]
    if len(fired) < 2:
        return None

    pnl = fired["fade_pnl"].astype(float)
    n = len(pnl)
    mean = float(pnl.mean())
    std = float(pnl.std(ddof=1))

    # Per-year breakdown for regime stability
    by_year = (
        fired.assign(year=lambda d: pd.to_datetime(d["date"]).dt.year)
        .groupby("year")["fade_pnl"]
        .mean()
    )
    by_year_min = float(by_year.min()) if len(by_year) > 0 else float("nan")

    # Use shared statistical context helper (handles std==0 edge case)
    sc = _statistical_context(fired.assign(forward_return=fired["fade_pnl"]))

    verdict_simple = "EDGE_PRESENT" if mean > 0 else "NO_EDGE"
    if abs(sc["t_stat"]) >= 2.0:
        verdict_nuanced = (
            "EDGE_PRESENT_FADE_WORKS"
            if sc["t_stat"] > 0
            else "EDGE_PRESENT_FADE_BACKFIRES"
        )
    elif verdict_simple == "EDGE_PRESENT":
        verdict_nuanced = "EDGE_INCONCLUSIVE"
    else:
        verdict_nuanced = "NO_EDGE"

    return GapCandidateResult(
        candidate=candidate,
        n=n,
        mean=mean,
        std=std,
        t_stat=sc["t_stat"],
        p_value=sc["p_value"],
        ci_low_95=sc["ci_low_95"],
        ci_high_95=sc["ci_high_95"],
        hit_rate=float((pnl > 0).mean()),
        by_year_min=by_year_min,
        verdict_simple=verdict_simple,
        verdict_nuanced=verdict_nuanced,
        n_gap_up=int((fired["gap"] > 0).sum()),
        n_gap_down=int((fired["gap"] < 0).sum()),
    )


def sweep_gaps(
    *,
    gap_data: pd.DataFrame,
    thresholds: tuple[float, ...] = _DEFAULT_GAP_THRESHOLDS,
) -> list[GapCandidateResult]:
    results: list[GapCandidateResult] = []
    for t in thresholds:
        r = evaluate_gap_candidate(
            gap_data=gap_data,
            candidate=GapCandidate(threshold=t),
        )
        if r is not None:
            results.append(r)
    results.sort(key=lambda r: abs(r.t_stat), reverse=True)
    return results


def filter_gap_data_by_window(
    gap_data: pd.DataFrame, *, start: dt.date, end: dt.date,
) -> pd.DataFrame:
    return gap_data[
        (gap_data["date"] >= start) & (gap_data["date"] <= end)
    ].copy()


def filter_gap_data_excluding_events(
    gap_data: pd.DataFrame, *, excluded_dates: set[dt.date],
) -> pd.DataFrame:
    if not excluded_dates:
        return gap_data
    return gap_data[~gap_data["date"].isin(excluded_dates)].copy()


def format_report(
    *,
    results: list[GapCandidateResult],
    window_name: str,
    window_start: dt.date,
    window_end: dt.date,
    es_path: Path,
    code_revision: str,
    run_timestamp: dt.datetime,
    event_filtered: bool,
    total_days_in_window: int,
    days_after_filter: int,
) -> str:
    lines = [
        f"# v4-A Gap-Fade Sweep — {window_name.upper()} window",
        "",
        f"**Window**: {window_start.isoformat()} → {window_end.isoformat()}",
        f"**Days in window**: {total_days_in_window}; "
        f"after event filter: {days_after_filter}.",
        f"**Event filter**: {'enabled' if event_filtered else 'disabled'}",
        "",
        "## Provenance",
        "",
        f"- code_revision: `{code_revision}`",
        f"- run_timestamp_utc: {run_timestamp.isoformat()}",
        f"- ES dataset: `{es_path.name}`",
        "",
        "## Mechanic",
        "",
        "Per-day signal: ``gap = today_open(09:30 ET) / prior_close(15:55 ET, "
        "prior trading day) - 1``. Fire a fade trade when ``|gap| >= threshold``: "
        "short if gap > 0, long if gap < 0. ``fade_pnl = -sign(gap) * "
        "forward_return`` where ``forward_return = today_close(15:55) / "
        "today_open(09:30) - 1``.",
        "",
        "## Important caveat",
        "",
        f"This is a SHAPING sweep on the **{window_name.upper()}** window.",
        "It is not v4 evidence — v4 evidence requires a frozen v4 spec",
        "evaluated on validation (one-shot) and holdout (one-shot).",
        "",
        "## Candidates ranked by |t-stat|",
        "",
        "| threshold | n | n_up | n_down | mean | t-stat | p-value | 95% CI | hit_rate | by_year_min | verdict |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        c = r.candidate
        lines.append(
            f"| {c.threshold:.4%} | {r.n} | {r.n_gap_up} | {r.n_gap_down} | "
            f"{r.mean:+.4%} | {r.t_stat:+.2f} | {r.p_value:.3f} | "
            f"[{r.ci_low_95:+.4%}, {r.ci_high_95:+.4%}] | "
            f"{r.hit_rate:.1%} | {r.by_year_min:+.4%} | {r.verdict_nuanced} |"
        )
    lines.extend([
        "",
        "## Reading the table",
        "",
        "- **EDGE_PRESENT_FADE_WORKS** (t ≥ +2): the fade trade is",
        "  significantly profitable.",
        "- **EDGE_PRESENT_FADE_BACKFIRES** (t ≤ -2): gaps are significantly",
        "  more likely to *continue* than fade — a momentum edge in the",
        "  opposite direction.",
        "- **n_up / n_down**: count of gap-up vs gap-down days that fired",
        "  the candidate. Asymmetric ratios may indicate direction-specific",
        "  behavior worth a follow-up sweep.",
    ])
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="sweep_gap_fade")
    p.add_argument("--es", type=Path,
                   default=Path("research/data/dataset-v1/es_intraday.parquet"))
    p.add_argument("--calendar", type=Path,
                   default=Path("research/data/dataset-v1/trading_calendar.parquet"))
    p.add_argument("--window-name", required=True,
                   choices=["train", "validation", "holdout"])
    p.add_argument("--window-start", type=dt.date.fromisoformat, required=True)
    p.add_argument("--window-end", type=dt.date.fromisoformat, required=True)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--csv", type=Path, required=True)
    p.add_argument("--code-revision", default="HEAD")
    p.add_argument("--event-calendar", type=Path, default=None,
                   help="Optional event_calendar.parquet to drop event days")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    bars = _load_bars_parquet(args.es)
    cal = pd.read_parquet(args.calendar)

    gap_data = extract_gap_data(bars=bars, calendar=cal)
    gap_data = filter_gap_data_by_window(
        gap_data, start=args.window_start, end=args.window_end,
    )
    total_days = len(gap_data)

    if args.event_calendar is not None:
        events = pd.read_parquet(args.event_calendar)
        excluded = set(events["date"].tolist())
        gap_data = filter_gap_data_excluding_events(
            gap_data, excluded_dates=excluded,
        )

    results = sweep_gaps(gap_data=gap_data)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(format_report(
        results=results,
        window_name=args.window_name,
        window_start=args.window_start,
        window_end=args.window_end,
        es_path=args.es,
        code_revision=args.code_revision,
        run_timestamp=dt.datetime.now(tz=dt.timezone.utc),
        event_filtered=args.event_calendar is not None,
        total_days_in_window=total_days,
        days_after_filter=len(gap_data),
    ))

    rows: list[dict[str, Any]] = []
    for r in results:
        rows.append({
            "threshold": r.candidate.threshold,
            "n": r.n,
            "n_gap_up": r.n_gap_up,
            "n_gap_down": r.n_gap_down,
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
    print(f"days in window after filter: {len(gap_data)}")
    print(f"candidates evaluated: {len(results)}")
    if results:
        top = results[0]
        print(
            f"top by |t-stat|: thr={top.candidate.threshold:.4%} "
            f"t={top.t_stat:+.2f} p={top.p_value:.3f} "
            f"mean={top.mean:+.4%} n={top.n}",
        )
    print(f"report -> {args.report}")
    print(f"csv    -> {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
