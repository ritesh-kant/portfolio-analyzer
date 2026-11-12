import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  AR_SESSIONS,
  avrDays,
  avrUnits,
  biasLadder,
  dailyAtrByDay,
  environment,
  rangeFib,
  sessionBoxes,
  structureBias,
  structureLabels,
  swings,
} from './ar.ts';

const TZ = 'America/New_York';
const bar = (time, o, h, l, c, v = 100) => ({ time, open: o, high: h, low: l, close: c, volume: v });
/** Unix seconds for a New York wall-clock time in November 2026 (EST, UTC−5). */
const ny = (day, hh, mm = 0) => Date.parse(`2026-11-${String(day).padStart(2, '0')}T${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}:00-05:00`) / 1000;

/** 5-minute bars, flat at 100 with a 1-point range, from (day, hh:mm) for `n` bars. */
function flat(day, hh, mm, n, px = 100) {
  const out = [];
  for (let i = 0; i < n; i++) out.push(bar(ny(day, hh, mm) + i * 300, px, px + 0.5, px - 0.5, px));
  return out;
}

test('sessionBoxes finds Asia, London and New York in New York time', () => {
  // Mon 18:00 → Tue 12:00, every 5 minutes
  const bars = flat(9, 18, 0, 18 * 12);
  bars[30] = bar(bars[30].time, 100, 130, 100, 100); // Mon 20:30 → inside Asia
  const boxes = sessionBoxes(bars, TZ);
  assert.deepEqual(
    boxes.map((b) => [b.key, b.day, b.to - b.from + 1]),
    [
      ['asia', '2026-11-09', 48], // 20:00–24:00
      ['london', '2026-11-10', 36], // 02:00–05:00
      ['ny', '2026-11-10', 18], // 09:30–11:00
    ],
  );
  assert.equal(boxes[0].high, 130);
  assert.equal(boxes[0].open, false);
});

test('a session that crosses midnight is one box dated on the evening it started', () => {
  const bars = flat(9, 17, 0, 12 * 12);
  const boxes = sessionBoxes(bars, TZ, [{ key: 'asia', label: 'Asia', start: 18 * 60, end: 2 * 60 }]);
  assert.equal(boxes.length, 1);
  assert.equal(boxes[0].day, '2026-11-09');
  assert.equal(boxes[0].to - boxes[0].from + 1, 8 * 12);
  assert.equal(boxes[0].open, false);
});

test('avrUnits reproduces the two readings off his indicator table', () => {
  const a = avrUnits(278.74);
  assert.ok(Math.abs(a.up - 41.81) < 0.05, `up ${a.up}`);
  assert.ok(Math.abs(a.down - 44.6) < 0.1, `down ${a.down}`);
  const b = avrUnits(485.69);
  assert.ok(Math.abs(b.up - 63) < 0.1, `up ${b.up}`);
  assert.ok(Math.abs(b.down - 67.2) < 0.15, `down ${b.down}`);
  // clamped share at the extremes
  assert.ok(Math.abs(avrUnits(50).up / 50 - 0.16) < 1e-9);
  assert.ok(Math.abs(avrUnits(2000).up / 2000 - 0.12) < 1e-9);
});

test('avrDays uses the ATR known when Asia started, never a later daily candle', () => {
  // daily candles dated Oct 1 … Nov 10; ranges of 100 points, then a huge Nov 10 candle
  const daily = [];
  for (let d = 0; d < 41; d++) {
    const t = Date.UTC(2026, 9, 1 + d, 12) / 1000; // noon UTC: the same calendar date in New York
    const r = d === 40 ? 5000 : 100;
    daily.push(bar(t, 1000, 1000 + r, 1000, 1000));
  }
  const byDay = dailyAtrByDay(daily, TZ, 20);
  const bars = flat(9, 18, 0, 18 * 12); // Asia on the evening of Nov 9
  const days = avrDays(bars, sessionBoxes(bars, TZ), byDay);
  assert.equal(days.length, 1);
  assert.ok(Math.abs(days[0].atr - 100) < 1e-6, `atr ${days[0].atr}`); // the Nov 10 candle is not known yet
  const l1 = days[0].levels.find((l) => l.key === 'l1' && l.side === 'up');
  const asiaHigh = days[0].asia.high;
  assert.ok(Math.abs(l1.lo - (asiaHigh + 0.25 * days[0].up)) < 1e-9);
  assert.equal(days[0].from, days[0].asia.to + 1);
  assert.equal(days[0].to, bars.length - 1);
});

/** A zig-zag path through the given turning prices, 10 one-point-range bars per leg. */
function path(points) {
  const out = [];
  let t = ny(2, 9, 30);
  for (let k = 1; k < points.length; k++) {
    const a = points[k - 1];
    const b = points[k];
    for (let s = 1; s <= 10; s++) {
      const prev = a + ((b - a) * (s - 1)) / 10;
      const px = a + ((b - a) * s) / 10;
      out.push(bar(t, prev, Math.max(prev, px), Math.min(prev, px), px));
      t += 300;
    }
  }
  return out;
}

test('swings: a swing confirms only after price moves away, and a prefix sees the same swings', () => {
  const bars = path([100, 200, 150, 260, 120, 180]);
  const { pivots, tentative } = swings(bars, 2, 5);
  assert.deepEqual(
    pivots.map((p) => [p.kind, p.price]),
    [
      ['L', 100],
      ['H', 200],
      ['L', 150],
      ['H', 260],
      ['L', 120],
    ],
  );
  for (const p of pivots) assert.ok(p.confirmedAt > p.index);
  assert.deepEqual([tentative.kind, tentative.price], ['H', 180]);
  // no look-ahead: cut the bars anywhere; every swing confirmed by then is identical
  for (const cut of [25, 33, 41, 47]) {
    const early = swings(bars.slice(0, cut), 2, 5).pivots;
    assert.deepEqual(
      early,
      pivots.filter((p) => p.confirmedAt < cut),
      `cut ${cut}`,
    );
  }
});

test('structure labels and bias', () => {
  const up = swings(path([100, 150, 130, 180, 160, 210, 190]), 2, 5).pivots;
  assert.deepEqual(structureLabels(up).filter(Boolean), ['HL', 'HH', 'HL', 'HH']);
  assert.equal(structureBias(up), 'bull');
  const down = swings(path([300, 250, 270, 220, 240, 190, 210]), 2, 5).pivots;
  assert.equal(structureBias(down), 'bear');
  const mixed = swings(path([100, 150, 120, 140, 110, 145, 130]), 2, 5).pivots;
  assert.equal(structureBias(mixed), 'neutral');
  // a timeframe finer than the chart has no bias
  const ladder = biasLadder(path([100, 150, 130, 180]), 900, TZ);
  assert.deepEqual(
    ladder.map((r) => [r.label, r.bias === null]),
    [
      ['4h', true],
      ['1h', true],
      ['15m', true],
      ['5m', true],
    ],
  );
});

test('rangeFib: up leg gives a discount zone, then touch → target, or a close past 0.886 → invalidated', () => {
  // leg 100 → 200, retrace to 125 (into 0.705–0.886 = 129.5 … 111.4), then back above 200
  const won = path([100, 200, 125, 205]);
  const p = swings(won, 2, 5).pivots;
  const f = rangeFib(won, p, 'bull');
  assert.equal(f.dir, 'up');
  assert.equal(f.from.price, 100);
  assert.equal(f.to.price, 200);
  assert.ok(Math.abs(f.zoneTop - 129.5) < 1e-9 && Math.abs(f.zoneBottom - 111.4) < 1e-9);
  assert.ok(Math.abs(f.extension - 90) < 1e-9);
  assert.equal(f.target, 200);
  assert.equal(f.status, 'target hit');
  assert.ok(f.touchedAt != null && f.targetAt > f.touchedAt);

  // same leg, but price closes below 0.886 (111.4)
  const lost = path([100, 200, 105, 140]);
  const g = rangeFib(lost, swings(lost, 2, 5).pivots, 'bull');
  assert.equal(g.status, 'invalidated');

  // only a retracement so far, sitting in the zone
  const sitting = path([100, 200, 120]);
  const h = rangeFib(sitting, swings(sitting, 2, 5).pivots, 'neutral');
  assert.equal(h.status, 'in zone');

  // bearish preference picks the latest DOWN leg (200 → 125) instead: a premium zone above
  const s = rangeFib(won, p, 'bear');
  assert.equal(s.dir, 'down');
  assert.ok(s.zoneBottom > s.to.price && s.zoneTop < s.from.price);
});

test('environment: a straight run is expansion, a tight flat market is compression', () => {
  const quiet = [];
  let t = ny(2, 9, 30);
  // 48 wide, choppy bars, then 24 narrow ones
  for (let i = 0; i < 48; i++, t += 300) quiet.push(bar(t, 100, 110, 90, i % 2 ? 105 : 95));
  for (let i = 0; i < 24; i++, t += 300) quiet.push(bar(t, 100, 100.5, 99.5, i % 2 ? 100.2 : 99.8));
  assert.equal(environment(quiet, 24).env, 'compression');

  const trend = [];
  t = ny(2, 9, 30);
  for (let i = 0; i < 48; i++, t += 300) trend.push(bar(t, 100, 104, 96, i % 2 ? 102 : 98));
  for (let i = 0; i < 24; i++, t += 300) trend.push(bar(t, 100 + i * 5, 106 + i * 5, 99 + i * 5, 105 + i * 5));
  const e = environment(trend, 24);
  assert.equal(e.env, 'expansion');
  assert.ok(e.efficiency > 0.9);
  assert.equal(environment(trend.slice(0, 50), 24), null); // not enough history
});

test('default sessions are the documented New York windows', () => {
  assert.deepEqual(
    AR_SESSIONS.map((s) => [s.key, s.start, s.end]),
    [
      ['asia', 1200, 1440],
      ['london', 120, 300],
      ['ny', 570, 660],
    ],
  );
});
