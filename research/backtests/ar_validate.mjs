#!/usr/bin/env node
/**
 * Checks every AR strategy trade against the raw candles and against the course's checklist
 * (research/specs/orderflow-course-ar-model.md), with its own maths where it can:
 *
 *   engine    did the engine do what its own rules say? (leg is real swing prices, 0.705/0.886 maths, first touch,
 *             no close past 0.886 before the entry, confirmation candle, pull-back fill, stop, target, exit candle,
 *             points and R). Any FAIL here is a bug.
 *   course    does the trade meet the video's rules, including the ones the engine relaxes or leaves out
 *             (New York 09:30–11:00 only, zone outside the leg's value area, bias on 1h swings).
 *             A "no" here is a deviation from the course, not a bug.
 *
 *   node research/backtests/ar_validate.mjs                 5m, all saved candles, default settings
 *   node research/backtests/ar_validate.mjs --interval 60m --from 2026-01-20 --to 2026-02-10
 *   node research/backtests/ar_validate.mjs --set windowEnd=11:00
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { avrDays, dailyAtrByDay, sessionBoxes, swings } from '../../apps/web/lib/tv/ar.ts';
import { AR_STRATEGY_DEFAULTS, runArStrategy } from '../../apps/web/lib/tv/ar-strategy.ts';
import { cacheSymbolDir, parseCacheCsv } from '../../apps/web/lib/tv/bar-cache.ts';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CACHE = path.join(HERE, '.cache_yahoo_futures');
const TZ = 'America/New_York';
const SECS = { '5m': 300, '15m': 900, '60m': 3600 };

const { values: opt } = parseArgs({
  options: {
    symbol: { type: 'string', default: 'NQ=F' },
    interval: { type: 'string', default: '5m' },
    from: { type: 'string' },
    to: { type: 'string' },
    set: { type: 'string', multiple: true, default: [] },
  },
});
const secs = SECS[opt.interval];
if (!secs) throw new Error(`--interval is one of ${Object.keys(SECS).join(', ')}`);

function readBars(interval) {
  const folder = path.join(CACHE, cacheSymbolDir(opt.symbol), interval);
  const byTime = new Map();
  for (const f of fs.readdirSync(folder).filter((x) => x.endsWith('.csv')).sort())
    for (const b of parseCacheCsv(fs.readFileSync(path.join(folder, f), 'utf8'))) byTime.set(b.time, b);
  return [...byTime.values()].sort((a, b) => a.time - b.time);
}
const fmt = new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const ny = (t) => fmt.format(new Date(t * 1000)).replace(',', '');
const nyMinute = (t) => {
  const [h, m] = ny(t).slice(11, 16).split(':').map(Number);
  return h * 60 + m;
};
const dayStart = (d) => Date.parse(`${d}T00:00:00Z`) / 1000;

const cfg = {};
for (const kv of opt.set) {
  const [, k, raw] = /^(\w+)=(.+)$/.exec(kv) ?? [];
  if (!(k in AR_STRATEGY_DEFAULTS)) throw new Error(`unknown setting ${kv}`);
  const hm = /^(\d{1,2}):(\d{2})$/.exec(raw);
  cfg[k] = hm ? +hm[1] * 60 + +hm[2] : raw === 'true' ? true : raw === 'false' ? false : Number.isFinite(+raw) ? +raw : raw;
}

let bars = readBars(opt.interval);
if (opt.from) bars = bars.filter((b) => b.time >= dayStart(opt.from));
if (opt.to) bars = bars.filter((b) => b.time < dayStart(opt.to) + 86400 + 6 * 3600);
const daily = readBars('1d');
const avr = avrDays(bars, sessionBoxes(bars, TZ), dailyAtrByDay(daily, TZ, 20));
const res = runArStrategy(bars, { timeZone: TZ, chartSecs: secs, avr, cfg });
const C = { ...AR_STRATEGY_DEFAULTS, ...cfg };

// ── independent helpers ──
function atr14(i) {
  // Wilder ATR 14, written out again here
  let prev = 0;
  const tr = (k) => (k === 0 ? bars[0].high - bars[0].low : Math.max(bars[k].high - bars[k].low, Math.abs(bars[k].high - bars[k - 1].close), Math.abs(bars[k].low - bars[k - 1].close)));
  for (let k = 0; k < 14; k++) prev += tr(k);
  prev /= 14;
  for (let k = 14; k <= i; k++) prev = (prev * 13 + tr(k)) / 14;
  return prev;
}
/** Volume profile of candles from..to (inclusive), volume spread over each candle's range; 70 % value area. */
function valueArea(from, to, rows = 30) {
  let lo = Infinity, hi = -Infinity;
  for (let k = from; k <= to; k++) { lo = Math.min(lo, bars[k].low); hi = Math.max(hi, bars[k].high); }
  const step = (hi - lo) / rows;
  const vol = new Array(rows).fill(0);
  for (let k = from; k <= to; k++) {
    const b = bars[k];
    const span = Math.max(b.high - b.low, 1e-9);
    for (let r = 0; r < rows; r++) {
      const a = lo + r * step, z = a + step;
      const ov = Math.min(z, b.high) - Math.max(a, b.low);
      if (b.high === b.low) { if (b.low >= a && b.low <= z) { vol[r] += b.volume; break; } } else if (ov > 0) vol[r] += (b.volume * ov) / span;
    }
  }
  const total = vol.reduce((s, v) => s + v, 0);
  let poc = vol.indexOf(Math.max(...vol));
  let a = poc, z = poc, sum = vol[poc];
  while (sum < total * 0.7 && (a > 0 || z < rows - 1)) {
    const up = z < rows - 1 ? vol[z + 1] : -1, dn = a > 0 ? vol[a - 1] : -1;
    if (up >= dn) sum += vol[++z]; else sum += vol[--a];
  }
  return { val: lo + a * step, vah: lo + (z + 1) * step, poc: lo + (poc + 0.5) * step };
}
/** 1h candles from the chart's (UTC hour buckets = New York hour buckets). */
function hourly() {
  if (secs >= 3600) return bars.map((b, i) => ({ ...b, last: i }));
  const out = [];
  for (let i = 0; i < bars.length; i++) {
    const b = bars[i];
    const h = Math.floor(b.time / 3600) * 3600;
    const cur = out[out.length - 1];
    if (cur && cur.time === h) { cur.high = Math.max(cur.high, b.high); cur.low = Math.min(cur.low, b.low); cur.close = b.close; cur.volume += b.volume; cur.last = i; }
    else out.push({ time: h, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume, last: i });
  }
  return out;
}
const H = hourly();
const hPivots = swings(H, C.swingMult).pivots;

const legRe = /(?:swing|week range) ([\d.]+) \((\d\d-\d\d \d\d:\d\d)\) → ([\d.]+) \((\d\d-\d\d \d\d:\d\d)\)/;
function findPivotBar(price, label, upto) {
  for (let k = upto; k >= 0; k--) {
    const b = bars[k];
    if (ny(b.time).slice(5, 16) === label && (Math.abs(b.high - price) < 1e-6 || Math.abs(b.low - price) < 1e-6)) return k;
  }
  return -1;
}

const yes = (ok) => (ok ? 'ok ' : 'FAIL');
const rows = [];
let engineFails = 0;
for (const t of res.trades) {
  const long = t.dir === 'long';
  const read = res.reads[t.confirmIndex];
  const m = legRe.exec(read.checks.location.text + ' ' + (read.checks.location.detail ?? ''));
  const out = { id: t.id, dir: t.dir, confirm: ny(bars[t.confirmIndex].time), entryAt: ny(bars[t.entryIndex].time), engine: [], course: [] };
  const E = (ok, what) => { out.engine.push(`${yes(ok)} ${what}`); if (!ok) engineFails++; };
  const K = (ok, what) => out.course.push(`${ok ? 'yes' : 'NO '} ${what}`);
  if (!m) { E(false, 'leg not named in the checklist'); rows.push(out); continue; }
  const fromPx = +m[1], toPx = +m[3];
  const week = m[0].startsWith('week');
  const fromI = findPivotBar(fromPx, m[2], t.confirmIndex), toI = findPivotBar(toPx, m[4], t.confirmIndex);
  E(fromI >= 0 && toI >= 0 && fromI < toI, `leg ${fromPx} → ${toPx} are real candle ${long ? 'low → high' : 'high → low'} prices (${m[2]} → ${m[4]})`);
  E(long ? toPx > fromPx : toPx < fromPx, `leg direction fits a ${t.dir}`);
  // the leg's ends are the extremes between them
  if (fromI >= 0 && toI >= 0) {
    let lo = Infinity, hi = -Infinity;
    for (let k = fromI; k <= toI; k++) { lo = Math.min(lo, bars[k].low); hi = Math.max(hi, bars[k].high); }
    E(long ? lo === fromPx && hi === toPx : hi === fromPx && lo === toPx, 'leg ends are the high/low of the candles between them');
  }
  const span = toPx - fromPx;
  const z705 = toPx - 0.705 * span, z886 = toPx - 0.886 * span, z62 = toPx - 0.62 * span;
  // first touch, nothing beyond the leg end before it
  const touch = t.touchIndex;
  let early = -1, beyond = -1;
  for (let k = toI + 1; k < touch; k++) {
    if (long ? bars[k].low <= z705 : bars[k].high >= z705) early = k;
    if (long ? bars[k].high > toPx : bars[k].low < toPx) beyond = k;
  }
  E(long ? bars[touch].low <= z705 : bars[touch].high >= z705, `touch candle ${ny(bars[touch].time).slice(5)} reached 0.705 (${z705.toFixed(2)})`);
  // the week range re-arms while price sits in its zone (up to legTrades trades), so only a swing leg needs the first touch
  if (week) out.engine.push(`info ${early < 0 ? 'first touch of this week range' : 'a later touch of this week range (re-armed)'}`);
  else E(early < 0, 'it was the first touch since the leg ended');
  E(beyond < 0, 'price did not run past the leg end before the touch');
  let cancel = -1;
  for (let k = touch; k <= t.entryIndex; k++) if (long ? bars[k].close < z886 : bars[k].close > z886) { cancel = k; break; }
  E(cancel < 0, `no close past 0.886 (${z886.toFixed(2)}) between touch and fill`);
  // confirmation: the extreme candle since the touch, then a candle closing in the trade's direction beyond its other end
  let ext = touch;
  for (let k = touch; k < t.confirmIndex; k++) if (long ? bars[k].low < bars[ext].low : bars[k].high > bars[ext].high) ext = k;
  const cb = bars[t.confirmIndex];
  E(long ? cb.close > cb.open && cb.close > bars[ext].high : cb.close < cb.open && cb.close < bars[ext].low,
    `confirmation candle ${long ? 'green, closed above' : 'red, closed below'} the extreme candle's ${long ? 'high' : 'low'} (${(long ? bars[ext].high : bars[ext].low).toFixed(2)})`);
  E(t.confirmIndex - touch <= C.confirmWithin, `confirmed ${t.confirmIndex - touch} candles after the touch (≤ ${C.confirmWithin})`);
  const cm = nyMinute(cb.time);
  E(cm >= C.windowStart && cm < C.windowEnd, `confirmation inside the engine's window (${ny(cb.time).slice(11)})`);
  // entry
  if (t.entryRule === 'mid') {
    const limit = (cb.high + cb.low) / 2;
    const fb = bars[t.entryIndex];
    let missed = -1;
    for (let k = t.confirmIndex + 1; k < t.entryIndex; k++) if (long ? bars[k].low <= limit : bars[k].high >= limit) missed = k;
    E(t.entryIndex - t.confirmIndex <= C.entryWait && missed < 0 && (long ? fb.low <= limit : fb.high >= limit), `pull-back to mid ${limit.toFixed(2)} filled ${t.entryIndex - t.confirmIndex} candle(s) later`);
    E(Math.abs(t.entry - (long ? Math.min(limit, fb.open) : Math.max(limit, fb.open))) < 1e-6, `fill price ${t.entry.toFixed(2)} = the limit, or the open if it gapped through`);
  } else if (t.entryRule === 'close') E(Math.abs(t.entry - cb.close) < 1e-6, 'entry at the confirmation close');
  // stop and target
  const buf = atr14(t.confirmIndex) * C.stopBuffer;
  const wantStop = long ? bars[ext].low - buf : bars[ext].high + buf;
  E(Math.abs(t.stop - wantStop) < 1e-6, `stop ${t.stop.toFixed(2)} = extreme ${(long ? bars[ext].low : bars[ext].high).toFixed(2)} ${long ? '−' : '+'} 0.1 × ATR`);
  E(Math.abs(t.target - toPx) < 1e-6 || t.gamma?.level, `target ${t.target.toFixed(2)} = the leg end (draw on liquidity)`);
  const rr = Math.abs(t.target - t.entry) / Math.abs(t.entry - t.stop);
  E(rr >= C.minRR - 1e-9, `reward ÷ risk ${rr.toFixed(2)} ≥ ${C.minRR}`);
  // exit: walk the candles again, moving the stop the way he does (breakeven at +N, then trail past each candle),
  // each move decided at a candle's close and used from the next candle on
  let exitI = null, exitPx = null, how = null;
  let stopNow = t.stop, lastWhy = null, beAt = null, armed = false;
  const need = C.breakevenUnit === 'R' ? C.breakeven * Math.abs(t.entry - t.stop) : C.breakeven;
  for (let k = t.entryIndex; k < bars.length; k++) {
    const b = bars[k];
    if (k > t.entryIndex && b.time - bars[k - 1].time > 3600) { exitI = k - 1; exitPx = bars[k - 1].close; how = 'time'; break; }
    const hs = long ? b.low <= stopNow : b.high >= stopNow;
    const ht = long ? b.high >= t.target : b.low <= t.target;
    if (hs) { exitI = k; exitPx = k === t.entryIndex ? stopNow : long ? Math.min(stopNow, b.open) : Math.max(stopNow, b.open); how = lastWhy ?? 'stop'; break; }
    if (ht && k > t.entryIndex) { exitI = k; exitPx = long ? Math.max(t.target, b.open) : Math.min(t.target, b.open); how = 'target'; break; }
    if (nyMinute(b.time) + secs / 60 >= C.flatAt) { exitI = k; exitPx = b.close; how = 'time'; break; }
    if (k === t.entryIndex) continue; // the fill candle moves nothing
    const ok = (p) => (long ? p < b.close && p > stopNow + 1e-9 : p > b.close && p < stopNow - 1e-9);
    if (C.breakeven > 0 && beAt == null) {
      if ((long ? b.high - t.entry : t.entry - b.low) >= need) armed = true;
      if (armed && (long ? t.entry < b.close : t.entry > b.close)) { beAt = k; if (ok(t.entry)) { stopNow = t.entry; lastWhy = 'breakeven'; } }
    }
    if (C.trail === 'candle' && (C.breakeven <= 0 || beAt != null)) {
      const p = long ? b.low - atr14(k) * C.stopBuffer : b.high + atr14(k) * C.stopBuffer;
      if (ok(p)) { stopNow = p; lastWhy = 'trail'; }
    }
  }
  if (C.trail === 'poc15') out.engine.push('info trail past the 15-minute POC needs the footprint: exit not re-checked');
  if (C.trail !== 'poc15') E(exitI === t.exitIndex && Math.abs((exitPx ?? NaN) - (t.exit ?? NaN)) < 1e-6 && how === t.outcome, `exit: ${how} at ${exitPx?.toFixed(2)} on ${exitI != null ? ny(bars[exitI].time).slice(5) : '—'}`);
  const pts = long ? t.exit - t.entry : t.entry - t.exit;
  E(Math.abs(pts - t.points) < 1e-6, `points ${pts.toFixed(2)}, R ${(pts / Math.abs(t.entry - t.stop)).toFixed(2)}`);

  // ── the course's checklist ──
  // 1 bias: plain HH+HL / LH+LL on the last two 1h swing highs and lows known at the confirmation close
  const closeT = cb.time + secs;
  const known = hPivots.filter((p) => H[p.confirmedAt] && H[p.confirmedAt].time + 3600 <= closeT);
  const hs = known.filter((p) => p.kind === 'H').slice(-2), ls = known.filter((p) => p.kind === 'L').slice(-2);
  const up = hs.length === 2 && ls.length === 2 && hs[1].price > hs[0].price && ls[1].price > ls[0].price;
  const dn = hs.length === 2 && ls.length === 2 && hs[1].price < hs[0].price && ls[1].price < ls[0].price;
  const sw = `1h highs ${hs.map((p) => p.price.toFixed(0)).join('→')}, lows ${ls.map((p) => p.price.toFixed(0)).join('→')}`;
  K(long ? up : dn, `bias: 1h ${long ? 'higher highs + higher lows' : 'lower highs + lower lows'} (${sw})`);
  K(cm >= 9 * 60 + 30 && cm < 11 * 60, `New York AM session, first 90 min (09:30–11:00): confirmed ${ny(cb.time).slice(11)}`);
  if (fromI >= 0 && toI >= 0) {
    const va = valueArea(fromI, toI);
    const outside = long ? z705 < va.val : z705 > va.vah;
    K(outside, `zone ${long ? 'below VAL' : 'above VAH'}: 0.705 at ${z705.toFixed(2)} vs value area ${va.val.toFixed(2)}–${va.vah.toFixed(2)}`);
  }
  K(true, `stop beyond the swing extreme since the touch; target = the leg end ${toPx.toFixed(2)} (DOL)`);
  const deepest = long ? Math.min(...bars.slice(touch, t.confirmIndex + 1).map((b) => b.low)) : Math.max(...bars.slice(touch, t.confirmIndex + 1).map((b) => b.high));
  const depth = (toPx - deepest) / span;
  K(true, `deepest retracement before the turn: ${depth.toFixed(3)} of the leg (his examples turn at 0.62–0.705; 0.62 = ${z62.toFixed(2)})`);
  out.warnings = t.warnings;
  out.result = `${t.outcome} ${t.points.toFixed(2)} pts (${t.r.toFixed(2)}R)`;
  rows.push(out);
}

console.log(`AR trade validation · ${opt.symbol} ${opt.interval} · ${ny(bars[0].time).slice(0, 10)} → ${ny(bars.at(-1).time).slice(0, 10)} · settings ${JSON.stringify(cfg)}`);
console.log(`${res.trades.length} trades, ${engineFails} engine check(s) failed\n`);
for (const r of rows) {
  console.log(`#${r.id} ${r.dir.toUpperCase()} confirmed ${r.confirm}, filled ${r.entryAt} → ${r.result}`);
  for (const l of r.engine) console.log(`   engine  ${l}`);
  for (const l of r.course) console.log(`   course  ${l}`);
  for (const w of r.warnings ?? []) console.log(`   warned  ${w}`);
  console.log('');
}
