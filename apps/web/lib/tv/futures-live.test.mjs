import assert from 'node:assert/strict';
import { test } from 'node:test';
import { bucketStart, mergeLiveMinutes } from './futures-live.ts';

// 2026-11-25 11:40 IST
const T = Date.parse('2026-11-25T11:40:00+05:30') / 1000;
const bar = (time, o, h, l, c, v) => ({ time, open: o, high: h, low: l, close: c, volume: v });
const min = (time, o, h, l, c, v) => ({ time, open: o, high: h, low: l, close: c, volume: v });

test('buckets are anchored at 09:15 IST', () => {
  assert.equal(bucketStart(T + 59, 60), T);
  assert.equal(bucketStart(T + 120, 300), T);
  assert.equal(bucketStart(T, 3600), Date.parse('2026-11-25T11:15:00+05:30') / 1000);
  assert.equal(bucketStart(T, 1800), Date.parse('2026-11-25T11:15:00+05:30') / 1000);
});

test('1m: unpublished previous minute and the forming minute are appended', () => {
  const bars = [bar(T - 60, 10, 11, 9, 10, 5)];
  const out = mergeLiveMinutes(bars, [min(T, 10, 12, 10, 11, 3), min(T + 60, 11, 11, 10, 10.5, 1)], T - 60, 60);
  assert.deepEqual(out.map((b) => [b.time, b.close]), [
    [T - 60, 10],
    [T, 11],
    [T + 60, 10.5],
  ]);
});

test('a minute the candle API already published is never added twice', () => {
  const bars = [bar(T - 60, 10, 11, 9, 10, 5), bar(T, 10, 12, 10, 11, 3)];
  const out = mergeLiveMinutes(bars, [min(T, 10, 12, 10, 11, 3), min(T + 60, 11, 13, 11, 12, 2)], T, 60);
  assert.equal(out.length, 3);
  assert.equal(out[1].volume, 3);
  assert.equal(out[2].close, 12);
});

test('5m: forming minute folds into the partial bucket (high, low, close, volume)', () => {
  const bars = [bar(T, 100, 101, 99, 100, 10)]; // 11:40 bucket holding 11:40 only
  const out = mergeLiveMinutes(bars, [min(T + 60, 100, 103, 98, 102, 4)], T, 300);
  assert.deepEqual(out, [bar(T, 100, 103, 98, 102, 14)]);
});

test('5m: a new bucket starts from the live minute', () => {
  const bars = [bar(T - 300, 100, 101, 99, 100, 10)];
  const out = mergeLiveMinutes(bars, [min(T + 60, 100, 103, 98, 102, 4)], T - 60, 300);
  assert.deepEqual(out[1], bar(T, 100, 103, 98, 102, 4));
});

test('nothing usable: bars unchanged', () => {
  const bars = [bar(T, 1, 1, 1, 1, 1)];
  assert.equal(mergeLiveMinutes(bars, [], T, 60), bars);
  assert.equal(mergeLiveMinutes(bars, [min(T + 60, 0, 0, 0, 0, 0)], T, 60), bars);
});
