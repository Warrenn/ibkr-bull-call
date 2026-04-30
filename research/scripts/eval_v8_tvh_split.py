"""Evaluate v8 vol carry on the spec-mandated train/val/holdout split.

Reads the daily ledger produced by ``sim_vol_carry.py`` and applies
``decision_rules`` from
``research/specs/strategy-spec-v8-vol-carry.yaml``:
- Split daily returns 60/20/20 chronologically.
- Compute total return, annualized Sharpe (×√252), max DD, and worst
  single-day loss for each slice.
- Apply gate-by-gate KILL / CONTINUE rules per spec.

Stop at the first failed gate. Record the verdict and which rule fired.

Usage::

    uv run python -m research.scripts.eval_v8_tvh_split \\
        --ledger research/reports/v8-vol-carry-full-ledger.csv \\
        --report research/reports/v8-vol-carry-tvh-split.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass
class SliceMetrics:
    name: str
    n_days: int
    start_date: str
    end_date: str
    total_return: float
    cagr: float
    sharpe: float
    max_dd: float
    worst_single_day: float
    benchmark_total_return: float
    benchmark_cagr: float
    spread_total: float
    cumulative_max_dd_through_slice: float


def _max_dd(returns: pd.Series) -> float:
    nav = (1 + returns).cumprod()
    peak = nav.cummax()
    dd = (nav - peak) / peak
    return float(dd.min())


def _slice_metrics(
    *,
    name: str,
    slice_df: pd.DataFrame,
    cumulative_returns_so_far: pd.Series,
) -> SliceMetrics:
    s = slice_df["strategy_return"].astype(float)
    b = slice_df["spy_return"].astype(float)
    n = len(s)
    n_years = n / 252

    s_total = float((1 + s).prod() - 1)
    b_total = float((1 + b).prod() - 1)
    s_cagr = (1 + s_total) ** (1 / n_years) - 1 if n_years > 0 else float("nan")
    b_cagr = (1 + b_total) ** (1 / n_years) - 1 if n_years > 0 else float("nan")

    s_mean = float(s.mean())
    s_std = float(s.std(ddof=1))
    s_sharpe = (s_mean / s_std * math.sqrt(252)) if s_std > 0 else float("nan")

    return SliceMetrics(
        name=name,
        n_days=n,
        start_date=str(slice_df.iloc[0]["date"])[:10],
        end_date=str(slice_df.iloc[-1]["date"])[:10],
        total_return=s_total,
        cagr=s_cagr,
        sharpe=s_sharpe,
        max_dd=_max_dd(s),
        worst_single_day=float(s.min()),
        benchmark_total_return=b_total,
        benchmark_cagr=b_cagr,
        spread_total=s_total - b_total,
        cumulative_max_dd_through_slice=_max_dd(cumulative_returns_so_far),
    )


@dataclass
class GateResult:
    slice_name: str
    gate_name: str
    rules: list[tuple[str, bool, str]]
    verdict: str


def _evaluate_train(m: SliceMetrics) -> GateResult:
    kill_rules = [
        ("train_total_return < 0", m.total_return < 0, f"total_return={m.total_return:.2%}"),
        ("train_sharpe < 0", m.sharpe < 0, f"sharpe={m.sharpe:.2f}"),
        ("train_max_dd < -40%", m.max_dd < -0.40, f"max_dd={m.max_dd:.2%}"),
        ("train_max_single_day_loss < -25%", m.worst_single_day < -0.25, f"worst_day={m.worst_single_day:.2%}"),
    ]
    if any(p for _, p, _ in kill_rules):
        return GateResult(slice_name=m.name, gate_name="train_kill_if", rules=kill_rules, verdict="KILL")

    continue_rules = [
        ("train_total_return > 0", m.total_return > 0, f"total_return={m.total_return:.2%}"),
        ("train_sharpe >= 0.8", m.sharpe >= 0.8, f"sharpe={m.sharpe:.2f}"),
        ("train_max_dd >= -25%", m.max_dd >= -0.25, f"max_dd={m.max_dd:.2%}"),
        ("train_max_single_day_loss >= -15%", m.worst_single_day >= -0.15, f"worst_day={m.worst_single_day:.2%}"),
    ]
    all_pass = all(p for _, p, _ in continue_rules)
    return GateResult(slice_name=m.name, gate_name="train_continue_if", rules=continue_rules,
                      verdict="CONTINUE" if all_pass else "KILL")


def _evaluate_validation(m: SliceMetrics) -> GateResult:
    rules = [
        ("validation_total_return > 0", m.total_return > 0, f"total_return={m.total_return:.2%}"),
        ("validation_sharpe > 0.5", m.sharpe > 0.5, f"sharpe={m.sharpe:.2f}"),
        ("validation_max_dd > -30%", m.max_dd > -0.30, f"max_dd={m.max_dd:.2%}"),
    ]
    return GateResult(slice_name=m.name, gate_name="validation_continue_if", rules=rules,
                      verdict="CONTINUE" if all(p for _, p, _ in rules) else "KILL")


def _evaluate_holdout(m: SliceMetrics) -> GateResult:
    spy_minus_5pct = m.benchmark_total_return - 0.05
    rules = [
        ("holdout_total_return > 0", m.total_return > 0, f"total_return={m.total_return:.2%}"),
        ("holdout_sharpe > 0.3", m.sharpe > 0.3, f"sharpe={m.sharpe:.2f}"),
        ("holdout_outperforms_spy_or_within_5pct",
         m.total_return >= spy_minus_5pct,
         f"port_return={m.total_return:.2%} vs spy_return-5%={spy_minus_5pct:.2%}"),
    ]
    return GateResult(slice_name=m.name, gate_name="holdout_continue_if", rules=rules,
                      verdict="PROMOTE" if all(p for _, p, _ in rules) else "KILL")


def _evaluate_always_kill(m: SliceMetrics) -> GateResult:
    rules = [
        ("cumulative_max_dd < -40%",
         m.cumulative_max_dd_through_slice < -0.40,
         f"cum_max_dd={m.cumulative_max_dd_through_slice:.2%}"),
        ("max_single_day_loss < -25%",
         m.worst_single_day < -0.25,
         f"worst_day={m.worst_single_day:.2%}"),
    ]
    fired = any(p for _, p, _ in rules)
    return GateResult(slice_name=m.name, gate_name="always_kill_if", rules=rules,
                      verdict="KILL" if fired else "CONTINUE")


def evaluate(*, ledger: pd.DataFrame) -> tuple[list[SliceMetrics], list[GateResult], str, str]:
    ledger["date"] = pd.to_datetime(ledger["date"])
    ledger = ledger.sort_values("date").reset_index(drop=True)

    n = len(ledger)
    train_end = int(n * 0.6)
    val_end = int(n * 0.8)

    cumulative_returns = ledger["strategy_return"].astype(float)
    train_metrics = _slice_metrics(
        name="train", slice_df=ledger.iloc[:train_end],
        cumulative_returns_so_far=cumulative_returns.iloc[:train_end],
    )
    val_metrics = _slice_metrics(
        name="validation", slice_df=ledger.iloc[train_end:val_end],
        cumulative_returns_so_far=cumulative_returns.iloc[:val_end],
    )
    holdout_metrics = _slice_metrics(
        name="holdout", slice_df=ledger.iloc[val_end:],
        cumulative_returns_so_far=cumulative_returns,
    )

    metrics = [train_metrics, val_metrics, holdout_metrics]
    gates: list[GateResult] = []

    for m, eval_fn in [
        (train_metrics, _evaluate_train),
        (val_metrics, _evaluate_validation),
        (holdout_metrics, _evaluate_holdout),
    ]:
        ak = _evaluate_always_kill(m)
        gates.append(ak)
        if ak.verdict == "KILL":
            return metrics, gates, "KILL", f"always_kill_if ({m.name})"
        gate = eval_fn(m)
        gates.append(gate)
        if gate.verdict == "KILL":
            return metrics, gates, "KILL", gate.gate_name

    return metrics, gates, "PROMOTE", "all_gates_passed"


def format_report(*, metrics: list[SliceMetrics], gates: list[GateResult],
                  verdict: str, terminating_gate: str,
                  code_revision: str, run_timestamp: dt.datetime) -> str:
    lines = [
        "# v8 Vol Carry — Train/Validation/Holdout Split Evaluation",
        "",
        f"**Final verdict**: **{verdict}** — terminated at `{terminating_gate}`",
        "",
        "## Provenance",
        "",
        f"- code_revision: `{code_revision}`",
        f"- run_timestamp_utc: {run_timestamp.isoformat()}",
        f"- spec: `research/specs/strategy-spec-v8-vol-carry.yaml`",
        "",
        "## Slice metrics",
        "",
        "| Slice | n | Date range | Total ret | CAGR | Sharpe | Max DD | Worst day | SPY total | Spread |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for m in metrics:
        lines.append(
            f"| {m.name} | {m.n_days} | {m.start_date} → {m.end_date} | "
            f"{m.total_return:+.2%} | {m.cagr:+.2%} | {m.sharpe:+.2f} | {m.max_dd:.2%} | "
            f"{m.worst_single_day:.2%} | {m.benchmark_total_return:+.2%} | {m.spread_total:+.2%} |"
        )

    lines.extend(["", "## Gate-by-gate evaluation", ""])
    for g in gates:
        lines.append(f"### `{g.gate_name}` on `{g.slice_name}` → **{g.verdict}**")
        lines.append("")
        lines.append("| Rule | Status | Evidence |")
        lines.append("|---|---|---|")
        for rule, passed, evidence in g.rules:
            if "kill_if" in g.gate_name:
                status = "🔴 FIRED" if passed else "✅ ok"
            else:
                status = "✅ pass" if passed else "🔴 fail"
            lines.append(f"| `{rule}` | {status} | {evidence} |")
        lines.append("")

    lines.extend(["## Verdict reasoning", ""])
    if verdict == "PROMOTE":
        lines.append("All gates passed. v8 is promoted to paper-trading candidate.")
    else:
        lines.append(f"Terminated at `{terminating_gate}`. v8 is KILLED.")
        lines.append("")
        lines.append("Per spec freeze, no parameter retuning. Move to next strategy or")
        lines.append("create v8a with explicit acknowledgment that this v8 holdout is consumed.")
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="eval_v8_tvh_split")
    p.add_argument("--ledger", type=Path,
                   default=Path("research/reports/v8-vol-carry-full-ledger.csv"))
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--code-revision", default="HEAD")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    ledger = pd.read_csv(args.ledger)

    metrics, gates, verdict, terminating_gate = evaluate(ledger=ledger)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(format_report(
        metrics=metrics, gates=gates, verdict=verdict,
        terminating_gate=terminating_gate,
        code_revision=args.code_revision,
        run_timestamp=dt.datetime.now(tz=dt.timezone.utc),
    ))

    print(f"verdict: {verdict}")
    print(f"terminated at: {terminating_gate}")
    print()
    for m in metrics:
        print(f"  [{m.name}] n={m.n_days} ({m.start_date} → {m.end_date})")
        print(f"    return={m.total_return:+.2%}  sharpe={m.sharpe:+.2f}  max_dd={m.max_dd:.2%}  worst_day={m.worst_single_day:.2%}")
    print()
    for g in gates:
        print(f"  {g.gate_name}@{g.slice_name} -> {g.verdict}")
    print(f"\nreport -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
