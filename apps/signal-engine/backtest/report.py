"""report — console and CSV output for backtest results.

Two output formats:
    console  — rich prettytable summary + explicit PASS/FAIL verdict per gate criterion
    csv      — per-fold metrics written to results/<run_id>/folds.csv
               + aggregated metrics to results/<run_id>/summary.csv
               + equity curve to results/<run_id>/equity.csv
               + trade log to results/<run_id>/trades.csv
"""

from __future__ import annotations

import csv
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from prettytable import PrettyTable

from backtest.metrics import (
    GATE_MAX_DRAWDOWN_PCT,
    GATE_MIN_PROFIT_FACTOR,
    GATE_MIN_SHARPE,
    GATE_MIN_WIN_RATE,
    AggregateMetrics,
    FoldMetrics,
)

if TYPE_CHECKING:
    from backtest.order_simulator import ClosedTrade

_HERE = Path(__file__).parent
RESULTS_DIR = _HERE / "results"


def _pct(v: float) -> str:
    return f"{v * 100:.2f}%"


def _f(v: float, decimals: int = 2) -> str:
    return f"{v:.{decimals}f}"


def _gate_line(label: str, value: float, threshold: float, passes: bool, higher_is_better: bool = True) -> str:
    tick = "✅ PASS" if passes else "❌ FAIL"
    direction = "≥" if higher_is_better else "≤"
    return f"  {tick}  {label}: {_f(value, 2)}  (gate: {direction} {threshold})"


# ── Console output ─────────────────────────────────────────────────────────────

def print_fold_table(folds: list[FoldMetrics]) -> None:
    """Print per-fold performance table to stdout."""
    t = PrettyTable()
    t.field_names = [
        "Fold", "Period", "CAGR", "Sharpe", "Sortino",
        "Max DD", "Win%", "P.Factor", "Trades", "Alpha", "Gate",
    ]
    t.align = "r"
    t.align["Period"] = "l"

    for f in folds:
        t.add_row([
            f.fold_id,
            f"{f.start_date} → {f.end_date}",
            _pct(f.cagr),
            _f(f.sharpe_ratio),
            _f(f.sortino_ratio),
            f"{f.max_drawdown_pct:.1f}%",
            f"{f.win_rate * 100:.1f}%",
            _f(f.profit_factor),
            f.total_trades,
            _pct(f.alpha_vs_nifty),
            "✅" if f.passes_gate else "❌",
        ])

    print("\n" + "=" * 90)
    print("  WALK-FORWARD FOLD RESULTS")
    print("=" * 90)
    print(t)


def print_aggregate_summary(agg: AggregateMetrics, config_desc: str = "") -> None:
    """Print the aggregated performance summary + explicit gate verdict."""
    print("\n" + "=" * 90)
    print("  AGGREGATE PERFORMANCE SUMMARY")
    if config_desc:
        print(f"  {config_desc}")
    print("=" * 90)

    # Summary table
    t = PrettyTable()
    t.field_names = ["Metric", "Value", "Benchmark (Nifty)"]
    t.align = "r"
    t.align["Metric"] = "l"

    t.add_row(["Overall CAGR",         _pct(agg.overall_cagr),             _pct(agg.vs_nifty_cagr)])
    t.add_row(["Alpha vs Nifty",        _pct(agg.overall_alpha),            "—"])
    t.add_row(["Sharpe Ratio",          _f(agg.overall_sharpe),             _f(agg.baseline_nifty_sharpe)])
    t.add_row(["Sortino Ratio",         _f(agg.overall_sortino),            _f(agg.baseline_nifty_sortino)])
    t.add_row(["Max Drawdown",          f"{agg.overall_max_drawdown:.1f}%", f"{agg.baseline_nifty_max_dd:.1f}%"])
    t.add_row(["Win Rate",              f"{agg.overall_win_rate * 100:.1f}%", "—"])
    t.add_row(["Profit Factor",         _f(agg.overall_profit_factor), "—"])
    t.add_row(["Total Trades",          str(agg.total_trades),        "—"])
    t.add_row(["Folds Passing Gate",    f"{agg.folds_passing_gate}/{agg.total_folds}", "—"])
    t.add_row(["Mean Fold CAGR",        _pct(agg.mean_cagr),          "—"])
    t.add_row(["Mean Fold Sharpe",      _f(agg.mean_sharpe),          "—"])
    t.add_row(["Worst Drawdown (fold)", f"{agg.worst_drawdown:.1f}%", "—"])
    print(t)

    # Gate verdict
    print("\n  PHASE 1 DECISION GATE")
    print("  " + "-" * 50)
    print(_gate_line("Sharpe", agg.overall_sharpe, GATE_MIN_SHARPE,
                     agg.overall_sharpe >= GATE_MIN_SHARPE))
    print(_gate_line("Max Drawdown", agg.overall_max_drawdown, GATE_MAX_DRAWDOWN_PCT,
                     agg.overall_max_drawdown <= GATE_MAX_DRAWDOWN_PCT, higher_is_better=False))
    print(_gate_line("Win Rate", agg.overall_win_rate, GATE_MIN_WIN_RATE,
                     agg.overall_win_rate >= GATE_MIN_WIN_RATE))
    print(_gate_line("Profit Factor", agg.overall_profit_factor, GATE_MIN_PROFIT_FACTOR,
                     agg.overall_profit_factor >= GATE_MIN_PROFIT_FACTOR))
    alpha_passes = agg.overall_alpha > 0
    print(f"  {'✅ PASS' if alpha_passes else '❌ FAIL'}  Alpha vs Nifty: "
          f"{_pct(agg.overall_alpha)}  (gate: > 0%)")

    verdict = "✅  GATE PASSED — proceed to Phase 2" if agg.passes_gate else \
              "❌  GATE FAILED — diagnose strategy before live trading"
    print("\n  " + "=" * 60)
    print(f"  VERDICT: {verdict}")
    print("  " + "=" * 60 + "\n")


# ── CSV output ─────────────────────────────────────────────────────────────────

def _run_dir(run_id: str) -> Path:
    d = RESULTS_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_fold_csv(folds: list[FoldMetrics], run_id: str) -> Path:
    """Write per-fold metrics to results/<run_id>/folds.csv."""
    out = _run_dir(run_id) / "folds.csv"
    headers = [
        "fold_id", "start_date", "end_date", "initial_value", "final_value",
        "cagr_pct", "sharpe", "sortino", "max_drawdown_pct", "max_dd_duration_days",
        "win_rate_pct", "profit_factor", "total_trades", "winning_trades", "losing_trades",
        "avg_hold_days", "avg_win_pct", "avg_loss_pct", "alpha_pct",
        "vs_nifty_cagr_pct", "annualised_turnover", "passes_gate",
        # W1.3: Nifty baseline risk metrics for apples-to-apples fold comparison
        "baseline_nifty_sharpe", "baseline_nifty_sortino", "baseline_nifty_max_dd_pct",
    ]
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for fold in folds:
            w.writerow({
                "fold_id":                   fold.fold_id,
                "start_date":                fold.start_date,
                "end_date":                  fold.end_date,
                "initial_value":             round(fold.initial_value, 2),
                "final_value":               round(fold.final_value, 2),
                "cagr_pct":                  round(fold.cagr * 100, 4),
                "sharpe":                    round(fold.sharpe_ratio, 4),
                "sortino":                   round(fold.sortino_ratio, 4),
                "max_drawdown_pct":          round(fold.max_drawdown_pct, 4),
                "max_dd_duration_days":      fold.max_drawdown_duration,
                "win_rate_pct":              round(fold.win_rate * 100, 2),
                "profit_factor":             round(fold.profit_factor, 4),
                "total_trades":              fold.total_trades,
                "winning_trades":            fold.winning_trades,
                "losing_trades":             fold.losing_trades,
                "avg_hold_days":             round(fold.avg_hold_days, 2),
                "avg_win_pct":               round(fold.avg_win_pct, 4),
                "avg_loss_pct":              round(fold.avg_loss_pct, 4),
                "alpha_pct":                 round(fold.alpha_vs_nifty * 100, 4),
                "vs_nifty_cagr_pct":         round(fold.vs_nifty_cagr * 100, 4),
                "annualised_turnover":        round(fold.annualised_turnover, 4),
                "passes_gate":               fold.passes_gate,
                "baseline_nifty_sharpe":     round(fold.baseline_nifty_sharpe, 4),
                "baseline_nifty_sortino":    round(fold.baseline_nifty_sortino, 4),
                "baseline_nifty_max_dd_pct": round(fold.baseline_nifty_max_dd, 4),
            })
    return out


def save_summary_csv(agg: AggregateMetrics, run_id: str) -> Path:
    """Write aggregated summary to results/<run_id>/summary.csv."""
    out = _run_dir(run_id) / "summary.csv"
    rows = [
        ("overall_cagr_pct",        round(agg.overall_cagr * 100, 4)),
        ("overall_alpha_pct",       round(agg.overall_alpha * 100, 4)),
        ("vs_nifty_cagr_pct",       round(agg.vs_nifty_cagr * 100, 4)),
        ("overall_sharpe",          round(agg.overall_sharpe, 4)),
        ("overall_sortino",         round(agg.overall_sortino, 4)),
        ("overall_max_drawdown_pct",round(agg.overall_max_drawdown, 4)),
        ("overall_win_rate_pct",    round(agg.overall_win_rate * 100, 2)),
        ("overall_profit_factor",   round(agg.overall_profit_factor, 4)),
        ("total_trades",            agg.total_trades),
        ("total_folds",             agg.total_folds),
        ("folds_passing_gate",      agg.folds_passing_gate),
        ("mean_cagr_pct",           round(agg.mean_cagr * 100, 4)),
        ("mean_sharpe",             round(agg.mean_sharpe, 4)),
        ("mean_sortino",            round(agg.mean_sortino, 4)),
        ("mean_max_drawdown_pct",   round(agg.mean_max_drawdown, 4)),
        ("worst_drawdown_pct",      round(agg.worst_drawdown, 4)),
        ("mean_win_rate_pct",       round(agg.mean_win_rate * 100, 2)),
        ("mean_profit_factor",      round(agg.mean_profit_factor, 4)),
        ("mean_alpha_pct",          round(agg.mean_alpha * 100, 4)),
        ("passes_gate",             agg.passes_gate),
        # W1.3: Nifty buy-and-hold baseline risk metrics
        ("baseline_nifty_sharpe",   round(agg.baseline_nifty_sharpe, 4)),
        ("baseline_nifty_sortino",  round(agg.baseline_nifty_sortino, 4)),
        ("baseline_nifty_max_dd_pct", round(agg.baseline_nifty_max_dd, 4)),
    ]
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        w.writerows(rows)
    return out


def save_equity_csv(folds: list[FoldMetrics], run_id: str) -> Path:
    """Write concatenated daily equity curve to results/<run_id>/equity.csv."""
    out = _run_dir(run_id) / "equity.csv"
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "portfolio_value", "fold_id"])
        for fold in sorted(folds, key=lambda x: x.start_date):
            for date_str, val in zip(fold.daily_dates, fold.daily_values):
                w.writerow([date_str, round(val, 2), fold.fold_id])
    return out


def save_trades_csv(folds: list[FoldMetrics], run_id: str) -> Path:
    """Write all closed trades to results/<run_id>/trades.csv.

    Includes signal-attribution columns (W1.2) for post-run diagnostics:
    which signals fired, regime at entry, MFE/MAE excursions.
    """
    out = _run_dir(run_id) / "trades.csv"
    headers = [
        "fold_id", "symbol", "sector", "entry_date", "exit_date",
        "entry_price", "exit_price", "shares", "position_value",
        "confidence", "kelly_fraction", "exit_reason",
        "pnl", "return_pct", "was_correct",
        # W1.2: signal attribution
        "signal_score_raw",
        "vix_at_entry", "nifty_above_ema50_at_entry", "fii_net_cr_at_entry",
        "sector_score_at_entry", "had_tier_a_news",
        # W1.2: excursion diagnostics
        "mfe_pct", "mae_pct",
        # W1.2: human-readable signal list (pipe-separated)
        "triggered_signals",
    ]
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for fold in sorted(folds, key=lambda x: x.start_date):
            for t in fold.trades:
                w.writerow({
                    "fold_id":                    fold.fold_id,
                    "symbol":                     t.symbol,
                    "sector":                     t.sector,
                    "entry_date":                 t.entry_date,
                    "exit_date":                  t.exit_date,
                    "entry_price":                t.entry_price,
                    "exit_price":                 t.exit_price,
                    "shares":                     t.shares,
                    "position_value":             t.position_value,
                    "confidence":                 t.confidence,
                    "kelly_fraction":             t.kelly_fraction,
                    "exit_reason":                t.exit_reason,
                    "pnl":                        t.pnl,
                    "return_pct":                 t.return_pct,
                    "was_correct":                t.was_correct,
                    "signal_score_raw":           t.signal_score_raw,
                    "vix_at_entry":               t.vix_at_entry,
                    "nifty_above_ema50_at_entry": t.nifty_above_ema50_at_entry,
                    "fii_net_cr_at_entry":        t.fii_net_cr_at_entry,
                    "sector_score_at_entry":      t.sector_score_at_entry,
                    "had_tier_a_news":            t.had_tier_a_news,
                    "mfe_pct":                    t.mfe_pct,
                    "mae_pct":                    t.mae_pct,
                    "triggered_signals":          " | ".join(t.triggered_signals),
                })
    return out


def save_all(agg: AggregateMetrics, run_id: str | None = None) -> dict[str, Path]:
    """Save all CSV files and return paths dict."""
    if run_id is None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    paths = {
        "folds":   save_fold_csv(agg.fold_results, run_id),
        "summary": save_summary_csv(agg, run_id),
        "equity":  save_equity_csv(agg.fold_results, run_id),
        "trades":  save_trades_csv(agg.fold_results, run_id),
    }
    print(f"\n  Results saved to: {_run_dir(run_id)}/")
    for name, path in paths.items():
        print(f"    {name:10s}: {path.name}")
    return paths


def print_and_save(
    agg: AggregateMetrics,
    run_id: str | None = None,
    config_desc: str = "",
) -> dict[str, Path]:
    """Print console report and save all CSV files. Returns paths dict."""
    print_fold_table(agg.fold_results)
    print_aggregate_summary(agg, config_desc)
    return save_all(agg, run_id)
