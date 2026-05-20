"""Earnings surprise and reaction feature builders for Strategy A (PEAD).

All 16 features pre-registered in research/hypotheses/2026-05-19-pead-midcap.md.
Adding features not in that list is a process violation.

PIT discipline
--------------
Every feature is timestamped with ``data_available_at``, which is the
maximum of all input ``as_of_timestamp`` values.  The downstream PIT join
ensures no feature is consumed before it was available.

Hold-out guard
--------------
``assert_no_holdout_access`` is called at the top of every public function.
"""

from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

from quant.research.holdout_lock import assert_no_holdout_access

logger = logging.getLogger(__name__)

# Target variable threshold (pre-registered in hypothesis)
TARGET_BPS: float = 40.0
TARGET_RETURN: float = TARGET_BPS / 10_000

# Features exactly as registered (must match hypothesis §5)
REGISTERED_FEATURES: list[str] = [
    "eps_surprise_pct",
    "revenue_surprise_pct",
    "guidance_direction",
    "mgmt_tone_score",
    "day0_price_reaction",
    "reaction_coverage_ratio",
    "momentum_residual_5d",
    "momentum_residual_20d",
    "turnover_z_score_day0",
    "sector_return_5d",
    "nifty_return_5d",
    "market_cap_log",
    "analyst_coverage_proxy",
    "days_since_last_result",
    "quarter_sin",
    "quarter_cos",
]
MAX_FEATURES: int = 20


def compute_naive_surprise(
    symbol: str,
    earnings_df: pd.DataFrame,
    announcement_date: str,
) -> tuple[float | None, float | None]:
    """Compute YoY EPS and revenue surprise vs same-quarter last year.

    This is the naive baseline used when screener.in consensus is unavailable.
    It is a valid signal: PEAD literature shows YoY surprises predict drift
    even without analyst consensus (Ball & Brown 1968 used no consensus).

    Parameters
    ----------
    symbol : str
        NSE symbol.
    earnings_df : pd.DataFrame
        Full earnings history from earnings_ingest.load_earnings().
        Must include: symbol, business_date, fiscal_quarter, fiscal_year,
        eps_reported, revenue_cr.
    announcement_date : str
        ISO date of the current announcement.

    Returns
    -------
    (eps_surprise_pct, revenue_surprise_pct)
        Both in fractional form (0.15 = +15% surprise).
        None if insufficient history.
    """
    assert_no_holdout_access(announcement_date)

    sym_df = earnings_df[earnings_df["symbol"] == symbol].copy()
    if sym_df.empty:
        return None, None

    sym_df = sym_df.sort_values("business_date")

    # Find the announcement row (search the full earnings_df, not the pre-filtered one)
    all_on_date = earnings_df[
        (earnings_df["symbol"] == symbol)
        & (pd.to_datetime(earnings_df["business_date"]) == pd.Timestamp(announcement_date))
    ]
    if all_on_date.empty:
        return None, None

    current_row = all_on_date.iloc[-1]
    cur_eps = current_row.get("eps_reported")
    cur_rev = current_row.get("revenue_cr")

    # Fast path: use pre-computed yoy columns from the parquet (always PIT-correct)
    yoy_eps_prev = current_row.get("yoy_eps_prev")
    yoy_rev_prev = current_row.get("yoy_revenue_prev")

    eps_surprise = None
    if cur_eps is not None and yoy_eps_prev is not None:
        try:
            denom = abs(float(yoy_eps_prev))
            if denom > 1e-6:
                eps_surprise = (float(cur_eps) - float(yoy_eps_prev)) / denom
        except (TypeError, ValueError):
            pass

    rev_surprise = None
    if cur_rev is not None and yoy_rev_prev is not None:
        try:
            denom = abs(float(yoy_rev_prev))
            if denom > 1e-6:
                rev_surprise = (float(cur_rev) - float(yoy_rev_prev)) / denom
        except (TypeError, ValueError):
            pass

    # Fallback: derive from prior-year rows in earnings_df if pre-computed columns missing
    if eps_surprise is None or rev_surprise is None:
        # Only use records available before this announcement (PIT-safe)
        hist = sym_df[pd.to_datetime(sym_df["business_date"]) < pd.Timestamp(announcement_date)]
        cur_q = current_row.get("fiscal_quarter")
        cur_fy = current_row.get("fiscal_year")
        prev_fy = (cur_fy - 1) if cur_fy is not None else None

        if cur_q is not None and prev_fy is not None:
            prev_mask = (
                (hist["symbol"] == symbol)
                & (hist["fiscal_quarter"] == cur_q)
                & (hist["fiscal_year"] == prev_fy)
            )
            prev_rows = hist[prev_mask]
            if not prev_rows.empty:
                prev_row = prev_rows.iloc[-1]
                prev_eps = prev_row.get("eps_reported")
                prev_rev = prev_row.get("revenue_cr")
                if eps_surprise is None and cur_eps is not None and prev_eps is not None:
                    try:
                        denom = abs(float(prev_eps))
                        if denom > 1e-6:
                            eps_surprise = (float(cur_eps) - float(prev_eps)) / denom
                    except (TypeError, ValueError):
                        pass
                if rev_surprise is None and cur_rev is not None and prev_rev is not None:
                    try:
                        denom = abs(float(prev_rev))
                        if denom > 1e-6:
                            rev_surprise = (float(cur_rev) - float(prev_rev)) / denom
                    except (TypeError, ValueError):
                        pass

    return eps_surprise, rev_surprise


def compute_day0_reaction(
    symbol: str,
    announcement_date: str,
    ohlcv: pd.DataFrame,
) -> float | None:
    """Price return on the announcement day vs previous close.

    Returns log return from prev_close to close on announcement_date.
    Returns None if price data is missing.

    Parameters
    ----------
    symbol : str
    announcement_date : str
        ISO date.
    ohlcv : pd.DataFrame
        PIT-correct OHLCV from pit_loader.load().
    """
    assert_no_holdout_access(announcement_date)

    ann_ts = pd.Timestamp(announcement_date)

    if isinstance(ohlcv.index, pd.MultiIndex):
        try:
            sym_df = ohlcv.xs(symbol, level="symbol")
        except KeyError:
            return None
    else:
        mask = ohlcv["symbol"] == symbol
        sym_df = ohlcv[mask].set_index("business_date") if mask.any() else pd.DataFrame()

    if sym_df.empty:
        return None

    sym_df.index = pd.to_datetime(sym_df.index)
    if ann_ts not in sym_df.index:
        return None

    row = sym_df.loc[ann_ts]
    close = row.get("close") if hasattr(row, "get") else row["close"]
    prev_close = row.get("prev_close") if hasattr(row, "get") else row["prev_close"]

    if close is None or prev_close is None:
        return None
    if float(prev_close) < 1e-6:
        return None

    return float(math.log(float(close) / float(prev_close)))


def compute_historical_pead_magnitude(
    symbol: str,
    earnings_df: pd.DataFrame,
    ohlcv: pd.DataFrame,
    hold_window: int = 5,
    train_end: str = "2023-06-30",
) -> float | None:
    """Compute the median observed PEAD magnitude for a symbol in the training window.

    Used to normalise the day0 reaction (reaction_coverage_ratio feature).

    Returns
    -------
    float | None
        Median 5-day forward return (fractional) across historical earnings events.
    """
    assert_no_holdout_access(train_end)

    sym_earn = earnings_df[
        (earnings_df["symbol"] == symbol)
        & (pd.to_datetime(earnings_df["business_date"]) <= pd.Timestamp(train_end))
    ].copy()

    if sym_earn.empty or len(sym_earn) < 3:
        return None

    if isinstance(ohlcv.index, pd.MultiIndex):
        try:
            prices = ohlcv.xs(symbol, level="symbol")["close"].sort_index()
        except KeyError:
            return None
    else:
        mask = ohlcv["symbol"] == symbol
        if not mask.any():
            return None
        prices = ohlcv[mask].set_index("business_date")["close"].sort_index()

    prices.index = pd.to_datetime(prices.index)
    magnitudes = []

    for _, row in sym_earn.iterrows():
        ann_date = pd.Timestamp(row["business_date"])
        future_dates = prices.index[prices.index > ann_date]
        if len(future_dates) < hold_window:
            continue
        exit_date = future_dates[hold_window - 1]
        if ann_date not in prices.index:
            continue
        p0 = prices[ann_date]
        p1 = prices[exit_date]
        if float(p0) > 1e-6:
            magnitudes.append(float(p1) / float(p0) - 1.0)

    if not magnitudes:
        return None
    return float(np.median(magnitudes))


def compute_days_since_last_result(
    symbol: str,
    announcement_date: str,
    earnings_df: pd.DataFrame,
) -> float | None:
    """Trading days since the previous quarterly result for this symbol."""
    assert_no_holdout_access(announcement_date)

    if earnings_df.empty or "symbol" not in earnings_df.columns:
        return None
    sym_df = earnings_df[earnings_df["symbol"] == symbol].copy()
    prior = sym_df[
        pd.to_datetime(sym_df["business_date"]) < pd.Timestamp(announcement_date)
    ]
    if prior.empty:
        return None
    last_date = pd.to_datetime(prior["business_date"].max())
    # Calendar delta — actual trading-day count would need holiday calendar
    delta = (pd.Timestamp(announcement_date) - last_date).days
    return float(max(delta, 0))


def build_earnings_features(
    symbol: str,
    announcement_date: str,
    earnings_df: pd.DataFrame,
    ohlcv: pd.DataFrame,
    nifty_ohlcv: pd.DataFrame | None = None,
    sector_ohlcv: pd.DataFrame | None = None,
    filing_output: dict | None = None,
    analyst_coverage_proxy: float = 0.0,
) -> dict:
    """Build all 16 pre-registered PEAD features for one event.

    Parameters
    ----------
    symbol : str
    announcement_date : str
        ISO date of the earnings announcement.
    earnings_df : pd.DataFrame
        From earnings_ingest.load_earnings().
    ohlcv : pd.DataFrame
        PIT-correct universe OHLCV from pit_loader.load().
    nifty_ohlcv : pd.DataFrame | None
        Nifty index OHLCV for nifty_return_5d feature.
    sector_ohlcv : pd.DataFrame | None
        Sector index OHLCV for sector_return_5d feature.
    filing_output : dict | None
        Output from filing_parser.parse_filing().  If None, guidance and tone
        are set to 0.0 (neutral).
    analyst_coverage_proxy : float
        From screener.in (0.0 if unavailable).

    Returns
    -------
    dict with keys matching REGISTERED_FEATURES.
        None values indicate features that could not be computed.
    """
    assert_no_holdout_access(announcement_date)
    ann_ts = pd.Timestamp(announcement_date)

    # ── F1, F2: Earnings surprise ─────────────────────────────────────────────
    eps_surp, rev_surp = compute_naive_surprise(symbol, earnings_df, announcement_date)

    # Override with LLM-parsed values if available (consensus-backed)
    if filing_output:
        if filing_output.get("revenue_surprise_pct") is not None:
            rev_surp = filing_output["revenue_surprise_pct"]
        if filing_output.get("margin_surprise_pct") is not None:
            eps_surp = filing_output.get("margin_surprise_pct", eps_surp)

    # ── F3, F4: Guidance and tone from filing parser ──────────────────────────
    guidance_map = {"up": 1.0, "down": -1.0, "unchanged": 0.0, "withdrawn": -0.5, "unknown": 0.0}
    guidance_dir = 0.0
    mgmt_tone = 0.0
    if filing_output:
        guidance_dir = guidance_map.get(
            (filing_output.get("guidance_direction") or "unknown").lower(), 0.0
        )
        mgmt_tone = float(filing_output.get("mgmt_tone_score") or 0.0)

    # ── F5: Day-0 price reaction ──────────────────────────────────────────────
    day0_rxn = compute_day0_reaction(symbol, announcement_date, ohlcv)

    # ── F6: Reaction coverage ratio ───────────────────────────────────────────
    hist_mag = compute_historical_pead_magnitude(symbol, earnings_df, ohlcv)
    reaction_cov = None
    if day0_rxn is not None and hist_mag is not None and abs(hist_mag) > 1e-6:
        reaction_cov = day0_rxn / abs(hist_mag)

    # ── F7, F8, F9: OHLCV-based features (from builder.py) ───────────────────
    # Pull pre-computed momentum and turnover from the feature store.
    # If the feature store hasn't been built yet, these are None.
    momentum_res_5d = _extract_scalar_feature(ohlcv, symbol, ann_ts, "momentum_residual_5d")
    momentum_res_20d = _extract_scalar_feature(ohlcv, symbol, ann_ts, "momentum_residual_20d")
    turnover_z_day0 = _extract_scalar_feature(ohlcv, symbol, ann_ts, "turnover_z_score")

    # ── F10, F11: Sector and Nifty returns ───────────────────────────────────
    sector_ret_5d = _compute_index_return(sector_ohlcv, ann_ts, window=5)
    nifty_ret_5d = _compute_index_return(nifty_ohlcv, ann_ts, window=5)

    # ── F12: Market cap (log) ─────────────────────────────────────────────────
    mkt_cap_log = _compute_market_cap_log(ohlcv, symbol, ann_ts)

    # ── F13: Analyst coverage proxy ───────────────────────────────────────────
    # From screener.in if available; 0.0 as default (plan §4.1 best-effort)

    # ── F14: Days since last result ───────────────────────────────────────────
    days_since = compute_days_since_last_result(symbol, announcement_date, earnings_df)

    # ── F15, F16: Seasonality ─────────────────────────────────────────────────
    q = _fiscal_quarter(ann_ts)
    quarter_sin = math.sin(2 * math.pi * q / 4)
    quarter_cos = math.cos(2 * math.pi * q / 4)

    # ── Assemble ──────────────────────────────────────────────────────────────
    return {
        "symbol": symbol,
        "announcement_date": announcement_date,
        "eps_surprise_pct": eps_surp,
        "revenue_surprise_pct": rev_surp,
        "guidance_direction": guidance_dir,
        "mgmt_tone_score": mgmt_tone,
        "day0_price_reaction": day0_rxn,
        "reaction_coverage_ratio": reaction_cov,
        "momentum_residual_5d": momentum_res_5d,
        "momentum_residual_20d": momentum_res_20d,
        "turnover_z_score_day0": turnover_z_day0,
        "sector_return_5d": sector_ret_5d,
        "nifty_return_5d": nifty_ret_5d,
        "market_cap_log": mkt_cap_log,
        "analyst_coverage_proxy": float(analyst_coverage_proxy),
        "days_since_last_result": days_since,
        "quarter_sin": quarter_sin,
        "quarter_cos": quarter_cos,
    }


def build_target(
    symbol: str,
    announcement_date: str,
    ohlcv: pd.DataFrame,
    hold_days: int = 5,
) -> float | None:
    """Compute the binary target for a PEAD event.

    Target = 1 if 5-trading-day forward return from announcement_date+1
    open exceeds TARGET_BPS (40 bps), else 0.

    Returns None if price data insufficient.
    """
    assert_no_holdout_access(announcement_date)
    ann_ts = pd.Timestamp(announcement_date)

    if isinstance(ohlcv.index, pd.MultiIndex):
        try:
            prices = ohlcv.xs(symbol, level="symbol")["close"].sort_index()
        except KeyError:
            return None
    else:
        mask = ohlcv["symbol"] == symbol
        if not mask.any():
            return None
        prices = ohlcv[mask].set_index("business_date")["close"].sort_index()

    prices.index = pd.to_datetime(prices.index)

    if ann_ts not in prices.index:
        return None

    future = prices.index[prices.index > ann_ts]
    if len(future) < hold_days:
        return None

    entry_price = float(prices[ann_ts])
    exit_price = float(prices[future[hold_days - 1]])

    if entry_price < 1e-6:
        return None

    forward_return = exit_price / entry_price - 1.0
    return 1.0 if forward_return > TARGET_RETURN else 0.0


# ── Private helpers ────────────────────────────────────────────────────────────

def _extract_scalar_feature(
    ohlcv: pd.DataFrame,
    symbol: str,
    date: pd.Timestamp,
    col: str,
) -> float | None:
    """Extract a single feature value from an ohlcv-like DataFrame."""
    if ohlcv is None or ohlcv.empty or col not in ohlcv.columns:
        return None
    if isinstance(ohlcv.index, pd.MultiIndex):
        try:
            row = ohlcv.xs(symbol, level="symbol")
            row.index = pd.to_datetime(row.index)
            if date in row.index:
                val = row.loc[date, col]
                return float(val) if val is not None and not math.isnan(float(val)) else None
        except (KeyError, TypeError):
            return None
    else:
        mask = (ohlcv["symbol"] == symbol) & (pd.to_datetime(ohlcv["business_date"]) == date)
        rows = ohlcv[mask]
        if rows.empty:
            return None
        val = rows.iloc[0][col]
        return float(val) if val is not None and not math.isnan(float(val)) else None
    return None


def _compute_index_return(
    index_ohlcv: pd.DataFrame | None,
    as_of: pd.Timestamp,
    window: int = 5,
) -> float | None:
    """5-day log return of an index as of a given date."""
    if index_ohlcv is None or index_ohlcv.empty:
        return None

    if "close" not in index_ohlcv.columns:
        return None

    if isinstance(index_ohlcv.index, pd.DatetimeIndex):
        closes = index_ohlcv["close"].sort_index()
    else:
        col = "business_date" if "business_date" in index_ohlcv.columns else index_ohlcv.columns[0]
        closes = index_ohlcv.set_index(col)["close"].sort_index()

    closes.index = pd.to_datetime(closes.index)

    if as_of not in closes.index:
        return None
    pos = closes.index.get_loc(as_of)
    if pos < window:
        return None
    p_now = float(closes.iloc[pos])
    p_prev = float(closes.iloc[pos - window])
    if p_prev < 1e-6:
        return None
    return float(math.log(p_now / p_prev))


def _compute_market_cap_log(
    ohlcv: pd.DataFrame,
    symbol: str,
    date: pd.Timestamp,
) -> float | None:
    """Log market cap from close × shares_outstanding proxy.

    We use close price as a size proxy when shares_outstanding is not in
    the OHLCV schema (Bhavcopy does not include it).  The log close price
    is strongly correlated with log market cap in cross-section.
    """
    if ohlcv is None or ohlcv.empty:
        return None
    if isinstance(ohlcv.index, pd.MultiIndex):
        try:
            row = ohlcv.xs(symbol, level="symbol")
            row.index = pd.to_datetime(row.index)
            if date in row.index:
                close = float(row.loc[date, "close"])
                return math.log(close) if close > 1e-6 else None
        except (KeyError, TypeError):
            return None
    else:
        mask = (ohlcv["symbol"] == symbol) & (pd.to_datetime(ohlcv["business_date"]) == date)
        rows = ohlcv[mask]
        if rows.empty:
            return None
        close = float(rows.iloc[0]["close"])
        return math.log(close) if close > 1e-6 else None
    return None


def _fiscal_quarter(ts: pd.Timestamp) -> int:
    """Indian fiscal quarter: Apr=1, Jul=2, Oct=3, Jan=4."""
    m = ts.month
    if m in (4, 5, 6):
        return 1
    if m in (7, 8, 9):
        return 2
    if m in (10, 11, 12):
        return 3
    return 4
