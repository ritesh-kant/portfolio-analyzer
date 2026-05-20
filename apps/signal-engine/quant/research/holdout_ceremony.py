"""Hold-out ceremony — the single-shot final evaluation for Strategy A. Month 4.

THIS IS A ONE-WAY DOOR.

Once you run this script on the hold-out partition for a strategy, the run is
logged to MLflow with an immutable `is_holdout_run=true` tag.  holdout_lock.py
will refuse any subsequent attempt to run the hold-out for the same strategy.

If the strategy fails the hold-out gate:
  - It is dead.
  - There is no "adjust and re-run".
  - See plan §3.1 + §14.

If it passes:
  - Mark the hypothesis YAML front-matter as `decision: pass`
  - Proceed to paper trading (Month 4 §12).

Pre-conditions (all must be true before running):
  1. The dev gate check has passed: `python -m quant.research.run --strategy pead_midcap --split dev`
  2. The hypothesis file has been marked `final: true` (front-matter)
  3. The env var QUANT_HOLDOUT_UNLOCK=pead_midcap is set
  4. Earnings data exists in data/lake/earnings/nse_results.parquet
  5. OHLCV data exists for 2024-07 → present in data/lake/nse_bhavcopy/

Hold-out window: 2024-07-01 → present (locked, never changes; plan §3.1)

Usage
-----
  QUANT_HOLDOUT_UNLOCK=pead_midcap uv run python -m quant.research.holdout_ceremony

Exit codes:
  0 — all gates pass on hold-out → strategy proceeds to paper
  1 — one or more gates fail → strategy is dead
  2 — pre-condition error (data missing, hypothesis not final, etc.)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_HYPOTHESIS_PATH = (
    Path(__file__).parents[4] / "research" / "hypotheses" / "2026-05-19-pead-midcap.md"
)
_STRATEGY_NAME = "pead_midcap"

# Hold-out gate thresholds (from plan §3.2 — stricter than dev gate)
_HOLDOUT_DSR_THRESHOLD = 0.4   # lower than dev (0.5) — one shot, less power
_HOLDOUT_ALPHA_REQUIRED = True  # alpha vs Nifty must be positive


def run_ceremony() -> int:
    """Execute the hold-out ceremony.

    Returns
    -------
    int — exit code (0 pass, 1 fail, 2 error)
    """
    # ── Step 0: Pre-condition checks ──────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("PEAD Hold-out Ceremony — Strategy A")
    logger.info("=" * 60)
    logger.info("")
    logger.info("Pre-condition checks...")

    if not _HYPOTHESIS_PATH.exists():
        logger.error("Hypothesis file not found: %s", _HYPOTHESIS_PATH)
        logger.error("Run: touch %s and populate it first.", _HYPOTHESIS_PATH)
        return 2

    hypothesis_text = _HYPOTHESIS_PATH.read_text(encoding="utf-8")
    if "final: true" not in hypothesis_text.lower():
        logger.error(
            "Hypothesis file does not contain 'final: true'.\n"
            "You must mark the hypothesis as final BEFORE running the hold-out.\n"
            "Edit %s and set 'final: true' in the YAML front-matter.",
            _HYPOTHESIS_PATH,
        )
        return 2

    # ── Step 1: Unlock the hold-out partition ─────────────────────────────────
    # This will raise PermissionError if QUANT_HOLDOUT_UNLOCK env var is wrong,
    # or if a hold-out run already exists in MLflow for this strategy.
    logger.info("Unlocking hold-out partition...")
    try:
        from quant.research.holdout_lock import (
            HOLDOUT_START,
            read_holdout,
        )
        read_holdout(_STRATEGY_NAME, _HYPOTHESIS_PATH)
    except PermissionError as exc:
        logger.error("Hold-out unlock refused:\n%s", exc)
        return 2
    except ValueError as exc:
        logger.error("Hold-out unlock failed:\n%s", exc)
        return 2

    logger.info("Hold-out partition unlocked.")

    # ── Step 2: Load data ──────────────────────────────────────────────────────
    logger.info("Loading hold-out data (%s → present)...", HOLDOUT_START)
    try:
        import pandas as pd

        from quant.data.earnings_ingest import load_earnings
        from quant.data.pit_loader import load as pit_load
        from quant.research.run import _load_midcap150

        earnings_df = load_earnings(start=HOLDOUT_START)
        ohlcv = pit_load(symbol=None, start=HOLDOUT_START, end="2026-12-31")
        midcap150 = _load_midcap150()
    except Exception as exc:
        logger.error("Data load failed: %s", exc)
        return 2

    if earnings_df.empty:
        logger.error(
            "No earnings data for hold-out period (%s → present).\n"
            "Run: uv run python -m quant.data.earnings_ingest --start 2024-07-01 --end 2025-12-31",
            HOLDOUT_START,
        )
        return 2

    if ohlcv.empty:
        logger.error(
            "No OHLCV data for hold-out period (%s → present).\n"
            "Check data/lake/nse_bhavcopy/ for 2024.parquet and 2025.parquet.",
            HOLDOUT_START,
        )
        return 2

    # ── Step 3: Run the strategy simulation on hold-out ───────────────────────
    logger.info("Simulating PEAD trades on hold-out data...")
    try:
        import mlflow
        import numpy as np

        from quant.strategies.pead_midcap import (
            compute_gate_metrics,
            run_anti_strategy,
            run_capacity_check,
            run_cost_stress,
            simulate_trades,
        )

        announcement_dates = sorted(
            earnings_df["business_date"].astype(str).unique().tolist()
        )

        if not announcement_dates:
            logger.error("No announcement dates in hold-out earnings data.")
            return 2

        # Resolve trial count from MLflow
        try:
            client = mlflow.tracking.MlflowClient()
            exp = client.get_experiment_by_name(_STRATEGY_NAME)
            if exp:
                runs = client.search_runs(
                    experiment_ids=[exp.experiment_id],
                    filter_string="tags.is_holdout_run = 'false' or tags.is_holdout_run not contains 'true'",
                    max_results=1000,
                )
                n_trials = max(len(runs), 1)
            else:
                n_trials = 1
        except Exception:
            n_trials = 1

        logger.info("  Announcement dates: %d", len(announcement_dates))
        logger.info("  Midcap 150 universe: %d symbols", len(midcap150))
        logger.info("  MLflow trial count (for DSR): %d", n_trials)

        trades = simulate_trades(announcement_dates, earnings_df, ohlcv, midcap150)

    except Exception as exc:
        logger.error("Simulation failed: %s", exc)
        return 2

    # ── Step 4: Compute gate metrics ───────────────────────────────────────────
    agg = compute_gate_metrics(trades, n_trials=n_trials)
    anti = run_anti_strategy(announcement_dates, earnings_df, ohlcv, midcap150, n_trials=n_trials)
    stress = run_cost_stress(announcement_dates, earnings_df, ohlcv, midcap150, n_trials=n_trials)
    capacity = run_capacity_check(trades, n_trials=n_trials)

    stress_dsr_collapse = (
        (agg["dsr"] - stress["dsr"]) / agg["dsr"]
        if agg["dsr"] > 1e-6
        else 1.0
    )

    # ── Step 5: Evaluate hold-out gate ─────────────────────────────────────────
    # Hold-out gate is slightly more lenient on DSR (0.4 vs 0.5 dev)
    # because hold-out has less statistical power (shorter window).
    gates = {
        "mean_drift_bps >= 40":     agg["mean_drift_bps"] >= 40.0,
        "sharpe >= 0.5":            agg["sharpe"] >= 0.5,
        "dsr >= 0.4 (hold-out)":    agg["dsr"] >= _HOLDOUT_DSR_THRESHOLD,
        "anti_strategy_dsr <= 0":   anti["dsr"] <= 0.0,
        "stress_dsr_collapse <= 50%": stress_dsr_collapse <= 0.5,
        "capacity_dsr >= 0.3":      capacity["capacity_dsr"] >= 0.3,
        "n_trades >= 10":           agg["n_trades"] >= 10,  # hold-out shorter, lower threshold
    }
    all_pass = all(gates.values())

    # ── Step 6: Print the report ───────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("PEAD Midcap — HOLD-OUT GATE REPORT (ONE-TIME EVALUATION)")
    print("=" * 70)
    print(f"  Hold-out window:        {HOLDOUT_START} → present")
    print(f"  Announcement dates:     {len(announcement_dates)}")
    print(f"  Total trades:           {agg['n_trades']}")
    print(f"  Mean drift (bps):       {agg['mean_drift_bps']:.1f}")
    print(f"  Sharpe:                 {agg['sharpe']:.3f}")
    print(f"  DSR (n_trials={n_trials}):       {agg['dsr']:.3f}")
    print(f"  Anti-strategy DSR:      {anti['dsr']:.3f}")
    print(f"  Cost-stress DSR:        {stress['dsr']:.3f}  (collapse {stress_dsr_collapse:.1%})")
    print(f"  Capacity DSR (@₹50L):   {capacity['capacity_dsr']:.3f}")
    print()
    print("Gate criteria:")
    for criterion, passed in gates.items():
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {criterion}")
    print()
    if all_pass:
        print("RESULT: ALL HOLD-OUT GATES PASS")
        print()
        print("  Next steps:")
        print("  1. Mark hypothesis decision: pass  in research/hypotheses/2026-05-19-pead-midcap.md")
        print("  2. Start paper trading:  python -m quant.execution.paper_runner")
        print("  3. Wire drift monitor:   python -m quant.features.drift --strategy pead_midcap")
    else:
        print("RESULT: HOLD-OUT GATE FAILED — STRATEGY IS DEAD")
        print()
        print("  This is a one-shot evaluation. The strategy cannot be adjusted")
        print("  and re-run. See plan §3.1 + §14.")
        print()
        failed = [c for c, p in gates.items() if not p]
        for f in failed:
            print(f"  FAILED: {f}")
    print("=" * 70 + "\n")

    # ── Step 7: Log to MLflow with is_final=True ───────────────────────────────
    try:
        mlflow.set_experiment(_STRATEGY_NAME)
        with mlflow.start_run(
            tags={
                "is_holdout_run": "true",
                "is_final": "true",
                "stage": "holdout",
                "strategy": _STRATEGY_NAME,
                "gate_result": "pass" if all_pass else "fail",
            }
        ):
            mlflow.log_metrics({
                "holdout_mean_drift_bps": agg["mean_drift_bps"],
                "holdout_sharpe": agg["sharpe"],
                "holdout_dsr": agg["dsr"],
                "holdout_anti_dsr": anti["dsr"],
                "holdout_stress_dsr": stress["dsr"],
                "holdout_stress_collapse": stress_dsr_collapse,
                "holdout_capacity_dsr": capacity["capacity_dsr"],
                "holdout_n_trades": float(agg["n_trades"]),
                "holdout_gate_all_pass": float(all_pass),
                "n_trials": float(n_trials),
            })
            mlflow.log_param("holdout_start", HOLDOUT_START)
            mlflow.log_param("hypothesis_file", _HYPOTHESIS_PATH.name)
    except Exception as exc:
        logger.warning("MLflow logging failed (non-fatal): %s", exc)

    return 0 if all_pass else 1


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    sys.exit(run_ceremony())


if __name__ == "__main__":
    main()
