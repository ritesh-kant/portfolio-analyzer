"use strict";
/* Backtest lab UI. No build step: plain DOM, one file. Talks to /api/lab/* (btlab/server.py). */

const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let GLYPH = "₹";                      // currency of the market being shown (₹ NSE, $ US)
const G = () => GLYPH;
const inr = (x, d = 0) => (x == null ? "–" : (x < 0 ? "−" : "") + GLYPH + Math.abs(x).toLocaleString(GLYPH === "$" ? "en-US" : "en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
const sgn = (x, f) => (x == null ? "–" : (x > 0 ? "+" : x < 0 ? "−" : "") + f(Math.abs(x)));
const pct = (x, d = 2) => (x == null ? "–" : x.toFixed(d) + "%");
const tone = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : "dim");
const SEL_KEY = "btlab.state.v1";
const SCAN_KEY = "btlab.scan.open.v1";
function scanWantOpen() {
  try { return localStorage.getItem(SCAN_KEY) === "1"; } catch { return false; }
}
function saveScanOpen(open) {
  try { localStorage.setItem(SCAN_KEY, open ? "1" : "0"); } catch { /* private mode */ }
}

const MARKET_DEFAULTS = {
  NSE: { glyph: "₹", risk: 500, notional: 50000, presets: { strict: { day_chg_min: 4, day_chg_max: 8, rvol_min: 3 }, wide: { day_chg_min: 3, day_chg_max: 15, rvol_min: 2 } } },
  US: { glyph: "$", risk: 50, notional: 5000, presets: { strict: { day_chg_min: 10, day_chg_max: 50, rvol_min: 3 }, wide: { day_chg_min: 5, day_chg_max: 100, rvol_min: 2 } } },
};
const presets = () => MARKET_DEFAULTS[S.market].presets;
const S = {
  market: "NSE", byMarket: {},
  meta: null, plugins: [], runs: [], sel: {}, years: new Set(), patterns: new Set(),
  base: { ...MARKET_DEFAULTS.NSE.presets.strict }, risk: 500, notional: 50000, maxTrades: 0,
  current: null, compare: new Set(), shown: [], sort: { k: "created_at", dir: -1 }, busy: false,
  scanOpen: scanWantOpen(),
};

/* ---------------------------------------------------------------- api */
async function api(path, method = "GET", body) {
  const res = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  let data = null;
  try { data = await res.json(); } catch { /* not json */ }
  return { ok: res.ok, status: res.status, data };
}

function banner(msg, kind = "", progress = null) {
  const b = $("#banner");
  if (!msg) { b.hidden = true; return; }
  b.hidden = false;
  b.className = kind;
  b.innerHTML = esc(msg) + (progress != null ? `<div class="bar2"><i style="width:${Math.round(progress * 100)}%"></i></div>` : "");
}

/* ---------------------------------------------------------------- state persistence */
function stash() {
  S.byMarket[S.market] = { years: [...S.years], base: S.base, risk: S.risk, notional: S.notional, maxTrades: S.maxTrades };
}
function save() {
  stash();
  try {
    localStorage.setItem(SEL_KEY, JSON.stringify({ market: S.market, byMarket: S.byMarket, patterns: [...S.patterns], sel: S.sel }));
  } catch { /* private mode */ }
}
function useMarket(m) {
  // the form values this market last had, or its own defaults
  S.market = m; GLYPH = MARKET_DEFAULTS[m].glyph;
  const d = S.byMarket[m] || {};
  S.years = new Set(d.years || []); S.base = { ...MARKET_DEFAULTS[m].presets.strict, ...(d.base || {}) };
  S.risk = d.risk ?? MARKET_DEFAULTS[m].risk; S.notional = d.notional ?? MARKET_DEFAULTS[m].notional; S.maxTrades = d.maxTrades ?? 0;
}
function restore() {
  try {
    const o = JSON.parse(localStorage.getItem(SEL_KEY) || "null");
    if (!o) return;
    S.patterns = new Set(o.patterns || []); S.sel = o.sel || {};
    S.byMarket = o.byMarket || { NSE: { years: o.years, base: o.base, risk: o.risk, notional: o.notional } };   // v1 had NSE only
    useMarket(MARKET_DEFAULTS[o.market] ? o.market : "NSE");
  } catch { /* ignore */ }
}
function paintGlyph() {
  document.querySelectorAll(".g").forEach((e) => { e.textContent = GLYPH; });
  $("#mkt").innerHTML = S.meta.markets.map((m) => `<span class="chip ${m.id === S.market ? "on" : ""}" data-m="${m.id}" title="${esc(m.label)}">${esc(m.id)} <small>${esc(m.glyph)}</small></span>`).join("");
  document.querySelectorAll("[data-preset]").forEach((b) => {
    const p = presets()[b.dataset.preset];
    b.textContent = `${b.dataset.preset === "strict" ? "Strict" : "Wider"} ${p.day_chg_min}–${p.day_chg_max}% · RVOL ${p.rvol_min}`;
  });
}

async function switchMarket(m) {
  if (m === S.market || S.busy) return;
  save(); useMarket(m);
  const r = await api(`/api/lab/meta?market=${m}`);
  if (!r.ok) { banner("Could not load the " + m + " data: " + (r.data?.error || r.status), "err"); return; }
  S.meta = r.data;
  const have = new Set(r.data.years.map((y) => y.year));
  S.years = new Set([...S.years].filter((y) => have.has(y)));
  if (!S.years.size && r.data.years.length) S.years.add(r.data.years[r.data.years.length - 1].year);
  S.current = null; S.compare.clear(); S.shown = [];
  $("#result-card").className = "card empty"; $("#result-card").innerHTML = "Pick years, tick indicators and press Apply."; $("#scan-card").hidden = true;
  renderBase(); renderYears(); renderPatterns(); renderPlugins(); paintGlyph(); save();
  await loadRuns(); metaSoon();
  if (S.runs.length) showRun(S.runs[0].id, false);
}

/* ---------------------------------------------------------------- left column */
function renderYears() {
  const el = $("#years");
  el.innerHTML = S.meta.years.map((y) =>
    `<span class="chip ${S.years.has(y.year) ? "on" : ""}" data-y="${y.year}" title="${y.built ? `${y.n_candidates} base candidates` : "needs a one-time build (~20 s)"}">` +
    `<span class="dot ${y.built ? "ready" : ""}"></span>${y.year}</span>`).join("") +
    `<span class="chip" data-all="1">all</span>`;
  const need = S.meta.years.filter((y) => S.years.has(y.year) && !y.built).map((y) => y.year);
  $("#years-hint").textContent = need.length
    ? `${need.join(", ")} still need a one-time build of the base data (≈20 s each) — it runs automatically when you press Apply.`
    : "● = base data ready. Pick one year or several; results are pooled.";
}

function pluginRow(p) {
  const on = p.id in S.sel;
  const params = p.params.map((q) => {
    const v = (S.sel[p.id] || {})[q.key] ?? q.default;
    let input;
    if (q.kind === "select") input = `<select data-p="${p.id}" data-k="${q.key}">${q.options.map((o) => `<option ${o === v ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
    else if (q.kind === "time") input = `<input type="time" data-p="${p.id}" data-k="${q.key}" value="${esc(v)}">`;
    else input = `<input type="number" data-p="${p.id}" data-k="${q.key}" value="${v}" ${q.min != null ? `min="${q.min}"` : ""} ${q.max != null ? `max="${q.max}"` : ""} step="${q.step ?? (q.kind === "int" ? 1 : "any")}">`;
    return `<label>${esc(q.label)}${input}</label>`;
  }).join("");
  return `<div class="plug ${on ? "on" : ""}" data-id="${p.id}">
    <label class="head"><input type="checkbox" data-id="${p.id}" ${on ? "checked" : ""}>
      <span><span class="nm">${esc(p.name)}</span><br><span class="ds">${esc(p.desc)}</span>
      ${p.history ? `<br><span class="hist">ⓘ ${esc(p.history)}</span>` : ""}</span></label>
    ${p.params.length ? `<div class="params">${params}</div>` : ""}</div>`;
}

function renderPlugins() {
  for (const g of ["entry", "exit"]) {
    $(`#${g}-list`).innerHTML = S.plugins.filter((p) => p.group === g).map(pluginRow).join("");
  }
  renderSel();
}

function renderSel() {
  const ids = Object.keys(S.sel);
  const ent = ids.filter((i) => S.plugins.find((p) => p.id === i)?.group === "entry").length;
  const ext = ids.length - ent;
  $("#selsum").innerHTML = ids.length
    ? `<b>${ids.length}</b> selected — <span style="color:var(--entry)">${ent} entry</span> · <span style="color:var(--exit)">${ext} exit</span>`
    : "Nothing selected — Apply runs the plain base trade.";
  for (const g of ["entry", "exit"]) {
    const n = ids.filter((i) => S.plugins.find((p) => p.id === i)?.group === g).length;
    const h = $(`#sec-${g} h2`);
    h.querySelector(".cnt")?.remove();
    if (n) h.insertAdjacentHTML("beforeend", `<small class="cnt" style="color:var(--${g})">${n} on</small>`);
  }
  save();
}

function renderPatterns() {
  $("#patterns").innerHTML = S.meta.patterns.map((p) => `<span class="chip ${S.patterns.has(p) ? "on" : ""}" data-pat="${p}">${p.replace(/_/g, " ")}</span>`).join("");
}

function renderBase() {
  $("#base-text").innerHTML = S.meta.base_rule_text.map((t) => `<li>${esc(t)}</li>`).join("");
  $("#b-chg-min").value = S.base.day_chg_min; $("#b-chg-max").value = S.base.day_chg_max; $("#b-rvol").value = S.base.rvol_min;
  $("#b-maxtr").value = S.maxTrades; $("#b-risk").value = S.risk; $("#b-notional").value = S.notional;
}

function readBase() {
  const pz = presets().strict;
  S.base = { day_chg_min: +$("#b-chg-min").value || pz.day_chg_min, day_chg_max: +$("#b-chg-max").value || pz.day_chg_max, rvol_min: +$("#b-rvol").value || 0 };
  S.maxTrades = Math.max(0, +$("#b-maxtr").value || 0); S.risk = +$("#b-risk").value || MARKET_DEFAULTS[S.market].risk; S.notional = +$("#b-notional").value || MARKET_DEFAULTS[S.market].notional;
  save();
}

let metaTimer = null;
function metaSoon() {
  clearTimeout(metaTimer);
  metaTimer = setTimeout(async () => {
    const r = await api("/api/lab/meta", "POST", { market: S.market, base: S.base, years: S.meta.years.slice(0, 1).map((y) => y.year) });
    if (r.ok) { S.meta.years = r.data.years; S.meta.base_rule_text = r.data.base_rule_text; renderYears(); $("#base-text").innerHTML = S.meta.base_rule_text.map((t) => `<li>${esc(t)}</li>`).join(""); }
  }, 350);
}

function wireLeft() {
  $("#years").addEventListener("click", (e) => {
    const c = e.target.closest(".chip"); if (!c) return;
    if (c.dataset.all) { const all = S.years.size === S.meta.years.length; S.years = new Set(all ? [] : S.meta.years.map((y) => y.year)); }
    else { const y = +c.dataset.y; S.years.has(y) ? S.years.delete(y) : S.years.add(y); }
    renderYears(); save();
  });
  $("#patterns").addEventListener("click", (e) => {
    const c = e.target.closest(".chip"); if (!c) return;
    const p = c.dataset.pat; S.patterns.has(p) ? S.patterns.delete(p) : S.patterns.add(p);
    if (!S.patterns.size) S.patterns.add(p);
    renderPatterns(); save();
  });
  $("#base-settings").addEventListener("input", () => { readBase(); metaSoon(); });
  $("#base-settings").addEventListener("click", (e) => {
    const b = e.target.closest("[data-preset]"); if (!b) return;
    S.base = { ...presets()[b.dataset.preset] }; renderBase(); save(); metaSoon();
  });
  $("#mkt").addEventListener("click", (e) => { const c = e.target.closest("[data-m]"); if (c) switchMarket(c.dataset.m); });
  for (const g of ["entry", "exit"]) {
    const root = $(`#${g}-list`);
    root.addEventListener("change", (e) => {
      const t = e.target;
      if (t.matches('input[type=checkbox]')) {
        const id = t.dataset.id; const p = S.plugins.find((x) => x.id === id);
        if (t.checked) {
          if (p.exclusive) for (const o of S.plugins) if (o.id !== id && o.exclusive === p.exclusive) delete S.sel[o.id];
          S.sel[id] = Object.fromEntries(p.params.map((q) => [q.key, q.default]));
        } else delete S.sel[id];
        renderPlugins();
      } else if (t.dataset.p) {
        const q = S.plugins.find((x) => x.id === t.dataset.p).params.find((x) => x.key === t.dataset.k);
        (S.sel[t.dataset.p] ||= {})[t.dataset.k] = q.kind === "select" || q.kind === "time" ? t.value : +t.value;
        save();
      }
    });
  }
  $("#btn-clear").addEventListener("click", () => { S.sel = {}; renderPlugins(); });
  $("#btn-apply").addEventListener("click", () => applyNow());
  $("#btn-scan").addEventListener("click", () => scanEach());
}

/* ---------------------------------------------------------------- apply */
function payload(plugins = null) {
  return {
    market: S.market, years: [...S.years].sort(), base: S.base, patterns: [...S.patterns],
    risk_inr: S.risk, max_notional_inr: S.notional, max_trades: S.maxTrades,
    plugins: plugins ?? Object.entries(S.sel).map(([id, params]) => ({ id, params })),
  };
}

async function ensureBuilt() {
  const rule = { base: S.base, years: [...S.years] };
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
    banner(`Building base data for ${j.current_year ?? ""}… ${j.done}/${j.total} symbols (one-time, ~20 s per year)`, "", j.total ? j.done / j.total : 0);
    if (j.state === "error") { banner("Build failed: " + j.error, "err"); return false; }
    if (j.state === "done") break;
  }
  r = await api("/api/lab/meta", "POST", { market: S.market, base: S.base, years: S.meta.years.slice(0, 1).map((y) => y.year) });
  if (r.ok) { S.meta.years = r.data.years; renderYears(); }
  banner("");
  return true;
}

async function runOne(plugins) {
  const r = await api("/api/lab/apply", "POST", payload(plugins));
  if (r.status === 409) { if (!(await ensureBuilt())) return null; return runOne(plugins); }
  if (!r.ok) { banner(r.data?.error || `error ${r.status}`, "err"); return null; }
  return r.data;
}

async function applyNow() {
  if (S.busy) return;
  if (!S.years.size) { banner("Pick at least one year in step 1.", "err"); return; }
  S.busy = true; $("#btn-apply").disabled = true; $("#btn-apply").textContent = "Running…";
  try {
    if (!(await ensureBuilt())) return;
    banner("Running…", "", null);
    const rec = await runOne();
    banner("");
    if (rec) { S.current = rec; renderResult(rec); await loadRuns(); $("#result-card").scrollIntoView({ behavior: "smooth", block: "start" }); }
  } finally { S.busy = false; $("#btn-apply").disabled = false; $("#btn-apply").textContent = "Apply ▶"; }
}

/* ---------------------------------------------------------------- result card */
const EXIT_COLORS = { target: "#2dd4bf", stop: "#fb7185", eod: "#64748b", ema9_break: "#fbbf24", ema20_break: "#f59e0b", macd_fade: "#a78bfa", vwap_break: "#60a5fa", trail_stop: "#f472b6", breakeven_stop: "#94a3b8", checkpoint_stop: "#fb923c", resistance_reject: "#e879f9", volume_climax: "#38bdf8", stale: "#7c8aa0" };

function exitBar(mix, n) {
  const bars = Object.entries(mix).map(([k, v]) => `<i style="width:${(100 * v / n).toFixed(2)}%;background:${EXIT_COLORS[k] || "#475569"}" title="${esc(k)}: ${v}"></i>`).join("");
  const leg = Object.entries(mix).map(([k, v]) => `<span><b style="background:${EXIT_COLORS[k] || "#475569"}"></b>${esc(k.replace(/_/g, " "))} ${v} (${(100 * v / n).toFixed(0)}%)</span>`).join("");
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

function verdict(rec) {
  const m = rec.metrics, b = rec.base?.metrics, vs = rec.vs_base || {};
  if (!m.n) return { t: "No trades — the selection filtered every candidate out.", w: true };
  const lines = []; let warn = false;
  if (!b) {
    lines.push(`The base trade took <b>${m.n}</b> trades and ${m.mean_inr >= 0 ? "made" : "lost"} <b class="${tone(m.mean_inr)}">${inr(Math.abs(m.mean_inr), 1)}</b> per trade after real costs (median ${inr(m.median_inr, 1)}). This is the line every indicator has to beat.`);
  } else {
    const d = m.mean_inr - b.mean_inr;
    lines.push(`Versus the base trade, ${G()} per trade moved <b class="${tone(d)}">${sgn(d, (x) => inr(x, 1))}</b> (${inr(m.mean_inr, 1)} vs ${inr(b.mean_inr, 1)}) on ${m.n} trades (base ${b.n}).`);
    if (vs.paired) lines.push(`On the ${vs.paired.n} trades both took, the exits changed the result by ${inr(vs.paired.mean_change_inr, 1)} per trade (${vs.paired.changed} trades changed, approx. p = ${vs.paired.p}).`);
    if (vs.removed && vs.overlap && vs.overlap.same > 0) {
      const keptMean = (b.net_real_inr - vs.removed.net_real_inr) / (b.n - vs.removed.n);
      lines.push(`The ${vs.removed.n} base trades it dropped averaged ${inr(vs.removed.mean_inr, 1)}; the ${vs.overlap.same} it kept averaged ${inr(keptMean, 1)}${vs.kept_vs_removed ? ` (approx. p = ${vs.kept_vs_removed.p})` : ""}. ${vs.overlap.new ? `It also took ${vs.overlap.new} trade${vs.overlap.new === 1 ? "" : "s"} the base did not (a vetoed candidate lets a later one through).` : ""}`);
    }
    const p = (vs.paired || vs.kept_vs_removed || {}).p;
    if (p != null && p > 0.1) { lines.push("Not statistically distinguishable from no change."); warn = true; }
  }
  if (m.n < 100) { lines.push(`Small sample (${m.n} trades) — treat as a hint, not a result.`); warn = true; }
  if (m.mean_ex_top5_inr != null && Math.sign(m.mean_ex_top5_inr) !== Math.sign(m.mean_inr)) { lines.push("The mean flips sign once the 5 best trades are removed: a few trades carry this result."); warn = true; }
  if (rec.trial_n >= 5) { lines.push(`This is trial #${rec.trial_n} on this data window. The more variants you try on the same data, the more of them look good by luck.`); warn = true; }
  return { t: lines.join(" "), w: warn };
}

function tagsFor(rec) {
  const ps = rec.config.plugins;
  if (!ps.length) return `<span class="tag base">base trade only</span>`;
  return ps.map((x) => {
    const p = S.plugins.find((q) => q.id === x.id); if (!p) return "";
    const prm = Object.values(x.params);
    return `<span class="tag ${p.group}" title="${esc(p.desc)}">${esc(p.name)}${prm.length ? " · " + esc(prm.join(" / ")) : ""}</span>`;
  }).join("");
}

function renderResult(rec) {
  const m = rec.metrics, b = rec.base?.metrics || null, v = verdict(rec);
  const card = $("#result-card"); card.className = "card";
  card.innerHTML = `
    <h2>Result <small>${esc(rec.name)}${rec.cached ? " · already run, loaded from the saved result" : " · saved"}</small></h2>
    <div class="tags">${tagsFor(rec)}</div>
    <p class="hint">${esc(rec.describe)}</p>
    <div class="verdict ${v.w ? "warn" : ""}">${v.t}</div>
    ${m.n ? `<div class="two">
      <div><table><thead><tr><th>Metric</th><th class="num">This run</th>${b ? `<th class="num">Base</th><th class="num">Change</th>` : ""}</tr></thead><tbody>${kpiRows(m, b)}</tbody></table></div>
      <div>${curveSvg(m.curve, b?.curve)}
        <h3 style="font-size:13px;margin:14px 0 4px">How the trades ended</h3>${exitBar(m.exit_mix, m.n)}
        <h3 style="font-size:13px;margin:14px 0 4px">By year</h3>
        <table><thead><tr><th>Year</th><th class="num">Trades</th><th class="num">Win %</th><th class="num">${G()}/trade</th><th class="num">Net ${G()}</th></tr></thead><tbody>
        ${m.by_year.map((y) => `<tr><td>${y.year}</td><td class="num">${y.n}</td><td class="num">${y.win_pct}%</td><td class="num ${tone(y.mean_inr)}">${inr(y.mean_inr, 1)}</td><td class="num ${tone(y.net_real_inr)}">${inr(y.net_real_inr)}</td></tr>`).join("")}
        </tbody></table></div></div>` : ""}
    <div class="btns" style="justify-content:flex-start;margin-top:12px">
      ${m.n ? `<a href="/lab/run/${rec.id}" target="_blank"><button class="primary" type="button">Open every trade on a candle chart ↗</button></a>` : ""}
      <button class="ghost" type="button" data-act="load" data-id="${rec.id}">Load these settings</button>
      <button class="ghost" type="button" data-act="note" data-id="${rec.id}">✎ Name / note</button>
    </div>
    ${rec.notes ? `<p class="hint">Note: ${esc(rec.notes)}</p>` : ""}`;
}

/* ---------------------------------------------------------------- history */
async function loadRuns() {
  const r = await api("/api/lab/runs"); if (r.ok) { S.runs = r.data.filter((x) => (x.config.base.market || "NSE") === S.market); renderHistory(); }
}

function rowDelta(r) {
  const base = r.base_id ? S.runs.find((x) => x.id === r.base_id) : null;
  return base ? r.metrics.mean_inr - base.metrics.mean_inr : null;
}

function renderHistory() {
  const q = $("#h-q").value.trim().toLowerCase(), starOnly = $("#h-star").checked, kind = $("#h-kind").value;
  let rows = S.runs.filter((r) => {
    if (starOnly && !r.starred) return false;
    const groups = r.config.plugins.map((x) => S.plugins.find((p) => p.id === x.id)?.group);
    if (kind === "base" && r.config.plugins.length) return false;
    if (kind === "entry" && !groups.includes("entry")) return false;
    if (kind === "exit" && !groups.includes("exit")) return false;
    return !q || (r.name + " " + r.notes + " " + r.config.years.join(" ") + " " + r.describe).toLowerCase().includes(q);
  });
  const key = { created_at: (r) => r.created_at, name: (r) => r.name, years: (r) => r.config.years.join(), n: (r) => r.metrics.n || 0, win: (r) => r.metrics.win_pct ?? -1, mean: (r) => r.metrics.mean_inr ?? -1e9, median: (r) => r.metrics.median_inr ?? -1e9, net: (r) => r.metrics.net_real_inr ?? -1e9, delta: (r) => rowDelta(r) ?? -1e9 }[S.sort.k];
  rows = rows.sort((a, b) => ((b.starred ? 1 : 0) - (a.starred ? 1 : 0))
    || ((key(a) > key(b) ? 1 : key(a) < key(b) ? -1 : 0) * S.sort.dir)
    || (a.created_at > b.created_at ? -1 : 1));
  S.shown = rows.map((r) => r.id);
  const allBox = $("#h-all");
  if (allBox) {
    allBox.checked = S.shown.length > 0 && S.shown.every((id) => S.compare.has(id));
    allBox.indeterminate = !allBox.checked && S.shown.some((id) => S.compare.has(id));
  }
  $("#hist-count").textContent = `${S.runs.length} saved`;
  $("#history tbody").innerHTML = rows.map((r) => {
    const m = r.metrics, d = rowDelta(r), yrs = r.config.years;
    return `<tr data-id="${r.id}" class="${S.current?.id === r.id ? "shown" : ""}">
      <td><input type="checkbox" data-cmp="${r.id}" ${S.compare.has(r.id) ? "checked" : ""} title="select to compare"></td>
      <td><span class="star ${r.starred ? "on" : ""}" data-act="star" data-id="${r.id}" title="star">${r.starred ? "★" : "☆"}</span></td>
      <td class="dim">${esc(r.created_at.replace("T", " ").slice(5, 16))}</td>
      <td><div class="tags">${tagsFor(r)}</div>${r.notes ? `<div class="hint">${esc(r.notes)}</div>` : ""}</td>
      <td>${yrs.length > 2 ? yrs[0] + "–" + yrs[yrs.length - 1] : yrs.join(", ")}</td>
      <td class="num">${m.n ?? 0}</td><td class="num">${m.n ? m.win_pct + "%" : "–"}</td>
      <td class="num ${tone(m.mean_inr)}">${m.n ? inr(m.mean_inr, 1) : "–"}</td>
      <td class="num ${tone(m.median_inr)}">${m.n ? inr(m.median_inr, 1) : "–"}</td>
      <td class="num ${tone(m.net_real_inr)}">${m.n ? inr(m.net_real_inr) : "–"}</td>
      <td class="num ${d == null ? "dim" : tone(d)}">${d == null ? (r.config.plugins.length ? "–" : "base") : sgn(d, (x) => inr(x, 1))}</td>
      <td class="acts"><button class="mini" data-act="show" data-id="${r.id}">View</button>
        <button class="mini" data-act="load" data-id="${r.id}" title="load these settings into the form">Load</button>
        <button class="mini" data-act="del" data-id="${r.id}" title="delete this run">✕</button></td></tr>`;
  }).join("") || `<tr><td colspan="12" class="dim">No saved runs match.</td></tr>`;
  $("#btn-compare").disabled = S.compare.size < 2;
  const delBtn = $("#btn-del-sel");
  if (delBtn) { delBtn.disabled = S.compare.size < 1; delBtn.textContent = `Delete selected${S.compare.size ? ` (${S.compare.size})` : ""}`; }
}

function loadSettings(rec) {
  const c = rec.config;
  S.years = new Set(c.years); S.base = { day_chg_min: c.base.day_chg_min, day_chg_max: c.base.day_chg_max, rvol_min: c.base.rvol_min };
  S.patterns = new Set(c.patterns); S.risk = c.risk_inr; S.notional = c.max_notional_inr; S.maxTrades = c.max_trades;
  S.sel = Object.fromEntries(c.plugins.map((x) => [x.id, x.params]));
  renderBase(); renderYears(); renderPatterns(); renderPlugins(); save(); metaSoon();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function showRun(id, scroll = true) {
  const r = await api(`/api/lab/run/${id}`); if (!r.ok) return;
  const rec = r.data; rec.base = rec.base_id ? (await api(`/api/lab/run/${rec.base_id}`)).data : null;
  S.current = rec; renderResult(rec); renderHistory(); if (scroll) $("#result-card").scrollIntoView({ behavior: "smooth", block: "start" });
}

function wireRight() {
  $("#h-q").addEventListener("input", renderHistory); $("#h-star").addEventListener("change", renderHistory); $("#h-kind").addEventListener("change", renderHistory);
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
    if (!confirm(`Delete ${ids.length} selected run${ids.length === 1 ? "" : "s"}?\n\n(This cannot be undone. The trial counter is not reset.)`)) return;
    banner(`Deleting ${ids.length} run${ids.length === 1 ? "" : "s"}…`);
    for (let i = 0; i < ids.length; i++) {
      banner(`Deleting… ${i + 1}/${ids.length}`, "", i / ids.length);
      await api(`/api/lab/run/${ids[i]}`, "DELETE");
      S.compare.delete(ids[i]);
    }
    if (S.current && ids.includes(S.current.id)) S.current = null;
    banner(""); await loadRuns();
  });
  document.addEventListener("click", async (e) => {
    const row = e.target.closest("#history tbody tr[data-id]");
    if (row && !e.target.closest("[data-act], input, button, a, label")) { showRun(row.dataset.id); return; }
    const b = e.target.closest("[data-act]"); if (!b) return;
    const id = b.dataset.id, act = b.dataset.act, rec = S.runs.find((x) => x.id === id) || S.current;
    if (act === "star") { await api(`/api/lab/run/${id}`, "PATCH", { starred: !rec.starred }); await loadRuns(); }
    else if (act === "load") loadSettings(rec);
    else if (act === "show") showRun(id);
    else if (act === "del") { if (confirm(`Delete this run?\n\n${rec.name}\n\n(The trial counter is not reset.)`)) { await api(`/api/lab/run/${id}`, "DELETE"); S.compare.delete(id); await loadRuns(); } }
    else if (act === "note") {
      const name = prompt("Name for this run:", rec.name); if (name === null) return;
      const notes = prompt("Note (what you were testing, what you concluded):", rec.notes || ""); if (notes === null) return;
      const r = await api(`/api/lab/run/${id}`, "PATCH", { name, notes });
      if (r.ok) { await loadRuns(); if (S.current?.id === id) { S.current = { ...S.current, ...r.data }; renderResult(S.current); } }
    }
  });
}

function renderCompare() {
  const runs = [...S.compare].map((id) => S.runs.find((r) => r.id === id)).filter(Boolean);
  const card = $("#compare-card"); card.hidden = false;
  const rows = [["Trades", "n", (x) => x], ["Win rate", "win_pct", (x) => x + "%"], [`${G()}/trade mean`, "mean_inr", (x) => inr(x, 1)], [`${G()}/trade median`, "median_inr", (x) => inr(x, 1)], [`${G()}/trade ex top 5`, "mean_ex_top5_inr", (x) => inr(x, 1)], [`Net ${G()} (real)`, "net_real_inr", (x) => inr(x)], ["Profit factor", "profit_factor", (x) => x], ["Worst drawdown", "max_drawdown_inr", (x) => inr(x)], ["Net % / trade", "net_real_pct_mean", (x) => x + "%"], ["Return on capital deployed", "return_on_deployed_pct", (x) => x + "%"]];
  const cols = runs.map((_, i) => `hsl(${(40 + i * 137.5) % 360} 85% 62%)`);
  const W = 560, H = 160, P = 28;
  const all = runs.flatMap((r) => r.metrics.curve || []).concat([0]);
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const lines = runs.map((r, i) => { const c = r.metrics.curve || []; return c.length < 2 ? "" : `<polyline fill="none" stroke="${cols[i]}" stroke-width="2" points="${c.map((v, j) => `${(P + (W - P - 6) * j / (c.length - 1)).toFixed(1)},${(H - 16 - (H - 28) * (v - lo) / span).toFixed(1)}`).join(" ")}"/>`; }).join("");
  card.innerHTML = `<h2>Compare <small><button class="mini" id="cmp-close">close</button></small></h2>
    <div class="scroll" style="max-height:none"><table><thead><tr><th></th>${runs.map((r, i) => `<th class="num" style="color:${cols[i]}">${esc(r.name).slice(0, 60)}</th>`).join("")}</tr></thead><tbody>
    ${rows.map(([l, k, f]) => `<tr><td>${l}</td>${runs.map((r) => `<td class="num">${r.metrics[k] == null ? "–" : f(r.metrics[k])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
    <svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:680px;margin-top:10px"><line x1="${P}" x2="${W - 6}" y1="${H - 16 - (H - 28) * (0 - lo) / span}" y2="${H - 16 - (H - 28) * (0 - lo) / span}" stroke="#334155" stroke-dasharray="3 3"/>${lines}<text x="2" y="12">${inr(hi)}</text><text x="2" y="${H - 6}">${inr(lo)}</text></svg>`;
  $("#cmp-close").onclick = () => { card.hidden = true; };
  card.scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ---------------------------------------------------------------- test each alone */
async function scanEach() {
  if (S.busy) return;
  if (!S.years.size) { banner("Pick at least one year in step 1.", "err"); return; }
  const list = S.plugins;
  if (!confirm(`Run each of the ${list.length} indicators on its own against the base trade?\n\nThis saves ${list.length} runs and adds up to ${list.length} trials to the counter for this data window — more tries on the same data means more results that look good by luck.`)) return;
  S.busy = true; $("#btn-scan").disabled = true; $("#btn-apply").disabled = true;
  S.scanOpen = true; // a fresh scan always opens; collapsing afterwards is remembered
  const card = $("#scan-card"); card.hidden = false;
  const out = [];
  try {
    if (!(await ensureBuilt())) return;
    const base = await runOne([]); if (!base) return;
    for (let i = 0; i < list.length; i++) {
      const p = list[i];
      banner(`Testing each indicator alone… ${i + 1}/${list.length}: ${p.name}`, "", i / list.length);
      const rec = await runOne([{ id: p.id, params: Object.fromEntries(p.params.map((q) => [q.key, q.default])) }]);
      if (!rec) continue;
      out.push({ p, rec, d: rec.metrics.n ? rec.metrics.mean_inr - base.metrics.mean_inr : null });
      renderScan(base, out, i + 1 < list.length);
    }
    banner(""); renderScan(base, out, false); await loadRuns();
  } finally { S.busy = false; $("#btn-scan").disabled = false; $("#btn-apply").disabled = false; }
}

function renderScan(base, out, running) {
  const bm = base.metrics;
  const sorted = [...out].sort((a, b) => (b.d ?? -1e9) - (a.d ?? -1e9));
  const card = $("#scan-card");
  card.innerHTML = `<h2 style="cursor:pointer" id="scan-head">${S.scanOpen ? "▾" : "▸"} Each indicator alone vs the base trade <small>${running ? "running…" : "done"} · base: ${bm.n} trades, ${inr(bm.mean_inr, 1)} per trade · click to ${S.scanOpen ? "collapse" : "expand"}</small></h2>
    ${S.scanOpen ? `<p class="hint">Sorted by the change in ${G()} per trade (default settings of each indicator). Click a row to see the full result. With this many tests, expect a few to look good by chance — check the sample size and the median before believing one.</p>
    <div class="scroll"><table><thead><tr><th>Indicator</th><th>type</th><th class="num">Trades</th><th class="num">Win %</th><th class="num">${G()}/trade</th><th class="num">Median</th><th class="num">Δ ${G()}/trade</th></tr></thead><tbody>
    ${sorted.map(({ p, rec, d }) => `<tr data-act="show" data-id="${rec.id}" style="cursor:pointer"><td>${esc(p.name)}</td><td class="${p.group === "entry" ? "" : ""}"><span class="tag ${p.group}">${p.group}</span></td>
      <td class="num">${rec.metrics.n}</td><td class="num">${rec.metrics.n ? rec.metrics.win_pct + "%" : "–"}</td>
      <td class="num ${tone(rec.metrics.mean_inr)}">${rec.metrics.n ? inr(rec.metrics.mean_inr, 1) : "–"}</td>
      <td class="num ${tone(rec.metrics.median_inr)}">${rec.metrics.n ? inr(rec.metrics.median_inr, 1) : "–"}</td>
      <td class="num ${d == null ? "dim" : tone(d)}">${d == null ? "–" : sgn(d, (x) => inr(x, 1))}</td></tr>`).join("")}</tbody></table></div>` : ""}`;
  $("#scan-head").onclick = () => { S.scanOpen = !S.scanOpen; saveScanOpen(S.scanOpen); renderScan(base, out, running); };
}

/* ---------------------------------------------------------------- boot */
(async function boot() {
  restore();
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
  renderBase(); renderYears(); renderPatterns(); renderPlugins(); wireLeft(); wireRight(); paintGlyph();
  await loadRuns(); metaSoon();
  if (S.runs.length) { const last = S.runs[0]; const full = (await api(`/api/lab/run/${last.id}`)).data; full.base = full.base_id ? (await api(`/api/lab/run/${full.base_id}`)).data : null; S.current = full; renderResult(full); }
})();
