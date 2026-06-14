# /// script
# requires-python = ">=3.11"
# dependencies = ["yfinance", "pymongo", "pandas"]
# ///
"""BT11 — news-signal bullish-LONG expressed as a long call, synthetic pricing.
(hypothesis: research/hypotheses/2026-06-13-news-options-long.md)

⚠️ EXPLORATORY SCREEN, NOT A RESULT. This sweeps strike × expiry × stop-loss =
a grid of variations on SYNTHETIC Black-Scholes premiums (no real option bars).
A grid is multiple testing; any green cell here is a HYPOTHESIS to validate on
real Kite option bars, never a ship signal. Synthetic IV cannot be a hold-out.

Same signal cohort as BT9/BT10 (bullish / high-conf / moderate|major, NIFTY500
ex-NIFTY50) so the option P&L is directly comparable to the equity result —
PLUS a has_options() filter (single-stock options exist for ~77% of the cohort).

Each variation is scored under three regimes on the SAME trades:
  - baseline : IV flat at IV_BASELINE entry->exit
  - vol-crush: IV declines linearly to IV_BASELINE*IV_CRUSH over the hold
  - spread-2x: baseline path, slippage doubled (the dominant option cost)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from replay_lib import (
    DELAY_MIN, ENTRY_CUTOFF, ENTRY_EARLIEST, EOD_CLOSE, IST, TIME_STOP_MIN,
    NIFTY_50, NIFTY_500, _ist, fetch_bars, load_signals,
)
import options_lib as opt

BARS_START, BARS_END = "2026-06-01", "2026-06-12"
BUDGET_INR = 50_000.0          # recommended option slot (see note in summary)
IV_BASELINE = 0.30
IV_CRUSH = 0.75                # exit IV = IV_BASELINE * 0.75 under crush
SLIP_BASE = 0.01               # 100 bps/side on premium
SLIP_STRESS = 0.02             # 2x spread stress

# Single-stock options are MONTHLY only — these are the two month-ends bracketing
# the June signal window (verified against the Zerodha dump).
EXPIRIES = {
    "near": datetime(2026, 6, 30, 15, 30, tzinfo=IST),
    "next": datetime(2026, 7, 28, 15, 30, tzinfo=IST),
}

# --- the variation grid ----------------------------------------------------
STRIKES = {"ATM": 0, "1-OTM": 1}                 # moneyness in strike steps
STOPS = {                                         # (premium SL, premium TP)
    "30/50": (-0.30, 0.50),
    "50/100": (-0.50, 1.00),
}


def iv_at(elapsed_min: float, crush: bool) -> float:
    if not crush:
        return IV_BASELINE
    frac = min(elapsed_min / TIME_STOP_MIN, 1.0)
    return IV_BASELINE * (1.0 - (1.0 - IV_CRUSH) * frac)


@dataclass
class OTrade:
    symbol: str
    lots: int
    entry_exposure: float
    entry_prem: float
    exit_prem: float
    exit_reason: str
    gross: float
    net: float


def _run_option_trade(sym, day, entry_t, expiry, moneyness, sl_tp, crush, slip):
    spot_entry = float(day.loc[entry_t, "Open"])
    step = opt.strike_step(sym)
    K = opt.select_contract(spot_entry, "CE", step, moneyness)
    t_entry = opt.year_fraction(entry_t, expiry)
    entry_prem = opt.price_option(spot_entry, K, t_entry, IV_BASELINE, "CE")
    if entry_prem <= 0.05:          # deep-OTM / no premium -> untradeable
        return None
    ls = opt.lot_size(sym)
    lots = opt.lots_for_budget(entry_prem, ls, BUDGET_INR)
    sl_lvl = entry_prem * (1 + sl_tp[0])
    tp_lvl = entry_prem * (1 + sl_tp[1])
    eod_t = entry_t.replace(hour=EOD_CLOSE[0], minute=EOD_CLOSE[1])

    path = day[day.index >= entry_t]
    exit_prem = exit_reason = None
    for ts, bar in path.iterrows():
        t = ts.to_pydatetime()
        elapsed = (t - entry_t).total_seconds() / 60
        iv = iv_at(elapsed, crush)
        ty = opt.year_fraction(t, expiry)
        if t >= eod_t:
            exit_prem = opt.price_option(float(bar["Open"]), K, ty, iv, "CE")
            exit_reason = "eod_close"
            break
        prem_low = opt.price_option(float(bar["Low"]), K, ty, iv, "CE")
        prem_high = opt.price_option(float(bar["High"]), K, ty, iv, "CE")
        if prem_low <= sl_lvl:          # conservative: SL wins intrabar ties
            exit_prem, exit_reason = sl_lvl, "sl_hit"
            break
        if prem_high >= tp_lvl:
            exit_prem, exit_reason = tp_lvl, "target_hit"
            break
        held = (t + timedelta(minutes=5) - entry_t).total_seconds() / 60
        if held >= TIME_STOP_MIN:
            exit_prem = opt.price_option(float(bar["Close"]), K, ty, iv, "CE")
            exit_reason = "time_stop"
            break
    if exit_prem is None:
        last_t, last_bar = path.index[-1].to_pydatetime(), path.iloc[-1]
        iv = iv_at((last_t - entry_t).total_seconds() / 60, crush)
        ty = opt.year_fraction(last_t, expiry)
        exit_prem = opt.price_option(float(last_bar["Close"]), K, ty, iv, "CE")
        exit_reason = "eod_close"

    gross, net, _ = opt.calc_option_pnl(entry_prem, exit_prem, lots, ls, slip)
    return OTrade(
        symbol=sym, lots=lots, entry_exposure=round(entry_prem * ls * lots, 0),
        entry_prem=round(entry_prem, 2), exit_prem=round(exit_prem, 2),
        exit_reason=exit_reason, gross=round(gross, 2), net=round(net, 2),
    )


def simulate(signals, eligible, expiry, moneyness, sl_tp, crush, slip):
    open_until: dict[str, datetime] = {}
    last_accept: dict[str, datetime] = {}
    trades: list[OTrade] = []
    for sig in signals:
        sig_at = _ist(sig["created_at"])
        for sym in eligible(sig):
            prev = last_accept.get(sym)
            if prev and (sig_at - prev) <= timedelta(minutes=90):
                continue
            bars = fetch_bars(sym, BARS_START, BARS_END)
            if bars is None:
                continue
            earliest = sig_at.replace(hour=ENTRY_EARLIEST[0], minute=ENTRY_EARLIEST[1],
                                      second=0, microsecond=0)
            entry_after = max(sig_at + timedelta(minutes=DELAY_MIN), earliest)
            cutoff = sig_at.replace(hour=ENTRY_CUTOFF[0], minute=ENTRY_CUTOFF[1],
                                    second=0, microsecond=0)
            day = bars[bars.index.date == sig_at.date()]
            win = day[(day.index >= entry_after) & (day.index <= cutoff)]
            if win.empty:
                continue
            entry_t = win.index[0].to_pydatetime()
            if open_until.get(sym) and entry_t < open_until[sym]:
                continue
            tr = _run_option_trade(sym, day, entry_t, expiry, moneyness, sl_tp, crush, slip)
            last_accept[sym] = sig_at
            if tr is None:
                continue
            # approximate the hold end as entry + time-stop for the open-position guard
            open_until[sym] = entry_t + timedelta(minutes=TIME_STOP_MIN)
            trades.append(tr)
    return trades


def score(trades: list[OTrade]) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0}
    net = sum(t.net for t in trades)
    wins = sum(1 for t in trades if t.gross > 0)
    gross_pct = sum((t.exit_prem / t.entry_prem - 1) * 100 for t in trades) / n
    return {
        "n": n, "win": wins / n * 100, "net_per": net / n,
        "gross_pct_per": gross_pct,
        "avg_lots": sum(t.lots for t in trades) / n,
        "avg_exp": sum(t.entry_exposure for t in trades) / n,
    }


def main():
    signals = load_signals({
        "signal": "bullish", "confidence": "high",
        "magnitude": {"$in": ["moderate", "major"]},
        "created_at": {"$gte": datetime(2026, 6, 2)},
        "stocks.0": {"$exists": True},
    })
    eligible = lambda s: [
        x for x in s["stocks"]
        if x in NIFTY_500 and x not in NIFTY_50 and opt.has_options(x)
    ]
    print(f"cohort: {len(signals)} signals | budget ₹{BUDGET_INR:,.0f}/slot | "
          f"IV {IV_BASELINE:.0%} crush->{IV_BASELINE*IV_CRUSH:.0%}")
    print(f"F&O-eligible symbols in cohort: {len(opt.NFO_SPECS)} have options, "
          f"{len(opt.NOT_FO)} dropped (cash-only)\n")

    hdr = f"{'strike':6} {'exp':5} {'stop':7} | {'n':>3} {'win%':>5} {'grs%/t':>7} " \
          f"{'net/t base':>10} {'net/t crush':>11} {'net/t 2xslip':>12}"
    print(hdr)
    print("-" * len(hdr))
    rows = []
    for sk_name, mny in STRIKES.items():
        for ex_name, expiry in EXPIRIES.items():
            for st_name, sl_tp in STOPS.items():
                base = simulate(signals, eligible, expiry, mny, sl_tp, False, SLIP_BASE)
                crush = simulate(signals, eligible, expiry, mny, sl_tp, True, SLIP_BASE)
                strn = simulate(signals, eligible, expiry, mny, sl_tp, False, SLIP_STRESS)
                b, c, s = score(base), score(crush), score(strn)
                if b["n"] == 0:
                    continue
                rows.append((sk_name, ex_name, st_name, b, c, s))
                print(f"{sk_name:6} {ex_name:5} {st_name:7} | {b['n']:>3} "
                      f"{b['win']:>5.0f} {b['gross_pct_per']:>+7.1f} "
                      f"{b['net_per']:>+10,.0f} {c['net_per']:>+11,.0f} "
                      f"{s['net_per']:>+12,.0f}")

    print(f"\nequity BT9 baseline (same cohort, long STOCK): "
          f"gross -0.25%/trade, net ~-₹68/trade  (KILLED)")
    print("kill line: a cell only 'survives' if net/t > 0 under base AND crush "
          "AND 2xslip AND beats equity. Reminder: synthetic-IV screen, not validation.")
    if rows:
        avg_exp = sum(r[3]["avg_exp"] for r in rows) / len(rows)
        avg_lots = sum(r[3]["avg_lots"] for r in rows) / len(rows)
        print(f"\nsizing: avg {avg_lots:.1f} lots/trade, avg exposure ₹{avg_exp:,.0f} "
              f"at ₹{BUDGET_INR:,.0f} budget")


if __name__ == "__main__":
    main()
