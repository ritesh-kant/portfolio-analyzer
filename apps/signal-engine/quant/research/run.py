"""Gate-check CLI for quantitative strategies.

Usage:
  python -m quant.research.run --strategy pead_midcap --split dev
  python -m quant.research.run --strategy index_recon --split dev
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


def run_gate_check_bdm(split: str, n_trials: int | None = None) -> int:
    """Run the full gate-check sequence for the BDM (Strategy F) strategy.

    Returns 0 if all gates pass, 1 if any fail, 2 if data error.
    """
    from quant.data.bulk_deals import load_bulk_deals
    from quant.research.holdout_lock import HOLDOUT_START, read_holdout
    from quant.strategies.bdm import (
        build_events,
        compute_gate_metrics as bdm_gate_metrics,
        run_anti_strategy as bdm_anti,
        run_cost_stress as bdm_stress,
        simulate_trades as bdm_simulate,
    )

    _BDM_HYPOTHESIS = (
        Path(__file__).parents[4] / "research" / "hypotheses"
        / "2026-05-22-bulk-deal-momentum.md"
    )
    _BDM_EXPERIMENT = "bdm_v1"

    _BDM_SPLIT_DATES = {
        "train": ("2015-01-01", "2023-06-30"),
        "dev":   (DEV_START, DEV_END),
        "holdout": (HOLDOUT_START, "2026-12-31"),
    }

    if split not in _BDM_SPLIT_DATES:
        logger.error("Invalid split: %r. Choose 'train', 'dev', or 'holdout'.", split)
        return 2

    if split == "holdout":
        try:
            read_holdout("bdm_v1", _BDM_HYPOTHESIS)
        except (PermissionError, ValueError, FileNotFoundError) as exc:
            logger.error("Hold-out unlock failed: %s", exc)
            return 2

    start, end = _BDM_SPLIT_DATES[split]

    if n_trials is None:
        try:
            client = mlflow.tracking.MlflowClient()
            exp = client.get_experiment_by_name(_BDM_EXPERIMENT)
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

    logger.info("Gate check: strategy=bdm split=%s trials=%d", split, n_trials)

    # ── Load bulk deal data ───────────────────────────────────────────────────
    try:
        raw_deals = load_bulk_deals(start=start, end=end, side="BUY",
                                    min_value_cr=1.0)
    except Exception as exc:
        logger.error("Bulk deal data load failed: %s", exc)
        return 2

    if raw_deals.empty:
        logger.error(
            "No bulk deal data for %s → %s.  Run bulk_deals ingest first:\n"
            "  python -m quant.data.bulk_deals --start %s --end %s",
            start, end, start, end,
        )
        return 2

    # ── Load Midcap 150 universe ──────────────────────────────────────────────
    midcap150 = _load_midcap150()
    if not midcap150:
        logger.warning(
            "midcap150_constituents.csv not found — running on full bulk deal "
            "universe (no Midcap 150 filter).  Results may overstate edge."
        )

    # ── Build signal events ───────────────────────────────────────────────────
    events = build_events(raw_deals, midcap150=midcap150 if midcap150 else None)
    n_raw_events = len(events)
    logger.info("Raw BDM signal events in %s → %s: %d", start, end, n_raw_events)

    if n_raw_events == 0:
        logger.error(
            "Zero events built for %s → %s.  "
            "Check bulk deal data coverage and Midcap 150 constituent list.",
            start, end,
        )
        return 2

    # ── Load OHLCV ────────────────────────────────────────────────────────────
    try:
        ohlcv = pit_load(symbol=None, start=start, end=end)
    except Exception as exc:
        logger.error("OHLCV load failed: %s", exc)
        return 2

    if ohlcv.empty:
        logger.error("No OHLCV data for %s → %s.", start, end)
        return 2

    # ── Simulate trades ───────────────────────────────────────────────────────
    trades = bdm_simulate(events, ohlcv)
    agg = bdm_gate_metrics(trades, n_trials=n_trials)

    # ── Anti-strategy ─────────────────────────────────────────────────────────
    anti = bdm_anti(events, ohlcv, n_trials=n_trials)

    # ── Cost-stress ───────────────────────────────────────────────────────────
    stress = bdm_stress(events, ohlcv, n_trials=n_trials)
    stress_dsr_collapse = (
        (agg["dsr"] - stress["dsr"]) / agg["dsr"]
        if agg["dsr"] > 1e-6
        else 1.0
    )

    # ── Gate evaluation ───────────────────────────────────────────────────────
    gates = {
        "mean_return_bps >= 100":     agg["mean_return_bps"] >= 100.0,
        "win_rate >= 52%":            agg["win_rate"] >= 0.52,
        "sharpe >= 0.5":              agg["sharpe"] >= 0.5,
        "dsr >= 0.5":                 agg["dsr"] >= 0.5,
        "anti_strategy_return <= 0":  anti["mean_return_bps"] <= 0.0,
        "stress_dsr_collapse <= 50%": stress_dsr_collapse <= 0.5,
        "total_dev_events >= 40":     agg["n_trades"] >= 40,
    }
    all_pass = all(gates.values())

    # ── Gate report ───────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"BDM v1 Gate Report — split={split}")
    print("=" * 60)
    print(f"  Raw events:            {n_raw_events}")
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

    # ── MLflow logging ────────────────────────────────────────────────────────
    try:
        mlflow.set_experiment(_BDM_EXPERIMENT)
        with mlflow.start_run(tags={"stage": split, "strategy": "bdm_v1"}):
            mlflow.log_metrics({
                "raw_events":           float(n_raw_events),
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


def run_gate_check_g(split: str, n_trials: int | None = None) -> int:
    """Run the full gate-check sequence for Strategy G (BDM-Momentum).

    Returns 0 if all gates pass, 1 if any fail, 2 if data error.
    """
    from quant.data.bulk_deals import load_bulk_deals
    from quant.research.holdout_lock import HOLDOUT_START, read_holdout
    from quant.strategies.bdm_momentum import (
        build_events,
        compute_ema50,
        compute_gate_metrics as g_gate_metrics,
        run_anti_strategy as g_anti,
        run_cost_stress as g_stress,
        simulate_trades as g_simulate,
    )

    _G_HYPOTHESIS = (
        Path(__file__).parents[4] / "research" / "hypotheses"
        / "2026-05-23-bdm-momentum.md"
    )
    _G_EXPERIMENT = "bdm_momentum_v1"

    _G_SPLIT_DATES = {
        "train":   ("2015-01-01", "2023-06-30"),
        "dev":     (DEV_START, DEV_END),
        "holdout": (HOLDOUT_START, "2026-12-31"),
    }

    if split not in _G_SPLIT_DATES:
        logger.error("Invalid split: %r. Choose 'train', 'dev', or 'holdout'.", split)
        return 2

    if split == "holdout":
        try:
            read_holdout("bdm_momentum_v1", _G_HYPOTHESIS)
        except (PermissionError, ValueError, FileNotFoundError) as exc:
            logger.error("Hold-out unlock failed: %s", exc)
            return 2

    start, end = _G_SPLIT_DATES[split]

    if n_trials is None:
        try:
            client = mlflow.tracking.MlflowClient()
            exp = client.get_experiment_by_name(_G_EXPERIMENT)
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

    logger.info("Gate check: strategy=g split=%s trials=%d", split, n_trials)

    # ── Load bulk deal data ───────────────────────────────────────────────────
    try:
        raw_deals = load_bulk_deals(start=start, end=end, side="BUY",
                                    min_value_cr=1.0)
    except Exception as exc:
        logger.error("Bulk deal data load failed: %s", exc)
        return 2

    if raw_deals.empty:
        logger.error(
            "No bulk deal data for %s → %s.  Run bulk_deals ingest first.",
            start, end,
        )
        return 2

    # ── Load Midcap 150 universe ──────────────────────────────────────────────
    midcap150 = _load_midcap150()
    if not midcap150:
        logger.warning(
            "midcap150_constituents.csv not found — running on full bulk deal "
            "universe (no Midcap 150 filter).  Results may overstate edge."
        )

    # ── Build signal events (same as BDM; momentum filter applied in simulate) ─
    events = build_events(raw_deals, midcap150=midcap150 if midcap150 else None)
    n_raw_events = len(events)
    logger.info("Raw BDM-M signal events in %s → %s: %d", start, end, n_raw_events)

    if n_raw_events == 0:
        logger.error("Zero events built for %s → %s.", start, end)
        return 2

    # ── Load OHLCV with EMA warmup lookback (~100 calendar days before start) ──
    from datetime import timedelta
    ohlcv_start = (pd.Timestamp(start) - pd.Timedelta(days=100)).strftime("%Y-%m-%d")
    try:
        ohlcv = pit_load(symbol=None, start=ohlcv_start, end=end)
    except Exception as exc:
        logger.error("OHLCV load failed: %s", exc)
        return 2

    if ohlcv.empty:
        logger.error("No OHLCV data for %s → %s.", ohlcv_start, end)
        return 2

    # ── Pre-compute EMA50 once (shared across simulate / anti / stress) ────────
    logger.info("Computing 50-day EMA for all symbols...")
    ema50 = compute_ema50(ohlcv)

    # ── Simulate trades ───────────────────────────────────────────────────────
    trades = g_simulate(events, ohlcv, ema50=ema50)
    agg    = g_gate_metrics(trades, n_trials=n_trials)

    # ── Anti-strategy ─────────────────────────────────────────────────────────
    anti = g_anti(events, ohlcv, n_trials=n_trials, ema50=ema50)

    # ── Cost-stress ───────────────────────────────────────────────────────────
    stress = g_stress(events, ohlcv, n_trials=n_trials, seed=42, ema50=ema50)
    stress_dsr_collapse = (
        (agg["dsr"] - stress["dsr"]) / agg["dsr"]
        if agg["dsr"] > 1e-6
        else 1.0
    )

    # ── Gate evaluation ───────────────────────────────────────────────────────
    gates = {
        "mean_return_bps >= 100":     agg["mean_return_bps"] >= 100.0,
        "win_rate >= 52%":            agg["win_rate"] >= 0.52,
        "sharpe >= 0.5":              agg["sharpe"] >= 0.5,
        "dsr >= 0.5":                 agg["dsr"] >= 0.5,
        "anti_strategy_return <= 0":  anti["mean_return_bps"] <= 0.0,
        "stress_dsr_collapse <= 50%": stress_dsr_collapse <= 0.5,
        "total_dev_events >= 30":     agg["n_trades"] >= 30,
    }
    all_pass = all(gates.values())

    # ── Gate report ───────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"BDM-Momentum (G) v1 Gate Report — split={split}")
    print("=" * 60)
    print(f"  Raw events (pre-momentum): {n_raw_events}")
    print(f"  Executed trades:           {agg['n_trades']}")
    print(f"  Mean return (bps):         {agg['mean_return_bps']:.1f}")
    print(f"  Win rate:                  {agg['win_rate']:.1%}")
    print(f"  Sharpe:                    {agg['sharpe']:.3f}")
    print(f"  DSR (n_trials={n_trials}):      {agg['dsr']:.3f}")
    print(f"  Anti-strategy return:      {anti['mean_return_bps']:.1f} bps")
    print(f"  Cost-stress DSR:           {stress['dsr']:.3f} "
          f"(collapse {stress_dsr_collapse:.1%})")
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

    # ── MLflow logging ────────────────────────────────────────────────────────
    try:
        mlflow.set_experiment(_G_EXPERIMENT)
        with mlflow.start_run(tags={"stage": split, "strategy": "bdm_momentum_v1"}):
            mlflow.log_metrics({
                "raw_events":           float(n_raw_events),
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


def run_gate_check_h(split: str, n_trials: int | None = None) -> int:
    """Run the full gate-check sequence for Strategy H (BDM-Institutional).

    Returns 0 if all gates pass, 1 if any fail, 2 if data error.
    """
    from quant.data.bulk_deals import load_bulk_deals
    from quant.research.holdout_lock import HOLDOUT_START, read_holdout
    from quant.strategies.bdm_institutional import (
        build_events,
        compute_ema50,
        compute_gate_metrics as h_gate_metrics,
        run_anti_strategy as h_anti,
        run_cost_stress as h_stress,
        simulate_trades as h_simulate,
    )

    _H_HYPOTHESIS = (
        Path(__file__).parents[4] / "research" / "hypotheses"
        / "2026-05-23-bdm-institutional.md"
    )
    _H_EXPERIMENT = "bdm_institutional_v1"

    _H_SPLIT_DATES = {
        "train":   ("2015-01-01", "2023-06-30"),
        "dev":     (DEV_START, DEV_END),
        "holdout": (HOLDOUT_START, "2026-12-31"),
    }

    if split not in _H_SPLIT_DATES:
        logger.error("Invalid split: %r. Choose 'train', 'dev', or 'holdout'.", split)
        return 2

    if split == "holdout":
        try:
            read_holdout("bdm_institutional_v1", _H_HYPOTHESIS)
        except (PermissionError, ValueError, FileNotFoundError) as exc:
            logger.error("Hold-out unlock failed: %s", exc)
            return 2

    start, end = _H_SPLIT_DATES[split]

    if n_trials is None:
        try:
            client = mlflow.tracking.MlflowClient()
            exp = client.get_experiment_by_name(_H_EXPERIMENT)
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

    logger.info("Gate check: strategy=h split=%s trials=%d", split, n_trials)

    # ── Load bulk deal data ───────────────────────────────────────────────────
    try:
        raw_deals = load_bulk_deals(start=start, end=end, side="BUY",
                                    min_value_cr=1.0)
    except Exception as exc:
        logger.error("Bulk deal data load failed: %s", exc)
        return 2

    if raw_deals.empty:
        logger.error(
            "No bulk deal data for %s → %s.  Run bulk_deals ingest first.",
            start, end,
        )
        return 2

    midcap150 = _load_midcap150()
    if not midcap150:
        logger.warning(
            "midcap150_constituents.csv not found — running on full universe."
        )

    events = build_events(raw_deals, midcap150=midcap150 if midcap150 else None)
    n_raw_events = len(events)
    logger.info("Raw BDM-I signal events in %s → %s: %d", start, end, n_raw_events)

    if n_raw_events == 0:
        logger.error("Zero events built for %s → %s.", start, end)
        return 2

    # ── Load OHLCV with EMA warmup lookback ───────────────────────────────────
    ohlcv_start = (pd.Timestamp(start) - pd.Timedelta(days=100)).strftime("%Y-%m-%d")
    try:
        ohlcv = pit_load(symbol=None, start=ohlcv_start, end=end)
    except Exception as exc:
        logger.error("OHLCV load failed: %s", exc)
        return 2

    if ohlcv.empty:
        logger.error("No OHLCV data for %s → %s.", ohlcv_start, end)
        return 2

    logger.info("Computing 50-day EMA for all symbols...")
    ema50 = compute_ema50(ohlcv)

    trades = h_simulate(events, ohlcv, ema50=ema50)
    agg    = h_gate_metrics(trades, n_trials=n_trials)
    anti   = h_anti(events, ohlcv, n_trials=n_trials, ema50=ema50)
    stress = h_stress(events, ohlcv, n_trials=n_trials, seed=42, ema50=ema50)
    stress_dsr_collapse = (
        (agg["dsr"] - stress["dsr"]) / agg["dsr"]
        if agg["dsr"] > 1e-6
        else 1.0
    )

    gates = {
        "mean_return_bps >= 100":     agg["mean_return_bps"] >= 100.0,
        "win_rate >= 52%":            agg["win_rate"] >= 0.52,
        "sharpe >= 0.5":              agg["sharpe"] >= 0.5,
        "dsr >= 0.5":                 agg["dsr"] >= 0.5,
        "anti_strategy_return <= 0":  anti["mean_return_bps"] <= 0.0,
        "stress_dsr_collapse <= 50%": stress_dsr_collapse <= 0.5,
        "total_dev_events >= 20":     agg["n_trades"] >= 20,
    }
    all_pass = all(gates.values())

    print("\n" + "=" * 60)
    print(f"BDM-Institutional (H) v1 Gate Report — split={split}")
    print("=" * 60)
    print(f"  Raw events (pre-filters):  {n_raw_events}")
    print(f"  Executed trades:           {agg['n_trades']}")
    print(f"  Mean return (bps):         {agg['mean_return_bps']:.1f}")
    print(f"  Win rate:                  {agg['win_rate']:.1%}")
    print(f"  Sharpe:                    {agg['sharpe']:.3f}")
    print(f"  DSR (n_trials={n_trials}):      {agg['dsr']:.3f}")
    print(f"  Anti-strategy return:      {anti['mean_return_bps']:.1f} bps")
    print(f"  Cost-stress DSR:           {stress['dsr']:.3f} "
          f"(collapse {stress_dsr_collapse:.1%})")
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

    try:
        mlflow.set_experiment(_H_EXPERIMENT)
        with mlflow.start_run(tags={"stage": split, "strategy": "bdm_institutional_v1"}):
            mlflow.log_metrics({
                "raw_events":           float(n_raw_events),
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


def run_gate_check_i(split: str, n_trials: int | None = None) -> int:
    """Run the full gate-check sequence for Strategy I (Block Deal Momentum).

    Returns 0 if all gates pass, 1 if any fail, 2 if data error.
    """
    from quant.data.block_deals import load_block_deals
    from quant.research.holdout_lock import HOLDOUT_START, read_holdout
    from quant.strategies.block_momentum import (
        build_events,
        compute_ema50,
        compute_gate_metrics as i_gate_metrics,
        run_anti_strategy as i_anti,
        run_cost_stress as i_stress,
        simulate_trades as i_simulate,
    )

    _I_HYPOTHESIS = (
        Path(__file__).parents[4] / "research" / "hypotheses"
        / "2026-05-23-block-deal-momentum.md"
    )
    _I_EXPERIMENT = "block_momentum_v1"

    _I_SPLIT_DATES = {
        "train":   ("2015-01-01", "2023-06-30"),
        "dev":     (DEV_START, DEV_END),
        "holdout": (HOLDOUT_START, "2026-12-31"),
    }

    if split not in _I_SPLIT_DATES:
        logger.error("Invalid split: %r.", split)
        return 2

    if split == "holdout":
        try:
            read_holdout("block_momentum_v1", _I_HYPOTHESIS)
        except (PermissionError, ValueError, FileNotFoundError) as exc:
            logger.error("Hold-out unlock failed: %s", exc)
            return 2

    start, end = _I_SPLIT_DATES[split]

    if n_trials is None:
        try:
            client = mlflow.tracking.MlflowClient()
            exp = client.get_experiment_by_name(_I_EXPERIMENT)
            if exp:
                runs = client.search_runs(
                    experiment_ids=[exp.experiment_id],
                    filter_string="", max_results=1000,
                )
                n_trials = max(len(runs), 1)
            else:
                n_trials = 1
        except Exception:
            n_trials = 1

    logger.info("Gate check: strategy=i split=%s trials=%d", split, n_trials)

    try:
        raw_deals = load_block_deals(start=start, end=end, side="BUY")
    except Exception as exc:
        logger.error("Block deal data load failed: %s", exc)
        return 2

    if raw_deals.empty:
        logger.error("No block deal data for %s → %s.", start, end)
        return 2

    midcap150 = _load_midcap150()
    if not midcap150:
        logger.warning("midcap150_constituents.csv not found — running on full universe.")

    events = build_events(raw_deals, midcap150=midcap150 if midcap150 else None)
    n_raw_events = len(events)
    logger.info("Raw block deal events in %s → %s: %d", start, end, n_raw_events)

    if n_raw_events == 0:
        logger.error("Zero block deal events for %s → %s.", start, end)
        return 2

    ohlcv_start = (pd.Timestamp(start) - pd.Timedelta(days=100)).strftime("%Y-%m-%d")
    try:
        ohlcv = pit_load(symbol=None, start=ohlcv_start, end=end)
    except Exception as exc:
        logger.error("OHLCV load failed: %s", exc)
        return 2

    if ohlcv.empty:
        logger.error("No OHLCV data for %s → %s.", ohlcv_start, end)
        return 2

    logger.info("Computing 50-day EMA for all symbols...")
    ema50 = compute_ema50(ohlcv)

    trades = i_simulate(events, ohlcv, ema50=ema50)
    agg    = i_gate_metrics(trades, n_trials=n_trials)
    anti   = i_anti(events, ohlcv, n_trials=n_trials, ema50=ema50)
    stress = i_stress(events, ohlcv, n_trials=n_trials, seed=42, ema50=ema50)
    stress_dsr_collapse = (
        (agg["dsr"] - stress["dsr"]) / agg["dsr"]
        if agg["dsr"] > 1e-6 else 1.0
    )

    gates = {
        "mean_return_bps >= 100":     agg["mean_return_bps"] >= 100.0,
        "win_rate >= 52%":            agg["win_rate"] >= 0.52,
        "sharpe >= 0.5":              agg["sharpe"] >= 0.5,
        "dsr >= 0.5":                 agg["dsr"] >= 0.5,
        "anti_strategy_return <= 0":  anti["mean_return_bps"] <= 0.0,
        "stress_dsr_collapse <= 50%": stress_dsr_collapse <= 0.5,
        "total_dev_events >= 15":     agg["n_trades"] >= 15,
    }
    all_pass = all(gates.values())

    print("\n" + "=" * 60)
    print(f"Block Deal Momentum (I) v1 Gate Report — split={split}")
    print("=" * 60)
    print(f"  Raw events (pre-momentum): {n_raw_events}")
    print(f"  Executed trades:           {agg['n_trades']}")
    print(f"  Mean return (bps):         {agg['mean_return_bps']:.1f}")
    print(f"  Win rate:                  {agg['win_rate']:.1%}")
    print(f"  Sharpe:                    {agg['sharpe']:.3f}")
    print(f"  DSR (n_trials={n_trials}):      {agg['dsr']:.3f}")
    print(f"  Anti-strategy return:      {anti['mean_return_bps']:.1f} bps")
    print(f"  Cost-stress DSR:           {stress['dsr']:.3f} "
          f"(collapse {stress_dsr_collapse:.1%})")
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

    try:
        mlflow.set_experiment(_I_EXPERIMENT)
        with mlflow.start_run(tags={"stage": split, "strategy": "block_momentum_v1"}):
            mlflow.log_metrics({
                "raw_events":           float(n_raw_events),
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


def run_gate_check_j(split: str, n_trials: int | None = None) -> int:
    """Run the full gate-check sequence for Strategy J (Block Deal Stop).

    Returns 0 if all gates pass, 1 if any fail, 2 if data error.
    """
    from quant.data.block_deals import load_block_deals
    from quant.research.holdout_lock import HOLDOUT_START, read_holdout
    from quant.strategies.bdm_momentum import compute_ema50
    from quant.strategies.block_momentum import build_events
    from quant.strategies.block_momentum_stop import (
        compute_gate_metrics as j_gate_metrics,
        run_anti_strategy as j_anti,
        run_cost_stress as j_stress,
        simulate_trades as j_simulate,
    )

    _J_HYPOTHESIS = (
        Path(__file__).parents[4] / "research" / "hypotheses"
        / "2026-05-23-block-deal-stop.md"
    )
    _J_EXPERIMENT = "block_deal_stop_v1"

    _J_SPLIT_DATES = {
        "train":   ("2015-01-01", "2023-06-30"),
        "dev":     (DEV_START, DEV_END),
        "holdout": (HOLDOUT_START, "2026-12-31"),
    }

    if split not in _J_SPLIT_DATES:
        logger.error("Invalid split: %r.", split)
        return 2

    if split == "holdout":
        try:
            read_holdout("block_deal_stop_v1", _J_HYPOTHESIS)
        except (PermissionError, ValueError, FileNotFoundError) as exc:
            logger.error("Hold-out unlock failed: %s", exc)
            return 2

    start, end = _J_SPLIT_DATES[split]

    if n_trials is None:
        try:
            client = mlflow.tracking.MlflowClient()
            exp = client.get_experiment_by_name(_J_EXPERIMENT)
            if exp:
                runs = client.search_runs(
                    experiment_ids=[exp.experiment_id],
                    filter_string="", max_results=1000,
                )
                n_trials = max(len(runs), 1)
            else:
                n_trials = 1
        except Exception:
            n_trials = 1

    logger.info("Gate check: strategy=j split=%s trials=%d", split, n_trials)

    try:
        raw_deals = load_block_deals(start=start, end=end, side="BUY")
    except Exception as exc:
        logger.error("Block deal data load failed: %s", exc)
        return 2

    if raw_deals.empty:
        logger.error("No block deal data for %s → %s.", start, end)
        return 2

    midcap150 = _load_midcap150()
    if not midcap150:
        logger.warning("midcap150_constituents.csv not found — running on full universe.")

    events = build_events(raw_deals, midcap150=midcap150 if midcap150 else None)
    n_raw_events = len(events)
    logger.info("Raw block deal events in %s → %s: %d", start, end, n_raw_events)

    if n_raw_events == 0:
        logger.error("Zero block deal events for %s → %s.", start, end)
        return 2

    # EMA warmup lookback: 100 calendar days before start
    ohlcv_start = (pd.Timestamp(start) - pd.Timedelta(days=100)).strftime("%Y-%m-%d")
    try:
        ohlcv = pit_load(symbol=None, start=ohlcv_start, end=end)
    except Exception as exc:
        logger.error("OHLCV load failed: %s", exc)
        return 2

    if ohlcv.empty:
        logger.error("No OHLCV data for %s → %s.", ohlcv_start, end)
        return 2

    logger.info("Computing 50-day EMA for all symbols...")
    ema50 = compute_ema50(ohlcv)

    trades = j_simulate(events, ohlcv, ema50=ema50)
    agg    = j_gate_metrics(trades, n_trials=n_trials)
    anti   = j_anti(events, ohlcv, n_trials=n_trials, ema50=ema50)
    stress = j_stress(events, ohlcv, n_trials=n_trials, seed=42, ema50=ema50)
    stress_dsr_collapse = (
        (agg["dsr"] - stress["dsr"]) / agg["dsr"]
        if agg["dsr"] > 1e-6 else 1.0
    )

    gates = {
        "mean_return_bps >= 100":     agg["mean_return_bps"] >= 100.0,
        "win_rate >= 52%":            agg["win_rate"] >= 0.52,
        "sharpe >= 0.5":              agg["sharpe"] >= 0.5,
        "dsr >= 0.5":                 agg["dsr"] >= 0.5,
        "anti_strategy_return <= 0":  anti["mean_return_bps"] <= 0.0,
        "stress_dsr_collapse <= 50%": stress_dsr_collapse <= 0.5,
        "total_dev_events >= 15":     agg["n_trades"] >= 15,
    }
    all_pass = all(gates.values())

    n_stops = agg.get("n_stops", 0)

    print("\n" + "=" * 60)
    print(f"Block Deal Stop (J) v1 Gate Report — split={split}")
    print("=" * 60)
    print(f"  Raw events (pre-momentum): {n_raw_events}")
    print(f"  Executed trades:           {agg['n_trades']}")
    print(f"  Stop-loss exits:           {n_stops} "
          f"({n_stops / max(agg['n_trades'], 1):.0%} of trades)")
    print(f"  Mean return (bps):         {agg['mean_return_bps']:.1f}")
    print(f"  Win rate:                  {agg['win_rate']:.1%}")
    print(f"  Sharpe:                    {agg['sharpe']:.3f}")
    print(f"  DSR (n_trials={n_trials}):      {agg['dsr']:.3f}")
    print(f"  Anti-strategy return:      {anti['mean_return_bps']:.1f} bps")
    print(f"  Cost-stress DSR:           {stress['dsr']:.3f} "
          f"(collapse {stress_dsr_collapse:.1%})")
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

    try:
        mlflow.set_experiment(_J_EXPERIMENT)
        with mlflow.start_run(tags={"stage": split, "strategy": "block_deal_stop_v1"}):
            mlflow.log_metrics({
                "raw_events":           float(n_raw_events),
                "n_stops":              float(n_stops),
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


def run_gate_check_k(split: str, n_trials: int | None = None) -> int:
    """Run the full gate-check sequence for Strategy K (Block Deal 5-Day Hold).

    Returns 0 if all gates pass, 1 if any fail, 2 if data error.
    """
    from quant.data.block_deals import load_block_deals
    from quant.research.holdout_lock import HOLDOUT_START, read_holdout
    from quant.strategies.bdm_momentum import compute_ema50
    from quant.strategies.block_momentum import build_events
    from quant.strategies.block_momentum_5day import (
        compute_gate_metrics as k_gate_metrics,
        run_anti_strategy as k_anti,
        run_cost_stress as k_stress,
        simulate_trades as k_simulate,
    )

    _K_HYPOTHESIS = (
        Path(__file__).parents[4] / "research" / "hypotheses"
        / "2026-05-24-block-deal-5day.md"
    )
    _K_EXPERIMENT = "block_deal_5day_v1"

    _K_SPLIT_DATES = {
        "train":   ("2015-01-01", "2023-06-30"),
        "dev":     (DEV_START, DEV_END),
        "holdout": (HOLDOUT_START, "2026-12-31"),
    }

    if split not in _K_SPLIT_DATES:
        logger.error("Invalid split: %r.", split)
        return 2

    if split == "holdout":
        try:
            read_holdout("block_deal_5day_v1", _K_HYPOTHESIS)
        except (PermissionError, ValueError, FileNotFoundError) as exc:
            logger.error("Hold-out unlock failed: %s", exc)
            return 2

    start, end = _K_SPLIT_DATES[split]

    if n_trials is None:
        try:
            client = mlflow.tracking.MlflowClient()
            exp = client.get_experiment_by_name(_K_EXPERIMENT)
            if exp:
                runs = client.search_runs(
                    experiment_ids=[exp.experiment_id],
                    filter_string="", max_results=1000,
                )
                n_trials = max(len(runs), 1)
            else:
                n_trials = 1
        except Exception:
            n_trials = 1

    logger.info("Gate check: strategy=k split=%s trials=%d", split, n_trials)

    try:
        raw_deals = load_block_deals(start=start, end=end, side="BUY")
    except Exception as exc:
        logger.error("Block deal data load failed: %s", exc)
        return 2

    if raw_deals.empty:
        logger.error("No block deal data for %s → %s.", start, end)
        return 2

    midcap150 = _load_midcap150()
    if not midcap150:
        logger.warning("midcap150_constituents.csv not found — running on full universe.")

    events = build_events(raw_deals, midcap150=midcap150 if midcap150 else None)
    n_raw_events = len(events)
    logger.info("Raw block deal events in %s → %s: %d", start, end, n_raw_events)

    if n_raw_events == 0:
        logger.error("Zero block deal events for %s → %s.", start, end)
        return 2

    ohlcv_start = (pd.Timestamp(start) - pd.Timedelta(days=100)).strftime("%Y-%m-%d")
    try:
        ohlcv = pit_load(symbol=None, start=ohlcv_start, end=end)
    except Exception as exc:
        logger.error("OHLCV load failed: %s", exc)
        return 2

    if ohlcv.empty:
        logger.error("No OHLCV data for %s → %s.", ohlcv_start, end)
        return 2

    logger.info("Computing 50-day EMA for all symbols...")
    ema50 = compute_ema50(ohlcv)

    trades = k_simulate(events, ohlcv, ema50=ema50)
    agg    = k_gate_metrics(trades, n_trials=n_trials)
    anti   = k_anti(events, ohlcv, n_trials=n_trials, ema50=ema50)
    stress = k_stress(events, ohlcv, n_trials=n_trials, seed=42, ema50=ema50)
    stress_dsr_collapse = (
        (agg["dsr"] - stress["dsr"]) / agg["dsr"]
        if agg["dsr"] > 1e-6 else 1.0
    )

    gates = {
        "mean_return_bps >= 100":     agg["mean_return_bps"] >= 100.0,
        "win_rate >= 52%":            agg["win_rate"] >= 0.52,
        "sharpe >= 0.5":              agg["sharpe"] >= 0.5,
        "dsr >= 0.5":                 agg["dsr"] >= 0.5,
        "anti_strategy_return <= 0":  anti["mean_return_bps"] <= 0.0,
        "stress_dsr_collapse <= 50%": stress_dsr_collapse <= 0.5,
        "total_dev_events >= 15":     agg["n_trades"] >= 15,
    }
    all_pass = all(gates.values())

    print("\n" + "=" * 60)
    print(f"Block Deal 5-Day (K) v1 Gate Report — split={split}")
    print("=" * 60)
    print(f"  Raw events (pre-momentum): {n_raw_events}")
    print(f"  Executed trades:           {agg['n_trades']}")
    print(f"  Mean return (bps):         {agg['mean_return_bps']:.1f}")
    print(f"  Win rate:                  {agg['win_rate']:.1%}")
    print(f"  Sharpe:                    {agg['sharpe']:.3f}")
    print(f"  DSR (n_trials={n_trials}):      {agg['dsr']:.3f}")
    print(f"  Anti-strategy return:      {anti['mean_return_bps']:.1f} bps")
    print(f"  Cost-stress DSR:           {stress['dsr']:.3f} "
          f"(collapse {stress_dsr_collapse:.1%})")
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

    try:
        mlflow.set_experiment(_K_EXPERIMENT)
        with mlflow.start_run(tags={"stage": split, "strategy": "block_deal_5day_v1"}):
            mlflow.log_metrics({
                "raw_events":           float(n_raw_events),
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


def run_gate_check_l(split: str, n_trials: int | None = None) -> int:
    """Run the full gate-check sequence for Strategy L (PEAD YoY v3).

    Returns 0 if all gates pass, 1 if any fail, 2 if data error.
    """
    from quant.data.earnings_ingest import load_earnings
    from quant.research.holdout_lock import HOLDOUT_START, read_holdout
    from quant.strategies.pead_yoy import (
        build_events,
        compute_gate_metrics as l_gate_metrics,
        run_anti_strategy as l_anti,
        run_cost_stress as l_stress,
        simulate_trades as l_simulate,
    )

    _L_HYPOTHESIS = (
        Path(__file__).parents[4] / "research" / "hypotheses"
        / "2026-05-24-pead-yoy.md"
    )
    _L_EXPERIMENT = "pead_yoy_v1"

    _L_SPLIT_DATES = {
        "train":   ("2015-01-01", "2023-06-30"),
        "dev":     (DEV_START, DEV_END),
        "holdout": (HOLDOUT_START, "2026-12-31"),
    }

    if split not in _L_SPLIT_DATES:
        logger.error("Invalid split: %r.", split)
        return 2

    if split == "holdout":
        try:
            read_holdout("pead_yoy_v1", _L_HYPOTHESIS)
        except (PermissionError, ValueError, FileNotFoundError) as exc:
            logger.error("Hold-out unlock failed: %s", exc)
            return 2

    start, end = _L_SPLIT_DATES[split]

    if n_trials is None:
        try:
            client = mlflow.tracking.MlflowClient()
            exp = client.get_experiment_by_name(_L_EXPERIMENT)
            if exp:
                runs = client.search_runs(
                    experiment_ids=[exp.experiment_id],
                    filter_string="", max_results=1000,
                )
                n_trials = max(len(runs), 1)
            else:
                n_trials = 1
        except Exception:
            n_trials = 1

    logger.info("Gate check: strategy=l split=%s trials=%d", split, n_trials)

    # ── Load earnings data ────────────────────────────────────────────────────
    try:
        earnings = load_earnings(start=start, end=end)
    except Exception as exc:
        logger.error("Earnings data load failed: %s", exc)
        return 2

    if earnings.empty:
        logger.error(
            "No earnings data for %s → %s.  Run earnings ingest first.",
            start, end,
        )
        return 2

    # ── Load Midcap 150 universe ──────────────────────────────────────────────
    midcap150 = _load_midcap150()
    if not midcap150:
        logger.warning("midcap150_constituents.csv not found — running on full universe.")

    # ── Build signal events ───────────────────────────────────────────────────
    events = build_events(earnings, midcap150=midcap150 if midcap150 else None)
    n_raw_events = len(events)
    logger.info(
        "YoY EPS signal events (>=25%% growth) in %s → %s: %d",
        start, end, n_raw_events,
    )

    if n_raw_events == 0:
        logger.error(
            "Zero signal events for %s → %s.  "
            "Check earnings data and Midcap 150 constituent list.",
            start, end,
        )
        return 2

    # ── Load OHLCV ────────────────────────────────────────────────────────────
    try:
        ohlcv = pit_load(symbol=None, start=start, end=end)
    except Exception as exc:
        logger.error("OHLCV load failed: %s", exc)
        return 2

    if ohlcv.empty:
        logger.error("No OHLCV data for %s → %s.", start, end)
        return 2

    # ── Simulate trades ───────────────────────────────────────────────────────
    trades = l_simulate(events, ohlcv)
    agg    = l_gate_metrics(trades, n_trials=n_trials)
    anti   = l_anti(events, ohlcv, n_trials=n_trials)
    stress = l_stress(events, ohlcv, n_trials=n_trials, seed=42)
    stress_dsr_collapse = (
        (agg["dsr"] - stress["dsr"]) / agg["dsr"]
        if agg["dsr"] > 1e-6 else 1.0
    )

    gates = {
        "mean_return_bps >= 100":     agg["mean_return_bps"] >= 100.0,
        "win_rate >= 52%":            agg["win_rate"] >= 0.52,
        "sharpe >= 0.5":              agg["sharpe"] >= 0.5,
        "dsr >= 0.5":                 agg["dsr"] >= 0.5,
        "anti_strategy_return <= 0":  anti["mean_return_bps"] <= 0.0,
        "stress_dsr_collapse <= 50%": stress_dsr_collapse <= 0.5,
        "total_dev_events >= 15":     agg["n_trades"] >= 15,
    }
    all_pass = all(gates.values())

    print("\n" + "=" * 60)
    print(f"PEAD YoY v3 (L) Gate Report — split={split}")
    print("=" * 60)
    print(f"  Signal events (raw):       {n_raw_events}")
    print(f"  Executed trades:           {agg['n_trades']}")
    print(f"  Mean return (bps):         {agg['mean_return_bps']:.1f}")
    print(f"  Win rate:                  {agg['win_rate']:.1%}")
    print(f"  Sharpe:                    {agg['sharpe']:.3f}")
    print(f"  DSR (n_trials={n_trials}):      {agg['dsr']:.3f}")
    print(f"  Anti-strategy return:      {anti['mean_return_bps']:.1f} bps")
    print(f"  Cost-stress DSR:           {stress['dsr']:.3f} "
          f"(collapse {stress_dsr_collapse:.1%})")
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

    try:
        mlflow.set_experiment(_L_EXPERIMENT)
        with mlflow.start_run(tags={"stage": split, "strategy": "pead_yoy_v1"}):
            mlflow.log_metrics({
                "raw_events":           float(n_raw_events),
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


def run_gate_check_m(split: str, n_trials: int | None = None) -> int:
    """Run the full gate-check sequence for Strategy M (PEAD ML v4).

    Returns 0 if all gates pass, 1 if any fail, 2 if data error.
    """
    from quant.data.earnings_ingest import load_earnings
    from quant.research.holdout_lock import HOLDOUT_START, read_holdout
    from quant.strategies.pead_ml import (
        _PEADModel,
        build_events,
        build_feature_matrix,
        compute_gate_metrics as m_gate_metrics,
        compute_ohlcv_features,
        compute_targets,
        simulate_trades as m_simulate,
    )

    _M_HYPOTHESIS = (
        Path(__file__).parents[4] / "research" / "hypotheses"
        / "2026-05-24-pead-ml.md"
    )
    _M_EXPERIMENT = "pead_ml_v1"

    _M_SPLIT_DATES = {
        "train":   ("2015-01-01", "2023-06-30"),
        "dev":     (DEV_START, DEV_END),
        "holdout": (HOLDOUT_START, "2026-12-31"),
    }

    if split not in _M_SPLIT_DATES:
        logger.error("Invalid split: %r.", split)
        return 2

    if split == "holdout":
        try:
            read_holdout("pead_ml_v1", _M_HYPOTHESIS)
        except (PermissionError, ValueError, FileNotFoundError) as exc:
            logger.error("Hold-out unlock failed: %s", exc)
            return 2

    start, end = _M_SPLIT_DATES[split]
    train_start, train_end = _M_SPLIT_DATES["train"]

    if n_trials is None:
        try:
            client = mlflow.tracking.MlflowClient()
            exp = client.get_experiment_by_name(_M_EXPERIMENT)
            if exp:
                runs = client.search_runs(
                    experiment_ids=[exp.experiment_id],
                    filter_string="", max_results=1000,
                )
                n_trials = max(len(runs), 1)
            else:
                n_trials = 1
        except Exception:
            n_trials = 1

    logger.info("Gate check: strategy=m split=%s trials=%d", split, n_trials)

    # ── Load earnings ─────────────────────────────────────────────────────────
    logger.info("Loading earnings data (train + eval)...")
    try:
        # Load full history including train for YoY feature computation
        all_earnings = load_earnings(start=train_start, end=end)
        eval_earnings = all_earnings[
            (pd.to_datetime(all_earnings["business_date"]) >= pd.Timestamp(start))
            & (pd.to_datetime(all_earnings["business_date"]) <= pd.Timestamp(end))
        ].copy()
    except Exception as exc:
        logger.error("Earnings load failed: %s", exc)
        return 2

    if all_earnings.empty:
        logger.error("No earnings data for %s → %s.", train_start, end)
        return 2

    # ── Load Midcap 150 ───────────────────────────────────────────────────────
    midcap150 = _load_midcap150()
    if not midcap150:
        logger.warning("midcap150_constituents.csv not found — running on full universe.")

    mc150 = midcap150 if midcap150 else None

    # ── Build event universes ─────────────────────────────────────────────────
    train_events = build_events(
        all_earnings[
            (pd.to_datetime(all_earnings["business_date"]) >= pd.Timestamp(train_start))
            & (pd.to_datetime(all_earnings["business_date"]) <= pd.Timestamp(train_end))
        ],
        midcap150=mc150,
    )
    eval_events = build_events(eval_earnings, midcap150=mc150)

    logger.info(
        "Events — train: %d, eval (%s→%s): %d",
        len(train_events), start, end, len(eval_events),
    )

    if len(train_events) < 50:
        logger.error(
            "Too few training events (%d < 50). Cannot fit LightGBM.",
            len(train_events),
        )
        return 2

    if eval_events.empty:
        logger.error("No eval events for %s → %s.", start, end)
        return 2

    # ── Load OHLCV (EMA warmup + full eval period) ────────────────────────────
    ohlcv_start = (pd.Timestamp(train_start) - pd.Timedelta(days=100)).strftime("%Y-%m-%d")
    logger.info("Loading OHLCV from %s to %s...", ohlcv_start, end)
    try:
        ohlcv_full = pit_load(symbol=None, start=ohlcv_start, end=end)
    except Exception as exc:
        logger.error("OHLCV load failed: %s", exc)
        return 2

    if ohlcv_full.empty:
        logger.error("No OHLCV data for %s → %s.", ohlcv_start, end)
        return 2

    # ── Compute OHLCV features (vectorized over all symbols/dates) ────────────
    logger.info("Computing OHLCV features (vectorized)...")
    ohlcv_feats = compute_ohlcv_features(ohlcv_full)

    # ── Build feature matrices ────────────────────────────────────────────────
    logger.info("Building feature matrices...")
    X_train = build_feature_matrix(train_events, ohlcv_feats)
    X_eval  = build_feature_matrix(eval_events,  ohlcv_feats)

    # ── Compute targets for training ──────────────────────────────────────────
    logger.info("Computing training targets...")
    train_ohlcv = ohlcv_full[
        ohlcv_full.index.get_level_values("business_date") <= pd.Timestamp(train_end).date()
    ]
    y_train_raw = compute_targets(train_events, train_ohlcv)

    valid_mask = ~np.isnan(y_train_raw)
    if valid_mask.sum() < 50:
        logger.error(
            "Too few training examples with valid targets (%d < 50).",
            valid_mask.sum(),
        )
        return 2

    X_tr_valid   = X_train[valid_mask]
    y_tr_valid   = y_train_raw[valid_mask]
    dates_train  = pd.DatetimeIndex(
        pd.to_datetime(train_events[valid_mask]["event_date"])
    )

    base_rate = float(y_tr_valid.mean())
    logger.info(
        "Training: n=%d, base_rate=%.1f%% (target: >100 bps net)",
        len(y_tr_valid), base_rate * 100,
    )

    # ── Fit model ─────────────────────────────────────────────────────────────
    logger.info("Fitting PEAD ML model (LightGBM + isotonic calibration)...")
    model = _PEADModel()
    try:
        model.fit(
            X_tr_valid, y_tr_valid, dates_train,
            n_trials=n_trials,
            experiment_name=_M_EXPERIMENT,
        )
    except Exception as exc:
        logger.error("Model fit failed: %s", exc)
        return 2

    logger.info(
        "Model fitted: oof_brier=%.4f (random baseline=%.4f)",
        model.oof_brier,
        float(2 * base_rate * (1 - base_rate)),
    )

    # ── Simulate trades on eval period ────────────────────────────────────────
    eval_ohlcv = ohlcv_full  # need full range for T+5 exit prices
    trades = m_simulate(eval_events, eval_ohlcv, model, X_eval)
    agg    = m_gate_metrics(trades, n_trials=n_trials)

    # ── Anti-strategy ─────────────────────────────────────────────────────────
    anti_trades = m_simulate(eval_events, eval_ohlcv, model, X_eval)
    for t in anti_trades:
        t.gross_return = -t.gross_return
        t.net_return   = t.gross_return - _ROUND_TRIP_COST
    from quant.strategies.bdm import _ROUND_TRIP_COST
    anti = m_gate_metrics(anti_trades, n_trials=n_trials)
    anti["is_anti_strategy"] = True

    # ── Cost-stress ───────────────────────────────────────────────────────────
    rng = np.random.default_rng(42)
    stress_trades = m_simulate(eval_events, eval_ohlcv, model, X_eval, slippage_scale=2.0, rng=rng)
    stress = m_gate_metrics(stress_trades, n_trials=n_trials)
    stress["is_cost_stress"] = True
    stress_dsr_collapse = (
        (agg["dsr"] - stress["dsr"]) / agg["dsr"]
        if agg["dsr"] > 1e-6 else 1.0
    )

    gates = {
        "mean_return_bps >= 100":     agg["mean_return_bps"] >= 100.0,
        "win_rate >= 52%":            agg["win_rate"] >= 0.52,
        "sharpe >= 0.5":              agg["sharpe"] >= 0.5,
        "dsr >= 0.5":                 agg["dsr"] >= 0.5,
        "anti_strategy_return <= 0":  anti["mean_return_bps"] <= 0.0,
        "stress_dsr_collapse <= 50%": stress_dsr_collapse <= 0.5,
        "total_dev_trades >= 10":     agg["n_trades"] >= 10,
    }
    all_pass = all(gates.values())

    print("\n" + "=" * 60)
    print(f"PEAD ML v4 (M) Gate Report — split={split}")
    print("=" * 60)
    print(f"  Training events:           {len(y_tr_valid)}")
    print(f"  Training base rate:        {base_rate:.1%} (events with >155bps gross)")
    print(f"  OOF Brier score:           {model.oof_brier:.4f}  "
          f"(random={2*base_rate*(1-base_rate):.4f})")
    print(f"  Eval signal events:        {len(eval_events)}")
    print(f"  Executed trades (P≥0.5):   {agg['n_trades']}")
    print(f"  Mean return (bps):         {agg['mean_return_bps']:.1f}")
    print(f"  Win rate:                  {agg['win_rate']:.1%}")
    print(f"  Sharpe:                    {agg['sharpe']:.3f}")
    print(f"  DSR (n_trials={n_trials}):      {agg['dsr']:.3f}")
    print(f"  Anti-strategy return:      {anti['mean_return_bps']:.1f} bps")
    print(f"  Cost-stress DSR:           {stress['dsr']:.3f} "
          f"(collapse {stress_dsr_collapse:.1%})")
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

    try:
        mlflow.set_experiment(_M_EXPERIMENT)
        with mlflow.start_run(tags={"stage": split, "strategy": "pead_ml_v1"}):
            mlflow.log_metrics({
                "n_train_events":       float(len(y_tr_valid)),
                "train_base_rate":      base_rate,
                "oof_brier":            model.oof_brier,
                "eval_raw_events":      float(len(eval_events)),
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


def run_gate_check_n(split: str, n_trials: int | None = None) -> int:
    """Run the full gate-check sequence for Strategy N (PEAD ML v5 — repaired data).

    Identical to Strategy M in model architecture, features, threshold (P≥0.50),
    and falsification criteria.  The only difference is the earnings parquet now
    has net_profit_cr and revenue_cr patched from 132 Tickertape income-statement
    CSVs (DEC 2023 → MAR 2026).

    Returns 0 if all gates pass, 1 if any fail, 2 if data error.
    """
    from quant.data.earnings_ingest import load_earnings
    from quant.research.holdout_lock import HOLDOUT_START, read_holdout
    from quant.strategies.pead_ml import (
        _PEADModel,
        build_events,
        build_feature_matrix,
        compute_gate_metrics as m_gate_metrics,
        compute_ohlcv_features,
        compute_targets,
        simulate_trades as m_simulate,
    )

    _N_EXPERIMENT = "pead_ml_v2"

    _N_SPLIT_DATES = {
        "train":   ("2015-01-01", "2023-06-30"),
        "dev":     (DEV_START, DEV_END),
        "holdout": (HOLDOUT_START, "2026-12-31"),
    }

    if split not in _N_SPLIT_DATES:
        logger.error("Unknown split %r for strategy N", split)
        return 2

    if split == "holdout":
        read_holdout()

    train_start, train_end = _N_SPLIT_DATES["train"]
    eval_start, eval_end   = _N_SPLIT_DATES[split]
    end = eval_end  # alias used below

    # ── Load earnings ────────────────────────────────────────────────────────
    logger.info("[N] Loading earnings parquet...")
    try:
        earnings = load_earnings()
    except Exception as exc:
        logger.error("[N] Failed to load earnings: %s", exc)
        return 2

    # ── Load OHLCV ───────────────────────────────────────────────────────────
    ohlcv_start = (pd.Timestamp(train_start) - pd.Timedelta(days=100)).strftime("%Y-%m-%d")
    logger.info("[N] Loading OHLCV from %s to %s...", ohlcv_start, end)
    try:
        ohlcv_full = pit_load(symbol=None, start=ohlcv_start, end=end)
    except Exception as exc:
        logger.error("[N] OHLCV load failed: %s", exc)
        return 2

    if ohlcv_full.empty:
        logger.error("[N] No OHLCV data for %s → %s.", ohlcv_start, end)
        return 2

    logger.info("[N] Computing OHLCV features (vectorized)...")
    ohlcv_feats = compute_ohlcv_features(ohlcv_full)

    midcap = _load_midcap150()

    # ── Build events ─────────────────────────────────────────────────────────
    logger.info("[N] Building events...")
    all_events = build_events(earnings, midcap)
    all_events_dates = pd.to_datetime(all_events.event_date)
    train_events = all_events[
        (all_events_dates >= pd.Timestamp(train_start)) &
        (all_events_dates <= pd.Timestamp(train_end))
    ]
    eval_events = all_events[
        (all_events_dates >= pd.Timestamp(eval_start)) &
        (all_events_dates <= pd.Timestamp(eval_end))
    ]
    logger.info("[N] Train events: %d | Eval events: %d", len(train_events), len(eval_events))

    # ── Feature matrices ─────────────────────────────────────────────────────
    logger.info("[N] Building feature matrices...")
    X_train = build_feature_matrix(train_events, ohlcv_feats)
    X_eval  = build_feature_matrix(eval_events,  ohlcv_feats)

    # ── Training targets ─────────────────────────────────────────────────────
    logger.info("[N] Computing training targets...")
    train_ohlcv = ohlcv_full[
        ohlcv_full.index.get_level_values("business_date") <= pd.Timestamp(train_end).date()
    ]
    y_train_raw = compute_targets(train_events, train_ohlcv)

    valid_mask = ~np.isnan(y_train_raw)
    if valid_mask.sum() < 50:
        logger.error("[N] Too few training examples (%d < 50).", valid_mask.sum())
        return 2

    X_tr_valid  = X_train[valid_mask]
    y_tr_valid  = y_train_raw[valid_mask]
    dates_train = pd.DatetimeIndex(pd.to_datetime(train_events[valid_mask]["event_date"]))
    base_rate   = float(y_tr_valid.mean())
    logger.info("[N] Training: n=%d, base_rate=%.1f%%", len(y_tr_valid), base_rate * 100)

    # ── Fit model ─────────────────────────────────────────────────────────────
    logger.info("[N] Fitting LightGBM + isotonic calibration...")
    model = _PEADModel()
    try:
        model.fit(X_tr_valid, y_tr_valid, dates_train,
                  n_trials=n_trials or 1, experiment_name=_N_EXPERIMENT)
    except Exception as exc:
        logger.error("[N] Model fit failed: %s", exc)
        return 2
    logger.info("[N] OOF Brier=%.4f (random=%.4f)",
                model.oof_brier, float(2 * base_rate * (1 - base_rate)))

    # ── Simulate trades on eval period ────────────────────────────────────────
    eval_ohlcv = ohlcv_full
    trades = m_simulate(eval_events, eval_ohlcv, model, X_eval)
    agg    = m_gate_metrics(trades, n_trials=n_trials)

    # Diagnostics: P-score distribution
    p_scores = model.predict_proba(X_eval)
    p_max  = float(p_scores.max()) if len(p_scores) else 0.0
    p_uniq = int(pd.Series(p_scores).round(4).nunique())

    # Spearman ρ (informational)
    from scipy.stats import spearmanr
    ev_targets = compute_targets(eval_events, eval_ohlcv)
    valid_both = ~np.isnan(ev_targets)
    if valid_both.sum() > 1:
        rho, pval = spearmanr(p_scores[valid_both], ev_targets[valid_both])
    else:
        rho, pval = 0.0, 1.0
    logger.info("[N] Dev max_p=%.4f unique_p=%d Spearman_rho=%.3f p=%.3f",
                p_max, p_uniq, rho, pval)

    # ── Anti-strategy ─────────────────────────────────────────────────────────
    from quant.strategies.bdm import _ROUND_TRIP_COST as _N_ROUND_TRIP_COST
    anti_trades = m_simulate(eval_events, eval_ohlcv, model, X_eval)
    for t in anti_trades:
        t.gross_return = -t.gross_return
        t.net_return   = t.gross_return - _N_ROUND_TRIP_COST
    anti = m_gate_metrics(anti_trades, n_trials=n_trials)

    # ── Cost-stress ───────────────────────────────────────────────────────────
    rng = np.random.default_rng(42)
    stress_trades = m_simulate(eval_events, eval_ohlcv, model, X_eval,
                               slippage_scale=2.0, rng=rng)
    stress = m_gate_metrics(stress_trades, n_trials=n_trials)
    stress_dsr_collapse = (
        (agg["dsr"] - stress["dsr"]) / agg["dsr"]
        if agg["dsr"] > 1e-6 else 1.0
    )

    gates = {
        "mean_return_bps >= 100":     agg["mean_return_bps"] >= 100.0,
        "win_rate >= 52%":            agg["win_rate"] >= 0.52,
        "sharpe >= 0.5":              agg["sharpe"] >= 0.5,
        "dsr >= 0.5":                 agg["dsr"] >= 0.5,
        "anti_strategy_return <= 0":  anti["mean_return_bps"] <= 0.0,
        "stress_dsr_collapse <= 50%": stress_dsr_collapse <= 0.5,
        "total_dev_trades >= 10":     agg["n_trades"] >= 10,
    }
    all_pass = all(gates.values())

    print("\n" + "=" * 60)
    print(f"PEAD ML v5 (N) Gate Report — split={split}")
    print("=" * 60)
    print(f"  Training events:           {len(y_tr_valid)}")
    print(f"  Training base rate:        {base_rate:.1%}")
    print(f"  OOF Brier score:           {model.oof_brier:.4f}  "
          f"(random={2*base_rate*(1-base_rate):.4f})")
    print(f"  Eval signal events:        {len(eval_events)}")
    print(f"  Max dev P-score:           {p_max:.4f}")
    print(f"  Unique P values (dev):     {p_uniq}")
    print(f"  Spearman rho:              {rho:.3f} (p={pval:.3f})")
    print(f"  Executed trades (P≥0.5):   {agg['n_trades']}")
    print(f"  Mean return (bps):         {agg['mean_return_bps']:.1f}")
    print(f"  Win rate:                  {agg['win_rate']:.1%}")
    print(f"  Sharpe:                    {agg['sharpe']:.3f}")
    print(f"  DSR (n_trials={n_trials}):      {agg['dsr']:.3f}")
    print(f"  Anti-strategy return:      {anti['mean_return_bps']:.1f} bps")
    print(f"  Cost-stress DSR:           {stress['dsr']:.3f} "
          f"(collapse {stress_dsr_collapse:.1%})")
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

    try:
        mlflow.set_experiment(_N_EXPERIMENT)
        with mlflow.start_run(tags={"stage": split, "strategy": "pead_ml_v2"}):
            mlflow.log_metrics({
                "n_train_events":       float(len(y_tr_valid)),
                "train_base_rate":      base_rate,
                "oof_brier":            model.oof_brier,
                "eval_raw_events":      float(len(eval_events)),
                "max_p_score":          p_max,
                "unique_p":             float(p_uniq),
                "spearman_rho":         rho,
                "spearman_pval":        pval,
                "mean_return_bps":      agg["mean_return_bps"],
                "win_rate":             agg["win_rate"],
                "sharpe":               agg["sharpe"],
                "dsr":                  agg["dsr"],
                "anti_mean_return_bps": anti["mean_return_bps"],
                "stress_dsr":           stress["dsr"],
                "stress_dsr_collapse":  stress_dsr_collapse,
                "n_trials":             float(n_trials) if n_trials else 0.0,
                "gate_all_pass":        float(all_pass),
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
        choices=["pead_midcap", "index_recon", "idi", "bdm", "g", "h", "i", "j", "k", "l", "m", "n"],
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
    elif args.strategy == "idi":
        exit_code = run_gate_check_idi(args.split, n_trials=args.n_trials)
    elif args.strategy == "bdm":
        exit_code = run_gate_check_bdm(args.split, n_trials=args.n_trials)
    elif args.strategy == "g":
        exit_code = run_gate_check_g(args.split, n_trials=args.n_trials)
    elif args.strategy == "h":
        exit_code = run_gate_check_h(args.split, n_trials=args.n_trials)
    elif args.strategy == "i":
        exit_code = run_gate_check_i(args.split, n_trials=args.n_trials)
    elif args.strategy == "j":
        exit_code = run_gate_check_j(args.split, n_trials=args.n_trials)
    elif args.strategy == "k":
        exit_code = run_gate_check_k(args.split, n_trials=args.n_trials)
    elif args.strategy == "l":
        exit_code = run_gate_check_l(args.split, n_trials=args.n_trials)
    elif args.strategy == "m":
        exit_code = run_gate_check_m(args.split, n_trials=args.n_trials)
    else:  # "n"
        exit_code = run_gate_check_n(args.split, n_trials=args.n_trials)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
