import assert from 'node:assert/strict';
import { test } from 'node:test';
import { containingBarIndex, fiveMinuteBars } from './momentum-bars.ts';

const start = Date.parse('2026-09-11T09:15:00+05:30');
const bars = Array.from({ length: 13 }, (_, i) => ({
  time: new Date(start + i * 60_000).toISOString(),
  open: 100 + i, high: 102 + i, low: 99 + i, close: 101 + i, volume: 100,
}));

test('five minute candles use start timestamps and exclude the unfinished bucket', () => {
  assert.deepEqual(fiveMinuteBars(bars), [
    { time: new Date(start).toISOString(), open: 100, high: 106, low: 99, close: 105, volume: 500 },
    { time: new Date(start + 300_000).toISOString(), open: 105, high: 111, low: 104, close: 110, volume: 500 },
  ]);
});

test('a missing minute cannot shift later candles', () => {
  assert.deepEqual(fiveMinuteBars(bars.filter((_, i) => i !== 2)), fiveMinuteBars(bars).slice(1));
});

test('duplicate and off-minute input cannot fabricate a complete candle', () => {
  assert.deepEqual(fiveMinuteBars([...bars.slice(0, 4), bars[3]]), []);
  assert.deepEqual(fiveMinuteBars([{ ...bars[0], time: new Date(start + 1).toISOString() }, ...bars.slice(1)]), []);
});

test('an incomplete leading bucket is dropped without reanchoring the clock', () => {
  assert.deepEqual(fiveMinuteBars(bars.slice(2)), fiveMinuteBars(bars).slice(1));
});

test('fills near the end of a candle are never snapped forward', () => {
  assert.equal(containingBarIndex(bars, new Date(start + 59_000).toISOString(), '1m'), 0);
  assert.equal(containingBarIndex(fiveMinuteBars(bars), new Date(start + 299_000).toISOString(), '5m'), 0);
  assert.equal(containingBarIndex(bars.slice(1), new Date(start).toISOString(), '1m'), -1);
});
