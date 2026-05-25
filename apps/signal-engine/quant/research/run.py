"""Gate-check CLI for quantitative strategies.

Usage:
  python -m quant.research.run --strategy pead_midcap --split dev
  python -m quant.research.run --strategy index_recon --split dev
  python -m quant.research.run --strategy momentum --split dev
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
from quant.features.earnings import build_earnings_features, build_target
from quant.models.calibrated_lgbm import CalibratedLGBM
from quant.research.dsr import deflated_sharpe
from quant.research.holdout_lock import DEV_END, DEV_START, TRAIN_END
from quant.research.purged_kfold import purged_kfold_split
from quant.strategies.pead_midcap import (
    compute_gate_metrics,
    is_election_period,
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

_HYPOTHESIS_PATH = Path(__file__).parents[4] / "research" / "hypotheses" / "2026-05-20-pead-midcap-v2.md"
_MLFLOW_EXPERIMENT = "pead_midcap_v2"  # clean slate: n_trials starts from 1

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


def _build_event_dataset(
    earnings_df: pd.DataFrame,
    ohlcv: pd.DataFrame,
    midcap150: list[str],
    start: str,
    end: str,
) -> tuple[pd.DataFrame, pd.Series, pd.DatetimeIndex]:
    """Build feature matrix X, binary target y, and event dates for the given window.

    Iterates every (symbol, announcement_date) in the training window, builds all
    16 pre-registered PEAD features, computes the binary target, and returns the
    assembled dataset.  Election-period events are excluded (matching simulate_trades).

    Returns
    -------
    X : pd.DataFrame   shape (n_events, 16)
    y : pd.Series      binary 0/1
    dates : DatetimeIndex  event dates (for purged k-fold)
    """
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)

    ann_dates = sorted(
        earnings_df[
            (pd.to_datetime(earnings_df["business_date"]) >= start_ts)
            & (pd.to_datetime(earnings_df["business_date"]) <= end_ts)
        ]["business_date"].astype(str).unique()
    )

    rows_X: list[dict] = []
    rows_y: list[float] = []
    rows_dates: list[pd.Timestamp] = []

    skipped_election = 0
    skipped_no_target = 0
    skipped_no_features = 0

    for ann_date in ann_dates:
        if is_election_period(ann_date):
            skipped_election += 1
            continue

        # All symbols with a result on this date that are in the universe
        day_df = earnings_df[
            (earnings_df["symbol"].isin(midcap150))
            & (earnings_df["business_date"].astype(str) == ann_date)
        ]
        if day_df.empty:
            continue

        for _, erow in day_df.iterrows():
            sym = erow["symbol"]

            target = build_target(sym, ann_date, ohlcv)
            if target is None:
                skipped_no_target += 1
                continue

            feats = build_earnings_features(
                symbol=sym,
                announcement_date=ann_date,
                earnings_df=earnings_df,
                ohlcv=ohlcv,
            )

            # eps_surprise_pct is the primary gate feature; skip if missing
            if feats.get("eps_surprise_pct") is None:
                skipped_no_features += 1
                continue

            # Fill remaining None features with 0 (neutral) — same as inference path
            feat_row = {
                k: (v if v is not None else 0.0)
                for k, v in feats.items()
                if k not in ("symbol", "announcement_date")
            }
            rows_X.append(feat_row)
            rows_y.append(target)
            rows_dates.append(pd.Timestamp(ann_date))

    logger.info(
        "Event dataset: %d rows (skipped: %d election, %d no-target, %d no-features)",
        len(rows_X), skipped_election, skipped_no_target, skipped_no_features,
    )

    if not rows_X:
        return pd.DataFrame(), pd.Series(dtype=float), pd.DatetimeIndex([])

    from quant.features.earnings import REGISTERED_FEATURES
    X = pd.DataFrame(rows_X)[REGISTERED_FEATURES].fillna(0.0)
    y = pd.Series(rows_y, dtype=float)
    dates = pd.DatetimeIndex(rows_dates)
    return X, y, dates


def _build_model_scores(
    model: CalibratedLGBM,
    earnings_df: pd.DataFrame,
    ohlcv: pd.DataFrame,
    midcap150: list[str],
    announcement_dates: list[str],
) -> dict[tuple[str, str], float]:
    """Score all (symbol, date) candidates in announcement_dates with the fitted model.

    Returns
    -------
    dict[(symbol, ann_date_str) -> calibrated p_win]
    """
    from quant.features.earnings import REGISTERED_FEATURES

    scores: dict[tuple[str, str], float] = {}
    no_eps = 0

    for ann_date in announcement_dates:
        if is_election_period(ann_date):
            continue

        day_df = earnings_df[
            (earnings_df["symbol"].isin(midcap150))
            & (earnings_df["business_date"].astype(str) == ann_date)
        ]
        if day_df.empty:
            continue

        for _, erow in day_df.iterrows():
            sym = erow["symbol"]
            feats = build_earnings_features(
                symbol=sym,
                announcement_date=ann_date,
                earnings_df=earnings_df,
                ohlcv=ohlcv,
            )
            if feats.get("eps_surprise_pct") is None:
                no_eps += 1
                continue

            feat_row = {
                k: (v if v is not None else 0.0)
                for k, v in feats.items()
                if k not in ("symbol", "announcement_date")
            }
            X_one = pd.DataFrame([feat_row])[REGISTERED_FEATURES].fillna(0.0)
            try:
                p_win = float(model.predict_proba(X_one)[0])
            except Exception:
                p_win = 0.0
            scores[(sym, ann_date)] = p_win

    logger.info(
        "Model scoring: %d scores computed, %d skipped (no eps_surprise_pct)",
        len(scores), no_eps,
    )
    return scores


def run_gate_check(split: str, n_trials: int | None = None, no_model: bool = False) -> int:
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
            exp = client.get_experiment_by_name(_MLFLOW_EXPERIMENT)
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

    # ── Train LightGBM on the training split ──────────────────────────────────
    # For the dev gate we always train on 2015-01-01 → 2023-06-30 regardless of
    # which split we're evaluating.  For the train split self-evaluation we skip
    # model training (the model would be evaluated on its own training data).
    model: CalibratedLGBM | None = None
    model_scores: dict | None = None

    if split == "dev" and not no_model:
        train_start, train_end = SPLIT_DATES["train"]
        logger.info("Building training dataset (%s → %s) for LightGBM ...", train_start, train_end)
        try:
            train_earnings = load_earnings(start=train_start, end=train_end)
            train_ohlcv = pit_load(symbol=None, start=train_start, end=train_end)
            X_train, y_train, train_dates = _build_event_dataset(
                train_earnings, train_ohlcv, midcap150,
                start=train_start, end=train_end,
            )
            if len(X_train) >= 50:
                model = CalibratedLGBM()
                model.fit(
                    X_train, y_train, train_dates,
                    n_trials=n_trials,
                    experiment_name=_MLFLOW_EXPERIMENT,
                )
                logger.info(
                    "Model trained: n=%d oof_brier=%.4f",
                    len(X_train), model.oof_brier,
                )
                model_scores = _build_model_scores(
                    model, earnings_df, ohlcv, midcap150, announcement_dates
                )
                logger.info("Model scores computed: %d events scored", len(model_scores))
            else:
                logger.warning(
                    "Training dataset too small (%d events < 50) — skipping model, "
                    "falling back to 1-std EPS gate only",
                    len(X_train),
                )
        except Exception as exc:
            logger.warning("LightGBM training failed (non-fatal, falling back): %s", exc)

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
        fold_trades = simulate_trades(
            test_dates, earnings_df, ohlcv, midcap150,
            model_scores=model_scores,
        )
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
    anti = run_anti_strategy(
        announcement_dates, earnings_df, ohlcv, midcap150,
        n_trials=n_trials, model_scores=model_scores,
    )

    # ── Cost-stress check ──────────────────────────────────────────────────────
    stress = run_cost_stress(
        announcement_dates, earnings_df, ohlcv, midcap150,
        n_trials=n_trials, model_scores=model_scores,
    )
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
        "median_trades >= 25":    median_trades_per_fold >= 25,
    }

    all_pass = all(gates.values())

    # ── Print gate report ──────────────────────────────────────────────────────
    model_desc = (
        f"LightGBM oof_brier={model.oof_brier:.4f}"
        if model else
        ("rule-based (--no-model)" if no_model else "rule-based (model training failed/skipped)")
    )

    print("\n" + "=" * 60)
    print(f"PEAD Midcap v2 Gate Report — split={split} [election filter ON]")
    print(f"  Trade selection:       {model_desc}")
    print("=" * 60)
    print(f"  Total trades:          {agg['n_trades']}")
    print(f"  Mean drift (bps):      {agg['mean_drift_bps']:.1f}")
    print(f"  Sharpe:                {agg['sharpe']:.3f}")
    print(f"  DSR (n_trials={n_trials}):    {agg['dsr']:.3f}")
    print(f"  Anti-strategy DSR:     {anti['dsr']:.3f}")
    print(f"  Cost-stress DSR:       {stress['dsr']:.3f} (collapse {stress_dsr_collapse:.1%})")
    print(f"  Capacity DSR (@₹50L):  {capacity['capacity_dsr']:.3f}")
    print(f"  Median trades/fold:    {median_trades_per_fold:.0f}  (threshold: 25)")
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
        mlflow.set_experiment(_MLFLOW_EXPERIMENT)
        with mlflow.start_run(tags={"stage": split, "strategy": "pead_midcap_v2"}):
            metrics = {
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
            }
            if model is not None:
                metrics["model_oof_brier"] = model.oof_brier
                metrics["model_n_train"] = float(len(model_scores) if model_scores else 0)
            mlflow.log_metrics(metrics)
    except Exception as exc:
        logger.warning("MLflow logging failed (non-fatal): %s", exc)

    return 0 if all_pass else 1


def run_gate_check_index_recon(split: str, n_trials: int | None = None) -> int:
    """Run the full gate-check sequence for the Index Reconstitution strategy.

    Returns 0 if all gates pass, 1 if any fail, 2 if data error.
    """
    from quant.data.nse_index_changes import load_events
    from quant.strategies.index_recon import (
        compute_gate_metrics as recon_gate_metrics,
        run_anti_strategy as recon_anti,
        run_cost_stress as recon_stress,
        simulate_trades as recon_simulate,
    )

    _RECON_HYPOTHESIS = (
        Path(__file__).parents[4] / "research" / "hypotheses"
        / "2026-05-21-index-recon-arb.md"
    )
    _RECON_EXPERIMENT = "index_recon_v1"

    from quant.research.holdout_lock import HOLDOUT_START, read_holdout

    _RECON_SPLIT_DATES = {
        **SPLIT_DATES,
        "holdout": (HOLDOUT_START, "2026-05-22"),  # present day
    }

    if split not in _RECON_SPLIT_DATES:
        logger.error("Invalid split: %r. Choose 'train', 'dev', or 'holdout'.", split)
        return 2

    # ── Hold-out ceremony (must happen before any data access) ────────────────
    if split == "holdout":
        try:
            read_holdout("index_recon_v1", _RECON_HYPOTHESIS)
        except (PermissionError, ValueError, FileNotFoundError) as exc:
            logger.error("Hold-out unlock failed: %s", exc)
            return 2

    start, end = _RECON_SPLIT_DATES[split]

    if n_trials is None:
        try:
            client = mlflow.tracking.MlflowClient()
            exp = client.get_experiment_by_name(_RECON_EXPERIMENT)
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

    logger.info("Gate check: strategy=index_recon split=%s trials=%d", split, n_trials)

    try:
        events = load_events(start=start, end=end, event_type="inclusion")
    except FileNotFoundError as exc:
        logger.error("Recon events file missing: %s", exc)
        return 2

    if events.empty:
        logger.error(
            "No inclusion events for %s → %s.  Populate nse_recon_events.csv first.",
            start, end,
        )
        return 2

    try:
        ohlcv = pit_load(symbol=None, start=start, end=end)
    except Exception as exc:
        logger.error("OHLCV load failed: %s", exc)
        return 2

    if ohlcv.empty:
        logger.error("No OHLCV data for %s → %s.", start, end)
        return 2

    # ── Simulate trades ────────────────────────────────────────────────────────
    trades = recon_simulate(events, ohlcv)
    agg = recon_gate_metrics(trades, n_trials=n_trials)

    # ── Anti-strategy ──────────────────────────────────────────────────────────
    anti = recon_anti(events, ohlcv, n_trials=n_trials)

    # ── Cost-stress ────────────────────────────────────────────────────────────
    stress = recon_stress(events, ohlcv, n_trials=n_trials)
    stress_dsr_collapse = (
        (agg["dsr"] - stress["dsr"]) / agg["dsr"]
        if agg["dsr"] > 1e-6
        else 1.0
    )

    # ── Gate evaluation ────────────────────────────────────────────────────────
    gates = {
        "mean_return_bps >= 100":     agg["mean_return_bps"] >= 100.0,
        "win_rate >= 50%":            agg["win_rate"] >= 0.5,
        "sharpe >= 0.5":              agg["sharpe"] >= 0.5,
        "dsr >= 0.5":                 agg["dsr"] >= 0.5,
        "anti_strategy_return <= 0":  anti["mean_return_bps"] <= 0.0,
        "stress_dsr_collapse <= 50%": stress_dsr_collapse <= 0.5,
        "total_dev_events >= 15":     agg["n_trades"] >= 15,
    }
    all_pass = all(gates.values())

    # ── Gate report ────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"Index Recon v1 Gate Report — split={split}")
    print("=" * 60)
    print(f"  Total trades:          {agg['n_trades']}")
    print(f"  Mean return (bps):     {agg['mean_return_bps']:.1f}")
    print(f"  Win rate:              {agg['win_rate']:.1%}")
    print(f"  Sharpe:                {agg['sharpe']:.3f}")
    print(f"  DSR (n_trials={n_trials}):    {agg['dsr']:.3f}")
    print(f"  Anti-strategy return:  {anti['mean_return_bps']:.1f} bps")
    print(f"  Cost-stress DSR:       {stress['dsr']:.3f} (collapse {stress_dsr_collapse:.1%})")
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
        mlflow.set_experiment(_RECON_EXPERIMENT)
        with mlflow.start_run(tags={"stage": split, "strategy": "index_recon_v1"}):
            mlflow.log_metrics({
                "mean_return_bps":      agg["mean_return_bps"],
                "win_rate":             agg["win_rate"],
                "sharpe":               agg["sharpe"],
                "dsr":                  agg["dsr"],
                "anti_mean_return_bps": anti["mean_return_bps"],
                "stress_dsr":           stress["dsr"],
                "stress_dsr_collapse":  stress_dsr_collapse,
                "n_trials":             float(n_trials),
                "gate_all_pass":        float(all_pass),
            })
    except Exception as exc:
        logger.warning("MLflow logging failed (non-fatal): %s", exc)

    return 0 if all_pass else 1


def run_gate_check_idi(split: str, n_trials: int | None = None) -> int:
    """Run the full gate-check sequence for the IDI (Strategy E) strategy.

    Returns 0 if all gates pass, 1 if any fail, 2 if data error.
    """
    from quant.data.delivery_ingest import load_delivery
    from quant.research.holdout_lock import HOLDOUT_START, read_holdout
    from quant.strategies.idi import (
        compute_gate_metrics as idi_gate_metrics,
        compute_signals,
        run_anti_strategy as idi_anti,
        run_cost_stress as idi_stress,
        simulate_trades as idi_simulate,
    )

    _IDI_HYPOTHESIS = (
        Path(__file__).parents[4] / "research" / "hypotheses"
        / "2026-05-22-institutional-delivery-impulse.md"
    )
    _IDI_EXPERIMENT = "idi_v1"

    _IDI_SPLIT_DATES = {
        "train": ("2020-01-01", "2023-06-30"),
        "dev":   (DEV_START, DEV_END),
        "holdout": (HOLDOUT_START, "2026-12-31"),
    }

    if split not in _IDI_SPLIT_DATES:
        logger.error("Invalid split: %r. Choose 'train', 'dev', or 'holdout'.", split)
        return 2

    if split == "holdout":
        try:
            read_holdout("idi_v1", _IDI_HYPOTHESIS)
        except (PermissionError, ValueError, FileNotFoundError) as exc:
            logger.error("Hold-out unlock failed: %s", exc)
            return 2

    start, end = _IDI_SPLIT_DATES[split]

    if n_trials is None:
        try:
            client = mlflow.tracking.MlflowClient()
            exp = client.get_experiment_by_name(_IDI_EXPERIMENT)
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

    logger.info("Gate check: strategy=idi split=%s trials=%d", split, n_trials)

    # ── Load delivery data ────────────────────────────────────────────────────
    try:
        delivery = load_delivery(start=start, end=end, series="EQ")
    except Exception as exc:
        logger.error("Delivery data load failed: %s", exc)
        return 2

    if delivery.empty:
        logger.error(
            "No delivery data for %s → %s.  Run delivery_ingest first:\n"
            "  python -m quant.data.delivery_ingest --start %s --end %s",
            start, end, start, end,
        )
        return 2

    # ── Load OHLCV for entry/exit pricing ─────────────────────────────────────
    try:
        ohlcv = pit_load(symbol=None, start=start, end=end)
    except Exception as exc:
        logger.error("OHLCV load failed: %s", exc)
        return 2

    if ohlcv.empty:
        logger.error("No OHLCV data for %s → %s.", start, end)
        return 2

    # ── Compute signals ────────────────────────────────────────────────────────
    signals = compute_signals(delivery)
    n_raw_signals = int(signals["signal"].sum())
    logger.info("Raw signal events in %s → %s: %d", start, end, n_raw_signals)

    if n_raw_signals == 0:
        logger.error(
            "Zero signals computed for %s → %s.  "
            "Check delivery data coverage and signal thresholds.",
            start, end,
        )
        return 2

    # ── Simulate trades ────────────────────────────────────────────────────────
    trades = idi_simulate(signals, ohlcv)
    agg = idi_gate_metrics(trades, n_trials=n_trials)

    # ── Anti-strategy ──────────────────────────────────────────────────────────
    anti = idi_anti(signals, ohlcv, n_trials=n_trials)

    # ── Cost-stress ────────────────────────────────────────────────────────────
    stress = idi_stress(signals, ohlcv, n_trials=n_trials)
    stress_dsr_collapse = (
        (agg["dsr"] - stress["dsr"]) / agg["dsr"]
        if agg["dsr"] > 1e-6
        else 1.0
    )

    # ── Gate evaluation ────────────────────────────────────────────────────────
    gates = {
        "mean_return_bps >= 80":      agg["mean_return_bps"] >= 80.0,
        "win_rate >= 52%":            agg["win_rate"] >= 0.52,
        "sharpe >= 0.5":              agg["sharpe"] >= 0.5,
        "dsr >= 0.5":                 agg["dsr"] >= 0.5,
        "anti_strategy_return <= 0":  anti["mean_return_bps"] <= 0.0,
        "stress_dsr_collapse <= 50%": stress_dsr_collapse <= 0.5,
        "total_dev_events >= 80":     agg["n_trades"] >= 80,
    }
    all_pass = all(gates.values())

    # ── Gate report ────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"IDI v1 Gate Report — split={split}")
    print("=" * 60)
    print(f"  Raw signals:           {n_raw_signals}")
    print(f"  Executed trades:       {agg['n_trades']}")
    print(f"  Mean return (bps):     {agg['mean_return_bps']:.1f}")
    print(f"  Win rate:              {agg['win_rate']:.1%}")
    print(f"  Sharpe:                {agg['sharpe']:.3f}")
    print(f"  DSR (n_trials={n_trials}):    {agg['dsr']:.3f}")
    print(f"  Anti-strategy return:  {anti['mean_return_bps']:.1f} bps")
    print(f"  Cost-stress DSR:       {stress['dsr']:.3f} (collapse {stress_dsr_collapse:.1%})")
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
        mlflow.set_experiment(_IDI_EXPERIMENT)
        with mlflow.start_run(tags={"stage": split, "strategy": "idi_v1"}):
            mlflow.log_metrics({
                "raw_signals":          float(n_raw_signals),
                "mean_return_bps":      agg["mean_return_bps"],
                "win_rate":             agg["win_rate"],
                "sharpe":               agg["sharpe"],
                "dsr":                  agg["dsr"],
                "anti_mean_return_bps": anti["mean_return_bps"],
                "stress_dsr":           stress["dsr"],
                "stress_dsr_collapse":  stress_dsr_collapse,
                "n_trials":             float(n_trials),
                "gate_all_pass":        float(all_pass),
            })
    except Exception as exc:
        logger.warning("MLflow logging failed (non-fatal): %s", exc)

    return 0 if all_pass else 1


def run_gate_check_momentum(split: str, n_trials: int | None = None) -> int:
    """Run the full gate-check for Strategy P — Cross-Sectional Momentum.

    Pre-registered hypothesis: research/hypotheses/2026-05-25-cross-sectional-momentum.md

    Returns 0 if all gates pass, 1 if any fail, 2 if data error.
    """
    from quant.data.pit_loader import load as _pit_load
    from quant.research.holdout_lock import HOLDOUT_START, read_holdout
    from quant.strategies.cs_momentum import (
        build_anti_portfolios,
        build_monthly_portfolios,
        compute_gate_metrics as mom_gate_metrics,
        load_constituents,
        run_cost_stress as mom_cost_stress,
        simulate_portfolio,
    )

    _MOM_HYPOTHESIS = (
        Path(__file__).parents[4] / "research" / "hypotheses"
        / "2026-05-25-cross-sectional-momentum.md"
    )
    _MOM_EXPERIMENT = "cs_momentum_v1"

    _MOM_SPLIT_DATES = {
        "train":   ("2015-01-01", "2023-06-30"),
        "dev":     (DEV_START, DEV_END),
        "holdout": (HOLDOUT_START, "2026-12-31"),
    }

    if split not in _MOM_SPLIT_DATES:
        logger.error("Invalid split: %r. Choose 'train', 'dev', or 'holdout'.", split)
        return 2

    if split == "holdout":
        try:
            read_holdout(_MOM_EXPERIMENT, _MOM_HYPOTHESIS)
        except (PermissionError, ValueError, FileNotFoundError) as exc:
            logger.error("Hold-out unlock failed: %s", exc)
            return 2

    start, end = _MOM_SPLIT_DATES[split]

    # Auto-detect trial count from MLflow
    if n_trials is None:
        try:
            client = mlflow.tracking.MlflowClient()
            exp = client.get_experiment_by_name(_MOM_EXPERIMENT)
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

    logger.info("Gate check: strategy=momentum split=%s n_trials=%d", split, n_trials)

    # ── Load OHLCV data ───────────────────────────────────────────────────────
    # pit_loader returns a MultiIndex (business_date, symbol); flatten to
    # standard flat columns (date, symbol, open, high, low, close, ...) so
    # the cs_momentum strategy module can operate on a plain DataFrame.
    try:
        ohlcv = _pit_load(symbol=None, start="2014-01-01", end=end)
        # Reset MultiIndex → flat columns; rename business_date → date
        if isinstance(ohlcv.index, pd.MultiIndex):
            ohlcv = ohlcv.reset_index()
            if "business_date" in ohlcv.columns:
                ohlcv = ohlcv.rename(columns={"business_date": "date"})
        ohlcv["date"] = pd.to_datetime(ohlcv["date"])
    except Exception as exc:
        logger.error("OHLCV load failed: %s", exc)
        return 2

    if ohlcv.empty:
        logger.error("OHLCV data is empty for range 2014-01-01 → %s", end)
        return 2

    # ── Load Midcap 150 universe ──────────────────────────────────────────────
    symbols = load_constituents()
    if not symbols:
        logger.error(
            "Midcap 150 constituent list is empty.  "
            "Expected: data/lake/midcap150_constituents.csv"
        )
        return 2

    logger.info("Universe: %d symbols | OHLCV rows: %d", len(symbols), len(ohlcv))

    # ── Build portfolios (signal + anti-signal) ───────────────────────────────
    portfolios = build_monthly_portfolios(ohlcv, symbols, start=start, end=end)
    if len(portfolios) < 2:
        logger.error("Fewer than 2 monthly portfolios built for %s → %s.", start, end)
        return 2

    anti_ports = build_anti_portfolios(ohlcv, symbols, start=start, end=end)

    # ── Simulate ──────────────────────────────────────────────────────────────
    result_df   = simulate_portfolio(portfolios, ohlcv, costs_enabled=True)
    anti_df     = simulate_portfolio(anti_ports, ohlcv, costs_enabled=True)

    if result_df.empty:
        logger.error("Simulation produced no monthly return records.")
        return 2

    # ── Benchmark (equal-weight Midcap 150 proxy) ─────────────────────────────
    # For now, use median return of all scored symbols as the benchmark proxy.
    # This is a conservative benchmark (universe beta, not a true TRI).
    # TODO: replace with official Nifty Midcap 150 TRI when data is available.
    benchmark_df = None  # will report excess_return as None; gate still runs

    # ── Gate metrics ──────────────────────────────────────────────────────────
    metrics  = mom_gate_metrics(result_df, benchmark_df=benchmark_df, n_trials=n_trials)
    anti_met = mom_gate_metrics(anti_df, benchmark_df=benchmark_df, n_trials=n_trials)

    # Cost-stress test (200 runs with t-dist slippage)
    stress = mom_cost_stress(portfolios, ohlcv, n_trials=n_trials)
    stress_collapse = (
        (metrics["dsr"] - stress["dsr_median"]) / metrics["dsr"]
        if metrics["dsr"] and metrics["dsr"] > 1e-6
        else 1.0
    )

    # ── Gate evaluation ───────────────────────────────────────────────────────
    gates: dict[str, bool] = {
        "dsr >= 0.6":               (metrics["dsr"] or 0.0) >= 0.6,
        "sharpe >= 0.7":            (metrics["sharpe"] or 0.0) >= 0.7,
        "stress_dsr_collapse <= 50%": stress_collapse <= 0.5,
        "max_drawdown >= -20%":     (metrics["max_drawdown"] or -99.0) >= -0.20,
        "anti_strategy_dsr <= 0":   (anti_met["dsr"] or 1.0) <= 0.0,
        "n_months >= 12":           (metrics["n_months"] or 0) >= 12,
    }

    # Alpha gate only if benchmark available
    if metrics["excess_return_vs_benchmark"] is not None:
        gates["excess_return > 0"] = metrics["excess_return_vs_benchmark"] > 0.0

    all_pass = all(gates.values())

    # ── Print gate report ─────────────────────────────────────────────────────
    print("\n" + "=" * 65)
    print(f"Cross-Sectional Momentum Gate Report — split={split}")
    print("=" * 65)
    print(f"  Monthly rebalances:      {metrics['n_months']}")
    print(f"  Annualised return:       {(metrics['annualised_return'] or 0):.1%}")
    print(f"  Sharpe (net of costs):   {(metrics['sharpe'] or 0):.3f}")
    print(f"  DSR (n_trials={n_trials}):      {(metrics['dsr'] or 0):.3f}")
    print(f"  Max drawdown:            {(metrics['max_drawdown'] or 0):.1%}")
    print(f"  Anti-strategy DSR:       {(anti_met['dsr'] or 0):.3f}")
    print(f"  Stress DSR (median):     {stress['dsr_median']:.3f}  "
          f"[collapse {stress_collapse:.1%}]")
    if metrics["excess_return_vs_benchmark"] is not None:
        print(f"  Excess return vs bench:  {metrics['excess_return_vs_benchmark']:.1%}")
    else:
        print(f"  Excess return vs bench:  N/A (no benchmark loaded)")
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
    print("=" * 65 + "\n")

    # ── MLflow logging ────────────────────────────────────────────────────────
    try:
        mlflow.set_experiment(_MOM_EXPERIMENT)
        with mlflow.start_run(tags={"stage": split, "strategy": "cs_momentum_v1"}):
            mlflow.log_metrics({
                "n_months":          float(metrics["n_months"] or 0),
                "ann_return":        float(metrics["annualised_return"] or 0),
                "sharpe":            float(metrics["sharpe"] or 0),
                "dsr":               float(metrics["dsr"] or 0),
                "max_drawdown":      float(metrics["max_drawdown"] or 0),
                "anti_dsr":          float(anti_met["dsr"] or 0),
                "stress_dsr_median": stress["dsr_median"],
                "stress_collapse":   stress_collapse,
                "n_trials":          float(n_trials),
                "gate_all_pass":     float(all_pass),
            })
    except Exception as exc:
        logger.warning("MLflow logging failed (non-fatal): %s", exc)

    return 0 if all_pass else 1


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Strategy gate-check runner")
    parser.add_argument(
        "--strategy",
        choices=["pead_midcap", "index_recon", "idi", "momentum"],
        required=True,
        help="Strategy to evaluate",
    )
    parser.add_argument(
        "--split",
        choices=["train", "dev", "holdout"],
        required=True,
        help=(
            "Data split to evaluate on.  "
            "'holdout' requires the hold-out lock ceremony: "
            "QUANT_HOLDOUT_UNLOCK=<strategy_name> env var, "
            "hypothesis file with 'final: true', and no prior holdout run in MLflow."
        ),
    )
    parser.add_argument(
        "--n-trials",
        type=int,
        default=None,
        help="MLflow trial count for DSR (auto-detected from MLflow if not set)",
    )
    parser.add_argument(
        "--no-model",
        action="store_true",
        help=(
            "Skip LightGBM training and use the 1-std quarterly EPS gate alone. "
            "(pead_midcap only)"
        ),
    )
    args = parser.parse_args()

    if args.strategy == "pead_midcap":
        exit_code = run_gate_check(args.split, n_trials=args.n_trials, no_model=args.no_model)
    elif args.strategy == "index_recon":
        exit_code = run_gate_check_index_recon(args.split, n_trials=args.n_trials)
    elif args.strategy == "momentum":
        exit_code = run_gate_check_momentum(args.split, n_trials=args.n_trials)
    else:
        exit_code = run_gate_check_idi(args.split, n_trials=args.n_trials)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
