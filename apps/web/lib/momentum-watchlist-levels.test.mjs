import assert from 'node:assert/strict';
import { test } from 'node:test';

import { watchlistLevels } from './momentum-watchlist-levels.ts';

const start = Date.parse('2026-10-01T09:15:00+05:30');
const time = (minute) => new Date(start + minute * 60_000).toISOString();
const bars = (count, highs = []) => Array.from({ length: count }, (_, minute) => ({
  time: time(minute), open: 100, high: highs[Math.floor(minute / 5)] ?? 101,
  low: 99, close: 100, volume: 100,
}));

test('watchlist levels use only candles available at the latest flag', () => {
  const before = bars(10);
  const future = { ...before[0], time: time(10), high: 200, low: 50 };
  const levels = watchlistLevels([...before, future], time(9), 'NSE');
  assert.equal(levels.find((level) => level.side === 'resistance')?.price, 101);
  assert.equal(levels.find((level) => level.side === 'support')?.price, 99);
  assert.ok(levels.every((level) => level.faint));
});

test('a 5-minute pivot appears only after three complete bars confirm it', () => {
  const data = bars(40, [101, 102, 103, 110, 104, 102, 101, 115]);
  const early = watchlistLevels(data, time(33), 'NSE');
  const confirmed = watchlistLevels(data, time(34), 'NSE');
  assert.equal(early.find((level) => level.side === 'resistance')?.kind, 'session high');
  assert.equal(confirmed.find((level) => level.side === 'resistance')?.kind, '5m pivot high');
  assert.equal(confirmed.find((level) => level.side === 'resistance')?.price, 110);
});

test('NSE round rupee level supplies resistance after a breakout', () => {
  const data = bars(5).map((bar) => ({ ...bar, open: 1260, high: 1265, low: 1250, close: 1265 }));
  const levels = watchlistLevels(data, time(4), 'NSE');
  assert.equal(levels.find((level) => level.side === 'resistance')?.price, 1300);
  assert.equal(levels.find((level) => level.side === 'support')?.price, 1250);
});
