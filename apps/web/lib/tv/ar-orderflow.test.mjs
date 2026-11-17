import assert from 'node:assert/strict';
import { test } from 'node:test';
import { absorption, deltaFlip, lvnPrice, usualVolume } from './ar-orderflow.ts';

const bar = (o, h, l, c, v = 100) => ({ time: 0, open: o, high: h, low: l, close: c, volume: v });
/** Footprint from [price, bid, ask] rows (low → high). */
function fp(rows, step, source) {
  const levels = rows.map(([price, bid, ask]) => ({ price, bid, ask }));
  let poc = 0;
  levels.forEach((l, i) => {
    if (l.bid + l.ask > levels[poc].bid + levels[poc].ask) poc = i;
  });
  const delta = levels.reduce((s, l) => s + l.ask - l.bid, 0);
  const total = levels.reduce((s, l) => s + l.ask + l.bid, 0);
  return { poc, levels, delta, total, step, ...(source ? { source } : {}) };
}

// 12 ordinary candles of 100, then the candle under test
const history = Array.from({ length: 12 }, () => bar(100, 101, 99, 100, 100));

test('usual volume is the median of the 12 candles before, never the candle itself', () => {
  const bars = [...history, bar(100, 101, 99, 100, 5000)];
  assert.equal(usualVolume(bars, 12), 100);
  assert.equal(usualVolume(bars, 0), 0);
});

test('absorption (long): heavy volume, POC at the low, closed in the top half', () => {
  const bars = [...history, bar(105, 106, 97, 103, 300)];
  const f = fp([[97, 150, 60], [100, 20, 20], [103, 20, 30]], 3);
  const a = absorption(bars, f, 12, 'long', 1.5);
  assert.ok(a.ok, a.why.join());
  assert.equal(a.volRatio, 3);
  assert.ok(a.pocPos < 1 / 3);
  // the mirror test for a short fails on every count
  const s = absorption(bars, f, 12, 'short', 1.5);
  assert.ok(!s.ok);
  assert.ok(s.why.some((w) => /POC not at the high/.test(w)));
  assert.ok(s.why.some((w) => /closed near the high/.test(w)));
});

test('absorption fails with the reasons: normal volume, POC mid-candle, closed at the low', () => {
  const bars = [...history, bar(105, 106, 97, 97.5, 110)];
  const a = absorption(bars, fp([[97, 10, 10], [100, 60, 60], [103, 10, 10]], 3), 12, 'long', 1.5);
  assert.ok(!a.ok);
  assert.equal(a.why.length, 3, a.why.join(' | '));
  assert.match(a.why[0], /volume 1\.1× usual \(needs 1\.5×\)/);
  // no footprint, or no range: nothing to say
  assert.equal(absorption(bars, null, 12, 'long', 1.5), null);
  assert.equal(absorption([...history, bar(1, 1, 1, 1, 500)], fp([[1, 1, 1]], 1), 12, 'long', 1.5), null);
});

test('delta flip: delta on the trade side by the share, a candle in the trade direction beyond the absorption close', () => {
  const bars = [bar(105, 106, 97, 103, 300), bar(103, 110, 102, 109)];
  const buyers = fp([[102, 20, 40], [105, 10, 30]], 3); // delta +40 of 100
  const ok = deltaFlip(bars, buyers, 1, 0, 'long', 0.1);
  assert.ok(ok.ok);
  assert.ok(Math.abs(ok.share - 0.4) < 1e-9);
  const weak = deltaFlip(bars, fp([[102, 30, 34], [105, 18, 18]], 3), 1, 0, 'long', 0.1);
  assert.ok(!weak.ok);
  assert.match(weak.why, /delta \+4 \(4% of volume on the buy side, needs 10%\)/);
  // delta is fine but the candle is red: no change in price behaviour
  const red = deltaFlip([bars[0], bar(109, 110, 102, 104)], buyers, 1, 0, 'long', 0.1);
  assert.ok(!red.ok);
  assert.match(red.why, /did not close up/);
  assert.equal(deltaFlip(bars, null, 1, 0, 'long', 0.1), null);
  // short: sellers' share
  const sell = deltaFlip([bar(95, 103, 94, 97), bar(97, 98, 90, 91)], fp([[90, 40, 20], [93, 30, 10]], 3), 1, 0, 'short', 0.1);
  assert.ok(sell.ok);
});

test('LVN: the thinnest inner row on the pull-back side of the close, nearest the close on ties', () => {
  const b = bar(100, 118, 100, 117);
  const f = fp([[100, 5, 5], [103, 30, 30], [106, 2, 2], [109, 2, 2], [112, 40, 40], [115, 1, 1]], 3);
  // the edge rows (100, 115) are left out; 106 and 109 tie, 109 is nearer the close
  assert.equal(lvnPrice(b, f, 'long'), 110.5);
  // short: only rows at/above the close count; none inside → null
  assert.equal(lvnPrice(bar(118, 118, 100, 101), f, 'short') != null, true);
  assert.equal(lvnPrice(b, f, 'short'), null);
  assert.equal(lvnPrice(b, fp([[100, 1, 1], [103, 1, 1]], 3), 'long'), null);
});
