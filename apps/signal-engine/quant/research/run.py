"""Gate-check CLI for Strategy A (PEAD).  Month 3.

Usage:
  python -m quant.research.run --strategy pead_midcap --split dev
  python -m quant.research.run --strategy pead_midcap --split train

The --split argument must be "train" or "dev".  The hold-out split is
intentionally not accessible here — it requires the separate
holdout_lock.read_holdout() ceremony (plan §3.1 + §14).

Exit codes:
  0 — all gate criteria pass
  1 — one or more gate criteria fail (strategy killed)
  2 — data / config error

Output: gate report printed to stdout + logged to MLflow.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import mlflow
import numpy as np
import pandas as pd

from quant.data.earnings_ingest import load_earnings
from quant.data.pit_loader import load as pit_load
from quant.research.dsr import deflated_sharpe
from quant.research.holdout_lock import DEV_END, DEV_START, TRAIN_END
from quant.research.purged_kfold import purged_kfold_split
from quant.strategies.pead_midcap import (
    compute_gate_metrics,
    run_anti_strategy,
    run_capacity_check,
    run_cost_stress,
    simulate_trades,
)

logger = logging.getLogger(__name__)

SPLIT_DATES = {
    "train": ("2015-01-01", "2023-06-30"),
    "dev":   (DEV_START, DEV_END),
}

_HYPOTHESIS_PATH = Path(__file__).parents[4] / "research" / "hypotheses" / "2026-05-19-pead-midcap.md"

# Nifty Midcap 150 — hardcoded constituent list placeholder.
# In production this should be loaded from a versioned file keyed by date.
# For now, Month 3 uses a static list for development purposes only.
_MIDCAP150_STATIC: list[str] = []  # populated from NSE constituent files


def _load_midcap150() -> list[str]:
    """Load Nifty Midcap 150 constituent list."""
    data_dir = Path("data/lake")
    constituent_file = data_dir / "midcap150_constituents.csv"
    if constituent_file.exists():
        df = pd.read_csv(constituent_file)
        return df["symbol"].str.upper().tolist()
    logger.warning("midcap150_constituents.csv not found — using empty universe")
    return []


def run_gate_check(split: str, n_trials: int | None = None) -> int:
    """Run the full gate-check sequence for the PEAD strategy on a data split.

    Returns
    -------
    int — 0 if all gates pass, 1 if any fail, 2 if data error.
    """
    if split not in SPLIT_DATES:
        logger.error("Invalid split: %r. Choose 'train' or 'dev'.", split)
        return 2

    start, end = SPLIT_DATES[split]

    # ── Resolve MLflow trial count ─────────────────────────────────────────────
    if n_trials is None:
        try:
            client = mlflow.tracking.MlflowClient()
            exp = client.get_experiment_by_name("pead_midcap")
            if exp:
                runs = client.search_runs(
                    experiment_ids=[exp.experiment_id],
                    filter_string="",
                    max_results=1000,
                )
                n_trials = max(len(runs), 1)
            else:
                n_trials = 1
        except Exception:
            n_trials = 1

    logger.info("Gate check: strategy=pead_midcap split=%s trials=%d", split, n_trials)

    # ── Load data ──────────────────────────────────────────────────────────────
    try:
        earnings_df = load_earnings(start=start, end=end)
        ohlcv = pit_load(symbol=None, start=start, end=end)
        midcap150 = _load_midcap150()
    except Exception as exc:
        logger.error("Data load failed: %s", exc)
        return 2

    if earnings_df.empty:
        logger.error("No earnings data for %s → %s.  Run earnings_ingest first.", start, end)
        return 2

    if ohlcv.empty:
        logger.error("No OHLCV data for %s → %s.  Run NSE Bhavcopy ingest first.", start, end)
        return 2

    announcement_dates = sorted(
        earnings_df["business_date"].astype(str).unique().tolist()
    )

    # ── Purged k-fold on announcement dates ───────────────────────────────────
    dates_ts = pd.to_datetime(announcement_dates)
    try:
        splits = purged_kfold_split(dates_ts, holding_period_days=10, k=5)
    except ValueError as exc:
        logger.error("Purged k-fold error: %s", exc)
        return 2

    fold_metrics = []
    all_trades = []

    for fold_i, (train_pos, test_pos) in enumerate(splits):
        test_dates = [announcement_dates[i] for i in test_pos]
        fold_trades = simulate_trades(test_dates, earnings_df, ohlcv, midcap150)
        all_trades.extend(fold_trades)
        fold_m = compute_gate_metrics(fold_trades, n_trials=n_trials)
        fold_m["fold"] = fold_i
        fold_metrics.append(fold_m)
        logger.info(
            "  Fold %d: trades=%d drift_bps=%.1f sharpe=%.3f dsr=%.3f pass=%s",
            fold_i,
            fold_m["n_trades"],
            fold_m["mean_drift_bps"],
            fold_m["sharpe"],
            fold_m["dsr"],
            fold_m["gate_pass"],
        )

    # ── Aggregate metrics ──────────────────────────────────────────────────────
    agg = compute_gate_metrics(all_trades, n_trials=n_trials)
    median_trades_per_fold = float(np.median([m["n_trades"] for m in fold_metrics]))

    # ── Anti-strategy check ────────────────────────────────────────────────────
    anti = run_anti_strategy(announcement_dates, earnings_df, ohlcv, midcap150, n_trials=n_trials)

    # ── Cost-stress check ──────────────────────────────────────────────────────
    stress = run_cost_stress(announcement_dates, earnings_df, ohlcv, midcap150, n_trials=n_trials)
    stress_dsr_collapse = (
        (agg["dsr"] - stress["dsr"]) / agg["dsr"]
        if agg["dsr"] > 1e-6
        else 1.0
    )

    # ── Capacity check ─────────────────────────────────────────────────────────
    capacity = run_capacity_check(all_trades, n_trials=n_trials)

    # ── Gate evaluation ────────────────────────────────────────────────────────
    gates = {
        "mean_drift_bps >= 40":   agg["mean_drift_bps"] >= 40.0,
        "sharpe >= 0.5":          agg["sharpe"] >= 0.5,
        "dsr >= 0.5":             agg["dsr"] >= 0.5,
        "anti_strategy_dsr <= 0.5": anti["dsr"] <= 0.5,
        "stress_dsr_collapse <= 50%": stress_dsr_collapse <= 0.5,
        "capacity_dsr >= 0.3":    capacity["capacity_dsr"] >= 0.3,
        "median_trades >= 30":    median_trades_per_fold >= 30,
    }

    all_pass = all(gates.values())

    # ── Print gate report ──────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"PEAD Midcap Gate Report — split={split}")
    print("=" * 60)
    print(f"  Total trades:          {agg['n_trades']}")
    print(f"  Mean drift (bps):      {agg['mean_drift_bps']:.1f}")
    print(f"  Sharpe:                {agg['sharpe']:.3f}")
    print(f"  DSR (n_trials={n_trials}):    {agg['dsr']:.3f}")
    print(f"  Anti-strategy DSR:     {anti['dsr']:.3f}")
    print(f"  Cost-stress DSR:       {stress['dsr']:.3f} (collapse {stress_dsr_collapse:.1%})")
    print(f"  Capacity DSR (@₹50L):  {capacity['capacity_dsr']:.3f}")
    print(f"  Median trades/fold:    {median_trades_per_fold:.0f}")
    print()
    print("Gate results:")
    for criterion, passed in gates.items():
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {criterion}")
    print()
    if all_pass:
        print("RESULT: ALL GATES PASS")
        if split == "dev":
            print("  Next step: mark hypothesis final=true, then run hold-out (once).")
    else:
        print("RESULT: STRATEGY KILLED — one or more gates failed.")
        print("  Do NOT adjust parameters and re-run. The strategy is dead.")
    print("=" * 60 + "\n")

    # ── MLflow logging ─────────────────────────────────────────────────────────
    try:
        mlflow.set_experiment("pead_midcap")
        with mlflow.start_run(tags={"stage": split, "strategy": "pead_midcap"}):
            mlflow.log_metrics({
                "mean_drift_bps": agg["mean_drift_bps"],
                "sharpe": agg["sharpe"],
                "dsr": agg["dsr"],
                "anti_strategy_dsr": anti["dsr"],
                "stress_dsr": stress["dsr"],
                "stress_dsr_collapse": stress_dsr_collapse,
                "capacity_dsr": capacity["capacity_dsr"],
                "median_trades_per_fold": median_trades_per_fold,
                "n_trials": float(n_trials),
                "gate_all_pass": float(all_pass),
            })
    except Exception as exc:
        logger.warning("MLflow logging failed (non-fatal): %s", exc)

    return 0 if all_pass else 1


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="PEAD strategy gate-check runner")
    parser.add_argument(
        "--strategy",
        choices=["pead_midcap"],
        required=True,
        help="Strategy to evaluate",
    )
    parser.add_argument(
        "--split",
        choices=["train", "dev"],
        required=True,
        help="Data split to evaluate on (hold-out requires a separate ceremony)",
    )
    parser.add_argument(
        "--n-trials",
        type=int,
        default=None,
        help="MLflow trial count for DSR (auto-detected from MLflow if not set)",
    )
    args = parser.parse_args()

    exit_code = run_gate_check(args.split, n_trials=args.n_trials)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
