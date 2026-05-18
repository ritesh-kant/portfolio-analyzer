"""data_loader — fetch and cache all historical data needed by the backtest.

All data is fetched once and written to parquet files under backtest/data/.
Subsequent runs read from cache unless --refresh is passed via the CLI.

Data sources:
    OHLCV (stocks)  : yfinance  <SYMBOL>.NS
    Nifty 50        : yfinance  ^NSEI
    India VIX       : yfinance  ^INDIAVIX
    FII flows       : NSE India daily CSV  (falls back to 0 on failure)
    Earnings dates  : NSE corporate actions API (falls back to {} on failure)

Cache layout:
    backtest/data/stocks/<SYMBOL>.parquet   — daily OHLCV per symbol
    backtest/data/market.parquet            — Nifty + VIX daily
    backtest/data/fii.parquet               — FII net crore daily
    backtest/data/earnings.json             — {symbol: [date_str, ...]}
"""

import asyncio
import json
import logging
import math
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# ── Cache paths ────────────────────────────────────────────────────────────────
_HERE = Path(__file__).parent
CACHE_DIR = _HERE / "data"
STOCKS_CACHE_DIR = CACHE_DIR / "stocks"
MARKET_CACHE = CACHE_DIR / "market.parquet"
FII_CACHE = CACHE_DIR / "fii.parquet"
EARNINGS_CACHE = CACHE_DIR / "earnings.json"

# ── yfinance symbols ───────────────────────────────────────────────────────────
_NIFTY_SYMBOL = "^NSEI"
_VIX_SYMBOL = "^INDIAVIX"

# ── NSE FII data URL ──────────────────────────────────────────────────────────
# NSE publishes a daily FII/FPI activity CSV; we read the "Net Purchase/Sales"
# column which is equivalent to fii_net_crore in the production system.
_NSE_FII_URL = (
    "https://www.nseindia.com/api/fiidiiTradeReact"
    "?type=stock&category=fii&fromDate={from_date}&toDate={to_date}"
)
_NSE_FII_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; backtest/1.0)",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com/",
}

# Earnings calendar via NSE corporate actions
_NSE_CORP_ACTIONS_URL = (
    "https://www.nseindia.com/api/corporateActions"
    "?index=equities&symbol={symbol}&from_date={from_date}&to_date={to_date}"
    "&csv=true"
)


# ── Utilities ─────────────────────────────────────────────────────────────────

def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return default


def _ensure_dirs() -> None:
    STOCKS_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_covers(df: pd.DataFrame, need_start: str, need_end: str) -> bool:
    """Return True if df's DatetimeIndex spans the full [need_start, need_end] range.

    Allows 5 calendar days of slack at the end to account for weekends/holidays.
    Used to decide whether a parquet cache is fresh enough or must be re-fetched.
    """
    if df is None or df.empty:
        return False
    s = pd.Timestamp(need_start)
    e = pd.Timestamp(need_end)
    return df.index.min() <= s and df.index.max() >= (e - pd.Timedelta(days=5))


def _date_range_str(start: str, end: str) -> str:
    """Format dates as dd-mm-yyyy for NSE APIs."""
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    return s.strftime("%d-%m-%Y"), e.strftime("%d-%m-%Y")


# ── Stock OHLCV ───────────────────────────────────────────────────────────────

def load_stock_ohlcv(
    symbol: str,
    start: str,
    end: str,
    *,
    refresh: bool = False,
) -> pd.DataFrame:
    """Return daily OHLCV for a single NSE symbol.

    Fetches via yfinance on first call, then reads from parquet cache.
    Columns: date (index), open, high, low, close, volume  — all lowercase.

    Args:
        symbol:  yfinance symbol, e.g. "RELIANCE.NS"
        start:   ISO date string "YYYY-MM-DD" (inclusive)
        end:     ISO date string "YYYY-MM-DD" (inclusive)
        refresh: Force re-download even if cache exists.
    """
    _ensure_dirs()
    # We cache the full history; on range queries we filter afterwards.
    cache_path = STOCKS_CACHE_DIR / f"{symbol.replace('/', '_')}.parquet"

    # The cache must span (start − 120 days) → end so that indicators have their
    # full lookback on the first day of the requested window.
    need_start = (date.fromisoformat(start) - timedelta(days=120)).isoformat()

    if cache_path.exists() and not refresh:
        df_cached = pd.read_parquet(cache_path)
        if _cache_covers(df_cached, need_start, end):
            return df_cached.loc[start:end].copy()
        # Cache exists but is stale — log and fall through to re-fetch
        logger.info(
            "data_loader stock cache stale symbol=%s "
            "(covers %s→%s, need %s→%s) — re-fetching",
            symbol,
            df_cached.index.min().date() if not df_cached.empty else "empty",
            df_cached.index.max().date() if not df_cached.empty else "empty",
            need_start, end,
        )

    logger.info("data_loader fetching OHLCV symbol=%s start=%s end=%s", symbol, start, end)
    # Fetch a generous window — extra history costs nothing and avoids re-fetching
    fetch_start = (date.fromisoformat(start) - timedelta(days=120)).isoformat()
    raw = yf.download(
        symbol,
        start=fetch_start,
        end=(date.fromisoformat(end) + timedelta(days=1)).isoformat(),
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)

    if raw.empty:
        logger.warning("data_loader no_data symbol=%s", symbol)
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    # Normalise column names to lowercase
    raw.columns = [c.lower() for c in raw.columns]
    raw.index.name = "date"
    raw.index = pd.to_datetime(raw.index).normalize()
    raw.to_parquet(cache_path)

    return raw.loc[start:end].copy()


async def load_stock_ohlcv_batch(
    symbols: list[str],
    start: str,
    end: str,
    *,
    refresh: bool = False,
    max_concurrent: int = 10,
) -> dict[str, pd.DataFrame]:
    """Fetch OHLCV for a list of symbols concurrently.

    Returns {symbol: DataFrame}. Symbols that fail are omitted.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _fetch_one(sym: str) -> tuple[str, pd.DataFrame | None]:
        async with semaphore:
            try:
                df = await asyncio.to_thread(load_stock_ohlcv, sym, start, end, refresh=refresh)
                return sym, df
            except Exception as exc:
                logger.warning("data_loader stock_fetch_failed symbol=%s error=%s", sym, exc)
                return sym, None

    results = await asyncio.gather(*[_fetch_one(s) for s in symbols])
    return {sym: df for sym, df in results if df is not None and not df.empty}


# ── Market data (Nifty + VIX) ────────────────────────────────────────────────

def load_market_data(
    start: str,
    end: str,
    *,
    refresh: bool = False,
) -> pd.DataFrame:
    """Return daily Nifty 50 + India VIX data.

    Columns: date (index), nifty_close, nifty_change_pct, nifty_above_ema50,
             nifty_5d_return, nifty_30d_return, vix, vix_caution.
    """
    _ensure_dirs()

    # Cache must cover (start − 120d) → end for EMA50 lookback on first fold day.
    need_start = (date.fromisoformat(start) - timedelta(days=120)).isoformat()

    if MARKET_CACHE.exists() and not refresh:
        df_cached = pd.read_parquet(MARKET_CACHE)
        if _cache_covers(df_cached, need_start, end):
            return df_cached.loc[start:end].copy()
        logger.info(
            "data_loader market cache stale (covers %s→%s, need %s→%s) — re-fetching",
            df_cached.index.min().date() if not df_cached.empty else "empty",
            df_cached.index.max().date() if not df_cached.empty else "empty",
            need_start, end,
        )

    logger.info("data_loader fetching market data start=%s end=%s", start, end)
    fetch_start = (date.fromisoformat(start) - timedelta(days=120)).isoformat()
    fetch_end = (date.fromisoformat(end) + timedelta(days=1)).isoformat()

    nifty = yf.download(
        _NIFTY_SYMBOL,
        start=fetch_start,
        end=fetch_end,
        auto_adjust=True,
        progress=False,
    )
    vix = yf.download(
        _VIX_SYMBOL,
        start=fetch_start,
        end=fetch_end,
        auto_adjust=True,
        progress=False,
    )

    # Handle MultiIndex
    for raw in (nifty, vix):
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

    if nifty.empty:
        logger.error("data_loader nifty data empty")
        return pd.DataFrame()

    nifty.index = pd.to_datetime(nifty.index).normalize()
    nifty.index.name = "date"
    nifty.columns = [c.lower() for c in nifty.columns]

    df = pd.DataFrame(index=nifty.index)
    df["nifty_close"] = nifty["close"]

    # Daily % change
    df["nifty_change_pct"] = df["nifty_close"].pct_change() * 100

    # EMA50 + EMA200 on Nifty
    import pandas_ta as ta  # noqa: PLC0415
    nifty_ta = nifty.copy()
    nifty_ta.ta.ema(length=50, append=True)
    nifty_ta.ta.ema(length=200, append=True)
    df["nifty_ema50"] = nifty_ta.get("EMA_50")
    df["nifty_ema200"] = nifty_ta.get("EMA_200")
    df["nifty_above_ema50"] = df["nifty_close"] > df["nifty_ema50"]
    df["nifty_above_ema200"] = df["nifty_close"] > df["nifty_ema200"]

    # Rolling returns
    df["nifty_5d_return"] = df["nifty_close"].pct_change(periods=5) * 100
    df["nifty_30d_return"] = df["nifty_close"].pct_change(periods=30) * 100

    # Realised volatility (annualised) for chop-regime detection
    _daily_ret = df["nifty_close"].pct_change()
    df["nifty_20d_vol"] = _daily_ret.rolling(20).std() * (252 ** 0.5) * 100   # %
    df["nifty_60d_vol"] = _daily_ret.rolling(60).std() * (252 ** 0.5) * 100   # %

    # VIX
    if not vix.empty:
        vix.index = pd.to_datetime(vix.index).normalize()
        vix.index.name = "date"
        vix.columns = [c.lower() for c in vix.columns]
        df["vix"] = vix["close"].reindex(df.index)
    else:
        logger.warning("data_loader vix data empty — using NaN")
        df["vix"] = float("nan")

    df["vix_caution"] = (df["vix"] > 18.0) & (df["vix"] <= 22.0)

    df.to_parquet(MARKET_CACHE)
    return df.loc[start:end].copy()


# ── FII flows ────────────────────────────────────────────────────────────────

def load_fii_data(
    start: str,
    end: str,
    *,
    refresh: bool = False,
) -> pd.DataFrame:
    """Return daily FII net crore data.

    Columns: date (index), fii_net_crore.

    Falls back to 0 on fetch failure so the backtest treats missing FII data
    as neutral (same behaviour as production signal_agent when FII unavailable).
    """
    _ensure_dirs()

    if FII_CACHE.exists() and not refresh:
        df = pd.read_parquet(FII_CACHE)
        return df.loc[start:end].copy()

    logger.info("data_loader fetching FII data start=%s end=%s", start, end)

    # NSE's FII endpoint requires a session cookie; we attempt a simple fetch.
    # On failure we return a zero-filled frame so backtest proceeds (neutral, not bullish).
    try:
        from_d, to_d = _date_range_str(start, end)
        url = _NSE_FII_URL.format(from_date=from_d, to_date=to_d)

        with httpx.Client(headers=_NSE_FII_HEADERS, timeout=30.0, follow_redirects=True) as client:
            # Seed a session cookie by hitting the homepage first
            client.get("https://www.nseindia.com/", timeout=15.0)
            resp = client.get(url, timeout=30.0)
            resp.raise_for_status()
            payload = resp.json()

        # NSE API returns either {"data": [...]} or a bare list
        raw_entries = payload if isinstance(payload, list) else payload.get("data", [])
        rows = []
        for entry in raw_entries:
            try:
                trade_date = pd.to_datetime(entry.get("date"), dayfirst=True).normalize()
                net = _safe_float(entry.get("NET_AMT") or entry.get("netAmt"), 0.0)
                rows.append({"date": trade_date, "fii_net_crore": net})
            except Exception:
                continue

        if rows:
            df = pd.DataFrame(rows).set_index("date").sort_index()
            df.to_parquet(FII_CACHE)
            return df.loc[start:end].copy()

    except Exception as exc:
        logger.warning("data_loader fii_fetch_failed error=%s — using neutral zeros", exc)

    # Fallback: zero-filled trading day index aligned to market data
    mkt = load_market_data(start, end)
    df = pd.DataFrame({"fii_net_crore": 0.0}, index=mkt.index)
    return df


# ── Earnings calendar ────────────────────────────────────────────────────────

def load_earnings_calendar(
    symbols: list[str],
    start: str,
    end: str,
    *,
    refresh: bool = False,
) -> dict[str, list[str]]:
    """Return {symbol: [date_str, ...]} for earnings/result announcement dates.

    Used by the backtest guard to replicate guard_agent's earnings kill-switch.
    Falls back to {} on failure (guard assumes no upcoming earnings — optimistic,
    but noted as a backtest limitation in the report).
    """
    _ensure_dirs()

    if EARNINGS_CACHE.exists() and not refresh:
        with open(EARNINGS_CACHE) as f:
            cached: dict[str, list[str]] = json.load(f)
        # Return only requested symbols; add missing ones if cache predates them
        missing = [s for s in symbols if s not in cached]
        if not missing:
            return {s: cached.get(s, []) for s in symbols}
        # Fall through to fetch the missing ones and merge
        existing = cached
    else:
        existing = {}

    fetch_syms = [s for s in symbols if s not in existing]
    logger.info("data_loader fetching earnings calendar symbols=%d", len(fetch_syms))

    from_d, to_d = _date_range_str(start, end)
    results: dict[str, list[str]] = dict(existing)

    # NSE earnings come from corporate actions; we filter for "Board Meeting" / "Quarterly Results"
    with httpx.Client(headers=_NSE_FII_HEADERS, timeout=30.0, follow_redirects=True) as client:
        try:
            client.get("https://www.nseindia.com/", timeout=15.0)
        except Exception:
            pass

        for sym in fetch_syms:
            nse_sym = sym.replace(".NS", "").replace(".BO", "")
            try:
                url = _NSE_CORP_ACTIONS_URL.format(
                    symbol=nse_sym, from_date=from_d, to_date=to_d
                )
                resp = client.get(url, timeout=20.0)
                resp.raise_for_status()
                data = resp.json()
                dates: list[str] = []
                for item in data if isinstance(data, list) else []:
                    subject = str(item.get("subject") or item.get("purpose") or "").lower()
                    if any(k in subject for k in ("quarterly results", "board meeting", "financial results")):
                        ex_date = item.get("exDate") or item.get("recordDate") or item.get("bcStartDate")
                        if ex_date:
                            try:
                                dates.append(
                                    pd.to_datetime(ex_date, dayfirst=True).date().isoformat()
                                )
                            except Exception:
                                pass
                results[sym] = sorted(set(dates))
            except Exception as exc:
                logger.warning(
                    "data_loader earnings_fetch_failed symbol=%s error=%s — using []", sym, exc
                )
                results[sym] = []

    # Persist merged cache
    with open(EARNINGS_CACHE, "w") as f:
        json.dump(results, f, indent=2)

    return {s: results.get(s, []) for s in symbols}


# ── Convenience: load everything for a backtest run ──────────────────────────

async def load_all(
    symbols: list[str],
    start: str,
    end: str,
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    """Load all data sources in parallel and return a single context dict.

    Returns:
        {
            "stocks":   {symbol: DataFrame},   # OHLCV
            "market":   DataFrame,             # Nifty + VIX daily
            "fii":      DataFrame,             # fii_net_crore daily
            "earnings": {symbol: [date_str]},  # earnings announcement dates
        }
    """
    stocks_task = load_stock_ohlcv_batch(symbols, start, end, refresh=refresh)
    market_task = asyncio.to_thread(load_market_data, start, end, refresh=refresh)
    fii_task = asyncio.to_thread(load_fii_data, start, end, refresh=refresh)

    stocks, market, fii = await asyncio.gather(stocks_task, market_task, fii_task)

    # Earnings is slow (one HTTP call per symbol) — only fetch if not cached or refresh
    earnings = await asyncio.to_thread(
        load_earnings_calendar, symbols, start, end, refresh=refresh
    )

    logger.info(
        "data_loader load_all complete stocks=%d market_rows=%d fii_rows=%d earnings_symbols=%d",
        len(stocks), len(market), len(fii), len(earnings),
    )
    return {"stocks": stocks, "market": market, "fii": fii, "earnings": earnings}
