import assert from 'node:assert/strict';
import { test } from 'node:test';
import { aggregate, classify, coverage, parseTicks } from './bigtrades.ts';

const tk = (t, p, q, bp, ap, v = 0) => ({ t, p, q, v, bp, bq: 1, ap, aq: 1 });

test('a trade at the ask is a buy, at the bid a sell', () => {
  const r = classify([tk(1, 101, 5, 100, 101), tk(2, 100, 5, 100, 101)]);
  assert.deepEqual(r.map((x) => [x.side, x.method]), [['buy', 'quote'], ['sell', 'quote']]);
});

test('inside the spread falls to the tick rule; first trade with no history is dropped', () => {
  const r = classify([tk(1, 100.5, 5, 100, 101), tk(2, 100.6, 5, 100, 101), tk(3, 100.4, 5, 100, 101), tk(4, 100.4, 5, 100, 101)]);
  assert.deepEqual(r.map((x) => x.side), ['buy', 'sell', 'sell']);
  assert.ok(r.every((x) => x.method === 'tick'));
});

test('aggregate merges same price and side inside the window, then filters by size', () => {
  const t = [
    { t: 0, p: 10, q: 300, side: 'buy', method: 'quote' },
    { t: 500, p: 10, q: 300, side: 'buy', method: 'quote' },
    { t: 5000, p: 10, q: 300, side: 'buy', method: 'quote' },
    { t: 600, p: 10, q: 900, side: 'sell', method: 'quote' },
  ];
  const b = aggregate(t, { minQty: 500, aggMs: 1000 });
  assert.deepEqual(b.map((x) => [x.side, x.q, x.n]), [['buy', 600, 2], ['sell', 900, 1]]);
});

test('aggMs 0 keeps every trade apart', () => {
  const t = [
    { t: 0, p: 10, q: 600, side: 'buy', method: 'quote' },
    { t: 1, p: 10, q: 600, side: 'buy', method: 'quote' },
  ];
  assert.equal(aggregate(t, { minQty: 500, aggMs: 0 }).length, 2);
});

test('coverage = seen quantity over day-volume change; partial lines are skipped', () => {
  assert.equal(coverage([tk(1, 1, 1, 0, 1, 100), tk(2, 1, 40, 0, 1, 180)]), 0.5);
  assert.equal(parseTicks('{"t":1,"p":2,"q":3,"v":4}\n{"t":2,"p"').length, 1);
});

test('estimateFromBars marks volume spikes, sided by the candle, only after enough history', async () => {
  const { estimateFromBars } = await import('./bigtrades.ts');
  const bar = (i, o, c, v) => ({ time: 60 * i, open: o, high: Math.max(o, c) + 1, low: Math.min(o, c) - 1, close: c, volume: v });
  const bars = [];
  bars.push(bar(0, 100, 101, 5000)); // a spike with no history: never marked
  for (let i = 1; i <= 12; i += 1) bars.push(bar(i, 100, 100.5, 100));
  bars.push(bar(13, 100, 102, 400)); // 4x median, up candle -> buy
  bars.push(bar(14, 102, 101, 250)); // 2.5x: below 3x, not marked
  bars.push(bar(15, 101, 99, 1000)); // down candle -> sell
  bars.push(bar(16, 99, 99, 900)); // flat candle, close == previous close -> no side
  const r = estimateFromBars(bars, 3);
  assert.deepEqual(r.map((b) => [b.t / 60000, b.side, b.q]), [[13, 'buy', 400], [15, 'sell', 1000]]);
  assert.equal(r[0].p, (103 + 99 + 102) / 3);
});
