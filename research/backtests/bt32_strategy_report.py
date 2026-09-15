"""BT32 — strategy report: every backtested trade drawn on its own chart, with
the indicators the engine actually reads.

Reads a `bt17_trades_<tag>.csv` written by bt17_momentum_pool.py, rebuilds each
session's 5-minute chart from the cached 1-minute bars, recomputes the exact
indicator series the engine uses, derives the support/resistance levels AS OF
THE ENTRY BAR (no look-ahead), and writes ONE self-contained HTML file:

  * three stacked panels per trade — price (candles, EMA9, EMA20, EMA200, VWAP, every
    support/resistance level, BUY/STOP/TARGET/EXIT, holding period shaded),
    volume (with its 20-bar average and the RVOL at entry), and MACD(12/26/9)
  * a sortable, filterable trade table
  * headline P&L at BOTH cost models: bt17's stressed net (its +40 bps/side
    slippage on top of real charges) and a realistic net recomputed per trade
    with the same itemised MIS model the engine books exits with

Levels are recomputed with `derive_levels` over bars up to and including the
entry bar only, which is what the engine saw. Drawing the day's final levels
would show a chart the strategy never had.

Example (from the repository root):
  apps/signal-engine/.venv/bin/python research/backtests/bt32_strategy_report.py \
      --trades research/backtests/bt17_trades_demo15.csv
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "signal-engine"))

from src.momentum_trader.candles import (  # noqa: E402
    PATTERN_RULES_VERSION,
    STRENGTH_WEAK_BELOW,
    completed_pattern_matches,
)
from src.momentum_trader.engine import resample_5m  # noqa: E402
from src.momentum_trader.indicators import volume_ratio  # noqa: E402
from src.momentum_trader.levels import derive_levels, is_structural  # noqa: E402
from src.momentum_trader.risk import DEFAULT_RR  # noqa: E402
from src.news_trader.trailing_sl import calc_costs  # noqa: E402

CACHE = ROOT / "research" / "backtests" / ".cache_upstox" / "1m"
ASSETS = Path(__file__).resolve().parent / "bt32_assets"
# bt17 nets its CSV at the +40 bps/side Gate-0 stress, so its `net_pct` column
# IS the stressed number and is used as-is. The realistic figure is recomputed
# per trade with `calc_costs` — the same itemised MIS model the engine books
# exits with — rather than by subtracting a flat percentage, so brokerage caps
# and the sell-side-only STT land on the right side of each trade.
STRESS_SLIP_PCT = 0.80        # 40 bps/side, for labelling only


def read_cache(symbol: str, year: int) -> pd.DataFrame:
    f = CACHE / f"{symbol}_{year}.parquet"
    return pd.read_parquet(f) if f.exists() else pd.DataFrame()


def session_mask(df: pd.DataFrame, day: pd.Timestamp) -> pd.Series:
    """Rows belonging to `day`. The cache index is tz-aware (Asia/Kolkata) and
    `day` comes from a date string, so compare on wall-clock dates rather than
    against a tz-naive timestamp, which matches nothing."""
    return pd.Series(df.index.tz_localize(None).normalize() == day.normalize(), index=df.index)


def load_1m(symbol: str, day: pd.Timestamp) -> pd.DataFrame:
    """Cached 1-minute bars for one session, plus nothing else."""
    df = read_cache(symbol, day.year)
    return df if df.empty else df[session_mask(df, day)]


def scan_patterns(bars: pd.DataFrame, timeframe: str) -> list[dict]:
    """Every formation in the session, found by PREFIX scan.

    Running the detector once over the finished session would let a formation be
    recognised using candles that had not printed yet. Scanning prefixes marks
    each formation at the moment it could first have been seen, which is what a
    review chart must show. Same approach as bt29.
    """
    seen: dict[tuple[str, str, str], dict] = {}
    for end in range(11, len(bars) + 1):
        for m in completed_pattern_matches(bars.iloc[:end], timeframe):
            key = (m.name, m.start, m.end)
            if key in seen:
                continue
            seen[key] = {
                "name": m.name, "timeframe": m.timeframe, "direction": m.direction,
                "kind": m.kind, "strength": round(float(m.strength), 2),
                "start": pd.Timestamp(m.start).strftime("%H:%M"),
                "end": pd.Timestamp(m.end).strftime("%H:%M"),
            }
    return list(seen.values())


def build_day(row: pd.Series) -> dict | None:
    """One trade → everything its chart needs.

    Only RAW 1-minute bars are shipped. The 5-minute resample and every
    indicator (EMA9/20, VWAP, MACD) are computed in the browser, exactly as the
    /momentum chart does, so the two views cannot drift apart and the payload
    does not carry the same numbers twice.
    """
    day = pd.Timestamp(row["date"])
    bars_1m = load_1m(str(row["symbol"]), day)
    if bars_1m.empty or len(bars_1m) < 30:
        return None
    tf5 = resample_5m(bars_1m)
    if len(tf5) < 35:
        return None

    tz = tf5.index.tz
    entry_at = pd.Timestamp(f"{day.date()} {row['entry_time']}", tz=tz)
    exit_at = pd.Timestamp(f"{day.date()} {row['exit_time']}", tz=tz)
    # searchsorted on bar STARTS: the bar containing a timestamp is the last one
    # that started at or before it.
    entry_i5 = max(0, int(tf5.index.searchsorted(entry_at, side="right")) - 1)

    # Levels exactly as the engine saw them: bars up to and including entry.
    prev_day = None
    allb = read_cache(str(row["symbol"]), day.year)
    if not allb.empty:
        dates = allb.index.tz_localize(None).normalize()
        before = allb[dates < day.normalize()]
        if not before.empty:
            bdates = before.index.tz_localize(None).normalize()
            last = before[bdates == bdates.max()]
            prev_day = {"high": float(last["high"].max()), "low": float(last["low"].min()),
                        "close": float(last["close"].iloc[-1])}
    entry, stop = float(row["entry"]), float(row["stop"])
    levels = derive_levels(tf5.iloc[: entry_i5 + 1], prev_day)
    seen: set[tuple[float, str]] = set()
    lv = []
    for x in sorted(levels, key=lambda z: -z.strength):
        key = (round(x.price, 2), x.kind)
        if key in seen:
            continue
        seen.add(key)
        lv.append({"price": round(x.price, 2), "kind": x.kind, "touches": int(x.touches),
                   "strength": round(float(x.strength), 2), "structural": is_structural(x),
                   "side": "resistance" if x.price > entry else "support"})

    gross_pct = float(row["gross_pct"])
    qty = int(row["qty"])
    notional = max(entry * qty, 1e-9)
    real_costs = calc_costs(entry, float(row["exit"]), qty, direction="long")["total"]
    vr5 = volume_ratio(tf5)
    rvol_5m = (None if entry_i5 >= len(vr5) or pd.isna(vr5.iloc[entry_i5])
               else round(float(vr5.iloc[entry_i5]), 2))
    return {
        "symbol": str(row["symbol"]), "date": str(day.date()), "setup": str(row["setup"]),
        "entry_time": entry_at.strftime("%H:%M"), "exit_time": exit_at.strftime("%H:%M"),
        "trigger": float(row["trigger"]), "entry": entry, "stop": stop,
        # the engine writes no target in trend modes; show where 2R would have been
        "target": round(entry + DEFAULT_RR * (entry - stop), 2),
        "exit": float(row["exit"]), "exit_reason": str(row["exit_reason"]),
        "qty": qty, "day_chg_pct": round(float(row["day_chg_pct"]), 2),
        "rvol": round(float(row["rvol"]), 2), "rvol_5m": rvol_5m,
        "gross_pct": round(gross_pct, 3),
        "gross_inr": round(float(row["gross_inr"]), 2),
        "net_stress_inr": round(float(row["net_inr"]), 2),
        "net_stress_pct": round(float(row["net_pct"]), 3),
        "real_cost_inr": round(real_costs, 2),
        "net_real_inr": round(float(row["gross_inr"]) - real_costs, 2),
        "net_real_pct": round(gross_pct - 100.0 * real_costs / notional, 3),
        "levels": lv,
        "patterns": scan_patterns(tf5, "5m") + scan_patterns(bars_1m, "1m"),
        "bars": [
            [ts.strftime("%H:%M"), round(float(r["open"]), 2), round(float(r["high"]), 2),
             round(float(r["low"]), 2), round(float(r["close"]), 2), float(r["volume"])]
            for ts, r in bars_1m.iterrows()
        ],
    }


def summarise(rows: list[dict]) -> dict:
    tr = pd.DataFrame(rows)
    n = len(tr)
    return {
        "n": n,
        "symbols": int(tr["symbol"].nunique()),
        "days": int(tr["date"].nunique()),
        "gross_pct": round(float(tr["gross_pct"].mean()), 3),
        "net_stress_pct": round(float(tr["net_stress_pct"].mean()), 3),
        "net_real_pct": round(float(tr["net_real_pct"].mean()), 3),
        "real_cost_pct": round(float((100.0 * tr["gross_pct"] - 100.0 * tr["net_real_pct"]).mean())
                               / 100.0, 3),
        "gross_inr": round(float(tr["gross_inr"].sum()), 0),
        "net_stress_inr": round(float(tr["net_stress_inr"].sum()), 0),
        "net_real_inr": round(float(tr["net_real_inr"].sum()), 0),
        "win_gross": round(100.0 * float((tr["gross_pct"] > 0).mean()), 1),
        "win_real": round(100.0 * float((tr["net_real_pct"] > 0).mean()), 1),
    }


def build_html(title: str, sub: str, data: dict) -> str:
    css = (ASSETS / "report.css").read_text()
    js = (ASSETS / "report.js").read_text()
    s = data["summary"]

    def chip(label: str, value: str, tone: str = "") -> str:
        return f'<span class="chip {tone}"><b>{html.escape(value)}</b> {html.escape(label)}</span>'

    tone = lambda x: "pos" if x > 0 else "neg" if x < 0 else ""  # noqa: E731
    chips = "".join([
        chip("trades", str(s["n"])), chip("symbols", str(s["symbols"])),
        chip("sessions", str(s["days"])),
        chip("gross / trade", f'{s["gross_pct"]:+.3f}%', tone(s["gross_pct"])),
        chip(f'net / trade @ real costs ({s["real_cost_pct"]:.2f}%)',
             f'{s["net_real_pct"]:+.3f}%', tone(s["net_real_pct"])),
        chip("net / trade @ bt17 stress", f'{s["net_stress_pct"]:+.3f}%',
             tone(s["net_stress_pct"])),
        chip("win% gross", f'{s["win_gross"]:.1f}%'),
        chip("win% @ real costs", f'{s["win_real"]:.1f}%'),
        chip("net ₹ @ real", f'{s["net_real_inr"]:,.0f}', tone(s["net_real_inr"])),
    ])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>{css}</style></head><body>
<header>
  <h1>{html.escape(title)}</h1>
  <p>{sub}</p>
</header>
<div class="stats">{chips}</div>
<div class="wrap">
  <div id="charts"></div>
  <div class="side">
    <div class="panel">
      <h3>Chart</h3>
      <div class="filters">
        <label>Timeframe <select id="f-tf">
          <option value="5m">5-minute (what the engine decides on)</option>
          <option value="1m">1-minute</option>
        </select></label>
      </div>
      <div class="legend" id="legend"></div>
    </div>
    <div class="panel">
      <h3>Filters</h3>
      <div class="filters">
        <label>Symbol <select id="f-sym"><option value="">all</option></select></label>
        <label>Exit <select id="f-exit"><option value="">all</option></select></label>
        <label>Outcome <select id="f-out">
          <option value="">all</option><option value="win">gross win</option>
          <option value="loss">gross loss</option></select></label>
      </div>
      <p class="hint">Click any row to jump to its chart.</p>
      <div class="scroll"><table id="det"><thead><tr>
        <th data-k="date">date</th><th data-k="symbol">symbol</th>
        <th data-k="entry_time">in</th><th data-k="exit_reason">exit</th>
        <th data-k="gross_pct" class="num">gross%</th>
        <th data-k="net_real_inr" class="num">net ₹</th>
      </tr></thead><tbody></tbody></table></div>
    </div>
  </div>
</div>
<script>var DATA = {json.dumps(data)};</script>
<script>{js}</script>
</body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", default="research/backtests/bt17_trades_demo15.csv")
    ap.add_argument("--output", default="research/backtests/bt32_strategy_report.html")
    ap.add_argument("--max-charts", type=int, default=120,
                    help="cap the number of charts embedded (0 = all)")
    ap.add_argument("--title", default="BT32 — momentum strategy, trade by trade")
    a = ap.parse_args()

    src = Path(a.trades) if Path(a.trades).is_absolute() else ROOT / a.trades
    tr = pd.read_csv(src)
    if tr.empty:
        print(f"{src.name} has no trades")
        return 1

    rows: list[dict] = []
    skipped = 0
    for _, r in tr.iterrows():
        d = build_day(r)
        if d is None:
            skipped += 1
            continue
        rows.append(d)
        if a.max_charts and len(rows) >= a.max_charts:
            break
    if not rows:
        print("no trade could be rebuilt from the bar cache")
        return 1

    data = {"trades": rows, "summary": summarise(rows),
            "weak_strength": STRENGTH_WEAK_BELOW,
            "rules_version": PATTERN_RULES_VERSION}
    sub = (
        f"{len(rows)} trades from <code>{html.escape(src.name)}</code>, each rebuilt from the "
        "cached 1-minute bars and resampled to the 5-minute frame the engine decides on. "
        "Support/resistance is recomputed <b>as of the entry bar only</b> — the levels the "
        "engine actually had, not the day's final ones. "
        f"Costs are shown twice: bt17's stressed number (its own +{STRESS_SLIP_PCT / 2:.2f}%/side "
        "slippage on top of real charges) and a realistic one recomputed per trade with the "
        "same itemised MIS model the engine books exits with (brokerage cap, sell-side STT, "
        "exchange, SEBI, stamp, GST). "
        "Descriptive report of an already-measured window — not a new test."
    )
    out = Path(a.output) if Path(a.output).is_absolute() else ROOT / a.output
    out.write_text(build_html(a.title, sub, data))
    size = out.stat().st_size / 1e6
    print(f"wrote {out} ({size:.1f} MB, {len(rows)} charts, {skipped} skipped for missing bars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
