/* BT49 — per-setup charts (adapted from BT32/BT48) with the same feature set as the /momentum review
   chart: 1m/5m timeframes, horizontal + vertical zoom, drag-pan on both axes,
   ctrl+wheel / trackpad pinch, keyboard control, a crosshair with per-panel
   readouts, candlestick-formation overlays, and full screen.

   Indicators are computed HERE from the raw 1-minute bars, exactly as
   momentum-trade-chart.tsx does, so the two views cannot drift apart. */
var NS = "http://www.w3.org/2000/svg";
var W = 1000, H = 580, L = 64, R = 18;
var PRICE_TOP = 20, PRICE_H = 292;
var MACD_TOP = 350, MACD_H = 92;
var VOL_TOP = 478, VOL_H = 66;
var MAX_ZOOM = 8, MIN_BARS = 15;
var DEFAULT_ZOOM = { "1m": 6, "5m": 2 };
var WEAK = DATA.weak_strength;
var cards = [], tf = "1m";

function el(tag, attrs, parent, text) {
  var e = document.createElementNS(NS, tag);
  for (var k in attrs) e.setAttribute(k, attrs[k]);
  if (text != null) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
}
function h(tag, attrs, parent, text) {
  var e = document.createElement(tag);
  for (var k in attrs) { if (k === "class") e.className = attrs[k]; else e.setAttribute(k, attrs[k]); }
  if (text != null) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
}
function pct(x) { return (x >= 0 ? "+" : "") + x.toFixed(2) + "%"; }
function inr(x) { return (x < 0 ? "−$" : "$") + Math.abs(x).toFixed(2); }
function money(x) { return "$" + x.toFixed(x < 10 ? 3 : 2); }
function cls(x) { return x > 0 ? "pos" : x < 0 ? "neg" : ""; }
function nice(s) { return String(s).split("_").join(" "); }
function fmtVol(v) {
  return v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? (v / 1e3).toFixed(1) + "K" : Math.round(v);
}

/* ---------- indicators (mirrors momentum-trade-chart.tsx `points`) ---------- */
function ema(vals, span) {
  var alpha = 2 / (span + 1), v = null;
  return vals.map(function (c, i) {
    v = v === null ? c : c * alpha + v * (1 - alpha);
    return i < span - 1 ? null : v;
  });
}
/* An EMA seeded at the first non-null input, valid only once `minPeriods`
   real observations have gone in. This is what pandas' ewm(adjust=False,
   min_periods=N) does, and it is why the MACD here differs slightly from the
   /momentum chart: that one feeds 0 in place of the MACD line's warm-up NaNs,
   which drags the signal line toward zero for its first ~30 bars. The engine's
   own exit logic reads the pandas version, so a backtest review chart has to
   match the engine, not reproduce that. */
function emaSparse(vals, span, minPeriods) {
  var alpha = 2 / (span + 1), v = null, seen = 0;
  return vals.map(function (x) {
    if (x === null) return null;
    v = v === null ? x : x * alpha + v * (1 - alpha);
    seen += 1;
    return seen < minPeriods ? null : v;
  });
}
function points(bars) {
  var closes = bars.map(function (b) { return b.c; });
  var e9 = ema(closes, 9), e20 = ema(closes, 20);
  /* The guide's third moving average. Sparse so it stays null until 200
     bars exist, rather than drawing a meaningless seeded curve. */
  var e200 = emaSparse(closes, 200, 200);
  var fast = ema(closes, 12), slow = ema(closes, 26);
  var macd = fast.map(function (v, i) {
    return v === null || slow[i] === null ? null : v - slow[i];
  });
  var sig = emaSparse(macd, 9, 9);
  var cumPv = 0, cumVol = 0;
  return bars.map(function (b, i) {
    cumPv += ((b.h + b.l + b.c) / 3) * b.v;
    cumVol += b.v;
    return {
      t: b.t, o: b.o, h: b.h, l: b.l, c: b.c, v: b.v,
      ema9: e9[i], ema20: e20[i], ema200: e200[i],
      vwap: cumVol ? cumPv / cumVol : null,
      macd: macd[i], signal: sig[i],
      hist: macd[i] === null || sig[i] === null ? null : macd[i] - sig[i]
    };
  });
}
/* 5-minute buckets aligned to the session open (09:30 ET here), matching engine.resample_5m. */
function fiveMinute(raw) {
  var out = [], cur = null, key = null;
  raw.forEach(function (b) {
    var hh = +b.t.slice(0, 2), mm = +b.t.slice(3, 5);
    var k = hh * 60 + Math.floor(mm / 5) * 5;
    if (k !== key) {
      if (cur) out.push(cur);
      key = k;
      var lab = String(Math.floor(k / 60)).padStart(2, "0") + ":" + String(k % 60).padStart(2, "0");
      cur = { t: lab, o: b.o, h: b.h, l: b.l, c: b.c, v: b.v };
    } else {
      cur.h = Math.max(cur.h, b.h); cur.l = Math.min(cur.l, b.l);
      cur.c = b.c; cur.v += b.v;
    }
  });
  if (cur) out.push(cur);
  return out;
}

/* ---------- BT49: one card per guide-shaped setup; bars shared per symbol ---------- */
function rawBars(tr) {
  return DATA.bars[tr.symbol].map(function (r) {
    return { t: r[0], o: r[1], h: r[2], l: r[3], c: r[4], v: r[5] };
  });
}
/* index of the bar CONTAINING a HH:MM stamp (last bar starting at or before it) */
function barIndex(data, hhmm) {
  var best = 0;
  for (var i = 0; i < data.length; i++) if (data[i].t <= hhmm) best = i;
  return best;
}
function traded(tr) { return tr.status === "traded"; }

/* ---------- per-card view state ---------- */
function ensureSeries(c) {
  if (c.series && c.series.tf === tf) return c.series;
  var raw = rawBars(c.tr);
  var data = points(tf === "5m" ? fiveMinute(raw) : raw);
  /* On the 1-minute view the MACD panel is the ENGINE's series (warmed with
     prior sessions) — the exact numbers the MACD gates read. */
  var eng = DATA.eng[c.tr.symbol];
  if (tf === "1m" && eng) data.forEach(function (b, i) {
    var e = eng[i]; b.macd = e[0]; b.signal = e[1]; b.hist = e[2];
  });
  c.series = {
    tf: tf, all: data,
    decFull: barIndex(data, c.tr.time),
    firstFull: barIndex(data, DATA.first[c.tr.symbol]),
    entryFull: traded(c.tr) ? barIndex(data, c.tr.entry_time) : -1,
    exitFull: traded(c.tr) ? barIndex(data, c.tr.exit_time) : -1
  };
  resetView(c);
  return c.series;
}
function resetView(c) {
  c.view = { zoom: DEFAULT_ZOOM[tf], start: null, pZoom: 1, pCenter: null };
}

/* ---------- draw ---------- */
function drawChart(c) {
  var tr = c.tr, S = ensureSeries(c), V = c.view;
  var all = S.all, svg = c.svg;
  svg.innerHTML = "";

  var visible = Math.min(all.length, Math.max(MIN_BARS, Math.ceil(all.length / V.zoom)));
  var maxStart = Math.max(0, all.length - visible);
  var focused = Math.min(Math.max(0, S.decFull - Math.floor(visible / 2)), maxStart);
  var viewStart = Math.min(Math.max(0, V.start === null ? focused : V.start), maxStart);
  var data = all.slice(viewStart, viewStart + visible);
  c.frame = { viewStart: viewStart, visible: visible, maxStart: maxStart, data: data };

  var plotW = W - L - R;
  var x = function (i) { return L + (i / Math.max(data.length - 1, 1)) * plotW; };
  var cw = Math.max(1, Math.min(7, (plotW / Math.max(data.length, 1)) * 0.68));

  var pv = [];
  data.forEach(function (b) {
    [b.l, b.h, b.ema9, b.ema20, b.ema200, b.vwap].forEach(function (v) {
      if (v !== null && isFinite(v)) pv.push(v);
    });
  });
  pv.push(tr.trigger, tr.stop, tr.target);
  if (traded(tr)) pv.push(tr.entry, tr.exit);
  var rawMin = Math.min.apply(null, pv), rawMax = Math.max.apply(null, pv);
  var span0 = rawMax - rawMin || 1;
  tr.levels.forEach(function (Lv) {
    if (Lv.price >= rawMin - span0 * 0.12 && Lv.price <= rawMax + span0 * 0.12) {
      rawMin = Math.min(rawMin, Lv.price); rawMax = Math.max(rawMax, Lv.price);
    }
  });
  var pad = Math.max((rawMax - rawMin) * 0.08, rawMax * 0.001);
  var minP = rawMin - pad, maxP = rawMax + pad;
  var pSpan = (maxP - minP) / V.pZoom;
  var center = Math.min(maxP - pSpan / 2, Math.max(minP + pSpan / 2,
    V.pCenter === null ? tr.trigger : V.pCenter));
  var vMin = center - pSpan / 2, vMax = center + pSpan / 2;
  var Y = function (v) { return PRICE_TOP + ((vMax - v) / (vMax - vMin || 1)) * PRICE_H; };
  c.frame.scale = { minP: minP, maxP: maxP, pSpan: pSpan, center: center, vMin: vMin, vMax: vMax, x: x, plotW: plotW };

  el("rect", { width: W, height: H, rx: 8, fill: "#101922" }, svg);
  el("text", { x: L + 6, y: PRICE_TOP + 16, fill: "#e2e8f0", "font-size": 13,
               "font-weight": 600, opacity: ".85" }, svg, tr.symbol + " · " + tf + " · ET");

  [0, 0.25, 0.5, 0.75, 1].forEach(function (r) {
    var yy = PRICE_TOP + PRICE_H * r, val = vMax - (vMax - vMin) * r;
    el("line", { x1: L, x2: W - R, y1: yy, y2: yy, stroke: "#fff", "stroke-opacity": ".10" }, svg);
    el("text", { x: 4, y: yy + 4, fill: "#a9bac9", "font-size": 11 }, svg, money(val));
  });

  /* time before the stock was on the LIVE watchlist (the service was not looking) */
  var fIdx = S.firstFull - viewStart;
  if (fIdx > 0) {
    var fx = x(Math.min(data.length - 1, fIdx));
    el("rect", { x: L, y: PRICE_TOP, width: Math.max(0, fx - L), height: VOL_TOP + VOL_H - PRICE_TOP,
                 fill: "#64748b", opacity: 0.08 }, svg);
    if (fIdx < data.length) {
      el("line", { x1: fx, x2: fx, y1: PRICE_TOP, y2: VOL_TOP + VOL_H, stroke: "#c4b5fd",
                   "stroke-opacity": ".6", "stroke-dasharray": "2 3" }, svg);
      el("text", { x: fx + 4, y: PRICE_TOP + 44, fill: "#c4b5fd", "font-size": 10 }, svg,
         "on live watchlist " + DATA.first[tr.symbol]);
    }
  }

  /* holding period of the rules-waived trade */
  var eIdx = S.entryFull - viewStart, xIdx = S.exitFull - viewStart;
  if (traded(tr) && xIdx >= 0 && eIdx < data.length) {
    var hx0 = x(Math.max(0, eIdx)), hx1 = x(Math.min(data.length - 1, xIdx));
    el("rect", { x: hx0, y: PRICE_TOP, width: Math.max(2, hx1 - hx0), height: PRICE_H,
                 fill: tr.net_usd > 0 ? "#2dd4bf" : "#fb7185", opacity: 0.07 }, svg);
  }

  /* support / resistance as of the decision candle */
  var shown = tr.levels.filter(function (Lv) { return Lv.price >= vMin && Lv.price <= vMax; });
  shown.forEach(function (Lv) {
    var y = Y(Lv.price), col = Lv.side === "resistance" ? "#f472b6" : "#38bdf8";
    el("line", { x1: L, x2: W - R, y1: y, y2: y, stroke: col,
                 "stroke-width": Lv.structural ? 1.3 : 1,
                 "stroke-dasharray": Lv.structural ? "" : "2 4",
                 "stroke-opacity": Lv.structural ? ".65" : ".4" }, svg);
    Lv._y = y; Lv._col = col;
  });
  var lastY = -99;
  shown.slice().sort(function (a, b) { return a._y - b._y; }).forEach(function (Lv) {
    var ly = Math.max(Lv._y + 3, lastY + 11);
    lastY = ly;
    el("text", { x: W - R - 2, y: ly, "text-anchor": "end", fill: Lv._col, "font-size": 10 },
       svg, money(Lv.price) + " " + nice(Lv.kind) + (Lv.structural ? "" : " ?"));
  });

  /* the pause candle(s) and the decision candle */
  var dIdx = S.decFull - viewStart;
  if (tf === "1m") tr.pause.forEach(function (t) {
    var pi = barIndexExact(all, t) - viewStart;
    if (pi < 0 || pi >= data.length) return;
    el("rect", { x: x(pi) - cw, y: PRICE_TOP, width: cw * 2, height: VOL_TOP + VOL_H - PRICE_TOP,
                 fill: "#94a3b8", opacity: 0.12 }, svg);
  });
  if (dIdx >= 0 && dIdx < data.length) {
    el("rect", { x: x(dIdx) - cw, y: PRICE_TOP, width: cw * 2, height: VOL_TOP + VOL_H - PRICE_TOP,
                 fill: "#fbbf24", opacity: 0.10 }, svg);
    el("line", { x1: x(dIdx), x2: x(dIdx), y1: PRICE_TOP, y2: VOL_TOP + VOL_H, stroke: "#fbbf24",
                 "stroke-opacity": ".7", "stroke-dasharray": "4 3" }, svg);
    el("text", { x: x(dIdx) + 5, y: PRICE_TOP + PRICE_H - 8, fill: "#fbbf24", "font-size": 11 }, svg,
       "setup " + tr.time + " · " + tr.failed_n + " rule(s) failed" + (tf === "5m" ? " (1m candle)" : ""));
  }

  /* candles */
  data.forEach(function (b, i) {
    var up = b.c >= b.o, col = up ? "#2dd4bf" : "#fb7185", cx = x(i);
    el("line", { x1: cx, x2: cx, y1: Y(b.h), y2: Y(b.l), stroke: col, "stroke-width": 1 }, svg);
    var top = Y(Math.max(b.o, b.c)), bot = Y(Math.min(b.o, b.c));
    el("rect", { x: cx - cw / 2, y: top, width: cw, height: Math.max(1, bot - top), fill: col }, svg);
  });

  /* engine attention promotions (replayed) */
  (DATA.att[tr.symbol] || []).forEach(function (a) {
    var ai = barIndex(all, a.t) - viewStart;
    if (ai < 0 || ai >= data.length) return;
    el("path", { d: "M " + (x(ai) - 5) + " " + (PRICE_TOP + 2) + " L " + (x(ai) + 5) + " "
                 + (PRICE_TOP + 2) + " L " + x(ai) + " " + (PRICE_TOP + 10) + " Z", fill: "#fde68a" }, svg);
    el("text", { x: x(ai) + 6, y: PRICE_TOP + 10, fill: "#fde68a", "font-size": 9.5 }, svg,
       "attention " + a.t + " " + nice(a.reason.split(":").slice(1, 2)[0] || a.reason));
  });

  line(svg, data.map(function (b) { return b.ema9; }), x, Y, "#fbbf24", 1.5);
  line(svg, data.map(function (b) { return b.ema20; }), x, Y, "#a78bfa", 1.5);
  line(svg, data.map(function (b) { return b.ema200; }), x, Y, "#94a3b8", 1.5);
  line(svg, data.map(function (b) { return b.vwap; }), x, Y, "#60a5fa", 1.5, "4 3");

  /* the setup's own levels */
  [["Buy-stop", tr.trigger, "#fbbf24"], ["Stop", tr.stop, "#fb7185"],
   ["Target 2R", tr.target, "#34d399"]].forEach(function (r) {
    if (r[1] < vMin || r[1] > vMax) return;
    el("line", { x1: L, x2: W - R, y1: Y(r[1]), y2: Y(r[1]), stroke: r[2],
                 "stroke-opacity": ".75", "stroke-dasharray": "5 4" }, svg);
    el("text", { x: W - R - 2, y: Y(r[1]) - 4, "text-anchor": "end", fill: r[2],
                 "font-size": 11 }, svg, r[0] + " " + money(r[1]));
  });
  if (traded(tr)) {
    var entryY = Y(tr.entry), exitY = Y(tr.exit);
    el("text", { x: L + 4, y: entryY - 5, fill: "#bbf7d0", "font-size": 11 },
       svg, "BUY " + money(tr.entry) + " " + tr.entry_time);
    el("text", { x: L + 4, y: Math.abs(exitY - entryY) >= 13 ? exitY - 5 : entryY + 13,
                 fill: "#fecaca", "font-size": 11 }, svg, "SELL " + money(tr.exit) + " " + tr.exit_time);
    if (eIdx >= 0 && eIdx < data.length)
      el("path", { d: "M " + (x(eIdx) - 6) + " " + (entryY + 13) + " L " + (x(eIdx) + 6) + " "
                   + (entryY + 13) + " L " + x(eIdx) + " " + (entryY + 3) + " Z", fill: "#4ade80" }, svg);
    if (xIdx >= 0 && xIdx < data.length)
      el("path", { d: "M " + (x(xIdx) - 6) + " " + (exitY - 13) + " L " + (x(xIdx) + 6) + " "
                   + (exitY - 13) + " L " + x(xIdx) + " " + (exitY - 3) + " Z", fill: "#f87171" }, svg);
  }

  /* MACD */
  var mv = [];
  data.forEach(function (b) {
    [b.macd, b.signal, b.hist].forEach(function (v) { if (v !== null && isFinite(v)) mv.push(v); });
  });
  var mMin = Math.min.apply(null, [0].concat(mv)), mMax = Math.max.apply(null, [0].concat(mv));
  var mPad = Math.max((mMax - mMin) * 0.15, 1e-4);
  var MY = function (v) { return MACD_TOP + ((mMax + mPad - v) / (mMax - mMin + mPad * 2 || 1)) * MACD_H; };
  c.frame.macd = { mMin: mMin, mMax: mMax, mPad: mPad };
  [0, 0.5, 1].forEach(function (r) {
    var yy = MACD_TOP + MACD_H * r, val = (mMax + mPad) - (mMax - mMin + mPad * 2) * r;
    el("line", { x1: L, x2: W - R, y1: yy, y2: yy, stroke: "#fff", "stroke-opacity": ".08" }, svg);
    el("text", { x: 4, y: yy + 3, fill: "#a9bac9", "font-size": 10 }, svg, val.toFixed(3));
  });
  el("line", { x1: L, x2: W - R, y1: MY(0), y2: MY(0), stroke: "#fff", "stroke-opacity": ".25" }, svg);
  data.forEach(function (b, i) {
    if (b.hist === null) return;
    var y0 = MY(0), y1 = MY(b.hist);
    el("rect", { x: x(i) - cw / 2, y: Math.min(y0, y1), width: cw,
                 height: Math.max(1, Math.abs(y1 - y0)),
                 fill: b.hist >= 0 ? "#2dd4bf" : "#fb7185", opacity: ".75" }, svg);
  });
  if (tf === "1m" && dIdx >= 1 && dIdx < data.length) {
    [[dIdx - 1, "prev"], [dIdx, "now"]].forEach(function (p) {
      var hb = data[p[0]].hist; if (hb === null) return;
      var y0 = MY(0), y1 = MY(hb);
      el("rect", { x: x(p[0]) - cw / 2 - 1.5, y: Math.min(y0, y1) - 1.5, width: cw + 3,
                   height: Math.max(1, Math.abs(y1 - y0)) + 3, fill: "none",
                   stroke: p[1] === "now" ? "#fbbf24" : "#e2e8f0", "stroke-width": 1.4 }, svg);
    });
  }
  line(svg, data.map(function (b) { return b.macd; }), x, MY, "#fbbf24", 1.4);
  line(svg, data.map(function (b) { return b.signal; }), x, MY, "#a78bfa", 1.4);
  el("text", { x: L + 4, y: MACD_TOP + 10, fill: "#a9bac9", "font-size": 11 }, svg,
     "MACD 12/26/9" + (tf === "1m" ? " · engine, warmed (what the MACD rules read)" : " · browser, unwarmed"));

  /* volume */
  var vMaxV = Math.max.apply(null, data.map(function (b) { return b.v; }).concat([1]));
  c.frame.volMax = vMaxV;
  [0, 0.5, 1].forEach(function (r) {
    var yy = VOL_TOP + VOL_H * (1 - r);
    el("line", { x1: L, x2: W - R, y1: yy, y2: yy, stroke: "#fff", "stroke-opacity": ".08" }, svg);
    el("text", { x: 4, y: yy + 3, fill: "#a9bac9", "font-size": 10 }, svg, fmtVol(vMaxV * r));
  });
  data.forEach(function (b, i) {
    var hgt = (b.v / vMaxV) * VOL_H;
    el("rect", { x: x(i) - cw / 2, y: VOL_TOP + VOL_H - hgt, width: cw, height: hgt,
                 fill: b.c >= b.o ? "#2dd4bf" : "#fb7185", opacity: ".65" }, svg);
  });
  el("text", { x: L + 4, y: VOL_TOP + 10, fill: "#a9bac9", "font-size": 11 }, svg,
     "Volume · RVOL at setup " + (tr.rvol == null ? "–" : tr.rvol.toFixed(1) + "×")
     + " · breakout candle " + (tr.vol_ratio == null ? "–" : tr.vol_ratio.toFixed(2) + "× recent (needs 2.5×)"));

  var ticks = Math.min(6, data.length);
  for (var t = 0; t < ticks; t++) {
    var idx = Math.round((t / Math.max(ticks - 1, 1)) * (data.length - 1));
    el("text", { x: x(idx), y: 570, "text-anchor": "middle", fill: "#a9bac9", "font-size": 11 },
       svg, data[idx].t);
  }

  c.cursorLayer = el("g", { "pointer-events": "none" }, svg);
  if (c.cursor) drawCursor(c);
  updateToolbar(c);
}
function barIndexExact(data, t) {
  for (var i = 0; i < data.length; i++) if (data[i].t === t) return i;
  return -1;
}
function line(svg, vals, x, y, color, width, dash) {
  var d = "", started = false;
  vals.forEach(function (v, i) {
    if (v === null || !isFinite(v)) { started = false; return; }
    d += (started ? "L" : "M") + x(i).toFixed(1) + "," + y(v).toFixed(1) + " ";
    started = true;
  });
  if (d) el("path", { d: d, fill: "none", stroke: color, "stroke-width": width,
                      "stroke-dasharray": dash || "" }, svg);
}

/* ---------- crosshair ---------- */
function drawCursor(c) {
  var g = c.cursorLayer;
  if (!g) return;
  g.innerHTML = "";
  var f = c.frame, data = f.data, sc = f.scale;
  var i = Math.min(data.length - 1, Math.max(0,
    Math.round(((c.cursor.x - L) / sc.plotW) * Math.max(data.length - 1, 1))));
  var b = data[i];
  if (!b) return;
  var cx = sc.x(i);
  el("line", { x1: cx, x2: cx, y1: PRICE_TOP, y2: VOL_TOP + VOL_H, stroke: "#cbd5e1",
               "stroke-opacity": ".55", "stroke-dasharray": "3 3" }, g);
  // the horizontal readout reports whichever panel the pointer sits in
  var y = c.cursor.y, label = null;
  if (y >= PRICE_TOP && y <= PRICE_TOP + PRICE_H)
    label = money(sc.vMax - ((y - PRICE_TOP) / PRICE_H) * (sc.vMax - sc.vMin));
  else if (y >= MACD_TOP && y <= MACD_TOP + MACD_H)
    label = ((f.macd.mMax + f.macd.mPad)
      - ((y - MACD_TOP) / MACD_H) * (f.macd.mMax - f.macd.mMin + f.macd.mPad * 2)).toFixed(2);
  else if (y >= VOL_TOP && y <= VOL_TOP + VOL_H)
    label = fmtVol(((VOL_TOP + VOL_H - y) / VOL_H) * f.volMax);
  if (label !== null) {
    el("line", { x1: L, x2: W - R, y1: y, y2: y, stroke: "#cbd5e1",
                 "stroke-opacity": ".55", "stroke-dasharray": "3 3" }, g);
    el("rect", { x: 2, y: y - 9, width: 60, height: 18, rx: 3, fill: "#22303f",
                 stroke: "#cbd5e1", "stroke-opacity": ".4" }, g);
    el("text", { x: 32, y: y + 4, "text-anchor": "middle", fill: "#e2e8f0",
                 "font-size": 11 }, g, label);
  }
  var bx = Math.min(Math.max(cx - 24, 2), W - 50);
  el("rect", { x: bx, y: 557, width: 48, height: 18, rx: 3, fill: "#22303f",
               stroke: "#cbd5e1", "stroke-opacity": ".4" }, g);
  el("text", { x: bx + 24, y: 570, "text-anchor": "middle", fill: "#e2e8f0",
               "font-size": 11 }, g, b.t);
  c.readout.textContent =
    b.t + "  O " + b.o + "  H " + b.h + "  L " + b.l + "  C " + b.c
    + "  vol " + fmtVol(b.v)
    + "  EMA9 " + (b.ema9 == null ? "–" : b.ema9.toFixed(2))
    + "  EMA20 " + (b.ema20 == null ? "–" : b.ema20.toFixed(2))
    + "  EMA200 " + (b.ema200 == null ? "–" : b.ema200.toFixed(2))
    + "  VWAP " + (b.vwap == null ? "–" : b.vwap.toFixed(2))
    + "  MACD " + (b.macd == null ? "–" : b.macd.toFixed(3))
    + "  hist " + (b.hist == null ? "–" : b.hist.toFixed(3));
}

/* ---------- interaction ---------- */
function zoomBy(c, f) {
  c.view.zoom = Math.min(MAX_ZOOM, Math.max(1, c.view.zoom * f));
  c.view.start = null;
  drawChart(c);
}
function panBy(c, dir) {
  var f = c.frame;
  c.view.start = Math.min(f.maxStart, Math.max(0,
    f.viewStart + dir * Math.ceil(f.visible * 0.7)));
  drawChart(c);
}
function pZoomBy(c, f) {
  c.view.pZoom = Math.min(MAX_ZOOM, Math.max(1, c.view.pZoom * f));
  drawChart(c);
}
function pPanBy(c, dir) {
  var s = c.frame.scale;
  c.view.pCenter = Math.min(s.maxP - s.pSpan / 2,
    Math.max(s.minP + s.pSpan / 2, s.center + s.pSpan * 0.3 * dir));
  drawChart(c);
}
/* zooms both axes around the cursor, so the pinched area stays under the pointer */
function pinch(c, deltaY, clientX, clientY, rect) {
  var factor = Math.exp(-deltaY * 0.01);
  var cxs = ((clientX - rect.left) / rect.width) * W;
  var cys = ((clientY - rect.top) / rect.height) * H;
  var f = c.frame, all = c.series.all;
  var nz = Math.round(Math.min(MAX_ZOOM, Math.max(1, c.view.zoom * factor)) * 100) / 100;
  var fx = Math.min(1, Math.max(0, (cxs - L) / f.scale.plotW));
  var atCursor = f.viewStart + fx * (f.visible - 1);
  var nVisible = Math.min(all.length, Math.max(MIN_BARS, Math.ceil(all.length / nz)));
  var nMaxStart = Math.max(0, all.length - nVisible);
  c.view.zoom = nz;
  c.view.start = Math.min(nMaxStart, Math.max(0, Math.round(atCursor - fx * (nVisible - 1))));
  if (cys >= PRICE_TOP && cys <= PRICE_TOP + PRICE_H) {
    var s = f.scale;
    var npz = Math.round(Math.min(MAX_ZOOM, Math.max(1, c.view.pZoom * factor)) * 100) / 100;
    var fy = (cys - PRICE_TOP) / PRICE_H;
    var priceAt = s.vMax - fy * (s.vMax - s.vMin);
    var nSpan = (s.maxP - s.minP) / npz;
    c.view.pZoom = npz;
    c.view.pCenter = Math.min(s.maxP - nSpan / 2,
      Math.max(s.minP + nSpan / 2, priceAt + nSpan * (fy - 0.5)));
  }
  drawChart(c);
}
function updateToolbar(c) {
  var v = c.view, f = c.frame;
  c.zlabel.textContent = (v.zoom <= 1 ? "Full session"
    : (v.zoom % 1 === 0 ? v.zoom : v.zoom.toFixed(1)) + "× · " + f.data.length + " " + tf + " candles")
    + (v.pZoom > 1 ? " · " + (v.pZoom % 1 === 0 ? v.pZoom : v.pZoom.toFixed(1)) + "× price" : "");
  c.btn.left.disabled = f.viewStart === 0;
  c.btn.right.disabled = f.viewStart === f.maxStart;
  c.btn.zout.disabled = v.zoom <= 1;
  c.btn.zin.disabled = v.zoom >= MAX_ZOOM || f.visible <= MIN_BARS;
  c.btn.up.disabled = v.pZoom <= 1;
  c.btn.down.disabled = v.pZoom <= 1;
  c.btn.vout.disabled = v.pZoom <= 1;
  c.btn.vin.disabled = v.pZoom >= MAX_ZOOM;
}
function wire(c) {
  var svg = c.svg;
  svg.addEventListener("wheel", function (ev) {
    if (!ev.ctrlKey) return;   // trackpad pinch and ctrl+wheel; plain scroll is left alone
    ev.preventDefault();
    pinch(c, ev.deltaY, ev.clientX, ev.clientY, svg.getBoundingClientRect());
  }, { passive: false });
  svg.addEventListener("pointermove", function (ev) {
    var rect = svg.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    c.cursor = { x: ((ev.clientX - rect.left) / rect.width) * W,
                 y: ((ev.clientY - rect.top) / rect.height) * H };
    if (!c.drag) { drawCursor(c); return; }
    // offsets measured from where the drag began, so a round trip lands back
    var barsPerPx = Math.max(c.frame.data.length - 1, 1) / (c.frame.scale.plotW * (rect.width / W));
    c.view.start = Math.min(c.frame.maxStart, Math.max(0,
      c.drag.startView - Math.round((ev.clientX - c.drag.startX) * barsPerPx)));
    var s = c.frame.scale;
    var pricePerPx = s.pSpan / (PRICE_H * (rect.height / H));
    c.view.pCenter = Math.min(s.maxP - s.pSpan / 2, Math.max(s.minP + s.pSpan / 2,
      c.drag.startCenter + (ev.clientY - c.drag.startY) * pricePerPx));
    drawChart(c);
  });
  svg.addEventListener("pointerdown", function (ev) {
    if (ev.button !== 0 && ev.pointerType === "mouse") return;
    svg.setPointerCapture(ev.pointerId);
    c.drag = { id: ev.pointerId, startX: ev.clientX, startY: ev.clientY,
               startView: c.frame.viewStart, startCenter: c.frame.scale.center };
    svg.classList.add("dragging");
    c.sec.focus();
  });
  function endDrag(ev) {
    if (!c.drag || c.drag.id !== ev.pointerId) return;
    if (svg.hasPointerCapture(ev.pointerId)) svg.releasePointerCapture(ev.pointerId);
    c.drag = null;
    svg.classList.remove("dragging");
  }
  svg.addEventListener("pointerup", endDrag);
  svg.addEventListener("pointercancel", endDrag);
  svg.addEventListener("pointerleave", function () {
    if (c.drag) return;
    c.cursor = null;
    if (c.cursorLayer) c.cursorLayer.innerHTML = "";
    c.readout.textContent = "";
  });
  c.sec.addEventListener("keydown", function (ev) {
    var k = ev.key;
    if (k === "+" || k === "=") { ev.preventDefault(); zoomBy(c, 2); }
    else if (k === "-") { ev.preventDefault(); zoomBy(c, 0.5); }
    else if (k === "0") { ev.preventDefault(); resetView(c); drawChart(c); }
    else if (k === "ArrowLeft") { ev.preventDefault(); panBy(c, -1); }
    else if (k === "ArrowRight") { ev.preventDefault(); panBy(c, 1); }
    else if (k === "ArrowUp") { ev.preventDefault(); pPanBy(c, 1); }
    else if (k === "ArrowDown") { ev.preventDefault(); pPanBy(c, -1); }
  });
}

/* ---------- full screen ----------
   Native Fullscreen API where allowed; a fixed-position fallback where it is
   not (some embedded webviews reject the request). Redraw after either, since
   the SVG is sized from its container. */
function toggleFull(c) {
  var sec = c.sec;
  if (document.fullscreenElement === sec) { document.exitFullscreen(); return; }
  if (sec.classList.contains("zoomed")) {
    sec.classList.remove("zoomed");
    document.body.classList.remove("has-zoom");
    setTimeout(function () { drawChart(c); }, 60);
    return;
  }
  var fallback = function () {
    sec.classList.add("zoomed");
    document.body.classList.add("has-zoom");
    setTimeout(function () { drawChart(c); }, 60);
  };
  if (sec.requestFullscreen) {
    sec.requestFullscreen()
      .then(function () { setTimeout(function () { drawChart(c); }, 60); })
      .catch(fallback);
  } else fallback();
}
document.addEventListener("fullscreenchange", function () {
  cards.forEach(function (c) { setTimeout(function () { drawChart(c); }, 60); });
});
document.addEventListener("keydown", function (ev) {
  if (ev.key !== "Escape") return;
  var z = document.querySelector(".card.zoomed");
  if (z) cards.forEach(function (c) { if (c.sec === z) toggleFull(c); });
});

/* ---------- cards ---------- */
function outcomeText(tr) {
  if (!traded(tr)) return "Even with every rule waived: <b>no trade</b> — " + nice(tr.why)
    + (tr.why === "cost_over_risk" ? "" : "");
  return "With every rule waived" + (tr.cost_lifted ? " <i>(cost gate lifted too)</i>" : "")
    + ": bought <b>" + tr.entry_time + "</b> @ <b>" + money(tr.entry) + "</b> × " + tr.qty
    + ", sold <b>" + tr.exit_time + "</b> @ <b>" + money(tr.exit) + "</b> (" + nice(tr.exit_reason) + ")"
    + " · gross <b class='" + cls(tr.gross_usd) + "'>" + inr(tr.gross_usd) + "</b>"
    + " − costs <b>" + inr(tr.costs_usd) + "</b> = net <b class='" + cls(tr.net_usd) + "'>"
    + inr(tr.net_usd) + "</b> (" + (tr.r_multiple >= 0 ? "+" : "") + tr.r_multiple.toFixed(2)
    + "R; best seen +" + Math.max(0, tr.mfe_r).toFixed(2) + "R)";
}
function buildCards() {
  var host = document.getElementById("charts");
  DATA.cards.forEach(function (tr, i) {
    var sec = h("section", { class: "card", tabindex: "0" }, host);
    var head = h("div", { class: "card-head" }, sec);
    var left = h("div", {}, head);
    var h2 = h("h2", {}, left, tr.symbol + "  " + tr.time + " ET   micro pullback ("
      + tr.pause.length + "-bar pause)");
    h("span", { class: "badge " + (tr.watched_live ? "live" : "pre") }, h2,
      tr.watched_live ? "service was watching" : "before it was on the live watchlist");
    var meta = h("div", { class: "meta" }, left);
    meta.innerHTML = "buy-stop <b>" + money(tr.trigger) + "</b> · stop <b>" + money(tr.stop)
      + "</b> (" + tr.risk_pct.toFixed(2) + "% away) · 2R target <b>" + money(tr.target) + "</b>"
      + " · day <b>" + tr.day_chg_pct.toFixed(1) + "%</b>";
    var gates = h("div", { class: "gates" }, left);
    tr.gates.forEach(function (g) {
      h("span", { class: "g " + (g[2] ? "ok" : "no"), title: g[3] || "" }, gates,
        (g[2] ? "✓ " : "✗ ") + g[1]);
    });
    var oc = h("div", { class: "outcome" }, left);
    oc.innerHTML = outcomeText(tr);
    var kpi = h("div", { class: "kpi" }, head);
    kpi.innerHTML = "<b>" + tr.failed_n + " / " + tr.gates.length + "</b>rules failed<br>"
      + (traded(tr) ? "<b class='" + cls(tr.net_usd) + "'>" + inr(tr.net_usd) + "</b>if waived"
                    : "<b>–</b>no fill even if waived");

    var bar = h("div", { class: "toolbar" }, sec);
    var zlabel = h("span", { class: "zlabel" }, bar);
    var grp = h("div", { class: "btns" }, bar);
    var c = { sec: sec, tr: tr, i: i, cursor: null, drag: null, btn: {}, zlabel: zlabel };
    function mk(txt, title, fn) {
      var b = h("button", { type: "button", title: title }, grp, txt);
      b.onclick = function () { fn(); };
      return b;
    }
    c.btn.left = mk("←", "Earlier candles", function () { panBy(c, -1); });
    c.btn.zout = mk("− Zoom", "Zoom out", function () { zoomBy(c, 0.5); });
    c.btn.zin = mk("+ Zoom", "Zoom in", function () { zoomBy(c, 2); });
    c.btn.right = mk("→", "Later candles", function () { panBy(c, 1); });
    h("span", { class: "sep" }, grp);
    c.btn.up = mk("↑", "Higher prices", function () { pPanBy(c, 1); });
    c.btn.vout = mk("− V-Zoom", "Price zoom out", function () { pZoomBy(c, 0.5); });
    c.btn.vin = mk("+ V-Zoom", "Price zoom in", function () { pZoomBy(c, 2); });
    c.btn.down = mk("↓", "Lower prices", function () { pPanBy(c, -1); });
    h("span", { class: "sep" }, grp);
    mk("Reset", "Reset both axes", function () { resetView(c); drawChart(c); });
    mk("⛶ Full screen", "Full screen (Esc to exit)", function () { toggleFull(c); });

    c.svg = el("svg", { viewBox: "0 0 " + W + " " + H, role: "img",
                        "aria-label": tr.symbol + " candle chart" }, sec);
    c.readout = h("div", { class: "readout" }, sec);
    cards.push(c);
    wire(c);
  });
}

/* ---------- table ---------- */
var sortKey = "symbol", sortDir = 1;
function visible() {
  var sym = document.getElementById("f-sym").value;
  var lv = document.getElementById("f-live").value;
  var out = document.getElementById("f-out").value;
  return cards.filter(function (c) {
    var t = c.tr;
    if (sym && t.symbol !== sym) return false;
    if (lv === "live" && !t.watched_live) return false;
    if (lv === "pre" && t.watched_live) return false;
    if (out === "filled" && !traded(t)) return false;
    if (out === "win" && !(traded(t) && t.net_usd > 0)) return false;
    if (out === "loss" && !(traded(t) && t.net_usd <= 0)) return false;
    if (out === "nofill" && traded(t)) return false;
    if (out === "close" && t.failed_n > 3) return false;
    return true;
  });
}
function render() {
  var keep = visible();
  cards.forEach(function (c) { c.sec.style.display = "none"; });
  keep.forEach(function (c) { c.sec.style.display = ""; drawChart(c); });
  document.getElementById("n-shown").textContent = keep.length + " of " + cards.length + " setups shown";
  var tb = document.querySelector("#det tbody");
  tb.innerHTML = "";
  keep.slice().sort(function (a, b) {
    var x = a.tr[sortKey], y = b.tr[sortKey];
    if (x === y) { x = a.tr.symbol + a.tr.time; y = b.tr.symbol + b.tr.time; }
    return (x > y ? 1 : x < y ? -1 : 0) * sortDir;
  }).forEach(function (c) {
    var row = h("tr", {}, tb);
    h("td", {}, row, c.tr.symbol);
    h("td", {}, row, c.tr.time);
    h("td", {}, row, c.tr.watched_live ? "yes" : "no");
    h("td", { class: "num" }, row, String(c.tr.failed_n));
    h("td", {}, row, traded(c.tr) ? nice(c.tr.exit_reason) : "no fill");
    h("td", { class: "num " + (traded(c.tr) ? cls(c.tr.net_usd) : "") }, row,
      traded(c.tr) ? inr(c.tr.net_usd) : "–");
    row.onclick = function () {
      c.sec.scrollIntoView({ behavior: "smooth", block: "center" });
      c.sec.classList.add("flash");
      setTimeout(function () { c.sec.classList.remove("flash"); }, 2000);
    };
  });
}

function init() {
  buildCards();
  var leg = document.getElementById("legend");
  [["setup (decision) candle", "#fbbf24"], ["pause candle(s) (grey band)", "#94a3b8"],
   ["up candle", "#2dd4bf"], ["down candle", "#fb7185"], ["EMA9 / MACD", "#fbbf24"],
   ["EMA20 / signal", "#a78bfa"], ["EMA200", "#94a3b8"], ["VWAP (dashed)", "#60a5fa"],
   ["resistance", "#f472b6"], ["support", "#38bdf8"], ["buy-stop", "#fbbf24"],
   ["stop", "#fb7185"], ["2R target", "#34d399"], ["BUY ▲ / SELL ▼ (rules waived)", "#4ade80"],
   ["▼ engine attention promotion", "#fde68a"], ["not yet on live watchlist (grey)", "#c4b5fd"]
  ].forEach(function (p) {
    var s = h("span", {}, leg);
    h("i", { style: "border-top-color:" + p[1] }, s);
    h("span", {}, s, p[0]);
  });
  h("p", { class: "hint" }, leg.parentNode,
    "Drag to pan · ctrl+scroll or trackpad pinch to zoom both axes · click a chart then "
    + "+/− zoom, 0 reset, ←/→ pan, ↑/↓ price. Hover a rule chip for the measured value. "
    + "Levels are as of the setup candle; a dashed level marked ? is a single unconfirmed pivot.");
  var syms = {};
  DATA.cards.forEach(function (t) { syms[t.symbol] = 1; });
  Object.keys(syms).sort().forEach(function (s) {
    h("option", { value: s }, document.getElementById("f-sym"), s);
  });
  ["f-sym", "f-live", "f-out"].forEach(function (id) {
    document.getElementById(id).onchange = render;
  });
  document.querySelectorAll("#stocks tbody tr[data-sym]").forEach(function (row) {
    row.onclick = function () {
      var s = row.getAttribute("data-sym");
      if (!syms[s]) return;
      document.getElementById("f-sym").value = s;
      render();
      document.getElementById("charts-start").scrollIntoView({ behavior: "smooth" });
    };
  });
  document.getElementById("f-tf").onchange = function () {
    tf = this.value;
    cards.forEach(function (c) { c.series = null; });
    render();
  };
  document.querySelectorAll("#det th").forEach(function (th) {
    th.onclick = function () {
      var k = th.getAttribute("data-k");
      sortDir = k === sortKey ? -sortDir : 1;
      sortKey = k;
      render();
    };
  });
  render();
}
document.addEventListener("DOMContentLoaded", init);
