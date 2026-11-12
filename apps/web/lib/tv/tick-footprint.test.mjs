import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  buildTickFootprints,
  mergeFootprints,
  packSteps,
  unpackSteps,
  volumeSteps,
} from './tick-footprint.ts';

// t in ms; bp/ap = best bid/ask riding with the update
const tick = (t, p, q, v, bp = null, ap = null) => ({ t, p, q, v, bp, bq: null, ap, aq: null });
const bar = (time) => ({ time, open: 0, high: 0, low: 0, close: 0, volume: 0 });

test('volume between updates is the day-volume difference; the seen trade is capped by it', () => {
  const { steps, segments } = volumeSteps([
    tick(1_000, 100, 5, 1_000, 99.9, 100.6), // inside its own spread, nothing before it → no side
    tick(2_000, 100.5, 10, 1_040, 100.4, 100.6), // inside both quotes, price up → tick rule buyer
    tick(3_000, 100.2, 50, 1_070, 100.2, 100.3), // at the bid → seller; seen 50 > 30 traded → capped
  ]);
  assert.deepEqual(segments, [[1_000, 3_000]]);
  assert.deepEqual(
    steps.map((x) => [x.vol, x.q, x.side, x.quote, x.from]),
    [
      [5, 5, null, false, 100], // first update: only its own trade is known
      [40, 10, 'buy', false, 100],
      [30, 30, 'sell', true, 100.5],
    ],
  );
});

test('the previous update’s quote is used when this one’s no longer brackets the trade', () => {
  const { steps } = volumeSteps([
    tick(1_000, 100, 5, 1_000, 99.9, 100.1),
    // a buy lifted 100.1 and cleared it: the new quote is 100.0 / 100.3, so only the old ask says "buyer"
    tick(1_800, 100.1, 7, 1_007, 100.0, 100.3),
  ]);
  assert.equal(steps[1].side, 'buy');
  assert.equal(steps[1].quote, true);
});

test('a hole or a new day restarts the count instead of dumping the missed volume on one update', () => {
  const { steps, segments } = volumeSteps(
    [
      tick(0, 100, 5, 1_000, 99, 100),
      tick(1_000, 100, 5, 1_010, 99, 100),
      tick(500_000, 101, 3, 9_000, 100, 101), // 8 minutes later: recorder was off
      tick(501_000, 101, 2, 20, 100, 101), // counter went down: next day
    ],
    120_000,
  );
  assert.deepEqual(segments, [
    [0, 1_000],
    [500_000, 500_000],
    [501_000, 501_000],
  ]);
  assert.deepEqual(
    steps.map((x) => x.vol),
    [5, 10, 3, 2],
  );
});

test('candles: the seen trade sits on its row, the rest spreads over the path, uncovered candles are null', () => {
  const series = volumeSteps([
    tick(59_000, 100, 1, 1_000, 99.5, 100), // recording starts inside candle 0
    tick(61_000, 100, 4, 1_004, 99.5, 100), // candle 1: 4 at 100 at the ask
    tick(62_000, 102, 2, 1_014, 101.5, 102), // candle 1: seen 2 at 102 (buyer), 8 more on the way 100 → 102
    tick(125_000, 102, 1, 1_015, 101.5, 102), // candle 2 starts; recording keeps going
  ]);
  const fps = buildTickFootprints([bar(0), bar(60), bar(120)], series, 60, 1);
  assert.equal(fps[0], null, 'recording began after the candle opened');
  const c = fps[1];
  assert.equal(c.total, 14);
  assert.deepEqual(
    c.levels.map((l) => [l.price, +l.ask.toFixed(4), +l.bid.toFixed(4)]),
    [
      [100, 4 + 8 / 3, 0],
      [101, 8 / 3, 0],
      [102, 2 + 8 / 3, 0],
    ].map(([p, a, b]) => [p, +a.toFixed(4), b]),
  );
  assert.ok(Math.abs(c.delta - 14) < 1e-9);
  assert.equal(c.seenShare, 6 / 14);
  assert.equal(c.quoteShare, 6 / 14);
  assert.equal(c.source, 'ticks');
  assert.ok(fps[2], 'the last candle counts once the recording reached it');
});

test('a candle the recording does not reach falls back to the estimate', () => {
  const series = volumeSteps([tick(0, 100, 1, 10, 99, 100), tick(1_000, 100, 1, 11, 99, 100)]);
  const fps = buildTickFootprints([bar(0), bar(60), bar(120)], series, 60, 1);
  assert.equal(fps[1], null);
  assert.equal(fps[2], null, 'last candle, but the recording stopped before it opened');
  assert.deepEqual(mergeFootprints(['a', 'b', 'c'], [null, 'X', null]), ['a', 'X', 'c']);
  assert.deepEqual(mergeFootprints(['a'], []), ['a']);
});

test('pack / unpack round-trips', () => {
  const { steps } = volumeSteps([
    tick(1_000, 100, 5, 1_000, 99.9, 100),
    tick(2_000, 100, 3, 1_010, 99.9, 100.2),
  ]);
  assert.deepEqual(unpackSteps(packSteps(steps)), steps);
});

test('NSE clock and chip states', async () => {
  const { nseClock, chipState } = await import('./tick-recorder.ts');
  const ist = (iso) => Date.parse(`${iso}+05:30`);
  const thu = nseClock(ist('2026-11-12T14:19:00'));
  assert.deepEqual([thu.day, thu.open, thu.canRecord], ['2026-11-12', true, true]);
  const early = nseClock(ist('2026-11-13T08:50:00'));
  assert.deepEqual([early.day, early.open, early.canRecord], ['2026-11-13', false, true]);
  const late = nseClock(ist('2026-11-12T15:30:00'));
  assert.deepEqual([late.open, late.canRecord], [false, false]);
  const sat = nseClock(ist('2026-11-14T10:00:00'));
  assert.deepEqual([sat.open, sat.canRecord], [false, false]);
  assert.equal(chipState({ fresh: true, running: false, clock: late }), 'recording');
  assert.equal(chipState({ fresh: false, running: true, clock: thu }), 'stalled');
  assert.equal(chipState({ fresh: false, running: true, clock: early }), 'waiting');
  assert.equal(chipState({ fresh: false, running: false, clock: thu }), 'idle');
  assert.equal(chipState({ fresh: false, running: false, clock: sat }), 'closed');
});
