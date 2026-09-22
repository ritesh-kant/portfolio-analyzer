import assert from 'node:assert/strict';
import { test } from 'node:test';
import { points, sessionBars, sessionDayKey, tradeLevels } from './momentum-session.ts';

// ── the session window (2026-09-22) ──────────────────────────────────────────
// Stored trades begin with the previous session's last trade and the pre-open
// print; the chart must not draw or measure either.

const prologue = [
  { time: '2026-09-21T15:56:00+05:30', open: 367, high: 367, low: 367, close: 367, volume: 15666 },
  { time: '2026-09-22T09:09:00+05:30', open: 392.6, high: 392.6, low: 392.6, close: 392.6, volume: 0 },
];
const session = Array.from({ length: 6 }, (_, i) => ({
  time: new Date(Date.parse('2026-09-22T09:15:00+05:30') + i * 60_000).toISOString(),
  open: 389 + i, high: 390 + i, low: 388 + i, close: 389.5 + i, volume: 1000,
}));
const entry = '2026-09-22T10:48:02.364+05:30';

test('bars before the opening bell are not part of the session', () => {
  assert.deepEqual(sessionBars([...prologue, ...session], entry), session);
});

test('the opening bar itself is kept', () => {
  assert.equal(sessionBars(session, entry)[0]?.time, session[0].time);
});

test('a different session day is dropped whatever offset it is written in', () => {
  const asUtc = { ...prologue[0], time: new Date(Date.parse(prologue[0].time)).toISOString() };
  assert.deepEqual(sessionBars([asUtc, ...session], entry), session);
});

test('with no usable entry time the last bar decides the session', () => {
  assert.deepEqual(sessionBars([...prologue, ...session], null), session);
});

test('bars in one session share a day key, across a day boundary they do not', () => {
  assert.equal(sessionDayKey(session[0].time), sessionDayKey(session[5].time));
  assert.notEqual(sessionDayKey(prologue[0].time), sessionDayKey(session[0].time));
  assert.equal(sessionDayKey('not a time'), null);
});

// ── indicators (2026-09-22) ─────────────────────────────────────────────────
// These are checked against the engine's own pandas definitions: on the real
// RHIM 2026-09-22 session all six series agree with `indicators.py` to 1e-13,
// including the bar each one starts on.

const minute = 60_000;
const day0 = Date.parse('2026-09-22T09:15:00+05:30');
const walk = (n, start, from = day0) =>
  Array.from({ length: n }, (_, i) => {
    const close = start + Math.sin(i / 3) * 2 + i * 0.05;
    return {
      time: new Date(from + i * minute).toISOString(),
      open: close - 0.2, high: close + 0.3, low: close - 0.4, close, volume: 1000 + i,
    };
  });

test('the MACD signal waits for nine real MACD values, not nine bars', () => {
  const out = points(walk(60, 100));
  // MACD needs 26 closes, its signal nine MACD values on top of that.
  assert.equal(out.findIndex((p) => p.macd !== null), 25);
  assert.equal(out.findIndex((p) => p.signal !== null), 33);
  assert.equal(out.findIndex((p) => p.histogram !== null), 33);
  assert.ok(out.slice(0, 33).every((p) => p.signal === null), 'no signal may be drawn early');
});

test('leading nulls are not fed to the signal EMA as zeros', () => {
  // The regression, pinned against the definition rather than the helper: the
  // signal is seeded on the FIRST MACD value. Seeding it 25 bars earlier on a
  // run of zeros — what this did until 2026-09-22 — drags it toward zero.
  const out = points(walk(60, 100));
  const macd = out.map((p) => p.macd);
  const first = macd.findIndex((value) => value !== null);
  const alpha = 2 / 10;
  let honest = macd[first];
  let seededOnZeros = 0;
  for (let i = 1; i < macd.length; i += 1) {
    if (i > first) honest = macd[i] * alpha + honest * (1 - alpha);
    seededOnZeros = (macd[i] ?? 0) * alpha + seededOnZeros * (1 - alpha);
    if (i < first + 8) continue;
    assert.ok(Math.abs(out[i].signal - honest) < 1e-9,
      `bar ${i}: signal ${out[i].signal} should be ${honest}`);
    if (i < first + 20) {
      assert.ok(Math.abs(out[i].signal - seededOnZeros) > 1e-9,
        `bar ${i}: signal still matches the zero-seeded series`);
    }
  }
});

test('EMAs start on their own span, as pandas min_periods does', () => {
  const out = points(walk(30, 100));
  assert.equal(out.findIndex((p) => p.ema9 !== null), 8);
  assert.equal(out.findIndex((p) => p.ema20 !== null), 19);
});

test('VWAP is a session measure and resets at the next open', () => {
  const first = walk(5, 100);
  const second = walk(5, 400, day0 + 24 * 60 * minute);
  const out = points([...first, ...second]);
  const typical = (b) => (b.high + b.low + b.close) / 3;
  assert.ok(Math.abs(out[0].vwap - typical(first[0])) < 1e-9);
  // the first bar of the second session starts its own average again
  assert.ok(Math.abs(out[5].vwap - typical(second[0])) < 1e-9,
    `${out[5].vwap} carried the previous session in`);
  assert.ok(out[5].vwap > 300, 'yesterday must not anchor today');
});


// ── which level governed what (2026-09-22) ───────────────────────────────────
// The real RHIM row: the percentages were measured from the trigger 391.30,
// the fill was 392.60, and the chart rebuilt both lines off the fill.

const rhim = {
  entry_price: 392.6,
  trigger_px: 391.3,
  stop: 389.55,
  qty: 1,
  symbol: 'RHIM',
  status: 'closed',
  setup: 'attention_1m_confirmation',
  time: '2026-09-22T10:47:00+05:30',
  entry_time: '2026-09-22T10:48:02+05:30',
  resist_head_pct: 0.08944543828263887,
  support_drop_pct: 3.3350370559672915,
};

test('an old row rebuilds its levels from the trigger, never the fill', () => {
  const levels = tradeLevels(rhim);
  const resistance = levels.find((level) => level.side === 'resistance');
  const support = levels.find((level) => level.side === 'support');
  assert.ok(Math.abs(resistance.price - 391.65) < 0.01, `got ${resistance.price}`);
  assert.ok(Math.abs(support.price - 378.26) < 0.01, `got ${support.price}`);
  // the old reconstruction off the fill, which must not come back
  assert.ok(Math.abs(resistance.price - 392.95) > 1.0);
  assert.ok(Math.abs(support.price - 379.51) > 1.0);
});

test('level_anchor_px wins over trigger_px once rows carry it', () => {
  const levels = tradeLevels({ ...rhim, level_anchor_px: 400, trigger_px: 391.3 });
  const resistance = levels.find((level) => level.side === 'resistance');
  assert.ok(Math.abs(resistance.price - 400 * 1.0008944543828264) < 1e-6);
});

test('a recorded price is used as-is, not reconstructed', () => {
  const levels = tradeLevels({ ...rhim, resist_px: 391.65, resist_kind: 'pivot_high' });
  const resistance = levels.find((level) => level.side === 'resistance');
  assert.equal(resistance.price, 391.65);
  assert.equal(resistance.kind, 'pivot_high');
});

test('the rule-bearing level is the solid line; the recorded-only one is faint', () => {
  const levels = tradeLevels({
    ...rhim,
    resist_px: 391.65, resist_kind: 'pivot_high',
    structural_resistance: 397.85, structural_resistance_kind: 'prev_day',
  });
  const solid = levels.filter((level) => level.side === 'resistance' && !level.faint);
  const faint = levels.filter((level) => level.side === 'resistance' && level.faint);
  assert.equal(solid.length, 1);
  assert.equal(solid[0].price, 397.85);
  assert.equal(solid[0].kind, 'prev_day');
  assert.equal(solid[0].label, 'Resistance');
  assert.equal(faint.length, 1);
  assert.equal(faint[0].price, 391.65);
});

test('one price is drawn once, whichever field it came from', () => {
  const levels = tradeLevels({
    ...rhim, resist_px: 391.65, structural_resistance: 391.65,
    support_px: 378.26, structural_support: 378.26,
  });
  assert.equal(levels.filter((level) => level.side === 'resistance').length, 1);
  assert.equal(levels.filter((level) => level.side === 'support').length, 1);
  assert.ok(levels.every((level) => !level.faint));
});

test('a trade with no levels at all draws none', () => {
  const levels = tradeLevels({ ...rhim, resist_head_pct: null, support_drop_pct: null });
  assert.deepEqual(levels, []);
});
