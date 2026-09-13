/* BT29 trade-level report: every card is one REAL past trade of the momentum
   system, with the v3 candlestick formations that had closed before the fill
   marked in the chart itself. DATA is injected by bt29_trade_report.py. */
"use strict";

var SVGNS = "http://www.w3.org/2000/svg";
var UP = "#2dd4bf", DN = "#fb7185", ACC = "#fbbf24", DIM = "#8fa1bb";
var charts = document.getElementById("charts");
var tip = document.getElementById("tip");
var cards = [], rows = [];
var sortKey = "t", sortDir = 1, selected = null;

function el(tag, attrs, parent, text) {
  var n = document.createElementNS(SVGNS, tag);
  for (var k in attrs) n.setAttribute(k, attrs[k]);
  if (text !== undefined) n.textContent = text;
  if (parent) parent.appendChild(n);
  return n;
}
function pct(x, d) {
  if (x === null || x === undefined || isNaN(x)) return "–";
  return (x >= 0 ? "+" : "") + x.toFixed(d === undefined ? 2 : d) + "%";
}
function inr(x) {
  if (x === null || x === undefined || isNaN(x)) return "–";
  return (x >= 0 ? "+₹" : "−₹") + Math.abs(x).toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}
function cls(x) { return x > 0 ? "pos" : x < 0 ? "neg" : ""; }
function nice(s) { return String(s).split("_").join(" "); }
function hhmm(ts) { return ts ? ts.slice(11, 16) : "–"; }

/* ---------------------------------------------------------------- chart */

function drawChart(card) {
  var day = card.day, svg = card.svg, tr = day.trade;
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  var all = day.bars;
  var from = Math.max(0, Math.min(card.from, all.length - 3));
  var to = Math.min(all.length, Math.max(card.to, from + 3));
  var bars = all.slice(from, to);
  if (!bars.length) return;
  var W = 1000, H = 340, L = 60, R = 66, T = 46, B = 24;
  var lo = Infinity, hi = -Infinity, i;
  for (i = 0; i < bars.length; i++) {
    if (bars[i].l < lo) lo = bars[i].l;
    if (bars[i].h > hi) hi = bars[i].h;
  }
  [tr.entry, tr.stop, tr.exit].forEach(function (v) {
    if (v && v > 0 && v < lo) lo = v;
    if (v && v > hi) hi = v;
  });
  var pad = (hi - lo) * 0.13 || 1;
  lo -= pad; hi += pad;
  var step = (W - L - R) / bars.length;
  function X(gi) { return L + (gi - from + 0.5) * step; }
  function Xf(f) { return L + (f - from) * step; }
  function Y(v) { return T + (1 - (v - lo) / (hi - lo)) * (H - T - B); }
  var cw = Math.max(2, Math.min(16, step * 0.66));

  for (i = 0; i <= 4; i++) {
    var yv = lo + (hi - lo) * i / 4;
    el("line", { x1: L, x2: W - R, y1: Y(yv), y2: Y(yv), stroke: "#fff", "stroke-opacity": ".07" }, svg);
    el("text", { x: L - 6, y: Y(yv) + 3, fill: DIM, "font-size": "9.5", "text-anchor": "end" },
      svg, "₹" + yv.toFixed(1));
  }

  /* the holding period, shaded behind the candles */
  if (tr.entry_x !== null && tr.exit_x !== null) {
    var hx1 = Xf(Math.max(tr.entry_x, from)), hx2 = Xf(Math.min(tr.exit_x, to));
    if (hx2 > hx1) {
      el("rect", { x: hx1, y: T, width: hx2 - hx1, height: H - T - B,
        fill: tr.gross_pct >= 0 ? UP : DN, "fill-opacity": ".055" }, svg);
    }
  }

  var every = Math.max(1, Math.ceil(bars.length / 9));
  bars.forEach(function (b, k) {
    var gi = from + k, col = b.c >= b.o ? UP : DN;
    el("line", { x1: X(gi), x2: X(gi), y1: Y(b.h), y2: Y(b.l), stroke: col, "stroke-width": "1" }, svg);
    el("rect", { x: X(gi) - cw / 2, y: Y(Math.max(b.o, b.c)), width: cw,
      height: Math.max(1.4, Y(Math.min(b.o, b.c)) - Y(Math.max(b.o, b.c))), fill: col }, svg);
    if (k % every === 0) {
      el("text", { x: X(gi), y: H - 7, "text-anchor": "middle", fill: DIM, "font-size": "9.5" },
        svg, b.label);
    }
  });

  /* trade levels: BUY / STOP / EXIT, drawn like the reference chart */
  [["BUY", tr.entry, ACC, "6 0"], ["STOP", tr.stop, DN, "5 4"],
   ["EXIT", tr.exit, tr.gross_pct >= 0 ? UP : DN, "5 4"]].forEach(function (row) {
    var v = row[1];
    if (!v || v < lo || v > hi) return;
    el("line", { x1: L, x2: W - R, y1: Y(v), y2: Y(v), stroke: row[2], "stroke-width": "1",
      "stroke-dasharray": row[3], "stroke-opacity": ".85" }, svg);
    el("text", { x: W - R + 4, y: Y(v) + 3, fill: row[2], "font-size": "9" }, svg,
      row[0] + " ₹" + v.toFixed(1));
  });
  if (tr.entry_x !== null && tr.entry >= lo && tr.entry <= hi) {
    var ex = Xf(tr.entry_x);
    el("path", { d: "M " + ex + " " + (Y(tr.entry) + 11) + " l -5 8 l 10 0 Z", fill: ACC }, svg);
  }
  if (tr.exit_x !== null && tr.exit >= lo && tr.exit <= hi) {
    var xx = Xf(tr.exit_x), xc = tr.gross_pct >= 0 ? UP : DN;
    el("path", { d: "M " + xx + " " + (Y(tr.exit) - 11) + " l -5 -8 l 10 0 Z", fill: xc }, svg);
  }

  /* pattern overlays, lane-stacked so labels never collide */
  var idx = card.index, lanes = [];
  var vis = day.patterns.filter(function (p) {
    var s = idx[p.start], e = idx[p.end];
    return s !== undefined && e !== undefined && e >= from && s < to && passes(p);
  });
  vis.forEach(function (p) {
    var s = Math.max(idx[p.start], from), e = Math.min(idx[p.end], to - 1);
    var x1 = X(s) - cw * 0.9, x2 = X(e) + cw * 0.9;
    var label = nice(p.name) + " · " + day.tf +
      (p.strength === undefined ? "" : " · " + p.strength.toFixed(2) + "\u00d7");
    var wpx = label.length * 4.9, cx = (x1 + x2) / 2;
    var lx1 = Math.min(Math.max(cx - wpx / 2, L), W - R - wpx), lx2 = lx1 + wpx;
    var lane = 0;
    while (lanes[lane] !== undefined && lanes[lane] > lx1 - 5) lane++;
    lanes[lane] = lx2;
    var g = el("g", { "data-s": p.start, "data-e": p.end,
      opacity: p.weak ? 0.42 : 1,
      "class": "pat " + (p.direction || "neutral") + (p.weak ? " weak" : "") }, svg);
    el("rect", { x: x1, y: T, width: Math.max(3, x2 - x1), height: H - T - B, "class": "pat-box" }, g);
    var ly = T - 32 + lane * 10;
    el("path", { d: "M " + x1 + " " + (ly + 9) + " V " + (ly + 3) + " H " + x2 + " V " + (ly + 9),
      "class": "pat-bracket" }, g);
    el("text", { x: cx, y: ly, "text-anchor": "middle", "class": "pat-label" }, g, label);
    var body = tipText(p, day);
    g.addEventListener("mousemove", function (ev) {
      tip.style.display = "block";
      tip.style.left = Math.min(ev.clientX + 14, window.innerWidth - 350) + "px";
      tip.style.top = (ev.clientY + 14) + "px";
      tip.textContent = body;
    });
    g.addEventListener("mouseleave", function () { tip.style.display = "none"; });
    g.addEventListener("click", function () { selectPattern(card, p, true); });
  });
  if (lanes.length > 4) {
    svg.setAttribute("viewBox", "0 -" + ((lanes.length - 4) * 10) + " " + W +
      " " + (H + (lanes.length - 4) * 10));
  } else {
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  }

  if (selected && selected.card === card && selected.pattern) {
    var p2 = selected.pattern;
    var gsel = svg.querySelector('g[data-s="' + p2.start + '"][data-e="' + p2.end + '"]');
    if (gsel) gsel.classList.add("sel");
  }
}

function tipText(p, day) {
  return nice(p.name).toUpperCase() + "  (" + p.direction + " / " + p.kind + ")\n" +
    day.symbol + "  " + day.date + "\n" +
    "formation " + hhmm(p.start) + " → " + hhmm(p.end) + " (" + day.tf + ")\n" +
    "closed " + p.bars_ago + " bar(s) before the fill at " + hhmm(day.trade.entry_ts) + "\n" +
    "prior trend: " + p.prior_trend + "\n" +
    (p.strength === undefined ? "" :
      "strength: " + p.strength.toFixed(2) + "x the recent average range" +
      (p.weak ? "  <- too small to act on\n" : "\n")) +
    "confirmation ₹" + p.confirmation.toFixed(2) + "   invalidation ₹" + p.invalidation.toFixed(2);
}

/* ------------------------------------------------------------- filtering */

function passes(p) {
  var pre = document.getElementById("f-pre");
  if (pre && pre.checked && !p.pre) return false;
  var d = document.getElementById("f-dir").value;
  var n = document.getElementById("f-pat").value;
  if (d === "directional" && p.direction === "neutral") return false;
  if (d !== "all" && d !== "directional" && p.direction !== d) return false;
  if (n !== "all" && p.name !== n) return false;
  return true;
}
function tradePasses(r) {
  var b = document.getElementById("f-bucket").value;
  var o = document.getElementById("f-out").value;
  var ordSel = document.getElementById("f-ord");
  var sizeSel = document.getElementById("f-size");
  if (b !== "all" && r.bucket !== b) return false;
  if (o === "win" && !(r.gross > 0)) return false;
  if (o === "loss" && !(r.gross <= 0)) return false;
  if (ordSel) {
    var v = ordSel.value;
    if (v === "12" && !(r.ord === 1 || r.ord === 2)) return false;
    if (v === "3" && !(r.ord >= 3)) return false;
  }
  if (sizeSel) {
    var sv = sizeSel.value;
    if (sv === "big" && !(r.size >= 1)) return false;
    if (sv === "small" && !(r.size !== null && r.size < 1)) return false;
  }
  return true;
}

/* ----------------------------------------------------------------- cards */

function buildCards() {
  DATA.trades.forEach(function (day, di) {
    var tr = day.trade;
    var sec = document.createElement("section");
    sec.className = "card"; sec.id = "trade-" + di;
    var head = document.createElement("div");
    head.className = "card-head";
    var left = document.createElement("div");
    left.innerHTML = "<h2>" + day.symbol + " · " + day.date + " · " + nice(tr.setup) + "</h2>" +
      '<div class="meta">bought ' + hhmm(tr.entry_ts) + " @ ₹" + tr.entry.toFixed(2) +
      " · stop ₹" + tr.stop.toFixed(2) + " · exited " + hhmm(tr.exit_ts) + " @ ₹" +
      tr.exit.toFixed(2) + " (" + nice(tr.exit_reason) + ") · qty " + tr.qty +
      " · pullback #" + (tr.pullback_ord === null ? "?" : tr.pullback_ord) +
      " · v3 saw: <b>" + (tr.v3_tags ? nice(tr.v3_tags.split("|").join(", ")) : "nothing") +
      "</b>" + (tr.strength === null || tr.strength === undefined ? "" :
        " (nearest formation spans " + tr.strength.toFixed(2) +
        "× the recent average range)") + "</div>";
    var right = document.createElement("div");
    right.className = "pnl " + cls(tr.net_real_inr);
    right.innerHTML = inr(tr.net_real_inr) +
      '<span class="sub">' + pct(tr.gross_pct) + " gross · " + pct(tr.net_real_pct) +
      " after real costs</span>";
    head.appendChild(left); head.appendChild(right);
    sec.appendChild(head);
    var zoom = document.createElement("div");
    zoom.className = "zoom";
    [["←", "pan-"], ["−", "out"], ["+", "in"], ["→", "pan+"], ["reset", "reset"],
     ["the trade", "trade"]].forEach(function (b) {
      var btn = document.createElement("button");
      btn.textContent = b[0];
      btn.onclick = (function (act, c) { return function () { zoomCard(c, act); }; })(b[1], di);
      zoom.appendChild(btn);
    });
    sec.appendChild(zoom);
    var svg = el("svg", { viewBox: "0 0 1000 340", "class": "chart" });
    sec.appendChild(svg);
    charts.appendChild(sec);
    var index = {};
    day.bars.forEach(function (b, i) { index[b.t] = i; });
    var card = { sec: sec, svg: svg, day: day, index: index, from: 0, to: day.bars.length };
    cards.push(card);
    if (!day.bars.length) {
      var d0 = document.createElement("div");
      d0.className = "empty"; d0.textContent = "No complete 5-minute bars for this session.";
      sec.appendChild(d0);
    } else {
      drawChart(card);
    }
  });
}

function zoomCard(di, act) {
  var c = cards[di], n = c.day.bars.length, span = c.to - c.from, tr = c.day.trade;
  if (act === "reset") { c.from = 0; c.to = n; }
  else if (act === "trade") {
    var s = tr.entry_x === null ? 0 : Math.floor(tr.entry_x);
    var e = tr.exit_x === null ? n : Math.ceil(tr.exit_x);
    c.from = Math.max(0, s - 10); c.to = Math.min(n, e + 6);
  } else if (act === "in") {
    var cut = Math.max(1, Math.round(span * 0.2));
    if (span - 2 * cut >= 8) { c.from += cut; c.to -= cut; }
  } else if (act === "out") {
    var grow = Math.max(1, Math.round(span * 0.25));
    c.from = Math.max(0, c.from - grow); c.to = Math.min(n, c.to + grow);
  } else if (act === "pan-") {
    var d1 = Math.min(c.from, Math.max(1, Math.round(span * 0.3)));
    c.from -= d1; c.to -= d1;
  } else if (act === "pan+") {
    var d2 = Math.min(n - c.to, Math.max(1, Math.round(span * 0.3)));
    c.from += d2; c.to += d2;
  }
  drawChart(c);
}

function selectPattern(card, p, skipScroll) {
  selected = { card: card, pattern: p };
  cards.forEach(function (c) { c.sec.classList.remove("flash"); });
  card.sec.classList.add("flash");
  var n = card.day.bars.length;
  if (!document.getElementById("f-nozoom").checked) {
    var tr = card.day.trade;
    var s = p ? card.index[p.start] : (tr.entry_x === null ? 0 : Math.floor(tr.entry_x));
    var e = p ? card.index[p.end] : (tr.exit_x === null ? n : Math.ceil(tr.exit_x));
    if (tr.exit_x !== null) e = Math.max(e, Math.ceil(tr.exit_x));
    card.from = Math.max(0, s - 10);
    card.to = Math.min(n, e + 8);
  }
  drawChart(card);
  if (!skipScroll) card.sec.scrollIntoView({ behavior: "smooth", block: "center" });
  setTimeout(function () { card.sec.classList.remove("flash"); }, 2500);
}

/* ----------------------------------------------------------------- table */

function buildRows() {
  DATA.trades.forEach(function (day, di) {
    var tr = day.trade;
    rows.push({
      t: tr.entry_ts, date: day.date, symbol: day.symbol, setup: tr.setup,
      pat: tr.v3_tags ? tr.v3_tags.split("|").map(nice).join(", ") : "—",
      ord: tr.pullback_ord === null ? -1 : tr.pullback_ord,
      size: tr.strength === undefined ? null : tr.strength,
      bucket: tr.bucket, reason: tr.exit_reason, gross: tr.gross_pct,
      net: tr.net_real_inr, dayIdx: di,
      /* jump to the formation closest to the fill, not just the day's first */
      firstPattern: day.patterns.filter(function (p) { return p.pre; })
        .sort(function (a, b) { return a.bars_ago - b.bars_ago; })[0] || null
    });
  });
}

function renderTable() {
  var tbody = document.querySelector("#det tbody");
  var keep = rows.filter(tradePasses);
  keep.sort(function (a, b) {
    var x = a[sortKey], y = b[sortKey];
    if (x === null || x === undefined || (typeof x === "number" && isNaN(x))) x = -Infinity;
    if (y === null || y === undefined || (typeof y === "number" && isNaN(y))) y = -Infinity;
    return (x < y ? -1 : x > y ? 1 : 0) * sortDir;
  });
  tbody.innerHTML = "";
  var pnl = 0;
  keep.forEach(function (r) {
    pnl += r.net;
    var tr = document.createElement("tr");
    tr.className = r.bucket;
    tr.innerHTML =
      "<td>" + r.date.slice(5) + " " + hhmm(r.t) + "</td>" +
      "<td>" + r.symbol + "</td>" +
      "<td>" + nice(r.setup) + "</td>" +
      '<td class="num">' + (r.ord > 0 ? r.ord : "–") + "</td>" +
      '<td class="dir-' + r.bucket + '">' + r.pat + "</td>" +
      '<td class="num">' + (r.size === null ? "–" : r.size.toFixed(2) + "×") + "</td>" +
      "<td>" + nice(r.reason) + "</td>" +
      '<td class="num ' + cls(r.gross) + '">' + pct(r.gross) + "</td>" +
      '<td class="num ' + cls(r.net) + '">' + inr(r.net) + "</td>";
    tr.onclick = function () {
      var act = document.querySelectorAll("#det tr.active");
      for (var i = 0; i < act.length; i++) act[i].classList.remove("active");
      tr.classList.add("active");
      selectPattern(cards[r.dayIdx], r.firstPattern);
    };
    tbody.appendChild(tr);
  });
  document.getElementById("det-count").textContent =
    keep.length + " of " + rows.length + " charted trades shown · " + inr(pnl) +
    " net at real costs. Click a row to jump to that minute in its chart.";
}

function applyAll() { renderTable(); cards.forEach(drawChart); }

/* ------------------------------------------------------------------ init */

function init() {
  var sel = document.getElementById("f-pat"), names = {};
  DATA.trades.forEach(function (d) {
    d.patterns.forEach(function (p) { names[p.name] = (names[p.name] || 0) + 1; });
  });
  Object.keys(names).sort().forEach(function (n) {
    var o = document.createElement("option");
    o.value = n; o.textContent = nice(n) + " (" + names[n] + ")";
    sel.appendChild(o);
  });
  buildRows();
  buildCards();
  ["f-dir", "f-pat", "f-bucket", "f-out", "f-pre", "f-ord", "f-size"].forEach(function (id) {
    var node = document.getElementById(id);
    if (node) node.onchange = applyAll;
  });
  document.querySelectorAll("#det th").forEach(function (th) {
    th.onclick = function () {
      var k = th.getAttribute("data-k");
      if (!k) return;
      if (sortKey === k) sortDir *= -1; else { sortKey = k; sortDir = 1; }
      renderTable();
    };
  });
  renderTable();
}
init();
