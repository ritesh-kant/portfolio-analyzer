import assert from 'node:assert/strict';
import { test } from 'node:test';
import { absorption, deltaFlip, failedAggression, lvnPrice, usualVolume } from './ar-orderflow.ts';

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

test("rule 'video': POC in the wick and a bearish close, no volume threshold; any delta flip that closes down", () => {
  // short at a high: open 100, high 110, low 98, close 99 → body 99–100, upper wick 100–110; ordinary volume (1×)
  const bars = [...history, bar(100, 110, 98, 99, 100), bar(99, 100, 95, 96, 100)];
  const inWick = fp([[98, 5, 5], [101, 10, 10], [107, 60, 70]], 3); // POC row 107 → middle 108.5, in the wick
  const a = absorption(bars, inWick, 12, 'short', 1.5, 'video');
  assert.equal(a.ok, true, a.why.join());
  assert.equal(absorption(bars, inWick, 12, 'short', 1.5).ok, false, 'our rule wants 1.5× volume');
  const inBody = fp([[98, 60, 70], [101, 10, 10], [107, 5, 5]], 3); // POC 99.5: inside the body
  assert.match(absorption(bars, inBody, 12, 'short', 1.5, 'video').why.join(), /not in the upper wick/);
  const green = [...history, bar(99, 110, 98, 100, 100)];
  assert.match(absorption(green, inWick, 12, 'short', 1.5, 'video').why.join(), /did not close bearish/);
  // flip: a small negative delta is enough, but the candle must close down
  const tiny = fp([[95, 51, 49]], 5);
  assert.equal(deltaFlip(bars, tiny, 13, 12, 'short', 0.1, 'video').ok, true);
  assert.equal(deltaFlip(bars, tiny, 13, 12, 'short', 0.1).ok, false, 'our rule wants 10% of volume');
  const up = [...bars.slice(0, 13), bar(96, 100, 95, 99, 100)];
  assert.match(deltaFlip(up, tiny, 13, 12, 'short', 0.1, 'video').why, /closed up/);
});

test("failedAggression: the 26 Nov NIFTY sequence — heavy selling at 13:55 fails, 14:05 flips (rule 'tree')", () => {
  // 13:50, 13:55, 14:00, 14:05 (prices and deltas from the estimated footprint), zone near edge 25,910.69
  const bars = [
    bar(25946.3, 25959, 25906, 25906, 35425),
    bar(25906, 25923, 25881.5, 25893.2, 53105),
    bar(25893.8, 25909.8, 25882, 25897.9, 28665),
    bar(25902, 25960, 25891.2, 25959.2, 28990),
  ];
  const d = (delta) => ({ poc: 0, levels: [{ price: 25900, bid: 1, ask: 1 }], delta, total: 1e5, step: 5 });
  const fps = [d(-18136), d(-27245), d(-2846), d(20051)];
  // at 14:00: the 13:55 selling has held (14:00's low 25,882 is above 25,881.5); 13:50 has not (13:55 went lower)
  assert.deepEqual(failedAggression(bars, fps, 0, 2, 'long', 25910.69).agg?.index, 1);
  assert.equal(deltaFlip(bars, fps[2], 2, 1, 'long', 0, 'video').ok, false, '14:00 delta still negative');
  assert.equal(deltaFlip(bars, fps[3], 3, 1, 'long', 0, 'video').ok, true, '14:05 flips and closes up');
  assert.equal(failedAggression(bars, fps, 0, 3, 'long', 25910.69).agg?.index, 1);
  // a later candle under 25,881.5 means the selling did not fail
  const lower = [...bars.slice(0, 3), bar(25902, 25960, 25870, 25959.2, 28990)];
  assert.equal(failedAggression(lower, fps, 0, 3, 'long', 25910.69).agg, null);
  // no footprint at the level → noData
  assert.equal(failedAggression(bars, [null, null, null, null], 0, 3, 'long', 25910.69).noData, true);
});
