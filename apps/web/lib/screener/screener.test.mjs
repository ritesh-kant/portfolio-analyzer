import assert from 'node:assert/strict';
import { test } from 'node:test';
import { compact, formatValue, parseNumber, rangeLabel } from './format.ts';
import { PRESETS } from './fields.ts';
import {
  isCommonStock,
  postFilter,
  rowFromQuote,
  sanitizeFilters,
  sortRows,
  tiersForFilters,
  tiersForView,
  yahooQuery,
  yahooSort,
} from './query.ts';
import { relVolAtTime, zoneClock } from './relvol.ts';
import { bestSetup, pocCandidates, pocSetup, pocZone, POC_DEFAULTS } from './poc-retest.ts';
import { runScreen } from './run.ts';

// A real quote from Yahoo's screener (AI Okto, a recent IPO that gapped), trimmed to what we read.
const AIOK = {
  symbol: 'AIOK',
  longName: 'AI OKTO CORP.',
  exchange: 'NCM',
  regularMarketPrice: 12.02,
  regularMarketChange: 7.77,
  regularMarketChangePercent: 182.824,
  regularMarketVolume: 41_847_594,
  regularMarketOpen: 5.55,
  regularMarketDayHigh: 15.59,
  regularMarketDayLow: 5.4,
  regularMarketPreviousClose: 4.25,
  averageDailyVolume3Month: 3_928_763,
  sharesOutstanding: 4_319_916,
  marketCap: 51_925_392,
  fiftyTwoWeekHigh: 15.59,
  fiftyTwoWeekChangePercent: 38.16,
  fiftyDayAverageChangePercent: 1.0983971,
  twoHundredDayAverageChangePercent: 1.0983971,
  epsTrailingTwelveMonths: -0.36,
  dividendYield: 0,
  firstTradeDateMilliseconds: 1791552600000,
};
const NOW = Date.parse('2026-10-25T12:00:00Z');

test('parseNumber understands K/M/B suffixes and ignores $ , %', () => {
  assert.equal(parseNumber('15M'), 15e6);
  assert.equal(parseNumber('1.5k'), 1500);
  assert.equal(parseNumber('$2'), 2);
  assert.equal(parseNumber('10%'), 10);
  assert.equal(parseNumber('-3'), -3);
  assert.equal(parseNumber('2b'), 2e9);
  assert.equal(parseNumber('abc'), null);
  assert.equal(parseNumber(''), null);
  assert.equal(parseNumber('1m2'), null);
});

test('compact + formatValue match the TradingView look', () => {
  assert.equal(compact(12_880_000), '12.88M');
  assert.equal(compact(3600), '3.6K');
  assert.equal(compact(341_850_000), '341.85M');
  assert.equal(compact(1_640_000), '1.64M');
  assert.equal(formatValue('pct', 49.64, true), '+49.64%');
  assert.equal(formatValue('pct', -2.1, true), '-2.10%');
  assert.equal(formatValue('usd', 6.27), '6.27');
  assert.equal(formatValue('usd', 0.1234), '0.1234');
  assert.equal(formatValue('x', null), '—');
});

test('rangeLabel reads like the pills in the screenshot', () => {
  assert.equal(rangeLabel('usd', { min: 2, max: 20 }), '2 to 20 USD');
  assert.equal(rangeLabel('pct', { min: 10 }), '> 10%');
  assert.equal(rangeLabel('x', { min: 4 }), '> 4');
  assert.equal(rangeLabel('shares', { min: 1e6, max: 15e6 }), '1M to 15M');
  assert.equal(rangeLabel('pct', { max: 5 }), '< 5%');
});

test('yahooQuery pushes Yahoo-runnable filters down and leaves the rest', () => {
  const five = PRESETS.find((p) => p.name === '5 pillar');
  const q = yahooQuery(five.filters, NOW);
  assert.equal(q.operator, 'and');
  const flat = JSON.stringify(q);
  assert.match(flat, /"region","us"/);
  assert.match(flat, /"btwn","operands":\["intradayprice",2,20\]/);
  assert.match(flat, /\["percentchange",10\]/);
  assert.match(flat, /"exchange","NMS"/);
  // float and rel-vol-at-time have no Yahoo operand: they must NOT be sent.
  assert.doesNotMatch(flat, /float/i);
  assert.doesNotMatch(flat, /relative_volume/);
  // price uses a single btwn; change uses gte
  const ops = q.operands.map((o) => o.operator);
  assert.ok(ops.includes('btwn'));
  assert.ok(ops.includes('gte'));
});

test('yahooQuery: a single exchange is a plain eq, none selected adds no exchange clause', () => {
  const one = yahooQuery({ exchange: { in: ['NYSE'] } }, NOW);
  assert.deepEqual(
    one.operands.find((o) => JSON.stringify(o).includes('exchange')),
    {
      operator: 'or',
      operands: [
        { operator: 'eq', operands: ['exchange', 'NYQ'] },
        { operator: 'eq', operands: ['exchange', 'NYS'] },
      ],
    },
  );
  const none = yahooQuery({ exchange: { in: [] } }, NOW);
  assert.ok(!JSON.stringify(none).includes('exchange'));
});

test('yahooQuery: listing age becomes a listing-date bound', () => {
  const q = yahooQuery({ listedDays: { max: 30 } }, NOW);
  assert.deepEqual(q.operands.at(-1), {
    operator: 'gte',
    operands: ['new_listing_date', '2026-09-25'],
  });
});

test('yahooSort uses Yahoo for sortable columns and falls back to biggest movers', () => {
  assert.deepEqual(yahooSort({ key: 'volume', dir: 'desc' }), { field: 'dayvolume', type: 'DESC' });
  assert.deepEqual(yahooSort({ key: 'price', dir: 'asc' }), {
    field: 'intradayprice',
    type: 'ASC',
  });
  assert.deepEqual(yahooSort({ key: 'rvol', dir: 'desc' }), {
    field: 'percentchange',
    type: 'DESC',
  });
});

test('rowFromQuote derives the momentum columns from one quote', () => {
  const r = rowFromQuote(AIOK, NOW);
  assert.equal(r.symbol, 'AIOK');
  assert.equal(r.exchange, 'NASDAQ');
  assert.equal(r.changePct, 182.824);
  assert.ok(Math.abs(r.rvol - 41_847_594 / 3_928_763) < 1e-9);
  assert.ok(Math.abs(r.gapPct - (5.55 / 4.25 - 1) * 100) < 1e-9);
  assert.ok(Math.abs(r.fromOpenPct - (12.02 / 5.55 - 1) * 100) < 1e-9);
  assert.ok(Math.abs(r.rangePct - (15.59 / 5.4 - 1) * 100) < 1e-9);
  assert.equal(r.from52High, (12.02 / 15.59 - 1) * 100);
  assert.ok(Math.abs(r.vs50d - 109.83971) < 1e-6);
  assert.equal(r.dollarVol, 41_847_594 * 12.02);
  assert.equal(r.sharesOut, 4_319_916);
  assert.equal(r.listedDays, 15); // first trade 2026-10-07
  assert.equal(r.earningsDays, null);
  assert.equal(r.float, undefined); // not fetched, not "no data"
});

test('rowFromQuote rejects quotes with no price and null-safes zero denominators', () => {
  assert.equal(rowFromQuote({ symbol: 'X' }, NOW), null);
  const r = rowFromQuote(
    { symbol: 'Y', regularMarketPrice: 1, averageDailyVolume3Month: 0, regularMarketVolume: 5 },
    NOW,
  );
  assert.equal(r.rvol, null);
});

test('earningsDays counts only upcoming dates', () => {
  const soon = rowFromQuote(
    { symbol: 'A', regularMarketPrice: 1, earningsTimestamp: NOW / 1000 + 3 * 86400 },
    NOW,
  );
  assert.equal(soon.earningsDays, 3);
  const past = rowFromQuote(
    { symbol: 'B', regularMarketPrice: 1, earningsTimestamp: NOW / 1000 - 86400 },
    NOW,
  );
  assert.equal(past.earningsDays, null);
});

test('isCommonStock drops warrants, rights, units and preferreds', () => {
  assert.equal(isCommonStock({ symbol: 'AAPL', name: 'Apple Inc.' }), true);
  assert.equal(isCommonStock({ symbol: 'BGLWW', name: 'BiOptio Warrants' }), false);
  assert.equal(isCommonStock({ symbol: 'XYZ', name: 'XYZ Acquisition Corp. Units' }), false);
  assert.equal(isCommonStock({ symbol: 'ABC', name: 'ABC Rights' }), false);
  assert.equal(isCommonStock({ symbol: 'BAC-PL', name: 'Bank of America' }), false);
  assert.equal(isCommonStock({ symbol: 'BRK-B', name: 'Berkshire Hathaway Inc.' }), true);
  assert.equal(isCommonStock({ symbol: 'RWAY', name: 'Runway Growth Finance' }), true);
});

const mk = (symbol, over = {}) => ({ ...rowFromQuote({ ...AIOK, symbol }, NOW), ...over });

test('postFilter: range bounds are inclusive, a missing value cannot pass, and it is counted', () => {
  const rows = [
    mk('A', { rvol: 4 }),
    mk('B', { rvol: 3.99 }),
    mk('C', { rvol: null }),
    mk('D', { rvol: 10 }),
  ];
  const r = postFilter(rows, { rvol: { min: 4 } }, new Set(['quote']));
  assert.deepEqual(
    r.rows.map((x) => x.symbol),
    ['A', 'D'],
  );
  assert.deepEqual(r.missing, { rvol: 1 });
});

test('postFilter: only the requested tiers run, and float is tier profile', () => {
  const rows = [mk('A', { float: 5e6 }), mk('B', { float: 20e6 }), mk('C')];
  const filters = { float: { min: 1e6, max: 15e6 }, rvol: { min: 100 } };
  const quoteOnly = postFilter(rows, filters, new Set(['quote']));
  assert.equal(quoteOnly.rows.length, 0); // rvol ≈ 10.6 < 100
  const profileOnly = postFilter(rows, filters, new Set(['profile']));
  assert.deepEqual(
    profileOnly.rows.map((x) => x.symbol),
    ['A'],
  );
  assert.deepEqual(profileOnly.missing, { float: 1 }); // C was never fetched
});

test('postFilter: commonOnly removes warrants in the quote stage', () => {
  const rows = [mk('GOOD'), mk('BGLWW', { name: 'X Warrants' })];
  const r = postFilter(rows, { commonOnly: { on: true } }, new Set(['quote']));
  assert.deepEqual(
    r.rows.map((x) => x.symbol),
    ['GOOD'],
  );
});

test('postFilter: analyst multi-select', () => {
  const rows = [
    mk('A', { analyst: 'Buy' }),
    mk('B', { analyst: 'Sell' }),
    mk('C', { analyst: null }),
  ];
  const r = postFilter(rows, { analyst: { in: ['Buy', 'Strong Buy'] } }, new Set(['quote']));
  assert.deepEqual(
    r.rows.map((x) => x.symbol),
    ['A'],
  );
});

test('tiersForFilters / tiersForView tell the server what to fetch', () => {
  const five = PRESETS.find((p) => p.name === '5 pillar');
  assert.deepEqual([...tiersForFilters(five.filters)].sort(), ['intraday', 'profile']);
  // sector is pushed to Yahoo as a filter, so filtering by it needs no per-name lookup
  assert.deepEqual([...tiersForFilters({ sector: { in: ['Technology'] } })], []);
  // …but showing the Sector column does
  assert.deepEqual(
    [...tiersForView(['price', 'sector'], { key: 'price', dir: 'asc' })],
    ['profile'],
  );
  assert.deepEqual([...tiersForView(['price'], { key: 'rvolTime', dir: 'desc' })], ['intraday']);
});

test('sortRows: numbers by value, nulls last in both directions, ties by symbol', () => {
  const rows = [
    mk('B', { rvol: 2 }),
    mk('A', { rvol: 2 }),
    mk('C', { rvol: null }),
    mk('D', { rvol: 9 }),
  ];
  assert.deepEqual(
    sortRows(rows, { key: 'rvol', dir: 'desc' }).map((r) => r.symbol),
    ['D', 'A', 'B', 'C'],
  );
  assert.deepEqual(
    sortRows(rows, { key: 'rvol', dir: 'asc' }).map((r) => r.symbol),
    ['A', 'B', 'D', 'C'],
  );
  assert.deepEqual(
    sortRows(rows, { key: 'symbol', dir: 'asc' }).map((r) => r.symbol),
    ['A', 'B', 'C', 'D'],
  );
});

test('sanitizeFilters drops unknown fields, junk numbers, unknown options', () => {
  const out = sanitizeFilters({
    price: { min: 2, max: 'abc' },
    __proto__x: { min: 1 },
    nonsense: { min: 1 },
    changePct: { min: Infinity },
    sector: { in: ['Technology', 'Bogus'] },
    exchange: { in: ['NYSE', 'MARS'] },
    commonOnly: { on: true },
    float: { min: 1e6 },
  });
  assert.deepEqual(out, {
    price: { min: 2 },
    sector: { in: ['Technology'] },
    exchange: { in: ['NYSE'] },
    commonOnly: { on: true },
    float: { min: 1e6 },
  });
  assert.deepEqual(sanitizeFilters(null), {});
  assert.deepEqual(sanitizeFilters('x'), {});
});

// ── relative volume at time ─────────────────────────────────────────────────

const ET = zoneClock('America/New_York');
const open = (day) => Date.parse(`${day}T13:30:00Z`) / 1000; // 09:30 EDT (before the Nov 1 change)
const session = (day, vols) => vols.map((v, i) => ({ t: open(day) + i * 300, v }));

test('relVolAtTime compares the same clock time across sessions', () => {
  // 6 prior sessions each trade 100/bar for 78 bars; today has only reached bar 12 (10:30) at 300/bar
  const bars = [];
  for (const d of [
    '2026-10-12',
    '2026-10-13',
    '2026-10-14',
    '2026-10-15',
    '2026-10-16',
    '2026-10-19',
  ])
    bars.push(...session(d, Array(78).fill(100)));
  bars.push(...session('2026-10-20', Array(12).fill(300)));
  const r = relVolAtTime(bars, ET);
  // today so far: bars 0..11 = 12×300 = 3600; prior by the same time (bars 0..11 each: 12×100 = 1200) → 3×
  assert.ok(Math.abs(r - 3) < 1e-9, String(r));
});

test('relVolAtTime reads the full day once the session is over', () => {
  const bars = [];
  for (const d of ['2026-10-14', '2026-10-15', '2026-10-16', '2026-10-19'])
    bars.push(...session(d, Array(78).fill(100)));
  bars.push(...session('2026-10-20', Array(78).fill(250)));
  assert.ok(Math.abs(relVolAtTime(bars, ET) - 2.5) < 1e-9);
});

test('relVolAtTime: too little history or half-day sessions give null / are skipped', () => {
  const few = [
    ...session('2026-10-15', Array(78).fill(100)),
    ...session('2026-10-16', Array(78).fill(100)),
  ];
  assert.equal(relVolAtTime(few, ET), null);
  assert.equal(relVolAtTime([], ET), null);
  // an early-close day (only 30 bars, ends 12:00) must not drag the average to zero at 15:55
  const bars = [];
  for (const d of ['2026-10-13', '2026-10-14', '2026-10-15', '2026-10-16'])
    bars.push(...session(d, Array(78).fill(100)));
  bars.push(...session('2026-10-17', Array(30).fill(100)));
  bars.push(...session('2026-10-20', Array(78).fill(200)));
  assert.ok(Math.abs(relVolAtTime(bars, ET) - 2) < 1e-9);
});

// ── the whole pipeline, with a fake Yahoo ───────────────────────────────────

const quote = (symbol, over) => ({ ...AIOK, symbol, longName: `${symbol} Inc`, ...over });
function fake(quotes, { floats = {}, rvt = {}, poc = {}, total } = {}) {
  const calls = { screen: [], profiles: [], rvt: [], poc: [] };
  return {
    calls,
    deps: {
      screenPage: async (query, sortField, sortType, offset) => {
        calls.screen.push({ query, sortField, sortType, offset });
        return { quotes, total: total ?? quotes.length };
      },
      profiles: async (syms) => {
        calls.profiles.push([...syms]);
        const m = new Map();
        for (const s of syms)
          if (s in floats)
            m.set(s, {
              float: floats[s],
              sector: 'Technology',
              industry: 'Software',
              shortFloat: null,
              insiders: null,
              institutions: null,
              beta: null,
              peg: null,
              revGrowth: null,
              roe: null,
            });
        return m;
      },
      rvolsAtTime: async (syms) => {
        calls.rvt.push([...syms]);
        const m = new Map();
        for (const s of syms) if (s in rvt) m.set(s, rvt[s]);
        return m;
      },
      pocSetups: async (syms) => {
        calls.poc.push([...syms]);
        const m = new Map();
        for (const s of syms) if (s in poc) m.set(s, poc[s]);
        return m;
      },
    },
  };
}
const req = (filters, over = {}) => ({
  filters,
  sort: { key: 'changePct', dir: 'desc' },
  columns: ['price', 'changePct'],
  ...over,
});

test('runScreen: the 5-pillar screen looks up float first, then rel vol only for the survivors', async () => {
  const five = PRESETS.find((p) => p.name === '5 pillar').filters;
  const f = fake(
    [
      quote('BOLT', { regularMarketChangePercent: 49.6 }),
      quote('BKYI', { regularMarketChangePercent: 23.7 }),
      quote('BIG', { regularMarketChangePercent: 20 }),
      quote('SLOW', { regularMarketChangePercent: 15 }),
      quote('GLNDW', { longName: 'Glenda Warrants' }),
    ],
    {
      floats: { BOLT: 1.64e6, BKYI: 1.91e6, BIG: 80e6, SLOW: 5e6, GLNDW: 2e6 },
      rvt: { BOLT: 461.79, BKYI: 4.2, BIG: 9, SLOW: 1.2 },
    },
  );
  const res = await runScreen(req(five), f.deps, NOW);
  assert.deepEqual(
    res.rows.map((r) => r.symbol),
    ['BOLT', 'BKYI'],
  );
  assert.equal(res.total, 2);
  assert.deepEqual(f.calls.profiles[0], ['BOLT', 'BKYI', 'BIG', 'SLOW']); // warrant removed before any lookup
  assert.deepEqual(f.calls.rvt[0], ['BOLT', 'BKYI', 'SLOW']); // BIG failed the float filter: no intraday call for it
  assert.equal(res.rows[0].float, 1.64e6);
  assert.equal(res.rows[0].rvolTime, 461.79);
  assert.equal(f.calls.screen.length, 1);
});

test('runScreen: a column that needs a lookup triggers it only when visible', async () => {
  const f = fake([quote('A'), quote('B', { regularMarketChangePercent: 5 })], {
    floats: { A: 3e6, B: 4e6 },
  });
  const noCol = await runScreen(req({ price: { min: 1 } }), f.deps, NOW);
  assert.equal(f.calls.profiles.length, 0);
  assert.equal(noCol.rows[0].float, undefined);
  const withCol = await runScreen(
    req({ price: { min: 1 } }, { columns: ['price', 'float', 'sector'] }),
    f.deps,
    NOW,
  );
  assert.equal(f.calls.profiles.length, 1);
  assert.equal(withCol.rows[0].float, 3e6);
  assert.equal(withCol.rows[0].sector, 'Technology');
});

test('runScreen: names with no float are left out by a float filter and the note says so', async () => {
  const f = fake([quote('A'), quote('B')], { floats: { A: 3e6 } }); // B: Yahoo answered nothing for it
  const res = await runScreen(req({ float: { max: 10e6 } }), f.deps, NOW);
  assert.deepEqual(
    res.rows.map((r) => r.symbol),
    ['A'],
  );
  assert.ok(res.notes.some((n) => /did not answer/.test(n)));
});

test('runScreen: pages through a big pool and warns when it is capped', async () => {
  const mkq = (n) => Array.from({ length: n }, (_, i) => quote(`S${i}_${n}`));
  const f = fake(mkq(250), { total: 5000 });
  const res = await runScreen(req({}), f.deps, NOW);
  assert.equal(f.calls.screen.length, 3); // 3 pages max
  assert.deepEqual(
    f.calls.screen.map((c) => c.offset),
    [0, 250, 500],
  );
  assert.ok(res.notes.some((n) => /5,000 names/.test(n)));
});

test('runScreen: enrichment is capped so a loose screen cannot fan out to hundreds of calls', async () => {
  const quotes = Array.from({ length: 200 }, (_, i) => quote(`T${i}`));
  const floats = Object.fromEntries(quotes.map((q) => [q.symbol, 5e6]));
  const f = fake(quotes, { floats });
  const res = await runScreen(req({ float: { max: 10e6 } }), f.deps, NOW);
  assert.equal(f.calls.profiles[0].length, 150);
  assert.ok(res.notes.some((n) => /first 150 of 200/.test(n)));
  assert.equal(res.total, 150);
});

test('runScreen: limit and sort apply after filtering', async () => {
  const f = fake([
    quote('A', { regularMarketVolume: 1e6 }),
    quote('B', { regularMarketVolume: 9e6 }),
    quote('C', { regularMarketVolume: 5e6 }),
  ]);
  const res = await runScreen(
    { ...req({}), sort: { key: 'volume', dir: 'asc' }, limit: 2 },
    f.deps,
    NOW,
  );
  assert.deepEqual(
    res.rows.map((r) => r.symbol),
    ['A', 'C'],
  );
  assert.equal(res.total, 3);
});

// ── saved screens ───────────────────────────────────────────────────────────
import {
  pillsFor,
  pushHistory,
  redo,
  seedScreens,
  sameFilters,
  startHistory,
  toggleIn,
  undo,
  withFilter,
  withPill,
  withRange,
} from './screens.ts';

test('withRange / withFilter: an empty edit removes the filter instead of leaving a dead key', () => {
  let f = withRange({}, 'price', { min: 2, max: 20 });
  assert.deepEqual(f, { price: { min: 2, max: 20 } });
  f = withRange(f, 'price', { max: 20 });
  assert.deepEqual(f, { price: { max: 20 } });
  f = withRange(f, 'price', {});
  assert.deepEqual(f, {});
  assert.deepEqual(withFilter({ sector: { in: ['Energy'] } }, 'sector', { in: [] }), {});
});

test('seedScreens gives every preset its pills, including active non-quick filters', () => {
  const screens = seedScreens();
  assert.equal(screens[0].name, '5 pillar');
  assert.ok(screens.every((s) => s.pills.includes('price')));
  const ipo = screens.find((s) => s.name === 'Recent IPOs');
  assert.ok(ipo.pills.includes('listedDays'), 'an active filter must always have a pill');
  assert.ok(new Set(screens.map((s) => s.id)).size === screens.length);
});

test('withPill inserts in registry order and is idempotent; pillsFor appends missing active ones', () => {
  const base = ['price', 'marketCap'];
  assert.deepEqual(withPill(base, 'volume'), ['price', 'volume', 'marketCap']);
  assert.deepEqual(withPill(withPill(base, 'volume'), 'volume'), ['price', 'volume', 'marketCap']);
  assert.deepEqual(pillsFor({ beta: { max: 1 } }, ['price']), ['price', 'beta']);
});

test('toggleIn adds then removes', () => {
  assert.deepEqual(toggleIn(['a'], 'b'), ['a', 'b']);
  assert.deepEqual(toggleIn(['a', 'b'], 'a'), ['b']);
});

test('history: undo, redo, and a new edit drops the redo branch', () => {
  let h = startHistory({ price: { min: 1 } });
  h = pushHistory(h, { price: { min: 2 } }, sameFilters);
  h = pushHistory(h, { price: { min: 3 } }, sameFilters);
  h = pushHistory(h, { price: { min: 3 } }, sameFilters); // identical: no new step
  assert.equal(h.past.length, 2);
  h = undo(h);
  assert.deepEqual(h.present, { price: { min: 2 } });
  h = redo(h);
  assert.deepEqual(h.present, { price: { min: 3 } });
  h = undo(undo(h));
  assert.deepEqual(h.present, { price: { min: 1 } });
  assert.deepEqual(undo(h), h); // nothing left to undo
  h = pushHistory(h, { price: { min: 9 } }, sameFilters);
  assert.equal(h.future.length, 0);
});

test('runScreen: no Yahoo float falls back to shares outstanding, flagged, and says so', async () => {
  const noFloat = quote('NF', { sharesOutstanding: 3_000_000 });
  const f = fake([noFloat, quote('WF', { sharesOutstanding: 90_000_000 })], {
    floats: { NF: null, WF: 4e6 },
  });
  const res = await runScreen(req({ float: { max: 10e6 } }), f.deps, NOW);
  assert.deepEqual(res.rows.map((r) => r.symbol).sort(), ['NF', 'WF']);
  const nf = res.rows.find((r) => r.symbol === 'NF');
  assert.equal(nf.float, 3_000_000);
  assert.equal(nf.floatEstimated, true);
  assert.equal(res.rows.find((r) => r.symbol === 'WF').floatEstimated, undefined);
  assert.ok(res.notes.some((n) => /shares outstanding/.test(n)));
  // …but an estimate that is too big still fails the filter
  const big = fake([quote('BIG', { sharesOutstanding: 90_000_000 })], { floats: { BIG: null } });
  assert.equal((await runScreen(req({ float: { max: 10e6 } }), big.deps, NOW)).rows.length, 0);
});

// ── markets ─────────────────────────────────────────────────────────────────
import { MARKETS, MARKET_LIST, marketOf, universeFor, venueOf } from './markets.ts';
import { filtersForMarket } from './screens.ts';

test('marketOf falls back to the US for missing / unknown ids (old saved screens)', () => {
  assert.equal(marketOf(undefined).id, 'us');
  assert.equal(marketOf('mars').id, 'us');
  assert.equal(marketOf('in').currency, 'INR');
});

test('every market has a default venue that exists, and a zone Intl accepts', () => {
  for (const m of MARKET_LIST) {
    assert.ok(m.defaultVenues.length > 0, m.id);
    for (const v of m.defaultVenues) assert.ok(v in m.venues, `${m.id}:${v}`);
    assert.doesNotThrow(() => zoneClock(m.tz), m.id);
  }
});

test('yahooQuery uses the market region and its own exchange codes', () => {
  const india = MARKETS.in;
  const q = yahooQuery(universeFor(india), NOW, india);
  const flat = JSON.stringify(q);
  assert.match(flat, /"region","in"/);
  assert.match(flat, /"exchange","NSI"/);
  assert.doesNotMatch(flat, /NMS|NYQ/);
  // Germany defaults to XETRA only, not the regional duplicates
  const de = JSON.stringify(yahooQuery(universeFor(MARKETS.de), NOW, MARKETS.de));
  assert.match(de, /"exchange","GER"/);
  assert.doesNotMatch(de, /STU|MUN|FRA/);
});

test('venueOf maps codes per market and passes unknown codes through', () => {
  assert.equal(venueOf('NSI', MARKETS.in), 'NSE');
  assert.equal(venueOf('NMS', MARKETS.us), 'NASDAQ');
  assert.equal(venueOf('ZZZ', MARKETS.us), 'ZZZ');
  assert.equal(venueOf(undefined, MARKETS.us), '');
});

test('sanitizeFilters accepts venues of the chosen market only', () => {
  assert.deepEqual(sanitizeFilters({ exchange: { in: ['NSE', 'NYSE'] } }, 'in'), {
    exchange: { in: ['NSE'] },
  });
  assert.deepEqual(sanitizeFilters({ exchange: { in: ['NSE', 'NYSE'] } }), {
    exchange: { in: ['NYSE'] },
  });
});

test('switching market keeps non-currency filters and drops price / market cap / $ volume', () => {
  const us = {
    ...universeFor(MARKETS.us),
    price: { min: 2, max: 20 },
    marketCap: { max: 5e8 },
    changePct: { min: 10 },
    volume: { min: 1e6 },
    sector: { in: ['Energy'] },
  };
  const next = filtersForMarket(us, 'in');
  assert.deepEqual(next, {
    exchange: { in: ['NSE'] },
    changePct: { min: 10 },
    volume: { min: 1e6 },
    sector: { in: ['Energy'] },
  });
  assert.equal(next.commonOnly, undefined); // US-only option
});

test('runScreen: rel vol at time is measured on the market clock', async () => {
  const f = fake([quote('RELIANCE.NS')], {
    floats: { 'RELIANCE.NS': 5e6 },
    rvt: { 'RELIANCE.NS': 3 },
  });
  let tz;
  const deps = { ...f.deps, rvolsAtTime: async (syms, z) => ((tz = z), f.deps.rvolsAtTime(syms)) };
  await runScreen({ ...req({ rvolTime: { min: 2 } }), market: 'in' }, deps, NOW);
  assert.equal(tz, 'Asia/Kolkata');
  assert.match(JSON.stringify(f.calls.screen[0].query), /"region","in"/);
});

// ── POC first retest ────────────────────────────────────────────────────────

const UTC = zoneClock('UTC');
const D1 = Date.UTC(2026, 9, 26, 14, 0) / 1000;
const D2 = Date.UTC(2026, 9, 27, 14, 0) / 1000;
const bar = (t, lo, hi, v = 1000, up = true) => ({
  time: t,
  open: up ? lo : hi,
  high: hi,
  low: lo,
  close: up ? hi : lo,
  volume: v,
});
/** Yesterday: heavy trade at 99.9–100.1, thin tails at 98 and 102. POC ≈ 99.9, zone ≈ 99.8–100.2. */
const yesterday = () => [
  ...Array.from({ length: 16 }, (_, i) => bar(D1 + i * 300, 99.9, 100.1, 10_000)),
  bar(D1 + 16 * 300, 97.9, 98.1, 100),
  bar(D1 + 17 * 300, 97.9, 98.1, 100),
  bar(D1 + 18 * 300, 101.9, 102.1, 100),
  bar(D1 + 19 * 300, 101.9, 102.1, 100),
];
const today = (...ranges) => ranges.map(([lo, hi, up], i) => bar(D2 + i * 300, lo, hi, 1000, up));

test('pocZone: the heavy rows around the POC, not the thin tails', () => {
  const z = pocZone(yesterday());
  assert.ok(z.poc > 99.9 && z.poc < 100.25, `poc ${z.poc}`);
  assert.ok(z.lo >= 99.7 && z.lo <= 99.9, `lo ${z.lo}`);
  assert.ok(z.hi >= 100.1 && z.hi <= 100.3, `hi ${z.hi}`);
});

test('pocSetup: departs up ≥1%, first pullback into the top edge = long first touch', () => {
  const s = pocSetup(
    [
      ...yesterday(),
      ...today([100.3, 100.6], [100.9, 101.6], [101.2, 101.8], [100.1, 101.3, false]),
    ],
    UTC,
  );
  assert.equal(s.stage, 'Long · first touch');
  assert.ok(s.entry > s.stop);
  assert.equal(s.fromTime, D1); // the zone is drawn from yesterday's first bar
  assert.equal(s.departTime, D2 + 300); // the bar that closed ≥1% above the zone
  assert.equal(s.touchTime, D2 + 900); // the first bar back at the top edge
  assert.ok(s.edgeDistPct <= 0.5);
});

test('pocSetup: before the touch it is approaching / away; a second touch is a retest', () => {
  const up = [
    [100.3, 100.6],
    [100.9, 101.6],
    [101.2, 101.8],
  ];
  assert.equal(pocSetup([...yesterday(), ...today(...up)], UTC).stage, 'Long · away');
  assert.equal(
    pocSetup([...yesterday(), ...today(...up, [100.5, 101.2, false])], UTC, 100.6).stage,
    'Long · approaching',
  );
  assert.equal(
    pocSetup(
      [...yesterday(), ...today(...up, [100.1, 101.3, false], [100.6, 101.4], [100.0, 101, false])],
      UTC,
    ).stage,
    'Retested',
  );
});

test('pocSetup: a close through the far edge is broken; the short side mirrors the long', () => {
  const up = [
    [100.3, 100.6],
    [100.9, 101.6],
    [101.2, 101.8],
  ];
  assert.equal(
    pocSetup([...yesterday(), ...today(...up, [99.5, 100.8, false])], UTC).stage,
    'Broken',
  );
  const short = pocSetup(
    [
      ...yesterday(),
      ...today([99.5, 99.8, false], [98.4, 99.1, false], [98.2, 98.8, false], [98.7, 99.95]),
    ],
    UTC,
  );
  assert.equal(short.stage, 'Short · first touch');
  assert.ok(short.entry < short.stop);
});

test('pocSetup: never left the zone = not departed; one session only = null', () => {
  assert.equal(
    pocSetup([...yesterday(), ...today([99.9, 100.2], [99.95, 100.3])], UTC).stage,
    'Not departed',
  );
  assert.equal(pocSetup(yesterday(), UTC), null);
});

test('pocSetup: a wick past the 1% line that closes back is not a move away', () => {
  // HARDWYN 2026-10-28: one print 1% under the zone in the last bar, which closed back inside it
  const wick = { time: D2 + 600, open: 100, high: 100.1, low: 98.5, close: 100, volume: 5000 };
  const s = pocSetup([...yesterday(), ...today([99.9, 100.2], [99.95, 100.3]), wick], UTC);
  assert.equal(s.stage, 'Not departed');
  assert.equal(s.departTime, null);
});

test('pocCandidates: 1-minute bars covering a session build its profile, as the chart SVP does', () => {
  // the 5-minute bars put yesterday's volume at ~100; the 1-minute bars underneath put it at ~101
  const fine = [
    ...Array.from({ length: 90 }, (_, i) => bar(D1 + i * 60, 100.9, 101.1, 3000)),
    bar(D1 + 90 * 60, 97.9, 98.1, 10),
    bar(D1 + 99 * 60, 101.9, 102.1, 10),
  ];
  const bars = [...yesterday(), ...today([99.9, 100.2])];
  const coarse = pocCandidates(bars, UTC)[0];
  const fromFine = pocCandidates(bars, UTC, null, POC_DEFAULTS, fine)[0];
  assert.ok(coarse.poc < 100.25, `coarse poc ${coarse.poc}`);
  assert.ok(fromFine.poc > 100.8 && fromFine.poc < 101.2, `fine poc ${fromFine.poc}`);
  // 1-minute bars that start after the session began do not cover it: the 5-minute profile stays
  const late = fine.filter((b) => b.time > D1);
  assert.equal(pocCandidates(bars, UTC, null, POC_DEFAULTS, late)[0].poc, coarse.poc);
});

const DAY = 86_400;
/** A full session `days` after D1 whose volume sits at `center` (POC ≈ center), 16 five-minute bars. */
const heavyDay = (days, center) =>
  Array.from({ length: 16 }, (_, i) =>
    bar(D1 + days * DAY + i * 300, center - 0.1, center + 0.1, 10_000),
  );
const on = (days, ...ranges) =>
  ranges.map(([lo, hi, up], i) => bar(D1 + days * DAY + i * 300, lo, hi, 1000, up));

test('pocSetup: an older POC left over several days and now pulling back is found, with its age', () => {
  const s = pocSetup(
    [
      ...yesterday(), // day 0: POC ≈ 100
      ...heavyDay(1, 105), // day 1: price lives at 105, its own POC
      // today: from 105 back down to 102.6, the last bar closing at its low
      ...on(2, [104.4, 104.9, false], [103.4, 104.4, false], [102.6, 103.4, false]),
    ],
    UTC,
  );
  assert.equal(s.stage, 'Long · pulling back');
  assert.equal(s.sessionsAgo, 2);
  assert.equal(s.fromTime, D1);
  assert.equal(s.departTime, D1 + DAY); // left on day 1, not today
  assert.ok(Math.abs(s.extreme - 105.1) < 1e-9);
  assert.ok(s.pullbackPct > 30 && s.pullbackPct < 60, `pullback ${s.pullbackPct}`);
});

test('pocSetup: the first touch of a POC from days ago is a first touch', () => {
  const s = pocSetup(
    [
      ...yesterday(),
      ...heavyDay(1, 105),
      ...on(2, [103.4, 104.4, false], [101.4, 103.4, false], [100.15, 101.4, false]),
    ],
    UTC,
  );
  assert.equal(s.stage, 'Long · first touch');
  assert.equal(s.sessionsAgo, 2);
  assert.equal(s.touchTime, D1 + 2 * DAY + 600);
});

test('pocSetup: a POC price already came back to on an earlier day is tested, not a setup', () => {
  const s = pocSetup(
    [
      ...yesterday(),
      ...heavyDay(1, 105).slice(0, 8),
      ...on(1, ...Array.from({ length: 8 }, () => [100.0, 100.4])), // day 1 ends back at the old POC
      ...on(2, [101.0, 101.3], [101.2, 101.5]),
    ],
    UTC,
  );
  assert.notEqual(s.sessionsAgo, 2);
});

test('pocSetup: of two live POCs the one closer to a trade wins', () => {
  const s = pocSetup(
    [
      ...yesterday(), // POC ≈ 100, far below
      ...heavyDay(1, 110), // POC ≈ 110
      // today: up to 115, then back to 110.6: approaching the 110 zone, only pulling back toward 100
      ...on(2, [112, 115], [111, 115, false], [110.6, 111, false]),
    ],
    UTC,
  );
  assert.equal(s.stage, 'Long · approaching');
  assert.equal(s.sessionsAgo, 1);
});

test('pocCandidates: one entry per recent session, newest first; settings change the stages', () => {
  const bars = [
    ...yesterday(),
    ...heavyDay(1, 110),
    ...on(2, [112, 115], [111, 115, false], [110.6, 111, false]),
  ];
  const all = pocCandidates(bars, UTC);
  assert.deepEqual(
    all.map((p) => [p.sessionsAgo, p.stage]),
    [
      [1, 'Long · approaching'],
      [2, 'Long · away'], // gave back 29.7% of 100.2 → 115
    ],
  );
  assert.equal(bestSetup(all), all[0]);
  // a looser pullback share makes the older one "pulling back"; a 1-session look-back drops it
  assert.equal(
    pocCandidates(bars, UTC, null, { ...POC_DEFAULTS, pullbackPct: 25 })[1].stage,
    'Long · pulling back',
  );
  assert.equal(pocCandidates(bars, UTC, null, { ...POC_DEFAULTS, lookback: 1 }).length, 1);
});

test('runScreen: the POC scanner looks up the setup only for names that passed the cheap filters', async () => {
  const preset = {
    filters: {
      commonOnly: { on: true },
      avgVolume: { min: 5e5 },
      pocStage: { in: ['Long · first touch'] },
    },
    sort: { key: 'volume', dir: 'desc' },
    columns: ['price', 'pocStage', 'pocEntry'],
  };
  const f = fake(
    [
      quote('HIT', { averageDailyVolume3Month: 2e6 }),
      quote('THINW', { longName: 'Thin Corp Warrants' }),
      quote('FAR', { averageDailyVolume3Month: 2e6 }),
    ],
    {
      poc: {
        HIT: {
          stage: 'Long · first touch',
          poc: 10,
          zoneLo: 9.9,
          zoneHi: 10.1,
          entry: 10.1,
          stop: 9.9,
          riskPct: 2,
          edgeDistPct: -0.1,
          pocDistPct: 0.5,
        },
        FAR: {
          stage: 'Long · away',
          poc: 10,
          zoneLo: 9.9,
          zoneHi: 10.1,
          entry: 10.1,
          stop: 9.9,
          riskPct: 2,
          edgeDistPct: 4,
          pocDistPct: 5,
        },
      },
    },
  );
  const res = await runScreen(
    req(preset.filters, { sort: preset.sort, columns: [...preset.columns] }),
    f.deps,
    NOW,
  );
  assert.deepEqual(f.calls.poc[0], ['HIT', 'FAR']); // the warrant was dropped before any bar download
  assert.deepEqual(
    res.rows.map((r) => r.symbol),
    ['HIT'],
  );
  assert.equal(res.rows[0].pocEntry, 10.1);
});

// ── POC setup tracking ───────────────────────────────────────────────────────

const { evaluate, summarize } = await import('./poc-eval.ts');
/** UTC "sessions": 14:00–20:00, so 20:00 (1200 min) is the close. */
const CLOSE = 20 * 60;
const T0 = D2 + 3600; // added 15:00 on day 2
const ev = (setup, bars, now) =>
  evaluate({ addedAt: T0, ...setup }, bars, UTC, now ?? D2 + 5 * DAY, CLOSE);
const longFT = { long: true, stage: 'Long · first touch', entry: 100, stop: 99 };
const ohlc = (t, o, h, l, c) => ({ time: t, open: o, high: h, low: l, close: c, volume: 1000 });

test('evaluate: a first touch fills at the next open; +2R before the stop is a target', () => {
  const e = ev(longFT, [
    ohlc(T0 - 300, 100, 100.2, 99.9, 100), // before it was added: ignored
    ohlc(T0, 100.1, 100.4, 100, 100.3),
    ohlc(T0 + 300, 100.3, 102.5, 100.2, 102.4),
  ]);
  assert.equal(e.outcome, 'target');
  assert.equal(e.fillPrice, 100.1);
  assert.ok(Math.abs(e.r - 2) < 1e-9, `r ${e.r}`);
  assert.equal(e.done, true);
});

test('evaluate: a candle reaching both the stop and the target counts as the stop', () => {
  const e = ev(longFT, [ohlc(T0, 100, 100.1, 99.95, 100), ohlc(T0 + 300, 100, 103, 98.5, 101)]);
  assert.equal(e.outcome, 'stopped');
  assert.ok(Math.abs(e.r + 1) < 1e-9, `r ${e.r}`);
});

test('evaluate: a resting short fills only when price comes up to the entry; a gap exits at the open', () => {
  const short = { long: false, stage: 'Short · approaching', entry: 50, stop: 50.5 };
  const notYet = ev(short, [ohlc(T0, 49, 49.5, 48.8, 49.2)], T0 + 600);
  assert.equal(notYet.outcome, 'waiting');
  const gap = ev(short, [
    ohlc(T0, 49, 49.9, 48.8, 49.8),
    ohlc(T0 + 300, 49.9, 50.2, 49.8, 50),
    ohlc(D2 + DAY, 51, 51.2, 50.8, 51),
  ]);
  assert.equal(gap.outcome, 'stopped');
  assert.equal(gap.fillPrice, 50);
  assert.equal(gap.exitPrice, 51); // opened past the 50.5 stop
  assert.ok(Math.abs(gap.r + 2) < 1e-9, `r ${gap.r}`);
});

test('evaluate: the window runs to the next close; open until then, then closed at that close', () => {
  const bars = [ohlc(T0, 100, 100.3, 99.9, 100.2), ohlc(D2 + DAY, 100.2, 100.9, 100.1, 100.5)];
  const during = ev(longFT, bars, D2 + DAY + 600);
  assert.equal(during.outcome, 'open');
  assert.equal(during.done, false);
  const after = ev(longFT, bars, D2 + DAY + CLOSE * 60);
  assert.equal(after.outcome, 'closed');
  assert.ok(Math.abs(after.r - 0.5) < 1e-9, `r ${after.r}`);
  // a candle on the day after the window is never looked at
  const later = ev(longFT, [...bars, ohlc(D2 + 2 * DAY, 100.5, 105, 100.5, 104)], D2 + 3 * DAY);
  assert.equal(later.outcome, 'closed');
  // never reached the entry by the end: not filled
  const limit = { ...longFT, stage: 'Long · pulling back' };
  const nf = ev(limit, [ohlc(T0, 101, 101.5, 100.5, 101), ohlc(D2 + DAY, 101, 102, 100.4, 101.5)]);
  assert.equal(nf.outcome, 'not filled');
  assert.equal(
    summarize([
      nf,
      after,
      ev(longFT, [ohlc(T0, 100, 100.1, 99.95, 100), ohlc(T0 + 300, 100, 100, 98.5, 99)]),
    ]).totalR,
    0.5 - 1,
  );
});

test('summarize: median and average % beside the total, which one tiny-stop trade can swing', () => {
  const done = (r, pct) => ({ outcome: 'closed', done: true, r, pct });
  const s = summarize([
    done(13.5, 1),
    done(-1, -0.3),
    done(-0.5, -0.1),
    { outcome: 'open', done: false, r: 5, pct: 2 },
  ]);
  assert.equal(s.traded, 3);
  assert.equal(s.totalR, 12);
  assert.equal(s.medianR, -0.5); // the total says +12R, the typical trade lost
  assert.ok(Math.abs(s.avgPct - 0.2) < 1e-9, `avg ${s.avgPct}`);
  assert.equal(s.rightWay, 1);
});
