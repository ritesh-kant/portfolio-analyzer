import assert from 'node:assert/strict';
import { test } from 'node:test';
import { diagonalImbalances, flagRuns, stackedOnly, stackedZones, zoneActive, zoneAt } from './stacked-imbalance.ts';

const bar = (time, o, h, l, c) => ({ time, open: o, high: h, low: l, close: c, volume: 100 });
/** A footprint from [bid, ask] pairs, lowest row first, rows `step` apart from `base`. */
const fp = (base, step, rows, source) => ({
  poc: 0,
  step,
  delta: 0,
  total: 0,
  levels: rows.map(([bid, ask], i) => ({ price: base + i * step, bid, ask })),
  ...(source ? { source } : {}),
});

test('diagonal imbalance compares ask with the bid one row DOWN, bid with the ask one row UP', () => {
  const { ask, bid } = diagonalImbalances(
    [
      { price: 100, bid: 10, ask: 1 },
      { price: 101, bid: 50, ask: 30 }, // ask 30 ≥ 3 × bid below 10 → buyer imbalance
      { price: 102, bid: 5, ask: 10 }, // ask 10 < 3 × 50
    ],
    3,
  );
  assert.deepEqual(ask, [false, true, false]);
  // bid 50 at 101 vs ask 10 at 102 → seller imbalance; bid 10 at 100 vs ask 30 above → no
  assert.deepEqual(bid, [false, true, false]);
});

test('runs: only stretches of at least n rows count', () => {
  const f = [true, true, false, true, true, true, false, true];
  assert.deepEqual(flagRuns(f, 3), [[3, 5]]);
  assert.deepEqual(flagRuns(f, 2), [[0, 1], [3, 5]]);
  assert.deepEqual(stackedOnly(f, 3), [false, false, false, true, true, true, false, false]);
  assert.deepEqual(stackedOnly(f, 0), f);
});

test('a buy stack becomes a zone from its lowest row to the top of its highest, until price trades through', () => {
  // rows 100…105, asks overwhelm the bid one row down on 101, 102, 103 (a stack of 3)
  const candle0 = fp(100, 1, [
    [10, 0],
    [10, 40],
    [10, 40],
    [10, 40],
    [10, 5],
    [10, 5],
  ]);
  const bars = [
    bar(0, 100, 106, 100, 105),
    bar(60, 105, 107, 104, 106), // stays above the zone
    bar(120, 106, 106, 101.5, 102), // dips INTO the zone: still standing
    bar(180, 102, 103, 100.5, 101), // trades below 101 (the zone's bottom): used
  ];
  const zones = stackedZones(bars, [candle0, null, null, null], { ratio: 3, minRows: 3 });
  assert.equal(zones.length, 1);
  const z = zones[0];
  assert.deepEqual([z.side, z.lo, z.hi, z.rows, z.index, z.usedAt, z.estimated], ['buy', 101, 104, 3, 0, 3, true]);
  assert.ok(zoneActive(z, 2));
  assert.ok(!zoneActive(z, 3));
  // a long filling at 102 on candle 2 has the zone under it; a short does not; before it printed there is none
  assert.equal(zoneAt(zones, 'buy', 102, 2), z);
  assert.equal(zoneAt(zones, 'sell', 102, 2), null);
  assert.equal(zoneAt(zones, 'buy', 108, 2), null);
  assert.equal(zoneAt(zones, 'buy', 108, 2, 5), z, 'tolerance widens the zone');
});

test('a sell stack is the mirror; two rows are not a stack of three; ticks are not estimated', () => {
  const sell = fp(
    200,
    0.5,
    [
      [2, 1], // bid 2 < 3 × the ask above (1 → floor 1): no imbalance
      [60, 1],
      [60, 10],
      [60, 10],
      [5, 10],
    ],
    'ticks',
  );
  const two = fp(300, 1, [
    [10, 0],
    [10, 40],
    [10, 40],
    [10, 5],
  ]);
  const bars = [bar(0, 202, 202.5, 200, 200.5), bar(60, 300, 304, 300, 303), bar(120, 201, 203, 200, 201)];
  const zones = stackedZones(bars, [sell, two, null], { minRows: 3 });
  assert.equal(zones.length, 1);
  const z = zones[0];
  // bid rows 200.5, 201, 201.5 each beat the ask one row up by 3× → zone 200.5 … 202
  assert.deepEqual([z.side, z.lo, z.hi, z.rows, z.estimated], ['sell', 200.5, 202, 3, false]);
  assert.equal(z.usedAt, 1, 'the next candle (high 304) trades above 202: through a sell zone');
  assert.equal(stackedZones(bars, [sell, two, null], { minRows: 2 }).length, 2);
});

test('no look-ahead: zones found on a prefix are the same zones, with "used" only when it happened inside it', () => {
  const rows = [
    [10, 0],
    [10, 40],
    [10, 40],
    [10, 40],
    [10, 5],
  ];
  const bars = [];
  const fps = [];
  for (let i = 0; i < 12; i++) {
    const base = 100 + (i % 4) * 3;
    bars.push(bar(i * 60, base + 2, base + 5, base - (i % 3 === 2 ? 4 : 0), base + 3));
    fps.push(i % 2 ? null : fp(base, 1, rows));
  }
  const full = stackedZones(bars, fps);
  for (let cut = 1; cut <= bars.length; cut++) {
    const part = stackedZones(bars.slice(0, cut), fps.slice(0, cut));
    const expect = full
      .filter((z) => z.index < cut)
      .map((z) => ({ ...z, usedAt: z.usedAt != null && z.usedAt < cut ? z.usedAt : null }));
    assert.deepEqual(part, expect, `cut ${cut}`);
  }
});

test('a gap in the rows is no evidence, and a stack taller than ATR (a bad print) is dropped', () => {
  // rows 100, 101, then nothing until 105: with the step given, 105 is not compared with 101
  const levels = [
    { price: 100, bid: 10, ask: 1 },
    { price: 101, bid: 10, ask: 40 },
    { price: 105, bid: 10, ask: 40 },
  ];
  assert.deepEqual(diagonalImbalances(levels, 3, 1).ask, [false, true, false]);
  assert.deepEqual(diagonalImbalances(levels, 3).ask, [false, true, true], 'without a step every neighbour counts');

  // 20 quiet 2-point candles (ATR ≈ 2), then one whose footprint stacks 10 rows (10 points tall)
  const bars = [];
  const fps = [];
  for (let i = 0; i < 20; i++) {
    bars.push(bar(i * 60, 100, 101, 99, 100));
    fps.push(null);
  }
  const tall = fp(100, 1, [[10, 0], ...Array.from({ length: 10 }, () => [10, 40]), [10, 5]]);
  bars.push(bar(20 * 60, 100, 112, 100, 111));
  fps.push(tall);
  assert.equal(stackedZones(bars, fps).length, 0, 'taller than 1 × ATR: dropped');
  assert.equal(stackedZones(bars, fps, { maxAtr: 10 }).length, 1);
});
