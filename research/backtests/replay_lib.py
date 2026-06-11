"""Shared signal-level replay engine for BT7/BT8 (registered 2026-06-11).

Replays untraded nt_signals against real 5-min bars (Yahoo Finance) using the
PRODUCTION exit policy and cost model imported from
apps/signal-engine/src/news_trader/trailing_sl.py — not a copy.

Mechanics (locked in the hypothesis files before any run):
  - entry: first 5-min bar opening at/after max(signal_time + 15 min, 09:30 IST),
    same trading day only, no later than 14:30 IST; entry at bar OPEN
  - exits, priority order as deployed: sl_hit (3% initial, trail never activates
    because TP 1% < trail_activate 2%) -> target_hit (1%) -> time_stop (90 min)
    -> eod_close (15:15 IST)
  - intra-bar ambiguity resolved CONSERVATIVELY: if a bar spans both SL and TP,
    the SL is assumed to fill first
  - dedupe: one open simulated position per symbol; additionally a same
    symbol+direction signal within 90 min of an accepted one is skipped
    (mirrors the live entity-window merge)
  - position size ₹10,000 (capital 100k / 10 slots), qty = calc_qty
  - costs: production intraday MIS model; cost-stress = slippage doubled
    (extra 5 bps/side added on top of the modelled 5 bps)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "apps" / "signal-engine" / "src"))

from news_trader.nifty50 import NIFTY_50  # noqa: E402
from news_trader.nifty500 import NIFTY_500  # noqa: E402
from news_trader.trailing_sl import calc_pnl, calc_qty  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
CACHE_DIR = Path(__file__).resolve().parent / ".cache"
CACHE_DIR.mkdir(exist_ok=True)

POSITION_SIZE_INR = 10_000.0
DELAY_MIN = 15
TP_PCT = 0.01
SL_PCT = 0.03
TIME_STOP_MIN = 90
ENTRY_EARLIEST = (9, 30)
ENTRY_CUTOFF = (14, 30)
EOD_CLOSE = (15, 15)
EXTRA_SLIPPAGE_RATE = 0.0005  # cost-stress: +5 bps/side on top of modelled 5 bps


def load_signals(filt: dict) -> list[dict]:
    """Pull signals straight from MongoDB using MONGODB_URI in the repo .env."""
    from pymongo import MongoClient

    uri = None
    for line in (_REPO / ".env").read_text().splitlines():
        if line.startswith("MONGODB_URI="):
            uri = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not uri:
        raise RuntimeError("MONGODB_URI not found in repo .env")
    client = MongoClient(uri)
    db = client.get_default_database()
    docs = list(db.nt_signals.find(filt).sort("created_at", 1))
    client.close()
    return docs


def fetch_bars(symbol: str, start: str, end: str) -> pd.DataFrame | None:
    """5-min bars for an NSE symbol, IST-indexed, disk-cached."""
    cache = CACHE_DIR / f"{symbol.replace('&', '_')}_{start}_{end}.csv"
    if cache.exists():
        df = pd.read_csv(cache, index_col=0, parse_dates=True)
        if df.empty:
            return None
        df.index = pd.to_datetime(df.index, utc=True).tz_convert(IST)
        return df
    import yfinance as yf

    try:
        df = yf.download(
            f"{symbol}.NS", start=start, end=end, interval="5m",
            progress=False, auto_adjust=False, multi_level_index=False,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] fetch failed {symbol}: {exc}", file=sys.stderr)
        df = None
    if df is None or df.empty:
        pd.DataFrame().to_csv(cache)
        return None
    df = df[["Open", "High", "Low", "Close"]].copy()
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(IST)
    df.to_csv(cache)
    return df


@dataclass
class Trade:
    signal_id: str
    symbol: str
    direction: str  # "long" | "short"
    signal_at: datetime
    entry_at: datetime
    exit_at: datetime
    entry: float
    exit: float
    exit_reason: str
    qty: int
    gross: float
    net: float
    cost: float
    net_stress: float  # net under 2x slippage


def _ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def simulate(
    signals: list[dict],
    symbols_for: callable,
    direction_for: callable,
    bars_start: str,
    bars_end: str,
    entry_mode: str = "market",
) -> tuple[list[Trade], dict]:
    """Replay signals chronologically. symbols_for(sig) -> eligible symbols;
    direction_for(sig) -> 'long'|'short'.

    entry_mode='limit': rest a limit at the entry bar's open for 15 min
    (entry bar + 2 more); fill requires bar LOW strictly below the limit
    (long) / HIGH strictly above (short); fill price = limit; entry-leg
    slippage removed. Unfilled -> signal skipped (counted in drops)."""
    open_until: dict[str, datetime] = {}
    last_accept: dict[tuple[str, str], datetime] = {}
    trades: list[Trade] = []
    drops = {
        "no_bars": 0, "outside_window": 0, "pos_open": 0,
        "merge_window": 0, "limit_unfilled": 0,
    }

    for sig in signals:
        sig_at = _ist(sig["created_at"])
        direction = direction_for(sig)
        for sym in symbols_for(sig):
            key = (sym, direction)
            prev = last_accept.get(key)
            if prev and (sig_at - prev) <= timedelta(minutes=90):
                drops["merge_window"] += 1
                continue

            bars = fetch_bars(sym, bars_start, bars_end)
            if bars is None:
                drops["no_bars"] += 1
                continue

            earliest = sig_at.replace(
                hour=ENTRY_EARLIEST[0], minute=ENTRY_EARLIEST[1], second=0, microsecond=0
            )
            entry_after = max(sig_at + timedelta(minutes=DELAY_MIN), earliest)
            cutoff = sig_at.replace(
                hour=ENTRY_CUTOFF[0], minute=ENTRY_CUTOFF[1], second=0, microsecond=0
            )
            day = bars[bars.index.date == sig_at.date()]
            entry_bars = day[(day.index >= entry_after) & (day.index <= cutoff)]
            if entry_bars.empty:
                drops["outside_window"] += 1
                continue

            entry_t = entry_bars.index[0].to_pydatetime()
            if open_until.get(sym) and entry_t < open_until[sym]:
                drops["pos_open"] += 1
                continue

            trade = _run_trade(sig, sym, direction, sig_at, day, entry_t, entry_mode)
            if trade is None:
                drops["limit_unfilled"] += 1
                last_accept[key] = sig_at  # the order was placed; story consumed
                continue
            trades.append(trade)
            open_until[sym] = trade.exit_at
            last_accept[key] = sig_at

    return trades, drops


def _run_trade(sig, sym, direction, sig_at, day, entry_t, entry_mode="market") -> Trade | None:
    entry_px = float(day.loc[entry_t, "Open"])
    limit_entry = entry_mode == "limit"
    if limit_entry:
        rest = day[day.index >= entry_t].head(3)  # entry bar + 2 = 15 min
        fill_t = None
        for t, bar in rest.iterrows():
            crossed = (
                float(bar["High"]) > entry_px if direction == "short"
                else float(bar["Low"]) < entry_px
            )
            if crossed:
                fill_t = t.to_pydatetime()
                break
        if fill_t is None:
            return None
        entry_t = fill_t  # price stays at the limit; clock starts at fill
    qty = calc_qty(POSITION_SIZE_INR, entry_px)
    if direction == "short":
        sl_px = entry_px * (1 + SL_PCT)
        tp_px = entry_px * (1 - TP_PCT)
    else:
        sl_px = entry_px * (1 - SL_PCT)
        tp_px = entry_px * (1 + TP_PCT)
    eod_t = entry_t.replace(hour=EOD_CLOSE[0], minute=EOD_CLOSE[1])

    path = day[day.index >= entry_t]
    exit_px, exit_reason, exit_at = None, None, None
    for t, bar in path.iterrows():
        t = t.to_pydatetime()
        if t >= eod_t:
            exit_px, exit_reason, exit_at = float(bar["Open"]), "eod_close", t
            break
        hi, lo = float(bar["High"]), float(bar["Low"])
        hit_sl = hi >= sl_px if direction == "short" else lo <= sl_px
        hit_tp = lo <= tp_px if direction == "short" else hi >= tp_px
        if hit_sl:  # conservative: SL wins ties within a bar
            exit_px, exit_reason, exit_at = sl_px, "sl_hit", t
            break
        if hit_tp:
            exit_px, exit_reason, exit_at = tp_px, "target_hit", t
            break
        held = (t + timedelta(minutes=5) - entry_t).total_seconds() / 60
        if held >= TIME_STOP_MIN:
            exit_px, exit_reason, exit_at = float(bar["Close"]), "time_stop", t
            break
    if exit_px is None:  # ran out of bars (truncated day) — close on last bar
        last_t, last_bar = path.index[-1].to_pydatetime(), path.iloc[-1]
        exit_px, exit_reason, exit_at = float(last_bar["Close"]), "eod_close", last_t

    gross, net, costs = calc_pnl(entry_px, exit_px, qty, direction=direction)
    if limit_entry:  # entry leg has no slippage on a resting limit fill
        net += entry_px * qty * 0.0005
        stress_extra = exit_px * qty * EXTRA_SLIPPAGE_RATE
    else:
        stress_extra = (entry_px + exit_px) * qty * EXTRA_SLIPPAGE_RATE
    return Trade(
        signal_id=str(sig["_id"]), symbol=sym, direction=direction,
        signal_at=sig_at, entry_at=entry_t, exit_at=exit_at,
        entry=entry_px, exit=exit_px, exit_reason=exit_reason, qty=qty,
        gross=round(gross, 2), net=round(net, 2), cost=round(costs["total"], 2),
        net_stress=round(net - stress_extra, 2),
    )


def summarize(trades: list[Trade], label: str) -> dict:
    n = len(trades)
    if n == 0:
        print(f"\n== {label}: 0 trades ==")
        return {}
    gross = sum(t.gross for t in trades)
    net = sum(t.net for t in trades)
    stress = sum(t.net_stress for t in trades)
    wins = sum(1 for t in trades if t.gross > 0)
    gross_pct = sum(
        (t.gross / (t.entry * t.qty)) * 100 for t in trades
    ) / n
    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    print(f"\n== {label} ==")
    print(f"  n={n}  wins={wins} ({wins / n * 100:.0f}%)")
    print(f"  gross  ₹{gross:+,.0f}  ({gross / n:+,.1f}/trade, {gross_pct:+.3f}%/trade)")
    print(f"  net    ₹{net:+,.0f}  ({net / n:+,.1f}/trade)")
    print(f"  stress ₹{stress:+,.0f}  ({stress / n:+,.1f}/trade)  [2x slippage]")
    print(f"  exits: {reasons}")
    return {
        "n": n, "wins": wins, "gross": gross, "net": net, "stress": stress,
        "gross_per": gross / n, "net_per": net / n, "stress_per": stress / n,
        "gross_pct_per": gross_pct, "reasons": reasons,
    }


def dump_trades(trades: list[Trade], path: Path) -> None:
    pd.DataFrame([t.__dict__ for t in trades]).to_csv(path, index=False)
    print(f"  trades -> {path}")
