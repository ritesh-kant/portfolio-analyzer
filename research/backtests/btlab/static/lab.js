"use strict";
/* Backtest lab UI. No build step: plain DOM, one file. Talks to /api/lab/* (btlab/server.py).
 *
 * Layout: three tabs (Build experiment · Result · Saved runs), switched by showTab() and kept in the URL hash.
 *   Build   data window → base trade → indicators (grouped by CATEGORIES), with a sticky "Your experiment"
 *           panel (renderRecipe) that spells out what will run and checks it before Run (recipeChecks).
 *   Result  one run (renderResult: headline verdict, KPIs vs base, details), Compare, Test-each-alone.
 *   Runs    the saved-run table (renderHistory).
 */

const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let GLYPH = "₹";                      // currency of the market being shown (₹ NSE, $ US)
const G = () => GLYPH;
const inr = (x, d = 0) => (x == null ? "–" : (x < 0 ? "−" : "") + GLYPH + Math.abs(x).toLocaleString(GLYPH === "$" ? "en-US" : "en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
const sgn = (x, f) => (x == null ? "–" : (x > 0 ? "+" : x < 0 ? "−" : "") + f(Math.abs(x)));
const pct = (x, d = 2) => (x == null ? "–" : x.toFixed(d) + "%");
const tone = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : "dim");
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const SEL_KEY = "btlab.state.v1";
const SCAN_KEY = "btlab.scan.open.v1";
function lsGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } }

const MARKET_DEFAULTS = {
  NSE: { glyph: "₹", risk: 500, notional: 50000, presets: { strict: { day_chg_min: 4, day_chg_max: 8, rvol_min: 3 }, wide: { day_chg_min: 3, day_chg_max: 15, rvol_min: 2 } } },
  US: { glyph: "$", risk: 50, notional: 5000, presets: { strict: { day_chg_min: 10, day_chg_max: 50, rvol_min: 3 }, wide: { day_chg_min: 5, day_chg_max: 100, rvol_min: 2 } } },
};
const presets = () => MARKET_DEFAULTS[S.market].presets;
// the base settings that are not the momentum band: kept when a preset replaces the band. A new lab opens on the nine patterns + doji / spinning top trigger;
// triggerOf() below still reads a missing entry_mode as "pattern", which is what every run saved before the option existed used.
const TRIGGER_DEFAULTS = { pattern_tf: "5m", entry_mode: "hybrid", stop_levels: "all" };
const triggerOf = (b) => ({ pattern_tf: b?.pattern_tf || "5m", entry_mode: b?.entry_mode || "pattern", stop_levels: b?.stop_levels || "all" });
const TF_NAME = { "5m": "5-minute", "1m": "1-minute" };
const EOD = { NSE: "15:15", US: "15:59 ET" };            // the last bar a trade can stay open to

/* Indicator categories, for the grouped list. An id not listed here lands in "Other", so a new Plugin(...) still shows. */
const CATEGORIES = {
  entry: [
    ["News", ["news", "market_reaction", "news_pullback"]],
    ["Momentum & time", ["time_window", "momentum_tighter", "atr_band", "pullback_ordinal"]],
    ["Trend & moving averages", ["ema9_over_ema20", "close_over_ema9", "above_ema200", "daily_uptrend", "uptrend_f1", "chart_quality_f2"]],
    ["VWAP", ["above_vwap", "vwap_cross"]],
    ["MACD", ["macd5_positive", "macd1_open"]],
    ["Volume", ["volume_surge", "surge_f3", "rising_price_volume"]],
    ["Order flow", ["cvd_divergence", "delta_divergence"]],
    ["Pattern", ["pattern_strength"]],
    ["Risk & levels", ["min_reward_risk", "max_stop_distance", "support_rule", "resistance_rule", "away_from_round"]],
  ],
  exit: [
    ["Target", ["target_fixed_rr", "checkpoint_stop", "tap_high_ceiling"]],
    ["Stop management", ["breakeven", "swing_trail"]],
    ["Indicator exits", ["vwap_exit", "macd_fade_exit", "volume_climax_exit", "resistance_reject_exit", "stale_exit"]],
  ],
};
const NEEDS_NEWS = new Set(["market_reaction", "news_pullback"]);
const NEWS_IDS = ["news_reaction", "news", "market_reaction"];   // indicators that read the filings cache

const S = {
  market: "NSE", byMarket: {},
  meta: null, plugins: [], runs: [], sel: {}, years: new Set(), patterns: new Set(),
  base: { ...MARKET_DEFAULTS.NSE.presets.strict, ...TRIGGER_DEFAULTS }, risk: 500, notional: 50000, maxTrades: 0, maxPositions: 0,
  current: null, compare: new Set(), shown: [], sort: { k: "created_at", dir: -1 }, busy: false,
  scanOpen: lsGet(SCAN_KEY) === "1",
  tab: "build", indGroup: "entry", indCat: "", onlySel: false,
};
const plug = (id) => S.plugins.find((p) => p.id === id);

/* ---------------------------------------------------------------- api */
async function api(path, method = "GET", body) {
  const res = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  let data = null;
  try { data = await res.json(); } catch { /* not json */ }
  return { ok: res.ok, status: res.status, data };
}

function banner(msg, kind = "", progress = null, cancel = null) {
  const b = $("#banner");
  if (!msg) { b.hidden = true; return; }
  b.hidden = false;
  b.className = kind;
  b.innerHTML = (cancel ? `<button type="button" class="mini" id="banner-cancel" style="float:right">Cancel</button>` : "") + esc(msg)
    + (progress != null ? `<div class="bar2"><i style="width:${Math.round(progress * 100)}%"></i></div>` : "");
  if (cancel) $("#banner-cancel").onclick = (e) => { e.target.disabled = true; e.target.textContent = "Cancelling…"; cancel(); };
}

/* ---------------------------------------------------------------- tabs */
const TABS = ["build", "result", "runs"];
function showTab(t, { focus = false, push = true } = {}) {
  if (t.startsWith("run/")) { openRunFromHash(t.slice(4)); return; }      // #run/<id>: the trade-chart page's back link
  if (!TABS.includes(t)) t = "build";
  S.tab = t;
  for (const x of TABS) {
    const on = x === t;
    $(`#tab-${x}`).setAttribute("aria-selected", String(on));
    $(`#tab-${x}`).tabIndex = on ? 0 : -1;
    $(`#panel-${x}`).hidden = !on;
  }
  if (push && location.hash !== "#" + t) history.replaceState(null, "", "#" + t);
  if (focus) $(`#tab-${t}`).focus();
}
function wireTabs() {
  $(".tabs").addEventListener("click", (e) => { const b = e.target.closest("[data-tab]"); if (b) showTab(b.dataset.tab); });
  $(".tabs").addEventListener("keydown", (e) => {          // arrow keys move between tabs (WAI-ARIA tabs pattern)
    if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
    const i = TABS.indexOf(S.tab), j = (i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length;
    showTab(TABS[j], { focus: true });
  });
  document.addEventListener("click", (e) => { const a = e.target.closest("[data-goto]"); if (a) { e.preventDefault(); showTab(a.dataset.goto); } });
  window.addEventListener("hashchange", () => showTab(location.hash.slice(1), { push: false }));
  $("#btn-help").addEventListener("click", (e) => {
    const h = $("#help"); h.hidden = !h.hidden; e.currentTarget.setAttribute("aria-expanded", String(!h.hidden));
    lsSet("btlab.help", h.hidden ? "0" : "1");
  });
}

let pendingRun = null;
function openRunFromHash(id) {
  if (!S.meta) { pendingRun = id; showTab("result", { push: false }); return; }   // boot opens it once the page is ready
  showRun(id);
}

/* ---------------------------------------------------------------- state persistence */
function stash() {
  S.byMarket[S.market] = { years: [...S.years], base: S.base, risk: S.risk, notional: S.notional,
    maxTrades: S.maxTrades, maxPositions: S.maxPositions, patterns: [...S.patterns], sel: S.sel };
}
function save() {
  stash();
  lsSet(SEL_KEY, JSON.stringify({ market: S.market, byMarket: S.byMarket }));
}
function useMarket(m) {
  // the form values this market last had, or its own defaults
  S.market = m; GLYPH = MARKET_DEFAULTS[m].glyph;
  const d = S.byMarket[m] || {};
  S.years = new Set(d.years || []); S.base = { ...MARKET_DEFAULTS[m].presets.strict, ...TRIGGER_DEFAULTS, ...(d.base || {}) };
  S.risk = d.risk ?? MARKET_DEFAULTS[m].risk; S.notional = d.notional ?? MARKET_DEFAULTS[m].notional; S.maxTrades = d.maxTrades ?? 0; S.maxPositions = d.maxPositions ?? 0;
  S.patterns = new Set(d.patterns || []); S.sel = { ...(d.sel || {}) };
}
function restore() {
  try {
    const o = JSON.parse(lsGet(SEL_KEY) || "null");
    if (!o) return;
    S.byMarket = o.byMarket || { NSE: { years: o.years, base: o.base, risk: o.risk, notional: o.notional } };   // v1 had NSE only
    const market = MARKET_DEFAULTS[o.market] ? o.market : "NSE";
    // Older saved state kept indicators and patterns globally. Keep them on the last active market.
    if (o.sel || o.patterns) {
      S.byMarket[market] ||= {};
      S.byMarket[market].sel ??= o.sel || {};
      S.byMarket[market].patterns ??= o.patterns || [];
    }
    useMarket(market);
  } catch { /* ignore */ }
}
function paintGlyph() {
  document.querySelectorAll(".g").forEach((e) => { e.textContent = GLYPH; });
  $("#mkt").innerHTML = S.meta.markets.map((m) => `<button type="button" aria-pressed="${m.id === S.market}" class="chip ${m.id === S.market ? "on" : ""}" data-m="${m.id}" title="${esc(m.label)}">${esc(m.id)} <small>${esc(m.glyph)}</small></button>`).join("");
  document.querySelectorAll("[data-preset]").forEach((b) => {
    const p = presets()[b.dataset.preset];
    b.textContent = `${b.dataset.preset === "strict" ? "Strict" : "Wider"} ${p.day_chg_min}–${p.day_chg_max}% · RVOL ${p.rvol_min}`;
  });
}

async function switchMarket(m) {
  if (m === S.market || S.busy) return;
  const r = await api(`/api/lab/meta?market=${m}`);
  if (!r.ok) { banner("Could not load the " + m + " data: " + (r.data?.error || r.status), "err"); return; }
  save(); useMarket(m);
  S.meta = r.data;
  const have = new Set(r.data.years.map((y) => y.year));
  S.years = new Set([...S.years].filter((y) => have.has(y)));
  if (!S.years.size && r.data.years.length) S.years.add(r.data.years[r.data.years.length - 1].year);
  if (!S.patterns.size) S.patterns = new Set(r.data.patterns);
  S.patterns = new Set([...S.patterns].filter((p) => r.data.patterns.includes(p)));
  S.current = null; S.compare.clear(); S.shown = [];
  emptyResult(); $("#scan-card").hidden = true; $("#compare-card").hidden = true;
  renderBase(); renderYears(); renderPatterns(); renderPlugins(); paintGlyph(); save();
  await loadRuns(); metaSoon();
}

/* ---------------------------------------------------------------- step 1: data */
function renderYears() {
  const el = $("#years");
  el.innerHTML = S.meta.years.map((y) =>
    `<button type="button" aria-pressed="${S.years.has(y.year)}" aria-label="${y.year}, ${y.built ? "base data ready" : "base data needs building"}" class="chip ${S.years.has(y.year) ? "on" : ""}" data-y="${y.year}" title="${y.built ? `${y.n_candidates} base candidates` : "needs a one-time build (~20 s)"}">` +
    `<span class="dot ${y.built ? "ready" : ""}"></span>${y.year}</button>`).join("") +
    `<button type="button" class="chip" data-all="1">${S.years.size === S.meta.years.length ? "Clear" : "All years"}</button>`;
  const need = S.meta.years.filter((y) => S.years.has(y.year) && !y.built).map((y) => y.year);
  $("#years-hint").innerHTML = need.length
    ? `<span class="dot-k">○</span> ${need.join(", ")} need a one-time build of the base data for this base setting (≈20–70 s each). Run does it for you.`
    : `<span class="pos">●</span> = base data ready for this base setting. Pick one year or several; results are pooled.`;
  renderRecipe();
}

/* ---------------------------------------------------------------- step 2: base trade */
function renderPatterns() {
  $("#patterns").innerHTML = S.meta.patterns.map((p) => `<button type="button" aria-pressed="${S.patterns.has(p)}" class="chip ${S.patterns.has(p) ? "on" : ""}" data-pat="${p}">${p.replace(/_/g, " ")}</button>`).join("");
}

function renderBase() {
  renderBaseOverview();
  $("#base-text").innerHTML = S.meta.base_rule_text.map((t) => `<li>${esc(t)}</li>`).join("");
  $("#b-chg-min").value = S.base.day_chg_min; $("#b-chg-max").value = S.base.day_chg_max; $("#b-rvol").value = S.base.rvol_min;
  $("#b-tf").value = triggerOf(S.base).pattern_tf; $("#b-mode").value = triggerOf(S.base).entry_mode; $("#b-stops").value = triggerOf(S.base).stop_levels;
  paintTrigger();
  $("#b-maxtr").value = S.maxTrades; $("#b-maxpos").value = S.maxPositions; $("#b-risk").value = S.risk; $("#b-notional").value = S.notional;
}

/* What differs from this market's default base (for the "changed" markers and the recipe). */
function baseChanges() {
  const d = { ...presets().strict, ...TRIGGER_DEFAULTS }, md = MARKET_DEFAULTS[S.market], t = triggerOf(S.base), out = {};
  if (S.base.day_chg_min !== d.day_chg_min || S.base.day_chg_max !== d.day_chg_max || S.base.rvol_min !== d.rvol_min) out.stocks = 1;
  if (t.pattern_tf !== d.pattern_tf || t.entry_mode !== d.entry_mode || (S.meta && S.patterns.size !== S.meta.patterns.length)) out.trigger = 1;
  if (t.stop_levels !== d.stop_levels) out.stop = 1;
  if (S.risk !== md.risk || S.notional !== md.notional || S.maxTrades || S.maxPositions) out.size = 1;
  return out;
}

function renderBaseOverview() {
  const t = triggerOf(S.base), ch = baseChanges(), n = S.patterns.size, all = S.meta?.patterns.length;
  const trig = t.entry_mode === "market" ? `No pattern: market buy at the next minute's open when an entry indicator agrees`
    : `${t.entry_mode === "hybrid" ? "Bullish pattern or doji / spinning top after a dip" : "Bullish candlestick pattern"} on a ${TF_NAME[t.pattern_tf]} candle (${n === all ? `all ${n}` : `${n} of ${all}`} patterns); buy-stop just over its high`;
  const cells = [
    ["stocks", "Stocks", `Up ${S.base.day_chg_min}–${S.base.day_chg_max}% on the day, RVOL ≥ ${S.base.rvol_min}`],
    ["trigger", "Entry", trig],
    ["stop", "Stop", t.stop_levels === "local" ? "Under today's own nearest support" : "Under the nearest structural support (0.3–3% away)"],
    ["size", "Target · size", `Under the nearest resistance, or the close at ${EOD[S.market]} · risk ${inr(S.risk)} per trade, ≤ ${inr(S.notional)}${S.maxTrades ? ` · ${S.maxTrades}/stock/day` : ""}${S.maxPositions ? ` · ≤ ${S.maxPositions} open` : ""}`],
  ];
  $("#base-flow").innerHTML = cells.map(([k, h, v]) => `<div class="${ch[k] ? "changed" : ""}" title="${ch[k] ? "Changed from the default" : ""}"><b>${h}${ch[k] ? " · changed" : ""}</b>${esc(v)}</div>`).join("");
  const nCh = Object.keys(ch).length;
  $("#base-changed").textContent = nCh ? `· ${plural(nCh, "group")} changed from default` : "· all defaults";
}
/* The news-bar trigger replaces the candlestick, so the base runs without one (the server switches it too). */
function useNewsBarBase() {
  if (S.sel.news?.trigger !== "news_bar" || triggerOf(S.base).entry_mode === "market") return;
  S.base = { ...S.base, entry_mode: "market" };
  renderBase(); metaSoon();
  banner("The News indicator's news_bar trigger replaces the candlestick, so the base entry trigger was switched to “No pattern”.", "");
  setTimeout(() => banner(""), 6000);
}
/* The pattern chips mean nothing when no pattern is required, so they are dimmed rather than hidden. */
function paintTrigger() {
  const t = triggerOf(S.base), mkt = t.entry_mode === "market", hyb = t.entry_mode === "hybrid";
  $("#patterns").style.opacity = mkt ? ".4" : ""; $("#patterns").style.pointerEvents = mkt ? "none" : "";
  $("#patterns-title").textContent = hyb ? "Candlestick patterns that can trigger a trade (doji / spinning tops after a downtrend are always added)" : mkt ? "Candlestick patterns (not used: no pattern trigger)" : "Candlestick patterns that can trigger a trade";
  $("#b-mode-hint").textContent = hyb
    ? `The ticked patterns, plus a doji or spinning top that completes on a ${TF_NAME[t.pattern_tf]} bar after the five candles before it fell. Both are entered with a buy-stop one tick over the high, cancelled if price trades under the low first. Untick pattern chips to narrow which patterns count; the doji / spinning-top trades are always included. Measured 2022-26: gross about 0% per trade.`
    : mkt
    ? `No price lines from a pattern. Every closed ${TF_NAME[t.pattern_tf]} bar that passes the stock screen is a candidate; an entry indicator (e.g. News + Market reaction) decides which, and the order is a market buy at the next minute's open. Tip: set max entries per stock per day to 1, or it re-enters after every exit.`
    : t.pattern_tf === "1m"
      ? "1-minute candles trigger earlier but are noisier. Indicators and levels are read from the last 5-minute bar that had closed."
      : "";
}

function readBase() {
  const pz = presets().strict;
  S.base = { day_chg_min: +$("#b-chg-min").value || pz.day_chg_min, day_chg_max: +$("#b-chg-max").value || pz.day_chg_max, rvol_min: +$("#b-rvol").value || 0,
    pattern_tf: $("#b-tf").value || "5m", entry_mode: $("#b-mode").value || "pattern", stop_levels: $("#b-stops").value || "all" };
  paintTrigger();
  S.maxTrades = Math.max(0, +$("#b-maxtr").value || 0); S.maxPositions = Math.max(0, +$("#b-maxpos").value || 0); S.risk = +$("#b-risk").value || MARKET_DEFAULTS[S.market].risk; S.notional = +$("#b-notional").value || MARKET_DEFAULTS[S.market].notional;
  renderBaseOverview(); renderRecipe(); save();
}

let metaTimer = null;
function metaSoon() {
  clearTimeout(metaTimer);
  metaTimer = setTimeout(async () => {
    const r = await api("/api/lab/meta", "POST", { market: S.market, base: S.base, years: S.meta.years.slice(0, 1).map((y) => y.year) });
    if (r.ok) { S.meta.years = r.data.years; S.meta.base_rule_text = r.data.base_rule_text; renderYears(); $("#base-text").innerHTML = S.meta.base_rule_text.map((t) => `<li>${esc(t)}</li>`).join(""); }
  }, 350);
}

/* ---------------------------------------------------------------- step 3: indicators */
/* A to-scale sketch of the Support / Resistance rule: the shaded band is where the level must sit. */
function srDiagram(id, v) {
  const sup = id === "support_rule";
  const lo = Math.max(0, +v.min_pct || 0), hi = Math.max(lo, +v.max_pct || 0);
  const Y0 = 80, PX = 11, CAP = 70;                       // fill line, px per %, max px from the fill
  const dy = (pct) => Math.min(CAP, pct * PX) * (sup ? 1 : -1);
  const yA = Y0 + dy(lo), yB = Y0 + dy(hi);
  const col = sup ? "#38bdf8" : "#f472b6";
  const top = Math.min(yA, yB), h = Math.abs(yB - yA);
  const candle = (x, o, c, hh, ll) => {
    const up = c < o, k = up ? "#34d399" : "#fb7185";
    return `<line x1="${x}" x2="${x}" y1="${hh}" y2="${ll}" stroke="${k}"/><rect x="${x - 5}" y="${Math.min(o, c)}" width="10" height="${Math.max(2, Math.abs(c - o))}" fill="${k}"/>`;
  };
  const kind = { any: "any level", swing: sup ? "swing low only" : "swing high only", not_round: "not a round number" }[v.kind] || "";
  return `<svg class="srd" viewBox="0 0 250 160" role="img" aria-label="${sup ? "Support" : "Resistance"} rule sketch">
    <rect x="0" y="${top}" width="250" height="${Math.max(h, 1)}" fill="${col}" opacity=".18"/>
    <line x1="0" x2="250" y1="${yA}" y2="${yA}" stroke="${col}" stroke-dasharray="3 3"/>
    <line x1="0" x2="250" y1="${yB}" y2="${yB}" stroke="${col}" stroke-dasharray="3 3"/>
    <line x1="0" x2="250" y1="${Y0}" y2="${Y0}" stroke="#34d399" stroke-width="1.5"/>
    ${candle(20, 70, 90, 62, 98)}${candle(40, 90, 76, 70, 96)}${candle(60, 76, 84, 70, 92)}${candle(80, 84, 66, 60, 90)}
    <text x="246" y="${Y0 - 3}" text-anchor="end" fill="#34d399" font-size="9">BUY / fill</text>
    <text x="246" y="${sup ? Math.min(yA, yB) - 3 : Math.max(yA, yB) + 11}" text-anchor="end" fill="${col}" font-size="9">${lo}%</text>
    <text x="246" y="${sup ? Math.max(yA, yB) + 11 : Math.min(yA, yB) - 3}" text-anchor="end" fill="${col}" font-size="9">${hi}%${hi * PX > CAP ? " (off scale)" : ""}</text>
    <text x="150" y="${top + h / 2 + 3}" text-anchor="middle" fill="${col}" font-size="9">level must sit here · ${esc(kind)}</text>
  </svg>`;
}

const fmtVal = (v) => (typeof v === "number" ? String(+v.toFixed(4)) : String(v));
/* "Label value" for the params of one selected indicator; onlyChanged keeps the ones that differ from the default. */
function paramText(id, params, onlyChanged = true) {
  const p = plug(id); if (!p) return Object.values(params || {}).join(" / ");
  return p.params.filter((q) => !onlyChanged || String(params?.[q.key] ?? q.default) !== String(q.default))
    .map((q) => `${q.label.replace(/\s*\(.*\)$/, "")} ${fmtVal(params?.[q.key] ?? q.default)}`).join(" · ");
}

function categoryOf(p) {
  for (const [name, ids] of CATEGORIES[p.group] || []) if (ids.includes(p.id)) return name;
  return "Other";
}

function pluginRow(p) {
  const on = p.id in S.sel;
  const diagram = (p.id === "support_rule" || p.id === "resistance_rule") && on
    ? `<div class="sr" data-sr="${p.id}">${srDiagram(p.id, S.sel[p.id])}</div>` : "";
  const params = p.params.map((q) => {
    const v = (S.sel[p.id] || {})[q.key] ?? q.default;
    let input;
    if (q.kind === "select") input = `<select data-p="${p.id}" data-k="${q.key}">${q.options.includes(v) ? "" : `<option value="${esc(v)}" selected disabled>Unsupported: ${esc(v)}</option>`}${q.options.map((o) => `<option ${o === v ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
    else if (q.kind === "time") input = `<input type="time" data-p="${p.id}" data-k="${q.key}" value="${esc(v)}">`;
    else input = `<input type="number" data-p="${p.id}" data-k="${q.key}" value="${v}" ${q.min != null ? `min="${q.min}"` : ""} ${q.max != null ? `max="${q.max}"` : ""} step="${q.step ?? (q.kind === "int" ? 1 : "any")}">`;
    return `<label>${esc(q.label)}${input}<span class="def">default ${esc(fmtVal(q.default))}</span></label>`;
  }).join("");
  const changed = on && paramText(p.id, S.sel[p.id]);
  const flags = [
    NEEDS_NEWS.has(p.id) ? `<span class="flag req" title="Needs the News indicator ticked too">needs News</span>` : "",
    p.exclusive === "target" ? `<span class="flag" title="Replaces the resistance target; only one target replacement at a time">replaces target</span>` : "",
  ].join("");
  return `<div class="plug ${on ? "on" : ""}" data-id="${p.id}">
    <label class="head"><input type="checkbox" aria-describedby="desc-${p.id}" data-id="${p.id}" ${on ? "checked" : ""}>
      <span><span class="nm">${esc(p.name)}</span>${flags}<span class="ds" id="desc-${p.id}">${esc(p.desc)}</span></span></label>
    ${p.history ? `<details class="research-note"><summary>Previous research</summary><p class="hist">${esc(p.history)}</p></details>` : ""}
    ${p.params.length ? `<div class="params">${params}${changed ? `<button type="button" class="mini reset" data-reset="${p.id}">Reset to defaults</button>` : ""}</div>` : ""}${diagram}</div>`;
}

function renderPlugins() {
  for (const g of ["entry", "exit"]) {
    const list = S.plugins.filter((p) => p.group === g);
    const cats = [...(CATEGORIES[g] || []).map(([n]) => n), "Other"];
    $(`#${g}-list`).innerHTML = cats.map((c) => {
      const order = (CATEGORIES[g].find(([n]) => n === c) || [c, []])[1];
      const items = list.filter((p) => categoryOf(p) === c).sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
      return items.length ? `<div class="cat-h" data-cat="${esc(c)}">${esc(c)}</div>` + items.map(pluginRow).join("") : "";
    }).join("") + `<p class="ind-empty" hidden>No indicators match. Clear the search or the category.</p>`;
  }
  renderSel();
  filterPlugins();
}

function renderIndGroup() {
  const g = S.indGroup;
  document.querySelectorAll(".seg [data-g]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.g === g)));
  $("#entry-list").hidden = g !== "entry"; $("#exit-list").hidden = g !== "exit";
  const cats = [...(CATEGORIES[g] || []).map(([n]) => n), "Other"].filter((c) => S.plugins.some((p) => p.group === g && categoryOf(p) === c));
  if (S.indCat && !cats.includes(S.indCat)) S.indCat = "";
  const nSel = (c) => S.plugins.filter((p) => p.group === g && categoryOf(p) === c && p.id in S.sel).length;
  $("#ind-cats").innerHTML = `<button type="button" class="chip ${S.indCat ? "" : "on"}" aria-pressed="${!S.indCat}" data-cat="">All</button>` +
    cats.map((c) => `<button type="button" class="chip ${S.indCat === c ? "on" : ""}" aria-pressed="${S.indCat === c}" data-cat="${esc(c)}">${esc(c)}${nSel(c) ? `<span class="k">· ${nSel(c)} on</span>` : ""}</button>`).join("");
  filterPlugins();
}

function filterPlugins() {
  const g = S.indGroup, q = $("#ind-search").value.trim().toLowerCase(), root = $(`#${g}-list`);
  const list = S.plugins.filter((p) => p.group === g);
  let shown = 0;
  const visCat = new Set();
  for (const p of list) {
    const cat = categoryOf(p);
    const match = !q || (p.name + " " + p.desc + " " + cat + " " + p.id).toLowerCase().includes(q);
    const visible = match && (!S.indCat || cat === S.indCat) && (!S.onlySel || p.id in S.sel);
    const el = root.querySelector(`.plug[data-id="${p.id}"]`); if (el) el.hidden = !visible;
    if (visible) { shown++; visCat.add(cat); }
  }
  root.querySelectorAll(".cat-h").forEach((h) => { h.hidden = !visCat.has(h.dataset.cat) || !!S.indCat; });
  root.querySelector(".ind-empty").hidden = shown > 0;
  $("#ind-count").textContent = `${shown} of ${list.length} ${g} indicators shown`;
}

function renderSel() {
  for (const g of ["entry", "exit"]) {
    const n = Object.keys(S.sel).filter((i) => plug(i)?.group === g).length;
    $(`#cnt-${g}`).textContent = n ? `${n} on` : "";
  }
  renderIndGroup();
  renderRecipe();
  save();
}

/* ---------------------------------------------------------------- the "Your experiment" panel */
function selectedByGroup(g) { return Object.keys(S.sel).filter((id) => plug(id)?.group === g); }

/* The exact config the Run button will send, normalised the way a saved run stores it, to spot a re-run. */
function sameConfig(c) {
  const t = triggerOf(S.base), tc = triggerOf(c.base);
  if ((c.base.market || "NSE") !== S.market) return false;
  if ([...c.years].sort().join() !== [...S.years].sort().join()) return false;
  if (c.base.day_chg_min !== S.base.day_chg_min || c.base.day_chg_max !== S.base.day_chg_max || c.base.rvol_min !== S.base.rvol_min) return false;
  if (tc.pattern_tf !== t.pattern_tf || tc.entry_mode !== t.entry_mode || tc.stop_levels !== t.stop_levels) return false;
  if (c.risk_inr !== S.risk || c.max_notional_inr !== S.notional || (c.max_trades || 0) !== S.maxTrades || (c.max_positions || 0) !== S.maxPositions) return false;
  if (t.entry_mode !== "market" && [...c.patterns].sort().join() !== [...S.patterns].sort().join()) return false;
  const norm = (arr) => JSON.stringify(arr.map(([id, prm]) => [id, Object.entries(prm || {}).map(([k, v]) => [k, String(v)]).sort()]).sort());
  const mine = Object.entries(S.sel).map(([id, prm]) => [id, Object.fromEntries((plug(id)?.params || []).map((q) => [q.key, prm?.[q.key] ?? q.default]))]);
  return norm(c.plugins.map((x) => [x.id, x.params])) === norm(mine);
}

/* What would stop the run (err), what to know first (warn / info). Mirrors the server's own refusals in plugins.py / service.py. */
function recipeChecks() {
  const out = [], ids = new Set(Object.keys(S.sel)), t = triggerOf(S.base), ent = selectedByGroup("entry");
  if (!S.years.size) out.push(["err", "Pick at least one year in step 1."]);
  for (const id of ["market_reaction", "news_pullback"]) if (ids.has(id) && !ids.has("news")) out.push(["err", `${plug(id)?.name} measures from the filing: also tick <b>News</b>. <button type="button" class="mini" data-fix-tick="news">Tick News</button>`]);
  if (t.entry_mode === "market" && !ent.length) out.push(["err", "With no pattern trigger, an entry indicator must decide when to buy (e.g. News + Market reaction)."]);
  if (t.entry_mode === "market" && ids.has("pattern_strength")) out.push(["err", "<b>Pattern candle size</b> needs a pattern trigger."]);
  if (S.base.day_chg_min > S.base.day_chg_max) out.push(["err", "Day change “from” is above “to”."]);
  const need = S.meta.years.filter((y) => S.years.has(y.year) && !y.built).map((y) => y.year);
  if (need.length) out.push(["warn", `First builds base data for ${need.join(", ")} (≈20–70 s each, once).`]);
  if (ids.has("news") || ids.has("market_reaction")) out.push(["info", `News reads ${S.market === "US" ? "SEC EDGAR" : "NSE"} filings; any not fetched yet are fetched once (it asks first).`]);
  if (t.entry_mode === "market" && !S.maxTrades && S.sel.news?.trigger !== "news_bar") out.push(["warn", "No pattern + no entry cap: it re-enters after every exit. Consider max entries per stock per day = 1."]);
  if (ent.length >= 4) out.push(["info", `${ent.length} entry filters: each one shrinks the sample. Watch the trade count.`]);
  const prior = S.runs.find((r) => sameConfig(r.config));
  const usesNews = NEWS_IDS.some((id) => ids.has(id));          // the server never re-uses a news run (service.apply): it always re-runs
  if (prior && !usesNews) out.push(["ok", `Already run (saved ${prior.created_at.replace("T", " ").slice(0, 16)}): Run opens the saved result instantly.`]);
  if (prior && usesNews) out.push(["warn", `Run before (saved ${prior.created_at.replace("T", " ").slice(0, 16)}). News experiments always re-run and count as a new trial. <button type="button" class="mini" data-act="show" data-id="${prior.id}">Open the saved one</button>`]);
  return out;
}

function renderRecipe() {
  if (!S.meta) return;
  const yrs = [...S.years].sort(), ent = selectedByGroup("entry"), ext = selectedByGroup("exit");
  const item = (id, g) => { const p = plug(id), pt = paramText(id, S.sel[id]);
    return `<li class="${g}"><span>${esc(p?.name || id)}${pt ? `<small>${esc(pt)}</small>` : p?.params.length ? `<small>defaults</small>` : ""}</span><button type="button" data-unsel="${id}" aria-label="Remove ${esc(p?.name || id)}" title="Remove">×</button></li>`; };
  const ch = Object.keys(baseChanges());
  $("#recipe-body").innerHTML = `<dl>
    <dt>Data</dt><dd>${S.market} · ${yrs.length ? (yrs.length > 2 ? `${yrs[0]}–${yrs[yrs.length - 1]} (${yrs.length} yrs)` : yrs.join(", ")) : `<span class="neg">no year picked</span>`}</dd>
    <dt>Base</dt><dd>${ch.length ? `<span class="c-warn">custom</span> (${ch.join(", ")} changed)` : "default"} · ${S.base.day_chg_min}–${S.base.day_chg_max}%, RVOL ≥ ${S.base.rvol_min}</dd>
    <dt class="c-entry">Entry</dt><dd>${ent.length ? `<ul class="sel">${ent.map((id) => item(id, "entry")).join("")}</ul>` : `<span class="dim">none — every base trade is taken</span>`}</dd>
    <dt class="c-exit">Exit</dt><dd>${ext.length ? `<ul class="sel">${ext.map((id) => item(id, "exit")).join("")}</ul>` : `<span class="dim">none — base stop / target / ${EOD[S.market]}</span>`}</dd>
  </dl>`;
  const checks = recipeChecks(), blocked = checks.some(([k]) => k === "err");
  $("#checks").innerHTML = checks.map(([k, t]) => `<li class="${k}">${t}</li>`).join("");
  $("#btn-apply").disabled = blocked || S.busy;
  $("#btn-apply").textContent = S.busy ? "Running…" : ent.length + ext.length ? "Run experiment" : "Run the base trade";
  $("#btn-scan").disabled = S.busy || triggerOf(S.base).entry_mode === "market" || !S.years.size;
  $("#btn-scan").title = triggerOf(S.base).entry_mode === "market" ? "Not available without a pattern trigger: the base trade has no entry of its own" : "Runs every indicator on its own (default settings) against the base";
  $("#btn-clear").disabled = !(ent.length + ext.length);
  $("#recipe-foot").textContent = ent.length + ext.length ? "The base trade on the same data runs alongside (reused if already run), and the result shows both." : "With no indicators this runs the base trade: the line every indicator has to beat.";
}

function wireBuild() {
  $("#years").addEventListener("click", (e) => {
    const c = e.target.closest(".chip"); if (!c) return;
    if (c.dataset.all) { const all = S.years.size === S.meta.years.length; S.years = new Set(all ? [] : S.meta.years.map((y) => y.year)); }
    else { const y = +c.dataset.y; S.years.has(y) ? S.years.delete(y) : S.years.add(y); }
    const focusKey = c.dataset.all ? "[data-all]" : `[data-y="${c.dataset.y}"]`;
    renderYears(); save(); $("#years " + focusKey)?.focus();
  });
  $("#patterns").addEventListener("click", (e) => {
    const c = e.target.closest(".chip"); if (!c) return;
    const p = c.dataset.pat; S.patterns.has(p) ? S.patterns.delete(p) : S.patterns.add(p);
    if (!S.patterns.size) S.patterns.add(p);
    renderPatterns(); renderBaseOverview(); renderRecipe(); save(); $(`#patterns [data-pat="${p}"]`)?.focus();
  });
  $("#base-settings").addEventListener("input", (e) => { if (e.target.closest("#patterns")) return; readBase(); metaSoon(); });
  $("#base-settings").addEventListener("click", (e) => {
    const b = e.target.closest("[data-preset]"); if (!b) return;
    S.base = { ...presets()[b.dataset.preset], ...triggerOf(S.base) }; renderBase(); renderRecipe(); save(); metaSoon();
  });
  $("#btn-base-reset").addEventListener("click", () => {
    const md = MARKET_DEFAULTS[S.market];
    S.base = { ...md.presets.strict, ...TRIGGER_DEFAULTS }; S.risk = md.risk; S.notional = md.notional; S.maxTrades = 0; S.maxPositions = 0;
    S.patterns = new Set(S.meta.patterns);
    renderBase(); renderPatterns(); renderRecipe(); save(); metaSoon();
  });
  $("#mkt").addEventListener("click", (e) => { const c = e.target.closest("[data-m]"); if (c) switchMarket(c.dataset.m); });

  $(".seg").addEventListener("click", (e) => { const b = e.target.closest("[data-g]"); if (!b) return; S.indGroup = b.dataset.g; S.indCat = ""; renderIndGroup(); });
  $("#ind-cats").addEventListener("click", (e) => { const b = e.target.closest("[data-cat]"); if (!b) return; S.indCat = b.dataset.cat; renderIndGroup(); });
  $("#ind-search").addEventListener("input", filterPlugins);
  $("#ind-onlysel").addEventListener("change", (e) => { S.onlySel = e.target.checked; filterPlugins(); });
  for (const g of ["entry", "exit"]) {
    const root = $(`#${g}-list`);
    root.addEventListener("change", (e) => {
      const t = e.target;
      if (t.matches("input[type=checkbox]")) {
        const id = t.dataset.id; const p = plug(id);
        if (t.checked) {
          if (p.exclusive) for (const o of S.plugins) if (o.id !== id && o.exclusive === p.exclusive && o.id in S.sel) {
            delete S.sel[o.id];
            banner(`“${o.name}” was unticked: only one target replacement can be used at a time.`); setTimeout(() => banner(""), 5000);
          }
          S.sel[id] = Object.fromEntries(p.params.map((q) => [q.key, q.default]));
          if (NEEDS_NEWS.has(id) && !("news" in S.sel)) {          // these measure from the filing News finds: tick it for you
            const n = plug("news"); if (n) S.sel.news = Object.fromEntries(n.params.map((q) => [q.key, q.default]));
          }
        } else {
          delete S.sel[id];
        }
        useNewsBarBase();
        renderPlugins();
        $(`#${g}-list input[data-id="${id}"]`)?.focus();
      } else if (t.dataset.p) {
        const q = plug(t.dataset.p).params.find((x) => x.key === t.dataset.k);
        (S.sel[t.dataset.p] ||= {})[t.dataset.k] = q.kind === "select" || q.kind === "time" ? t.value : +t.value;
        const sr = root.querySelector(`[data-sr="${t.dataset.p}"]`);
        if (sr) sr.innerHTML = srDiagram(t.dataset.p, S.sel[t.dataset.p]);
        if (t.dataset.p === "news" && t.dataset.k === "trigger") useNewsBarBase();
        renderRecipe(); save();
      }
    });
    root.addEventListener("click", (e) => {
      const b = e.target.closest("[data-reset]"); if (!b) return;
      const p = plug(b.dataset.reset); S.sel[p.id] = Object.fromEntries(p.params.map((q) => [q.key, q.default]));
      renderPlugins();
    });
  }
  $("#recipe").addEventListener("click", (e) => {
    const fix = e.target.closest("[data-fix-tick]");
    if (fix) { const p = plug(fix.dataset.fixTick); S.sel[p.id] = Object.fromEntries(p.params.map((q) => [q.key, q.default])); renderPlugins(); return; }
    const b = e.target.closest("[data-unsel]"); if (!b) return;
    delete S.sel[b.dataset.unsel]; renderPlugins();
  });
  $("#btn-clear").addEventListener("click", () => { S.sel = {}; renderPlugins(); });
  $("#btn-apply").addEventListener("click", () => applyNow());
  $("#btn-scan").addEventListener("click", () => scanEach());
}

/* ---------------------------------------------------------------- run */
function payload(plugins = null) {
  return {
    market: S.market, years: [...S.years].sort(), base: S.base, patterns: [...S.patterns],
    risk_inr: S.risk, max_notional_inr: S.notional, max_trades: S.maxTrades, max_positions: S.maxPositions,
    plugins: plugins ?? Object.entries(S.sel).map(([id, params]) => ({ id, params })),
  };
}

async function ensureBuilt() {
  let r = await api("/api/lab/meta", "POST", { market: S.market, base: S.base, years: S.meta.years.slice(0, 1).map((y) => y.year) });
  if (r.ok) S.meta.years = r.data.years;
  const need = S.meta.years.filter((y) => S.years.has(y.year) && !y.built).map((y) => y.year);
  if (!need.length) return true;
  const job = await api("/api/lab/build", "POST", { market: S.market, base: S.base, years: need });
  if (!job.ok) { banner(job.data?.error || "could not start the build", "err"); return false; }
  for (;;) {
    await new Promise((res) => setTimeout(res, 1200));
    const jobs = (await api("/api/lab/jobs")).data || [];
    const j = jobs.find((x) => x.id === job.data.id);
    if (!j) break;
    if (j.state === "cancelled") { banner("Build cancelled — nothing was run.", "err"); setTimeout(() => banner(""), 3000); return false; }
    banner(`Step 1 of 2 · building base data for ${j.current_year ?? ""}… ${j.done}/${j.total} symbols (one-time, ~20 s per year)`, "", j.total ? j.done / j.total : 0,
      () => api("/api/lab/build/cancel", "POST", { id: j.id }));
    if (j.state === "error") { banner("Build failed: " + j.error, "err"); return false; }
    if (j.state === "done") break;
  }
  r = await api("/api/lab/meta", "POST", { market: S.market, base: S.base, years: S.meta.years.slice(0, 1).map((y) => y.year) });
  if (r.ok) { S.meta.years = r.data.years; renderYears(); }
  banner("");
  return true;
}

// The 'News' indicator reads NSE filings; fetch the ones never fetched (one request per symbol, ~2 s each), then re-run.
async function ensureNews(need, plugins) {
  const ok = confirm(`'News' needs ${need.source || "NSE"} filings for ${need.symbols} of ${need.total} symbols that were never fetched.\n\n` +
    `About ${Math.max(1, Math.ceil(need.est_s / 60))} min, one polite request per symbol; cached forever after, shared by every run. Fetch now?`);
  if (!ok) return false;
  const job = await api("/api/lab/news/prefetch", "POST", payload(plugins));
  if (!job.ok) { banner(job.data?.error || "could not start the news fetch", "err"); return false; }
  const key = job.data.key;
  for (;;) {
    const j = (await api(`/api/lab/news/prefetch/${key}`)).data || {};
    if (j.state === "done") {
      if (j.unmapped?.length) { banner(`SEC coverage is unknown for ${j.unmapped.length} symbol(s): ${j.unmapped.slice(0, 6).join(", ")}${j.unmapped.length > 6 ? "…" : ""}. Supply their historical CIK mappings and fetch again before running.`, "err"); return false; }
      break;
    }
    if (j.state === "cancelled") { banner("News fetch cancelled — nothing was run.", "err"); setTimeout(() => banner(""), 3000); return false; }
    if (j.state === "failed" || j.state === "unknown") { banner(j.error || "News fetch failed", "err"); return false; }
    banner(`Fetching ${need.source || "NSE"} filings… ${j.done || 0}/${j.total || need.symbols} symbols (one-time, cached afterwards)`, "", j.total ? j.done / j.total : 0,
      () => api("/api/lab/news/prefetch/cancel", "POST", { key }));
    await new Promise((res) => setTimeout(res, 2000));
  }
  banner("");
  return true;
}

async function runOne(plugins) {
  const r = await api("/api/lab/apply", "POST", payload(plugins));
  if (r.status === 409 && r.data?.need_news) { if (!(await ensureNews(r.data.need_news, plugins))) return null; return runOne(plugins); }
  if (r.status === 409) { if (!(await ensureBuilt())) return null; return runOne(plugins); }
  if (!r.ok) { banner(r.data?.error || `error ${r.status}`, "err"); return null; }
  return r.data;
}

function setBusy(on) {
  S.busy = on;
  renderRecipe();
}

async function applyNow() {
  if (S.busy) return;
  const bad = recipeChecks().find(([k]) => k === "err");
  if (bad) { banner(bad[1].replace(/<[^>]+>/g, ""), "err"); return; }
  setBusy(true);
  try {
    if (!(await ensureBuilt())) return;
    banner("Running the experiment…", "", null);
    const rec = await runOne();
    if (rec) {
      banner("");
      if (!("base" in rec)) rec.base = rec.base_id ? (await api(`/api/lab/run/${rec.base_id}`)).data : null;
      S.current = rec; renderResult(rec); showTab("result"); resumeNews(rec); await loadRuns();
      $("#result-card").focus({ preventScroll: true }); window.scrollTo({ top: 0, behavior: "smooth" });
    }
  } catch (error) { banner("The experiment could not finish. Check the server connection and try again.", "err");
  } finally { setBusy(false); }
}

/* ---------------------------------------------------------------- result */
const EXIT_COLORS = { target: "#2dd4bf", stop: "#fb7185", eod: "#64748b", ema9_break: "#fbbf24", ema20_break: "#f59e0b", macd_fade: "#a78bfa", vwap_break: "#60a5fa", trail_stop: "#f472b6", breakeven_stop: "#94a3b8", checkpoint_stop: "#fb923c", resistance_reject: "#e879f9", volume_climax: "#38bdf8", stale: "#7c8aa0" };
const EXIT_NAMES = { eod: "end of day", target: "target hit", stop: "stopped out" };

function emptyResult() {
  const c = $("#result-card"); c.className = "card empty";
  c.innerHTML = `<h2>Result</h2><p class="hint">Nothing open yet. Run an experiment from <a href="#build" data-goto="build">Build experiment</a>, or open one from <a href="#runs" data-goto="runs">Saved runs</a>.</p>`;
  $("#result-badge").hidden = true;
}

function exitBar(mix, n) {
  const bars = Object.entries(mix).map(([k, v]) => `<i style="width:${(100 * v / n).toFixed(2)}%;background:${EXIT_COLORS[k] || "#475569"}" title="${esc(EXIT_NAMES[k] || k)}: ${v}"></i>`).join("");
  const leg = Object.entries(mix).map(([k, v]) => `<span><b style="background:${EXIT_COLORS[k] || "#475569"}"></b>${esc(EXIT_NAMES[k] || k.replace(/_/g, " "))} ${v} (${(100 * v / n).toFixed(0)}%)</span>`).join("");
  return `<div class="exitbar">${bars}</div><div class="legend">${leg}</div>`;
}

function curveSvg(runC, baseC) {
  const W = 560, H = 160, P = 28;
  const all = [...(runC || []), ...(baseC || []), 0];
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const line = (c, color, w) => {
    if (!c || c.length < 2) return "";
    return `<polyline fill="none" stroke="${color}" stroke-width="${w}" points="${c.map((v, i) => `${(P + (W - P - 6) * i / (c.length - 1)).toFixed(1)},${(H - 16 - (H - 28) * (v - lo) / span).toFixed(1)}`).join(" ")}"/>`;
  };
  const y0 = H - 16 - (H - 28) * (0 - lo) / span;
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="cumulative net profit">
    <line x1="${P}" x2="${W - 6}" y1="${y0}" y2="${y0}" stroke="#334155" stroke-dasharray="3 3"/>
    ${line(baseC, "#8fa1bb", 1.4)}${line(runC, "#fbbf24", 2)}
    <text x="2" y="12">${inr(hi)}</text><text x="2" y="${H - 6}">${inr(lo)}</text>
    <text x="${W - 150}" y="${H - 2}">trades in order →</text></svg>
    <div class="legend"><span><b style="background:#fbbf24"></b>this run</span>${baseC ? `<span><b style="background:#8fa1bb"></b>base trade</span>` : ""}<span>cumulative net ${G()} at real costs</span></div>`;
}

function kpiRows(m, b) {
  const rows = [
    ["Trades", "n", (x) => x.toLocaleString(), null],
    [`Net ${G()} (real costs)`, "net_real_inr", (x) => inr(x), 1],
    [`${G()} per trade (mean)`, "mean_inr", (x) => inr(x, 1), 1],
    [`${G()} per trade (median)`, "median_inr", (x) => inr(x, 1), 1],
    [`${G()} per trade without the 5 best`, "mean_ex_top5_inr", (x) => inr(x, 1), 1],
    ["Win rate", "win_pct", (x) => x.toFixed(1) + "%", 1],
    ["Profit factor", "profit_factor", (x) => x.toFixed(2), 1],
    ["Gross % per trade (before costs)", "gross_pct_mean", (x) => x.toFixed(3) + "%", 1],
    ["Net % per trade (real costs)", "net_real_pct_mean", (x) => x.toFixed(3) + "%", 1],
    ["Return on capital deployed", "return_on_deployed_pct", (x) => x.toFixed(3) + "%", 1],
    ["Worst drawdown", "max_drawdown_inr", (x) => inr(x), 1],
    ["Reached the target", "target_hit_pct", (x) => x.toFixed(1) + "%", null],
    ["Stopped out", "stop_pct", (x) => x.toFixed(1) + "%", null],
    ["Costs paid", "costs_inr", (x) => inr(x), null],
  ];
  return rows.map(([label, k, f, good]) => {
    const v = m[k], bv = b ? b[k] : null;
    const d = v != null && bv != null && b ? v - bv : null;
    const dTxt = d == null ? "" : sgn(d, (x) => f(x).replace(/^−/, ""));
    return `<tr><td>${label}</td><td class="num"><b class="${good && v != null ? tone(v) : ""}">${v == null ? "–" : f(v)}</b></td>` +
      (b ? `<td class="num dim">${bv == null ? "–" : f(bv)}</td><td class="num ${good && d != null ? tone(d) : "dim"}">${dTxt}</td>` : "") + `</tr>`;
  }).join("");
}

function changesSizing(rec) {
  return rec.config.plugins.some((p) => p.id === "target_fixed_rr" && Number(p.params.stop_x ?? 1) !== 1);
}

/* The one-line answer at the top of a result: better / worse / no clear difference than the base, plus the caveats. */
function headline(rec) {
  const m = rec.metrics, b = rec.base?.metrics, vs = rec.vs_base || {}, cautions = [];
  if (!m.n) return { cls: "flat", pill: "No trades", text: "The selection filtered out every candidate. Loosen a filter or add years.", cautions };
  if (m.n < 100) cautions.push(`Small sample: ${m.n} trades. Treat it as a hint, not a result.`);
  if (m.mean_ex_top5_inr != null && Math.sign(m.mean_ex_top5_inr) !== Math.sign(m.mean_inr)) cautions.push(`Without its 5 best trades the mean flips to ${inr(m.mean_ex_top5_inr, 1)}: a few trades carry it.`);
  if (m.median_inr != null && Math.sign(m.median_inr) !== Math.sign(m.mean_inr) && m.mean_inr > 0) cautions.push(`The typical (median) trade loses ${inr(Math.abs(m.median_inr), 1)}: the mean relies on big winners.`);
  if (rec.trial_n >= 5) cautions.push(`Trial #${rec.trial_n} on this data window: the more variants tried, the more look good by luck.`);
  if (!b) {
    return { cls: "base", pill: "Base trade", cautions,
      text: `${m.n} trades, ${m.mean_inr >= 0 ? "making" : "losing"} <b class="${tone(m.mean_inr)}">${inr(Math.abs(m.mean_inr), 1)}</b> per trade at real costs (median ${inr(m.median_inr, 1)}). This is the line every indicator has to beat.` };
  }
  let d, unit, p = null;
  if (changesSizing(rec)) {
    d = m.return_on_deployed_pct - b.return_on_deployed_pct; unit = sgn(d, (x) => x.toFixed(3) + " pp") + " return per rupee deployed";
    cautions.push("Position size changes with this setting, so it is judged per rupee deployed, and no significance test applies.");
  } else {
    d = m.mean_inr - b.mean_inr; unit = sgn(d, (x) => inr(x, 1)) + " per trade";
    p = (vs.paired || vs.kept_vs_removed || {}).p ?? null;
    if (p == null) cautions.push("No significance test was possible for this comparison.");
  }
  const noise = p != null && p > 0.1;
  const cls = noise || Math.abs(d) < 1e-9 ? "flat" : d > 0 ? "good" : "bad";
  const pill = cls === "flat" ? "No clear difference" : cls === "good" ? "Better than base" : "Worse than base";
  let text = `<b class="${tone(d)}">${unit}</b> vs the base (${inr(m.mean_inr, 1)} vs ${inr(b.mean_inr, 1)} per trade; ${m.n} vs ${b.n} trades)`;
  text += noise ? `, but p ≈ ${p}: indistinguishable from chance.` : p != null ? ` · p ≈ ${p}.` : ".";
  if (cls !== "bad" && m.mean_inr < 0) text += ` It still <b class="neg">loses ${inr(Math.abs(m.mean_inr), 1)}</b> per trade at real costs.`;
  return { cls, pill, text, cautions };
}

/* The longer explanation of how the comparison was made (kept under "How this was compared"). */
function verdict(rec) {
  const m = rec.metrics, b = rec.base?.metrics, vs = rec.vs_base || {};
  if (!m.n || !b) return "";
  const lines = [];
  if (!changesSizing(rec) && vs.paired) lines.push(`On the ${vs.paired.n} trades both took, the exits changed the result by ${inr(vs.paired.mean_change_inr, 1)} per trade (${vs.paired.changed} trades changed, approx. p = ${vs.paired.p}).`);
  if (changesSizing(rec)) lines.push(`Return per rupee deployed: ${pct(m.return_on_deployed_pct, 3)} vs ${pct(b.return_on_deployed_pct, 3)}. Mean ${G()} per trade was ${inr(m.mean_inr, 1)} vs ${inr(b.mean_inr, 1)}, but size changed, so that is not a fair comparison.`);
  if (vs.removed && vs.overlap && vs.overlap.same > 0) {
    const keptMean = (b.net_real_inr - vs.removed.net_real_inr) / (b.n - vs.removed.n);
    lines.push(`The ${vs.removed.n} base trades it dropped averaged ${inr(vs.removed.mean_inr, 1)}; the ${vs.overlap.same} it kept averaged ${inr(keptMean, 1)}${vs.kept_vs_removed ? ` (approx. p = ${vs.kept_vs_removed.p})` : ""}.${vs.overlap.new ? ` It also took ${plural(vs.overlap.new, "trade")} the base did not (a vetoed candidate lets a later one through).` : ""}`);
  }
  return lines.join(" ");
}

/* The base setting a run was made with: momentum band, RVOL, sizing, entry cap, patterns. */
function baseSummary(c) {
  const b = c.base, g = (b.market || "NSE") === "US" ? "$" : "₹";
  const n = c.patterns.length, all = S.meta?.patterns?.length;
  const t = triggerOf(b);
  const parts = [`${b.day_chg_min}–${b.day_chg_max}%`, `RVOL ≥ ${b.rvol_min}`, ...(t.pattern_tf === "1m" ? ["1-min candles"] : []), ...(t.stop_levels === "local" ? ["today's-levels stop"] : []), ...(t.entry_mode === "market" ? ["no pattern"] : t.entry_mode === "hybrid" ? ["+ doji / spinning top"] : []), `risk ${g}${c.risk_inr}`, `max ${g}${c.max_notional_inr}`,
    c.max_trades ? `${c.max_trades}/day` : "no entry cap", ...(c.max_positions ? [`max ${c.max_positions} open`] : []), ...(t.entry_mode === "market" ? [] : [all && n === all ? "all patterns" : plural(n, "pattern")])];
  return { text: parts.join(" · "), title: `Base trade setting: day change ${b.day_chg_min}–${b.day_chg_max}%, RVOL ≥ ${b.rvol_min}, risk ${g}${c.risk_inr} per trade, max position ${g}${c.max_notional_inr}, ${c.max_trades || "unlimited"} entries per stock per day, ${c.max_positions || "unlimited"} positions open at once. ${t.entry_mode === "market" ? "No pattern trigger: market entry at the next minute's open." : "Patterns: " + c.patterns.join(", ")}. Candles: ${TF_NAME[t.pattern_tf]}.` };
}

function tagsFor(rec) {
  const ps = rec.config.plugins, bs = baseSummary(rec.config);
  const baseTag = `<span class="tag base" title="${esc(bs.title)}">${ps.length ? "base" : "base trade only"} · ${esc(bs.text)}</span>`;
  return ps.map((x) => {
    const p = plug(x.id), pt = paramText(x.id, x.params);
    return `<span class="tag ${p?.group || ""}" title="${esc((p?.desc || "") + "\n" + paramText(x.id, x.params, false))}">${esc(p?.name || x.id)}${pt ? ` <small>· ${esc(pt)}</small>` : ""}</span>`;
  }).join("") + baseTag;
}

/* A short title for a run: its indicators by name, or "Base trade". */
function runTitle(r) {
  const ps = r.config.plugins;
  if (!ps.length) return `<span class="dim">Base trade</span>`;
  const by = (g) => ps.filter((x) => plug(x.id)?.group === g).map((x) => esc(plug(x.id)?.name || x.id)).join(" + ");
  const e = by("entry"), x = by("exit");
  return [e && `<span class="e">${e}</span>`, x && `<span class="x">${x}</span>`].filter(Boolean).join(" · ");
}

function renderResult(rec) {
  const m = rec.metrics, b = rec.base?.metrics || null, h = headline(rec), more = verdict(rec);
  const card = $("#result-card"); card.className = "card"; card.tabIndex = -1;
  const badge = $("#result-badge"); badge.hidden = false;
  badge.className = "badge " + ({ good: "pos", bad: "neg", flat: "warn" }[h.cls] || "");
  badge.textContent = { good: "better", bad: "worse", flat: "≈", base: "base" }[h.cls] || "";
  const kpi = (l, k, d, f = (x) => inr(x, d), good = true, delta = true) => {
    const v = m[k], bv = b?.[k], dd = delta && v != null && bv != null ? v - bv : null;
    return `<div class="kpi"><div class="l">${l}</div><div class="v ${good && v != null ? tone(v) : ""}">${v == null ? "–" : f(v)}</div>` +
      (b ? `<div class="d">base ${bv == null ? "–" : f(bv)}${dd != null ? ` · <span class="${good ? tone(dd) : "dim"}">${sgn(dd, (x) => f(x).replace(/^−/, ""))}</span>` : ""}</div>` : "") + `</div>`;
  };
  card.innerHTML = `
    <div class="res-head">
      <div><h2>${runTitle(rec)}</h2>
        <p class="res-name">${rec.cached ? "Already run: loaded from the saved result" : "Saved"} · ${esc(rec.created_at.replace("T", " ").slice(0, 16))} · ${esc(rec.config.base.market || "NSE")} ${esc(rec.config.years.join(", "))}${rec.name && !/^(Entry|Exit|Base)/.test(rec.name) ? ` · “${esc(rec.name)}”` : ""}</p></div>
      <div class="btns">
        ${m.n ? `<a class="primary" href="/lab/run/${rec.id}" target="_blank" rel="noopener">Trade charts ↗</a>` : ""}
        <button class="ghost" type="button" data-act="load" data-id="${rec.id}" title="Put these settings back into Build experiment">Edit these settings</button>
        <button class="ghost" type="button" data-act="note" data-id="${rec.id}">✎ Name / note</button>
        ${m.n ? newsBtn(rec, "ghost") : ""}
      </div>
    </div>
    <div class="tags">${tagsFor(rec)}</div>
    ${rec.config.plugins.some((p) => NEWS_IDS.includes(p.id) &&
      (rec.config.indicator_revisions?.[p.id] || 1) !== S.meta.news_revision) ?
      `<p class="hint">Historical news result: these saved metrics use an earlier indicator revision. Running the settings now saves a separate experiment.</p>` : ""}
    <div class="headline ${h.cls}"><span class="pill">${h.pill}</span><p>${h.text}</p></div>
    ${h.cautions.length ? `<ul class="cautions">${h.cautions.map((c) => `<li>${c}</li>`).join("")}</ul>` : ""}
    ${rec.notes ? `<p class="hint">📝 ${esc(rec.notes)}</p>` : ""}
    ${m.n ? `<div class="kpis">
      ${kpi("Net at real costs", "net_real_inr", 0, undefined, true, false)}
      ${kpi(`${G()} per trade (mean)`, "mean_inr", 1)}
      ${kpi(`${G()} per trade (median)`, "median_inr", 1)}
      ${kpi("Mean without the 5 best", "mean_ex_top5_inr", 1)}
      ${kpi("Trades", "n", 0, (x) => x.toLocaleString(), false, false)}
      ${kpi("Win rate", "win_pct", 1, (x) => x.toFixed(1) + "%", false)}
    </div>` : ""}
    ${m.n ? `<div class="two">
      <div><h3>All metrics</h3><table><thead><tr><th>Metric</th><th class="num">This run</th>${b ? `<th class="num">Base</th><th class="num">Change</th>` : ""}</tr></thead><tbody>${kpiRows(m, b)}</tbody></table></div>
      <div><h3>Cumulative profit</h3>${curveSvg(m.curve, b?.curve)}
        <h3>How the trades ended</h3>${exitBar(m.exit_mix, m.n)}
        <h3>By year</h3>
        <table><thead><tr><th>Year</th><th class="num">Trades</th><th class="num">Win %</th><th class="num">${G()}/trade</th><th class="num">Net ${G()}</th></tr></thead><tbody>
        ${m.by_year.map((y) => `<tr><td>${y.year}</td><td class="num">${y.n}</td><td class="num">${y.win_pct}%</td><td class="num ${tone(y.mean_inr)}">${inr(y.mean_inr, 1)}</td><td class="num ${tone(y.net_real_inr)}">${inr(y.net_real_inr)}</td></tr>`).join("")}
        </tbody></table></div></div>` : ""}
    ${more ? `<details><summary>How this was compared with the base</summary><p class="verdict">${more}</p></details>` : ""}
    <details><summary>Exact saved settings</summary><p class="hint">${esc(rec.describe)}</p></details>
    <div id="news-box"></div>`;
}

/* ---------------------------------------------------------------- news check */
let newsTimer = null;
// The News button: green with a tick once the check has finished for this run.
function newsBtn(r, kind) {
  const c = r.news_check, done = c && c.complete, part = c && !c.complete;
  const label = kind === "mini" ? (done ? "✓ News" : part ? "News ◐" : "News") : (done ? "✓ News checked" : part ? "📰 Resume news check" : "📰 Check news");
  const tip = c ? `News checked ${String(c.checked_at).replace("T", " ")} UTC: ${c.news} with a material filing, ${c.minor} routine only, ${c.none} none${part ? `, ${c.error + c.skipped} not checked (click to fill the gaps; cached windows are free)` : ""}. Click to view.`
    : `Ask ${S.market === "US" ? "SEC EDGAR" : "NSE"} whether each stock filed anything in the 24h before its entry (one request per symbol, rate-limited)`;
  return `<button class="${kind}${done ? " news-done" : ""}" type="button" data-act="news" data-id="${r.id}" title="${esc(tip)}">${label}</button>`;
}
// Re-attach to a news check that is still running on the server, or show the saved result.
async function resumeNews(rec) {
  if (!rec.metrics.n) return;
  const d = (await api(`/api/lab/run/${rec.id}/news`)).data;
  if (d?.job?.state === "running") checkNews(rec.id); else if (d?.result) renderNews($("#news-box"), rec.id, d);
}
async function checkNews(id) {
  if (!$("#news-box") || S.current?.id !== id) await showRun(id);
  const box = $("#news-box"); if (!box) return;
  clearTimeout(newsTimer);
  const st = (await api(`/api/lab/run/${id}/news`)).data;
  if (!(st?.job?.state === "running")) {
    const r = await api(`/api/lab/run/${id}/news`, "POST");
    if (!r.ok) { box.innerHTML = `<p class="hint">${esc(r.data?.error || "could not start")}</p>`; return; }
  }
  const poll = async () => {
    const d = (await api(`/api/lab/run/${id}/news`)).data, j = d.job;
    if (j && j.state === "running") {
      const src = j.source || "NSE";
      box.innerHTML = `<p class="hint">📰 Asking ${src}… ${j.done}/${j.total} symbols (${j.requests} requests to ${src}, ${j.cached} already in the cache). One request at a time, so this takes a while. ` +
        `<a href="#" data-act="newscancel" data-id="${id}">stop</a></p>`;
      newsTimer = setTimeout(poll, 2000); return;
    }
    renderNews(box, id, d);
    await loadRuns();   // the history row's News button turns green
    const fresh = (await api(`/api/lab/run/${id}`)).data;
    const cardBtn = document.querySelector(`#result-card [data-act="news"][data-id="${id}"]`);
    if (fresh && cardBtn) cardBtn.outerHTML = newsBtn(fresh, "ghost");
  };
  poll();
}
function renderNews(box, id, d) {
  const res = d.result, j = d.job;
  if (j?.state === "failed") { box.innerHTML = `<p class="hint">News check failed: ${esc(j.error)}</p>`; return; }
  if (!res) { box.innerHTML = ""; return; }
  const sm = res.summary, errs = (j?.errors || []);
  const chip = (t) => t.status === "news" ? `<span class="newsb news" title="a material filing (results, orders, deals, rating, dividend, fund-raising, litigation…)">📰 ${t.material} material</span>`
    : t.status === "minor" ? `<span class="newsb minor" title="only routine / unclear / exchange-query filings">${t.count} routine</span>`
    : t.status === "none" ? `<span class="newsb none">no filing</span>` : `<span class="newsb err" title="could not be checked">news ?</span>`;
  const ord = { news: 0, minor: 1, none: 2 };
  const rows = res.trades.slice().sort((a, b) => (ord[a.status] ?? 3) - (ord[b.status] ?? 3) || a.date.localeCompare(b.date));
  const wasOpen = box.querySelector("details.newsd")?.open === true;   // collapsed by default; a re-render keeps what you opened
  const tag = res.classifier ? ` · AI: ${esc(res.classifier.model)}` : "";
  box.innerHTML = `<details class="newsd" ${wasOpen ? "open" : ""}><summary>📰 News check — <b>${sm.news}</b> material · ${sm.minor} routine · ${sm.none} none${sm.error || sm.skipped ? ` · ${(sm.error || 0) + (sm.skipped || 0)} not checked` : ""}${tag}</summary>
    <h3 style="font-size:13px;margin:14px 0 4px">News check <small>· ${res.market === "US" ? "SEC EDGAR filings" : "NSE filings"} in the 24h before each entry · ${esc(res.checked_at.replace("T", " "))} UTC</small></h3>
    <p class="hint"><b>${sm.news}</b> of ${res.trades.length} trades had a <b>material</b> filing, <b>${sm.minor}</b> only routine / unclear ones (trading window, newspaper ads, call schedules, "updates"…), <b>${sm.none}</b> none${sm.error ? `, <b>${sm.error}</b> could not be checked (${res.market === "US" ? "EDGAR" : "NSE"} failed or the ticker has no CIK mapping — click again; fetched symbols are not asked twice)` : ""}${sm.skipped ? `, <b>${sm.skipped}</b> not fetched yet` : ""}.
    ${res.classifier ? `<b>Classified by AI</b> (${esc(res.classifier.model)}): of ${res.classifier.filings} filings the keyword rules could not place, <b>${res.classifier.to_material}</b> became material (${res.classifier.changed} changed in all). <a href="#" data-act="aiaudit" data-id="${id}">review the changes</a>.` : (res.market === "US" ? `"Material" is the 8-K item code / form type (results, agreements, Reg FD, other events, 6-K, 425); share offerings and routine forms never count. Times are SEC acceptance times, which can lag the press release.` : `"Material" is a keyword judgement on NSE's filing subject.`)} Descriptive only. <a href="/lab/run/${id}" target="_blank">Open the charts ↗</a> to see the sign on each trade.</p>
    ${errs.length ? `<p class="hint">${esc(errs[0])}</p>` : ""}
    <div style="max-height:320px;overflow:auto"><table><thead><tr><th>Date</th><th>Symbol</th><th>News</th><th>Best filing</th></tr></thead><tbody>
    ${rows.map((t) => `<tr><td>${t.date}</td><td>${esc(t.symbol)}</td><td>${chip(t)}</td><td>${t.items[0] ? esc(t.items[0].h.slice(0, 110)) : ""}</td></tr>`).join("")}
    </tbody></table></div>
    <div class="aibar" id="aibar"></div><div id="ai-audit"></div></details>`;
  if (res.market === "US") $("#aibar").innerHTML = ""; else renderAiBar(id, res);
}

const AI_KEY = "lab.aiModel";
async function renderAiBar(id, res) {
  const bar = $("#aibar"); if (!bar) return;
  const m = (await api("/api/lab/ai/models")).data;
  if (!m?.running) { bar.innerHTML = `<p class="hint">AI classifier: Ollama is not running. Start it with <code>ollama serve</code>, then reopen this run.</p>`; return; }
  let saved = ""; try { saved = localStorage.getItem(AI_KEY) || ""; } catch (e) {}
  const pick = m.models.find((x) => x.name === saved)?.name || m.models.find((x) => /gemma/i.test(x.name) && !x.cloud)?.name || m.models.find((x) => !x.cloud)?.name || "";
  bar.innerHTML = `<label>Classifier model
      <select id="ai-model">${m.models.map((x) => `<option value="${esc(x.name)}" ${x.name === pick ? "selected" : ""}>${esc(x.name)}${x.params ? " · " + esc(x.params) : ""}${x.cloud ? " · ☁ sends text to ollama.com" : ""}</option>`).join("")}</select></label>
    <button class="ghost" type="button" data-act="airun" data-id="${id}" title="Classify the filings the keyword rules call 'unclear' (press releases, 'updates') using the chosen local model. Cached per model.">Classify with AI</button>
    ${res.classifier ? `<button class="ghost" type="button" data-act="aioff" data-id="${id}">Back to keyword rules</button>` : ""}
    <span id="ai-state" class="hint"></span>
    <p class="hint">Small models (≈3B) tend to miss order wins in press releases; an 8B model or larger is safer. On this Mac, ~4 s per filing for an 8B model.</p>`;
  $("#ai-model").onchange = (e) => { try { localStorage.setItem(AI_KEY, e.target.value); } catch (x) {} };
  const st = (await api(`/api/lab/run/${id}/news/ai`)).data;
  if (st?.job?.state === "running") aiPoll(id);
}
let aiTimer = null;
async function aiPoll(id) {
  clearTimeout(aiTimer);
  const el = $("#ai-state"); const j = (await api(`/api/lab/run/${id}/news/ai`)).data?.job;
  if (!j) return;
  if (j.state === "running") {
    if (el) el.innerHTML = `${esc(j.phase)}${j.total ? ` · ${j.done}/${j.total} filings` : ""} <a href="#" data-act="aicancel" data-id="${id}">stop</a>`;
    aiTimer = setTimeout(() => aiPoll(id), 2500); return;
  }
  if (j.state === "failed") { if (el) el.textContent = "AI classification failed: " + j.error; return; }
  const d = (await api(`/api/lab/run/${id}/news`)).data; renderNews($("#news-box"), id, d); await loadRuns();
}
async function aiAudit(id) {
  const box = $("#ai-audit"); if (!box) return;
  const rows = (await api(`/api/lab/run/${id}/news/ai/audit`)).data || [];
  box.innerHTML = rows.length ? `<h3 style="font-size:13px;margin:12px 0 4px">Filings where the AI and the keyword rules disagree (${rows.length} shown, material first)</h3>
    <div style="max-height:300px;overflow:auto"><table><thead><tr><th>Stock</th><th>Subject</th><th>Filing text</th><th>Keywords</th><th>AI</th></tr></thead><tbody>
    ${rows.map((r) => `<tr><td>${esc(r.symbol)}</td><td>${esc(r.subject)}</td><td>${esc(r.text.slice(-150))}</td><td>${r.keyword}</td><td><b>${r.ai}</b> · ${esc(r.category)}</td></tr>`).join("")}</tbody></table></div>` : `<p class="hint">No disagreements to show.</p>`;
}


/* ---------------------------------------------------------------- saved runs */
async function loadRuns() {
  const r = await api("/api/lab/runs");
  if (r.ok) { S.runs = r.data.filter((x) => (x.config.base.market || "NSE") === S.market); renderHistory(); renderRecipe(); }
}

function rowDelta(r) {
  const base = r.base_id ? S.runs.find((x) => x.id === r.base_id) : null;
  return base ? r.metrics.mean_inr - base.metrics.mean_inr : null;
}

const baseKey = (c) => JSON.stringify([c.base.day_chg_min, c.base.day_chg_max, c.base.rvol_min, triggerOf(c.base).pattern_tf, triggerOf(c.base).entry_mode, triggerOf(c.base).stop_levels, c.risk_inr, c.max_notional_inr, c.max_trades, c.max_positions || 0, [...c.patterns].sort()]);

function renderBaseFilter() {
  const sel = $("#h-base"), cur = sel.value, seen = new Map();
  for (const r of S.runs) {
    const k = baseKey(r.config), e = seen.get(k) || { n: 0, c: r.config };
    e.n++; seen.set(k, e);
  }
  sel.innerHTML = `<option value="">all bases (${seen.size})</option>` +
    [...seen].map(([k, e]) => `<option value="${esc(k)}" title="${esc(baseSummary(e.c).title)}">${esc(baseSummary(e.c).text)} (${e.n})</option>`).join("");
  sel.value = seen.has(cur) ? cur : "";
}

function renderHistory() {
  const active = document.activeElement;
  const focusCmp = active?.dataset?.cmp, focusStar = active?.dataset?.act === "star" ? active.dataset.id : null;
  renderBaseFilter();
  const q = $("#h-q").value.trim().toLowerCase(), starOnly = $("#h-star").checked, kind = $("#h-kind").value, baseSel = $("#h-base").value;
  let rows = S.runs.filter((r) => {
    if (baseSel && baseKey(r.config) !== baseSel) return false;
    if (starOnly && !r.starred) return false;
    const groups = r.config.plugins.map((x) => plug(x.id)?.group);
    if (kind === "base" && r.config.plugins.length) return false;
    if (kind === "entry" && !groups.includes("entry")) return false;
    if (kind === "exit" && !groups.includes("exit")) return false;
    const names = r.config.plugins.map((x) => plug(x.id)?.name || x.id).join(" ");
    return !q || (r.name + " " + names + " " + r.notes + " " + r.config.years.join(" ") + " " + r.describe).toLowerCase().includes(q);
  });
  const key = { created_at: (r) => r.created_at, name: (r) => r.name, years: (r) => r.config.years.join(), n: (r) => r.metrics.n || 0, win: (r) => r.metrics.win_pct ?? -1, mean: (r) => r.metrics.mean_inr ?? -1e9, median: (r) => r.metrics.median_inr ?? -1e9, net: (r) => r.metrics.net_real_inr ?? -1e9, delta: (r) => rowDelta(r) ?? -1e9 }[S.sort.k];
  rows = rows.sort((a, b) => ((b.starred ? 1 : 0) - (a.starred ? 1 : 0))
    || ((key(a) > key(b) ? 1 : key(a) < key(b) ? -1 : 0) * S.sort.dir)
    || (a.created_at > b.created_at ? -1 : 1));
  S.shown = rows.map((r) => r.id);
  const allBox = $("#h-all");
  allBox.checked = S.shown.length > 0 && S.shown.every((id) => S.compare.has(id));
  allBox.indeterminate = !allBox.checked && S.shown.some((id) => S.compare.has(id));
  $("#hist-count").textContent = `${rows.length} shown · ${S.runs.length} saved for ${S.market}`;
  $("#runs-badge").textContent = S.runs.length;
  $("#history tbody").innerHTML = rows.map((r) => {
    const m = r.metrics, d = rowDelta(r), yrs = r.config.years, bs = baseSummary(r.config);
    const named = r.name && !/^(Entry|Exit|Base)\b/.test(r.name);
    const pt = r.config.plugins.map((x) => paramText(x.id, x.params) && `${plug(x.id)?.name || x.id}: ${paramText(x.id, x.params)}`).filter(Boolean).join(" · ");
    return `<tr data-id="${r.id}" class="${S.current?.id === r.id ? "shown" : ""}">
      <td><input type="checkbox" data-cmp="${r.id}" ${S.compare.has(r.id) ? "checked" : ""} aria-label="Select ${esc(r.name)} to compare"></td>
      <td><button type="button" aria-label="Favourite ${esc(r.name)}" aria-pressed="${!!r.starred}" class="star ${r.starred ? "on" : ""}" data-act="star" data-id="${r.id}" title="star">${r.starred ? "★" : "☆"}</button></td>
      <td class="exp"><div class="exp-t">${named ? `“${esc(r.name)}” · ` : ""}${runTitle(r)}</div>
        ${pt ? `<div class="exp-b exp-p" title="${esc(pt)}">${esc(pt)}</div>` : ""}
        <div class="exp-b" title="${esc(bs.title)}">base: ${esc(bs.text)}</div>
        ${r.notes ? `<div class="exp-n">📝 ${esc(r.notes)}</div>` : ""}</td>
      <td>${yrs.length > 2 ? yrs[0] + "–" + yrs[yrs.length - 1] : yrs.join(", ")}</td>
      <td class="dim">${esc(r.created_at.replace("T", " ").slice(5, 16))}</td>
      <td class="num">${m.n ?? 0}</td><td class="num">${m.n ? m.win_pct + "%" : "–"}</td>
      <td class="num ${tone(m.mean_inr)}">${m.n ? inr(m.mean_inr, 1) : "–"}</td>
      <td class="num ${tone(m.median_inr)}">${m.n ? inr(m.median_inr, 1) : "–"}</td>
      <td class="num ${tone(m.net_real_inr)}">${m.n ? inr(m.net_real_inr) : "–"}</td>
      <td class="num ${d == null ? "dim" : tone(d)}">${d == null ? (r.config.plugins.length ? "–" : "base") : sgn(d, (x) => inr(x, 1))}</td>
      <td class="acts">${m.n ? `<a class="mini" href="/lab/run/${r.id}" target="_blank" rel="noopener" title="Every trade on a candle chart">Charts ↗</a>` : ""}
        <button type="button" class="mini" data-act="load" data-id="${r.id}" title="Put these settings into Build experiment">Edit</button>
        ${m.n ? newsBtn(r, "mini") : ""}
        <button type="button" class="mini danger" data-act="del" data-id="${r.id}" aria-label="Delete ${esc(r.name)}" title="Delete">✕</button></td></tr>`;
  }).join("") || `<tr><td colspan="12" class="dim">${S.runs.length ? "No saved runs match the filters." : "No runs saved yet for this market. Build an experiment and run it."}</td></tr>`;
  document.querySelectorAll("#history th[data-k]").forEach((th) => {
    const on = th.dataset.k === S.sort.k;
    th.setAttribute("aria-sort", on ? (S.sort.dir === 1 ? "ascending" : "descending") : "none");
  });
  if (focusCmp) $(`#history [data-cmp="${focusCmp}"]`)?.focus({ preventScroll: true });
  if (focusStar) $(`#history [data-act="star"][data-id="${focusStar}"]`)?.focus({ preventScroll: true });
  $("#btn-compare").textContent = `Compare selected (${S.compare.size})`;
  $("#btn-compare").disabled = S.compare.size < 2;
  const delBtn = $("#btn-del-sel");
  delBtn.disabled = S.compare.size < 1; delBtn.textContent = `Delete selected${S.compare.size ? ` (${S.compare.size})` : ""}`;
}

function loadSettings(rec) {
  const c = rec.config;
  const nr = c.plugins.find((p) => NEWS_IDS.includes(p.id) && p.params.trigger !== undefined);
  if (nr && ![undefined, "pattern", ...(nr.id === "news" ? ["news_bar"] : [])].includes(nr.params.trigger)) {
    banner(`Saved news trigger '${nr.params.trigger}' has no implementation in this Lab. Its result is preserved. Configure a new experiment using the pattern or news_bar trigger.`, "err");
    return;
  }
  S.years = new Set(c.years); S.base = { day_chg_min: c.base.day_chg_min, day_chg_max: c.base.day_chg_max, rvol_min: c.base.rvol_min, ...triggerOf(c.base) };
  S.patterns = new Set(c.patterns); S.risk = c.risk_inr; S.notional = c.max_notional_inr; S.maxTrades = c.max_trades; S.maxPositions = c.max_positions || 0;
  S.sel = Object.fromEntries(c.plugins.filter((x) => plug(x.id)).map((x) => [x.id, x.params]));
  const firstSel = c.plugins.map((x) => plug(x.id)).find(Boolean);
  if (firstSel) { S.indGroup = firstSel.group; S.indCat = ""; }
  renderBase(); renderYears(); renderPatterns(); renderPlugins(); save(); metaSoon();
  showTab("build"); window.scrollTo({ top: 0, behavior: "smooth" });
  banner("Settings loaded into Build experiment. Change what you want, then Run.", "ok"); setTimeout(() => banner(""), 4000);
}

async function showRun(id, go = true) {
  const r = await api(`/api/lab/run/${id}`); if (!r.ok) return;
  const rec = r.data; rec.base = rec.base_id ? (await api(`/api/lab/run/${rec.base_id}`)).data : null;
  S.current = rec; renderResult(rec); renderHistory();
  if (go) { showTab("result"); window.scrollTo({ top: 0 }); $("#result-card").focus({ preventScroll: true }); }
  await resumeNews(rec);
}

function wireRuns() {
  $("#h-q").addEventListener("input", renderHistory); $("#h-star").addEventListener("change", renderHistory); $("#h-kind").addEventListener("change", renderHistory); $("#h-base").addEventListener("change", renderHistory);
  document.querySelectorAll("#history th[data-k]").forEach((th) => {
    const button = document.createElement("button"); button.type = "button"; button.className = "sort-button";
    button.innerHTML = th.innerHTML; th.replaceChildren(button);
  });
  $("#history thead").addEventListener("click", (e) => {
    const k = e.target.closest("th")?.dataset.k; if (!k) return;
    S.sort = { k, dir: S.sort.k === k ? -S.sort.dir : -1 }; renderHistory();
  });
  document.addEventListener("change", (e) => {
    if (e.target.id === "h-all") {
      if (e.target.checked) S.shown.forEach((id) => S.compare.add(id));
      else S.shown.forEach((id) => S.compare.delete(id));
      renderHistory(); return;
    }
    const id = e.target.dataset?.cmp; if (!id) return;
    e.target.checked ? S.compare.add(id) : S.compare.delete(id);
    renderHistory();
  });
  $("#btn-compare").addEventListener("click", renderCompare);
  $("#btn-del-sel").addEventListener("click", async () => {
    const ids = [...S.compare];
    if (!ids.length) return;
    if (!confirm(`Delete ${plural(ids.length, "selected run")}?\n\n(This cannot be undone. The trial counter is not reset.)`)) return;
    for (let i = 0; i < ids.length; i++) {
      banner(`Deleting… ${i + 1}/${ids.length}`, "", i / ids.length);
      await api(`/api/lab/run/${ids[i]}`, "DELETE");
      S.compare.delete(ids[i]);
    }
    if (S.current && ids.includes(S.current.id)) { S.current = null; emptyResult(); }
    banner(""); await loadRuns();
  });
  document.addEventListener("click", async (e) => {
    const row = e.target.closest("#history tbody tr[data-id]");
    if (row && !e.target.closest("[data-act], input, button, a, label")) { showRun(row.dataset.id); return; }   // a click on the row opens its result
    const b = e.target.closest("[data-act]"); if (!b) return;
    if (b.dataset.act === "aicancel") { e.preventDefault(); await api(`/api/lab/run/${b.dataset.id}/news/ai/cancel`, "POST"); return; }
    if (b.dataset.act === "aiaudit") { e.preventDefault(); aiAudit(b.dataset.id); return; }
    if (b.dataset.act === "airun") {
      const model = $("#ai-model").value, el = $("#ai-state");
      const r = await api(`/api/lab/run/${b.dataset.id}/news/ai`, "POST", { model });
      if (!r.ok) { el.textContent = r.data?.error || "could not start"; return; }
      aiPoll(b.dataset.id); return;
    }
    if (b.dataset.act === "aioff") { await api(`/api/lab/run/${b.dataset.id}/news/ai/off`, "POST"); const d = (await api(`/api/lab/run/${b.dataset.id}/news`)).data; renderNews($("#news-box"), b.dataset.id, d); await loadRuns(); return; }
    if (b.dataset.act === "newscancel") { e.preventDefault(); await api(`/api/lab/run/${b.dataset.id}/news/cancel`, "POST"); return; }
    const id = b.dataset.id, act = b.dataset.act, rec = S.runs.find((x) => x.id === id) || S.current;
    if (act === "show") showRun(id);
    else if (!rec) return;
    else if (act === "star") { await api(`/api/lab/run/${id}`, "PATCH", { starred: !rec.starred }); await loadRuns(); }
    else if (act === "load") loadSettings(rec);
    else if (act === "news") checkNews(id);
    else if (act === "del") {
      if (confirm(`Delete this run?\n\n${rec.name}\n\n(The trial counter is not reset.)`)) {
        await api(`/api/lab/run/${id}`, "DELETE"); S.compare.delete(id);
        if (S.current?.id === id) { S.current = null; emptyResult(); }
        await loadRuns();
      }
    } else if (act === "note") {
      const name = prompt("Name for this run:", rec.name); if (name === null) return;
      const notes = prompt("Note (what you were testing, what you concluded):", rec.notes || ""); if (notes === null) return;
      const r = await api(`/api/lab/run/${id}`, "PATCH", { name, notes });
      if (r.ok) { await loadRuns(); if (S.current?.id === id) { S.current = { ...S.current, ...r.data, base: S.current.base }; renderResult(S.current); resumeNews(S.current); } }
    }
  });
}

/* ---------------------------------------------------------------- compare */
function renderCompare() {
  const runs = [...S.compare].map((id) => S.runs.find((r) => r.id === id)).filter(Boolean);
  const card = $("#compare-card"); card.hidden = false;
  const rows = [["Trades", "n", (x) => x], ["Win rate", "win_pct", (x) => x + "%"], [`${G()}/trade mean`, "mean_inr", (x) => inr(x, 1)], [`${G()}/trade median`, "median_inr", (x) => inr(x, 1)], [`${G()}/trade without the 5 best`, "mean_ex_top5_inr", (x) => inr(x, 1)], [`Net ${G()} (real costs)`, "net_real_inr", (x) => inr(x)], ["Profit factor", "profit_factor", (x) => x], ["Worst drawdown", "max_drawdown_inr", (x) => inr(x)], ["Net % / trade", "net_real_pct_mean", (x) => x + "%"], ["Return on capital deployed", "return_on_deployed_pct", (x) => x + "%"]];
  const best = { mean_inr: 1, median_inr: 1, mean_ex_top5_inr: 1, net_real_inr: 1, win_pct: 1, profit_factor: 1, return_on_deployed_pct: 1, net_real_pct_mean: 1 };
  const cols = runs.map((_, i) => `hsl(${(40 + i * 137.5) % 360} 85% 62%)`);
  const W = 560, H = 160, P = 28;
  const all = runs.flatMap((r) => r.metrics.curve || []).concat([0]);
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const lines = runs.map((r, i) => { const c = r.metrics.curve || []; return c.length < 2 ? "" : `<polyline fill="none" stroke="${cols[i]}" stroke-width="2" points="${c.map((v, j) => `${(P + (W - P - 6) * j / (c.length - 1)).toFixed(1)},${(H - 16 - (H - 28) * (v - lo) / span).toFixed(1)}`).join(" ")}"/>`; }).join("");
  card.innerHTML = `<h2>Compare ${plural(runs.length, "run")} <button type="button" class="mini" id="cmp-close" style="margin-left:auto">Close</button></h2>
    <p class="hint">The best value in each row is bold. Runs on different years or bases are not directly comparable: check the base line under each name.</p>
    <div class="scroll" style="max-height:none"><table><thead><tr><th></th>${runs.map((r, i) => `<th class="num" style="color:${cols[i]};white-space:normal;min-width:160px">${runTitle(r)}<div class="exp-b" style="color:var(--faint);font-weight:400">${esc(r.config.years.join(", "))} · ${esc(baseSummary(r.config).text)}</div></th>`).join("")}</tr></thead><tbody>
    ${rows.map(([l, k, f]) => {
      const vals = runs.map((r) => r.metrics[k]).filter((v) => v != null), top = best[k] && vals.length > 1 ? Math.max(...vals) : null;
      return `<tr><td>${l}</td>${runs.map((r) => { const v = r.metrics[k]; return `<td class="num ${best[k] && v != null ? tone(k === "win_pct" || k === "profit_factor" ? 0 : v) : ""}">${v == null ? "–" : v === top ? `<b>${f(v)}</b>` : f(v)}</td>`; }).join("")}</tr>`;
    }).join("")}</tbody></table></div>
    <svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:680px;margin-top:10px" role="img" aria-label="Cumulative net profit of the compared runs"><line x1="${P}" x2="${W - 6}" y1="${H - 16 - (H - 28) * (0 - lo) / span}" y2="${H - 16 - (H - 28) * (0 - lo) / span}" stroke="#334155" stroke-dasharray="3 3"/>${lines}<text x="2" y="12">${inr(hi)}</text><text x="2" y="${H - 6}">${inr(lo)}</text></svg>
    <div class="legend">${runs.map((r, i) => `<span><b style="background:${cols[i]}"></b>${runTitle(r)}</span>`).join("")}<span>cumulative net ${G()} at real costs</span></div>`;
  $("#cmp-close").onclick = () => { card.hidden = true; };
  showTab("result");
  card.scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ---------------------------------------------------------------- test each alone */
async function scanEach() {
  if (S.busy) return;
  if (!S.years.size) { banner("Pick at least one year in step 1.", "err"); return; }
  if (triggerOf(S.base).entry_mode === "market") { banner("Test each indicator separately compares against the base trade, which has no entry when the pattern trigger is off. Tick an entry indicator (such as News and Market reaction) and run the experiment instead.", "err"); return; }
  const list = S.plugins;
  if (!confirm(`Run each of the ${list.length} indicators on its own against the base trade?\n\nThis saves ${list.length} runs and adds up to ${list.length} trials to the counter for this data window — more tries on the same data means more results that look good by luck.`)) return;
  setBusy(true);
  S.scanOpen = true; // a fresh scan always opens; collapsing afterwards is remembered
  const card = $("#scan-card"); card.hidden = false;
  showTab("result");
  const out = [];
  try {
    if (!(await ensureBuilt())) return;
    const base = await runOne([]); if (!base) return;
    for (let i = 0; i < list.length; i++) {
      const p = list[i];
      banner(`Testing each indicator alone… ${i + 1}/${list.length}: ${p.name}`, "", i / list.length);
      const rec = await runOne([{ id: p.id, params: Object.fromEntries(p.params.map((q) => [q.key, q.default])) }]);
      if (!rec) continue;
      out.push({ p, rec, d: rec.metrics.n ? rec.metrics.return_on_deployed_pct - base.metrics.return_on_deployed_pct : null });
      renderScan(base, out, i + 1 < list.length);
    }
    banner(""); renderScan(base, out, false); await loadRuns();
  } finally { setBusy(false); }
}

function renderScan(base, out, running) {
  const bm = base.metrics;
  const sorted = [...out].sort((a, b) => (b.d ?? -1e9) - (a.d ?? -1e9));
  const card = $("#scan-card");
  card.innerHTML = `<h2><button type="button" class="ghost" id="scan-head" aria-expanded="${S.scanOpen}">${S.scanOpen ? "▾" : "▸"} Each indicator alone vs the base trade</button> <small>${running ? "running…" : "done"} · base: ${bm.n} trades, ${inr(bm.mean_inr, 1)} per trade</small></h2>
    ${S.scanOpen ? `<p class="hint">Sorted by the change in return per rupee deployed (default settings of each indicator). Click a row to see its full result. With this many tests, expect a few to look good by chance — check the sample size and the median before believing one.</p>
    <div class="scroll"><table><thead><tr><th>Indicator</th><th>Type</th><th class="num">Trades</th><th class="num">Win %</th><th class="num">${G()}/trade</th><th class="num">Median</th><th class="num">Return/deployed</th><th class="num">Δ pp</th></tr></thead><tbody>
    ${sorted.map(({ p, rec, d }) => `<tr data-act="show" data-id="${rec.id}" style="cursor:pointer"><td><button type="button" class="mini" data-act="show" data-id="${rec.id}">${esc(p.name)}</button></td><td><span class="tag ${p.group}">${p.group}</span></td>
      <td class="num">${rec.metrics.n}</td><td class="num">${rec.metrics.n ? rec.metrics.win_pct + "%" : "–"}</td>
      <td class="num ${tone(rec.metrics.mean_inr)}">${rec.metrics.n ? inr(rec.metrics.mean_inr, 1) : "–"}</td>
      <td class="num ${tone(rec.metrics.median_inr)}">${rec.metrics.n ? inr(rec.metrics.median_inr, 1) : "–"}</td>
      <td class="num">${rec.metrics.n ? pct(rec.metrics.return_on_deployed_pct, 3) : "–"}</td>
      <td class="num ${d == null ? "dim" : tone(d)}">${d == null ? "–" : sgn(d, (x) => x.toFixed(3))}</td></tr>`).join("")}</tbody></table></div>` : ""}`;
  $("#scan-head").onclick = () => { S.scanOpen = !S.scanOpen; lsSet(SCAN_KEY, S.scanOpen ? "1" : "0"); renderScan(base, out, running); };
}

/* ---------------------------------------------------------------- boot */
(async function boot() {
  restore();
  wireTabs();
  if (lsGet("btlab.help") === null || lsGet("btlab.help") === "1") { $("#help").hidden = false; $("#btn-help").setAttribute("aria-expanded", "true"); }
  showTab(location.hash.slice(1) || "build", { push: false });
  const r = await api("/api/lab/meta");
  if (!r.ok) { banner("Could not load the lab: " + (r.data?.error || r.status), "err"); return; }
  const first = S.market;
  if (first !== "NSE") { const r2 = await api(`/api/lab/meta?market=${first}`); if (r2.ok) r.data = r2.data; }
  S.meta = r.data; S.plugins = r.data.plugins;
  if (!S.patterns.size) S.patterns = new Set(r.data.patterns);
  S.patterns = new Set([...S.patterns].filter((p) => r.data.patterns.includes(p)));
  S.sel = Object.fromEntries(Object.entries(S.sel).filter(([id]) => S.plugins.some((p) => p.id === id)));
  const have = new Set(r.data.years.map((y) => y.year));
  S.years = new Set([...S.years].filter((y) => have.has(y)));
  if (!S.years.size && r.data.years.length) S.years.add(r.data.years[r.data.years.length - 1].year);
  renderBase(); renderYears(); renderPatterns(); renderPlugins(); wireBuild(); wireRuns(); paintGlyph();
  await loadRuns(); metaSoon();
  if (pendingRun) {                                     // arrived from a trade-chart page: open that run, on its own market
    const r0 = await api(`/api/lab/run/${pendingRun}`);
    if (r0.ok && (r0.data.config.base.market || "NSE") !== S.market) await switchMarket(r0.data.config.base.market);
    await showRun(pendingRun);
    pendingRun = null;
  } else if (S.tab === "result" && !S.current && S.runs.length) showRun(S.runs[0].id, false);   // a bookmarked #result opens the latest run
  // A refresh drops the banner and the pending Run; the build itself kept running on the server. Re-attach: Run
  // re-joins the running build (start_build de-dupes on rule+years), shows the banner, then runs.
  const running = ((await api("/api/lab/jobs")).data || []).find((j) => j.state === "running");
  if (running && running.years.every((y) => S.years.has(y))) applyNow();
})();
