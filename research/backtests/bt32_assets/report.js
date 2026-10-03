/* BT32 — per-trade charts drawn with TradingView Lightweight Charts (v5,
   Apache-2.0, inlined by bt32_strategy_report.build_html; no CDN).

   Each card is one chart with three panes, in the /momentum order:
     price  — candles, EMA9, EMA20, EMA200, VWAP, holding period, every
              support/resistance level, stop, target, BUY/SELL markers and
              candlestick formations
     MACD   — 12/26/9 line, signal and histogram
     volume — up/down bars and the 20-prior-bar average the engine's
              volume_ratio divides by

   Indicators are computed HERE from the raw 1-minute bars with the same
   functions the SVG version used (verified against the engine's pandas
   definitions), so switching the renderer changed no number.

   Times: the bars carry exchange wall-clock HH:MM. They are encoded as UTC
   seconds and formatted in UTC, so the axis shows IST as printed, with no
   browser-timezone shift. */
var LWC = window.LightweightCharts;
var DEFAULT_ZOOM = { "1m": 8, "5m": 2 };
var MIN_BARS = 15, CHART_H = 600;
var WEAK = DATA.weak_strength == null ? 0 : DATA.weak_strength;
var cards = [], tf = "5m", levelMode = "key";
if (DATA.default_tf) tf = DATA.default_tf;
if (DATA.default_levels) levelMode = DATA.default_levels;
var COL = {
  up: "#2dd4bf", dn: "#fb7185", ema9: "#fbbf24", ema20: "#a78bfa", ema200: "#94a3b8",
  vwap: "#60a5fa", res: "#f472b6", sup: "#38bdf8", buy: "#4ade80", sell: "#f87171",
  target: "#34d399", stop: "#fb7185", pattern: "#fde68a", volAvg: "#e2e8f0"
};

function h(tag, attrs, parent, text) {
  var e = document.createElement(tag);
  for (var k in attrs) { if (k === "class") e.className = attrs[k]; else e.setAttribute(k, attrs[k]); }
  if (text != null) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
}
function pct(x) { return (x >= 0 ? "+" : "") + x.toFixed(2) + "%"; }
function inr(x) { return "₹" + Math.round(x).toLocaleString("en-IN"); }
function cls(x) { return x > 0 ? "pos" : x < 0 ? "neg" : ""; }
function nice(s) { return String(s).split("_").join(" "); }
function fmtVol(v) {
  return v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? (v / 1e3).toFixed(1) + "K" : String(Math.round(v));
}
function fx(v, d) { return v == null || !isFinite(v) ? "–" : v.toFixed(d == null ? 2 : d); }
function alpha(hex, a) {
  var n = parseInt(hex.slice(1), 16);
  return "rgba(" + (n >> 16 & 255) + "," + (n >> 8 & 255) + "," + (n & 255) + "," + a + ")";
}
function pad2(n) { return String(n).padStart(2, "0"); }
function utcSec(date, hhmm) {
  return Date.UTC(+date.slice(0, 4), +date.slice(5, 7) - 1, +date.slice(8, 10),
                  +hhmm.slice(0, 2), +hhmm.slice(3, 5)) / 1000;
}
function hhmm(t) { var d = new Date(t * 1000); return pad2(d.getUTCHours()) + ":" + pad2(d.getUTCMinutes()); }

/* ---------- indicators (unchanged from the SVG renderer) ---------- */
function ema(vals, span) {
  var a = 2 / (span + 1), v = null;
  return vals.map(function (c, i) {
    v = v === null ? c : c * a + v * (1 - a);
    return i < span - 1 ? null : v;
  });
}
/* An EMA seeded at the first non-null input, valid only once `minPeriods`
   real observations have gone in: pandas ewm(adjust=False, min_periods=N),
   which is what the engine's exit logic reads. */
function emaSparse(vals, span, minPeriods) {
  var a = 2 / (span + 1), v = null, seen = 0;
  return vals.map(function (x) {
    if (x === null) return null;
    v = v === null ? x : x * a + v * (1 - a);
    seen += 1;
    return seen < minPeriods ? null : v;
  });
}
/* Mean volume of the `n` bars BEFORE each bar, null until `minP` exist -
   indicators.volume_ratio's denominator (lookback 20, min_periods 10). */
function priorMean(vals, n, minP) {
  return vals.map(function (_, i) {
    var s = Math.max(0, i - n), k = i - s;
    if (k < minP) return null;
    var sum = 0;
    for (var j = s; j < i; j++) sum += vals[j];
    return sum / k;
  });
}
function points(bars) {
  var closes = bars.map(function (b) { return b.c; });
  var e9 = ema(closes, 9), e20 = ema(closes, 20), e200 = emaSparse(closes, 200, 200);
  var fast = ema(closes, 12), slow = ema(closes, 26);
  var macd = fast.map(function (v, i) { return v === null || slow[i] === null ? null : v - slow[i]; });
  var sig = emaSparse(macd, 9, 9);
  var vavg = priorMean(bars.map(function (b) { return b.v; }), 20, 10);
  var cumPv = 0, cumVol = 0;
  return bars.map(function (b, i) {
    cumPv += ((b.h + b.l + b.c) / 3) * b.v;
    cumVol += b.v;
    return {
      t: b.t, o: b.o, h: b.h, l: b.l, c: b.c, v: b.v,
      ema9: e9[i], ema20: e20[i], ema200: e200[i],
      vwap: cumVol ? cumPv / cumVol : null, vavg: vavg[i],
      macd: macd[i], signal: sig[i],
      hist: macd[i] === null || sig[i] === null ? null : macd[i] - sig[i]
    };
  });
}
/* 5-minute buckets aligned to the 09:15 session open, matching engine.resample_5m. */
function fiveMinute(raw) {
  var out = [], cur = null, key = null;
  raw.forEach(function (b) {
    var k = +b.t.slice(0, 2) * 60 + Math.floor(+b.t.slice(3, 5) / 5) * 5;
    if (k !== key) {
      if (cur) out.push(cur);
      key = k;
      cur = { t: pad2(Math.floor(k / 60)) + ":" + pad2(k % 60), o: b.o, h: b.h, l: b.l, c: b.c, v: b.v };
    } else {
      cur.h = Math.max(cur.h, b.h); cur.l = Math.min(cur.l, b.l); cur.c = b.c; cur.v += b.v;
    }
  });
  if (cur) out.push(cur);
  return out;
}
function rawBars(tr) {
  return tr.bars.map(function (r) { return { t: r[0], o: r[1], h: r[2], l: r[3], c: r[4], v: r[5] }; });
}
/* index of the bar CONTAINING a HH:MM stamp (last bar starting at or before it) */
function barIndex(data, t) {
  var best = 0;
  for (var i = 0; i < data.length; i++) if (data[i].t <= t) best = i;
  return best;
}
function barIndexExact(data, t) {
  for (var i = 0; i < data.length; i++) if (data[i].t === t) return i;
  return -1;
}

/* ---------- chart ---------- */
function targetLabel(tr) { return tr.target_label || "Target 2R"; }
/* Short axis titles: dense level sets stack their labels on the price axis,
   so the long names go in the per-card level table instead. */
var SHORT = { session_high: "S-high", session_pivot: "S-pivot", pivot_high: "pivot",
              pivot_low: "pivot", prev_day: "prev day", round: "round", orb: "ORB", shelf: "shelf" };
function shortKind(kind) {
  if (/^frozen/.test(kind)) return "▶ frozen";
  return SHORT[kind] || nice(kind);
}

/* Autoscale covers the candles, the trade's own prices, and only the levels
   close to the action - a distant level would squash the candles; it stays
   off-screen, which is the honest way to say "not near". */
function autoscaleFor(tr) {
  return function (base) {
    var r = base();
    if (!r || !r.priceRange) return r;
    var lo = Math.min(r.priceRange.minValue, tr.entry, tr.stop, tr.target, tr.exit);
    var hi = Math.max(r.priceRange.maxValue, tr.entry, tr.stop, tr.target, tr.exit);
    var span = hi - lo || 1;
    tr.levels.forEach(function (L) {
      if (L.price >= lo - span * 0.12 && L.price <= hi + span * 0.12) {
        lo = Math.min(lo, L.price); hi = Math.max(hi, L.price);
      }
    });
    var pad = Math.max((hi - lo) * 0.04, hi * 0.0005);
    return { priceRange: { minValue: lo - pad, maxValue: hi + pad }, margins: r.margins };
  };
}

function createChart(c) {
  var tr = c.tr;
  var chart = LWC.createChart(c.host, {
    autoSize: true,
    layout: {
      background: { type: "solid", color: "#101922" }, textColor: "#a9bac9", fontSize: 11,
      fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
      panes: { separatorColor: "#1e2b3c", separatorHoverColor: "rgba(148,163,184,0.25)", enableResize: true },
      attributionLogo: true
    },
    grid: { vertLines: { color: "rgba(255,255,255,0.04)" }, horzLines: { color: "rgba(255,255,255,0.06)" } },
    crosshair: { mode: LWC.CrosshairMode.Normal },
    rightPriceScale: { borderColor: "#1e2b3c", minimumWidth: 72 },
    timeScale: { borderColor: "#1e2b3c", timeVisible: true, secondsVisible: false, rightOffset: 3,
                 minBarSpacing: 0.5,
                 tickMarkFormatter: function (t) { return hhmm(t); } },
    localization: { timeFormatter: function (t) { return tr.date + " " + hhmm(t); } },
    // Plain wheel scrolls the PAGE, as before; ctrl+wheel / pinch zooms (see wire()).
    handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: { time: true, price: true },
                   axisDoubleClickReset: { time: true, price: true } }
  });
  var quiet = { priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };
  var s = {};
  // holding period: full-height columns on a hidden overlay scale, drawn first so it sits behind
  s.hold = chart.addSeries(LWC.HistogramSeries, Object.assign({}, quiet, {
    priceScaleId: "hold", color: alpha(COL.up, 0.09), base: 0 }), 0);
  chart.priceScale("hold", 0).applyOptions({ visible: false, scaleMargins: { top: 0, bottom: 0 } });
  s.candles = chart.addSeries(LWC.CandlestickSeries, {
    upColor: COL.up, downColor: COL.dn, wickUpColor: COL.up, wickDownColor: COL.dn,
    borderVisible: false, priceLineVisible: false, lastValueVisible: false,
    autoscaleInfoProvider: autoscaleFor(tr)
  }, 0);
  s.ema9 = chart.addSeries(LWC.LineSeries, Object.assign({}, quiet, { color: COL.ema9, lineWidth: 2 }), 0);
  s.ema20 = chart.addSeries(LWC.LineSeries, Object.assign({}, quiet, { color: COL.ema20, lineWidth: 2 }), 0);
  s.ema200 = chart.addSeries(LWC.LineSeries, Object.assign({}, quiet, { color: COL.ema200, lineWidth: 2 }), 0);
  s.vwap = chart.addSeries(LWC.LineSeries, Object.assign({}, quiet, {
    color: COL.vwap, lineWidth: 2, lineStyle: LWC.LineStyle.Dashed }), 0);

  var macdFmt = { type: "custom", minMove: 0.001, formatter: function (v) { return v.toFixed(3); } };
  s.hist = chart.addSeries(LWC.HistogramSeries, Object.assign({}, quiet, { priceFormat: macdFmt }), 1);
  s.macd = chart.addSeries(LWC.LineSeries, Object.assign({}, quiet, {
    color: COL.ema9, lineWidth: 2, priceFormat: macdFmt }), 1);
  s.signal = chart.addSeries(LWC.LineSeries, Object.assign({}, quiet, {
    color: COL.ema20, lineWidth: 2, priceFormat: macdFmt }), 1);
  s.hist.createPriceLine({ price: 0, color: "rgba(255,255,255,0.25)", lineWidth: 1,
                           lineStyle: LWC.LineStyle.Solid, axisLabelVisible: false });

  var volFmt = { type: "custom", minMove: 1, formatter: fmtVol };
  s.vol = chart.addSeries(LWC.HistogramSeries, Object.assign({}, quiet, { priceFormat: volFmt }), 2);
  s.vavg = chart.addSeries(LWC.LineSeries, Object.assign({}, quiet, {
    color: alpha(COL.volAvg, 0.7), lineWidth: 1, priceFormat: volFmt }), 2);

  var panes = chart.panes();
  panes[0].setStretchFactor(3.4); panes[1].setStretchFactor(1.1); panes[2].setStretchFactor(0.9);

  c.markers = LWC.createSeriesMarkers(s.candles, []);
  c.chart = chart;
  c.s = s;
  c.lines = [];
  drawLines(c);
  chart.subscribeCrosshairMove(function (p) { showLegend(c, p && p.time != null ? c.byTime[p.time] : null); });
  chart.timeScale().subscribeVisibleLogicalRangeChange(function () { updateZoomLabel(c); });
}

/* The key support / resistance are the two levels the ENGINE bracketed the
   trade with at the entry bar (bt17's structural_support / _resistance), so
   they are what the stop and target were read against. They are drawn thick and
   named in full; every other level is context. `levelMode` "key" keeps the
   context faint and unlabelled so the four prices that matter - support,
   resistance, BUY, SELL - read at a glance; "all" labels every level. */
function isKey(tr, L) {
  var k = L.side === "resistance" ? tr.key_resistance : tr.key_support;
  return !!k && Math.abs(k.price - L.price) < 0.005;
}
function drawLines(c) {
  var tr = c.tr, s = c.s;
  c.lines.forEach(function (pl) { s.candles.removePriceLine(pl); });
  c.lines = [];
  var line = function (price, color, style, width, title, label) {
    c.lines.push(s.candles.createPriceLine({ price: price, color: color, lineStyle: style,
      lineWidth: width, axisLabelVisible: label !== false, title: title }));
  };
  // a report built without key levels keeps every label, as before
  var mode = tr.key_support || tr.key_resistance ? levelMode : "all";
  tr.levels.forEach(function (L) {
    if (isKey(tr, L)) return;
    var col = L.side === "resistance" ? COL.res : COL.sup;
    // a level a report marks `emphasis` (e.g. BT52's checkpoint) is always
    // drawn and labelled, whatever the Levels selector says
    if (L.emphasis) {
      line(L.price, L.color || col, LWC.LineStyle.LargeDashed, 2, L.label || nice(L.kind));
      return;
    }
    if (mode === "key") {
      if (!L.structural) return;
      line(L.price, alpha(col, 0.28), LWC.LineStyle.Solid, 1, "", false);
      return;
    }
    line(L.price, alpha(col, L.structural ? 0.75 : 0.45),
         L.structural ? LWC.LineStyle.Solid : LWC.LineStyle.SparseDotted, 1,
         shortKind(L.kind) + (L.structural ? "" : " ?"), L.structural);
  });
  if (tr.key_resistance) line(tr.key_resistance.price, COL.res, LWC.LineStyle.Solid, 3,
                              "RESISTANCE · " + shortKind(tr.key_resistance.kind));
  if (tr.key_support) line(tr.key_support.price, COL.sup, LWC.LineStyle.Solid, 3,
                           "SUPPORT · " + shortKind(tr.key_support.kind));
  line(tr.stop, COL.stop, LWC.LineStyle.Dashed, 2, "Stop");
  line(tr.target, COL.target, LWC.LineStyle.Dashed, 2, targetLabel(tr));
  line(tr.entry, COL.buy, LWC.LineStyle.Solid, 2, "BUY");
  line(tr.exit, COL.sell, LWC.LineStyle.Solid, 2, "SELL");
}

/* load the current timeframe's bars into an existing chart */
function setSeries(c) {
  var tr = c.tr, s = c.s;
  var data = points(tf === "5m" ? fiveMinute(rawBars(tr)) : rawBars(tr));
  var T = data.map(function (b) { return utcSec(tr.date, b.t); });
  var ln = function (key) {
    return data.map(function (b, i) { return b[key] == null ? { time: T[i] } : { time: T[i], value: b[key] }; });
  };
  var eIdx = barIndex(data, tr.entry_time), xIdx = barIndex(data, tr.exit_time);
  // stored BEFORE setData: the chart's range-change callbacks read them
  c.data = data;
  c.tf = tf;
  c.eIdx = eIdx;
  c.byTime = {};
  T.forEach(function (t, i) { c.byTime[t] = i; });
  s.hold.setData(data.map(function (b, i) {
    return i >= eIdx && i <= xIdx ? { time: T[i], value: 1 } : { time: T[i] };
  }));
  s.candles.setData(data.map(function (b, i) { return { time: T[i], open: b.o, high: b.h, low: b.l, close: b.c }; }));
  s.ema9.setData(ln("ema9")); s.ema20.setData(ln("ema20"));
  s.ema200.setData(ln("ema200")); s.vwap.setData(ln("vwap"));
  s.macd.setData(ln("macd")); s.signal.setData(ln("signal"));
  s.hist.setData(data.map(function (b, i) {
    return b.hist == null ? { time: T[i] }
      : { time: T[i], value: b.hist, color: alpha(b.hist >= 0 ? COL.up : COL.dn, 0.75) };
  }));
  s.vol.setData(data.map(function (b, i) {
    return { time: T[i], value: b.v, color: alpha(b.c >= b.o ? COL.up : COL.dn, 0.6) };
  }));
  s.vavg.setData(ln("vavg"));

  /* BUY / SELL arrows and the formations seen on THIS timeframe */
  var mk = [
    { time: T[eIdx], position: "belowBar", shape: "arrowUp", color: COL.buy, size: 2,
      text: "BUY " + tr.entry.toFixed(2) + " @ " + tr.entry_time },
    { time: T[xIdx], position: "aboveBar", shape: "arrowDown", color: COL.sell, size: 2,
      text: "SELL " + tr.exit.toFixed(2) + " @ " + tr.exit_time + " · " + nice(tr.exit_reason) }
  ];
  (tr.patterns || []).forEach(function (m) {
    if (m.timeframe !== tf) return;
    var i = barIndexExact(data, m.end);
    if (i < 0) return;
    // A correctly-named formation on a candle much smaller than this stock's
    // recent average is drawn faint (candles.STRENGTH_WEAK_BELOW).
    var weak = m.strength < WEAK;
    mk.push({ time: T[i], position: m.direction === "bearish" ? "aboveBar" : "belowBar",
              shape: "circle", size: 0.6, color: alpha(COL.pattern, weak ? 0.4 : 0.95),
              text: nice(m.name) + " " + m.strength.toFixed(2) + "×" });
  });
  mk.sort(function (a, b) { return a.time - b.time; });
  c.markers.setMarkers(mk);

  resetView(c);
  showLegend(c, null);
}

/* ---------- view control ---------- */
function resetView(c) {
  var n = c.data.length;
  var visible = Math.min(n, Math.max(MIN_BARS, Math.ceil(n / DEFAULT_ZOOM[tf])));
  var from = Math.min(Math.max(0, c.eIdx - Math.floor(visible / 2)), Math.max(0, n - visible));
  var apply = function () {
    c.s.candles.priceScale().setAutoScale(true);
    c.chart.timeScale().setVisibleLogicalRange({ from: from - 0.5, to: from + visible - 0.5 });
  };
  apply();
  // After setData the chart re-applies its own bar spacing on the next frame,
  // which would keep the previous timeframe's window; set it again after that.
  requestAnimationFrame(function () { requestAnimationFrame(apply); });
}
function range(c) { return c.chart.timeScale().getVisibleLogicalRange(); }
/* zoom the time axis by `f` around logical index `anchor`, between the full
   session and MIN_BARS candles */
function zoomBy(c, f, anchor) {
  var r = range(c);
  if (!r) return;
  var n = c.data.length, a = anchor == null ? (r.from + r.to) / 2 : anchor;
  var k = (a - r.from) / (r.to - r.from || 1);
  var w = Math.min(n, Math.max(MIN_BARS, (r.to - r.from) / f));
  c.chart.timeScale().setVisibleLogicalRange({ from: a - k * w, to: a + (1 - k) * w });
}
/* pan by 70% of the window, stopping at the session's ends */
function panBy(c, dir) {
  var r = range(c);
  if (!r) return;
  var n = c.data.length, w = r.to - r.from;
  var from = Math.min(Math.max(-0.5, r.from + w * 0.7 * dir), Math.max(-0.5, n - 0.5 - w));
  c.chart.timeScale().setVisibleLogicalRange({ from: from, to: from + w });
}
function priceRange(c) {
  var ps = c.s.candles.priceScale();
  return ps.getVisibleRange();
}
function setPrice(c, from, to) {
  var ps = c.s.candles.priceScale();
  ps.setAutoScale(false);
  ps.setVisibleRange({ from: from, to: to });
}
function pZoomBy(c, f, anchor) {
  var r = priceRange(c);
  if (!r) return;
  var a = anchor == null ? (r.from + r.to) / 2 : anchor;
  var k = (a - r.from) / (r.to - r.from || 1), w = (r.to - r.from) / f;
  setPrice(c, a - k * w, a + (1 - k) * w);
}
function pPanBy(c, dir) {
  var r = priceRange(c);
  if (!r) return;
  var d = (r.to - r.from) * 0.3 * dir;
  setPrice(c, r.from + d, r.to + d);
}
function updateZoomLabel(c) {
  var r = range(c);
  if (!r || !c.data) return;
  var shown = Math.max(0, Math.round(Math.min(c.data.length - 1, r.to) - Math.max(0, r.from)) + 1);
  c.zlabel.textContent = shown >= c.data.length ? "Full session · " + c.data.length + " " + tf + " candles"
    : shown + " of " + c.data.length + " " + tf + " candles";
}

/* TradingView-style legend: values of the bar under the crosshair (entry bar otherwise) */
function showLegend(c, i) {
  if (!c.data) return;
  var b = c.data[i == null ? c.eIdx : i], tr = c.tr;
  if (!b) return;
  var chg = b.c - b.o;
  var sw = function (col, name, v) {
    return "<span><i style='background:" + col + "'></i>" + name + " <b>" + v + "</b></span>";
  };
  c.legend.innerHTML =
    "<div class='l1'><b>" + tr.symbol + "</b> · " + tf + " · " + b.t
    + (i == null ? " <em>(entry bar)</em>" : "")
    + " &nbsp; O <b>" + fx(b.o) + "</b> H <b>" + fx(b.h) + "</b> L <b>" + fx(b.l) + "</b> C <b class='"
    + cls(chg) + "'>" + fx(b.c) + "</b> <span class='" + cls(chg) + "'>" + (chg >= 0 ? "+" : "") + chg.toFixed(2)
    + "</span></div><div class='l2'>"
    + sw(COL.ema9, "EMA9", fx(b.ema9)) + sw(COL.ema20, "EMA20", fx(b.ema20))
    + sw(COL.ema200, "EMA200", fx(b.ema200)) + sw(COL.vwap, "VWAP", fx(b.vwap)) + "</div>";
  c.readout.innerHTML =
    sw(COL.ema9, "MACD 12/26/9", fx(b.macd, 3)) + sw(COL.ema20, "signal", fx(b.signal, 3))
    + sw(alpha(COL.up, 0.75), "hist", fx(b.hist, 3))
    + sw(alpha(COL.up, 0.6), "vol", fmtVol(b.v))
    + sw(alpha(COL.volAvg, 0.7), "avg (20 prior)", b.vavg == null ? "–" : fmtVol(b.vavg))
    + (b.vavg ? sw("transparent", "bar RVOL", (b.v / b.vavg).toFixed(1) + "×") : "")
    + sw("transparent", "day RVOL", tr.rvol.toFixed(1) + "×");
}

/* ---------- interaction ---------- */
function wire(c) {
  // ctrl+wheel and trackpad pinch zoom both axes around the pointer; plain scroll is the page's.
  c.host.addEventListener("wheel", function (ev) {
    if (!ev.ctrlKey || !c.chart) return;
    ev.preventDefault();
    ev.stopPropagation();
    var f = Math.exp(-ev.deltaY * 0.01), rect = c.host.getBoundingClientRect();
    var x = ev.clientX - rect.left, y = ev.clientY - rect.top;
    zoomBy(c, f, c.chart.timeScale().coordinateToLogical(x));
    if (y < c.chart.paneSize(0).height) pZoomBy(c, f, c.s.candles.coordinateToPrice(y));
  }, { passive: false, capture: true });
  c.host.addEventListener("pointerdown", function () { c.sec.focus({ preventScroll: true }); });
  c.sec.addEventListener("keydown", function (ev) {
    if (!c.chart) return;
    var k = ev.key, done = true;
    if (k === "+" || k === "=") zoomBy(c, 2);
    else if (k === "-") zoomBy(c, 0.5);
    else if (k === "0") resetView(c);
    else if (k === "ArrowLeft") panBy(c, -1);
    else if (k === "ArrowRight") panBy(c, 1);
    else if (k === "ArrowUp") pPanBy(c, 1);
    else if (k === "ArrowDown") pPanBy(c, -1);
    else done = false;
    if (done) ev.preventDefault();
  });
}

/* ---------- full screen ----------
   Native Fullscreen API where allowed; a fixed-position fallback where it is
   not (embedded webviews reject the request). autoSize redraws the chart. */
function toggleFull(c) {
  var sec = c.sec;
  if (document.fullscreenElement === sec) { document.exitFullscreen(); return; }
  if (sec.classList.contains("zoomed")) {
    sec.classList.remove("zoomed");
    document.body.classList.remove("has-zoom");
    return;
  }
  var fallback = function () {
    sec.classList.add("zoomed");
    document.body.classList.add("has-zoom");
  };
  if (sec.requestFullscreen) sec.requestFullscreen().catch(fallback);
  else fallback();
}
document.addEventListener("keydown", function (ev) {
  if (ev.key !== "Escape") return;
  var z = document.querySelector(".card.zoomed");
  if (z) cards.forEach(function (c) { if (c.sec === z) toggleFull(c); });
});

/* ---------- cards ---------- */
/* Charts are built when a card first nears the viewport: one canvas chart per
   trade, created up front for 100+ trades, is slow to open for no benefit. */
var observer = "IntersectionObserver" in window ? new IntersectionObserver(function (entries) {
  entries.forEach(function (e) {
    if (!e.isIntersecting) return;
    var c = cards[+e.target.getAttribute("data-i")];
    ensureChart(c);
  });
}, { rootMargin: "900px 0px" }) : null;

/* Lazy reports (a dashboard run with thousands of trades) ship each trade
   WITHOUT its bars/levels/formations; they are fetched from DATA.detail_url the
   first time a card needs its chart, then merged into the trade. */
function loadDetail(c) {
  if (c.loading || c.failed) return;
  c.loading = true;
  c.host.innerHTML = "";
  h("div", { class: "loading" }, c.host, "Building chart for " + c.tr.symbol + " " + c.tr.date + "…");
  fetch(DATA.detail_url + c.i).then(function (r) {
    return r.ok ? r.json() : r.json().then(function (j) { throw new Error(j.error || r.statusText); });
  }).then(function (d) {
    Object.assign(c.tr, d);
    c.host.innerHTML = "";
    c.loading = false;
    hydrate(c);
    ensureChart(c);
  }).catch(function (e) {
    c.loading = false; c.failed = true;
    c.host.innerHTML = "";
    h("div", { class: "loading" }, c.host, "No chart for this trade: " + e.message);
  });
}
function ensureChart(c) {
  if (DATA.lazy && !c.tr.bars) { loadDetail(c); return; }
  if (!c.chart) createChart(c);
  if (c.tf !== tf) setSeries(c);
}
/* key-level pills and the level table need the detail, so they fill in once it is there */
function hydrate(c) {
  if (c.hydrated || !c.tr.levels) return;
  c.hydrated = true;
  keyStrip(c.keysHost, c.tr);
  levelTable(c.lvHost, c.tr);
}

function buildCards() {
  var host = document.getElementById("charts");
  DATA.trades.forEach(function (tr, i) {
    var sec = h("section", { class: "card", tabindex: "0", "data-i": String(i) }, host);
    var head = h("div", { class: "card-head" }, sec);
    var left = h("div", {}, head);
    h("h2", {}, left, tr.symbol + "  " + tr.date + "   " + nice(tr.setup));
    var meta = h("div", { class: "meta" }, left);
    meta.innerHTML =
      "in <b>" + tr.entry_time + "</b> @ <b>" + tr.entry.toFixed(2) + "</b>"
      + " · stop <b>" + tr.stop.toFixed(2) + "</b>"
      + " · " + targetLabel(tr).toLowerCase() + " <b>" + tr.target.toFixed(2) + "</b>"
      + " · out <b>" + tr.exit_time + "</b> @ <b>" + tr.exit.toFixed(2) + "</b>"
      + " (" + nice(tr.exit_reason) + ")"
      + " · qty <b>" + tr.qty + "</b>"
      + " · day <b>" + tr.day_chg_pct.toFixed(1) + "%</b>";
    var keysHost = h("div", {}, left);
    var kpi = h("div", { class: "kpi" }, head);
    kpi.innerHTML = "<b class='" + cls(tr.gross_pct) + "'>" + pct(tr.gross_pct) + "</b>"
      + "gross · net " + inr(tr.net_real_inr) + " @ real"
      + "<br>" + inr(tr.net_stress_inr) + " @ stress";

    var bar = h("div", { class: "toolbar" }, sec);
    var zlabel = h("span", { class: "zlabel" }, bar);
    var grp = h("div", { class: "btns" }, bar);
    var c = { sec: sec, tr: tr, i: i, zlabel: zlabel, chart: null, tf: null };
    function mk(txt, title, fn) {
      var b = h("button", { type: "button", title: title }, grp, txt);
      b.onclick = function () { ensureChart(c); if (c.chart) fn(); };
      return b;
    }
    mk("←", "Earlier candles", function () { panBy(c, -1); });
    mk("− Zoom", "Zoom out", function () { zoomBy(c, 0.5); });
    mk("+ Zoom", "Zoom in", function () { zoomBy(c, 2); });
    mk("→", "Later candles", function () { panBy(c, 1); });
    h("span", { class: "sep" }, grp);
    mk("↑", "Higher prices", function () { pPanBy(c, 1); });
    mk("− V-Zoom", "Price zoom out", function () { pZoomBy(c, 0.5); });
    mk("+ V-Zoom", "Price zoom in", function () { pZoomBy(c, 2); });
    mk("↓", "Lower prices", function () { pPanBy(c, -1); });
    h("span", { class: "sep" }, grp);
    mk("Reset", "Reset both axes", function () { resetView(c); });
    mk("⛶ Full screen", "Full screen (Esc to exit)", function () { toggleFull(c); });

    var wrap = h("div", { class: "chart-wrap" }, sec);
    c.host = h("div", { class: "chart", role: "img", "aria-label": tr.symbol + " candlestick chart" }, wrap);
    c.host.style.height = CHART_H + "px";
    c.legend = h("div", { class: "chart-legend" }, wrap);
    c.readout = h("div", { class: "readout" }, sec);
    c.keysHost = keysHost;
    c.lvHost = h("div", {}, sec);
    hydrate(c);
    cards.push(c);
    wire(c);
    if (observer) observer.observe(sec); else ensureChart(c);
  });
}

/* the four prices a reader looks for first, as coloured pills under the title */
function keyStrip(parent, tr) {
  var strip = h("div", { class: "keys" }, parent);
  var pill = function (cl, label, price, extra) {
    var p = h("span", { class: "key " + cl }, strip);
    h("b", {}, p, label);
    h("span", {}, p, " " + price.toFixed(2) + (extra ? " · " + extra : ""));
  };
  var dist = function (px) { return pct(100 * (px - tr.entry) / tr.entry) + " vs buy"; };
  if (tr.key_support) pill("sup", "SUPPORT", tr.key_support.price,
                           nice(tr.key_support.kind) + " · " + dist(tr.key_support.price));
  else h("span", { class: "key none" }, strip, "no support below entry");
  pill("buy", "BUY", tr.entry, tr.entry_time);
  pill("sell", "SELL", tr.exit, tr.exit_time + " · " + nice(tr.exit_reason));
  if (tr.key_resistance) pill("res", "RESISTANCE", tr.key_resistance.price,
                              nice(tr.key_resistance.kind) + " · " + dist(tr.key_resistance.price));
  else h("span", { class: "key none" }, strip, "no resistance above entry");
}

/* every level on the chart, nearest the entry first, with its distance in R
   (1R = entry − stop, the trade's own risk unit) */
function levelTable(sec, tr) {
  if (!tr.levels || !tr.levels.length) return;
  var risk = tr.entry - tr.stop;
  var rows = tr.levels.slice().sort(function (a, b) {
    return Math.abs(a.price - tr.entry) - Math.abs(b.price - tr.entry);
  });
  var d = h("details", { class: "levels" }, sec);
  var res = rows.filter(function (L) { return L.side === "resistance"; }).length;
  h("summary", {}, d, "Levels on this chart — " + res + " resistance, " + (rows.length - res) + " support");
  var t = h("table", {}, d);
  var hr = h("tr", {}, h("thead", {}, t));
  ["price", "side", "kind", "touches", "structural", "vs entry", "in R"].forEach(function (x, i) {
    h("th", { class: i === 0 || i >= 5 ? "num" : "" }, hr, x);
  });
  var tb = h("tbody", {}, t);
  rows.forEach(function (L) {
    var key = isKey(tr, L);
    var r = h("tr", key ? { class: "keyrow" } : {}, tb), dist = L.price - tr.entry;
    h("td", { class: "num" }, r, L.price.toFixed(2));
    h("td", { class: L.side === "resistance" ? "res" : "sup" }, r,
      key ? "KEY " + L.side : L.side);
    h("td", {}, r, nice(L.kind));
    h("td", {}, r, L.touches ? String(L.touches) : "–");
    h("td", {}, r, L.structural ? "yes" : "no");
    h("td", { class: "num" }, r, (dist >= 0 ? "+" : "") + (100 * dist / tr.entry).toFixed(2) + "%");
    h("td", { class: "num" }, r, risk > 0 ? (dist / risk).toFixed(2) : "–");
  });
}

/* ---------- group by ----------
   "Target hit" = the trade's EXIT was the target fill (exit_reason "target").
   Every other exit - stop, trailing/checkpoint stop, EMA9/MACD/volume exits,
   support break, time - means the target was not what closed the trade. */
var WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
var GROUPS = {
  exit_reason: { label: "Exit reason", of: function (t) { return nice(t.exit_reason); } },
  target_hit: { label: "Target hit", fixed: ["Target hit", "Target not hit"],
                of: function (t) { return t.exit_reason === "target" ? "Target hit" : "Target not hit"; } },
  outcome: { label: "Outcome (net @ real costs)", fixed: ["Winner", "Loser"],
             of: function (t) { return t.net_real_inr > 0 ? "Winner" : "Loser"; } },
  symbol: { label: "Symbol", of: function (t) { return t.symbol; } },
  setup: { label: "Setup", of: function (t) { return nice(t.setup); } },
  month: { label: "Month", byKey: true, of: function (t) { return t.date.slice(0, 7); } },
  weekday: { label: "Weekday", byKey: true,
             of: function (t) { return WEEKDAYS[(new Date(t.date + "T00:00:00Z").getUTCDay() + 6) % 7]; },
             order: WEEKDAYS },
  entry_hour: { label: "Entry hour", byKey: true, of: function (t) { return t.entry_time.slice(0, 2) + ":00"; } }
};
var groupKey = DATA.default_group && GROUPS[DATA.default_group] ? DATA.default_group : "";
var openGroups = {};   // "<groupKey>:<name>" -> true, so a re-render keeps what the reader opened
/* A group of 1,800 trades would lay out 1,800 cards when opened. Show PAGE at a
   time; "Show more" reveals the next PAGE. `shown[gid]` survives a re-render. */
var PAGE = 20, PAGE_ABOVE = 60, shown = {};   // groups up to PAGE_ABOVE trades show in full
function first(list) { return list.length <= PAGE_ABOVE ? Infinity : PAGE; }
function showUpTo(list, gid, n) {
  shown[gid] = Math.max(shown[gid] || first(list), n);
  list.forEach(function (c, i) { c.gi = i; c.gid = gid; c.list = list; c.sec.style.display = i < shown[gid] || c.pinned ? "" : "none"; });
}
function pager(list, gid, parent) {
  showUpTo(list, gid, shown[gid] || first(list));
  if (list.length <= PAGE_ABOVE) return;
  var bar = h("div", { class: "more" }, parent);
  var btn = h("button", { type: "button" }, bar);
  var label = function () {
    var rest = list.length - shown[gid];
    btn.textContent = rest > 0 ? "Show " + Math.min(PAGE, rest) + " more · " + rest + " of " + list.length + " not shown" : "";
    bar.style.display = rest > 0 ? "" : "none";
  };
  bar.refresh = label;
  list.bar = bar;
  btn.onclick = function () { showUpTo(list, gid, shown[gid] + PAGE); label(); };
  label();
}
/* a trade picked from the table must be on screen: open its group, and reveal just that card */
function reveal(c) {
  if (c.group && c.group.classList.contains("collapsed")) {
    c.group.classList.remove("collapsed");
    openGroups[groupKey + ":" + c.group.querySelector(".gname").textContent] = true;
  }
  if (c.list && c.gi >= (shown[c.gid] || first(c.list))) {
    c.pinned = true;   // just this one: not every card between the page and it
    c.sec.style.display = "";
  }
}
function median(a) {
  if (!a.length) return 0;
  var b = a.slice().sort(function (x, y) { return x - y; }), m = b.length >> 1;
  return b.length % 2 ? b[m] : (b[m - 1] + b[m]) / 2;
}
function mean(a, f) { return a.length ? a.reduce(function (x, c) { return x + f(c.tr); }, 0) / a.length : 0; }
function stats(list) {
  var n = list.length;
  return {
    med: median(list.map(function (c) { return c.tr.net_real_inr; })),
    n: n, gross: mean(list, function (t) { return t.gross_pct; }),
    net: mean(list, function (t) { return t.net_real_pct; }),
    win: n ? 100 * list.filter(function (c) { return c.tr.net_real_inr > 0; }).length / n : 0,
    inr: list.reduce(function (x, c) { return x + c.tr.net_real_inr; }, 0)
  };
}
function groupList(keep) {
  var def = GROUPS[groupKey], by = {};
  keep.forEach(function (c) { (by[def.of(c.tr)] = by[def.of(c.tr)] || []).push(c); });
  var names = Object.keys(by);
  names.sort(function (a, b) {
    var o = def.fixed || def.order;
    if (o) return (o.indexOf(a) < 0 ? 99 : o.indexOf(a)) - (o.indexOf(b) < 0 ? 99 : o.indexOf(b));
    return def.byKey ? (a > b ? 1 : a < b ? -1 : 0) : by[b].length - by[a].length;
  });
  return names.map(function (nm) {
    by[nm].sort(function (a, b) {
      return a.tr.date + a.tr.entry_time > b.tr.date + b.tr.entry_time ? 1 : -1;
    });
    return { name: nm, cards: by[nm], st: stats(by[nm]) };
  });
}
function statSpans(parent, st, total) {
  var sp = function (html) { h("span", { class: "gstat" }, parent).innerHTML = html; };
  sp("<b>" + st.n + "</b> trades" + (total ? " (" + (100 * st.n / total).toFixed(1) + "%)" : ""));
  sp("gross <b class='" + cls(st.gross) + "'>" + pct(st.gross) + "</b>/trade");
  sp("net @ real <b class='" + cls(st.net) + "'>" + pct(st.net) + "</b>/trade");
  sp("win <b>" + st.win.toFixed(0) + "%</b>");
  sp("median <b class='" + cls(st.med) + "'>" + inr(st.med) + "</b>/trade");
  sp("net <b class='" + cls(st.inr) + "'>" + inr(st.inr) + "</b>");
}
/* put the cards back under #charts in their original order, then (if grouping)
   gather the visible ones under one collapsible header per group */
function layoutGroups(keep) {
  var host = document.getElementById("charts"), sum = document.getElementById("gsum");
  cards.forEach(function (c) { host.appendChild(c.sec); c.group = null; c.pinned = false; });
  Array.prototype.slice.call(host.querySelectorAll(".group, .more")).forEach(function (g) { g.remove(); });
  sum.innerHTML = "";
  var btns = document.getElementById("grp-btns");
  if (btns) btns.style.display = groupKey ? "" : "none";
  if (!groupKey) { pager(keep, "flat", host); return; }
  var groups = groupList(keep), total = keep.length;
  var panel = h("div", { class: "panel" }, sum);
  h("h3", {}, panel, "By " + GROUPS[groupKey].label.toLowerCase() + " ").appendChild(
    h("span", {}, null, "— " + groups.length + " groups · click a row to open it"));
  var maxN = Math.max.apply(null, groups.map(function (g) { return g.st.n; }).concat([1]));
  var t = h("table", {}, h("div", { class: "scroll" }, panel));
  var hr = h("tr", {}, h("thead", {}, t));
  ["group", "trades", "", "gross/trade", "net @ real/trade", "win %", "median ₹/trade", "net ₹"].forEach(function (x, i) {
    h("th", { class: i ? "num" : "" }, hr, x);
  });
  var tb = h("tbody", {}, t);
  groups.forEach(function (g) {
    var gid = groupKey + ":" + g.name;
    var wrapG = h("div", { class: "group" + (openGroups[gid] ? "" : " collapsed") }, host);
    var toggle = function (collapsed) {
      wrapG.classList.toggle("collapsed", collapsed);
      openGroups[gid] = !collapsed;
    };
    var head = h("div", { class: "group-head" }, wrapG);
    h("span", { class: "gname" }, head, g.name);
    statSpans(head, g.st, total);
    var body = h("div", { class: "group-body" }, wrapG);
    g.cards.forEach(function (c) { body.appendChild(c.sec); c.group = wrapG; });
    pager(g.cards, gid, body);
    head.onclick = function () { toggle(!wrapG.classList.contains("collapsed")); };
    var row = h("tr", { class: "gr-row" }, tb);
    h("td", { class: "gr-name" }, row, g.name);
    h("td", { class: "num" }, row, String(g.st.n));
    var bar = h("td", { class: "num" }, row);
    h("span", { class: "gr-bar", style: "width:" + Math.max(2, 70 * g.st.n / maxN) + "px" }, bar);
    h("td", { class: "num " + cls(g.st.gross) }, row, pct(g.st.gross));
    h("td", { class: "num " + cls(g.st.net) }, row, pct(g.st.net));
    h("td", { class: "num" }, row, g.st.win.toFixed(0) + "%");
    h("td", { class: "num " + cls(g.st.med) }, row, inr(g.st.med));
    h("td", { class: "num " + cls(g.st.inr) }, row, inr(g.st.inr));
    row.onclick = function () {
      toggle(false);
      wrapG.scrollIntoView({ behavior: "smooth", block: "start" });
    };
  });
  var all = h("tr", { class: "gr-total" }, tb), st = stats(keep);
  h("td", { class: "gr-name" }, all, "All");
  h("td", { class: "num" }, all, String(st.n));
  h("td", {}, all);
  h("td", { class: "num " + cls(st.gross) }, all, pct(st.gross));
  h("td", { class: "num " + cls(st.net) }, all, pct(st.net));
  h("td", { class: "num" }, all, st.win.toFixed(0) + "%");
  h("td", { class: "num " + cls(st.med) }, all, inr(st.med));
  h("td", { class: "num " + cls(st.inr) }, all, inr(st.inr));
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
  layoutGroups(keep);   // also decides which of the kept cards are on this page
  keep.forEach(function (c) {
    if (c.chart && c.tf !== tf) setSeries(c);
  });
  var tb = document.querySelector("#det tbody");
  tb.innerHTML = "";
  keep.slice().sort(function (a, b) {
    var x = a.tr[sortKey], y = b.tr[sortKey];
    return (x > y ? 1 : x < y ? -1 : 0) * sortDir;
  }).forEach(function (c) {
    var row = h("tr", {}, tb);
    h("td", {}, row, c.tr.date.slice(5));
    h("td", {}, row, c.tr.symbol);
    h("td", {}, row, c.tr.entry_time);
    h("td", {}, row, nice(c.tr.exit_reason));
    h("td", { class: "num " + cls(c.tr.gross_pct) }, row, pct(c.tr.gross_pct));
    h("td", { class: "num " + cls(c.tr.net_real_inr) }, row, inr(c.tr.net_real_inr));
    row.onclick = function () {
      reveal(c);
      ensureChart(c);
      c.sec.scrollIntoView({ behavior: "smooth", block: "center" });
      c.sec.classList.add("flash");
      setTimeout(function () { c.sec.classList.remove("flash"); }, 2000);
    };
  });
}

function init() {
  buildCards();
  var leg = document.getElementById("legend");
  [["up candle", COL.up], ["down candle", COL.dn], ["EMA9 / MACD", COL.ema9],
   ["EMA20 / signal", COL.ema20], ["EMA200", COL.ema200], ["VWAP (dashed)", COL.vwap],
   ["RESISTANCE (thick) · other resistance (thin)", COL.res],
   ["SUPPORT (thick) · other support (thin)", COL.sup], ["BUY / entry ▲", COL.buy],
   ["SELL / exit ▼", COL.sell], ["Stop (dashed)", COL.stop], ["Target (dashed)", COL.target],
   ["vol avg, 20 prior bars", alpha(COL.volAvg, 0.7)], ["● formation", COL.pattern],
   ["holding period", alpha(COL.up, 0.35)]].forEach(function (p) {
    var sp = h("span", {}, leg);
    h("i", { style: "border-top-color:" + p[1] }, sp);
    h("span", {}, sp, p[0]);
  });
  h("p", { class: "hint" }, leg.parentNode,
    "Charts by TradingView Lightweight Charts."
  );
  var syms = {}, exits = {};
  DATA.trades.forEach(function (t) { syms[t.symbol] = 1; exits[t.exit_reason] = 1; });
  Object.keys(syms).sort().forEach(function (s) { h("option", { value: s }, document.getElementById("f-sym"), s); });
  Object.keys(exits).sort().forEach(function (s) { h("option", { value: s }, document.getElementById("f-exit"), nice(s)); });
  ["f-sym", "f-exit", "f-out"].forEach(function (id) { document.getElementById(id).onchange = render; });
  var lvSel = document.getElementById("f-lv");
  if (lvSel) {
    lvSel.value = levelMode;
    lvSel.onchange = function () {
      levelMode = this.value;
      cards.forEach(function (c) { if (c.chart) drawLines(c); });
    };
  }
  var grpSel = document.getElementById("f-grp");
  if (grpSel) {
    h("option", { value: "" }, grpSel, "none (flat list)");
    Object.keys(GROUPS).forEach(function (k) { h("option", { value: k }, grpSel, GROUPS[k].label); });
    grpSel.value = groupKey;
    grpSel.onchange = function () { groupKey = this.value; render(); };
    var setAll = function (collapsed) {
      Array.prototype.forEach.call(document.querySelectorAll(".group"), function (g) {
        g.classList.toggle("collapsed", collapsed);
        var nm = g.querySelector(".gname").textContent;
        openGroups[groupKey + ":" + nm] = !collapsed;
      });
    };
    var gb = document.getElementById("grp-btns");
    h("button", { type: "button" }, gb, "Expand all").onclick = function () { setAll(false); };
    h("button", { type: "button" }, gb, "Collapse all").onclick = function () { setAll(true); };
  }
  var tfSel = document.getElementById("f-tf");
  tfSel.value = tf;
  tfSel.onchange = function () { tf = this.value; render(); };
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
