import assert from 'node:assert/strict';
import { test } from 'node:test';
import { aggregateBars, heikinAshi, mergeBars, sessionDay, wallClock } from './timeframes.ts';
import { atr, ema, macd, relativeVolume, rsi, sma, vwap } from './indicators.ts';
import { autoStep, buildFootprints, niceStep, stepLadder } from './footprint.ts';
import { makeTimeAxis } from './drawings.ts';
import { groupByBar } from './news-markers.ts';

const IST = 'Asia/Kolkata';
// 2026-10-19 09:15 IST
const open = Date.parse('2026-10-19T09:15:00+05:30') / 1000;
const minute = (i, o, h, l, c, v) => ({ time: open + i * 60, open: o, high: h, low: l, close: c, volume: v });

test('wallClock renders the exchange clock as if it were UTC', () => {
  const wall = wallClock(open, IST);
  assert.equal(new Date(wall * 1000).toISOString(), '2026-10-19T09:15:00.000Z');
});

test('aggregateBars anchors buckets to the session open and sums volume', () => {
  const bars = Array.from({ length: 7 }, (_, i) => minute(i, 100 + i, 102 + i, 99 + i, 101 + i, 10));
  const out = aggregateBars(bars, 180, IST);
  assert.equal(out.length, 3);
  assert.equal(out[0].time, open);
  assert.equal(out[0].open, 100);
  assert.equal(out[0].high, 104);
  assert.equal(out[0].low, 99);
  assert.equal(out[0].close, 103);
  assert.equal(out[0].volume, 30);
  assert.equal(out[2].volume, 10);
});

test('aggregateBars restarts at each session day', () => {
  const day2 = open + 86400;
  const bars = [minute(0, 1, 1, 1, 1, 1), { time: day2, open: 2, high: 2, low: 2, close: 2, volume: 1 }];
  assert.equal(aggregateBars(bars, 3600, IST).length, 2);
});

test('mergeBars replaces the overlapping tail and appends newer bars', () => {
  const a = [minute(0, 1, 1, 1, 1, 1), minute(1, 2, 2, 2, 2, 2)];
  const fresh = [minute(1, 9, 9, 9, 9, 9), minute(2, 3, 3, 3, 3, 3)];
  const merged = mergeBars(a, fresh);
  assert.deepEqual(merged.map((b) => b.open), [1, 9, 3]);
});

test('heikinAshi first candle averages open/close', () => {
  const ha = heikinAshi([minute(0, 10, 12, 9, 11, 1)]);
  assert.equal(ha[0].open, 10.5);
  assert.equal(ha[0].close, 10.5);
});

test('sma and ema agree on a flat series and seed with the SMA', () => {
  const flat = Array(10).fill(5);
  assert.equal(sma(flat, 3)[2], 5);
  assert.equal(ema(flat, 3)[2], 5);
  assert.equal(ema(flat, 3)[1], null);
  assert.equal(ema([1, 2, 3, 4], 3)[2], 2);
});

test('rsi is 100 on a monotone rise and 0 on a monotone fall', () => {
  const up = Array.from({ length: 30 }, (_, i) => i);
  const down = [...up].reverse();
  assert.equal(rsi(up, 14).at(-1), 100);
  assert.equal(rsi(down, 14).at(-1), 0);
});

test('macd histogram is macd minus signal and starts after the slow window', () => {
  const closes = Array.from({ length: 60 }, (_, i) => 100 + Math.sin(i / 5) * 5);
  const m = macd(closes);
  assert.equal(m.macd[24], null);
  assert.notEqual(m.macd[25], null);
  const i = 59;
  assert.ok(Math.abs(m.hist[i] - (m.macd[i] - m.signal[i])) < 1e-12);
});

test('vwap resets at the session boundary', () => {
  const bars = [minute(0, 10, 10, 10, 10, 100), { time: open + 86400, open: 20, high: 20, low: 20, close: 20, volume: 50 }];
  const v = vwap(bars, (t) => sessionDay(t, IST));
  assert.equal(v[0], 10);
  assert.equal(v[1], 20);
});

test('relativeVolume divides by the mean of the previous N candles', () => {
  const r = relativeVolume([10, 10, 10, 30], 3);
  assert.equal(r[3], 3);
  assert.equal(r[2], null);
});

test('niceStep and stepLadder stay on 1-2-2.5-5 rungs', () => {
  assert.equal(niceStep(0.013), 0.02);
  assert.equal(niceStep(0.04), 0.05);
  assert.equal(stepLadder(0.05, 1), 0.1);
  assert.equal(stepLadder(0.05, -1), 0.025);
  assert.ok(autoStep([minute(0, 100, 101, 99, 100, 1)]) > 0);
});

test('footprint conserves each candle\'s volume and sums delta correctly', () => {
  const subs = [
    minute(0, 100, 100.4, 99.8, 100.3, 1000),
    minute(1, 100.3, 100.6, 100.1, 100.5, 500),
    minute(2, 100.5, 100.5, 100.5, 100.5, 0),
  ];
  const chart = [{ time: open, open: 100, high: 100.6, low: 99.8, close: 100.5, volume: 1500 }];
  const [fp] = buildFootprints(chart, subs, 180, 0.1);
  assert.ok(fp);
  assert.ok(Math.abs(fp.total - 1500) < 1e-6);
  const asks = fp.levels.reduce((s, l) => s + l.ask, 0);
  const bids = fp.levels.reduce((s, l) => s + l.bid, 0);
  assert.ok(Math.abs(fp.delta - (asks - bids)) < 1e-6);
  assert.ok(fp.levels[fp.poc].ask + fp.levels[fp.poc].bid >= Math.max(...fp.levels.map((l) => l.ask + l.bid)) - 1e-9);
});

test('footprint candles with no sub-bars are null', () => {
  const chart = [{ time: open, open: 1, high: 1, low: 1, close: 1, volume: 1 }];
  assert.deepEqual(buildFootprints(chart, [], 60, 0.1), [null]);
});

test('time axis round-trips and extrapolates past both ends', () => {
  const axis = makeTimeAxis([1000, 1060, 1120, 5000], 60);
  assert.equal(axis.toLogical(1060), 1);
  assert.equal(axis.toLogical(1090), 1.5);
  assert.equal(axis.toTime(1.5), 1090);
  assert.equal(axis.toLogical(5000 + 120), 3 + 2);
  assert.equal(axis.toTime(-1), 940);
});

test('atr uses true range incl. gaps and Wilder smoothing', () => {
  const b = (h, l, c) => ({ time: 0, open: c, high: h, low: l, close: c, volume: 1 });
  const bars = [b(10, 8, 9), b(11, 9, 10), b(15, 14, 14)]; // 3rd bar gaps up: TR = 15-10 = 5
  const out = atr(bars, 2);
  assert.equal(out[0], null);
  assert.equal(out[1], 2); // mean(2, 2)
  assert.equal(out[2], 3.5); // (2*1 + 5)/2
});

test('news attaches to the containing candle, else the next one', () => {
  const times = [1000, 1300, 1600, 5000]; // 5-minute candles with an overnight gap
  const at = (t) => ({ headline: 'h', publisher: 'p', url: null, published_at: new Date(t * 1000).toISOString(), kind: 'news', text: null });
  const m = groupByBar([at(1350), at(1950), at(3000), at(5100), at(900)], times, 300);
  assert.equal(m.get(1).length, 1); // inside candle 1
  assert.equal(m.get(3).length, 3); // 1950 + 3000 fall in the gap -> next candle; 5100 is inside the last
  assert.equal(m.has(0), false); // older than first candle is dropped
});
