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
