# /// script
# requires-python = ">=3.11"
# dependencies = ["yfinance", "pandas", "numpy"]
# ///
"""BT14 — short-term (1-day) reversal cost-stress screen
(hypothesis: research/hypotheses/2026-06-29-short-term-reversal.md).

Gate 0, evaluated FIRST: does buying yesterday's top-N losers and shorting
yesterday's top-N winners (NIFTY 500 universe) survive realistic round-trip
costs? Mechanics LOCKED in the hypothesis before this ran:

  - rank on day D by the day's move r_D = close[D]/close[D-1] - 1 (known at D close)
  - losers = N lowest r_D, winners = N highest r_D  (N = 10)
  - entry D+1 OPEN (strictly after the ranking info — no same-bar look-ahead)
  - Config A (PRIMARY, both legs feasible intraday MIS): exit D+1 CLOSE
      costs = production intraday model (trailing_sl.calc_costs) + STRESS +10bps/side
  - Config B (secondary, long-only — overnight shorts infeasible in cash): exit D+2 OPEN
      costs = local DELIVERY model + STRESS slippage
  - Config C (secondary): D+1 open -> D+3 close, delivery long-only
  - anti-strategy = the same book with sides flipped (short losers / long winners).
    If the anti book is also gross-positive, the "edge" is vol-harvesting, not reversal.

DEV window only (2025-07-01..2025-12-31). The 2026 hold-out is NOT touched here.
Universe membership is as-of-today (mild survivorship bias, favours the strategy);
a KILL under that favourable bias is robust. Run: `uv run bt14_reversal.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "apps" / "signal-engine" / "src"))

from news_trader.nifty500 import NIFTY_500  # noqa: E402
from news_trader.trailing_sl import calc_costs  # noqa: E402  (production intraday MIS)

CACHE = Path(__file__).resolve().parent / ".cache_daily"
CACHE.mkdir(exist_ok=True)

# --- LOCKED parameters (from the hypothesis) ---
DEV_START, DEV_END = "2025-07-01", "2025-12-31"
FETCH_START, FETCH_END = "2025-06-15", "2026-01-08"  # padding for D-1 / D+2 / D+3
N = 10                          # top/bottom names per side per day
POSITION_INR = 50_000.0         # per-name notional (10 names => ₹5L book/side)
STRESS_SLIP_INTRADAY = 0.0010   # +10 bps/side on top of the model's 5 bps (Config A)
# delivery (overnight) cost constants — Indian equity CNC, Zerodha
DLV_STT = 0.001                 # 0.1% BOTH sides (vs 0.025% sell-only intraday)
DLV_STAMP = 0.00015             # 0.015% buy-side
DLV_EXCHANGE = 0.0000297        # per side
DLV_SEBI = 0.000001             # per side
DLV_GST = 0.18                  # on (brokerage + exchange); brokerage = 0 (Zerodha CNC free)
DLV_SLIP_BASE = 0.0005          # 5 bps/side base
DLV_SLIP_STRESS = 0.0015        # 15 bps/side stressed (wide spreads on big movers)


def fetch_daily(symbol: str) -> pd.DataFrame | None:
    cache = CACHE / f"{symbol.replace('&', '_')}.csv"
    if cache.exists():
        df = pd.read_csv(cache, index_col=0, parse_dates=True)
        return None if df.empty else df
    import yfinance as yf

    try:
        df = yf.download(
            f"{symbol}.NS", start=FETCH_START, end=FETCH_END, interval="1d",
            progress=False, auto_adjust=False, multi_level_index=False,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] {symbol}: {exc}", file=sys.stderr)
        df = None
    if df is None or df.empty:
        pd.DataFrame().to_csv(cache)
        return None
    df = df[["Open", "High", "Low", "Close"]].copy()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.to_csv(cache)
    return df


def delivery_cost(entry: float, exit_: float, qty: int, direction: str, slip: float) -> float:
    entry_val, exit_val = entry * qty, exit_ * qty
    sell_val = entry_val if direction == "short" else exit_val
    buy_val = exit_val if direction == "short" else entry_val
    brokerage = 0.0  # Zerodha CNC delivery is free
    stt = (buy_val + sell_val) * DLV_STT
    exchange = (entry_val + exit_val) * DLV_EXCHANGE
    sebi = (entry_val + exit_val) * DLV_SEBI
    stamp = buy_val * DLV_STAMP
    gst = (brokerage + exchange) * DLV_GST
    slippage = (entry_val + exit_val) * slip
    return brokerage + stt + exchange + sebi + stamp + gst + slippage


def intraday_net(entry: float, exit_: float, qty: int, direction: str, stress: bool) -> float:
    """Production MIS round-trip; stress adds +STRESS_SLIP_INTRADAY/side."""
    gross = (entry - exit_) * qty if direction == "short" else (exit_ - entry) * qty
    costs = calc_costs(entry, exit_, qty, direction=direction)["total"]
    if stress:
        costs += (entry + exit_) * qty * STRESS_SLIP_INTRADAY
    return gross - costs


def load_panel() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (open_px, close_px) wide frames indexed by date, columns = symbols."""
    opens, closes, miss = {}, {}, 0
    for sym in NIFTY_500:
        df = fetch_daily(sym)
        if df is None or len(df) < 5:
            miss += 1
            continue
        opens[sym], closes[sym] = df["Open"], df["Close"]
    print(f"universe: {len(closes)}/{len(NIFTY_500)} symbols with bars ({miss} missing)")
    return pd.DataFrame(opens).sort_index(), pd.DataFrame(closes).sort_index()


def run() -> None:
    open_px, close_px = load_panel()
    ret = close_px.pct_change()  # r_D = close[D]/close[D-1]-1, NaN-safe
    dates = [d for d in close_px.index if DEV_START <= d.strftime("%Y-%m-%d") <= DEV_END]
    all_dates = list(close_px.index)
    pos = {d: i for i, d in enumerate(all_dates)}

    rows: list[dict] = []
    daily: list[dict] = []  # one cohort row per ranking day (for Sharpe)

    for d in dates:
        i = pos[d]
        if i + 2 >= len(all_dates):  # need D+1 (entry/exit), and D+2 for overnight/D+3
            continue
        d1, d2 = all_dates[i + 1], all_dates[i + 2]
        d3 = all_dates[i + 3] if i + 3 < len(all_dates) else None

        r = ret.loc[d].dropna()
        if len(r) < 2 * N + 10:
            continue
        losers = r.nsmallest(N).index
        winners = r.nlargest(N).index

        def trade(sym, side, entry, exit_, cfg):
            if not (np.isfinite(entry) and np.isfinite(exit_)) or entry <= 0:
                return None
            qty = max(1, int(POSITION_INR // entry))
            gross = (entry - exit_) * qty if side == "short" else (exit_ - entry) * qty
            return {
                "rank_day": d.date(), "symbol": sym, "side": side, "cfg": cfg,
                "entry": round(entry, 2), "exit": round(exit_, 2), "qty": qty,
                "ret_pct": round((gross / (entry * qty)) * 100, 4), "gross": round(gross, 2),
            }

        # --- Config A: intraday next-day, BOTH legs (reversal) + anti ---
        a_rev_net, a_rev_grosspct = [], []
        a_anti_grosspct = []
        for sym in losers:
            e, x = open_px.at[d1, sym], close_px.at[d1, sym]
            t = trade(sym, "long", e, x, "A")
            if t:
                t["net_stress"] = round(intraday_net(e, x, t["qty"], "long", True), 2)
                t["net"] = round(intraday_net(e, x, t["qty"], "long", False), 2)
                rows.append(t); a_rev_net.append(t["net_stress"]); a_rev_grosspct.append(t["ret_pct"])
                a_anti_grosspct.append(-t["ret_pct"])  # anti = short the loser
        for sym in winners:
            e, x = open_px.at[d1, sym], close_px.at[d1, sym]
            t = trade(sym, "short", e, x, "A")
            if t:
                t["net_stress"] = round(intraday_net(e, x, t["qty"], "short", True), 2)
                t["net"] = round(intraday_net(e, x, t["qty"], "short", False), 2)
                rows.append(t); a_rev_net.append(t["net_stress"]); a_rev_grosspct.append(t["ret_pct"])
                a_anti_grosspct.append(-t["ret_pct"])  # anti = long the winner

        # --- Config B: overnight long-only losers (D+1 open -> D+2 open), delivery ---
        b_net = []
        for sym in losers:
            e, x = open_px.at[d1, sym], open_px.at[d2, sym]
            t = trade(sym, "long", e, x, "B")
            if t:
                c = delivery_cost(e, x, t["qty"], "long", DLV_SLIP_STRESS)
                b_net.append(t["gross"] - c)

        # --- Config C: long-only losers D+1 open -> D+3 close, delivery ---
        c_net = []
        if d3 is not None:
            for sym in losers:
                e, x = open_px.at[d1, sym], close_px.at[d3, sym]
                t = trade(sym, "long", e, x, "C")
                if t:
                    cc = delivery_cost(e, x, t["qty"], "long", DLV_SLIP_STRESS)
                    c_net.append(t["gross"] - cc)

        if a_rev_grosspct:
            daily.append({
                "day": d.date(),
                "rev_gross_pct": float(np.mean(a_rev_grosspct)),
                "anti_gross_pct": float(np.mean(a_anti_grosspct)),
                "rev_net_stress_inr": float(np.mean(a_rev_net)),
                "b_net_inr": float(np.mean(b_net)) if b_net else np.nan,
                "c_net_inr": float(np.mean(c_net)) if c_net else np.nan,
            })

    if not daily:
        print("no cohorts — check data coverage / window")
        return

    dd = pd.DataFrame(daily)
    n_days = len(dd)
    trades_df = pd.DataFrame(rows)
    trades_df.to_csv(Path(__file__).parent / "bt14_trades_dev.csv", index=False)

    def sharpe(series: pd.Series) -> float:
        s = series.dropna()
        return float(s.mean() / s.std() * np.sqrt(252)) if len(s) > 1 and s.std() > 0 else 0.0

    a_gross = trades_df[trades_df.cfg == "A"]["ret_pct"].mean()
    a_net_stress_per = trades_df[trades_df.cfg == "A"]["net_stress"].mean()
    a_net_stress_pct = (dd["rev_net_stress_inr"] / POSITION_INR * 100).mean()
    anti_gross = dd["anti_gross_pct"].mean()
    # daily long-short cohort net series (₹ per trade-day, stressed) for Sharpe
    sharpe_stress = sharpe(dd["rev_net_stress_inr"])

    print("\n" + "=" * 68)
    print(f"BT14 — short-term reversal | DEV {DEV_START}..{DEV_END} | N={N}/side")
    print(f"trade-days={n_days}  trades={len(trades_df)}  pos=₹{POSITION_INR:,.0f}/name")
    print("=" * 68)
    print("\n-- GATE 0 (PRIMARY): Config A intraday next-day, long-short --")
    print(f"  gross long-short        : {a_gross:+.4f}%/trade")
    print(f"  NET (stress +10bps/side): ₹{a_net_stress_per:+,.1f}/trade  ({a_net_stress_pct:+.4f}%/trade)")
    print(f"  daily cohort Sharpe     : {sharpe_stress:+.3f}  (stressed net, annualised)")
    print(f"  >>> GATE 0 {'PASS' if a_net_stress_per > 0 else 'FAIL → KILL'} "
          f"(stressed net per-trade {'> 0' if a_net_stress_per > 0 else '<= 0'})")
    print("\n-- supporting checks --")
    print(f"  anti-strategy (momentum) gross: {anti_gross:+.4f}%/trade "
          f"({'OK <=0 & < reversal' if anti_gross <= 0 and anti_gross < a_gross else 'WARN — reversal not distinct'})")
    print(f"  gross >= +0.30%/trade floor   : {'PASS' if a_gross >= 0.30 else 'FAIL'}")
    print(f"  n trade-days >= 30            : {'PASS' if n_days >= 30 else 'FAIL'}")
    print("\n-- secondaries (NOT a second shot at PASS) --")
    print(f"  Config B overnight long-only losers (delivery, stress): "
          f"₹{dd['b_net_inr'].mean():+,.1f}/trade  ({dd['b_net_inr'].mean()/POSITION_INR*100:+.4f}%)")
    print(f"  Config C D+1->D+3 long-only losers (delivery, stress) : "
          f"₹{dd['c_net_inr'].mean():+,.1f}/trade  ({dd['c_net_inr'].mean()/POSITION_INR*100:+.4f}%)")
    # long-only vs short-only split within Config A (which leg, if any, carries it)
    la = trades_df[(trades_df.cfg == "A") & (trades_df.side == "long")]
    sa = trades_df[(trades_df.cfg == "A") & (trades_df.side == "short")]
    print(f"  A long-losers gross  : {la['ret_pct'].mean():+.4f}%/trade  net_stress ₹{la['net_stress'].mean():+,.1f}")
    print(f"  A short-winners gross: {sa['ret_pct'].mean():+.4f}%/trade  net_stress ₹{sa['net_stress'].mean():+,.1f}")
    print(f"\n  trades -> bt14_trades_dev.csv")


if __name__ == "__main__":
    run()
