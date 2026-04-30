"""Run the Phase 1 directional-edge falsification test.

Per ``docs/STRATEGY-SPEC-v1.md`` and
``research/specs/directional-edge-v1.yaml``, this is the fastest
falsification step in the program: does the bullish intraday
continuation idea have standalone same-session edge?

Method:

1. For each NYSE full-trading day (half-days excluded — 15:55 ET is
   past the early close):
   a. Get session_open_price = open of bar at 09:30 ET.
   b. Get signal_time_price = open of bar at 10:30 ET.
   c. Compute confirmation_return =
      signal_time_price / session_open_price - 1.
   d. If confirmation_return >= signal_threshold (0.25%):
      entered = True; record forward_return =
      end_of_window_price / signal_time_price - 1.
   e. Else: entered = False, skip_reason = no_signal.
2. Aggregate over entered days: trade_count, mean / median
   forward_return, hit_rate (frac > 0), left_tail_p05 (5th pct).
3. Verdict: ``EDGE_PRESENT`` if mean_forward_return > 0; else
   ``NO_EDGE``.
4. Write summary report (markdown) + ledger CSV.

All decision parameters are pinned in
``research/specs/directional-edge-v1.yaml`` and loaded from there at
runtime; this script does NOT hardcode them so a spec edit produces
a different result without code changes (which would invalidate v1
evidence per the spec freeze rule).

Usage::

    uv run python -m research.scripts.run_directional_edge_v1 \\
        --es research/data/dataset-v1/es_intraday.parquet \\
        --calendar research/data/dataset-v1/trading_calendar.parquet \\
        --spec research/specs/directional-edge-v1.yaml \\
        --report research/reports/directional-edge-v1.md \\
        --ledger research/reports/directional-edge-v1-ledger.csv
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import yaml

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DirectionalEdgeMetrics:
    trade_count: int
    entered_count: int
    skipped_count: int
    total_sessions: int
    mean_forward_return: float
    median_forward_return: float
    hit_rate: float
    left_tail_p05: float
    verdict: Literal["EDGE_PRESENT", "NO_EDGE"]


@dataclass(frozen=True)
class DirectionalEdgeResult:
    metrics: DirectionalEdgeMetrics
    ledger: pd.DataFrame


def _load_bars_parquet(path: Path) -> pd.DataFrame:
    """Load a bars parquet, normalizing the timestamp column to ``ts_utc``.

    Databento writes ``ts_event``; IBKR writes ``ts_utc``. Canonicalize
    here so ``extract_intraday_prices`` doesn't branch on schema.
    """

    df = pd.read_parquet(path)
    if "ts_event" in df.columns and "ts_utc" not in df.columns:
        df = df.rename(columns={"ts_event": "ts_utc"})
    return df


def extract_intraday_prices(
    *,
    bars: pd.DataFrame,
    calendar: pd.DataFrame,
    et_tz: str = "America/New_York",
    session_open_time: str = "09:30",
    signal_time: str = "10:30",
    eow_time: str = "15:55",
) -> pd.DataFrame:
    """Return one row per full NYSE trading day with three price points.

    Columns: ``date``, ``session_open_price``, ``signal_price``,
    ``eow_price``. Days that are NYSE holidays, half-days, or are
    missing any of the three target bars (data gap) are excluded.

    Prices are the OPEN of the 1-min bar at each target ET time —
    i.e. the price observed at HH:MM:00 ET.
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

    target_times = {
        "session_open_price": f"{session_open_time}:00",
        "signal_price": f"{signal_time}:00",
        "eow_price": f"{eow_time}:00",
    }

    pivots: list[pd.DataFrame] = []
    for col, t_str in target_times.items():
        match = (
            bars[bars["time_et"] == t_str][["date_et", "open"]]
            .copy()
            .rename(columns={"open": col})
            .drop_duplicates(subset=["date_et"])
            .set_index("date_et")
        )
        pivots.append(match)

    result = pd.concat(pivots, axis=1).dropna()
    return (
        result
        .reset_index()
        .rename(columns={"date_et": "date"})
        .sort_values("date")
        .reset_index(drop=True)
    )


def compute_ledger(
    *,
    prices_per_day: pd.DataFrame,
    signal_threshold: float,
) -> pd.DataFrame:
    """Compute confirmation_return / entered / forward_return per day."""

    df = prices_per_day.copy()
    df["confirmation_return"] = (
        df["signal_price"] / df["session_open_price"] - 1
    )
    df["entered"] = df["confirmation_return"] >= signal_threshold
    df["skip_reason"] = df["entered"].apply(lambda e: "" if e else "no_signal")
    raw_forward = df["eow_price"] / df["signal_price"] - 1
    df["forward_return"] = raw_forward.where(df["entered"])
    return df


def aggregate_metrics(ledger: pd.DataFrame) -> DirectionalEdgeMetrics:
    """Aggregate over entered days. Verdict is ``EDGE_PRESENT`` iff
    ``mean_forward_return > 0``."""

    entered = ledger[ledger["entered"]]
    if len(entered) == 0:
        raise RuntimeError(
            "no signals fired across the entire sample — cannot compute "
            "directional edge metrics. Check signal_threshold and data range.",
        )

    fr = entered["forward_return"]
    mean = float(fr.mean())
    return DirectionalEdgeMetrics(
        trade_count=len(entered),
        entered_count=len(entered),
        skipped_count=int((~ledger["entered"]).sum()),
        total_sessions=len(ledger),
        mean_forward_return=mean,
        median_forward_return=float(fr.median()),
        hit_rate=float((fr > 0).mean()),
        left_tail_p05=float(fr.quantile(0.05)),
        verdict="EDGE_PRESENT" if mean > 0 else "NO_EDGE",
    )


def _statistical_context(entered: pd.DataFrame) -> dict[str, Any]:
    """Compute t-stat, p-value, 95% CI, and per-year breakdown.

    These are not in the spec's required_metrics list but are needed
    to interpret a "mean > 0" verdict honestly: a positive mean with
    t-stat < 2 is statistically indistinguishable from zero, which
    matters for the kill_if rule
    "standalone_directional_expectancy_is_near_zero_or_negative".
    """

    from scipy import stats

    fr = entered["forward_return"].astype(float)
    n = len(fr)
    mean = float(fr.mean())
    std = float(fr.std(ddof=1))
    sem = std / (n ** 0.5) if n > 1 else float("nan")

    if n > 1 and std > 0:
        t_stat = mean / sem
        # Two-tailed p-value against H0: mean == 0
        p_value = float(2 * stats.t.sf(abs(t_stat), df=n - 1))
        # 95% CI using t-distribution
        t_crit = float(stats.t.ppf(0.975, df=n - 1))
        ci_low = mean - t_crit * sem
        ci_high = mean + t_crit * sem
    else:
        # Either too few samples for a t-distribution (n <= 1) or zero
        # variance (synthetic test data, or one-week stretches with no
        # forward-return movement). Surface as NaN rather than divide
        # by zero — the report will display NaN and the operator can
        # see the candidate has no usable significance estimate.
        t_stat = float("nan")
        p_value = float("nan")
        ci_low = float("nan")
        ci_high = float("nan")

    # Per-year breakdown
    yearly = (
        entered.assign(year=lambda d: pd.to_datetime(d["date"]).dt.year)
        .groupby("year")["forward_return"]
        .agg(["count", "mean", "std", lambda s: (s > 0).mean()])
        .rename(columns={"<lambda_0>": "hit_rate"})
    )

    return {
        "n": n,
        "mean": mean,
        "std": std,
        "sem": sem,
        "t_stat": float(t_stat),
        "p_value": p_value,
        "ci_low_95": float(ci_low),
        "ci_high_95": float(ci_high),
        "yearly": yearly,
    }


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def format_report(
    *,
    metrics: DirectionalEdgeMetrics,
    spec: dict[str, Any],
    es_path: Path,
    es_sha256: str,
    cal_path: Path,
    cal_sha256: str,
    code_revision: str,
    run_timestamp: dt.datetime,
    actual_date_range: tuple[dt.date, dt.date],
    stats_context: dict[str, Any] | None = None,
) -> str:
    sig = spec["signal"]
    horiz = spec["horizon"]
    start_d, end_d = actual_date_range

    # Compute the more nuanced verdict the simple "mean > 0" rule misses.
    # The spec's kill_if includes "near_zero_or_negative" — a positive mean
    # with t-stat < 2 is statistically indistinguishable from zero and
    # belongs in the "near zero" bucket regardless of which side of zero
    # the point estimate happens to land on.
    if stats_context is not None and metrics.verdict == "EDGE_PRESENT":
        if abs(stats_context["t_stat"]) < 2.0:
            nuanced = "EDGE_INCONCLUSIVE — positive mean but not significant"
        else:
            nuanced = "EDGE_PRESENT_AND_SIGNIFICANT"
    elif stats_context is not None and metrics.verdict == "NO_EDGE":
        nuanced = "NO_EDGE"
    else:
        nuanced = metrics.verdict

    strategy_spec_id = spec.get("strategy_spec_id", "v1")
    lines = [
        f"# Directional Edge {strategy_spec_id} — Phase 1 Falsification Test",
        "",
        f"**Simple verdict (mean > 0?): `{metrics.verdict}`**",
        "",
        f"**Nuanced verdict: `{nuanced}`**",
        "",
        "## Provenance",
        "",
        f"- strategy_spec_id: `{strategy_spec_id}`",
        f"- spec_id: `{spec['spec_id']}`",
        f"- dataset_version: `{spec['dataset']['version']}`",
        f"- code_revision: `{code_revision}`",
        f"- run_timestamp_utc: {run_timestamp.isoformat()}",
        f"- ES dataset: `{es_path.name}` sha256:`{es_sha256}`",
        f"- Calendar dataset: `{cal_path.name}` sha256:`{cal_sha256}`",
        f"- Actual date range used: {start_d.isoformat()} → {end_d.isoformat()}",
        "",
        "## Setup",
        "",
        f"- underlying: {horiz.get('underlying_v1', 'es_front_month')}",
        f"- session_open_time_et: {sig['session_open_time_et']}",
        f"- signal_time_et: {sig['earliest_eligible_signal_time_et']} (single fixed)",
        f"- end_of_window_time_et: {horiz['end_of_window_time_et']}",
        f"- signal_threshold: {sig['signal_threshold']:.4%}",
        "",
        "## Results",
        "",
        f"| Metric | Value |",
        f"|---|---|",
        f"| total_sessions | {metrics.total_sessions} |",
        f"| entered | {metrics.entered_count} "
        f"({metrics.entered_count / metrics.total_sessions:.1%} of sessions) |",
        f"| skipped (no_signal) | {metrics.skipped_count} |",
        f"| mean_forward_return | {metrics.mean_forward_return:.4%} |",
        f"| median_forward_return | {metrics.median_forward_return:.4%} |",
        f"| hit_rate (forward > 0) | {metrics.hit_rate:.1%} |",
        f"| left_tail_p05 | {metrics.left_tail_p05:.4%} |",
        "",
        "## Verdict Rationale",
        "",
        f"`mean_forward_return = {metrics.mean_forward_return:.4%}` "
        + (
            "> 0 → **EDGE_PRESENT** (simple verdict)"
            if metrics.verdict == "EDGE_PRESENT"
            else "<= 0 → **NO_EDGE**"
        ),
        "",
    ]

    if stats_context is not None:
        sc = stats_context
        lines.extend([
            "## Statistical Significance",
            "",
            "Honest read of the simple verdict above: a positive point estimate is",
            "not the same as a real edge. With a small sample (~150 trades) and",
            "high per-trade noise (~1% std), random variation can produce a",
            "spurious-but-positive mean. Three numbers below decide whether the",
            "edge is statistically distinguishable from zero:",
            "",
            f"| Statistic | Value |",
            f"|---|---|",
            f"| sample size | {sc['n']} |",
            f"| std (per trade) | {sc['std']:.4%} |",
            f"| sem (mean's std error) | {sc['sem']:.4%} |",
            f"| **t-stat** vs zero | **{sc['t_stat']:.2f}** |",
            f"| **p-value** (two-tailed) | **{sc['p_value']:.3f}** |",
            f"| 95% CI on mean | [{sc['ci_low_95']:.4%}, {sc['ci_high_95']:.4%}] |",
            "",
            "**Interpretation rule:** `|t-stat| ≥ 2` (≈ p < 0.05) is the conventional",
            "threshold for rejecting "
            "\"the true mean is zero\". A `t-stat < 2` means the data is *consistent*",
            "with zero — the simple `mean > 0` verdict is unreliable.",
            "",
            "## Per-Year Breakdown (Regime Concentration Check)",
            "",
            "Spec rule `result_is_not_concentrated_in_one_small_regime_cluster`:",
            "if the entire mean is driven by a single year and other years are",
            "flat or negative, the \"edge\" is regime-dependent and not a",
            "stable underlying property.",
            "",
        ])
        lines.append("| year | n | mean | std | hit_rate |")
        lines.append("|---|---|---|---|---|")
        yearly = sc["yearly"]
        for year, row in yearly.iterrows():
            lines.append(
                f"| {year} | {int(row['count'])} | "
                f"{row['mean']:.4%} | {row['std']:.4%} | "
                f"{row['hit_rate']:.1%} |",
            )
        lines.append("")

    lines.extend([
        "## Decision Rules (per spec)",
        "",
        "Continue if:",
        "- standalone_directional_expectancy_is_positive",
        "- result_is_not_concentrated_in_one_small_regime_cluster",
        "- result_is_reproducible",
        "",
        "Kill if:",
        "- standalone_directional_expectancy_is_near_zero_or_negative",
        "- behavior_is_obviously_regime_fragile_without_a_salvageable_base_signal",
        "",
        "## Caveats",
        "",
        "- Underlying is ES front-month continuous, not SPX (SPX 1m TBD per",
        "  `docs/data-acquisition-decision.md` Path A; cross-validation",
        "  against SPX is a follow-up if `EDGE_PRESENT`).",
        "- Regime slices beyond per-year not computed — VIX not in the",
        "  manifest yet.",
        "- No transaction costs (Phase 1 evaluates the underlying view, not",
        "  the trade — costs come in Phase 2 expression comparison).",
        "- Reproducibility: same code_revision + dataset_version + spec_id",
        "  must produce a byte-identical ledger CSV.",
    ])
    return "\n".join(lines)


def run(
    *,
    es_path: Path,
    calendar_path: Path,
    spec_path: Path,
    output_report: Path,
    output_ledger: Path,
    code_revision: str,
    window_start: dt.date | None = None,
    window_end: dt.date | None = None,
    event_calendar_path: Path | None = None,
    vix_data_path: Path | None = None,
) -> DirectionalEdgeResult:
    """Run a directional-edge spec on a (possibly-filtered) calendar window.

    Defaults (no window args, no event calendar) reproduce the original
    v1 evidence: full dataset, no event filter. v1's ledger sha256 is
    therefore preserved across this signature extension.

    For v2 use, pass:
    - ``window_start`` / ``window_end`` to restrict to a split window
      (e.g. validation = 2025-02-18 → 2025-09-22)
    - ``event_calendar_path`` to drop FOMC / CPI / NFP / OPEX days
    """

    spec = yaml.safe_load(spec_path.read_text())

    bars = _load_bars_parquet(es_path)
    cal = pd.read_parquet(calendar_path)

    if window_start is not None:
        cal = cal[cal["date"] >= window_start]
    if window_end is not None:
        cal = cal[cal["date"] <= window_end]
    if event_calendar_path is not None:
        events = pd.read_parquet(event_calendar_path)
        excluded = set(events["date"].tolist())
        cal = cal[~cal["date"].isin(excluded)]

    # Optional VIX filter — active when the spec contains a
    # ``vix_filter`` section with ``enabled: true`` AND a vix_data
    # parquet is supplied. The threshold is read from the spec
    # (pinned at v3-freeze time on TRAIN data) so validation uses the
    # same band boundary as TRAIN.
    vix_cfg = spec.get("vix_filter", {})
    if vix_cfg.get("enabled") and vix_data_path is not None:
        from research.scripts.sweep_directional_edge import (
            compute_prior_vix_by_date, filter_calendar_by_vix_band,
        )
        vix_df = pd.read_parquet(vix_data_path)
        prior_vix = compute_prior_vix_by_date(vix_df)
        cal = filter_calendar_by_vix_band(
            cal,
            prior_vix_by_date=prior_vix,
            band=vix_cfg["band"],
            median=float(vix_cfg["prior_vix_threshold"]),
        )

    sig = spec["signal"]
    horiz = spec["horizon"]

    prices = extract_intraday_prices(
        bars=bars,
        calendar=cal,
        session_open_time=sig["session_open_time_et"],
        signal_time=sig["earliest_eligible_signal_time_et"],
        eow_time=horiz["end_of_window_time_et"],
    )

    if len(prices) == 0:
        raise RuntimeError(
            "no full-trading days with all three target bars found in the "
            "data — check date range, calendar, and that --es covers the "
            "session window 09:30-15:55 ET",
        )

    ledger = compute_ledger(
        prices_per_day=prices,
        signal_threshold=sig["signal_threshold"],
    )
    metrics = aggregate_metrics(ledger)
    stats_ctx = _statistical_context(ledger[ledger["entered"]])

    output_report.parent.mkdir(parents=True, exist_ok=True)
    output_ledger.parent.mkdir(parents=True, exist_ok=True)

    report = format_report(
        metrics=metrics,
        spec=spec,
        es_path=es_path,
        es_sha256=_sha256_of(es_path),
        cal_path=calendar_path,
        cal_sha256=_sha256_of(calendar_path),
        code_revision=code_revision,
        run_timestamp=dt.datetime.now(tz=dt.timezone.utc),
        actual_date_range=(prices["date"].min(), prices["date"].max()),
        stats_context=stats_ctx,
    )
    output_report.write_text(report)
    ledger.to_csv(output_ledger, index=False)

    return DirectionalEdgeResult(metrics=metrics, ledger=ledger)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="run_directional_edge_v1")
    p.add_argument("--es", type=Path,
                   default=Path("research/data/dataset-v1/es_intraday.parquet"))
    p.add_argument("--calendar", type=Path,
                   default=Path("research/data/dataset-v1/trading_calendar.parquet"))
    p.add_argument("--spec", type=Path,
                   default=Path("research/specs/directional-edge-v1.yaml"))
    p.add_argument("--report", type=Path,
                   default=Path("research/reports/directional-edge-v1.md"))
    p.add_argument("--ledger", type=Path,
                   default=Path("research/reports/directional-edge-v1-ledger.csv"))
    p.add_argument("--code-revision", default="HEAD",
                   help="Git revision (sha or symbolic) to record in the report")
    p.add_argument("--window-start", type=dt.date.fromisoformat, default=None,
                   help="Optional inclusive start date — restricts to a split window")
    p.add_argument("--window-end", type=dt.date.fromisoformat, default=None,
                   help="Optional inclusive end date — restricts to a split window")
    p.add_argument("--event-calendar", type=Path, default=None,
                   help="Optional event_calendar.parquet — dates listed are "
                        "excluded from eligibility (v2 event-filter)")
    p.add_argument("--vix-data", type=Path, default=None,
                   help="Optional vix_daily.parquet — used by v3+ when the "
                        "spec contains a vix_filter section with "
                        "enabled: true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    result = run(
        es_path=args.es,
        calendar_path=args.calendar,
        spec_path=args.spec,
        output_report=args.report,
        output_ledger=args.ledger,
        code_revision=args.code_revision,
        window_start=args.window_start,
        window_end=args.window_end,
        event_calendar_path=args.event_calendar,
        vix_data_path=args.vix_data,
    )
    print(f"verdict: {result.metrics.verdict}")
    print(f"trade_count: {result.metrics.trade_count}")
    print(f"mean_forward_return: {result.metrics.mean_forward_return:.4%}")
    print(f"hit_rate: {result.metrics.hit_rate:.1%}")
    print()
    print(f"report -> {args.report}")
    print(f"ledger -> {args.ledger}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
