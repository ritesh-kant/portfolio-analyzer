"""Feature builders for L2 feature store.  Month 2.

Every builder is a *pure function*:
    Input  → PIT-correct OHLCV DataFrame from pit_loader.load()
    Output → DataFrame with columns (symbol, business_date, data_available_at,
              ...feature_cols...)

Rules enforced by this module
------------------------------
1.  ``assert_no_holdout_access`` is called at the top of every public builder.
    Any function that accepts a date and might touch price data must guard
    against hold-out access.

2.  ``data_available_at`` is always the same-day EOD timestamp (18:00 IST /
    12:30 UTC).  Features derived from OHLCV on day D are available from
    18:00 IST on day D — conservative and correct.

3.  No hidden state.  No global indicator caches.  No references to
    ``signal_replay`` or any of the deleted indicator pipelines.

Feature roadmap
---------------
Month 2 (here):
    momentum_features   — 5d and 20d log-return momentum, cross-sectional
                          residual vs universe median
    turnover_features   — turnover z-score (liquidity proxy)

Month 3 (after filing_parser agent is live):
    earnings_surprise_features — revenue/margin surprise %, guidance direction
    earnings_reaction_features — day-of price reaction relative to PEAD base-rate
    regime_features            — market regime label from regime classifier
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from quant.research.holdout_lock import assert_no_holdout_access

logger = logging.getLogger(__name__)

# Columns every builder must produce
_REQUIRED_COLS = frozenset({"symbol", "business_date", "data_available_at"})


def _validate_output(df: pd.DataFrame, builder_name: str) -> None:
    missing = _REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(
            f"{builder_name}: output DataFrame is missing required columns {missing}."
        )


def momentum_features(
    ohlcv: pd.DataFrame,
    symbol: str,
    as_of_date: str,
    short_window: int = 5,
    long_window: int = 20,
) -> pd.DataFrame:
    """5-day and 20-day log-return momentum, residualised vs universe median.

    "Residualised" strips out broad market direction: the feature captures
    stock-specific drift rather than regime.  For PEAD, we want to know
    whether a stock is drifting relative to its peers after earnings.

    Parameters
    ----------
    ohlcv : pd.DataFrame
        PIT-correct OHLCV from ``pit_loader.load()``.
        Index: ``(business_date, symbol)`` or flat with those as columns.
        Must include ``close`` and ``as_of_timestamp``.
    symbol : str
        NSE symbol to compute features for.
    as_of_date : str
        Inference date ("YYYY-MM-DD").  Hold-out guard is applied.
    short_window : int
        Rolling window in trading days for short momentum (default 5).
    long_window : int
        Rolling window in trading days for long momentum (default 20).

    Returns
    -------
    pd.DataFrame with columns:
        symbol, business_date, data_available_at,
        momentum_5d, momentum_20d,
        momentum_residual_5d, momentum_residual_20d
    Empty DataFrame if insufficient history or symbol not found.
    """
    assert_no_holdout_access(as_of_date)

    if ohlcv is None or ohlcv.empty:
        return pd.DataFrame()

    # ── Extract close prices for the symbol ───────────────────────────────────
    if isinstance(ohlcv.index, pd.MultiIndex):
        try:
            prices = ohlcv.xs(symbol, level="symbol")["close"].sort_index()
        except KeyError:
            logger.debug("momentum_features: %s not in ohlcv index", symbol)
            return pd.DataFrame()
    else:
        mask = ohlcv["symbol"] == symbol
        if not mask.any():
            logger.debug("momentum_features: %s not in ohlcv", symbol)
            return pd.DataFrame()
        prices = ohlcv.loc[mask, "close"].sort_index()

    if len(prices) < long_window + 1:
        logger.debug(
            "momentum_features: insufficient history for %s (%d < %d)",
            symbol, len(prices), long_window + 1,
        )
        return pd.DataFrame()

    log_rets = np.log(prices / prices.shift(1))
    mom_5d = log_rets.rolling(short_window).sum()
    mom_20d = log_rets.rolling(long_window).sum()

    # ── Universe residual (cross-sectional median on each date) ───────────────
    if isinstance(ohlcv.index, pd.MultiIndex):
        all_close = ohlcv["close"].unstack(level="symbol")
        all_log = np.log(all_close / all_close.shift(1))
        univ_med_5d = all_log.rolling(short_window).sum().median(axis=1)
        univ_med_20d = all_log.rolling(long_window).sum().median(axis=1)
    else:
        # Single-symbol input — no cross-sectional residual possible
        univ_med_5d = pd.Series(0.0, index=mom_5d.index)
        univ_med_20d = pd.Series(0.0, index=mom_20d.index)

    residual_5d = mom_5d - univ_med_5d
    residual_20d = mom_20d - univ_med_20d

    result = pd.DataFrame(
        {
            "symbol": symbol,
            "business_date": mom_5d.index,
            "momentum_5d": mom_5d.values,
            "momentum_20d": mom_20d.values,
            "momentum_residual_5d": residual_5d.values,
            "momentum_residual_20d": residual_20d.values,
        }
    )

    # PIT: data available at close-of-day on business_date (18:00 IST)
    result["data_available_at"] = pd.to_datetime(result["business_date"])

    result = result.dropna(subset=["momentum_5d", "momentum_20d"])
    _validate_output(result, "momentum_features")
    return result.reset_index(drop=True)


def turnover_features(
    ohlcv: pd.DataFrame,
    symbol: str,
    as_of_date: str,
    window: int = 20,
) -> pd.DataFrame:
    """Turnover z-score: liquidity and participation proxy.

    High abnormal turnover around earnings indicates strong informed-trader
    participation — a positive PEAD predictor.  The z-score is:
        (today_turnover − rolling_mean_{t-window..t-1}) / rolling_std

    Parameters
    ----------
    ohlcv : pd.DataFrame
        PIT-correct OHLCV from ``pit_loader.load()``.
    symbol : str
        NSE symbol.
    as_of_date : str
        Inference date.  Hold-out guard applied.
    window : int
        Rolling window in trading days (default 20).

    Returns
    -------
    pd.DataFrame with columns:
        symbol, business_date, data_available_at,
        turnover_lacs, turnover_z_score
    Empty DataFrame if insufficient history or symbol not found.
    """
    assert_no_holdout_access(as_of_date)

    if ohlcv is None or ohlcv.empty:
        return pd.DataFrame()

    if isinstance(ohlcv.index, pd.MultiIndex):
        try:
            sym_df = ohlcv.xs(symbol, level="symbol")[["turnover_lacs"]].copy()
        except KeyError:
            logger.debug("turnover_features: %s not in ohlcv index", symbol)
            return pd.DataFrame()
    else:
        mask = ohlcv["symbol"] == symbol
        if not mask.any():
            logger.debug("turnover_features: %s not in ohlcv", symbol)
            return pd.DataFrame()
        sym_df = ohlcv.loc[mask, ["turnover_lacs"]].copy()

    sym_df = sym_df.sort_index()

    if len(sym_df) < window + 1:
        return pd.DataFrame()

    # Shift by 1 so today's turnover is not in its own rolling window
    roll_mean = sym_df["turnover_lacs"].rolling(window).mean().shift(1)
    roll_std = sym_df["turnover_lacs"].rolling(window).std(ddof=1).shift(1).clip(lower=1e-6)
    z = (sym_df["turnover_lacs"] - roll_mean) / roll_std

    result = pd.DataFrame(
        {
            "symbol": symbol,
            "business_date": sym_df.index,
            "turnover_lacs": sym_df["turnover_lacs"].values,
            "turnover_z_score": z.values,
            "data_available_at": pd.to_datetime(sym_df.index),
        }
    )

    result = result.dropna(subset=["turnover_z_score"])
    _validate_output(result, "turnover_features")
    return result.reset_index(drop=True)


def build_features(
    symbol: str,
    business_date: str,
    ohlcv: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Build all available L2 features for a symbol at a business_date.

    Month 2: OHLCV-based features only (momentum, turnover).
    Month 3: earnings_surprise_features() added once filing_parser is live.

    Parameters
    ----------
    symbol : str
        NSE symbol.
    business_date : str
        Target date ("YYYY-MM-DD").  Hold-out guard applied.
    ohlcv : pd.DataFrame or None
        PIT-correct OHLCV from ``pit_loader.load()``.
        If None, returns empty DataFrame with a warning.

    Returns
    -------
    pd.DataFrame with one row for ``business_date``, all feature columns.
    Empty DataFrame if data is insufficient or not found.
    """
    assert_no_holdout_access(business_date)

    if ohlcv is None or ohlcv.empty:
        logger.warning("build_features: no ohlcv data for %s @ %s", symbol, business_date)
        return pd.DataFrame()

    mom = momentum_features(ohlcv, symbol, business_date)
    tur = turnover_features(ohlcv, symbol, business_date)

    if mom.empty or tur.empty:
        return pd.DataFrame()

    # Filter to the target date
    bd = pd.Timestamp(business_date).date()

    def _row_for_date(df: pd.DataFrame) -> pd.DataFrame:
        dates = pd.to_datetime(df["business_date"]).dt.date
        return df[dates == bd]

    mom_row = _row_for_date(mom)
    tur_row = _row_for_date(tur)

    if mom_row.empty or tur_row.empty:
        return pd.DataFrame()

    merged = mom_row.merge(
        tur_row.drop(columns=["data_available_at"], errors="ignore"),
        on=["symbol", "business_date"],
        how="inner",
    )
    return merged.reset_index(drop=True)
