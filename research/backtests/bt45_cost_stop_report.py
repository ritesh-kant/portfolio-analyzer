"""BT45 — the cost-aware stop, drawn trade by trade.

A BT32-format report answering one question visually: when the stop is lifted
at 1R to a price that actually covers the round-trip charges (instead of to the
entry price, which leaves the trade down by them), what changes on the chart?

Each card shows the trade AS THE RULE TRADED IT, plus two extra things the
plain BT32 chart does not have:

  * the **cost stop** itself — a horizontal amber line at the lifted price,
    labelled with how far above entry it sits;
  * the **counterfactual exit** — a hollow grey marker at the price and time
    the very same trade exited WITHOUT the rule, so the money the rule made or
    gave up is visible rather than asserted.

Trades are chosen from the 184 (of 3,139) whose exit the rule actually changed,
spread across the range in both directions. **This is a deliberately curated
sample, not a random one**, so the cards' own P&L is not the strategy's P&L.
The headline carries the FULL population result next to the sample's for
exactly that reason — see BT43 and
research/hypotheses/2026-09-19-cost-aware-breakeven-stop.md, where the rule was
measured and KILLED at −₹0.09/trade (p=0.893).

Usage (from the repository root):
  apps/signal-engine/.venv/bin/python research/backtests/bt45_cost_stop_report.py
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "apps" / "signal-engine"))

import bt32_strategy_report as bt32  # noqa: E402
from src.momentum_trader.engine import _cost_aware_breakeven_stop  # noqa: E402

ASSETS = HERE / "bt45_assets"
STRESS_SLIP = 0.0040
KEY = ["date", "symbol", "entry_time"]


def load(tag: str) -> pd.DataFrame:
    df = pd.read_csv(HERE / f"bt17_trades_{tag}.csv")
    stress = (df["entry"] + df["exit"]) * df["qty"] * STRESS_SLIP
    df["real_net_inr"] = df["gross_inr"] - (df["costs_inr"] - stress)
    return df


def changed_trades(years: list[int]) -> tuple[pd.DataFrame, dict]:
    """Every trade whose exit the rule moved, plus the FULL population stats."""
    frames = []
    pop = {"n": 0, "base": 0.0, "cost": 0.0}
    for y in years:
        b, c = load(f"cs{str(y)[2:]}_base"), load(f"cs{str(y)[2:]}_cost")
        m = b.merge(c, on=KEY, suffixes=("_b", "_c"))
        pop["n"] += len(m)
        pop["base"] += float(m["real_net_inr_b"].sum())
        pop["cost"] += float(m["real_net_inr_c"].sum())
        frames.append(m)
    m = pd.concat(frames, ignore_index=True)
    ch = m[(m["exit_reason_b"] != m["exit_reason_c"])
           | ((m["exit_b"] - m["exit_c"]).abs() > 1e-6)].copy()
    ch["delta"] = ch["real_net_inr_c"] - ch["real_net_inr_b"]
    ch["cost_stop"] = [_cost_aware_breakeven_stop(float(e), int(q), 1)
                       for e, q in zip(ch["entry_b"], ch["qty_b"])]
    ch["cost_stop_lift_pct"] = (ch["cost_stop"] / ch["entry_b"] - 1.0) * 100.0
    pop["changed"] = len(ch)
    pop["better"] = int((ch["delta"] > 0).sum())
    pop["worse"] = int((ch["delta"] < 0).sum())
    pop["delta_per_trade"] = (pop["cost"] - pop["base"]) / max(pop["n"], 1)
    return ch, pop


def spread(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Evenly spaced picks across the sorted range, not just the extremes.

    Taking the biggest few would show the rule at its most flattering and its
    most damning and nothing in between, which is not what it usually does.
    """
    if len(df) <= n:
        return df
    step = (len(df) - 1) / (n - 1)
    return df.iloc[[int(round(i * step)) for i in range(n)]]


def pick(ch: pd.DataFrame, n_saved: int, n_cut: int) -> pd.DataFrame:
    saved = spread(ch[ch["delta"] > 0].sort_values("delta", ascending=False), n_saved)
    cut = spread(ch[ch["delta"] < 0].sort_values("delta"), n_cut)
    out = pd.concat([saved, cut], ignore_index=True)
    return out.sort_values(["date", "symbol"]).reset_index(drop=True)


def build_card(row: pd.Series) -> dict | None:
    """BT32's own card, rebuilt for the COST arm, plus the comparison fields."""
    cost_row = pd.Series({
        "date": row["date"], "symbol": row["symbol"], "setup": row["setup_c"],
        "entry_time": row["entry_time"], "exit_time": row["exit_time_c"],
        "trigger": row["trigger_c"], "entry": row["entry_c"], "stop": row["stop_c"],
        "exit": row["exit_c"], "exit_reason": row["exit_reason_c"], "qty": row["qty_c"],
        "day_chg_pct": row["day_chg_pct_c"], "rvol": row["rvol_c"],
        "gross_pct": row["gross_pct_c"], "gross_inr": row["gross_inr_c"],
        "net_inr": row["net_inr_c"], "net_pct": row["net_pct_c"],
    })
    card = bt32.build_day(cost_row)
    if card is None:
        return None
    card.update({
        "cost_stop": round(float(row["cost_stop"]), 2),
        "cost_stop_lift_pct": round(float(row["cost_stop_lift_pct"]), 3),
        "alt_exit": round(float(row["exit_b"]), 2),
        "alt_exit_time": str(row["exit_time_b"]),
        "alt_exit_reason": str(row["exit_reason_b"]),
        "alt_net_real_inr": round(float(row["real_net_inr_b"]), 2),
        "delta_inr": round(float(row["delta"]), 2),
        "verdict": "saved" if float(row["delta"]) > 0 else "cut short",
    })
    return card


def build_html(title: str, sub: str, data: dict) -> str:
    """BT32's shell, with the extra legend entries and a delta column."""
    css = (ASSETS / "report.css").read_text()
    js = (ASSETS / "report.js").read_text()
    s, pop = data["summary"], data["population"]

    def chip(label: str, value: str, tone: str = "") -> str:
        return f'<span class="chip {tone}"><b>{html.escape(value)}</b> {html.escape(label)}</span>'

    def tone(x: float) -> str:
        return "pos" if x > 0 else "neg" if x < 0 else ""

    chips = "".join([
        chip("charts below", str(s["n"])),
        chip("saved by the rule", str(data["n_saved"]), "pos"),
        chip("cut short by the rule", str(data["n_cut"]), "neg"),
        chip("← CURATED SAMPLE, not the result", "⚠"),
        chip("trades in the real test", f'{pop["n"]:,}'),
        chip("exits the rule changed", f'{pop["changed"]} ({pop["better"]} better, '
             f'{pop["worse"]} worse)'),
        chip("REAL VERDICT: net / trade", f'{pop["delta_per_trade"]:+.2f} ₹',
             tone(pop["delta_per_trade"])),
        chip("p-value", "0.893"),
    ])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>{css}
.cmp{{display:flex;gap:18px;flex-wrap:wrap;margin:6px 0 2px;font-size:12px}}
.cmp div{{background:#0f1722;border:1px solid #1e2b3a;border-radius:6px;padding:5px 9px}}
.cmp b{{color:#e6edf5}} .cmp .g{{color:#2dd4bf}} .cmp .r{{color:#fb7185}}
.warn{{background:#2a1f08;border:1px solid #7c5e10;color:#fde68a;padding:10px 14px;
border-radius:8px;margin:10px 18px;font-size:13px;line-height:1.5}}</style></head><body>
<header>
  <h1>{html.escape(title)}</h1>
  <p>{sub}</p>
</header>
<div class="warn"><b>Read this before the charts.</b> These {s["n"]} trades were
<b>hand-picked</b> from the {pop["changed"]} (out of {pop["n"]:,}) whose exit the rule
changed at all, chosen to show both what it saves and what it costs. Their combined
P&amp;L means nothing on its own. The measured answer across the whole
{pop["n"]:,}-trade test is <b>{pop["delta_per_trade"]:+.2f} ₹ per trade, p = 0.893</b>
— indistinguishable from zero, and the rule was <b>killed</b> on that basis. What the
charts are for is seeing <i>why</i> it nets out: the amber line banks a small win on
trades that were going to scratch, and ends trades that were still going.</div>
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
      <p class="hint">Click any row to jump to its chart. <b>Δ ₹</b> is what the
      cost-aware stop was worth on that trade, at real charges.</p>
      <div class="scroll"><table id="det"><thead><tr>
        <th data-k="date">date</th><th data-k="symbol">symbol</th>
        <th data-k="entry_time">in</th><th data-k="exit_reason">exit</th>
        <th data-k="gross_pct" class="num">gross%</th>
        <th data-k="delta_inr" class="num">Δ ₹</th>
      </tr></thead><tbody></tbody></table></div>
    </div>
  </div>
</div>
<script>var DATA = {json.dumps(data)};</script>
<script>{js}</script>
</body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2023,2024")
    ap.add_argument("--saved", type=int, default=5)
    ap.add_argument("--cut", type=int, default=5)
    ap.add_argument("--output", default="research/backtests/bt45_cost_stop_report.html")
    ap.add_argument("--title", default="BT45 — the cost-aware stop, trade by trade")
    a = ap.parse_args()

    ch, pop = changed_trades([int(y) for y in a.years.split(",")])
    chosen = pick(ch, a.saved, a.cut)
    cards, skipped = [], 0
    for _, r in chosen.iterrows():
        c = build_card(r)
        if c is None:
            skipped += 1
            continue
        cards.append(c)
    if not cards:
        print("no trade could be rebuilt from the bar cache")
        return 1

    summary = bt32.summarise(cards)
    data = {
        "trades": cards, "summary": summary, "population": pop,
        # count on the verdict, which is derived from the UNROUNDED delta: a
        # trade worth a few paise still belongs on one side or the other.
        "n_saved": sum(1 for c in cards if c["verdict"] == "saved"),
        "n_cut": sum(1 for c in cards if c["verdict"] == "cut short"),
        "weak_strength": bt32.STRENGTH_WEAK_BELOW,
        "rules_version": bt32.PATTERN_RULES_VERSION,
    }
    sub = (
        "Each card is one real trade replayed twice: once with the cost-aware stop and once "
        "without. The solid <b>amber</b> line is the cost stop — the price the stop is lifted to "
        "at 1R so that selling there covers the actual charges. The hollow grey marker is where "
        "the same trade exited <b>without</b> the rule. Support/resistance is computed "
        "<b>as of the entry bar only</b>. Costs are shown twice: bt17's stressed number and a "
        "realistic one recomputed per trade with the itemised MIS model. "
        "Descriptive report of an already-measured, already-killed rule — not a new test."
    )
    out = Path(a.output) if Path(a.output).is_absolute() else ROOT / a.output
    out.write_text(build_html(a.title, sub, data))
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB, {len(cards)} charts, "
          f"{skipped} skipped)")
    for c in cards:
        print(f"  {c['date']} {c['symbol']:<12} entry {c['entry']:>8.2f} "
              f"cost-stop {c['cost_stop']:>8.2f} (+{c['cost_stop_lift_pct']:.2f}%)  "
              f"rule: {c['exit']:>8.2f} {c['exit_reason']:<18} "
              f"no-rule: {c['alt_exit']:>8.2f} {c['alt_exit_reason']:<18} "
              f"Δ {c['delta_inr']:+8.2f}  [{c['verdict']}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
