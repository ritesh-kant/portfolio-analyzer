import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  AR_SESSIONS,
  arMarket,
  avrDays,
  avrUnits,
  biasLadder,
  candlesPerDay,
  dayRangeRatio,
  dailyAtrByDay,
  environment,
  rangeFib,
  decoderBox,
  decoderZones,
  DECODER_LEVELS,
  sessionBoxes,
  structureBias,
  structureReads,
  structureTrend,
  swingTrend,
  sessionAnchoredFrom,
  structureLabels,
  swings,
  weekPivots,
  weekRanges,
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
      ['1D', true],
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

test('decoder stand-in: 0.62 / 0.75 / 0.81 of the leg, measured back from its end, on both sides', () => {
  assert.deepEqual(DECODER_LEVELS, { near: 0.62, mid: 0.75, far: 0.81 });
  const close = (a, b) => Math.abs(a - b) < 1e-9;
  const up = decoderBox({ index: 0, price: 100, kind: 'L' }, { index: 5, price: 200, kind: 'H' });
  assert.ok(close(up.near, 138) && close(up.mid, 125) && close(up.far, 119), JSON.stringify(up));
  assert.ok(close(up.top, 138) && close(up.bottom, 119));
  const down = decoderBox({ index: 0, price: 200, kind: 'H' }, { index: 5, price: 100, kind: 'L' });
  assert.ok(close(down.near, 162) && close(down.mid, 175) && close(down.far, 181), JSON.stringify(down));
  assert.ok(close(down.top, 181) && close(down.bottom, 162));
  // the range fib carries the same box for its leg
  const won = path([100, 200, 125, 205]);
  const f = rangeFib(won, swings(won, 2, 5).pivots, 'bull');
  assert.deepEqual(f.decoder, decoderBox(f.from, f.to));
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

test('sessions stay on the New York clock whatever time zone the chart is in', () => {
  const bars = flat(9, 18, 0, 18 * 12);
  const ny = sessionBoxes(bars, TZ);
  for (const tz of ['UTC', 'Europe/London']) {
    assert.deepEqual(
      sessionBoxes(bars, tz).map((b) => [b.key, b.day, b.from, b.to]),
      ny.map((b) => [b.key, b.day, b.from, b.to]),
      tz,
    );
  }
});

test('NSE charts get one "NSE open" box, 09:15–10:45 India time, and no Asia / London / New York', () => {
  assert.equal(arMarket('Asia/Kolkata').key, 'nse');
  assert.equal(arMarket('America/New_York').key, 'us');
  // Tue 10 Nov 2026, 09:15 → 15:30 IST (03:45 → 10:00 UTC), 5-minute bars
  const t0 = Date.parse('2026-11-10T09:15:00+05:30') / 1000;
  const bars = Array.from({ length: 75 }, (_, i) => bar(t0 + i * 300, 100, 101, 99, 100));
  const boxes = sessionBoxes(bars, 'Asia/Kolkata');
  assert.deepEqual(
    boxes.map((b) => [b.key, b.day, b.from, b.to]),
    [['open', '2026-11-10', 0, 17]], // 09:15 … 10:40 start times = 18 candles
  );
  // so the Asia-range levels have nothing to hang off
  assert.deepEqual(avrDays(bars, boxes, new Map([['2026-11-09', 300], ['2026-11-10', 300]])), []);
});

test('decoderZones: a box per leg, held when price returns to the leg end, broken on a close through the far edge', () => {
  // up 100→200 (box 119–138), back to 125 (inside, no close below 119), then on to 205: held.
  // the down leg 200→125 has its box at 171.5–185.75; the rally closes above 185.75: broken.
  const bars = path([100, 200, 125, 205, 190]);
  const piv = swings(bars, 2, 5).pivots;
  const zones = decoderZones(bars, piv, { count: 5 });
  const up = zones.find((z) => z.from.price === 100 && z.to.price === 200);
  const down = zones.find((z) => z.from.price === 200 && z.to.price === 125);
  assert.ok(up && down, JSON.stringify(zones.map((z) => [z.from.price, z.to.price])));
  assert.equal(up.dir, 'up');
  assert.equal(up.status, 'held');
  assert.ok(up.touchedAt > up.to.index && up.endAt > up.touchedAt);
  assert.ok(bars[up.touchedAt].low <= up.box.top);
  assert.ok(bars[up.endAt].high >= 200);
  assert.equal(down.dir, 'down');
  assert.equal(down.status, 'broken');
  assert.ok(bars[down.endAt].close > down.box.top);
  // oldest first
  assert.ok(zones.every((z, k) => k === 0 || zones[k - 1].to.index < z.to.index));
});

test('decoderZones: a close below the far edge breaks a long box; side and count filter the legs', () => {
  const bars = path([100, 200, 110, 170, 150]);
  const piv = swings(bars, 2, 5).pivots;
  const all = decoderZones(bars, piv, { count: 10 });
  const up = all.find((z) => z.from.price === 100 && z.to.price === 200);
  assert.ok(up);
  assert.equal(up.status, 'broken');
  assert.ok(bars[up.endAt].close < up.box.bottom);
  const longs = decoderZones(bars, piv, { count: 10, side: 'up' });
  assert.ok(longs.length >= 1 && longs.every((z) => z.dir === 'up'));
  const shorts = decoderZones(bars, piv, { count: 10, side: 'down' });
  assert.ok(shorts.every((z) => z.dir === 'down'));
  assert.equal(longs.length + shorts.length, all.length);
  const last = decoderZones(bars, piv, { count: 1 });
  assert.equal(last.length, 1);
  assert.deepEqual(last[0], all[all.length - 1]);
});

test('decoderZones: custom levels move the box; untouched boxes stay waiting', () => {
  const bars = path([100, 200, 150]);
  const piv = [
    { index: 0, price: 100, kind: 'L', confirmedAt: 5 },
    { index: 9, price: 200, kind: 'H', confirmedAt: 14 },
  ];
  const z = decoderZones(bars, piv, { levels: { near: 0.5, mid: 0.6, far: 0.7 } });
  assert.equal(z.length, 1);
  assert.deepEqual(z[0].box, decoderBox(piv[0], piv[1], { near: 0.5, mid: 0.6, far: 0.7 }));
  assert.ok(Math.abs(z[0].box.top - 150) < 1e-9 && Math.abs(z[0].box.bottom - 130) < 1e-9);
  assert.equal(z[0].status, 'in box'); // 150 reaches the 0.5 edge
  const z2 = decoderZones(bars, piv);
  assert.equal(z2[0].status, 'waiting'); // 150 never reaches 138
  assert.equal(z2[0].endAt, null);
});

test('decoderZones: no look-ahead, a decided box in any prefix is decided the same way on the full chart', () => {
  const bars = path([100, 200, 125, 205, 140, 230, 120, 180]);
  const full = decoderZones(bars, swings(bars, 2, 5).pivots, { count: 20 });
  const key = (z) => `${z.from.index}-${z.to.index}`;
  const byKey = new Map(full.map((z) => [key(z), z]));
  for (let n = 10; n <= bars.length; n++) {
    const pre = bars.slice(0, n);
    for (const z of decoderZones(pre, swings(pre, 2, 5).pivots, { count: 20 })) {
      const f = byKey.get(key(z));
      if (!f) continue; // the leg's end swing moved on the full chart
      if (z.endAt != null) assert.deepEqual([z.status, z.endAt, z.touchedAt], [f.status, f.endAt, f.touchedAt], key(z));
      else if (z.touchedAt != null) assert.equal(z.touchedAt, f.touchedAt);
    }
  }
});

test('structureTrend: a shallow lower low does not flip an uptrend, a close through the protected low does', () => {
  // HH 210 from HL 160 (the protected low); then a dip to 158 and a lower high 205: the plain read says bear
  const shallow = path([100, 150, 130, 180, 160, 210, 158, 205, 170]);
  const sp = swings(shallow, 2, 5).pivots;
  assert.equal(structureBias(sp), 'bear');
  const st = structureTrend(shallow, sp, 1, 5);
  assert.equal(st.at(-1), 'bull');
  // a real break: a close well below 160 turns it down, and it stays down
  const deep = path([100, 150, 130, 180, 160, 210, 120, 150]);
  const dt = structureTrend(deep, swings(deep, 2, 5).pivots, 1, 5);
  assert.equal(dt.at(-1), 'bear');
  const firstBear = dt.indexOf('bear');
  assert.ok(firstBear > 0 && deep[firstBear].close < 160 - 5);
  // no look-ahead: a prefix gives the same values
  for (const cut of [30, 55, 66]) {
    const early = deep.slice(0, cut);
    assert.deepEqual(structureTrend(early, swings(early, 2, 5).pivots, 1, 5), dt.slice(0, cut), `cut ${cut}`);
  }
});

test('swingTrend: up / down only when both swings agree, unclear otherwise; a break goes to unclear, not the opposite', () => {
  const read = (pts) => {
    const b = path(pts);
    return { b, t: swingTrend(b, swings(b, 2, 5).pivots, 1, 5) };
  };
  // higher highs and higher lows: up
  assert.equal(read([100, 150, 130, 180, 160, 210, 190]).t.at(-1), 'bull');
  // lower highs and lower lows: down
  assert.equal(read([210, 160, 180, 130, 150, 100, 120]).t.at(-1), 'bear');
  // the 11-25 NQ case: a fall, then one spike to a higher high while the last low is still the lowest: unclear
  const spike = read([210, 160, 180, 100, 190, 170]);
  assert.equal(spike.t.at(-1), 'neutral');
  // lower high + higher low (a squeeze): unclear
  assert.equal(read([100, 200, 120, 180, 140, 160]).t.at(-1), 'neutral');
  // an uptrend whose close falls through the latest swing low turns unclear at once, never straight to down
  const brk = read([100, 150, 130, 180, 160, 210, 120]);
  const firstOff = brk.t.lastIndexOf('bull') + 1;
  assert.equal(brk.t.at(-1), 'neutral');
  assert.ok(brk.b[firstOff].close < 160 - 5, 'it turns unclear on the close through 160');
  assert.ok(!brk.t.includes('bear'));
  // no look-ahead: a prefix gives the same values
  for (const cut of [20, 40, 55]) {
    const early = brk.b.slice(0, cut);
    assert.deepEqual(swingTrend(early, swings(early, 2, 5).pivots, 1, 5), brk.t.slice(0, cut), `cut ${cut}`);
  }
});

test('sessionAnchoredFrom: a leg across an overnight gap starts at the session low', () => {
  // day 1 falls to 100; day 2 gaps up to 150, dips to 140, runs to 200
  const day1 = path([130, 100]);
  const t0 = day1.at(-1).time + 18 * 3600;
  const day2 = path([150, 140, 200]).map((b) => ({ ...b, time: b.time - day1[0].time + t0 }));
  const bars = [...day1, ...day2];
  const lowIdx = day1.findIndex((b) => b.low === 100);
  const from = { index: lowIdx, price: 100, kind: 'L', confirmedAt: lowIdx + 3 };
  const to = { index: bars.length - 1, price: 200, kind: 'H', confirmedAt: bars.length - 1 };
  const a = sessionAnchoredFrom(bars, from, to);
  assert.equal(a.price, 140);
  assert.ok(a.index > day1.length - 1);
  // the session part is too small: keep the original start
  assert.equal(sessionAnchoredFrom(bars, from, to, 70).price, 100);
  // a leg already inside the session is untouched
  const inside = { index: day1.length + 5, price: 145, kind: 'L', confirmedAt: day1.length + 8 };
  assert.equal(sessionAnchoredFrom(bars, inside, to), inside);
});

test('checklist names the swing, and a zone cancelled in an earlier session is shown as waiting, not failed', async () => {
  const { runArStrategy } = await import('./ar-strategy.ts');
  // day 1: up leg 100 → 200, then a close past 0.886 (111.40) cancels it; day 2 (after an overnight gap) drifts
  const day1 = path([130, 100, 200, 105]);
  const t0 = day1.at(-1).time + 18 * 3600;
  const day2 = path([105, 109, 106]).map((b) => ({ ...b, time: b.time - day1[0].time + t0 }));
  const bars = [...day1, ...day2];
  const { reads } = runArStrategy(bars, { timeZone: TZ, chartSecs: 300, cfg: { swingMult: 2, flatAt: 99999, envRule: 'efficiency', range: 'swing' } });
  const cancel = reads.findIndex((r) => r.checks.location.state === 'fail');
  assert.ok(cancel > 0 && cancel < day1.length, 'cancelled on day 1');
  assert.match(reads[cancel].checks.location.text, /zone cancelled · swing 100\.00 \(/);
  assert.equal(reads[cancel].checks.confirmation.state, 'wait');
  const later = reads.slice(day1.length).map((r) => r.checks.location);
  assert.ok(!later.some((c) => c.state === 'fail'), 'no red mark the next day');
  assert.ok(later.every((c) => /the last long zone was cancelled on .*waiting for a new swing/.test(c.text)));
  // in the zone before the cancel: one marked line per part, then the swing
  const touched = reads.slice(0, cancel).map((r) => r.checks.location).find((c) => c.lines);
  assert.ok(touched, 'a touched read is split');
  assert.equal(touched.lines[0].state, 'pass');
  assert.match(touched.lines[0].text, /^price reached the discount zone \(long\)$/);
  assert.equal(touched.lines[1].state, touched.state);
  assert.match(touched.detail, /^Fib drawn on swing 100\.00 \(/);
});

test('weekRanges: highest high and lowest low of the last N days, earlier extreme → later one, no look-ahead', () => {
  // 15 days of hourly candles, a random walk
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647) - 0.5;
  const H = 3600;
  const t0 = Date.parse('2026-10-01T00:00:00Z') / 1000;
  const bars = [];
  let px = 100;
  for (let i = 0; i < 15 * 24; i++) {
    const o = px;
    px += rnd() * 4;
    bars.push({ time: t0 + i * H, open: o, high: Math.max(o, px) + Math.abs(rnd()), low: Math.min(o, px) - Math.abs(rnd()), close: px, volume: 1 });
  }
  const got = weekRanges(bars, 7);
  for (let i = 0; i < bars.length; i++) {
    const t = bars[i].time;
    if (bars[0].time > t - 7 * 86400) {
      assert.equal(got[i], null, `candle ${i}: not a full week yet`);
      continue;
    }
    let h = -1;
    let l = -1;
    for (let j = 0; j <= i; j++) {
      if (bars[j].time <= t - 7 * 86400) continue;
      if (h < 0 || bars[j].high > bars[h].high) h = j;
      if (l < 0 || bars[j].low < bars[l].low) l = j;
    }
    const r = got[i];
    assert.equal(bars[r.down ? r.from : r.to].high, bars[h].high, `candle ${i}: high`);
    assert.equal(bars[r.down ? r.to : r.from].low, bars[l].low, `candle ${i}: low`);
    assert.ok(r.from < r.to);
    assert.equal(r.down, h < l);
    // the same answer from the candles up to i only
    assert.deepEqual(weekRanges(bars.slice(0, i + 1), 7)[i], r);
    const [from, to] = weekPivots(bars, r, i);
    assert.deepEqual([from.kind, to.kind], r.down ? ['H', 'L'] : ['L', 'H']);
  }
});

// ── structure environment (envRule: 'structure') ──

/** Flat candles at `px` with hand-made pivots, to test the structure rule on its own. */
function pivotScene(closes) {
  return closes.map((c, i) => bar(1_800_000_000 + i * 3600, c, c + 0.5, c - 0.5, c));
}
const P = (index, price, kind, confirmedAt = index + 1) => ({ index, price, kind, confirmedAt });

test('structureReads: HH+HL up, LH+LL down, mixed sideways, a close past the last swing wins', () => {
  const bars = pivotScene(new Array(12).fill(105));
  const up = structureReads(bars, [P(0, 100, 'L'), P(1, 110, 'H'), P(2, 102, 'L'), P(3, 112, 'H')]);
  assert.equal(up[3], null, 'pivot 3 is confirmed only at candle 4');
  assert.deepEqual(up[5], { dir: 'bull', why: 'higher highs and higher lows' });
  const down = structureReads(bars, [P(0, 112, 'H'), P(1, 102, 'L'), P(2, 110, 'H'), P(3, 100, 'L')]);
  assert.equal(down[5].dir, 'bear');
  const mixed = structureReads(bars, [P(0, 102, 'L'), P(1, 110, 'H'), P(2, 100, 'L'), P(3, 112, 'H')]);
  assert.deepEqual(mixed[5], { dir: 'neutral', why: 'the swings overlap (higher high, lower low)' });
  // the same overlapping swings, but the candle closes well above the last swing high
  const broke = pivotScene([...new Array(11).fill(105), 120]);
  assert.deepEqual(structureReads(broke, [P(0, 102, 'L'), P(1, 110, 'H'), P(2, 100, 'L'), P(3, 112, 'H')])[11], {
    dir: 'bull',
    why: 'closed above the last swing high 112.00',
  });
});

test('structureReads: a prefix of the candles gives the same reads (no look-ahead)', () => {
  let px = 100;
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647) - 0.5;
  const bars = [];
  for (let i = 0; i < 400; i++) {
    const o = px;
    px += rnd() * 4 + Math.sin(i / 25) * 1.5;
    bars.push(bar(1_800_000_000 + i * 3600, o, Math.max(o, px) + Math.abs(rnd()), Math.min(o, px) - Math.abs(rnd()), px));
  }
  const full = structureReads(bars, swings(bars, 2).pivots);
  assert.ok(full.filter((r) => r?.dir === 'bull').length > 10 && full.filter((r) => r?.dir === 'bear').length > 10);
  for (const k of [60, 150, 233, 399]) {
    const part = bars.slice(0, k);
    assert.deepEqual(structureReads(part, swings(part, 2).pivots), full.slice(0, k));
  }
});

test('dayRangeRatio: matches a brute-force count, and a quiet last day reads small', () => {
  let seed = 3;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  const bars = Array.from({ length: 200 }, (_, i) => {
    const m = 100 + rnd() * 10;
    return bar(1_800_000_000 + i * 3600, m, m + rnd() * 3, m - rnd() * 3, m);
  });
  const m = 7;
  const span = (e) => Math.max(...bars.slice(e - m + 1, e + 1).map((b) => b.high)) - Math.min(...bars.slice(e - m + 1, e + 1).map((b) => b.low));
  const got = dayRangeRatio(bars, m);
  for (let k = 0; k < bars.length; k++) {
    if (k < 3 * m - 1) {
      assert.equal(got[k], null);
      continue;
    }
    const past = [];
    for (let e = k - m; e >= m - 1 && past.length < 40; e -= 3) past.push(span(e));
    past.sort((a, b) => a - b);
    assert.ok(Math.abs(got[k] - span(k) / past[Math.floor(past.length / 2)]) < 1e-12, `k=${k}`);
  }
  const quiet = [...bars, ...Array.from({ length: m }, (_, i) => bar(1_800_000_000 + (200 + i) * 3600, 105, 105.5, 104.5, 105))];
  assert.ok(dayRangeRatio(quiet, m).at(-1) < 0.3);
});

test('candlesPerDay: the median count per market day', () => {
  const t0 = Date.parse('2026-11-02T09:00:00-05:00') / 1000;
  const bars = [];
  for (const [d, n] of [[0, 7], [1, 7], [2, 3], [3, 7]]) for (let k = 0; k < n; k++) bars.push(bar(t0 + d * 86400 + k * 3600, 1, 1, 1, 1));
  assert.equal(candlesPerDay(bars, TZ), 7);
});

test('structureLeg: inner swings are ignored, a new extreme extends from the lowest low, a break of the origin ends it', async () => {
  const { structureLeg } = await import('./ar.ts');
  const P = (index, price, kind) => ({ index, price, kind, confirmedAt: index + 1 });
  const piv = [P(0, 100, 'L'), P(1, 160, 'H'), P(2, 130, 'L'), P(3, 150, 'H'), P(4, 120, 'L')];
  assert.deepEqual(structureLeg(piv, 'up').map((p) => p.price), [100, 160], 'the lower high 150 is internal');
  const more = [...piv, P(5, 175, 'H')];
  assert.deepEqual(structureLeg(more, 'up').map((p) => p.price), [120, 175], 'new high: from the lowest low since 160');
  assert.equal(structureLeg([...piv, P(5, 140, 'H'), P(6, 95, 'L')], 'up'), null, 'a low under 100 ends the up structure');
  const down = piv.map((p) => ({ ...p, price: 300 - p.price, kind: p.kind === 'H' ? 'L' : 'H' }));
  assert.deepEqual(structureLeg(down, 'down').map((p) => p.price), [200, 140]);
});

test('withSessionOpen: a session that runs straight from the open gets its opening low as the leg origin', async () => {
  const { structureLeg, withSessionOpen } = await import('./ar.ts');
  // 25 Nov NIFTY futures shape: gap up, rally to the session's first swing high, no swing low before it
  const t0 = 1_800_000_000;
  const bars = [
    bar(t0, 100, 101, 99, 100), // yesterday
    bar(t0 + 20 * 3600, 130, 135, 129, 134), // gap open: the low of the session
    bar(t0 + 20 * 3600 + 300, 134, 150, 133, 149),
    bar(t0 + 20 * 3600 + 600, 149, 170, 148, 168),
    bar(t0 + 20 * 3600 + 900, 168, 169, 150, 152),
  ];
  const high = { index: 3, price: 170, kind: 'H', confirmedAt: 4 };
  assert.equal(structureLeg([high], 'up'), null, 'one swing: no leg');
  const ps = withSessionOpen(bars, 1, [high]);
  assert.deepEqual(ps[0], { index: 1, price: 129, kind: 'L', confirmedAt: 4 }, 'counted from when the high is confirmed');
  assert.deepEqual(structureLeg(ps, 'up').map((p) => p.price), [129, 170]);
  assert.deepEqual(withSessionOpen(bars, 1, []), [], 'no swing yet: unchanged');
  const low = { index: 1, price: 129, kind: 'L', confirmedAt: 2 };
  assert.deepEqual(withSessionOpen(bars, 1, [low, high]), [low, high], 'the extreme is the swing itself: unchanged');
});
