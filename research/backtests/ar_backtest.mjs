#!/usr/bin/env node
/**
 * AR (Auction Resolution) strategy over the saved NQ futures history — phase 3 of
 * research/specs/ar-model-chart-strategy-plan.md. Runs the SAME TypeScript engine the chart draws
 * (apps/web/lib/tv/ar-strategy.ts) and the same cost/stat maths as the chart's Strategy Tester
 * (apps/web/lib/tv/strategy-tester.ts), so this prints what the tester shows, over all saved candles.
 *
 *   pnpm bt:ar                                   his defaults, 5-minute candles, all saved history
 *   pnpm bt:ar --set entry=close --set minRR=2   other settings (any key of ArStrategyCfg; times as HH:MM)
 *   pnpm bt:ar --vs entry=close                  A (defaults + --set) vs B (A + --vs), side by side
 *   pnpm bt:ar --interval 60m                    1-hour candles (~2.4 years saved; not his timeframe)
 *   pnpm bt:ar --set confirm=orderflow --set entry=lvn   phase 4: absorption + delta flip on the footprint,
 *                                                that the chart ESTIMATES from the saved 1-minute bars (~4 weeks)
 *   pnpm bt:ar --set gamma=targets              phase 5: in positive gamma take profit at the first gamma level
 *                                                (needs recorded snapshots, `pnpm bt:gex`; candles before the first
 *                                                snapshot run without, and every trade says which regime it had)
 *   pnpm bt:ar --from 2026-10-01 --to 2026-11-01 --contract MNQ --contracts 2 --slip 2 --tag myrun
 *
 * Every run is saved to research/backtests/ar_runs/<id>/ (trades.csv in bt17 column names where they apply,
 * run.json with the exact settings) and counted in ar_runs/ledger.jsonl, like the Lab. Exploratory: no BT
 * number. The dashboard does not list these (it charts NSE longs only); the chart's Strategy Tester is the viewer.
 * Fill the cache first with the chart's History "Update" button or `pnpm bt:nq`.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { avrDays, dailyAtrByDay, sessionBoxes } from '../../apps/web/lib/tv/ar.ts';
import { AR_STRATEGY_DEFAULTS, runArStrategy } from '../../apps/web/lib/tv/ar-strategy.ts';
import { parseSnapshots } from '../../apps/web/lib/tv/gamma-history.ts';
import { cacheSymbolDir, parseCacheCsv } from '../../apps/web/lib/tv/bar-cache.ts';
import { autoStep, buildFootprints } from '../../apps/web/lib/tv/footprint.ts';
import { stackedZones } from '../../apps/web/lib/tv/stacked-imbalance.ts';
import {
  COST_DEFAULTS,
  costPoints,
  groupSkips,
  instrumentFor,
  instrumentOf,
  testerStats,
  tradeOverlap,
  tradeResults,
} from '../../apps/web/lib/tv/strategy-tester.ts';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CACHE = path.join(HERE, '.cache_yahoo_futures');
const RUNS = path.join(HERE, 'ar_runs');
const TZ = 'America/New_York';
const SECS = { '5m': 300, '15m': 900, '60m': 3600 };

const { values: opt } = parseArgs({
  options: {
    symbol: { type: 'string', default: 'NQ=F' },
    interval: { type: 'string', default: '5m' },
    from: { type: 'string' },
    to: { type: 'string' },
    set: { type: 'string', multiple: true, default: [] },
    vs: { type: 'string', multiple: true, default: [] },
    contract: { type: 'string' },
    contracts: { type: 'string', default: '1' },
    commission: { type: 'string' },
    slip: { type: 'string', default: String(COST_DEFAULTS.slipTicks) },
    tag: { type: 'string', default: '' },
    'no-save': { type: 'boolean', default: false },
  },
});

const die = (msg) => {
  console.error(`ar_backtest: ${msg}`);
  process.exit(2);
};

/** `key=value` → a partial ArStrategyCfg; numbers, on/off and HH:MM times are understood. */
function parseSettings(list) {
  const out = {};
  for (const kv of list) {
    const m = /^(\w+)=(.+)$/.exec(kv);
    if (!m) die(`--set/--vs takes key=value, got "${kv}"`);
    const [, k, raw] = m;
    if (!(k in AR_STRATEGY_DEFAULTS)) die(`unknown setting "${k}"; one of ${Object.keys(AR_STRATEGY_DEFAULTS).join(', ')}`);
    const def = AR_STRATEGY_DEFAULTS[k];
    let v;
    if (typeof def === 'boolean') v = /^(1|true|on|yes)$/i.test(raw);
    else if (typeof def === 'number') {
      const t = /^(\d{1,2}):(\d{2})$/.exec(raw);
      v = t ? Number(t[1]) * 60 + Number(t[2]) : Number(raw);
      if (!Number.isFinite(v)) die(`${k} must be a number, got "${raw}"`);
    } else v = raw;
    out[k] = v;
  }
  return out;
}

function readBars(dir, interval) {
  const folder = path.join(CACHE, dir, interval);
  if (!fs.existsSync(folder)) die(`no saved ${interval} candles in ${folder}; run pnpm bt:nq first`);
  const byTime = new Map();
  for (const f of fs.readdirSync(folder).filter((x) => x.endsWith('.csv')).sort()) {
    for (const b of parseCacheCsv(fs.readFileSync(path.join(folder, f), 'utf8'))) byTime.set(b.time, b);
  }
  return [...byTime.values()].sort((a, b) => a.time - b.time);
}

const nyFmt = new Intl.DateTimeFormat('en-CA', {
  timeZone: TZ,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});
const ny = (unix) => {
  const p = Object.fromEntries(nyFmt.formatToParts(new Date(unix * 1000)).map((x) => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`;
};
const dayStart = (ymd) => Math.floor(Date.parse(`${ymd}T00:00:00Z`) / 1000);

// ── candles ──
const symbol = opt.symbol;
const dir = cacheSymbolDir(symbol);
if (!dir) die(`not a cached futures symbol: ${symbol}`);
const secs = SECS[opt.interval];
if (!secs) die(`--interval must be one of ${Object.keys(SECS).join(', ')}`);
let bars = readBars(dir, opt.interval);
// a little slack either side of the NY day boundaries is fine: the strategy only trades inside its window
if (opt.from) bars = bars.filter((b) => b.time >= dayStart(opt.from));
if (opt.to) bars = bars.filter((b) => b.time < dayStart(opt.to) + 86400 + 6 * 3600);
if (bars.length < 50) die(`only ${bars.length} candles in that window`);
const daily = readBars(dir, '1d');
let manifest = null;
try {
  manifest = JSON.parse(fs.readFileSync(path.join(CACHE, dir, 'manifest.json'), 'utf8'));
} catch {
  /* no manifest: treat every candle as finished */
}
// The newest saved candle may have been saved while it was still forming.
const savedAt = Date.parse(manifest?.intervals?.[opt.interval]?.last_run ?? '') / 1000;
const last = bars[bars.length - 1];
const formingLast = Number.isFinite(savedAt) && last.time + secs > savedAt;

// ── footprints: estimated from the saved 1-minute bars, exactly as the chart builds them (Yahoo has no bid/ask) ──
const minute = fs.existsSync(path.join(CACHE, dir, '1m')) ? readBars(dir, '1m') : [];
const fpStep = autoStep(bars);
const footprints = minute.length ? buildFootprints(bars, minute, secs, fpStep) : bars.map(() => null);
const covered = footprints.map((f) => f != null);
const fpCount = covered.filter(Boolean).length;
const stacks = fpCount ? { zones: stackedZones(bars, footprints), covered } : undefined;

// ── gamma: recorded snapshots (`pnpm bt:gex`); each candle uses the newest one read before it closed ──
const snapFile = path.join(HERE, 'gamma_snapshots', `${dir}.jsonl`);
const gamma = fs.existsSync(snapFile) ? parseSnapshots(fs.readFileSync(snapFile, 'utf8')) : [];

const boxes = sessionBoxes(bars, TZ);
const avr = avrDays(bars, boxes, dailyAtrByDay(daily, TZ, 20));

// ── costs ──
const costs = {
  instrument: opt.contract ?? instrumentFor(symbol).id,
  contracts: Number(opt.contracts),
  commission: opt.commission != null ? Number(opt.commission) : null,
  slipTicks: Number(opt.slip),
};
const ins = instrumentOf(costs);
if (opt.contract && ins.id !== opt.contract.toUpperCase()) die(`unknown --contract ${opt.contract}`);

// ── run ──
const cfgA = parseSettings(opt.set);
const cfgB = opt.vs.length ? { ...cfgA, ...parseSettings(opt.vs) } : null;
const run = (cfg) => {
  const res = runArStrategy(bars, { timeZone: TZ, chartSecs: secs, avr, formingLast, stacks, footprints, gamma, cfg });
  const results = tradeResults(res.trades, costs);
  return { res, results, stats: testerStats(results) };
};
const A = run(cfgA);
const B = cfgB ? run(cfgB) : null;

// ── print ──
const cur = ins.currency === 'USD' ? '$' : '';
const money = (x) =>
  `${x < 0 ? '−' : x > 0 ? '+' : ''}${cur}${Math.abs(x).toLocaleString('en-US', { maximumFractionDigits: Math.abs(x) >= 1000 ? 0 : 2 })}`;
const R = (x) => `${x >= 0 ? '+' : '−'}${Math.abs(x).toFixed(2)}R`;
const pct = (x) => `${Math.round(x * 100)}%`;
const settingsText = (cfg) =>
  Object.keys(cfg).length ? Object.entries(cfg).map(([k, v]) => `${k}=${v}`).join(' ') : 'his defaults';
const days = new Set(bars.map((b) => ny(b.time).slice(0, 10))).size;

console.log(
  `\nAR strategy · ${symbol} ${opt.interval} · ${ny(bars[0].time).slice(0, 10)} → ${ny(last.time).slice(0, 10)} ` +
    `(${days} days with candles, ${bars.length.toLocaleString('en-US')} candles)`,
);
console.log(
  `costs: ${ins.id} × ${costs.contracts}, ${costPoints(costs).toFixed(3)} pts a round trip ` +
    `(${costs.slipTicks} tick${costs.slipTicks === 1 ? '' : 's'} slippage + ${cur}${costs.commission ?? ins.commission} fees per side)`,
);
const fpFirst = covered.indexOf(true);
console.log(
  fpCount
    ? `footprint: ${fpCount.toLocaleString('en-US')} of ${bars.length.toLocaleString('en-US')} candles (from ${ny(bars[fpFirst].time).slice(0, 10)}), ` +
        `ESTIMATED from 1-minute bars, rows of ${fpStep}`
    : 'footprint: none (no saved 1-minute bars in this window): confirm=orderflow and entry=lvn cannot work',
);
const withGamma = A.res.trades.filter((t) => t.gamma).length;
console.log(
  gamma.length
    ? `gamma: ${gamma.length} snapshot(s), ${ny(gamma[0].asOf)} → ${ny(gamma[gamma.length - 1].asOf)} (${gamma[0].proxy ?? symbol} options); ` +
        `${withGamma} of ${A.res.trades.length} trades had one`
    : `gamma: no snapshots (record with pnpm bt:gex): gamma=targets changes nothing`,
);
console.log('exploratory: confirmation is estimated (candles, or a footprint built from 1-minute bars); not a pre-registered test\n');

const ROWS = [
  ['Trades', (s) => String(s.trades)],
  ['  target / stop / breakeven / trail / end of day', (s) => ['target', 'stop', 'breakeven', 'trail', 'time'].map((o) => s.byOutcome[o]?.n ?? 0).join(' / ')],
  ['  long / short', (s) => `${s.byDir.long.n} / ${s.byDir.short.n}`],
  ['Win rate', (s) => (s.trades ? pct(s.winRate) : '–')],
  ['Before costs', (s) => money(s.gross)],
  ['Costs', (s) => money(-s.cost)],
  ['Net after costs', (s) => money(s.net)],
  ['Average / trade', (s) => (s.trades ? money(s.avgNet) : '–')],
  ['Median / trade', (s) => (s.trades ? money(s.medianNet) : '–')],
  ['Average / median R', (s) => (s.trades ? `${R(s.avgR)} / ${R(s.medianR)}` : '–')],
  ['Profit factor', (s) => (!s.trades ? '–' : s.profitFactor === Infinity ? '∞' : s.profitFactor.toFixed(2))],
  ['Max drawdown', (s) => (s.trades ? money(-s.maxDrawdown) : '–')],
  ['Net without the best 3', (s) => (s.trades > 3 ? money(s.netWithoutTop3) : '–')],
];
const w0 = Math.max(...ROWS.map(([k]) => k.length)) + 2;
const col = 18;
const head = B ? `${''.padEnd(w0)}${'A'.padStart(col)}${'B'.padStart(col)}` : '';
if (B) {
  console.log(`A: ${settingsText(cfgA)}`);
  console.log(`B: ${settingsText(cfgB)}\n`);
  console.log(head);
} else console.log(`settings: ${settingsText(cfgA)}\n`);
for (const [k, f] of ROWS) console.log(`${k.padEnd(w0)}${f(A.stats).padStart(col)}${B ? f(B.stats).padStart(col) : ''}`);
if (B) {
  const o = tradeOverlap(A.res.trades, B.res.trades);
  console.log(`\nsame trade in both: ${o.both} · only in A: ${o.onlyA.length} · only in B: ${o.onlyB.length}`);
}

console.log(`\nsetups that did not trade (A): ${A.res.skips.length}`);
for (const g of groupSkips(A.res.skips).slice(0, 8)) console.log(`  ${String(g.n).padStart(5)}  ${g.step}: ${g.reason}`);
if (withGamma) {
  console.log('\nby gamma regime (A, closed trades):');
  for (const regime of ['positive', 'negative', null]) {
    const rs = A.results.filter((r) => (r.trade.gamma?.regime ?? null) === regime);
    if (!rs.length) continue;
    const st = testerStats(rs);
    console.log(`  ${String(regime ?? 'no snapshot').padEnd(12)} ${String(st.trades).padStart(4)} trades  net ${money(st.net).padStart(9)}  median ${money(st.medianNet).padStart(8)}`);
  }
}
const openA = A.res.trades.filter((t) => t.outcome === 'open').length;
if (openA) console.log(`\n${openA} trade still open at the last candle (not counted).`);

// ── save ──
function tradesCsv(results) {
  const cols = [
    'date', 'symbol', 'side', 'setup', 'entry_time', 'exit_time', 'entry', 'stop', 'target', 'exit', 'exit_reason',
    'qty', 'gross_pts', 'net_pts', 'net_r', 'gross_pct', 'net_pct', 'gross_usd', 'net_usd', 'confirm', 'flow_source',
    'absorb_time', 'absorb_vol_x', 'flip_share', 'entry_rule', 'stack', 'gamma_regime', 'gamma_flip', 'gamma_level',
    'leg_target', 'warnings',
  ];
  const q = (v) => (/[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
  const lines = [cols.join(',')];
  for (const r of results) {
    const t = r.trade;
    const entryAt = ny(bars[t.entryIndex].time);
    const row = {
      date: entryAt.slice(0, 10),
      symbol,
      side: t.dir,
      setup: `ar_${t.dir}`,
      entry_time: entryAt,
      exit_time: ny(bars[t.exitIndex].time),
      entry: +t.entry.toFixed(4),
      stop: +t.stop.toFixed(4),
      target: +t.target.toFixed(4),
      exit: +t.exit.toFixed(4),
      exit_reason: t.outcome,
      qty: costs.contracts,
      gross_pts: r.grossPts.toFixed(4),
      net_pts: r.netPts.toFixed(4),
      net_r: r.netR.toFixed(4),
      gross_pct: ((r.grossPts / t.entry) * 100).toFixed(4),
      net_pct: ((r.netPts / t.entry) * 100).toFixed(4),
      gross_usd: r.gross.toFixed(2),
      net_usd: r.net.toFixed(2),
      confirm: t.flow ? 'orderflow' : 'candles',
      flow_source: t.flow?.source ?? '',
      absorb_time: t.flow ? ny(bars[t.flow.absorbIndex].time) : '',
      absorb_vol_x: t.flow ? t.flow.volRatio.toFixed(2) : '',
      flip_share: t.flow ? t.flow.flipShare.toFixed(3) : '',
      entry_rule: t.entryRule,
      stack: t.stack ?? '',
      gamma_regime: t.gamma?.regime ?? '',
      gamma_flip: t.gamma?.flip != null ? t.gamma.flip.toFixed(2) : '',
      gamma_level: t.gamma?.level ?? '',
      leg_target: t.gamma ? t.gamma.legTarget.toFixed(4) : '',
      warnings: t.warnings.join(' | '),
    };
    lines.push(cols.map((c) => q(row[c])).join(','));
  }
  return `${lines.join('\n')}\n`;
}

if (!opt['no-save']) {
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace('T', '-').slice(0, 15);
  const tag = opt.tag.replace(/[^\w-]/g, '_');
  const id = `${stamp}_${opt.interval}${tag ? `_${tag}` : ''}`;
  const out = path.join(RUNS, id);
  fs.mkdirSync(out, { recursive: true });
  const summary = (s) => ({ ...s, equity: undefined });
  const record = {
    id,
    created: new Date().toISOString(),
    symbol,
    interval: opt.interval,
    window: { from: ny(bars[0].time), to: ny(last.time), days, candles: bars.length, formingLast },
    gamma: { snapshots: gamma.length, from: gamma.length ? ny(gamma[0].asOf) : null, to: gamma.length ? ny(gamma[gamma.length - 1].asOf) : null, tradesWithSnapshot: withGamma },
    footprint: { candles: fpCount, from: fpCount ? ny(bars[fpFirst].time) : null, source: 'estimated from 1m bars', rowStep: fpStep },
    engine: 'apps/web/lib/tv/ar-strategy.ts',
    costs: { ...costs, roundTripPts: costPoints(costs) },
    a: { settings: { ...AR_STRATEGY_DEFAULTS, ...cfgA }, changed: cfgA, stats: summary(A.stats), skips: A.res.skips.length },
    b: B ? { settings: { ...AR_STRATEGY_DEFAULTS, ...cfgB }, changed: cfgB, stats: summary(B.stats), skips: B.res.skips.length } : null,
  };
  fs.writeFileSync(path.join(out, 'run.json'), `${JSON.stringify(record, null, 2)}\n`);
  fs.writeFileSync(path.join(out, 'trades.csv'), tradesCsv(A.results));
  if (B) fs.writeFileSync(path.join(out, 'trades_b.csv'), tradesCsv(B.results));
  fs.appendFileSync(
    path.join(RUNS, 'ledger.jsonl'),
    `${JSON.stringify({ id, created: record.created, symbol, interval: opt.interval, a: cfgA, b: cfgB, trades: A.stats.trades, net: A.stats.net })}\n`,
  );
  console.log(`\nsaved research/backtests/ar_runs/${id}/ (run ${fs.readFileSync(path.join(RUNS, 'ledger.jsonl'), 'utf8').trim().split('\n').length} in the ledger)`);
}
