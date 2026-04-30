"""v5 Phase 1 falsification: realized-vs-implied intraday vol test.

A short-iron-condor or similar premium-selling strategy depends on
**realized intraday volatility being LESS THAN implied volatility on
average** — the so-called "vol risk premium". This script tests
whether that premium exists in dataset-v1's expanded 60-month ES +
VIX coverage, before any options-data spending.

Method per day t (NYSE full-trading days only):

1. ``realized_intraday_pct`` = ``|ES_open_15:55 - ES_open_09:30| /
   ES_open_09:30``
   (signed magnitude of the intraday move)
2. ``implied_1day_pct`` = ``prior_VIX_close / 100 / sqrt(252)``
   (VIX-implied 1-sigma daily return)
3. ``ratio`` = ``realized_pct / implied_pct``

If ``mean(ratio) < 1`` (and statistically distinguishable from 1)
across the 60-month sample, vol risk premium exists and short-vol
strategies have a fundamental tailwind. If ``mean(ratio) >= 1``,
realized vol matches or exceeds implied — no premium to harvest.

Output: markdown report with overall stats, per-year breakdown,
per-VIX-regime breakdown.

Usage::

    uv run python -m research.scripts.test_realized_vs_implied_vol \\
        --es research/data/dataset-v1/es_intraday.parquet \\
        --vix research/data/dataset-v1/vix_daily.parquet \\
        --calendar research/data/dataset-v1/trading_calendar.parquet \\
        --report research/reports/v5-vol-premium-test.md \\
        --csv research/reports/v5-vol-premium-test-ledger.csv
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from research.scripts.run_directional_edge_v1 import _load_bars_parquet


_TRADING_DAYS_PER_YEAR = 252


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute_daily_realized_implied(
    *,
    bars: pd.DataFrame,
    calendar: pd.DataFrame,
    vix: pd.DataFrame,
    et_tz: str = "America/New_York",
    open_time_et: str = "09:30",
    close_time_et: str = "15:55",
) -> pd.DataFrame:
    """Return per-NYSE-full-trading-day frame with realized + implied move.

    Columns: ``date``, ``open_price``, ``close_price``, ``realized_pct``,
    ``prior_vix``, ``implied_pct``, ``ratio``.
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
    close_t = f"{close_time_et}:00"

    opens = (
        bars[bars["time_et"] == open_t][["date_et", "open"]]
        .copy().rename(columns={"open": "open_price"})
        .drop_duplicates(subset=["date_et"]).set_index("date_et")
    )
    closes = (
        bars[bars["time_et"] == close_t][["date_et", "open"]]
        .copy().rename(columns={"open": "close_price"})
        .drop_duplicates(subset=["date_et"]).set_index("date_et")
    )
    df = pd.concat([opens, closes], axis=1).dropna()
    df = df.reset_index().rename(columns={"date_et": "date"})

    # Realized intraday return (magnitude)
    df["realized_pct"] = (df["close_price"] - df["open_price"]).abs() / df["open_price"]

    # Prior-day VIX close
    vix_sorted = vix.sort_values("date").reset_index(drop=True)
    prior_vix_map: dict[dt.date, float] = {
        vix_sorted["date"].iloc[i]: float(vix_sorted["close"].iloc[i - 1])
        for i in range(1, len(vix_sorted))
    }
    df["prior_vix"] = df["date"].map(prior_vix_map)

    # Implied 1-day 1-sigma return
    df["implied_pct"] = df["prior_vix"] / 100 / math.sqrt(_TRADING_DAYS_PER_YEAR)

    # Drop days missing prior_vix (start of dataset)
    df = df.dropna(subset=["prior_vix", "implied_pct"]).copy()

    df["ratio"] = df["realized_pct"] / df["implied_pct"]

    return df.sort_values("date").reset_index(drop=True)


def _stats_block(series: pd.Series) -> dict[str, float]:
    """Mean / median / std / quantiles for a series."""

    n = len(series)
    return {
        "n": int(n),
        "mean": float(series.mean()),
        "median": float(series.median()),
        "std": float(series.std(ddof=1)) if n > 1 else float("nan"),
        "p25": float(series.quantile(0.25)),
        "p75": float(series.quantile(0.75)),
        "p05": float(series.quantile(0.05)),
        "p95": float(series.quantile(0.95)),
    }


def _t_stat_vs_1(series: pd.Series) -> tuple[float, float]:
    """Return (t-stat, p-value) for H0: mean(series) == 1."""

    from scipy import stats

    n = len(series)
    if n < 2:
        return float("nan"), float("nan")
    mean = float(series.mean())
    sem = float(series.std(ddof=1)) / math.sqrt(n)
    if sem == 0:
        return float("nan"), float("nan")
    t = (mean - 1.0) / sem
    p = float(2 * stats.t.sf(abs(t), df=n - 1))
    return t, p


def format_report(
    *,
    df: pd.DataFrame,
    es_path: Path,
    vix_path: Path,
    cal_path: Path,
    code_revision: str,
    run_timestamp: dt.datetime,
) -> str:
    overall_realized = _stats_block(df["realized_pct"])
    overall_implied = _stats_block(df["implied_pct"])
    overall_ratio = _stats_block(df["ratio"])
    t_stat, p_value = _t_stat_vs_1(df["ratio"])

    # Verdict logic
    # Mean ratio < 1 with statistical significance = vol risk premium exists.
    # We also report median because mean can be skewed by tails.
    if overall_ratio["mean"] < 1.0 and t_stat < -2.0:
        verdict = "VOL_PREMIUM_EXISTS"
    elif overall_ratio["mean"] < 1.0:
        verdict = "VOL_PREMIUM_LIKELY (ratio mean < 1 but t-stat not strongly significant)"
    elif overall_ratio["mean"] >= 1.0:
        verdict = "NO_VOL_PREMIUM_OR_INVERTED (realized >= implied on average)"
    else:
        verdict = "INCONCLUSIVE"

    # Per-year
    df_y = df.assign(year=lambda d: pd.to_datetime(d["date"]).dt.year)
    by_year = df_y.groupby("year").agg(
        n=("ratio", "size"),
        realized_mean=("realized_pct", "mean"),
        implied_mean=("implied_pct", "mean"),
        ratio_mean=("ratio", "mean"),
        ratio_median=("ratio", "median"),
    ).reset_index()

    # Per-VIX-regime (terciles by prior VIX)
    df_v = df.copy()
    vix_terciles = df_v["prior_vix"].quantile([1 / 3, 2 / 3]).tolist()
    df_v["vix_band"] = pd.cut(
        df_v["prior_vix"],
        bins=[-np.inf, vix_terciles[0], vix_terciles[1], np.inf],
        labels=["low", "mid", "high"],
    )
    by_vix = df_v.groupby("vix_band", observed=True).agg(
        n=("ratio", "size"),
        realized_mean=("realized_pct", "mean"),
        implied_mean=("implied_pct", "mean"),
        ratio_mean=("ratio", "mean"),
        ratio_median=("ratio", "median"),
    ).reset_index()

    lines = [
        "# v5 Phase 1 — Realized vs Implied Intraday Vol Test",
        "",
        f"**Verdict: `{verdict}`**",
        "",
        "## Mechanic",
        "",
        "Per-NYSE-full-trading-day, compute:",
        "",
        "- ``realized_pct`` = |ES_close(15:55) - ES_open(09:30)| / ES_open",
        "- ``implied_pct`` = prior-day VIX close / 100 / √252  "
        "(VIX-implied 1-σ daily return)",
        "- ``ratio`` = realized / implied",
        "",
        "If mean(ratio) < 1 with statistical significance, vol risk premium",
        "exists — short-vol strategies (e.g. far-OTM short iron condor) have",
        "a fundamental tailwind to harvest.",
        "",
        "## Provenance",
        "",
        f"- code_revision: `{code_revision}`",
        f"- run_timestamp_utc: {run_timestamp.isoformat()}",
        f"- ES dataset: `{es_path.name}` sha256:`{_sha256_of(es_path)}`",
        f"- VIX dataset: `{vix_path.name}` sha256:`{_sha256_of(vix_path)}`",
        f"- Calendar: `{cal_path.name}` sha256:`{_sha256_of(cal_path)}`",
        f"- Date range: {df['date'].min()} → {df['date'].max()}",
        f"- Days analyzed: {len(df)}",
        "",
        "## Aggregate Distribution",
        "",
        "| Metric | realized_pct | implied_pct | ratio (realized/implied) |",
        "|---|---|---|---|",
        f"| n | {overall_realized['n']} | {overall_implied['n']} | {overall_ratio['n']} |",
        f"| mean | {overall_realized['mean']:.4%} | {overall_implied['mean']:.4%} | {overall_ratio['mean']:.3f} |",
        f"| median | {overall_realized['median']:.4%} | {overall_implied['median']:.4%} | {overall_ratio['median']:.3f} |",
        f"| std | {overall_realized['std']:.4%} | {overall_implied['std']:.4%} | {overall_ratio['std']:.3f} |",
        f"| p05 | {overall_realized['p05']:.4%} | {overall_implied['p05']:.4%} | {overall_ratio['p05']:.3f} |",
        f"| p25 | {overall_realized['p25']:.4%} | {overall_implied['p25']:.4%} | {overall_ratio['p25']:.3f} |",
        f"| p75 | {overall_realized['p75']:.4%} | {overall_implied['p75']:.4%} | {overall_ratio['p75']:.3f} |",
        f"| p95 | {overall_realized['p95']:.4%} | {overall_implied['p95']:.4%} | {overall_ratio['p95']:.3f} |",
        "",
        "## Statistical Test (H0: mean(ratio) == 1)",
        "",
        f"- t-stat vs 1.0: **{t_stat:+.2f}**",
        f"- p-value (two-tailed): **{p_value:.4f}**",
        f"- 95% CI on mean ratio: [{overall_ratio['mean'] - 1.96 * overall_ratio['std'] / math.sqrt(overall_ratio['n']):.3f}, "
        f"{overall_ratio['mean'] + 1.96 * overall_ratio['std'] / math.sqrt(overall_ratio['n']):.3f}]",
        "",
        "## Per-Year Breakdown",
        "",
        "| year | n | realized | implied | ratio_mean | ratio_median |",
        "|---|---|---|---|---|---|",
    ]
    for _, row in by_year.iterrows():
        lines.append(
            f"| {int(row['year'])} | {int(row['n'])} | "
            f"{row['realized_mean']:.4%} | {row['implied_mean']:.4%} | "
            f"{row['ratio_mean']:.3f} | {row['ratio_median']:.3f} |"
        )
    lines.extend([
        "",
        "## Per-VIX-Tercile Breakdown",
        "",
        f"VIX terciles (prior-day close): low ≤ {vix_terciles[0]:.2f}, "
        f"mid ({vix_terciles[0]:.2f}, {vix_terciles[1]:.2f}], "
        f"high > {vix_terciles[1]:.2f}",
        "",
        "| vix_band | n | realized | implied | ratio_mean | ratio_median |",
        "|---|---|---|---|---|---|",
    ])
    for _, row in by_vix.iterrows():
        lines.append(
            f"| {row['vix_band']} | {int(row['n'])} | "
            f"{row['realized_mean']:.4%} | {row['implied_mean']:.4%} | "
            f"{row['ratio_mean']:.3f} | {row['ratio_median']:.3f} |"
        )

    lines.extend([
        "",
        "## Reading the Verdict",
        "",
        "- **VOL_PREMIUM_EXISTS** (mean ratio < 1 AND t-stat < -2): "
        "implied vol systematically overprices realized vol. Short-vol",
        "structures (sell premium) have positive expectation before",
        "transaction costs.",
        "- **VOL_PREMIUM_LIKELY** (mean ratio < 1 but |t| < 2): directionally",
        "favorable but not statistically significant.",
        "- **NO_VOL_PREMIUM** (mean ratio >= 1): realized vol matches or",
        "exceeds implied — short-premium strategies have negative or zero",
        "expectation. Long-vol structures (buy premium) would be the",
        "natural pivot.",
        "",
        "## Caveats",
        "",
        "- This is a 1-σ comparison. Far-OTM iron condors profit not from",
        "  the average move but from the TAIL of the realized distribution",
        "  — the 95th / 99th percentile move events. Even if mean(ratio) <",
        "  1, a few large-move days can wipe out months of small premium",
        "  collection. Tail behavior matters as much as mean behavior.",
        "- Daily VIX is annualized 30-day vol; using it as a 1-day implied",
        "  approximation is a common but imperfect convention.",
        "- This test does not include transaction costs. A real far-OTM",
        "  iron condor pays bid-ask spreads on 4 legs; the realized edge",
        "  must clear those costs before any net P&L.",
        "- Half-day sessions are excluded.",
    ])
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="test_realized_vs_implied_vol")
    p.add_argument("--es", type=Path,
                   default=Path("research/data/dataset-v1/es_intraday.parquet"))
    p.add_argument("--vix", type=Path,
                   default=Path("research/data/dataset-v1/vix_daily.parquet"))
    p.add_argument("--calendar", type=Path,
                   default=Path("research/data/dataset-v1/trading_calendar.parquet"))
    p.add_argument("--report", type=Path,
                   default=Path("research/reports/v5-vol-premium-test.md"))
    p.add_argument("--csv", type=Path,
                   default=Path("research/reports/v5-vol-premium-test-ledger.csv"))
    p.add_argument("--code-revision", default="HEAD")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    bars = _load_bars_parquet(args.es)
    cal = pd.read_parquet(args.calendar)
    vix = pd.read_parquet(args.vix)

    df = compute_daily_realized_implied(bars=bars, calendar=cal, vix=vix)
    if df.empty:
        raise RuntimeError("no overlapping NYSE full-trading-day data found")

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(format_report(
        df=df,
        es_path=args.es,
        vix_path=args.vix,
        cal_path=args.calendar,
        code_revision=args.code_revision,
        run_timestamp=dt.datetime.now(tz=dt.timezone.utc),
    ))
    df.to_csv(args.csv, index=False)

    mean_ratio = df["ratio"].mean()
    median_ratio = df["ratio"].median()
    t_stat, p_value = _t_stat_vs_1(df["ratio"])

    print(f"days analyzed: {len(df)}")
    print(f"date range: {df['date'].min()} → {df['date'].max()}")
    print(f"realized_pct mean: {df['realized_pct'].mean():.4%}")
    print(f"implied_pct mean: {df['implied_pct'].mean():.4%}")
    print(f"ratio (realized/implied) mean: {mean_ratio:.3f}")
    print(f"ratio median: {median_ratio:.3f}")
    print(f"t-stat vs 1.0: {t_stat:+.2f}, p-value: {p_value:.4f}")
    print()
    print(f"report -> {args.report}")
    print(f"csv    -> {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
