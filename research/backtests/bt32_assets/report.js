/* BT32 — per-trade charts with the same feature set as the /momentum review
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
var DEFAULT_ZOOM = { "1m": 8, "5m": 2 };
var WEAK = DATA.weak_strength;
var cards = [], tf = "5m";

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
function inr(x) { return "₹" + Math.round(x).toLocaleString("en-IN"); }
function money(x) { return "₹" + x.toLocaleString("en-IN", { maximumFractionDigits: 2 }); }
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
      ema9: e9[i], ema20: e20[i], vwap: cumVol ? cumPv / cumVol : null,
      macd: macd[i], signal: sig[i],
      hist: macd[i] === null || sig[i] === null ? null : macd[i] - sig[i]
    };
  });
}
/* 5-minute buckets aligned to the 09:15 session open, matching engine.resample_5m. */
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
function rawBars(tr) {
  return tr.bars.map(function (r) {
    return { t: r[0], o: r[1], h: r[2], l: r[3], c: r[4], v: r[5] };
  });
}
/* index of the bar CONTAINING a HH:MM stamp (last bar starting at or before it) */
function barIndex(data, hhmm) {
  var best = 0;
  for (var i = 0; i < data.length; i++) if (data[i].t <= hhmm) best = i;
  return best;
}

/* ---------- per-card view state ---------- */
function ensureSeries(c) {
  if (c.series && c.series.tf === tf) return c.series;
  var raw = rawBars(c.tr);
  var data = points(tf === "5m" ? fiveMinute(raw) : raw);
  c.series = {
    tf: tf, all: data,
    entryFull: barIndex(data, c.tr.entry_time),
    exitFull: barIndex(data, c.tr.exit_time)
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
  var focused = Math.min(Math.max(0, S.entryFull - Math.floor(visible / 2)), maxStart);
  var viewStart = Math.min(Math.max(0, V.start === null ? focused : V.start), maxStart);
  var data = all.slice(viewStart, viewStart + visible);
  c.frame = { viewStart: viewStart, visible: visible, maxStart: maxStart, data: data };

  var plotW = W - L - R;
  var x = function (i) { return L + (i / Math.max(data.length - 1, 1)) * plotW; };
  var cw = Math.max(1, Math.min(7, (plotW / Math.max(data.length, 1)) * 0.68));

  /* price scale — bars, overlays, the trade's own levels, and nearby S/R */
  var pv = [];
  data.forEach(function (b) {
    [b.l, b.h, b.ema9, b.ema20, b.vwap].forEach(function (v) {
      if (v !== null && isFinite(v)) pv.push(v);
    });
  });
  pv.push(tr.entry, tr.stop, tr.target, tr.exit);
  var rawMin = Math.min.apply(null, pv), rawMax = Math.max.apply(null, pv);
  // Levels can sit far from the visible action; letting a distant one set the
  // scale would squash the candles. Admit only those close by — the rest stay
  // off-screen, which is the honest way to say "not near".
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
    V.pCenter === null ? tr.entry : V.pCenter));
  var vMin = center - pSpan / 2, vMax = center + pSpan / 2;
  var Y = function (v) { return PRICE_TOP + ((vMax - v) / (vMax - vMin || 1)) * PRICE_H; };
  c.frame.scale = { minP: minP, maxP: maxP, pSpan: pSpan, center: center, vMin: vMin, vMax: vMax, x: x, plotW: plotW };

  el("rect", { width: W, height: H, rx: 8, fill: "#101922" }, svg);
  el("text", { x: L + 6, y: PRICE_TOP + 16, fill: "#e2e8f0", "font-size": 13,
               "font-weight": 600, opacity: ".85" }, svg, tr.symbol + " · " + tf);

  [0, 0.25, 0.5, 0.75, 1].forEach(function (r) {
    var yy = PRICE_TOP + PRICE_H * r, val = vMax - (vMax - vMin) * r;
    el("line", { x1: L, x2: W - R, y1: yy, y2: yy, stroke: "#fff", "stroke-opacity": ".10" }, svg);
    el("text", { x: 4, y: yy + 4, fill: "#a9bac9", "font-size": 11 }, svg, money(val));
  });

  /* holding period */
  var eIdx = S.entryFull - viewStart, xIdx = S.exitFull - viewStart;
  if (xIdx >= 0 && eIdx < data.length) {
    var hx0 = x(Math.max(0, eIdx)), hx1 = x(Math.min(data.length - 1, xIdx));
    el("rect", { x: hx0, y: PRICE_TOP, width: Math.max(2, hx1 - hx0), height: PRICE_H,
                 fill: "#2dd4bf", opacity: 0.07 }, svg);
  }

  /* support / resistance, labels nudged apart so none is lost */
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
       svg, Lv.price.toFixed(1) + " " + nice(Lv.kind) + (Lv.structural ? "" : " ?"));
  });

  /* candles */
  data.forEach(function (b, i) {
    var up = b.c >= b.o, col = up ? "#2dd4bf" : "#fb7185", cx = x(i);
    el("line", { x1: cx, x2: cx, y1: Y(b.h), y2: Y(b.l), stroke: col, "stroke-width": 1 }, svg);
    var top = Y(Math.max(b.o, b.c)), bot = Y(Math.min(b.o, b.c));
    el("rect", { x: cx - cw / 2, y: top, width: cw, height: Math.max(1, bot - top), fill: col }, svg);
  });

  /* candlestick formations on THIS timeframe only */
  tr.patterns.forEach(function (m) {
    if (m.timeframe !== tf) return;
    var s0 = barIndexExact(all, m.start), s1 = barIndexExact(all, m.end);
    if (s0 < 0 || s1 < 0) return;
    if (s1 < viewStart || s0 >= viewStart + data.length) return;
    var a = Math.max(0, s0 - viewStart), b2 = Math.min(data.length - 1, s1 - viewStart);
    var x0 = x(a) - cw, x1 = x(b2) + cw;
    // A correctly-named formation on a candle much smaller than this stock's
    // recent average is drawn faint: the label is right, the candle is not
    // worth acting on (candles.STRENGTH_WEAK_BELOW).
    var weak = m.strength < WEAK;
    var g = el("g", { "pointer-events": "none", opacity: weak ? 0.45 : 1 }, svg);
    el("rect", { x: x0, y: PRICE_TOP, width: Math.max(2, x1 - x0), height: PRICE_H,
                 fill: "#fbbf24", "fill-opacity": weak ? ".04" : ".10" }, g);
    el("path", { d: "M " + x0 + " " + (PRICE_TOP + 16) + " V " + (PRICE_TOP + 7)
                 + " H " + x1 + " V " + (PRICE_TOP + 16), fill: "none", stroke: "#fbbf24",
                 "stroke-width": 1.2, "stroke-dasharray": weak ? "3 3" : "" }, g);
    el("text", { x: (x0 + x1) / 2, y: PRICE_TOP + 31, "text-anchor": "middle",
                 fill: "#fde68a", "font-size": 10 }, g,
       nice(m.name) + " · " + m.timeframe + " · " + m.strength.toFixed(2) + "×");
  });

  /* overlays — same colours as the /momentum chart */
  line(svg, data.map(function (b) { return b.ema9; }), x, Y, "#fbbf24", 1.5);
  line(svg, data.map(function (b) { return b.ema20; }), x, Y, "#a78bfa", 1.5);
  line(svg, data.map(function (b) { return b.vwap; }), x, Y, "#60a5fa", 1.5, "4 3");

  /* trade levels */
  [["Stop", tr.stop, "#fb7185"], ["Target 2R", tr.target, "#34d399"]].forEach(function (r) {
    if (r[1] < vMin || r[1] > vMax) return;
    el("line", { x1: L, x2: W - R, y1: Y(r[1]), y2: Y(r[1]), stroke: r[2],
                 "stroke-opacity": ".75", "stroke-dasharray": "5 4" }, svg);
    el("text", { x: W - R - 2, y: Y(r[1]) - 4, "text-anchor": "end", fill: r[2],
                 "font-size": 11 }, svg, r[0] + " " + money(r[1]));
  });
  var entryY = Y(tr.entry), exitY = Y(tr.exit);
  el("line", { x1: L, x2: W - R, y1: entryY, y2: entryY, stroke: "#4ade80",
               "stroke-opacity": ".8", "stroke-dasharray": "2 3" }, svg);
  el("text", { x: L + 4, y: entryY - 5, fill: "#bbf7d0", "font-size": 11 },
     svg, "BUY " + money(tr.entry));
  el("line", { x1: L, x2: W - R, y1: exitY, y2: exitY, stroke: "#f87171",
               "stroke-opacity": ".8", "stroke-dasharray": "2 3" }, svg);
  // park the exit label below the entry one when the two nearly coincide
  el("text", { x: L + 4, y: Math.abs(exitY - entryY) >= 13 ? exitY - 5 : entryY + 13,
               fill: "#fecaca", "font-size": 11 }, svg, "SELL " + money(tr.exit));
  if (eIdx >= 0 && eIdx < data.length)
    el("path", { d: "M " + (x(eIdx) - 6) + " " + (entryY + 13) + " L " + (x(eIdx) + 6) + " "
                 + (entryY + 13) + " L " + x(eIdx) + " " + (entryY + 3) + " Z", fill: "#4ade80" }, svg);
  if (xIdx >= 0 && xIdx < data.length)
    el("path", { d: "M " + (x(xIdx) - 6) + " " + (exitY - 13) + " L " + (x(xIdx) + 6) + " "
                 + (exitY - 13) + " L " + x(xIdx) + " " + (exitY - 3) + " Z", fill: "#f87171" }, svg);

  /* MACD */
  var mv = [];
  data.forEach(function (b) {
    [b.macd, b.signal, b.hist].forEach(function (v) { if (v !== null && isFinite(v)) mv.push(v); });
  });
  var mMin = Math.min.apply(null, [0].concat(mv)), mMax = Math.max.apply(null, [0].concat(mv));
  var mPad = Math.max((mMax - mMin) * 0.15, 0.01);
  var MY = function (v) { return MACD_TOP + ((mMax + mPad - v) / (mMax - mMin + mPad * 2 || 1)) * MACD_H; };
  c.frame.macd = { mMin: mMin, mMax: mMax, mPad: mPad };
  [0, 0.5, 1].forEach(function (r) {
    var yy = MACD_TOP + MACD_H * r, val = (mMax + mPad) - (mMax - mMin + mPad * 2) * r;
    el("line", { x1: L, x2: W - R, y1: yy, y2: yy, stroke: "#fff", "stroke-opacity": ".08" }, svg);
    el("text", { x: 4, y: yy + 3, fill: "#a9bac9", "font-size": 10 }, svg, val.toFixed(2));
  });
  el("line", { x1: L, x2: W - R, y1: MY(0), y2: MY(0), stroke: "#fff", "stroke-opacity": ".25" }, svg);
  data.forEach(function (b, i) {
    if (b.hist === null) return;
    var y0 = MY(0), y1 = MY(b.hist);
    el("rect", { x: x(i) - cw / 2, y: Math.min(y0, y1), width: cw,
                 height: Math.max(1, Math.abs(y1 - y0)),
                 fill: b.hist >= 0 ? "#2dd4bf" : "#fb7185", opacity: ".75" }, svg);
  });
  line(svg, data.map(function (b) { return b.macd; }), x, MY, "#fbbf24", 1.4);
  line(svg, data.map(function (b) { return b.signal; }), x, MY, "#a78bfa", 1.4);
  el("text", { x: L + 4, y: MACD_TOP + 10, fill: "#a9bac9", "font-size": 11 }, svg, "MACD 12/26/9");

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
     "Volume · day RVOL " + tr.rvol.toFixed(1) + "×"
     + (tr.rvol_5m == null ? "" : " · 5m " + tr.rvol_5m.toFixed(1) + "× avg"));

  /* time axis */
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
function buildCards() {
  var host = document.getElementById("charts");
  DATA.trades.forEach(function (tr, i) {
    var sec = h("section", { class: "card", tabindex: "0" }, host);
    var head = h("div", { class: "card-head" }, sec);
    var left = h("div", {}, head);
    h("h2", {}, left, tr.symbol + "  " + tr.date + "   " + nice(tr.setup));
    var meta = h("div", { class: "meta" }, left);
    meta.innerHTML =
      "in <b>" + tr.entry_time + "</b> @ <b>" + tr.entry.toFixed(2) + "</b>"
      + " · stop <b>" + tr.stop.toFixed(2) + "</b>"
      + " · out <b>" + tr.exit_time + "</b> @ <b>" + tr.exit.toFixed(2) + "</b>"
      + " (" + nice(tr.exit_reason) + ")"
      + " · qty <b>" + tr.qty + "</b>"
      + " · day <b>" + tr.day_chg_pct.toFixed(1) + "%</b>";
    var kpi = h("div", { class: "kpi" }, head);
    kpi.innerHTML = "<b class='" + cls(tr.gross_pct) + "'>" + pct(tr.gross_pct) + "</b>"
      + "gross · net " + inr(tr.net_real_inr) + " @ real"
      + "<br>" + inr(tr.net_stress_inr) + " @ stress";

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
var sortKey = "date", sortDir = 1;
function visible() {
  var sym = document.getElementById("f-sym").value;
  var ex = document.getElementById("f-exit").value;
  var out = document.getElementById("f-out").value;
  return cards.filter(function (c) {
    if (sym && c.tr.symbol !== sym) return false;
    if (ex && c.tr.exit_reason !== ex) return false;
    if (out === "win" && c.tr.gross_pct <= 0) return false;
    if (out === "loss" && c.tr.gross_pct > 0) return false;
    return true;
  });
}
function render() {
  var keep = visible();
  cards.forEach(function (c) { c.sec.style.display = "none"; });
  keep.forEach(function (c) { c.sec.style.display = ""; drawChart(c); });
  var tb = document.querySelector("#det tbody");
  tb.innerHTML = "";
  keep.slice().sort(function (a, b) {
    var x = a.tr[sortKey], y = b.tr[sortKey];
    return (x > y ? 1 : x < y ? -1 : 0) * sortDir;
  }).forEach(function (c) {
    var tr = h("tr", {}, tb);
    h("td", {}, tr, c.tr.date.slice(5));
    h("td", {}, tr, c.tr.symbol);
    h("td", {}, tr, c.tr.entry_time);
    h("td", {}, tr, nice(c.tr.exit_reason));
    h("td", { class: "num " + cls(c.tr.gross_pct) }, tr, pct(c.tr.gross_pct));
    h("td", { class: "num " + cls(c.tr.net_real_inr) }, tr, inr(c.tr.net_real_inr));
    tr.onclick = function () {
      c.sec.scrollIntoView({ behavior: "smooth", block: "center" });
      c.sec.classList.add("flash");
      setTimeout(function () { c.sec.classList.remove("flash"); }, 2000);
    };
  });
}

function init() {
  buildCards();
  var leg = document.getElementById("legend");
  [["up candle", "#2dd4bf"], ["down candle", "#fb7185"], ["EMA9 / MACD", "#fbbf24"],
   ["EMA20 / signal", "#a78bfa"], ["VWAP (dashed)", "#60a5fa"], ["resistance", "#f472b6"],
   ["support", "#38bdf8"], ["BUY / entry ▲", "#4ade80"], ["SELL / exit ▼", "#f87171"],
   ["2R target", "#34d399"], ["▱ formation", "#fde68a"]].forEach(function (p) {
    var s = h("span", {}, leg);
    h("i", { style: "border-top-color:" + p[1] }, s);
    h("span", {}, s, p[0]);
  });
  h("p", { class: "hint" }, leg.parentNode,
    "Drag to pan (vertically too, once V-zoomed) · ctrl+scroll or trackpad pinch to zoom both axes · "
    + "click a chart then use +/− zoom, 0 reset, ←/→ pan, ↑/↓ price. "
    + "A dashed level marked ? is a single unconfirmed pivot; solid ones are structural. "
    + "A faint dashed formation sits on a candle under " + WEAK + "× the recent average range — "
    + "the label is right, the candle is not worth acting on.");
  var syms = {}, exits = {};
  DATA.trades.forEach(function (t) { syms[t.symbol] = 1; exits[t.exit_reason] = 1; });
  Object.keys(syms).sort().forEach(function (s) {
    h("option", { value: s }, document.getElementById("f-sym"), s);
  });
  Object.keys(exits).sort().forEach(function (s) {
    h("option", { value: s }, document.getElementById("f-exit"), nice(s));
  });
  ["f-sym", "f-exit", "f-out"].forEach(function (id) {
    document.getElementById(id).onchange = render;
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
