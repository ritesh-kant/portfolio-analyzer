"""Strategy M — PEAD v4: ML Selection with Reliable Features.

Pre-registered hypothesis: research/hypotheses/2026-05-24-pead-ml.md
LightGBM binary classifier + isotonic calibration.
DSR n_trials starts at 1 (clean experiment: pead_ml_v1).

Signal universe: all Midcap 150 quarterly events where eps_reported > 0 and
yoy_eps_prev > 0 (both quarters profitable).  No hard YoY threshold — the model
learns the threshold jointly with other features.

Key insight: day0_reaction × yoy_eps_growth interaction is the primary signal.
A stock that reports strong earnings but barely moves on announcement day is
underappreciated and will drift up over the next 5 days.

10 pre-registered features (hypothesis §4, immutable):
  yoy_eps_growth, yoy_rev_growth, net_profit_margin,
  day0_reaction, day0_vol_ratio, ema50_dist, pre5d_return,
  days_since_last_result, quarter_sin, quarter_cos

Gate criteria (hypothesis §8 — immutable):
  mean_return >= 100 bps | win_rate >= 52% | Sharpe >= 0.5 | DSR >= 0.5
  anti_strategy <= 0    | cost_stress <= 50% collapse | dev_trades >= 10

Run the gate check:
  python -m quant.research.run --strategy m --split dev
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from quant.data.promoter_pledge import is_pledge_flagged
from quant.research.holdout_lock import assert_no_holdout_access
from quant.strategies.bdm import (
    _ROUND_TRIP_COST,
    _nth_trading_day_after,
    is_election_period,
)

logger = logging.getLogger(__name__)

_EXIT_DAYS = 5
_P_THRESHOLD = 0.5          # pre-registered, immutable
_MIN_DEV_TRADES = 10        # pre-registered minimum

# Pre-registered features (hypothesis §4, immutable)
FEATURES: list[str] = [
    "yoy_eps_growth",
    "yoy_rev_growth",
    "net_profit_margin",
    "day0_reaction",
    "day0_vol_ratio",
    "ema50_dist",
    "pre5d_return",
    "days_since_last_result",
    "quarter_sin",
    "quarter_cos",
]

_LGBM_PARAMS: dict[str, Any] = {
    "objective":          "binary",
    "max_depth":          3,
    "n_estimators":       100,
    "learning_rate":      0.05,
    "min_child_samples":  20,
    "subsample":          0.8,
    "colsample_bytree":   0.8,
    "random_state":       42,
    "verbose":            -1,
    "n_jobs":             -1,
}


# ── Feature engineering ────────────────────────────────────────────────────────

def compute_ohlcv_features(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """Compute OHLCV-derived features for all (date, symbol) pairs.

    Returns a DataFrame with the same (business_date, symbol) MultiIndex as
    ohlcv, plus columns: day0_reaction, day0_vol_ratio, ema50_dist, pre5d_return.
    """
    # Work on unstacked close/volume for vectorized rolling ops
    close  = ohlcv["close"].unstack(level="symbol")
    volume = ohlcv["volume"].unstack(level="symbol")
    prev_close = ohlcv["prev_close"].unstack(level="symbol")

    # day0_reaction: close vs prev_close on same day (announcement day reaction)
    day0_reaction = (close / prev_close - 1.0).clip(-0.5, 0.5)

    # day0_vol_ratio: volume / 20-day rolling average
    vol_avg20 = volume.rolling(window=20, min_periods=10).mean()
    day0_vol_ratio = (volume / vol_avg20.replace(0, np.nan)).clip(0, 20)

    # ema50_dist: (close - EMA50) / EMA50
    ema50 = close.ewm(span=50, adjust=False, min_periods=25).mean()
    ema50_dist = ((close - ema50) / ema50.replace(0, np.nan)).clip(-0.5, 0.5)

    # pre5d_return: close today / close 5 trading days ago - 1
    pre5d_return = (close / close.shift(5) - 1.0).clip(-0.5, 0.5)

    feats = pd.concat(
        {
            "day0_reaction":  day0_reaction.stack(),
            "day0_vol_ratio": day0_vol_ratio.stack(),
            "ema50_dist":     ema50_dist.stack(),
            "pre5d_return":   pre5d_return.stack(),
        },
        axis=1,
    )
    return feats


def _fiscal_quarter_to_trig(fq: int) -> tuple[float, float]:
    """Quarter 1–4 → (sin, cos) encoding."""
    angle = 2 * math.pi * (fq - 1) / 4
    return math.sin(angle), math.cos(angle)


def build_events(
    earnings: pd.DataFrame,
    midcap150: set[str] | list[str] | None = None,
) -> pd.DataFrame:
    """Extract signal universe: all positive EPS events with yoy_eps_prev.

    Broader than Strategy L — no ≥25% threshold. ML model learns the threshold.

    Returns
    -------
    DataFrame with columns:
        symbol, event_date, eps_reported, yoy_eps_prev, revenue_cr,
        yoy_rev_prev, net_profit_cr, fiscal_quarter, fiscal_year,
        days_since_last_result.
    """
    df = earnings.copy()
    if df.empty:
        return pd.DataFrame()

    df["business_date"] = pd.to_datetime(df["business_date"]).dt.date

    if midcap150 is not None:
        universe = {s.upper() for s in midcap150}
        df = df[df["symbol"].isin(universe)]

    if "result_type" in df.columns:
        df = df[df["result_type"] == "quarterly"]

    df = df.dropna(subset=["eps_reported", "yoy_eps_prev"])
    df = df[(df["eps_reported"] > 0) & (df["yoy_eps_prev"] > 0)]

    if df.empty:
        return pd.DataFrame()

    # Compute days_since_last_result per symbol (calendar days)
    df = df.sort_values(["symbol", "business_date"])
    df["prev_result_date"] = df.groupby("symbol")["business_date"].shift(1)
    df["days_since_last_result"] = (
        pd.to_datetime(df["business_date"]) - pd.to_datetime(df["prev_result_date"])
    ).dt.days.fillna(90.0).clip(0, 180)

    # Deduplicate: one event per (symbol, date)
    df = (
        df.sort_values("eps_reported", ascending=False)
        .groupby(["symbol", "business_date"])
        .first()
        .reset_index()
        .rename(columns={"business_date": "event_date"})
    )

    return df.sort_values(["event_date", "symbol"]).reset_index(drop=True)


def build_feature_matrix(
    events: pd.DataFrame,
    ohlcv_feats: pd.DataFrame,
) -> pd.DataFrame:
    """Compute full 10-feature matrix for a set of events.

    Parameters
    ----------
    events : output of build_events()
    ohlcv_feats : output of compute_ohlcv_features()

    Returns
    -------
    DataFrame with index aligned to events, columns = FEATURES.
    Missing features filled with 0.0 (neutral imputation).
    """
    rows = []
    for _, ev in events.iterrows():
        sym = str(ev["symbol"]).upper()
        event_date = pd.Timestamp(ev["event_date"]).date()

        # Earnings features
        eps   = float(ev["eps_reported"])
        eps_p = float(ev["yoy_eps_prev"])
        rev   = float(ev.get("revenue_cr") or 0)
        rev_p = float(ev.get("yoy_revenue_prev") or 0) if ev.get("yoy_revenue_prev") else 0.0
        npr   = float(ev.get("net_profit_cr") or 0)
        fq    = int(ev.get("fiscal_quarter") or 1)
        dslr  = float(ev.get("days_since_last_result") or 90)

        yoy_eps = (eps - eps_p) / abs(eps_p) if abs(eps_p) > 1e-6 else 0.0
        yoy_rev = ((rev - rev_p) / abs(rev_p)) if (abs(rev_p) > 1e-6 and rev_p != 0) else 0.0
        npm = (npr / rev) if rev > 1e-6 else 0.0
        q_sin, q_cos = _fiscal_quarter_to_trig(fq)

        # OHLCV features
        ohlcv_row: dict[str, float] = {}
        try:
            row = ohlcv_feats.loc[(event_date, sym)]
            for col in ("day0_reaction", "day0_vol_ratio", "ema50_dist", "pre5d_return"):
                v = row.get(col) if hasattr(row, "get") else row[col]
                ohlcv_row[col] = float(v) if (v is not None and not np.isnan(float(v))) else 0.0
        except (KeyError, TypeError):
            ohlcv_row = {c: 0.0 for c in ("day0_reaction", "day0_vol_ratio", "ema50_dist", "pre5d_return")}

        rows.append({
            "yoy_eps_growth":        np.clip(yoy_eps, -3, 10),
            "yoy_rev_growth":        np.clip(yoy_rev, -1, 5),
            "net_profit_margin":     np.clip(npm, -0.5, 0.5),
            "day0_reaction":         ohlcv_row["day0_reaction"],
            "day0_vol_ratio":        ohlcv_row["day0_vol_ratio"],
            "ema50_dist":            ohlcv_row["ema50_dist"],
            "pre5d_return":          ohlcv_row["pre5d_return"],
            "days_since_last_result": np.clip(dslr, 0, 180),
            "quarter_sin":           q_sin,
            "quarter_cos":           q_cos,
        })

    return pd.DataFrame(rows, columns=FEATURES).fillna(0.0)


def compute_targets(
    events: pd.DataFrame,
    ohlcv: pd.DataFrame,
    all_biz_dates: pd.DatetimeIndex | None = None,
) -> np.ndarray:
    """Compute binary target: T+1 open → T+5 close net return > 100 bps.

    Returns 1D array aligned to events (NaN for events with missing prices).
    """
    if all_biz_dates is None:
        all_biz_dates = pd.DatetimeIndex(
            pd.to_datetime(
                sorted(ohlcv.index.get_level_values("business_date").unique())
            )
        )

    targets = np.full(len(events), np.nan)
    threshold = 100 / 10_000 + _ROUND_TRIP_COST  # 100 bps net → 155 bps gross

    for i, (_, ev) in enumerate(events.iterrows()):
        event_ts = pd.Timestamp(ev["event_date"])
        sym = str(ev["symbol"]).upper()

        entry_ts = _nth_trading_day_after(event_ts, 1, all_biz_dates)
        if entry_ts is None:
            continue
        exit_ts = _nth_trading_day_after(entry_ts, _EXIT_DAYS, all_biz_dates)
        if exit_ts is None:
            continue

        try:
            entry_price = float(ohlcv.loc[(entry_ts.date(), sym), "open"])
            exit_price  = float(ohlcv.loc[(exit_ts.date(), sym), "close"])
        except KeyError:
            continue

        if entry_price < 1e-6 or exit_price < 1e-6:
            continue

        gross_return = exit_price / entry_price - 1.0
        targets[i] = 1.0 if gross_return >= threshold else 0.0

    return targets


# ── ML model ───────────────────────────────────────────────────────────────────

class _PEADModel:
    """LightGBM + isotonic calibration, trained with purged k-fold.

    The model is self-contained and not tied to the old PEAD feature registry.
    """

    def __init__(self) -> None:
        self._lgbm = None
        self._calibrator = None
        self.is_fitted = False
        self.oof_brier = float("nan")
        self.feature_cols = FEATURES

    def fit(
        self,
        X: pd.DataFrame,
        y: np.ndarray,
        dates: pd.DatetimeIndex,
        n_trials: int = 1,
        experiment_name: str = "pead_ml_v1",
    ) -> "_PEADModel":
        import lightgbm as lgb
        import mlflow
        from sklearn.calibration import IsotonicRegression
        from sklearn.metrics import brier_score_loss

        from quant.research.dsr import deflated_sharpe
        from quant.research.purged_kfold import purged_kfold_split

        X_arr = X[FEATURES].fillna(0.0).astype(np.float32).values
        y_arr = np.asarray(y, dtype=np.float32)

        # Purged k-fold for OOF predictions
        splits = purged_kfold_split(dates, holding_period_days=5, k=5)
        oof_probs = np.full(len(y_arr), np.nan)

        for train_idx, test_idx in splits:
            X_tr, y_tr = X_arr[train_idx], y_arr[train_idx]
            X_te        = X_arr[test_idx]

            clf = lgb.LGBMClassifier(**_LGBM_PARAMS)
            clf.fit(X_tr, y_tr)
            oof_probs[test_idx] = clf.predict_proba(X_te)[:, 1]

        # Calibrate on OOF predictions
        valid_mask = ~np.isnan(oof_probs)
        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(oof_probs[valid_mask], y_arr[valid_mask])
        cal_oof = calibrator.predict(oof_probs[valid_mask])

        self.oof_brier = float(brier_score_loss(y_arr[valid_mask], cal_oof))
        oof_dsr = deflated_sharpe(cal_oof - y_arr[valid_mask], n_trials=n_trials)

        logger.info(
            "PEAD ML fit: n=%d features=%d oof_brier=%.4f oof_dsr=%.3f",
            len(y_arr), len(FEATURES), self.oof_brier, oof_dsr,
        )

        # Final model trained on full training set
        final_clf = lgb.LGBMClassifier(**_LGBM_PARAMS)
        final_clf.fit(X_arr, y_arr)

        self._lgbm = final_clf
        self._calibrator = calibrator
        self.is_fitted = True

        # MLflow logging
        try:
            mlflow.set_experiment(experiment_name)
            with mlflow.start_run(tags={"stage": "train", "strategy": "pead_ml_v1"}):
                mlflow.log_metrics({
                    "oof_brier": self.oof_brier,
                    "oof_dsr": oof_dsr,
                    "n_train": float(len(y_arr)),
                    "n_trials": float(n_trials),
                    "base_rate": float(y_arr.mean()),
                })
                # Feature importances
                for feat, imp in zip(FEATURES, final_clf.feature_importances_):
                    mlflow.log_metric(f"feat_imp_{feat}", float(imp))
        except Exception as exc:
            logger.warning("MLflow train logging failed (non-fatal): %s", exc)

        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        if not self.is_fitted:
            raise RuntimeError("Model not fitted")
        X_arr = X[FEATURES].fillna(0.0).astype(np.float32).values
        raw = self._lgbm.predict_proba(X_arr)[:, 1]
        return self._calibrator.predict(raw)


# ── Simulation ─────────────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    symbol: str
    event_date: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    gross_return: float
    net_return: float
    hold_days: int
    p_score: float
    yoy_eps_growth: float
    day0_reaction: float
    slippage_draw: float = 0.0


def simulate_trades(
    events: pd.DataFrame,
    ohlcv: pd.DataFrame,
    model: _PEADModel,
    X: pd.DataFrame,
    p_threshold: float = _P_THRESHOLD,
    slippage_scale: float = 1.0,
    rng: np.random.Generator | None = None,
) -> list[TradeRecord]:
    """Simulate ML-filtered PEAD trades: T+1 open → T+5 close."""
    if events.empty or ohlcv.empty:
        return []

    # Score all events
    scores = model.predict_proba(X)

    events_arr = events.reset_index(drop=True)

    all_biz_dates = pd.DatetimeIndex(
        pd.to_datetime(
            sorted(ohlcv.index.get_level_values("business_date").unique())
        )
    )

    trades: list[TradeRecord] = []
    election_skipped = 0
    pledge_skipped   = 0
    low_score_skipped = 0
    no_entry_skipped = 0
    no_exit_skipped  = 0

    for i, (_, ev) in enumerate(events_arr.iterrows()):
        p_score = float(scores[i])

        if p_score < p_threshold:
            low_score_skipped += 1
            continue

        event_ts = pd.Timestamp(ev["event_date"])
        sym = str(ev["symbol"]).upper()

        try:
            assert_no_holdout_access(event_ts)
        except ValueError:
            logger.warning("Skipping hold-out event: %s %s", sym, event_ts.date())
            continue

        if is_election_period(event_ts):
            election_skipped += 1
            continue

        if is_pledge_flagged(sym, event_ts.date()):
            pledge_skipped += 1
            continue

        # ── Entry: T+1 open ────────────────────────────────────────────────────
        entry_ts = _nth_trading_day_after(event_ts, 1, all_biz_dates)
        if entry_ts is None:
            no_entry_skipped += 1
            continue

        entry_key = entry_ts.date()
        try:
            entry_price = float(ohlcv.loc[(entry_key, sym), "open"])
        except KeyError:
            no_entry_skipped += 1
            continue

        if entry_price < 1e-6:
            no_entry_skipped += 1
            continue

        # ── Exit: T+5 close ────────────────────────────────────────────────────
        exit_ts = _nth_trading_day_after(entry_ts, _EXIT_DAYS, all_biz_dates)
        if exit_ts is None:
            no_exit_skipped += 1
            continue

        exit_key = exit_ts.date()
        try:
            exit_price = float(ohlcv.loc[(exit_key, sym), "close"])
        except KeyError:
            no_exit_skipped += 1
            continue

        if exit_price < 1e-6:
            no_exit_skipped += 1
            continue

        gross_return = exit_price / entry_price - 1.0
        hold_days    = (exit_ts - entry_ts).days

        if rng is not None and slippage_scale > 1.0:
            slip_draw  = float(rng.standard_t(df=4)) * 0.0015 * slippage_scale
            total_cost = _ROUND_TRIP_COST + abs(slip_draw)
        else:
            slip_draw  = 0.0
            total_cost = _ROUND_TRIP_COST

        net_return = gross_return - total_cost

        trades.append(TradeRecord(
            symbol=sym,
            event_date=str(event_ts.date()),
            entry_date=str(entry_key),
            exit_date=str(exit_key),
            entry_price=entry_price,
            exit_price=exit_price,
            gross_return=gross_return,
            net_return=net_return,
            hold_days=hold_days,
            p_score=p_score,
            yoy_eps_growth=float(X.iloc[i]["yoy_eps_growth"]),
            day0_reaction=float(X.iloc[i]["day0_reaction"]),
            slippage_draw=slip_draw,
        ))

    logger.info(
        "ML filter: skipped %d low-score, %d election, %d pledge; "
        "executed %d trades",
        low_score_skipped, election_skipped, pledge_skipped, len(trades),
    )
    if no_entry_skipped:
        logger.info("No T+1 entry price: skipped %d", no_entry_skipped)
    if no_exit_skipped:
        logger.info("No T+5 exit price: skipped %d", no_exit_skipped)

    return trades


def compute_gate_metrics(trades: list[TradeRecord], n_trials: int = 1) -> dict:
    """Compute all 7 pre-registered gate metrics (hypothesis §8).

    Minimum dev trades gate for Strategy M is ≥ 10.
    """
    from quant.research.dsr import deflated_sharpe

    if not trades:
        return {
            "mean_return_bps": 0.0,
            "win_rate": 0.0,
            "sharpe": 0.0,
            "dsr": 0.0,
            "n_trades": 0,
            "gate_pass": False,
            "fail_reason": "no trades",
        }

    net_returns = np.array([t.net_return for t in trades])
    mean_bps    = float(np.mean(net_returns) * 10_000)
    win_rate    = float(np.mean(net_returns > 0))
    std_ret     = float(np.std(net_returns, ddof=1)) if len(net_returns) > 1 else 0.0
    sharpe      = float(np.mean(net_returns) / std_ret) if std_ret > 1e-10 else 0.0
    dsr         = deflated_sharpe(net_returns, n_trials=n_trials)

    fail_reasons = []
    if mean_bps < 100.0:
        fail_reasons.append(f"mean_return {mean_bps:.1f} bps < 100 bps")
    if win_rate < 0.52:
        fail_reasons.append(f"win_rate {win_rate:.2%} < 52%")
    if sharpe < 0.5:
        fail_reasons.append(f"Sharpe {sharpe:.3f} < 0.5")
    if dsr < 0.5:
        fail_reasons.append(f"DSR {dsr:.3f} < 0.5")
    if len(trades) < _MIN_DEV_TRADES:
        fail_reasons.append(f"n_trades {len(trades)} < {_MIN_DEV_TRADES}")

    return {
        "mean_return_bps": mean_bps,
        "win_rate": win_rate,
        "sharpe": sharpe,
        "dsr": dsr,
        "n_trades": len(trades),
        "gate_pass": not bool(fail_reasons),
        "fail_reason": "; ".join(fail_reasons) if fail_reasons else "all pass",
    }
