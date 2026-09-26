# /// script
# requires-python = ">=3.12"
# dependencies = ["pandas", "pyarrow"]
# ///
"""BT50 — every trade whose outcome the session-resistance target changed,
drawn in the BT32 chart format.

Each card is the NEW rule's trade on a TradingView Lightweight Charts candle
chart (via bt32): BUY/SELL markers, stop, the buffered target, EMA9/20/200,
VWAP, MACD and volume. Every resistance the rule could choose from is drawn -
today's levels as of the entry bar plus the 10 earlier sessions' highs and
5-minute pivots - with the one that actually set the target labelled
`frozen resistance`, and the control's 2R target as a faint level so the move
is visible. The header line carries the paired net delta. A visual review
only; the verdict is in bt50_session_target_report.py.

Usage (repo root):
  uv run research/backtests/bt50_changed_trades_report.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "apps" / "signal-engine"))

import bt32_strategy_report as bt32  # noqa: E402
from src.momentum_trader.engine import build_session_levels  # noqa: E402
from src.momentum_trader.levels import SESSION_LEVEL_SESSIONS, is_structural  # noqa: E402

KEY = ["date", "symbol", "setup", "entry_time", "entry"]


def _load(paths: list[Path]) -> pd.DataFrame:
    return pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)


def changed_trades(control: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    ctl = control[KEY + ["exit", "exit_reason", "target", "net_inr"]].rename(columns={
        "exit": "ctl_exit", "exit_reason": "ctl_exit_reason",
        "target": "ctl_target", "net_inr": "ctl_net_inr"})
    m = new.merge(ctl, on=KEY, how="inner", validate="one_to_one")
    moved = (m["exit_reason"] != m["ctl_exit_reason"]) | ((m["exit"] - m["ctl_exit"]).abs() > 1e-9)
    out = m[moved].copy()
    out["delta"] = out["net_inr"] - out["ctl_net_inr"]
    return out.sort_values(["date", "symbol", "entry_time"]).reset_index(drop=True)


def prior_session_levels(symbol: str, day: pd.Timestamp, entry: float) -> list[dict]:
    """The session levels the engine had pre-open that day, above the entry.

    Built with the same `build_session_levels` the replay and the live scanner
    call, from cached sessions strictly before `day`.
    """
    frames = [bt32.read_cache(symbol, y) for y in (day.year - 1, day.year)]
    hist = pd.concat([f for f in frames if not f.empty]) if any(
        not f.empty for f in frames) else pd.DataFrame()
    if hist.empty:
        return []
    hist = hist[hist.index.tz_localize(None).normalize() < day.normalize()]
    out = []
    for lv in build_session_levels(hist, SESSION_LEVEL_SESSIONS):
        if lv.price > entry:
            out.append({"price": round(lv.price, 2), "kind": lv.kind,
                        "touches": int(lv.touches), "strength": round(float(lv.strength), 2),
                        "structural": is_structural(lv), "side": "resistance"})
    return out


def build_cards(rows: pd.DataFrame) -> list[dict]:
    cards: list[dict] = []
    for _, row in rows.iterrows():
        card = bt32.build_day(row)
        if card is None:
            continue
        card["target"] = round(float(row["target"]), 2)
        card["target_label"] = "Target (new)"
        card["setup"] = (f"{row['setup']} · Δ ₹{row['delta']:+,.0f} vs control "
                         f"({row['ctl_exit_reason']} @ {row['ctl_exit']:.2f})")
        frozen = (round(float(row["structural_resistance"]), 2)
                  if pd.notna(row["structural_resistance"]) else None)
        levels = [lv for lv in card["levels"] + prior_session_levels(
                      str(row["symbol"]), pd.Timestamp(row["date"]), float(row["entry"]))
                  if frozen is None or abs(lv["price"] - frozen) > 0.005]
        if frozen is not None:
            levels.append({"price": frozen,
                           "kind": f"frozen resistance ({row['structural_resistance_kind']})",
                           "touches": 0, "strength": 0.0, "structural": True,
                           "side": "resistance"})
        levels.append({"price": round(float(row["ctl_target"]), 2),
                       "kind": "control target (2R)", "touches": 0, "strength": 0.0,
                       "structural": False, "side": "resistance"})
        card["levels"] = levels
        card["patterns"] = []
        cards.append(card)
    return cards


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--control", type=Path, nargs="+", default=[
        HERE / "bt17_trades_bt50_25_ctl.csv", HERE / "bt17_trades_bt50_26_ctl.csv"])
    ap.add_argument("--experiment", type=Path, nargs="+", default=[
        HERE / "bt17_trades_bt50_25_new.csv", HERE / "bt17_trades_bt50_26_new.csv"])
    ap.add_argument("--output", type=Path, default=HERE / "bt50_changed_trades_report.html")
    a = ap.parse_args()

    rows = changed_trades(_load(a.control), _load(a.experiment))
    cards = build_cards(rows)
    if not cards:
        raise SystemExit("no changed trades could be charted")
    sub = ("Every trade whose outcome changed when the fixed target was capped just "
           "under earlier sessions' highs (BT50). Green dashed = the new buffered target; "
           "pink = every resistance the rule could pick from (today's levels as of the "
           "entry bar, plus the 10 earlier sessions' highs and 5-minute pivots; dotted = "
           "a single unconfirmed pivot), with the one that set the target labelled "
           "<b>frozen resistance</b>; faint = the control's 2R target. The header shows "
           "the paired net change. Descriptive: the window was spent by BT47.")
    data = {"summary": bt32.summarise(cards), "trades": cards, "default_tf": "1m",
            "weak_strength": bt32.STRENGTH_WEAK_BELOW,
            "rules_version": bt32.PATTERN_RULES_VERSION}
    html = bt32.build_html("BT50 — session-resistance target, changed trades", sub, data)
    a.output.write_text(html)
    print(f"wrote {a.output} ({len(cards)} charts of {len(rows)} changed trades)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
