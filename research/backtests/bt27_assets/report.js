/* BT27 visual pattern-audit report.
   Self-contained: no CDN, no framework, works from file://.
   DATA is injected by research/backtests/bt27_pattern_visual_report.py. */
"use strict";

var SVGNS = "http://www.w3.org/2000/svg";
var UP = "#2dd4bf", DN = "#fb7185";
var charts = document.getElementById("charts");
var tip = document.getElementById("tip");
var cards = [];   // one entry per day: {sec, svg, day, from, to}
var rows = [];    // one entry per detection
var sortKey = "t", sortDir = 1, selected = null;

function el(tag, attrs, parent, text) {
  var n = document.createElementNS(SVGNS, tag);
  for (var k in attrs) n.setAttribute(k, attrs[k]);
  if (text !== undefined) n.textContent = text;
  if (parent) parent.appendChild(n);
  return n;
}
function pct(x) {
  if (x === null || x === undefined || isNaN(x)) return "–";
  return (x >= 0 ? "+" : "") + (100 * x).toFixed(2) + "%";
}
function cls(x) { return x > 0 ? "pos" : x < 0 ? "neg" : ""; }
function nice(name) { return name.split("_").join(" "); }

/* ---------------------------------------------------------------- chart */

function drawChart(card) {
  var day = card.day, svg = card.svg;
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  var all = day.bars;
  var from = Math.max(0, Math.min(card.from, all.length - 3));
  var to = Math.min(all.length, Math.max(card.to, from + 3));
  var bars = all.slice(from, to);
  if (!bars.length) return;
  var W = 1000, H = 330, L = 58, R = 54, T = 42, B = 24;
  var lo = Infinity, hi = -Infinity, i;
  for (i = 0; i < bars.length; i++) {
    if (bars[i].l < lo) lo = bars[i].l;
    if (bars[i].h > hi) hi = bars[i].h;
  }
  var pad = (hi - lo) * 0.14 || 1;
  lo -= pad; hi += pad;
  var step = (W - L - R) / bars.length;
  function X(gi) { return L + (gi - from + 0.5) * step; }
  function Y(v) { return T + (1 - (v - lo) / (hi - lo)) * (H - T - B); }
  var cw = Math.max(2, Math.min(16, step * 0.66));

  for (i = 0; i <= 4; i++) {
    var yv = lo + (hi - lo) * i / 4;
    el("line", { x1: L, x2: W - R, y1: Y(yv), y2: Y(yv), stroke: "#fff",
      "stroke-opacity": ".07" }, svg);
    el("text", { x: L - 6, y: Y(yv) + 3, fill: "#8fa1bb", "font-size": "9.5",
      "text-anchor": "end" }, svg, "₹" + yv.toFixed(1));
  }
  var every = Math.max(1, Math.ceil(bars.length / 9));
  bars.forEach(function (b, k) {
    var gi = from + k, col = b.c >= b.o ? UP : DN;
    el("line", { x1: X(gi), x2: X(gi), y1: Y(b.h), y2: Y(b.l), stroke: col,
      "stroke-width": "1" }, svg);
    el("rect", { x: X(gi) - cw / 2, y: Y(Math.max(b.o, b.c)), width: cw,
      height: Math.max(1.4, Y(Math.min(b.o, b.c)) - Y(Math.max(b.o, b.c))),
      fill: col }, svg);
    if (k % every === 0) {
      el("text", { x: X(gi), y: H - 7, "text-anchor": "middle", fill: "#8fa1bb",
        "font-size": "9.5" }, svg, b.label);
    }
  });

  // pattern overlays, stacked so labels never collide
  var idx = card.index, lanes = [];
  var pool = day.patterns.slice();
  if (showMisses()) pool = pool.concat(day.misses || []);
  var vis = pool.filter(function (p) {
    var s = idx[p.start], e = idx[p.end];
    return s !== undefined && e !== undefined && e >= from && s < to && passes(p);
  });
  vis.forEach(function (p) {
    var s = Math.max(idx[p.start], from), e = Math.min(idx[p.end], to - 1);
    var x1 = X(s) - cw * 0.9, x2 = X(e) + cw * 0.9;
    var label = (p.kind === "near_miss" ? "? " : "") + nice(p.name) + " " + day.tf +
      (p.strength === undefined ? "" : " \u00b7 " + p.strength.toFixed(2) + "\u00d7");
    var wpx = label.length * 4.9;
    var cx = (x1 + x2) / 2;
    var lx1 = Math.min(Math.max(cx - wpx / 2, L), W - R - wpx), lx2 = lx1 + wpx;
    var lane = 0;
    while (lanes[lane] !== undefined && lanes[lane] > lx1 - 5) lane++;
    lanes[lane] = lx2;
    var g = el("g", { "data-s": p.start, "data-e": p.end,
      opacity: p.weak ? 0.42 : 1,
      "class": "pat " + (p.direction || "neutral") +
        (p.kind === "near_miss" ? " miss" : "") + (p.weak ? " weak" : "") }, svg);
    el("rect", { x: x1, y: T, width: Math.max(3, x2 - x1), height: H - T - B,
      "class": "pat-box" }, g);
    var ly = T - 30 + lane * 10;
    el("path", { d: "M " + x1 + " " + (ly + 9) + " V " + (ly + 3) + " H " + x2 +
      " V " + (ly + 9), "class": "pat-bracket" }, g);
    el("text", { x: cx, y: ly, "text-anchor": "middle", "class": "pat-label" }, g,
      label);
    var body = tipText(p, day);
    g.addEventListener("mousemove", function (ev) {
      tip.style.display = "block";
      tip.style.left = Math.min(ev.clientX + 14, window.innerWidth - 340) + "px";
      tip.style.top = (ev.clientY + 14) + "px";
      tip.textContent = body;
    });
    g.addEventListener("mouseleave", function () { tip.style.display = "none"; });
    g.addEventListener("click", function () { selectPattern(card, p, true); });
  });
  // label lanes grow upward from T-30; make sure T leaves room
  if (lanes.length > 4) svg.setAttribute("viewBox", "0 -" + ((lanes.length - 4) * 10) +
    " " + W + " " + (H + (lanes.length - 4) * 10));
  else svg.setAttribute("viewBox", "0 0 " + W + " " + H);

  if (selected && selected.card === card) {
    var p2 = selected.pattern;
    var gsel = svg.querySelector('g[data-s="' + p2.start + '"][data-e="' + p2.end + '"]');
    if (gsel) {
      gsel.classList.add("sel");
      [["confirmation", "#fbbf24"], ["invalidation", "#94a3b8"]].forEach(function (pair) {
        var v = p2[pair[0]];
        if (!v || v < lo || v > hi) return;
        el("line", { x1: L, x2: W - R, y1: Y(v), y2: Y(v), stroke: pair[1],
          "stroke-width": "1", "stroke-dasharray": "4 3", "stroke-opacity": ".8" }, svg);
        el("text", { x: W - R + 4, y: Y(v) + 3, fill: pair[1], "font-size": "9" }, svg,
          pair[0].slice(0, 5) + " ₹" + v.toFixed(1));
      });
    }
  }
}

function tipText(p, day) {
  if (p.kind === "near_miss") {
    return "NEAR MISS - " + nice(p.name).toUpperCase() + "\n" +
      day.symbol + "  " + day.date + "\n" +
      "candles " + p.start.slice(11, 16) + " → " + p.end.slice(11, 16) + " look like this\n" +
      "shape, but the detector did NOT label them.\nwhy: " + p.reason;
  }
  return nice(p.name).toUpperCase() + "  (" + p.direction + " / " + p.kind + ")\n" +
    day.symbol + "  " + day.date + "\n" +
    "formation " + p.start.slice(11, 16) + " → " + p.end.slice(11, 16) +
    " (" + day.tf + ", closes " + p.formed_at.slice(11, 16) + ")\n" +
    "prior trend: " + p.prior_trend + "\n" +
    (p.strength === undefined ? "" :
      "strength: " + p.strength.toFixed(2) + "x the recent average range" +
      (p.weak ? "  <- correctly named, too small to act on\n" : "\n")) +
    "confirmation ₹" + p.confirmation.toFixed(2) +
    "   invalidation ₹" + p.invalidation.toFixed(2) + "\n" +
    "next-bar open ₹" + (p.entry ? p.entry.toFixed(2) : "?") +
    "   +15m " + pct(p.r3) + "   +30m " + pct(p.r6) + "   EOD " + pct(p.r_eod);
}

/* ------------------------------------------------------------- filtering */

function showMisses() {
  var box = document.getElementById("f-miss");
  return box ? box.checked : false;
}
function passes(p) {
  if (p.kind === "near_miss" && !showMisses()) return false;
  var d = document.getElementById("f-dir").value;
  var n = document.getElementById("f-pat").value;
  if (d === "directional") {
    if (p.direction === "neutral") return false;
  } else if (d !== "all" && p.direction !== d) {
    return false;
  }
  if (n !== "all" && p.name !== n) return false;
  return true;
}

/* ----------------------------------------------------------------- cards */

function buildCards() {
  DATA.days.forEach(function (day, di) {
    var sec = document.createElement("section");
    sec.className = "card"; sec.id = "day-" + di;
    var head = document.createElement("div");
    head.className = "card-head";
    var left = document.createElement("div");
    left.innerHTML = "<h2>" + day.symbol + " · " + day.date + "</h2>" +
      '<div class="meta">' + day.bars.length + " " + day.tf + " candles · " +
      day.patterns.length + " formations detected</div>";
    var zoom = document.createElement("div");
    zoom.className = "zoom";
    [["←", "pan-"], ["−", "out"], ["+", "in"], ["→", "pan+"],
     ["reset", "reset"]].forEach(function (b) {
      var btn = document.createElement("button");
      btn.textContent = b[0];
      btn.onclick = (function (act, c) { return function () { zoomCard(c, act); }; })(b[1], di);
      zoom.appendChild(btn);
    });
    head.appendChild(left); head.appendChild(zoom);
    sec.appendChild(head);
    var svg = el("svg", { viewBox: "0 0 1000 330", "class": "chart" });
    sec.appendChild(svg);
    charts.appendChild(sec);
    var index = {};
    day.bars.forEach(function (b, i) { index[b.t] = i; });
    var card = { sec: sec, svg: svg, day: day, index: index, from: 0, to: day.bars.length };
    cards.push(card);
    if (!day.bars.length) {
      var d0 = document.createElement("div");
      d0.className = "empty";
      d0.textContent = "No complete 5-minute bars for this session.";
      sec.appendChild(d0);
    } else {
      drawChart(card);
    }
  });
}

function zoomCard(di, act) {
  var c = cards[di], n = c.day.bars.length, span = c.to - c.from;
  if (act === "reset") { c.from = 0; c.to = n; }
  else if (act === "in") {
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
  var s = card.index[p.start], e = card.index[p.end], n = card.day.bars.length;
  if (s !== undefined && !document.getElementById("f-nozoom").checked) {
    var padBars = 12;
    card.from = Math.max(0, s - padBars);
    card.to = Math.min(n, e + padBars + 1);
  }
  drawChart(card);
  if (!skipScroll) card.sec.scrollIntoView({ behavior: "smooth", block: "center" });
  setTimeout(function () { card.sec.classList.remove("flash"); }, 2500);
}

/* ----------------------------------------------------------------- table */

function buildRows() {
  DATA.days.forEach(function (day, di) {
    day.patterns.concat(day.misses || []).forEach(function (p) {
      rows.push({ t: p.end, name: p.name, direction: p.direction || "neutral",
        prior: p.prior_trend || "?", dayIdx: di, p: p, symbol: day.symbol,
        date: day.date, r3: p.r3, r6: p.r6, reod: p.r_eod });
    });
  });
}

function renderTable() {
  var tbody = document.querySelector("#det tbody");
  var keep = rows.filter(function (r) { return passes(r.p); });
  keep.sort(function (a, b) {
    var x = a[sortKey], y = b[sortKey];
    if (x === null || x === undefined || (typeof x === "number" && isNaN(x))) x = -Infinity;
    if (y === null || y === undefined || (typeof y === "number" && isNaN(y))) y = -Infinity;
    return (x < y ? -1 : x > y ? 1 : 0) * sortDir;
  });
  tbody.innerHTML = "";
  keep.forEach(function (r) {
    var tr = document.createElement("tr");
    tr.className = r.direction + (r.p.kind === "near_miss" ? " miss" : "");
    if (r.p.kind === "near_miss") tr.title = "near miss - " + r.p.reason;
    tr.innerHTML =
      "<td>" + r.date.slice(5) + " " + r.t.slice(11, 16) + "</td>" +
      "<td>" + r.symbol + "</td>" +
      "<td>" + (r.p.kind === "near_miss" ? "? " : "") + nice(r.name) + "</td>" +
      '<td class="dir-' + r.direction + '">' + r.direction.slice(0, 4) + "</td>" +
      "<td>" + r.prior + "</td>" +
      '<td class="num ' + cls(r.r3) + '">' + pct(r.r3) + "</td>" +
      '<td class="num ' + cls(r.r6) + '">' + pct(r.r6) + "</td>" +
      '<td class="num ' + cls(r.reod) + '">' + pct(r.reod) + "</td>";
    tr.onclick = function () {
      var act = document.querySelectorAll("#det tr.active");
      for (var i = 0; i < act.length; i++) act[i].classList.remove("active");
      tr.classList.add("active");
      selectPattern(cards[r.dayIdx], r.p);
    };
    tbody.appendChild(tr);
  });
  document.getElementById("det-count").textContent =
    keep.length + " of " + rows.length + " detections shown";
}

function applyAll() {
  renderTable();
  cards.forEach(drawChart);
}

/* ------------------------------------------------------------------ init */

function init() {
  var sel = document.getElementById("f-pat");
  var names = {};
  DATA.days.forEach(function (d) {
    d.patterns.concat(d.misses || []).forEach(function (p) {
      names[p.name] = (names[p.name] || 0) + 1;
    });
  });
  Object.keys(names).sort().forEach(function (n) {
    var o = document.createElement("option");
    o.value = n; o.textContent = nice(n) + " (" + names[n] + ")";
    sel.appendChild(o);
  });
  buildRows();
  buildCards();
  ["f-dir", "f-pat", "f-miss"].forEach(function (id) {
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
