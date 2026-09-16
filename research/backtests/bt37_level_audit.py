"""BT37 — level audit: what the engine drew as resistance, next to what it missed.

Takes the live trades of one session out of `mt_positions` and renders, per
trade, the level set the engine actually used at its decision bar against the
same set plus volume-by-price shelves, with the volume profile beside the chart
so every shelf can be checked against the evidence that produced it.

Why this report exists — EIHOTEL, 2026-09-16, entry 290.60:
  the gate's nearest structural resistance above the 290.40 trigger was ₹300,
  a round number 3.3% away, while 27% of the session's volume had already
  traded between the trigger and the 2R target. A pivot needs PIVOT_K lower
  bars on each side, so it cannot see supply built inside a fast move: one
  impulse bar blinds the detector for K bars either side, which is exactly
  where the sellers that stopped the run are.

Bars come from the position's own `chart.bars` — the feed the engine read, not
a reconstruction — so the chart ends where the trade did. Levels are derived
from bars STRICTLY BEFORE the entry bar, which is what the confirmation saw.

This report makes no P&L claim. It answers one question: are the lines right?

Example (from the repository root):
  apps/signal-engine/.venv/bin/python research/backtests/bt37_level_audit.py \
      --date 2026-09-16
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "signal-engine"))

from src.momentum_trader.indicators import atr  # noqa: E402
from src.momentum_trader.levels import (  # noqa: E402
    derive_levels,
    is_structural,
    volume_by_price,
    volume_shelf_levels,
    _shelf_bucket,
)
from src.news_trader.trailing_sl import calc_costs  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
COLS = ["open", "high", "low", "close", "volume"]


def mongo_db():
    from pymongo import MongoClient

    uri = None
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("MONGODB_URI="):
            uri = line.split("=", 1)[1].strip()
    if not uri:
        raise SystemExit("MONGODB_URI not found in .env")
    return MongoClient(uri)["portfolio_analyzer"]


def load_positions(day: dt.date) -> list[dict]:
    db = mongo_db()
    lo = dt.datetime(day.year, day.month, day.day)
    return list(db.mt_positions.find({"entry_time": {"$gte": lo,
                                                     "$lt": lo + dt.timedelta(days=1)}})
                .sort("entry_time", 1))


def bars_of(pos: dict, day: dt.date) -> pd.DataFrame:
    """The session's 1-minute bars as the engine recorded them."""
    raw = (pos.get("chart") or {}).get("bars") or []
    if not raw:
        return pd.DataFrame(columns=COLS)
    df = pd.DataFrame(raw)
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time").sort_index()
    df = df[df.index.date == day]
    return df[COLS].astype(float)


def level_rows(levels, entry: float) -> list[dict]:
    seen: set[tuple[float, str]] = set()
    out = []
    for x in sorted(levels, key=lambda z: -z.strength):
        key = (round(x.price, 2), x.kind)
        if key in seen:
            continue
        seen.add(key)
        out.append({"price": round(x.price, 2), "kind": x.kind,
                    "touches": int(x.touches), "structural": is_structural(x),
                    "side": "resistance" if x.price > entry else "support"})
    return out


def nearest_above(levels, price: float):
    above = [x for x in levels if x.price > price and is_structural(x)]
    return min(above, key=lambda x: x.price) if above else None


def build_trade(pos: dict, day: dt.date) -> dict | None:
    bars = bars_of(pos, day)
    if len(bars) < 20:
        return None
    entry_at = pos["entry_time"].replace(tzinfo=dt.timezone.utc).astimezone(IST)
    exit_at = pos["exit_time"].replace(tzinfo=dt.timezone.utc).astimezone(IST)
    entry_bar = entry_at.replace(second=0, microsecond=0)

    # What the confirmation saw: bars strictly before the entry minute.
    prior = bars[bars.index < pd.Timestamp(entry_bar)]
    if len(prior) < 20:
        return None

    entry = float(pos["entry_price"])
    stop = float(pos["stop"])
    trigger = float(pos.get("trigger_px") or entry)
    risk = trigger - stop

    shelf_atr = float(atr(prior).iloc[-1]) if len(prior) >= 15 else None
    old = derive_levels(prior)
    shelves = volume_shelf_levels(prior, shelf_atr)
    new = old + shelves

    before = nearest_above(old, trigger)
    after = nearest_above(new, trigger)
    bucket = _shelf_bucket(prior, shelf_atr)
    profile = volume_by_price(prior, bucket)
    total = float(profile.sum()) or 1.0

    qty = int(pos["qty"])
    gross = float(pos["gross_inr"])
    real_cost = calc_costs(entry, float(pos["exit_price"]), qty, direction="long")["total"]

    def lvl_json(x):
        if x is None:
            return None
        return {"price": round(float(x.price), 2), "kind": x.kind,
                "headroom": round(float(x.price) - trigger, 2)}

    return {
        "symbol": str(pos["symbol"]),
        "date": str(day),
        "setup": str(pos.get("setup") or ""),
        "entry_time": entry_at.strftime("%H:%M"),
        "exit_time": exit_at.strftime("%H:%M"),
        "trigger": round(trigger, 2), "entry": round(entry, 2), "stop": round(stop, 2),
        "target": round(float(pos["target"]), 2),
        "exit": round(float(pos["exit_price"]), 2),
        "exit_reason": str(pos["exit_reason"]),
        "qty": qty,
        "day_chg_pct": round(float(pos.get("day_chg_pct") or 0.0), 2),
        "rvol": round(float(pos.get("rvol") or 0.0), 2),
        "risk": round(risk, 2),
        "gross_inr": round(gross, 2),
        "stress_cost_inr": round(float(pos["costs_inr"]), 2),
        "net_stress_inr": round(float(pos["net_inr"]), 2),
        "real_cost_inr": round(real_cost, 2),
        "net_real_inr": round(gross - real_cost, 2),
        "bucket": round(bucket, 2),
        "atr_1m": round(shelf_atr, 2) if shelf_atr else None,
        "levels_old": level_rows(old, entry),
        "levels_new": level_rows(new, entry),
        "shelves": [round(float(x.price), 2) for x in sorted(shelves, key=lambda z: z.price)],
        "profile": [[round(float(p), 2), round(float(v) / total * 100.0, 2)]
                    for p, v in profile.items() if v > 0],
        "verdict": {
            "risk": round(risk, 2),
            "before": lvl_json(before),
            "after": lvl_json(after),
            "refuse": bool(after is not None and (after.price - trigger) < risk),
            "changed": bool(after is not None and before is not None
                            and abs(after.price - before.price) > 1e-9),
        },
        "decision_i": int(len(prior) - 1),
        "entry_i": int(bars.index.searchsorted(pd.Timestamp(entry_bar))),
        "bars": [[ts.strftime("%H:%M"), round(float(r.open), 2), round(float(r.high), 2),
                  round(float(r.low), 2), round(float(r.close), 2), float(r.volume)]
                 for ts, r in bars.iterrows()],
    }


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

CSS = """
:root{--bg:#0b0f14;--card:#121821;--ink:#e6edf3;--dim:#8b98a5;--line:#1f2a37;
      --up:#2dd4bf;--dn:#fb7185;--res:#f472b6;--sup:#38bdf8;--shelf:#fbbf24;--acc:#60a5fa}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
     font:14px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:24px 16px 80px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--dim);font-size:13px;margin-bottom:18px}
.note{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--acc);
      border-radius:8px;padding:12px 14px;margin:0 0 18px;color:var(--dim);font-size:13px}
.note b{color:var(--ink)}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:0 0 16px}
.bar button{background:var(--card);color:var(--dim);border:1px solid var(--line);
            border-radius:999px;padding:5px 12px;font-size:12px;cursor:pointer}
.bar button.on{color:var(--ink);border-color:var(--acc)}
.jump a{color:var(--dim);text-decoration:none;font-size:12px;margin-right:10px}
.jump a:hover{color:var(--ink)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
      padding:14px 16px;margin:0 0 16px}
.card.refuse{border-left:3px solid var(--shelf)}
.card h2{font-size:16px;margin:0 0 2px;display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.tag{font-size:11px;padding:2px 8px;border-radius:999px;border:1px solid var(--line);
     color:var(--dim);font-weight:400}
.tag.refuse{color:#111;background:var(--shelf);border-color:var(--shelf);font-weight:600}
.tag.take{color:var(--up);border-color:var(--up)}
.meta{color:var(--dim);font-size:12px;margin:0 0 10px}
.verdict{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:10px 0 4px}
.verdict div{background:#0e141c;border:1px solid var(--line);border-radius:8px;padding:9px 11px;
             font-size:12px}
.verdict .k{color:var(--dim);display:block;margin-bottom:3px;font-size:11px;
            text-transform:uppercase;letter-spacing:.04em}
.verdict b{font-size:15px}
.mono{font-variant-numeric:tabular-nums}
svg{display:block;width:100%;height:auto;touch-action:none}
.legend{color:var(--dim);font-size:11px;margin-top:6px;display:flex;gap:14px;flex-wrap:wrap}
.legend i{display:inline-block;width:10px;height:2px;vertical-align:middle;margin-right:4px}
table{width:100%;border-collapse:collapse;font-size:12px;margin-top:6px}
th,td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:right}
th:first-child,td:first-child{text-align:left}
th{color:var(--dim);font-weight:500;cursor:pointer;user-select:none}
.pos{color:var(--up)}.neg{color:var(--dn)}
.full{position:fixed;inset:0;background:var(--bg);z-index:9;overflow:auto;padding:20px}
.fsbtn{background:none;border:1px solid var(--line);color:var(--dim);border-radius:6px;
       font-size:11px;padding:2px 8px;cursor:pointer}
#tip{position:fixed;pointer-events:none;background:#0e141c;border:1px solid var(--line);
     border-radius:6px;padding:6px 9px;font-size:11px;color:var(--ink);display:none;z-index:99;
     white-space:pre}
@media(max-width:700px){.verdict{grid-template-columns:1fr}}
"""

JS = r"""
var DATA = __DATA__;
var NS = "http://www.w3.org/2000/svg";
function el(n, a, p, t) {
  var e = document.createElementNS(NS, n);
  for (var k in a) e.setAttribute(k, a[k]);
  if (t != null) e.textContent = t;
  if (p) p.appendChild(e);
  return e;
}
function ema(v, s) {
  var k = 2 / (s + 1), out = [], prev = null;
  v.forEach(function (x, i) { prev = i ? x * k + prev * (1 - k) : x; out.push(prev); });
  return out;
}
function money(x) { return (x < 0 ? "-" : "") + "₹" + Math.abs(x).toFixed(0); }

/* one trade -> one SVG: price + volume profile, volume, MACD */
function draw(tr, host, wide) {
  var W = wide ? 1600 : 1080, R = 58, L = 8, PROF = 96;
  var PT = 16, PH = wide ? 520 : 330, VT = PT + PH + 16, VH = 66,
      MT = VT + VH + 16, MH = 62, H = MT + MH + 18;
  var svg = el("svg", { viewBox: "0 0 " + W + " " + H }, host);
  var b = tr.bars, n = b.length;
  var px0 = L, px1 = W - R - PROF - 10;

  var lows = b.map(function (r) { return r[3]; }), highs = b.map(function (r) { return r[2]; });
  var vMin = Math.min.apply(null, lows.concat([tr.stop, tr.exit]));
  var vMax = Math.max.apply(null, highs.concat([tr.target, tr.entry]));
  tr.levels_new.forEach(function (Lv) {
    if (Lv.kind === "shelf") { vMin = Math.min(vMin, Lv.price); vMax = Math.max(vMax, Lv.price); }
  });
  var pad = (vMax - vMin) * 0.06 || 1;
  vMin -= pad; vMax += pad;
  function Y(p) { return PT + PH - (p - vMin) / (vMax - vMin) * PH; }
  function X(i) { return px0 + (i + 0.5) * (px1 - px0) / n; }
  var cw = Math.max(1.2, (px1 - px0) / n * 0.62);

  /* holding period + decision marker */
  el("rect", { x: X(tr.entry_i), y: PT, width: Math.max(2, X(n - 1) - X(tr.entry_i)),
               height: PH, fill: "#2dd4bf", opacity: .06 }, svg);
  el("line", { x1: X(tr.decision_i), x2: X(tr.decision_i), y1: PT, y2: PT + PH,
               stroke: "#94a3b8", "stroke-width": 1, "stroke-dasharray": "3 3",
               "stroke-opacity": .55 }, svg);
  el("text", { x: X(tr.decision_i) + 4, y: PT + 11, fill: "#94a3b8", "font-size": 10 },
     svg, "decision bar");

  /* volume-by-price, the evidence behind every shelf */
  var pmax = 0;
  tr.profile.forEach(function (r) { pmax = Math.max(pmax, r[1]); });
  var bh = Math.max(2, PH / ((vMax - vMin) / tr.bucket) - 1);
  tr.profile.forEach(function (r) {
    var w = r[1] / pmax * (PROF - 8);
    var onShelf = tr.shelves.some(function (s) { return Math.abs(s - r[0]) < tr.bucket / 2; });
    el("rect", { x: W - R - PROF, y: Y(r[0]) - bh / 2, width: Math.max(1, w), height: bh,
                 fill: onShelf ? "#fbbf24" : "#475569",
                 opacity: onShelf ? .85 : .5 }, svg)
      .setAttribute("data-tip", "₹" + r[0].toFixed(2) + "  " + r[1].toFixed(1) +
                    "% of volume before entry");
  });
  el("text", { x: W - R - PROF, y: PT + PH + 12, fill: "#8b98a5", "font-size": 10 },
     svg, "volume by price (₹" + tr.bucket.toFixed(2) + " buckets)");

  /* levels: old set dashed-grey, shelves solid amber */
  var drawn = [];
  tr.levels_new.forEach(function (Lv) {
    if (Lv.price < vMin || Lv.price > vMax) return;
    var shelf = Lv.kind === "shelf";
    var col = shelf ? "#fbbf24" : (Lv.side === "resistance" ? "#f472b6" : "#38bdf8");
    el("line", { x1: px0, x2: px1, y1: Y(Lv.price), y2: Y(Lv.price), stroke: col,
                 "stroke-width": shelf ? 1.6 : 1,
                 "stroke-dasharray": Lv.structural ? (shelf ? "" : "6 3") : "2 4",
                 "stroke-opacity": shelf ? .95 : .45 }, svg);
    drawn.push({ y: Y(Lv.price), col: col,
                 t: Lv.price.toFixed(2) + " " + Lv.kind + (Lv.structural ? "" : " ?") });
  });
  var lastY = -99;
  drawn.sort(function (a, c) { return a.y - c.y; }).forEach(function (d) {
    var ly = Math.max(d.y + 3, lastY + 11); lastY = ly;
    el("text", { x: px1 - 2, y: ly, "text-anchor": "end", fill: d.col, "font-size": 10 },
       svg, d.t);
  });

  /* trade prices */
  [["entry", tr.entry, "#e6edf3", "BUY " + tr.entry],
   ["stop", tr.stop, "#fb7185", "STOP " + tr.stop],
   ["target", tr.target, "#2dd4bf", "2R " + tr.target],
   ["exit", tr.exit, "#a78bfa", "EXIT " + tr.exit]].forEach(function (r) {
    if (r[1] < vMin || r[1] > vMax) return;
    el("line", { x1: px0, x2: px1, y1: Y(r[1]), y2: Y(r[1]), stroke: r[2],
                 "stroke-width": 1, "stroke-dasharray": "4 3", "stroke-opacity": .8 }, svg);
    el("text", { x: px0 + 3, y: Y(r[1]) - 3, fill: r[2], "font-size": 10 }, svg, r[3]);
  });

  /* EMA9 / EMA20 / VWAP */
  var cl = b.map(function (r) { return r[4]; });
  var e9 = ema(cl, 9), e20 = ema(cl, 20), cpv = 0, cv = 0, vw = [];
  b.forEach(function (r) {
    var tp = (r[2] + r[3] + r[4]) / 3; cpv += tp * r[5]; cv += r[5];
    vw.push(cv ? cpv / cv : r[4]);
  });
  [[e9, "#fbbf24", .55], [e20, "#a78bfa", .55], [vw, "#38bdf8", .55]].forEach(function (s) {
    el("path", { d: s[0].map(function (v, i) { return (i ? "L" : "M") + X(i) + " " + Y(v); }).join(" "),
                 fill: "none", stroke: s[1], "stroke-width": 1, "stroke-opacity": s[2] }, svg);
  });

  /* candles */
  b.forEach(function (r, i) {
    var up = r[4] >= r[1], col = up ? "#2dd4bf" : "#fb7185", cx = X(i);
    el("line", { x1: cx, x2: cx, y1: Y(r[2]), y2: Y(r[3]), stroke: col, "stroke-width": 1 }, svg);
    var t = Y(Math.max(r[1], r[4])), bo = Y(Math.min(r[1], r[4]));
    el("rect", { x: cx - cw / 2, y: t, width: cw, height: Math.max(1, bo - t), fill: col }, svg)
      .setAttribute("data-tip", r[0] + "\nO " + r[1] + "  H " + r[2] +
                    "\nL " + r[3] + "  C " + r[4] + "\nvol " + r[5].toLocaleString());
  });

  /* price axis */
  for (var g = 0; g <= 4; g++) {
    var p = vMin + (vMax - vMin) * g / 4;
    el("text", { x: W - R + 4, y: Y(p) + 3, fill: "#8b98a5", "font-size": 10 }, svg, p.toFixed(2));
  }

  /* volume */
  var vmax = Math.max.apply(null, b.map(function (r) { return r[5]; })) || 1;
  el("text", { x: px0, y: VT - 3, fill: "#8b98a5", "font-size": 10 }, svg, "volume");
  b.forEach(function (r, i) {
    var h = r[5] / vmax * VH;
    el("rect", { x: X(i) - cw / 2, y: VT + VH - h, width: cw, height: h,
                 fill: r[4] >= r[1] ? "#2dd4bf" : "#fb7185", opacity: .5 }, svg);
  });

  /* MACD(12,26,9) */
  var m12 = ema(cl, 12), m26 = ema(cl, 26);
  var line = m12.map(function (v, i) { return v - m26[i]; });
  var sig = ema(line, 9), hist = line.map(function (v, i) { return v - sig[i]; });
  var hmax = Math.max.apply(null, hist.map(Math.abs)) || 1;
  el("text", { x: px0, y: MT - 3, fill: "#8b98a5", "font-size": 10 }, svg, "MACD 12/26/9");
  el("line", { x1: px0, x2: px1, y1: MT + MH / 2, y2: MT + MH / 2, stroke: "#1f2a37" }, svg);
  hist.forEach(function (v, i) {
    var h = Math.abs(v) / hmax * (MH / 2);
    el("rect", { x: X(i) - cw / 2, y: v >= 0 ? MT + MH / 2 - h : MT + MH / 2,
                 width: cw, height: Math.max(.6, h),
                 fill: v >= 0 ? "#2dd4bf" : "#fb7185", opacity: .6 }, svg);
  });

  /* time axis */
  var step = Math.ceil(n / 8);
  for (var i = 0; i < n; i += step)
    el("text", { x: X(i), y: H - 4, fill: "#8b98a5", "font-size": 10, "text-anchor": "middle" },
       svg, b[i][0]);
  return svg;
}

function card(tr) {
  var v = tr.verdict, refuse = v.refuse;
  var d = document.createElement("div");
  d.className = "card" + (refuse ? " refuse" : "");
  d.id = "t-" + tr.symbol.replace(/[^A-Za-z0-9]/g, "");
  var before = v.before ? v.before.price.toFixed(2) + " (" + v.before.kind + ")" : "none";
  var after = v.after ? v.after.price.toFixed(2) + " (" + v.after.kind + ")" : "none";
  var bh = v.before ? v.before.headroom.toFixed(2) : "∞";
  var ah = v.after ? v.after.headroom.toFixed(2) : "∞";
  d.innerHTML =
    '<h2>' + tr.symbol +
    '<span class="tag ' + (refuse ? "refuse" : "take") + '">' +
      (refuse ? "shelf would REFUSE" : "shelf leaves it TAKEable") + '</span>' +
    '<span class="tag">' + tr.exit_reason + '</span>' +
    '<button class="fsbtn">full screen</button></h2>' +
    '<p class="meta mono">' + tr.entry_time + " → " + tr.exit_time +
      " · trigger " + tr.trigger + " · entry " + tr.entry +
      " · stop " + tr.stop + " (1R = ₹" + tr.risk + ")" +
      " · 2R " + tr.target + " · day " + tr.day_chg_pct + "% · RVOL " + tr.rvol +
      " · 1m ATR ₹" + (tr.atr_1m == null ? "-" : tr.atr_1m) + '</p>' +
    '<div class="verdict">' +
      '<div><span class="k">level set as shipped</span><b class="mono">' + before + '</b>' +
        '<br><span class="mono">headroom ₹' + bh + ' vs 1R ₹' + v.risk + '</span></div>' +
      '<div><span class="k">with volume shelves</span><b class="mono">' + after + '</b>' +
        '<br><span class="mono">headroom ₹' + ah + ' vs 1R ₹' + v.risk + '</span></div>' +
    '</div>';
  var chart = document.createElement("div");
  d.appendChild(chart);
  draw(tr, chart, false);
  var lg = document.createElement("div");
  lg.className = "legend";
  lg.innerHTML =
    '<span><i style="background:#fbbf24"></i>volume shelf (new)</span>' +
    '<span><i style="background:#f472b6"></i>resistance, shipped set</span>' +
    '<span><i style="background:#38bdf8"></i>support / VWAP</span>' +
    '<span><i style="background:#a78bfa"></i>EMA20 &amp; exit</span>' +
    '<span>dashed = non-structural (cannot block an entry)</span>';
  d.appendChild(lg);
  var t = document.createElement("table");
  t.innerHTML =
    "<tr><th>P&amp;L</th><th>gross</th><th>cost</th><th>net</th></tr>" +
    '<tr><td>as booked (stressed)</td><td class="mono">' + money(tr.gross_inr) +
      '</td><td class="mono">' + money(tr.stress_cost_inr) + '</td><td class="mono ' +
      (tr.net_stress_inr >= 0 ? "pos" : "neg") + '">' + money(tr.net_stress_inr) + "</td></tr>" +
    '<tr><td>at real MIS charges</td><td class="mono">' + money(tr.gross_inr) +
      '</td><td class="mono">' + money(tr.real_cost_inr) + '</td><td class="mono ' +
      (tr.net_real_inr >= 0 ? "pos" : "neg") + '">' + money(tr.net_real_inr) + "</td></tr>";
  d.appendChild(t);
  d.querySelector(".fsbtn").onclick = function () {
    var f = document.createElement("div");
    f.className = "full";
    var h = document.createElement("h2");
    h.textContent = tr.symbol + " " + tr.date;
    h.style.color = "#e6edf3";
    f.appendChild(h);
    draw(tr, f, true);
    f.onclick = function () { f.remove(); };
    document.body.appendChild(f);
  };
  return d;
}

var tip = document.getElementById("tip");
document.addEventListener("mouseover", function (e) {
  var t = e.target.getAttribute && e.target.getAttribute("data-tip");
  if (!t) { tip.style.display = "none"; return; }
  tip.textContent = t;
  tip.style.display = "block";
  tip.style.left = (e.clientX + 14) + "px";
  tip.style.top = (e.clientY + 14) + "px";
});

var host = document.getElementById("cards"), filter = "all";
function render() {
  host.innerHTML = "";
  DATA.trades.filter(function (t) {
    return filter === "all" || (filter === "refuse") === t.verdict.refuse;
  }).forEach(function (t) { host.appendChild(card(t)); });
}
document.querySelectorAll(".bar button").forEach(function (b) {
  b.onclick = function () {
    document.querySelectorAll(".bar button").forEach(function (x) { x.classList.remove("on"); });
    b.classList.add("on");
    filter = b.dataset.f;
    render();
  };
});
render();
"""


def build_html(day: str, trades: list[dict]) -> str:
    refused = [t for t in trades if t["verdict"]["refuse"]]
    jump = " ".join(
        '<a href="#t{id}">{s}</a>'.format(id="".join(ch for ch in t["symbol"] if ch.isalnum()),
                                          s=html.escape(t["symbol"]))
        for t in trades)
    net_real = sum(t["net_real_inr"] for t in trades)
    net_refused = sum(t["net_real_inr"] for t in refused)
    payload = json.dumps({"trades": trades}, separators=(",", ":"))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BT37 level audit &middot; {html.escape(day)}</title>
<style>{CSS}</style></head><body>
<div class="wrap">
<h1>BT37 &middot; level audit</h1>
<p class="sub">{html.escape(day)} &middot; {len(trades)} live trades &middot;
 levels derived from bars strictly before each entry bar, from the position's own recorded feed</p>
<div class="note">
<b>What to check.</b> The amber lines are volume shelves &mdash; prices where an unusual share of
the session's volume changed hands before the entry. The histogram on the right of each chart is
the evidence: shelves sit on its peaks. Compare them with the lines you would draw by hand.
The pink and blue lines are the level set as shipped today; dashed ones are non-structural and
cannot block an entry.<br><br>
<b>What this is not.</b> No P&amp;L claim. {len(refused)} of {len(trades)} entries would have been
refused for insufficient headroom, and those {len(refused)} were {"" if net_refused < 0 else "net "}
{money_py(net_refused)} at real charges against {money_py(net_real)} for the day &mdash; but four
trades on one session cannot measure a rule, and the parameters were chosen while these outcomes
were visible. Whether the rule pays needs a window we have not spent.
</div>
<div class="bar">
  <button class="on" data-f="all">all ({len(trades)})</button>
  <button data-f="refuse">would refuse ({len(refused)})</button>
  <button data-f="keep">would keep ({len(trades) - len(refused)})</button>
  <span class="jump">{jump}</span>
</div>
<div id="cards"></div>
</div>
<div id="tip"></div>
<script>{JS.replace("__DATA__", payload)}</script>
</body></html>
"""


def money_py(x: float) -> str:
    return ("-" if x < 0 else "") + "₹" + f"{abs(x):,.0f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", required=True, help="session to audit, YYYY-MM-DD")
    ap.add_argument("--out", default=None, help="output HTML path")
    a = ap.parse_args()

    day = dt.date.fromisoformat(a.date)
    positions = load_positions(day)
    if not positions:
        print(f"no mt_positions with an entry on {day}")
        return 1
    trades = []
    for pos in positions:
        built = build_trade(pos, day)
        if built is None:
            print(f"  skipped {pos.get('symbol')}: too few recorded bars before entry")
            continue
        trades.append(built)
    if not trades:
        print("nothing to render")
        return 1

    out = Path(a.out) if a.out else (Path(__file__).resolve().parent
                                     / f"bt37_level_audit_{day}.html")
    out.write_text(build_html(str(day), trades), encoding="utf-8")

    print(f"{len(trades)} trades -> {out}")
    for t in trades:
        v = t["verdict"]
        before = f"{v['before']['price']:.2f} ({v['before']['kind']})" if v["before"] else "none"
        after = f"{v['after']['price']:.2f} ({v['after']['kind']})" if v["after"] else "none"
        print(f"  {t['symbol']:10s} shipped: {before:22s} -> with shelves: {after:22s}"
              f"  {'REFUSE' if v['refuse'] else 'take'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
