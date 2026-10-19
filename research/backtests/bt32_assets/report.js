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
var MIN_BARS = 15;
/* Pattern names and filing headlines are written on the chart only when "Labels" is on (they pile up on a busy
   session); with it off they are dots / squares, and the legend names them for the candle under the cursor. */
var showLabels = false;
try { showLabels = localStorage.getItem("bt32.labels") === "1"; } catch (e) { /* storage blocked */ }
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
function keyboardAction(el, action, label) {
  el.tabIndex = 0;
  if (label) el.setAttribute("aria-label", label);
  el.addEventListener("keydown", function (ev) {
    if (ev.target !== el) return;
    if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); action(); }
  });
}
function pct(x) { return (x >= 0 ? "+" : "") + x.toFixed(2) + "%"; }
/* US runs (CSV currency USD) are shown in dollars with cents; NSE runs in whole rupees. */
var US = !!(DATA.summary && DATA.summary.currency === "USD"), CURSYM = US ? "$" : "₹";
function inr(x) {
  return US ? (x < 0 ? "−$" : "$") + Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
            : (x < 0 ? "−₹" : "₹") + Math.round(Math.abs(x)).toLocaleString("en-IN");
}
function cls(x) { return x > 0 ? "pos" : x < 0 ? "neg" : ""; }
/* "Check news" result (lab runs only): was there an NSE filing in the 24h before entry? */
function newsBadge(tr) {
  if (!tr.news) return null;
  var map = { news: ["\u26A1 news", "material filing in the 24h before entry"],
              offering: ["\u26A1 offering", "only share-offering filings in the 24h before entry (dilution: not a catalyst)"], minor: ["routine", "only routine / unclear NSE filings in the 24h before entry"],
              none: ["no filing", "no NSE filing in the 24h before entry"],
              unknown: ["news ?", "SEC coverage unavailable: historical ticker has no CIK mapping"],
              error: ["news ?", "could not be checked"], skipped: ["news ?", "not checked"] };
  var m = map[tr.news] || map.skipped, el = document.createElement("span");
  el.className = "newsb " + tr.news;
  el.textContent = m[0];
  el.title = (tr.news === "news" || tr.news === "unknown") && tr.news_items ? tr.news_items.join("\n") : m[1];
  return el;
}
/* ---------- What-if: indicators on ONE trade (lab runs) ---------- */
function toggleWhatIf(c) {
  if (c.wi) { c.wi.style.display = c.wi.style.display === "none" ? "" : "none"; return; }
  var W = DATA.whatif, G = W.glyph;
  var box = c.wi = document.createElement("div");
  box.className = "whatif";
  c.sec.insertBefore(box, c.sec.querySelector(".chart-wrap"));
  box.innerHTML = "<div class='wi-note'>The indicators this run applied are ticked already. Tick or untick others to see what they would have done to <b>this</b> trade only. "
    + "One trade is an illustration, not evidence — use Apply in the lab to test an indicator properly.</div>";
  var picks = {};
  ["entry", "exit"].forEach(function (g) {
    var col = h("div", { class: "wi-col" }, box);
    h("h4", {}, col, g === "entry" ? "Entry indicators (would it be taken?)" : "Exit indicators (where would it get out?)");
    W.plugins.filter(function (p) { return p.group === g; }).forEach(function (p) {
      var line = h("label", { class: "wi-line", title: p.desc }, col);
      var cb = h("input", { type: "checkbox", "aria-label": p.name }, line);
      h("span", {}, line, " " + p.name);
      var inputs = {};
      p.params.forEach(function (q) {
        var el;
        if (q.kind === "select") {
          el = h("select", { "aria-label": p.name + ": " + q.label }, line);
          q.options.forEach(function (o) { var op = h("option", { value: o }, el, o); if (o === q.default) op.selected = true; });
        } else {
          el = h("input", { type: q.kind === "time" ? "text" : "number", value: String(q.default), title: q.label, "aria-label": p.name + ": " + q.label }, line);
          if (q.step) el.step = String(q.step);
        }
        el.className = "wi-p";
        inputs[q.key] = el;
      });
      var pre = (W.selected || []).filter(function (x) { return x.id === p.id; })[0];
      if (pre) {                      // already applied in this run: start ticked, with its parameters
        cb.checked = true;
        Object.keys(inputs).forEach(function (k) { if (pre.params && pre.params[k] !== undefined) inputs[k].value = String(pre.params[k]); });
        line.classList.add("wi-on");
      }
      picks[p.id] = { cb: cb, inputs: inputs };
    });
  });
  var go = h("button", { type: "button", class: "wi-go" }, box, "Run on this trade");
  var out = h("div", { class: "wi-out" }, box);
  go.onclick = function () {
    var sel = Object.keys(picks).filter(function (id) { return picks[id].cb.checked; }).map(function (id) {
      var prm = {}; Object.keys(picks[id].inputs).forEach(function (k) { prm[k] = picks[id].inputs[k].value; });
      return { id: id, params: prm };
    });
    if (!sel.length) { out.textContent = "Tick at least one indicator."; return; }
    out.textContent = "Replaying on the 1-minute bars…";
    fetch(W.url + c.i + "/whatif", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plugins: sel }) })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) { out.textContent = d.error; return; }
        var sv = d.saved, base = d.scenarios[0].trade;
        c.wiMarks = d.scenarios.filter(function (s) { return s.kind === "exit" || s.kind === "combined"; })
          .filter(function (s) { return !s.trade.error; })
          .map(function (s) { return { label: s.label, exit_time: s.trade.exit_time, exit: s.trade.exit }; });
        if (c.chart) setSeries(c);
        var html = "<table><thead><tr><th>Scenario</th><th>Entry</th><th>Out</th><th>Exit</th><th class='num'>Gross</th><th class='num'>Net</th><th class='num'>vs saved</th></tr></thead><tbody>"
          + "<tr class='wi-saved'><td>As saved in this run</td><td>taken</td><td>" + sv.exit_time + " @ " + Number(sv.exit).toFixed(2) + "</td><td>" + nice(sv.exit_reason)
          + "</td><td class='num " + cls(sv.gross_pct) + "'>" + pct(sv.gross_pct) + "</td><td class='num " + cls(sv.net_real_inr) + "'>" + G + Math.round(sv.net_real_inr) + "</td><td></td></tr>";
        d.scenarios.forEach(function (s) {
          var t = s.trade, ok = s.entry_ok;
          if (t.error) { html += "<tr><td>" + s.label + "</td><td colspan='6'>" + t.error + "</td></tr>"; return; }
          var diff = t.net_real_inr - sv.net_real_inr;
          html += "<tr><td>" + s.label + "</td><td class='" + (ok ? "" : "wi-veto") + "'>" + (ok ? "taken" : "✕ vetoed") + "</td><td>"
            + t.exit_time + " @ " + Number(t.exit).toFixed(2) + "</td><td>" + nice(t.exit_reason) + "</td><td class='num " + cls(t.gross_pct) + "'>" + pct(t.gross_pct)
            + "</td><td class='num " + cls(t.net_real_inr) + "'>" + G + Math.round(t.net_real_inr) + "</td><td class='num " + cls(diff) + "'>" + (diff >= 0 ? "+" : "") + G + Math.round(diff) + "</td></tr>";
        });
        out.innerHTML = html + "</tbody></table><div class='wi-note'>Exits that differ from the saved SELL are drawn on the chart as orange WHAT-IF arrows. " + d.note + " Scenarios are built from the plain base trade plus what is ticked. A vetoed trade shows what it would have done if it had been taken.</div>";
      })
      .catch(function (e) { out.textContent = "Failed: " + e; });
  };
}
var CONTEXT = {
  down: { text: "after ↓", col: "#6ee7b7" },
  up: { text: "after ↑", col: "#fda4af" },
  sideways: { text: "sideways", col: "#fde68a" }
};
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
var MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function ymd(t) { var d = new Date(t * 1000); return d.getUTCFullYear() + "-" + pad2(d.getUTCMonth() + 1) + "-" + pad2(d.getUTCDate()); }
function dayLabel(date) { return date.slice(8, 10) + " " + MON[+date.slice(5, 7) - 1]; }        // "2026-02-04" -> "04 Feb"
function toMin(hm) { return +hm.slice(0, 2) * 60 + +hm.slice(3, 5); }
function fromMin(m) { return pad2(Math.floor(m / 60)) + ":" + pad2(m % 60); }

/* ---------- candle sizes and multi-day history ----------
   Every bar carries its session date `d`, its start `t` (HH:MM) and the sortable key `k` = "YYYY-MM-DD HH:MM".
   With History off the chart holds the trade's session only, exactly as before; with it on, the N sessions before
   it (fetched on demand from DATA.detail_url + i + "/history") are prepended. Indicators then run across the days
   (EMAs/MACD continuous, VWAP restarts each session), and 15m / 1h / daily candles become useful. */
var TF_MIN = { "1m": 1, "5m": 5, "15m": 15, "1h": 60, "D": 1440 };
var SESSION_OPEN = US ? 570 : 555;                       // 09:30 ET / 09:15 IST: candles are aligned to the open
var HIST_OPTS = [[0, "Trade day"], [5, "+5 days"], [10, "+10 days"], [21, "+1 month"], [63, "+3 months"]];
var histDefault = 0;
try { histDefault = +(localStorage.getItem("bt32.histDays") || 0) || 0; } catch (e) { /* storage blocked */ }

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
  var cumPv = 0, cumVol = 0, day = null;
  return bars.map(function (b, i) {
    if (b.d !== day) { day = b.d; cumPv = 0; cumVol = 0; }          // VWAP is a per-session line
    cumPv += ((b.h + b.l + b.c) / 3) * b.v;
    cumVol += b.v;
    return {
      d: b.d, t: b.t, k: b.k, o: b.o, h: b.h, l: b.l, c: b.c, v: b.v,
      ema9: e9[i], ema20: e20[i], ema200: e200[i],
      vwap: cumVol ? cumPv / cumVol : null, vavg: vavg[i],
      macd: macd[i], signal: sig[i],
      hist: macd[i] === null || sig[i] === null ? null : macd[i] - sig[i]
    };
  });
}
/* `mins`-minute candles aligned to the session open (09:15 IST / 09:30 ET), never spanning two sessions; for 5m
   this is engine.resample_5m. mins >= 1440 = one candle per session. */
function aggregate(raw, mins) {
  if (mins <= 1) return raw;
  var out = [], cur = null, key = null;
  raw.forEach(function (b) {
    var start = mins >= 1440 ? SESSION_OPEN : SESSION_OPEN + Math.floor((toMin(b.t) - SESSION_OPEN) / mins) * mins;
    var k = b.d + start;
    if (k !== key) {
      if (cur) out.push(cur);
      key = k;
      var t = fromMin(start);
      cur = { d: b.d, t: t, k: b.d + " " + t, o: b.o, h: b.h, l: b.l, c: b.c, v: b.v };
    } else {
      cur.h = Math.max(cur.h, b.h); cur.l = Math.min(cur.l, b.l); cur.c = b.c; cur.v += b.v;
    }
  });
  if (cur) out.push(cur);
  return out;
}
function rawBars(tr) {
  return tr.bars.map(function (r) { return { d: tr.date, t: r[0], k: tr.date + " " + r[0], o: r[1], h: r[2], l: r[3], c: r[4], v: r[5] }; });
}
/* the chart's 1-minute bars: the loaded history sessions (if History is on) + the trade's session */
function allRaw(c) {
  var day = rawBars(c.tr), n = c.histN || 0;
  if (!n || !c.hist) return day;
  var keep = {};
  c.hist.sessions.slice(-n).forEach(function (d) { keep[d] = 1; });
  return c.hist.bars.filter(function (b) { return keep[b.d]; }).concat(day);
}
/* "HH:MM" means that minute on the trade's own date; a full "YYYY-MM-DD HH:MM" key is used as is */
function fullKey(date, t) { return t.length <= 5 ? date + " " + t : t; }
/* index of the bar CONTAINING a stamp (last bar starting at or before it) */
function barIndex(data, t) {
  var key = fullKey(data.day, t), best = 0;
  for (var i = 0; i < data.length; i++) if (data[i].k <= key) best = i;
  return best;
}
function barIndexExact(data, t) {
  var key = fullKey(data.day, t);
  for (var i = 0; i < data.length; i++) if (data[i].k === key) return i;
  return -1;
}
function firstOfDay(data, date) {
  for (var i = 0; i < data.length; i++) if (data[i].d === date) return i;
  return 0;
}
/* the last minute a candle covers, as a key ("2026-02-04 12:39" for the 12:35 5m candle) */
function endKey(b, tfv) {
  var mins = TF_MIN[tfv] || 1;
  return mins >= 1440 ? b.d + " 23:59" : b.d + " " + fromMin(toMin(b.t) + mins - 1);
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
var VZOOM_TRIM = 0.12;
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
    lo -= pad; hi += pad;
    // default vertical zoom: trim ~12% off each end, never past the trade's own prices
    var tLo = Math.min(tr.entry, tr.stop, tr.target, tr.exit) - pad;
    var tHi = Math.max(tr.entry, tr.stop, tr.target, tr.exit) + pad;
    var trim = (hi - lo) * VZOOM_TRIM;
    lo = Math.min(lo + trim, tLo); hi = Math.max(hi - trim, tHi);
    return { priceRange: { minValue: lo, maxValue: hi }, margins: r.margins };
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
                 tickMarkFormatter: function (t, type) { return type < 3 ? dayLabel(ymd(t)) : hhmm(t); } },
    localization: { timeFormatter: function (t) { return c.tf === "D" ? ymd(t) : ymd(t) + " " + hhmm(t); } },
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
  // news filings: a full-height column on the same hidden scale. Unlike a marker it does not shrink with the
  // candle spacing, so a filing stays visible on a 375-candle 1-minute session.
  s.news = chart.addSeries(LWC.HistogramSeries, Object.assign({}, quiet, {
    priceScaleId: "hold", color: alpha("#f59e0b", 0.55), base: 0 }), 0);
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
  panes[0].setStretchFactor(4.6); panes[1].setStretchFactor(1); panes[2].setStretchFactor(0.85);

  c.markers = LWC.createSeriesMarkers(s.candles, []);
  c.chart = chart;
  c.s = s;
  c.lines = [];
  drawLines(c);
  loadUserLines(c);
  vpInit(c);
  chart.subscribeClick(function (p) { onChartClick(c, p); });
  chart.subscribeCrosshairMove(function (p) { showLegend(c, p && p.time != null ? c.byTime[p.time] : null); });
  chart.timeScale().subscribeVisibleLogicalRangeChange(function () { updateZoomLabel(c); });
}

/* ---------- user horizontal lines ----------
   Click "Draw line", then click the price pane to drop a horizontal line at that price; click near an existing
   line (still in draw mode) to remove it. The lines hang on the candle series' price scale, so they show on the
   1-minute and the 5-minute candles alike, and are kept per symbol+date in localStorage. */
var HLINE_COL = "#e2b93b";
function hlineKey(tr) { return "bt32.hlines." + tr.symbol + "." + tr.date; }
function saveUserLines(c) {
  try {
    var k = hlineKey(c.tr), v = c.userLines.map(function (u) { return u.price; });
    if (v.length) localStorage.setItem(k, JSON.stringify(v)); else localStorage.removeItem(k);
  } catch (e) { /* storage blocked: lines last for this view only */ }
}
function addUserLine(c, price) {
  c.userLines.push({ price: price, pl: c.s.candles.createPriceLine({
    price: price, color: HLINE_COL, lineStyle: LWC.LineStyle.Solid, lineWidth: 3,
    axisLabelVisible: true, title: "" }) });
}
function loadUserLines(c) {
  c.userLines = [];
  var v = [];
  try { v = JSON.parse(localStorage.getItem(hlineKey(c.tr)) || "[]"); } catch (e) { v = []; }
  v.forEach(function (p) { if (isFinite(p)) addUserLine(c, p); });
}
function clearUserLines(c) {
  c.userLines.forEach(function (u) { c.s.candles.removePriceLine(u.pl); });
  c.userLines = [];
  saveUserLines(c);
}
function setDrawMode(c, on) {
  c.drawing = on;
  c.host.style.cursor = on ? "crosshair" : "";
  if (c.drawBtn) c.drawBtn.setAttribute("aria-pressed", String(on));
}
function onChartClick(c, p) {
  if (!c.drawing || !p || !p.point) return;
  if (p.point.y >= c.chart.paneSize(0).height) return;           // price pane only, not MACD / volume
  var hit = null;
  c.userLines.forEach(function (u) {
    var y = c.s.candles.priceToCoordinate(u.price);
    if (y != null && Math.abs(y - p.point.y) <= 6) hit = u;
  });
  if (hit) {
    c.s.candles.removePriceLine(hit.pl);
    c.userLines.splice(c.userLines.indexOf(hit), 1);
  } else {
    var price = c.s.candles.coordinateToPrice(p.point.y);
    if (price == null) return;
    addUserLine(c, Math.round(price * 100) / 100);
  }
  saveUserLines(c);
}

/* ---------- fixed range volume profile (FRVP), TradingView style ----------
   Same workflow as TradingView's drawing tool:
     1. click "▥ Volume profile", click a candle (first anchor), move the mouse - the profile follows the cursor -
        click again (second anchor). Esc cancels.
     2. click a profile to select it; drag either anchor (the circles / edge lines) to change the range, or drag its
        histogram to slide the whole range. Delete / Backspace removes the selected one. Draw as many as you like.
     3. the settings row edits the selected profile: rows layout (number of rows | ticks per row), value area %,
        volume (up / down | total), placement (left | right), width (% of the range), POC and VAH / VAL lines.
   What it draws (all from the raw 1-minute bars, whichever candle size is on screen):
     histogram - volume traded at each price row; a bar's volume is spread over the rows its high-low range overlaps in
                 proportion to the overlap (TradingView does the same with intrabar data). Up volume = bar closed above its
                 open, down = below; drawn from the anchor edge, up first, then down. Rows inside the value area are darker.
     POC       - point of control: the row with the most volume (a solid line, extended to the right edge)
     VAH / VAL - value area high / low: grown from the POC one row at a time, always taking the heavier neighbour,
                 until it holds `va`% (default 70%) of the volume (dashed lines, extended)
   LOOK-AHEAD: a profile whose range reaches the buy minute includes bars the entry could not have seen. The info line
   says so. The "Open → entry" quick range stops before the fill minute: that is the as-of one to judge an entry with. */
var VP_RGB = { up: "41,98,255", dn: "251,192,45" };            /* TradingView's blue / yellow */
var VP_COL = { poc: "#ff5252", va: "#5b8def" };
var VP_DEF = { layout: "rows", rows: 24, ticks: 4, va: 70, vol: "ud", side: "left", width: 70, poc: true, lines: true };
var VP_KEYS = ["from", "to", "layout", "rows", "ticks", "va", "vol", "side", "width", "poc", "lines"];

/* bars: [{o,h,l,c,v}] -> rows, POC and value area. `step` > 0 = rows of that price height (ticks layout), else `rows` equal rows. */
function volumeProfile(bars, rows, vaPct, step) {
  if (!bars.length) return null;
  var lo = Infinity, hi = -Infinity, i;
  bars.forEach(function (b) { lo = Math.min(lo, b.l); hi = Math.max(hi, b.h); });
  var n;
  if (step > 0) {
    lo = Math.floor(lo / step + 1e-9) * step;
    n = Math.max(1, Math.ceil((hi - lo) / step - 1e-9));
    if (n > 400) { step = (hi - lo) / 400; n = 400; }                  // keep absurd tick sizes drawable
    hi = lo + n * step;
  } else {
    if (!(hi > lo)) { var e = Math.max(hi * 0.0005, 0.005); lo -= e; hi += e; }
    n = Math.max(1, Math.round(rows)); step = (hi - lo) / n;
  }
  var R = [];
  for (i = 0; i < n; i++) R.push({ p0: lo + i * step, p1: lo + (i + 1) * step, up: 0, dn: 0, tot: 0 });
  var clampRow = function (p) { return Math.min(n - 1, Math.max(0, Math.floor((p - lo) / step + 1e-9))); };
  bars.forEach(function (b) {
    var up = b.c > b.o ? 1 : b.c < b.o ? 0 : 0.5, a = clampRow(b.l), z = clampRow(b.h), span = b.h - b.l;
    for (var k = a; k <= z; k++) {
      var share = span > 0 ? (Math.min(b.h, R[k].p1) - Math.max(b.l, R[k].p0)) / span : 1 / (z - a + 1);
      if (!(share > 0)) continue;
      var v = b.v * share;
      R[k].up += v * up; R[k].dn += v * (1 - up); R[k].tot += v;
    }
  });
  var total = 0, poc = 0, mid = (n - 1) / 2;
  R.forEach(function (r, k) {
    total += r.tot;
    if (r.tot > R[poc].tot || (r.tot === R[poc].tot && Math.abs(k - mid) < Math.abs(poc - mid))) poc = k;
  });
  var a = poc, z = poc, acc = R[poc].tot, want = total * vaPct / 100;
  while (acc < want && (a > 0 || z < n - 1)) {
    var below = a > 0 ? R[a - 1].tot : -1, above = z < n - 1 ? R[z + 1].tot : -1;
    if (above >= below) { z++; acc += R[z].tot; } else { a--; acc += R[a].tot; }
  }
  return { rows: R, lo: lo, hi: hi, step: step, total: total, poc: poc, max: R[poc].tot,
           pocPrice: (R[poc].p0 + R[poc].p1) / 2, vah: R[z].p1, val: R[a].p0, vaLo: a, vaHi: z };
}

/* ---- state ---- */
function vpKey(c) { return "bt32.frvp2." + c.tr.symbol + "." + c.tr.date; }
function vpDefaults() {
  var d = Object.assign({}, VP_DEF);
  try { Object.assign(d, JSON.parse(localStorage.getItem("bt32.frvp2.defaults") || "{}")); } catch (e) { /* storage blocked */ }
  return d;
}
function vpSave(c) {
  try {
    var list = c.vps.map(function (p) { var o = {}; VP_KEYS.forEach(function (k) { o[k] = p[k]; }); return o; });
    if (list.length) localStorage.setItem(vpKey(c), JSON.stringify(list)); else localStorage.removeItem(vpKey(c));
    if (c.vpSel) {
      var d = {}; VP_KEYS.slice(2).forEach(function (k) { d[k] = c.vpSel[k]; });
      localStorage.setItem("bt32.frvp2.defaults", JSON.stringify(d));
    }
  } catch (e) { /* storage blocked: profiles last for this view only */ }
}
function vpEndOf(c, b) { return endKey(b, c.tf); }     // last minute covered by candle b
function vpModel(c, p) {
  var from = fullKey(c.tr.date, p.from), to = fullKey(c.tr.date, p.to);
  var bars = (c.raw || rawBars(c.tr)).filter(function (b) { return b.k >= from && b.k <= to; });
  p.model = volumeProfile(bars, p.rows, p.va, p.layout === "ticks" ? p.ticks * (US ? 0.01 : 0.05) : 0);
  return p.model;
}
function vpNew(c, from, to) {
  var p = Object.assign(vpDefaults(), { from: from, to: to, id: Date.now() + Math.random() });
  vpModel(c, p);
  return p;
}
/* quick ranges, in 1-minute clock times */
function vpPreset(c, name) {
  var raw = c.tr.bars || [], tr = c.tr;
  if (!raw.length) return null;
  var first = raw[0][0], last = raw[raw.length - 1][0];
  if (name === "session") return { from: first, to: last };
  if (name === "trade") return { from: tr.entry_time, to: tr.exit_time };
  var before = raw.filter(function (r) { return r[0] < tr.entry_time; });
  return before.length ? { from: first, to: before[before.length - 1][0] } : null;
}

/* ---- geometry shared by the renderer and the mouse ---- */
function vpGeom(c, p) {
  var m = p.model;
  if (!m || !c.data || !c.chart || fullKey(c.tr.date, p.to) < c.data[0].k) return null;    // range lies in sessions not loaded
  var ts = c.chart.timeScale(), i0 = barIndex(c.data, p.from), i1 = barIndex(c.data, p.to);
  var c0 = ts.logicalToCoordinate(i0), c1 = ts.logicalToCoordinate(i1), cn = ts.logicalToCoordinate(i0 + 1);
  var yt = c.s.candles.priceToCoordinate(m.hi), yb = c.s.candles.priceToCoordinate(m.lo);
  if (c0 == null || c1 == null || cn == null) return null;
  var half = Math.abs(cn - c0) / 2 || 2, x0 = c0 - half, x1 = c1 + half;
  if (yt == null) yt = 0;
  if (yb == null) yb = c.chart.paneSize(0).height;
  return { x0: x0, x1: x1, yt: yt, yb: yb, ym: Math.min(Math.max((yt + yb) / 2, 14), c.chart.paneSize(0).height - 14),
           wmax: (x1 - x0) * p.width / 100, left: p.side === "left", i0: i0, i1: i1 };
}
function vpAll(c) { return c.vpDraft ? c.vps.concat([c.vpDraft]) : c.vps; }

/* ---- drawing: histogram under the candles, lines + anchors over them ---- */
function VpPrimitive(c) {
  var self = this;
  var histogram = { draw: function (target) {
    target.useMediaCoordinateSpace(function (sc) {
      var ctx = sc.context;
      vpAll(c).forEach(function (p) {
        var g = vpGeom(c, p), m = p.model;
        if (!g) return;
        var dim = p === c.vpDraft ? 0.7 : 1;
        m.rows.forEach(function (r, k) {
          var yt = c.s.candles.priceToCoordinate(r.p1), yb = c.s.candles.priceToCoordinate(r.p0);
          if (yt == null || yb == null || r.tot <= 0) return;
          var inVa = k >= m.vaLo && k <= m.vaHi, h = Math.max(1, yb - yt - 1);
          var segs = p.vol === "ud" ? [[r.up, VP_RGB.up], [r.dn, VP_RGB.dn]] : [[r.tot, VP_RGB.up]], off = 0;
          segs.forEach(function (sg) {
            var w = g.wmax * sg[0] / m.max;
            if (w <= 0) return;
            ctx.fillStyle = "rgba(" + sg[1] + "," + (dim * (inVa ? 0.62 : 0.3)) + ")";
            ctx.fillRect(g.left ? g.x0 + off : g.x1 - off - w, yt + 0.5, w, h);
            off += w;
          });
        });
      });
    });
  } };
  var overlay = { draw: function (target) {
    target.useMediaCoordinateSpace(function (sc) {
      var ctx = sc.context, W = sc.mediaSize.width, H = sc.mediaSize.height;
      vpAll(c).forEach(function (p) {
        var g = vpGeom(c, p), m = p.model;
        if (!g) return;
        var sel = p === c.vpSel || p === c.vpDraft;
        ctx.save();
        // range edges (solid + handles when selected, faint otherwise)
        ctx.lineWidth = 1; ctx.setLineDash(sel ? [] : [3, 4]);
        ctx.strokeStyle = sel ? "rgba(91,141,239,0.9)" : "rgba(148,163,184,0.35)";
        [g.x0, g.x1].forEach(function (x) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); });
        var line = function (price, col, dash, label) {
          var y = c.s.candles.priceToCoordinate(price);
          if (y == null) return;
          ctx.setLineDash(dash); ctx.strokeStyle = col; ctx.lineWidth = 1.5;
          ctx.beginPath(); ctx.moveTo(g.x0, y); ctx.lineTo(W, y); ctx.stroke();
          if (sel) return;                     // the selected profile's prices are tagged on the price axis instead
          ctx.setLineDash([]); ctx.fillStyle = col; ctx.font = "10px sans-serif"; ctx.textAlign = "right";
          ctx.fillText(label + " " + price.toFixed(2), W - 4, y - 3);
        };
        if (p.poc) line(m.pocPrice, VP_COL.poc, [], "POC");
        if (p.lines) { line(m.vah, VP_COL.va, [5, 4], "VAH"); line(m.val, VP_COL.va, [5, 4], "VAL"); }
        if (sel) {
          ctx.setLineDash([]);
          [g.x0, g.x1].forEach(function (x) {
            ctx.beginPath(); ctx.arc(x, g.ym, 6, 0, 6.2832);
            ctx.fillStyle = "#101922"; ctx.fill();
            ctx.lineWidth = 2; ctx.strokeStyle = "#5b8def"; ctx.stroke();
          });
        }
        ctx.restore();
      });
    });
  } };
  var viewBottom = { zOrder: function () { return "bottom"; }, renderer: function () { return histogram; } };
  var viewTop = { zOrder: function () { return "top"; }, renderer: function () { return overlay; } };
  this.attached = function (p) { self.requestUpdate = p.requestUpdate; };
  this.detached = function () { self.requestUpdate = null; };
  this.updateAllViews = function () {};
  this.paneViews = function () { return [viewBottom, viewTop]; };
}
function vpRedraw(c) { if (c.vpPrim && c.vpPrim.requestUpdate) c.vpPrim.requestUpdate(); }

/* ---- refresh: models, axis tags, info line, settings controls ---- */
function vpRefresh(c) {
  if (!c.chart || !c.vps) return;
  c.vps.forEach(function (p) { vpModel(c, p); });
  c.vpTags.forEach(function (pl) { c.s.candles.removePriceLine(pl); });
  c.vpTags = [];
  var p = c.vpSel, m = p && p.model;
  if (m) {
    [["POC", m.pocPrice, VP_COL.poc, p.poc], ["VAH", m.vah, VP_COL.va, p.lines], ["VAL", m.val, VP_COL.va, p.lines]].forEach(function (x) {
      if (x[3]) c.vpTags.push(c.s.candles.createPriceLine({ price: x[1], color: x[2], lineVisible: false, axisLabelVisible: true, title: x[0] }));
    });
  }
  vpPanelSync(c);
  vpRedraw(c);
}
function vpInfo(c) {
  var p = c.vpSel, tr = c.tr;
  if (c.vpTool) return c.vpTool === 1 ? "Click a candle to anchor the profile's start." : "Move the mouse to size the profile, click to place it. Esc cancels.";
  if (!p) return c.vps.length ? "Click a profile to select it. Drag its anchors to change the range." : "Click “▥ Volume profile”, then click two candles on the chart.";
  var m = p.model;
  if (!m) return "No bars in this range.";
  var d = function (x) { return pct(100 * (x - tr.entry) / tr.entry) + " vs buy"; };
  var where = tr.entry > m.vah ? "buy is ABOVE the value area" : tr.entry < m.val ? "buy is BELOW the value area" : "buy is INSIDE the value area";
  var look = fullKey(tr.date, p.to) >= tr.date + " " + tr.entry_time ? "<span class='warn'>⚠ includes bars at or after the buy minute (look-ahead for this entry)</span>" : "<span>as-of: only bars before the buy</span>";
  return "<span><i style='background:" + VP_COL.poc + "'></i>POC <b>" + m.pocPrice.toFixed(2) + "</b> " + d(m.pocPrice) + "</span>"
    + "<span><i style='background:" + VP_COL.va + "'></i>VAH <b>" + m.vah.toFixed(2) + "</b> " + d(m.vah) + "</span>"
    + "<span><i style='background:" + VP_COL.va + "'></i>VAL <b>" + m.val.toFixed(2) + "</b> " + d(m.val) + "</span>"
    + "<span>" + vpWhen(c, p.from) + "–" + vpWhen(c, p.to) + " · <b>" + fmtVol(m.total) + "</b> shares · " + m.rows.length + " rows × " + m.step.toFixed(3) + "</span>"
    + "<span><b>" + where + "</b></span>" + look;
}
function vpWhen(c, x) {           // "12:35" on the trade day, "29 Jan 12:35" on an earlier one
  var k = fullKey(c.tr.date, x);
  return k.slice(0, 10) === c.tr.date ? k.slice(11) : dayLabel(k) + " " + k.slice(11);
}
function vpPanelSync(c) {
  if (!c.vpPanel) return;
  var p = c.vpSel, f = c.vpF;
  c.vpPanel.style.display = c.vpSel || c.vpTool ? "" : "none";       // only while drawing or editing a profile
  c.vpPanel.classList.toggle("idle", !p);                             // drawing: just the hint, no greyed-out settings
  c.vpBtn.setAttribute("aria-pressed", String(!!c.vpTool));
  Object.keys(f).forEach(function (k) {
    if (k === "preset" || k === "del") return;
    f[k].disabled = !p;
    if (p) { if (f[k].type === "checkbox") f[k].checked = !!p[k]; else f[k].value = String(p[k]); }
  });
  f.del.disabled = !p;
  f.rows.parentNode.style.display = !p || p.layout === "rows" ? "" : "none";
  f.ticks.parentNode.style.display = p && p.layout === "ticks" ? "" : "none";
  c.vpInfoEl.innerHTML = vpInfo(c);
}

/* ---- tool: place, select, drag, delete ---- */
function vpSelect(c, p) {
  if (c.vpSel === p) return;
  c.vpSel = p;
  vpRefresh(c);
}
function vpStartTool(c) {
  if (c.vpTool) { vpCancelTool(c); return; }
  c.vpTool = 1; c.vpDraft = null; c.vpSel = null;
  setDrawMode(c, false);
  c.host.style.cursor = "crosshair";
  vpRefresh(c);
}
function vpCancelTool(c) {
  c.vpTool = 0; c.vpDraft = null;
  c.host.style.cursor = "";
  vpRefresh(c);
}
function vpDelete(c) {
  if (!c.vpSel) return;
  c.vps.splice(c.vps.indexOf(c.vpSel), 1);
  c.vpSel = null;
  vpSave(c); vpRefresh(c);
}
function vpBarAt(c, x) {       // index of the candle under pixel x, clamped to the session
  var l = c.chart.timeScale().coordinateToLogical(x);
  return l == null ? null : Math.min(c.data.length - 1, Math.max(0, Math.round(l)));
}
function vpPos(c, ev) {
  var r = c.host.getBoundingClientRect();
  return { x: ev.clientX - r.left, y: ev.clientY - r.top };
}
/* what is under the pointer: an anchor / edge of the selected profile, or the body of any profile's histogram */
function vpHit(c, x, y) {
  if (!c.data || y > c.chart.paneSize(0).height) return null;
  var p = c.vpSel, g = p && vpGeom(c, p);
  if (g) {
    if (Math.hypot(x - g.x0, y - g.ym) <= 9 || Math.abs(x - g.x0) <= 4) return { p: p, mode: "from" };
    if (Math.hypot(x - g.x1, y - g.ym) <= 9 || Math.abs(x - g.x1) <= 4) return { p: p, mode: "to" };
  }
  for (var k = c.vps.length - 1; k >= 0; k--) {
    var q = c.vps[k], h = vpGeom(c, q);
    if (!h || y < h.yt || y > h.yb) continue;
    var a = h.left ? h.x0 : h.x1 - h.wmax, b = h.left ? h.x0 + h.wmax : h.x1;
    if (x >= a && x <= b) return { p: q, mode: "move" };
  }
  return null;
}
function vpDown(c, ev) {
  if (!c.chart || !c.data || ev.button !== 0) return;
  var pos = vpPos(c, ev);
  if (pos.y > c.chart.paneSize(0).height) return;
  if (c.vpTool) {                                  // placing: first click anchors, second places
    ev.preventDefault(); ev.stopPropagation();
    var i = vpBarAt(c, pos.x);
    if (i == null) return;
    if (c.vpTool === 1) {
      c.vpA = i; c.vpTool = 2;
      c.vpDraft = vpNew(c, c.data[i].k, vpEndOf(c, c.data[i]));
    } else {
      var lo = Math.min(c.vpA, i), hi = Math.max(c.vpA, i);
      var p = vpNew(c, c.data[lo].k, vpEndOf(c, c.data[hi]));
      c.vps.push(p);
      c.vpTool = 0; c.vpDraft = null; c.vpSel = p;
      c.host.style.cursor = "";
      vpSave(c);
    }
    vpRefresh(c);
    return;
  }
  var hit = vpHit(c, pos.x, pos.y);
  if (!hit) { if (c.vpSel) { c.vpSel = null; vpRefresh(c); } return; }   // let the chart pan as usual
  ev.preventDefault(); ev.stopPropagation();
  if (c.vpSel !== hit.p) { c.vpSel = hit.p; vpRefresh(c); }
  var g = vpGeom(c, hit.p), start = vpBarAt(c, pos.x);
  var drag = { p: hit.p, mode: hit.mode, i0: g.i0, i1: g.i1, start: start };
  c.chart.applyOptions({ handleScroll: false, handleScale: false });
  c.host.style.cursor = hit.mode === "move" ? "grabbing" : "ew-resize";
  var move = function (e) {
    var i = vpBarAt(c, vpPos(c, e).x);
    if (i == null) return;
    var a = drag.i0, b = drag.i1;
    if (drag.mode === "move") {
      var d = Math.max(-drag.i0, Math.min(c.data.length - 1 - drag.i1, i - drag.start)); a += d; b += d;
    } else {
      var fixed = drag.mode === "from" ? drag.i1 : drag.i0;
      a = Math.min(i, fixed); b = Math.max(i, fixed);
    }
    drag.p.from = c.data[a].k; drag.p.to = vpEndOf(c, c.data[b]);
    vpModel(c, drag.p);
    vpPanelSync(c); vpRedraw(c);
  };
  var up = function () {
    window.removeEventListener("pointermove", move, true);
    window.removeEventListener("pointerup", up, true);
    c.chart.applyOptions({ handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
                           handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: { time: true, price: true },
                                          axisDoubleClickReset: { time: true, price: true } } });
    c.host.style.cursor = "";
    vpSave(c); vpRefresh(c);
  };
  window.addEventListener("pointermove", move, true);
  window.addEventListener("pointerup", up, true);
}
function vpHover(c, ev) {
  if (!c.chart || !c.data) return;
  var pos = vpPos(c, ev);
  if (c.vpTool === 2 && c.vpDraft) {                 // rubber band: the profile follows the cursor
    var i = vpBarAt(c, pos.x);
    if (i == null) return;
    var lo = Math.min(c.vpA, i), hi = Math.max(c.vpA, i);
    c.vpDraft.from = c.data[lo].k; c.vpDraft.to = vpEndOf(c, c.data[hi]);
    vpModel(c, c.vpDraft);
    vpRedraw(c);
    return;
  }
  if (c.vpTool || (ev.buttons & 1)) return;
  var hit = vpHit(c, pos.x, pos.y);
  c.host.style.cursor = hit ? (hit.mode === "move" ? "grab" : "ew-resize") : (c.drawing ? "crosshair" : "");
}
function vpInit(c) {
  c.vps = []; c.vpSel = null; c.vpTool = 0; c.vpDraft = null; c.vpTags = [];
  c.vpPrim = new VpPrimitive(c);
  c.s.candles.attachPrimitive(c.vpPrim);
  try {
    JSON.parse(localStorage.getItem(vpKey(c)) || "[]").forEach(function (o) {
      var p = Object.assign(vpDefaults(), o, { id: Date.now() + Math.random() });
      if (p.from && p.to) c.vps.push(p);
    });
  } catch (e) { c.vps = []; }
  c.host.addEventListener("pointerdown", function (ev) { vpDown(c, ev); }, true);
  c.host.addEventListener("pointermove", function (ev) { vpHover(c, ev); });
  c.sec.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && c.vpTool) { vpCancelTool(c); ev.stopPropagation(); }
    else if ((ev.key === "Delete" || ev.key === "Backspace") && c.vpSel && !/INPUT|SELECT/.test(ev.target.tagName)) { ev.preventDefault(); vpDelete(c); }
  }, true);
  vpRefresh(c);
}

/* the settings row under the toolbar: edits the selected profile (new ones copy the last settings used) */
function vpBuildPanel(c, parent) {
  var p = h("div", { class: "vp-panel" }, parent), f = {};
  p.style.display = "none";
  h("b", { class: "vp-title" }, p, "Volume profile");
  var field = function (label, el) { var l = h("label", {}, p); l.appendChild(document.createTextNode(label + " ")); l.appendChild(el); return el; };
  var select = function (opts) {
    var s = h("select", {});
    opts.forEach(function (o) { h("option", { value: o[0] }, s, o[1]); });
    return s;
  };
  var num = function (min, max) { return h("input", { type: "number", min: String(min), max: String(max) }); };
  var check = function (label) { return field(label, h("input", { type: "checkbox" })); };
  f.preset = field("Quick range", select([["", "Set range…"], ["entry", "Open → entry (as-of)"], ["session", "Whole session"], ["trade", "Entry → exit"]]));
  f.layout = field("Rows", select([["rows", "Number of rows"], ["ticks", "Ticks per row"]]));
  f.rows = num(6, 150); f.ticks = num(1, 400);
  f.rows.setAttribute("aria-label", "Number of rows"); f.ticks.setAttribute("aria-label", "Ticks per row");
  var rl = h("label", {}, p); rl.appendChild(f.rows);
  var tl = h("label", {}, p); tl.appendChild(f.ticks);
  f.va = field("Value area %", num(30, 99));
  f.vol = field("Volume", select([["ud", "Up / down"], ["tot", "Total"]]));
  f.side = field("Placement", select([["left", "Left"], ["right", "Right"]]));
  f.width = field("Width %", num(10, 100));
  f.poc = check("POC"); f.lines = check("VAH / VAL");
  f.del = h("button", { type: "button", title: "Remove the selected profile (Delete key)" }, p, "Delete");
  c.vpInfoEl = h("div", { class: "vp-info" }, p);
  c.vpF = f;
  var apply = function () {
    var s = c.vpSel;
    if (!s) return;
    s.layout = f.layout.value;
    s.rows = Math.min(150, Math.max(6, Math.round(+f.rows.value) || VP_DEF.rows));
    s.ticks = Math.min(400, Math.max(1, Math.round(+f.ticks.value) || VP_DEF.ticks));
    s.va = Math.min(99, Math.max(30, +f.va.value || VP_DEF.va));
    s.vol = f.vol.value; s.side = f.side.value;
    s.width = Math.min(100, Math.max(10, +f.width.value || VP_DEF.width));
    s.poc = f.poc.checked; s.lines = f.lines.checked;
    vpSave(c); vpRefresh(c);
  };
  ["layout", "rows", "ticks", "va", "vol", "side", "width", "poc", "lines"].forEach(function (k) { f[k].onchange = apply; });
  f.preset.onchange = function () {
    var r = vpPreset(c, f.preset.value);
    f.preset.value = "";
    if (!r) return;
    if (c.vpSel) { c.vpSel.from = r.from; c.vpSel.to = r.to; }
    else { var n = vpNew(c, r.from, r.to); c.vps.push(n); c.vpSel = n; }
    vpSave(c); vpRefresh(c);
  };
  f.del.onclick = function () { vpDelete(c); };
  return p;
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

/* Each chart can be switched between 5-minute and 1-minute candles on its own (c.tfSel); with no choice it
   follows the page-level "Candles" filter. Every per-chart read below uses c.tf, the timeframe it was drawn in. */
function effTf(c) { return c.tfSel || tf; }
function paintTf(c) {
  if (!c.tfBtns) return;
  var cur = effTf(c);
  Object.keys(c.tfBtns).forEach(function (k) { c.tfBtns[k].setAttribute("aria-pressed", String(k === cur)); });
}
/* clock times of the first and last visible candle, so a timeframe switch can keep the same window */
function viewTimes(c) {
  var r = c.chart && c.data && c.data.length ? range(c) : null;
  if (!r) return null;
  var last = c.data.length - 1;
  var a = c.data[Math.min(last, Math.max(0, Math.round(r.from)))], b = c.data[Math.min(last, Math.max(0, Math.round(r.to)))];
  return { from: a.k, to: endKey(b, c.tf) };                                          // to = end of the last candle's span
}
function showTimes(c, t) {
  var from = barIndex(c.data, t.from), to = Math.max(from, barIndex(c.data, t.to));
  var apply = function () {
    c.s.candles.priceScale().setAutoScale(true);
    c.chart.timeScale().setVisibleLogicalRange({ from: from - 0.5, to: to + 0.5 });
  };
  apply();
  requestAnimationFrame(function () { requestAnimationFrame(apply); });
}

/* load the chart's timeframe's bars into an existing chart (keepView: keep the visible clock window) */
function setSeries(c, keepView) {
  var tr = c.tr, s = c.s, ctf = effTf(c);
  var kept = keepView ? viewTimes(c) : null;
  c.raw = allRaw(c);
  var data = points(aggregate(c.raw, TF_MIN[ctf] || 1));
  data.day = tr.date;                                   // barIndex reads a bare "HH:MM" as this date
  var T = data.map(function (b) { return utcSec(b.d, b.t); });
  var days = {};
  c.raw.forEach(function (b) { days[b.d] = 1; });
  c.sessions = Object.keys(days).length;
  c.drawn = drawKey(c);
  var day0 = firstOfDay(data, tr.date);                 // a before-the-open filing sits on the trade day's first candle
  var ln = function (key) {
    return data.map(function (b, i) { return b[key] == null ? { time: T[i] } : { time: T[i], value: b[key] }; });
  };
  var eIdx = barIndex(data, tr.entry_time), xIdx = barIndex(data, tr.exit_time);
  // stored BEFORE setData: the chart's range-change callbacks read them
  c.data = data;
  c.tf = ctf;
  c.eIdx = eIdx;
  c.byTime = {};
  T.forEach(function (t, i) { c.byTime[t] = i; });
  s.hold.setData(data.map(function (b, i) {
    return i >= eIdx && i <= xIdx ? { time: T[i], value: 1 } : { time: T[i] };
  }));
  var nbar = {};
  (tr.filings || []).forEach(function (n) {
    var i = n.pre ? day0 : barIndex(data, n.t);
    nbar[i] = nbar[i] === "material" || n.k === "material" ? "material" : n.k;
  });
  s.news.setData(data.map(function (b, i) {
    return nbar[i] ? { time: T[i], value: 1, color: alpha(nbar[i] === "offering" ? COL.dn : "#f59e0b", nbar[i] === "material" ? 0.2 : 0.09) } : { time: T[i] };
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
  /* What-if exits: one orange arrow per distinct alternative exit (same bar+price merged) */
  var wg = {};
  (c.wiMarks || []).forEach(function (w) {
    if (w.exit_time === tr.exit_time && Math.abs(w.exit - tr.exit) < 0.005) return;   // same as the saved SELL
    var k = w.exit_time + "|" + w.exit.toFixed(2);
    (wg[k] = wg[k] || { w: w, names: [] }).names.push(w.label);
  });
  Object.keys(wg).forEach(function (k) {
    var g = wg[k], i = barIndex(data, g.w.exit_time);
    mk.push({ time: T[i], position: "aboveBar", shape: "arrowDown", color: "#fb923c", size: 1.5,
              text: "WHAT-IF " + g.w.exit.toFixed(2) + " @ " + g.w.exit_time + " · " + g.names.join(", ") });
  });
  var notes = c.notes = {};
  var note = function (i, html) { (notes[i] = notes[i] || []).push(html); };
  (tr.patterns || []).forEach(function (m) {
    if (m.timeframe !== ctf) return;
    var i = barIndexExact(data, m.end);
    if (i < 0) return;
    // A correctly-named formation on a candle much smaller than this stock's
    // recent average is drawn faint (candles.STRENGTH_WEAK_BELOW).
    var weak = m.strength < WEAK;
    // Indecision candles (doji, spinning tops) are neutral by themselves; what they mean depends on the
    // five candles before them. Say which: green = after a down-move, rose = after an up-move.
    var ctx = m.direction === "neutral" ? CONTEXT[m.prior_trend] : null;
    mk.push({ time: T[i], position: m.direction === "bearish" ? "aboveBar" : "belowBar",
              shape: "circle", size: 0.6, color: alpha(ctx ? ctx.col : COL.pattern, weak ? 0.4 : 0.95),
              full: nice(m.name) + (ctx ? " " + ctx.text : "") + " " + m.strength.toFixed(2) + "×" });
    note(i, "<span><i style='background:" + (ctx ? ctx.col : COL.pattern) + ";border-radius:50%'></i>" + nice(m.name)
            + (ctx ? " " + ctx.text : "") + " " + m.strength.toFixed(2) + "×</span>");
  });
  /* NSE filings: one square per bar (several filings in a bar are counted); before-the-open ones sit on the first bar */
  var nb = {};
  (tr.filings || []).forEach(function (n) {
    var i = n.pre ? day0 : barIndex(data, n.t);
    (nb[i] = nb[i] || []).push(n);
  });
  Object.keys(nb).forEach(function (i) {
    var g = nb[i], mat = g.some(function (n) { return n.k === "material"; }), n0 = g[0];
    var dil = !mat && g.some(function (n) { return n.k === "offering"; });    // share offering (US): news, but the wrong sign for a long
    var head = (n0.pre ? n0.when + " · " : "") + n0.h;
    mk.push({ time: T[+i], position: "aboveBar", shape: "square", size: c.tf === "1m" ? 2 : 1, color: dil ? COL.dn : mat ? "#f59e0b" : alpha("#f59e0b", 0.45),
              full: "⚡ " + (n0.pre ? "pre-open" : head.length > 42 ? head.slice(0, 41) + "…" : head) + (g.length > 1 ? " +" + (g.length - 1) : "") });
    g.forEach(function (n) { note(+i, "<span class='nt'>⚡ " + (n.pre ? n.when : n.t) + " · " + escHtml(n.h) + "</span>"); });
  });
  /* The market-reaction decision: the bar whose close the Lab's gate read as "the market has reacted to the news" */
  var nr = tr.news_reaction;
  if (nr) {
    mk.push({ time: T[barIndex(data, nr.bar)], position: "belowBar", shape: "arrowUp", size: 2, color: "#38bdf8",
              text: "REACTED " + (nr.move_pct >= 0 ? "+" : "") + nr.move_pct.toFixed(2) + "%" + (nr.rvol != null ? " · RVOL " + nr.rvol.toFixed(1) + "\u00D7" : "") +
                    " @ " + nr.decided_at });
  }
  mk.sort(function (a, b) { return a.time - b.time; });
  c.mk = mk;
  paintMarkers(c);
  showNewsLine(c);

  if (kept) showTimes(c, kept); else resetView(c);
  paintTf(c);
  showLegend(c, null);
  vpRefresh(c);
}

/* markers with a `full` label (formations, filings) carry their text only while "Labels" is on */
function paintMarkers(c) {
  c.markers.setMarkers(c.mk.map(function (m) {
    if (m.full == null) return m;
    var o = Object.assign({}, m, { text: showLabels ? m.full : "" });
    delete o.full;
    return o;
  }));
}

/* every filing drawn on the chart, behind a "⚡ N filings" dropdown in the card header (the chart pins
   before-the-open ones to its first candle, where a label would be clipped) */
function escHtml(x) { return String(x).replace(/[&<>"']/g, function (ch) { return "&#" + ch.charCodeAt(0) + ";"; }); }
function showNewsLine(c) {
  var f = c.tr.filings || [];
  if (!c.newsLine) {
    c.newsBox = h("details", { class: "filings" }, c.metaRow);
    c.newsSum = h("summary", {}, c.newsBox);
    c.newsLine = h("div", { class: "newsline" }, c.newsBox);
  }
  c.newsBox.style.display = f.length ? "" : "none";
  var mat = f.filter(function (n) { return n.k === "material"; }).length;
  c.newsSum.textContent = "\u26A1 " + f.length + " filing" + (f.length === 1 ? "" : "s") + (mat ? " · " + mat + " material" : "");
  c.newsLine.textContent = "";
  f.forEach(function (n) {
    var li = h("span", { class: "nl " + n.k, title: n.k }, c.newsLine);
    li.textContent = "\u26A1 " + (n.pre ? n.when : n.t) + " \u00B7 " + n.h;
  });
}

/* ---------- view control ---------- */
/* The default window: the whole trade session (or everything loaded, on 1h / daily candles). With History on and
   1m-15m candles it is the trade session and the two before it; zoom out or pan left for the rest. */
function resetView(c) {
  var n = c.data.length, from = 0, visible = n;
  if (c.sessions > 1 && TF_MIN[c.tf] < 60) {
    var dates = [];
    c.data.forEach(function (b) { if (dates[dates.length - 1] !== b.d) dates.push(b.d); });
    var start = dates[Math.max(0, dates.indexOf(c.tr.date) - 2)];
    from = firstOfDay(c.data, start);
    visible = n - from;
  }
  visible = Math.min(n, Math.max(MIN_BARS, visible));
  from = Math.min(from, Math.max(0, n - visible));
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
  var span = c.sessions > 1 ? c.sessions + " sessions" : "Full session";
  c.zlabel.textContent = shown >= c.data.length ? span + " · " + c.data.length + " " + c.tf + " candles"
    : shown + " of " + c.data.length + " " + c.tf + " candles" + (c.sessions > 1 ? " · " + c.sessions + " sessions" : "");
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
    "<div class='l1'><b>" + tr.symbol + "</b> · " + c.tf + " · " + (b.d !== tr.date ? dayLabel(b.d) + " " : "") + (c.tf === "D" ? (b.d === tr.date ? dayLabel(b.d) : "") : b.t)
    + (i == null ? " <em>(entry bar)</em>" : "")
    + " &nbsp; O <b>" + fx(b.o) + "</b> H <b>" + fx(b.h) + "</b> L <b>" + fx(b.l) + "</b> C <b class='"
    + cls(chg) + "'>" + fx(b.c) + "</b> <span class='" + cls(chg) + "'>" + (chg >= 0 ? "+" : "") + chg.toFixed(2)
    + "</span></div><div class='l2'>"
    + sw(COL.ema9, "EMA9", fx(b.ema9)) + sw(COL.ema20, "EMA20", fx(b.ema20))
    + sw(COL.ema200, "EMA200", fx(b.ema200)) + sw(COL.vwap, "VWAP", fx(b.vwap)) + "</div>"
    + (c.notes && c.notes[i == null ? c.eIdx : i] ? "<div class='l3'>" + c.notes[i == null ? c.eIdx : i].join("") + "</div>" : "");
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
/* the card's dropdowns (Pan, filings) close on a click anywhere else, like a menu */
document.addEventListener("pointerdown", function (ev) {
  document.querySelectorAll(".card details.advanced-chart[open], .card details.filings[open]").forEach(function (d) {
    if (!d.contains(ev.target)) d.open = false;
  });
});
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
/* what the chart should show: candle size + sessions of history */
function drawKey(c) { return effTf(c) + "|" + (c.histN || 0); }
function ensureChart(c) {
  if (DATA.lazy && !c.tr.bars) { loadDetail(c); return; }
  if (!c.chart) createChart(c);
  if (c.histN && (!c.hist || c.hist.n < c.histN)) { loadHistory(c); return; }
  if (c.drawn !== drawKey(c)) {
    var sameDays = c.drawn != null && c.drawn.split("|")[1] === String(c.histN || 0);
    setSeries(c, sameDays);                             // a new candle size keeps the window; new history resets it
  }
}
/* fetch the sessions before the trade (once per size: a smaller History setting filters what is loaded) */
function loadHistory(c) {
  if (c.histLoading) return;
  if (!DATA.detail_url) { c.histN = 0; ensureChart(c); return; }
  var want = c.histN;
  c.histLoading = true;
  c.zlabel.textContent = "Loading " + want + " earlier sessions…";
  fetch(DATA.detail_url + c.i + "/history?days=" + want).then(function (r) {
    return r.ok ? r.json() : r.json().then(function (j) { throw new Error(j.error || r.statusText); });
  }).then(function (d) {
    c.histLoading = false;
    c.hist = { n: want, sessions: d.sessions, bars: d.bars.map(function (r) {
      return { d: r[0], t: r[1], k: r[0] + " " + r[1], o: r[2], h: r[3], l: r[4], c: r[5], v: r[6] }; }) };
    ensureChart(c);
  }).catch(function (e) {
    c.histLoading = false; c.histN = 0;
    if (c.histSel) c.histSel.value = "0";
    ensureChart(c);
    c.zlabel.textContent = "History unavailable: " + e.message;
  });
}
/* key-level pills and the level table need the detail, so they fill in once it is there */
function hydrate(c) {
  if (c.hydrated || !c.tr.levels) return;
  c.hydrated = true;
  keyStrip(c.keysHost, c.tr);
  levelTable(c.lvHost, c.tr);
}

/* ---------- plain-language trade summary ---------- */
/* the price move in units of the risk taken: (exit − entry) / (entry − stop) */
function rMultiple(tr) {
  var risk = tr.entry - tr.stop;
  return risk > 0 ? (tr.exit - tr.entry) / risk : null;
}
var EXIT_PHRASE = { stop: "stopped out", target: "hit the target", eod: "closed at the end of the session",
  trail_stop: "trailing stop hit", breakeven_stop: "breakeven stop hit", checkpoint_stop: "checkpoint stop hit",
  vwap_break: "closed under VWAP", macd_fade: "MACD faded", ema9_break: "closed under EMA9", ema20_break: "closed under EMA20",
  volume_climax: "volume climax", resistance_reject: "rejected at resistance", stale: "went nowhere (stale exit)" };
function px(x) { return (US ? "$" : "") + x.toFixed(2); }
function tradeStory(tr) {
  var held = toMin(tr.exit_time) - toMin(tr.entry_time), risk = tr.qty * (tr.entry - tr.stop);
  var stopPct = 100 * (tr.entry - tr.stop) / tr.entry;
  return "Bought <b>" + tr.qty + "</b> at <b>" + px(tr.entry) + "</b> at " + tr.entry_time
    + ", sold at <b>" + px(tr.exit) + "</b> at " + tr.exit_time + " — " + escHtml(EXIT_PHRASE[tr.exit_reason] || nice(tr.exit_reason)) + "."
    + (held >= 0 ? " Held " + (held < 60 ? held + " min" : Math.floor(held / 60) + " h " + (held % 60) + " min") + "." : "")
    + (risk > 0 ? " Risked " + inr(risk) + " (stop " + stopPct.toFixed(2) + "% below the entry)." : "")
    + (tr.real_cost_inr != null ? " Costs " + inr(tr.real_cost_inr) + "." : "");
}

/* ---------- trade-to-trade navigation ----------
   navOrder is the cards that pass the filters, in the order they appear (group by group).
   ‹ › on each card and J / K move through it; in full screen the next trade takes the screen. */
var navOrder = [];
function numberTrades(keep) {
  var inKeep = {};
  keep.forEach(function (c) { inKeep[c.i] = true; });
  navOrder = Array.prototype.map.call(document.querySelectorAll("#charts .card"), function (s) { return cards[+s.getAttribute("data-i")]; })
    .filter(function (c) { return c && inKeep[c.i]; });
  navOrder.forEach(function (c, k) { c.navPos = k; c.navLbl.textContent = (k + 1) + " / " + navOrder.length; });
}
function isFull(c) { return document.fullscreenElement === c.sec || c.sec.classList.contains("zoomed"); }
function goTrade(c, dir) {
  var t = navOrder[(c.navPos == null ? -1 : c.navPos) + dir];
  if (!t) return;
  var full = isFull(c);
  reveal(t);
  ensureChart(t);
  if (full) {
    if (document.fullscreenElement === c.sec && t.sec.requestFullscreen) {
      t.sec.requestFullscreen().catch(function () { toggleFull(c); toggleFull(t); });
    } else { c.sec.classList.remove("zoomed"); t.sec.classList.add("zoomed"); }
    t.sec.focus({ preventScroll: true });
    return;
  }
  t.sec.scrollIntoView({ behavior: "smooth", block: "start" });
  t.sec.focus({ preventScroll: true });
  t.sec.classList.add("flash");
  setTimeout(function () { t.sec.classList.remove("flash"); }, 1200);
}
/* the trade the keys act on: the one in full screen, the focused one, else the first whose card is on screen */
function currentCard() {
  var f = cards.filter(isFull)[0]; if (f) return f;
  var a = document.activeElement && document.activeElement.closest && document.activeElement.closest("#charts .card");
  if (a) return cards[+a.getAttribute("data-i")];
  return navOrder.filter(function (c) { var r = c.sec.getBoundingClientRect(); return r.bottom > 80 && r.height; })[0] || null;
}
document.addEventListener("keydown", function (ev) {
  if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
  var tag = (ev.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "select" || tag === "textarea") return;
  var k = ev.key.toLowerCase(), dir = k === "j" || k === "n" ? 1 : k === "k" || k === "p" ? -1 : 0;
  if (!dir) return;
  var c = currentCard();
  if (!c) { if (dir > 0 && navOrder[0]) goTrade({ navPos: -1, sec: document.body }, 1); return; }
  ev.preventDefault();
  goTrade(c, dir);
});

function buildCards() {
  var host = document.getElementById("charts");
  DATA.trades.forEach(function (tr, i) {
    var sec = h("section", { class: "card", tabindex: "0", "data-i": String(i) }, host);
    /* header: row 1 = title + P&L, row 2 = the four key prices, the trade's other facts and its filings */
    var head = h("div", { class: "card-head" }, sec);
    var row1 = h("div", { class: "ch-title" }, head);
    var h2 = h("h2", {}, row1);
    h("span", { class: "sym" }, h2, tr.symbol);
    h("span", { class: "sub" }, h2, tr.date + " · " + nice(tr.setup));
    var nb = newsBadge(tr);
    if (nb) h2.appendChild(nb);
    var kpi = h("div", { class: "kpi" }, row1);
    var rMul = rMultiple(tr), win = tr.net_real_inr > 0;
    kpi.innerHTML = "<span class='outcome " + (win ? "win" : "loss") + "' title='Net result after real costs (brokerage, taxes, fees, slippage)'>"
      + (win ? "WIN " : "LOSS ") + "<b>" + inr(tr.net_real_inr) + "</b></span>"
      + (rMul != null ? "<span class='rmul " + cls(rMul) + "' title='Price move in units of the risk taken (1R = entry − stop)'>" + (rMul >= 0 ? "+" : "−") + Math.abs(rMul).toFixed(1) + "R</span>" : "")
      + "<span class='kmore'>" + pct(tr.gross_pct) + " before costs<span class='kd'>·</span>"
      + inr(tr.net_stress_inr) + (US ? " at engine-booked costs" : " at stress costs") + "</span>";
    var nav = h("div", { class: "tnav" }, row1);
    var prevB = h("button", { type: "button", title: "Previous trade (K)", "aria-label": "Previous trade" }, nav, "‹");
    var navLbl = h("span", { class: "tnav-lbl" }, nav, "");
    var nextB = h("button", { type: "button", title: "Next trade (J)", "aria-label": "Next trade" }, nav, "›");
    h("p", { class: "story" }, head).innerHTML = tradeStory(tr);
    var row2 = h("div", { class: "ch-meta" }, head);
    var keysHost = h("div", { class: "keys-host" }, row2);
    var meta = h("div", { class: "meta" }, row2);
    meta.innerHTML =
      "stop <b>" + tr.stop.toFixed(2) + "</b>"
      + " · " + targetLabel(tr).toLowerCase() + " <b>" + tr.target.toFixed(2) + "</b>"
      + " · qty <b>" + tr.qty + "</b>"
      + " · day <b>" + tr.day_chg_pct.toFixed(1) + "%</b>";

    /* one slim toolbar: view | drawing | overlays | trade tools ............ visible candles */
    var bar = h("div", { class: "toolbar" }, sec);
    var grp = h("div", { class: "btns" }, bar);
    var c = { sec: sec, tr: tr, i: i, chart: null, tf: null, metaRow: row2, navLbl: navLbl };
    prevB.onclick = function () { goTrade(c, -1); };
    nextB.onclick = function () { goTrade(c, 1); };
    function mk(txt, title, fn, parent) {
      var b = h("button", { type: "button", title: title, "aria-label": title }, parent || grp, txt);
      b.onclick = function () { ensureChart(c); if (c.chart) fn(); };
      return b;
    }
    var sep = function () { h("span", { class: "sep" }, grp); };
    var sw = h("span", { class: "tfsw", role: "group", "aria-label": "Candle timeframe" }, grp);
    c.tfBtns = {};
    [["1m", "1m"], ["5m", "5m"], ["15m", "15m"], ["1h", "1h"], ["D", "D"]].forEach(function (o) {
      var b = h("button", { type: "button", title: "Show " + o[0] + " candles on this chart", "aria-pressed": String(o[0] === effTf(c)) }, sw, o[1]);
      b.onclick = function () { c.tfSel = o[0]; paintTf(c); ensureChart(c); };
      c.tfBtns[o[0]] = b;
    });
    if (DATA.detail_url) {
      c.histN = histDefault;
      c.histSel = h("select", { class: "histsel", title: "Show the sessions before the trade (indicators run across them; VWAP restarts each day)",
                                "aria-label": "History" }, grp);
      HIST_OPTS.forEach(function (o) { var op = h("option", { value: String(o[0]) }, c.histSel, o[1]); if (o[0] === histDefault) op.selected = true; });
      c.histSel.onchange = function () {
        c.histN = +c.histSel.value;
        histDefault = c.histN;
        try { localStorage.setItem("bt32.histDays", String(c.histN)); } catch (e) { /* storage blocked */ }
        ensureChart(c);
      };
    }
    sep();
    mk("−", "Zoom out", function () { zoomBy(c, 0.5); });
    mk("+", "Zoom in", function () { zoomBy(c, 2); });
    mk("⟲", "Reset both axes (0)", function () { resetView(c); });
    var advanced = h("details", { class: "advanced-chart" }, grp);
    h("summary", { title: "Pan and price-axis controls (or drag the chart / ctrl+scroll)" }, advanced, "Pan");
    var pan = h("div", { class: "btns pop" }, advanced);
    mk("←", "Earlier candles", function () { panBy(c, -1); }, pan);
    mk("→", "Later candles", function () { panBy(c, 1); }, pan);
    mk("↑", "Higher prices", function () { pPanBy(c, 1); }, pan);
    mk("↓", "Lower prices", function () { pPanBy(c, -1); }, pan);
    mk("− price", "Price zoom out", function () { pZoomBy(c, 0.5); }, pan);
    mk("+ price", "Price zoom in", function () { pZoomBy(c, 2); }, pan);
    sep();
    c.drawBtn = h("button", { type: "button", "aria-pressed": "false",
      title: "Draw a horizontal line: click, then click the chart (click a line again to remove it)" }, grp, "— Line");
    c.drawBtn.onclick = function () { ensureChart(c); if (c.chart) setDrawMode(c, !c.drawing); };
    mk("✕ Lines", "Remove the horizontal lines you drew", function () { clearUserLines(c); });
    c.vpBtn = h("button", { type: "button", "aria-pressed": "false",
      title: "Fixed range volume profile: click, then click two candles (TradingView's drawing tool)" }, grp, "▥ Profile");
    c.vpBtn.onclick = function () { ensureChart(c); if (c.chart) vpStartTool(c); };
    sep();
    c.lblBtn = h("button", { type: "button", "aria-pressed": String(showLabels),
      title: "Write pattern names and filing headlines on the chart (off: hover a candle to read them in the legend)" }, grp, "Aa Labels");
    c.lblBtn.onclick = function () {
      showLabels = !showLabels;
      try { localStorage.setItem("bt32.labels", showLabels ? "1" : "0"); } catch (e) { /* storage blocked */ }
      cards.forEach(function (k) { if (k.lblBtn) k.lblBtn.setAttribute("aria-pressed", String(showLabels)); if (k.mk) paintMarkers(k); });
    };
    if (DATA.whatif) {
      var wb = h("button", { type: "button", title: "Try entry / exit indicators on this one stock" }, grp, "⚗ What-if");
      wb.onclick = function () { toggleWhatIf(c); };
    }
    mk("⛶", "Full screen (Esc to exit)", function () { toggleFull(c); });
    c.zlabel = h("span", { class: "zlabel" }, bar);

    c.vpPanel = vpBuildPanel(c, sec);
    var wrap = h("div", { class: "chart-wrap" }, sec);
    c.host = h("div", { class: "chart", role: "img", "aria-label": tr.symbol + " candlestick chart" }, wrap);
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
  news: { label: "News (NSE filing in 24h before entry)", fixed: ["Material news", "Routine filings only", "No filing", "Not checked / failed"],
          of: function (t) { return t.news === "news" ? "Material news" : t.news === "minor" ? "Routine filings only" : t.news === "none" ? "No filing" : "Not checked / failed"; } },
  symbol: { label: "Symbol", of: function (t) { return t.symbol; } },
  setup: { label: "Setup", of: function (t) { return nice(t.setup); } },
  month: { label: "Month", byKey: true, of: function (t) { return t.date.slice(0, 7); } },
  weekday: { label: "Weekday", byKey: true,
             of: function (t) { return WEEKDAYS[(new Date(t.date + "T00:00:00Z").getUTCDay() + 6) % 7]; },
             order: WEEKDAYS },
  entry_hour: { label: "Entry hour", byKey: true, of: function (t) { return t.entry_time.slice(0, 2) + ":00"; } }
};
var HAS_NEWS = DATA.trades.some(function (t) { return t.news; });   // lab runs where 'Check news' was run
if (!HAS_NEWS) delete GROUPS.news;
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
    openGroups[c.group.dataset.gid] = true;
    c.group.querySelector(".group-head").setAttribute("aria-expanded", "true");
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
  var sp = function (label, value, tone) {
    var item = h("span", { class: "gstat" }, parent);
    h("span", {}, item, label + " ");
    h("b", { class: tone || "" }, item, value);
  };
  sp("Trades", String(st.n));
  sp("Net", inr(st.inr), cls(st.inr));
  sp("Mean / trade", inr(st.n ? st.inr / st.n : 0), cls(st.inr));
  sp("Median / trade", inr(st.med), cls(st.med));
}
var groupOverviewOpen = false;
/* put the cards back under #charts in their original order, then (if grouping)
   gather the visible ones under one collapsible header per group */
function layoutGroups(keep) {
  var host = document.getElementById("charts"), sum = document.getElementById("gsum");
  cards.forEach(function (c) { host.appendChild(c.sec); c.group = null; c.pinned = false; });
  Array.prototype.slice.call(host.querySelectorAll(".group, .more")).forEach(function (g) { g.remove(); });
  sum.innerHTML = "";
  var btns = document.getElementById("grp-btns");
  if (btns) btns.style.display = groupKey ? "" : "none";
  if (!keep.length) { h("p", { class: "empty-state" }, sum, "No trades match. Reset the filters or choose a different stock."); return; }
  if (!groupKey) { pager(keep, "flat", host); return; }
  var groups = groupList(keep), total = keep.length;
  var panel = h("details", { class: "panel group-overview" }, sum);
  panel.open = groupOverviewOpen;
  panel.ontoggle = function () { groupOverviewOpen = panel.open; };
  h("summary", {}, panel, "Detailed comparison by " + GROUPS[groupKey].label.toLowerCase() + " · " + groups.length + " groups");
  var maxN = Math.max.apply(null, groups.map(function (g) { return g.st.n; }).concat([1]));
  var t = h("table", {}, h("div", { class: "scroll" }, panel));
  var hr = h("tr", {}, h("thead", {}, t));
  ["group", "trades", "", "gross/trade", "net @ real/trade", "win %", "median " + CURSYM + "/trade", "net " + CURSYM].forEach(function (x, i) {
    h("th", { class: i ? "num" : "" }, hr, x);
  });
  var tb = h("tbody", {}, t);
  groups.forEach(function (g) {
    var gid = groupKey + ":" + g.name;
    var wrapG = h("div", { class: "group" + (openGroups[gid] ? "" : " collapsed"), "data-gid": gid }, host);
    var toggle = function (collapsed) {
      wrapG.classList.toggle("collapsed", collapsed);
      openGroups[gid] = !collapsed;
      head.setAttribute("aria-expanded", String(!collapsed));
    };
    var head = h("div", { class: "group-head", role: "button", "aria-expanded": String(!!openGroups[gid]) }, wrapG);
    var groupName = groupKey === "exit_reason" ? ({stop:"Stop hit",target:"Target hit",eod:"Session close"}[g.name] || nice(g.name)) : g.name;
    h("span", { class: "gname" }, head, groupName);
    statSpans(head, g.st, total);
    h("span", { class: "group-action" }, head);
    var body = h("div", { class: "group-body" }, wrapG);
    g.cards.forEach(function (c) { body.appendChild(c.sec); c.group = wrapG; });
    pager(g.cards, gid, body);
    head.onclick = function () { toggle(!wrapG.classList.contains("collapsed")); };
    keyboardAction(head, head.onclick, groupName + " trade group");
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
      head.focus({ preventScroll: true });
      wrapG.scrollIntoView({ behavior: "smooth", block: "start" });
    };
    keyboardAction(row, row.onclick, "Open " + g.name + " trades");
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
  var nwEl = document.getElementById("f-news"), nw = nwEl ? nwEl.value : "";
  var monEl = document.getElementById("f-mon"), mon = monEl ? monEl.value : "";
  return cards.filter(function (c) {
    if (sym && c.tr.symbol !== sym) return false;
    if (mon && c.tr.date.slice(0, 7) !== mon) return false;
    if (ex && c.tr.exit_reason !== ex) return false;
    if (nw === "news" && c.tr.news !== "news") return false;
    if (nw === "minor" && c.tr.news !== "minor") return false;
    if (nw === "none" && c.tr.news !== "none") return false;
    if (nw === "any" && c.tr.news !== "news" && c.tr.news !== "minor") return false;
    if (nw === "unk" && (c.tr.news === "news" || c.tr.news === "minor" || c.tr.news === "none")) return false;
    if (out === "win" && c.tr.gross_pct <= 0) return false;
    if (out === "loss" && c.tr.gross_pct > 0) return false;
    return true;
  });
}
/* "Showing 21 of 133 trades" under the filters, so a filter's effect is never a guess */
function showCount(keep) {
  var el = document.getElementById("f-count");
  if (!el) return;
  var st = stats(keep), all = cards.length;
  el.textContent = "Showing " + keep.length + " of " + all + " trades · headline figures reflect these filters, at real costs.";
  var values = { "metric-trades": String(st.n), "metric-win": st.n ? st.win.toFixed(1) + "%" : "–",
    "metric-net": st.n ? inr(st.inr) : "–", "metric-mean": st.n ? inr(st.inr / st.n) : "–", "metric-median": st.n ? inr(st.med) : "–" };
  Object.keys(values).forEach(function(id) {
    var metric = document.getElementById(id); if (!metric) return;
    metric.textContent = values[id];
    if (["metric-net", "metric-mean", "metric-median"].includes(id)) {
      metric.className = cls(id === "metric-median" ? st.med : st.inr);
      metric.parentNode.classList.remove("pos", "neg");
    }
  });
}
var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function monthName(ym) { return MONTHS[+ym.slice(5, 7) - 1] + " " + ym.slice(0, 4); }
function render() {
  var keep = visible();
  showCount(keep);
  cards.forEach(function (c) { c.sec.style.display = "none"; });
  layoutGroups(keep);   // also decides which of the kept cards are on this page
  numberTrades(keep);
  keep.forEach(function (c) {
    if (c.chart && c.drawn !== drawKey(c)) ensureChart(c);
  });
  var tb = document.querySelector("#det tbody");
  tb.innerHTML = "";
  keep.slice().sort(function (a, b) {
    var x = a.tr[sortKey], y = b.tr[sortKey];
    return (x > y ? 1 : x < y ? -1 : 0) * sortDir;
  }).forEach(function (c) {
    var row = h("tr", {}, tb);
    h("td", {}, row, c.tr.date);
    var symTd = h("td", {}, row, c.tr.symbol);
    var nb2 = newsBadge(c.tr);
    if (nb2) symTd.appendChild(nb2);
    h("td", {}, row, c.tr.entry_time);
    h("td", {}, row, nice(c.tr.exit_reason));
    h("td", { class: "num " + cls(c.tr.gross_pct) }, row, pct(c.tr.gross_pct));
    h("td", { class: "num " + cls(c.tr.net_real_inr) }, row, inr(c.tr.net_real_inr));
    row.onclick = function () {
      reveal(c);
      ensureChart(c);
      c.sec.scrollIntoView({ behavior: "smooth", block: "center" });
      c.sec.focus({ preventScroll: true });
      c.sec.classList.add("flash");
      setTimeout(function () { c.sec.classList.remove("flash"); }, 2000);
    };
    keyboardAction(row, row.onclick, "Open " + c.tr.symbol + " trade on " + c.tr.date);
  });
}

/* ---------- PDF export ----------
   A separate print document contains immutable chart images. The screen layout,
   pagination, collapsed groups and live canvas sizes cannot split those images. */
var pdfExportActive = false;
function nextPaint() {
  return new Promise(function(resolve) { requestAnimationFrame(function() { requestAnimationFrame(resolve); }); });
}
async function pdfChartSnapshot(tr) {
  var host = h("div", {class:"pdf-render-host", "aria-hidden":"true"}, document.body);
  var c = { tr:tr, host:host, zlabel:h("span",{}), legend:h("div",{}), readout:h("div",{}) };
  try {
    createChart(c);
    setSeries(c);
    await nextPaint();
    // Include both BUY and SELL plus context, even for a long-held 1-minute trade.
    var from = Math.max(-0.5, Math.min(c.eIdx, barIndex(c.data, tr.exit_time)) - 8.5);
    var to = Math.min(c.data.length - 0.5, Math.max(c.eIdx, barIndex(c.data, tr.exit_time)) + 8.5);
    c.chart.timeScale().setVisibleLogicalRange({from:from, to:to});
    await nextPaint();
    return { image:c.chart.takeScreenshot().toDataURL("image/png"), legend:c.legend.innerHTML, readout:c.readout.innerHTML };
  } finally { if (c.chart) c.chart.remove(); host.remove(); }
}
function pdfPrice(value) { return Number.isFinite(value) ? value.toFixed(2) : "—"; }
function pdfTradeHeading(parent, tr, index, count) {
  h("h2", {}, parent, "Trade " + (index + 1) + " of " + count + " · " + tr.symbol + " · " + tr.date);
  h("p", {class:"pdf-trade-meta"}, parent,
    nice(tr.setup) + " · " + tr.entry_time + " → " + tr.exit_time + " · " + nice(tr.exit_reason)
    + " · Qty " + tr.qty + " · Entry " + pdfPrice(tr.entry) + " · Stop " + pdfPrice(tr.stop)
    + " · Target " + pdfPrice(tr.target) + " · Exit " + pdfPrice(tr.exit));
  h("p", {class:"pdf-trade-meta"}, parent,
    "Net at real costs " + inr(tr.net_real_inr) + " · " + (US ? "Engine-booked" : "Stress") + " net " + inr(tr.net_stress_inr)
    + " · Gross " + pct(tr.gross_pct) + " · " + tf + " candles · " + (levelMode === "key" ? "Key price levels" : "All price levels"));
}
async function exportPdf() {
  if (pdfExportActive) return;
  var msg = document.getElementById("export-msg"), btn = document.getElementById("export-pdf");
  var list = visible(); // All matching trades, including collapsed groups and later pages.
  if (!list.length) { msg.textContent = "No trades match the filters. Nothing to export."; return; }
  if (list.length > 60 && !confirm("Export all " + list.length + " filtered trades, with a chart page and separate level tables for each?")) return;
  pdfExportActive = true; btn.disabled = true;
  var root = h("div", {id:"pdf-report", "aria-hidden":"true"}, document.body);
  var frame = null, detailRequests = new AbortController(), finished = false;
  var timeout = setTimeout(function() { detailRequests.abort(); }, 120000);
  var cleanup = function() {
    if (finished) return; finished = true;
    clearTimeout(timeout); detailRequests.abort();
    window.removeEventListener("afterprint", cleanup);
    if (frame) frame.remove(); root.remove();
    btn.disabled = false; pdfExportActive = false;
  };
  try {
    var summary = h("section", {class:"pdf-summary"}, root);
    h("h1", {}, summary, document.title);
    h("p", {}, summary, "Exploratory backtest · " + list.length + (list.length === 1 ? " exported trade · " : " exported trades · ") + tf + " candles · net at real costs.");
    var filters = ["f-sym","f-exit","f-mon","f-out","f-news"].map(function(id) {
      var el = document.getElementById(id);
      return el && el.value ? el.getAttribute("aria-label") + ": " + el.options[el.selectedIndex].textContent : null;
    }).filter(Boolean);
    h("p", {}, summary, filters.length ? "Filters: " + filters.join(" · ") : "Filters: all trades in this run.");
    var st = stats(list);
    h("p", {class:"pdf-summary-metrics"}, summary, "Net " + inr(st.inr) + " · Mean/trade " + inr(st.inr/st.n)
      + " · Median/trade " + inr(st.med) + " · Win rate " + st.win.toFixed(1) + "%");
    var savedSettings = document.querySelector(".run-notes > p");
    if (savedSettings) h("p", {class:"pdf-settings"}, summary, savedSettings.textContent);
    h("p", {}, summary, "Each following chart includes price, MACD and volume. Complete price-level tables follow each chart on separate pages. Charts cover entry through exit with surrounding candles.");
    var trades = new Array(list.length);
    // Read the cache with bounded concurrency. Never silently export a missing chart.
    var cursor = 0;
    await Promise.all(Array.from({length:Math.min(4,list.length)}, async function() {
      while (cursor < list.length) {
        var i = cursor++, source = list[i].tr;
        msg.textContent = "Loading trade data… " + (i + 1) + "/" + list.length;
        if (source.bars) trades[i] = source;
        else {
          var response = await fetch(DATA.detail_url + list[i].i, {signal:detailRequests.signal});
          if (!response.ok) throw new Error("Could not load " + source.symbol + " " + source.date + " (" + response.status + ")");
          trades[i] = Object.assign({},source,await response.json());
        }
        if (!trades[i].bars || !trades[i].bars.length) throw new Error("No cached candles for " + source.symbol + " " + source.date);
      }
    }));
    clearTimeout(timeout);
    for (var i = 0; i < trades.length; i++) {
      msg.textContent = "Preparing complete charts… " + (i + 1) + "/" + trades.length;
      var tr = trades[i], snapshot = await pdfChartSnapshot(tr);
      var page = h("section", {class:"pdf-chart-page"}, root);
      pdfTradeHeading(page,tr,i,trades.length);
      h("img", {class:"pdf-chart-image",src:snapshot.image,alt:tr.symbol + " price, MACD and volume chart"}, page);
      h("div", {class:"pdf-chart-legend"}, page).innerHTML = snapshot.legend;
      h("div", {class:"pdf-chart-readout"}, page).innerHTML = snapshot.readout;
      if (tr.levels && tr.levels.length) {
        var levels = h("section", {class:"pdf-level-page"}, root);
        pdfTradeHeading(levels,tr,i,trades.length);
        h("h3", {}, levels, "Complete support and resistance levels");
        levelTable(levels,tr);
        var d = levels.querySelector("details"); d.open = true;
      }
    }
    // The iframe stays alive while the native print preview is open. No screen
    // styles, responsive breakpoints, or afterprint mutations touch its content.
    frame = h("iframe", {class:"pdf-print-frame",title:"Printable trade report", "aria-hidden":"true"}, document.body);
    var doc = frame.contentDocument;
    doc.open(); doc.write("<!doctype html><html><head><meta charset='utf-8'><title></title></head><body></body></html>"); doc.close();
    doc.title = document.title;
    var style = doc.createElement("style"); style.textContent = Array.from(document.querySelectorAll("style")).map(function(e) {return e.textContent;}).join("\n");
    doc.head.appendChild(style);
    doc.body.className = "pdf-document";
    var report = root.cloneNode(true); report.removeAttribute("aria-hidden"); doc.body.appendChild(report);
    await Promise.all(Array.from(doc.images).map(function(img) { return img.decode(); }));
    await nextPaint();
    window.addEventListener("afterprint", cleanup, {once:true});
    // Some browsers dispatch afterprint only on the frame's window.
    frame.contentWindow.addEventListener("afterprint", cleanup, {once:true});
    msg.textContent = "Ready: " + trades.length + (trades.length === 1 ? " complete chart." : " complete charts.") + " Choose Save as PDF in the print dialog.";
    frame.contentWindow.focus(); frame.contentWindow.print();
  } catch (error) {
    msg.textContent = "PDF export stopped: " + (error.name === "AbortError" ? "chart data took too long to load. Try fewer trades." : error.message);
    cleanup();
  }
}

function improveReportUX() {
  var skip = h("a", { class: "skip-link", href: "#charts" }, null, "Skip to trade charts");
  document.body.prepend(skip);
  var main = document.querySelector(".main"); main.setAttribute("role", "main");
  document.getElementById("charts").tabIndex = -1;
  var side = document.querySelector(".side");
  side.setAttribute("aria-label", "Trade navigation and chart settings");
  // Keep reading and keyboard order aligned when the sidebar moves above charts on mobile.
  main.parentNode.insertBefore(side, main);
  var header = document.querySelector("header");
  if (DATA.whatif) {
    var title = header.querySelector("h1"), runName = title.textContent.replace(/^Lab run — /, "");
    title.textContent = "Trade explorer";
    h("p", { class: "run-name" }, header, runName);
  }
  var notes = header.querySelector("p");
  if (notes) {
    var detail = h("details", { class: "run-notes" }, header);
    h("summary", {}, detail, "Run details and costs"); detail.appendChild(notes);
  }
  if (DATA.whatif) h("p", { class: "hint" }, header, "Exploratory results · net figures include real costs.");
  var legend = document.getElementById("legend");
  var key = h("details", { class: "chart-key" }, null);
  h("summary", {}, key, "Chart legend — colours and indicators");
  legend.parentNode.insertBefore(key, legend); key.appendChild(legend);
  document.querySelectorAll(".side .hint").forEach(function(e) {
    if (e.textContent === "Click any row to jump to its chart.") e.textContent = "Choose a trade row with a click or Enter to open its chart.";
  });
  var count = document.getElementById("f-count"); count.setAttribute("role", "status");
  var kh = h("p", { class: "kbd-hint" }, null);
  kh.innerHTML = "Move between trades with <kbd>J</kbd> next and <kbd>K</kbd> previous, or ‹ › on each card (also in full screen). "
    + "On a chart: <kbd>+</kbd> <kbd>−</kbd> zoom, <kbd>0</kbd> reset, arrow keys pan.";
  count.after(kh);
  var reset = h("button", { type: "button", class: "reset-filters" }, count.parentNode, "Reset filters");
  reset.onclick = function () {
    ["f-sym", "f-exit", "f-mon", "f-out", "f-news"].forEach(function (id) { var e = document.getElementById(id); if (e) e.value = ""; });
    render();
  };
  document.querySelectorAll(".scroll").forEach(function (e) { e.tabIndex = 0; e.setAttribute("role", "region"); e.setAttribute("aria-label", "Scrollable trade table"); });
  // Give net mean and median the same prominence as total P&L.
  var statsHost = document.querySelector(".stats"), oldChips = Array.from(statsHost.children);
  var diagnostics = h("details", { class: "cost-details" }, null);
  h("summary", {}, diagnostics, "More metrics and cost assumptions");
  var diagnosticsBody = h("div", { class: "stats" }, diagnostics);
  oldChips.forEach(function(chip,i) { if (![0,7,8].includes(i)) diagnosticsBody.appendChild(chip); });
  if (detail) { detail.appendChild(diagnosticsBody); } else statsHost.after(diagnostics);
  var headlineLabels = {0: "Trades", 7: "Win rate", 8: "Net P&L"};
  [0,7,8].forEach(function(i) {
    var chip = oldChips[i], value = chip.querySelector("b");
    if (i === 8) value.textContent = inr(DATA.summary.net_real_inr);
    chip.replaceChildren(h("span", {class: "metric-label"}, null, headlineLabels[i]), value);
    value.id = {0:"metric-trades",7:"metric-win",8:"metric-net"}[i];
  });
  var real = DATA.trades.map(function (t) { return t.net_real_inr; }).filter(function (x) { return Number.isFinite(x); }).sort(function(a,b) { return a-b; });
  if (real.length) {
    var mid = Math.floor(real.length / 2), median = real.length % 2 ? real[mid] : (real[mid-1] + real[mid]) / 2;
    var mean = real.reduce(function(a,b) { return a+b; },0) / real.length;
    [["Mean / trade", mean], ["Median / trade", median]].forEach(function (v) {
      var chip = h("span", { class: "chip " + cls(v[1]) }, statsHost);
      h("span", {class: "metric-label"}, chip, v[0]); h("b", {id: v[0] === "Mean / trade" ? "metric-mean" : "metric-median"}, chip, inr(v[1]));
    });
  }
  // One navigation surface above the charts, with optional controls disclosed on demand.
  var chartPanel = side.children[0], filterPanel = side.children[1];
  side.classList.add("explorer-controls");
  side.replaceChildren(filterPanel, chartPanel);
  filterPanel.querySelector("h3").textContent = "Explore trades";
  var primaryFilters = filterPanel.querySelector(".filters");
  var extra = h("details", {class: "extra-filters"}, null);
  h("summary", {}, extra, "More filters");
  var extraFields = h("div", {class: "filters"}, extra);
  Array.from(primaryFilters.children).forEach(function(label) {
    if (!label.querySelector("#f-sym")) extraFields.appendChild(label);
  });
  primaryFilters.appendChild(document.getElementById("f-grp").parentNode);
  primaryFilters.appendChild(extra);
  primaryFilters.appendChild(reset);
  primaryFilters.appendChild(document.getElementById("grp-btns"));
  var oldGroupBar = filterPanel.querySelector(".grp"); if (oldGroupBar) oldGroupBar.remove();
  var tradeList = h("details", {class: "trade-index"}, filterPanel);
  h("summary", {}, tradeList, "Find a trade by date or stock");
  tradeList.appendChild(document.getElementById("det").parentNode);
  var rowHint = Array.from(filterPanel.querySelectorAll("p.hint"))[0]; if (rowHint) tradeList.appendChild(rowHint);
  chartPanel.classList.add("chart-settings-panel");
  chartPanel.querySelector("h3").remove();
  var settings = h("details", {class: "chart-settings"}, null);
  h("summary", {}, settings, "Chart settings & legend");
  Array.from(chartPanel.children).forEach(function(child) { settings.appendChild(child); });
  chartPanel.appendChild(settings);
  var exportButton = document.getElementById("export-pdf");
  primaryFilters.appendChild(exportButton);
  exportButton.title = "Export every trade matching the filters, including collapsed groups. Complete charts and separate price-level tables.";
  var exportStatus = document.getElementById("export-msg"); exportStatus.setAttribute("role","status"); primaryFilters.appendChild(exportStatus);
  document.getElementById("f-tf").options[0].textContent = "5-minute candles";
  document.getElementById("f-tf").options[1].textContent = "1-minute candles";
  document.getElementById("f-lv").options[0].textContent = "Key support & resistance";
  document.getElementById("f-lv").options[1].textContent = "All price levels";
  document.body.classList.add("trade-explorer");

}

function init() {
  buildCards();
  document.getElementById("export-pdf").onclick = exportPdf;
  var leg = document.getElementById("legend");
  [["up candle", COL.up], ["down candle", COL.dn], ["EMA9 / MACD", COL.ema9],
   ["EMA20 / signal", COL.ema20], ["EMA200", COL.ema200], ["VWAP (dashed)", COL.vwap],
   ["RESISTANCE (thick) · other resistance (thin)", COL.res],
   ["SUPPORT (thick) · other support (thin)", COL.sup], ["BUY / entry ▲", COL.buy],
   ["SELL / exit ▼", COL.sell], ["Stop (dashed)", COL.stop], ["Target (dashed)", COL.target],
   ["vol avg, 20 prior bars", alpha(COL.volAvg, 0.7)], ["● formation", COL.pattern], ["● indecision after ↓", CONTEXT.down.col], ["● indecision after ↑", CONTEXT.up.col],
   ["holding period", alpha(COL.up, 0.35)], ["⚡ news filing (column)", alpha("#f59e0b", 0.8)], ["▲ market reacted (decision bar)", "#38bdf8"]].forEach(function (p) {
    var sp = h("span", {}, leg);
    h("i", { style: "border-top-color:" + p[1] }, sp);
    h("span", {}, sp, p[0]);
  });
  h("p", { class: "hint" }, leg.parentNode,
    "Charts by TradingView Lightweight Charts."
  );
  // A server started before the Month filter existed serves the old template: add the
  // Month select and the count line here so the page works without a restart.
  if (!document.getElementById("f-mon")) {
    var exLabel = document.getElementById("f-exit").parentNode;
    var monLabel = h("label", {}, null, "Month ");
    h("select", { id: "f-mon" }, monLabel).appendChild(h("option", { value: "" }, null, "all"));
    exLabel.parentNode.insertBefore(monLabel, exLabel.nextSibling);
  }
  if (!document.getElementById("f-count")) {
    var fl = document.getElementById("f-exit").parentNode.parentNode;
    fl.parentNode.insertBefore(h("div", { class: "fcount", id: "f-count" }), fl.nextSibling);
  }
  // dropdown labels carry the trade count: "stop (27)", "Jan 2026 (21)"
  var syms = {}, exits = {}, months = {};
  DATA.trades.forEach(function (t) {
    syms[t.symbol] = (syms[t.symbol] || 0) + 1;
    exits[t.exit_reason] = (exits[t.exit_reason] || 0) + 1;
    var m = t.date.slice(0, 7);
    months[m] = (months[m] || 0) + 1;
  });
  Object.keys(syms).sort().forEach(function (s) {
    h("option", { value: s }, document.getElementById("f-sym"), s + " (" + syms[s] + ")");
  });
  Object.keys(exits).sort().forEach(function (s) {
    h("option", { value: s }, document.getElementById("f-exit"), nice(s) + " (" + exits[s] + ")");
  });
  var monSel = document.getElementById("f-mon");
  if (monSel) Object.keys(months).sort().forEach(function (m) {
    h("option", { value: m }, monSel, monthName(m) + " (" + months[m] + ")");
  });
  if (HAS_NEWS) {   // News filter, next to Outcome
    var lab = document.createElement("label");
    lab.innerHTML = "News <select id='f-news'><option value=''>all</option><option value='news'>material news</option>"
      + "<option value='minor'>routine filings only</option><option value='any'>any filing</option><option value='none'>no filing</option><option value='unk'>not checked / failed</option></select>";
    document.getElementById("f-out").parentNode.after(lab);
  }
  document.querySelectorAll(".filters label").forEach(function(label) {
    var select = label.querySelector("select");
    if (select) select.setAttribute("aria-label", label.firstChild.textContent.trim());
  });
  ["f-sym", "f-exit", "f-out", "f-mon", "f-news"].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.onchange = render;
  });
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
        g.querySelector(".group-head").setAttribute("aria-expanded", String(!collapsed));
        openGroups[g.dataset.gid] = !collapsed;
      });
    };
    var gb = document.getElementById("grp-btns");
    h("button", { type: "button" }, gb, "Expand all").onclick = function () { setAll(false); };
    h("button", { type: "button" }, gb, "Collapse all").onclick = function () { setAll(true); };
  }
  var tfSel = document.getElementById("f-tf");
  tfSel.value = tf;
  tfSel.onchange = function () { tf = this.value; cards.forEach(function (c) { c.tfSel = null; paintTf(c); }); render(); };
  document.querySelectorAll("#det th").forEach(function (th) {
    th.setAttribute("aria-sort", th.dataset.k === sortKey ? "ascending" : "none");
    th.onclick = function () {
      var k = th.getAttribute("data-k");
      sortDir = k === sortKey ? -sortDir : 1;
      sortKey = k;
      document.querySelectorAll("#det th").forEach(function(other) { other.setAttribute("aria-sort", other === th ? (sortDir === 1 ? "ascending" : "descending") : "none"); });
      render();
    };
    keyboardAction(th, th.onclick, "Sort by " + th.textContent);
  });
  improveReportUX();
  render();
}
document.addEventListener("DOMContentLoaded", init);
