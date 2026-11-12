import assert from 'node:assert/strict';
import { test } from 'node:test';
import { AR_STRATEGY_DEFAULTS, arStrategyDefaults, arSummary, runArStrategy } from './ar-strategy.ts';

const TZ = 'America/New_York';
const H = 3600;
const T0 = Date.parse('2026-11-02T00:00:00-05:00') / 1000;
const bar = (time, o, h, l, c, v = 100) => ({ time, open: o, high: h, low: l, close: c, volume: v });

/** Hourly candles moving in straight segments: [[price, candles to get there], …] from `start`. */
function segments(start, legs) {
  const out = [];
  let px = start;
  let t = T0;
  for (const [to, n] of legs) {
    const step = (to - px) / n;
    for (let s = 0; s < n; s++) {
      const a = px;
      const b = px + step;
      out.push(bar(t, a, Math.max(a, b), Math.min(a, b), b));
      px = b;
      t += H;
    }
  }
  return out;
}

// Up trend (L50 → H120 → HL 80 → HH 200), a pull-back into the 80→200 leg's zone (115.4 … 93.7), then a rally to 205.
const UP = [
  [120, 10],
  [80, 10],
  [200, 10],
  [110, 10],
  [205, 5],
];
// Every hour of the day is "his window", and nothing is closed at a time of day, so the test is about the rules.
const ALL_DAY = { windowStart: 0, windowEnd: 1440, flatAt: 99999, envWindow: 6 };

const run = (bars, cfg = {}) => runArStrategy(bars, { timeZone: TZ, chartSecs: H, cfg: { ...ALL_DAY, ...cfg } });

test('long: trend up, pull-back into the discount zone, pressure flips → entry at the close, target hit', () => {
  const bars = segments(50, UP);
  const { trades, skips } = run(bars, { entry: 'close' });
  assert.equal(trades.length, 1, JSON.stringify(skips));
  const t = trades[0];
  assert.equal(t.dir, 'long');
  assert.equal(t.target, 200);
  assert.equal(t.outcome, 'target');
  assert.ok(t.entry > 110 && t.entry < 130, `entry ${t.entry}`);
  assert.ok(t.stop < 110, `stop ${t.stop} must sit under the low`);
  assert.ok(t.touchIndex < t.confirmIndex && t.confirmIndex <= t.entryIndex && t.entryIndex < t.exitIndex);
  assert.ok(Math.abs(t.r - (t.exit - t.entry) / (t.entry - t.stop)) < 1e-9);
  assert.ok(t.warnings.some((w) => w.startsWith('path:')), 'no Asia levels → a path warning, still traded');
  const s = arSummary(trades);
  assert.equal(s.trades, 1);
  assert.equal(s.wins, 1);
});

test('short is the mirror image', () => {
  const bars = segments(250, UP.map(([p, n]) => [300 - p, n]));
  const { trades } = run(bars, { entry: 'close' });
  assert.equal(trades.length, 1);
  assert.equal(trades[0].dir, 'short');
  assert.equal(trades[0].target, 100);
  assert.equal(trades[0].outcome, 'target');
});

test('pull-back entry fills at the middle of the confirmation candle, or is skipped if price never returns', () => {
  // straight rally after confirmation: no pull-back
  const none = run(segments(50, UP), { entry: 'pullback', entryWait: 3 });
  assert.equal(none.trades.length, 0);
  assert.ok(none.skips.some((s) => s.step === 'entry' && /pull-back/.test(s.reason)));

  // same, but the candle after confirmation dips back
  const bars = segments(50, UP);
  const confirm = run(bars, { entry: 'close' }).trades[0].confirmIndex;
  const c = bars[confirm];
  const mid = (c.high + c.low) / 2;
  const next = bars[confirm + 1];
  bars[confirm + 1] = bar(next.time, next.open, next.high, mid - 1, next.close);
  const { trades } = run(bars, { entry: 'pullback', entryWait: 3 });
  assert.equal(trades.length, 1);
  assert.equal(trades[0].entryIndex, confirm + 1);
  assert.ok(Math.abs(trades[0].entry - mid) < 1e-9);
});

test('a close past 0.886 cancels the zone: no trade', () => {
  const bars = segments(50, [
    [120, 10],
    [80, 10],
    [200, 10],
    [85, 10], // closes below 93.7
    [205, 5],
  ]);
  const { trades, reads } = run(bars, { entry: 'close' });
  assert.equal(trades.length, 0);
  assert.ok(reads.some((r) => r.checks.location.state === 'fail' && /0\.886/.test(r.checks.location.text)));
});

test('a confirmation outside the time window is skipped with the reason', () => {
  const { trades, skips } = run(segments(50, UP), { entry: 'close', windowStart: 0, windowEnd: 1 });
  assert.equal(trades.length, 0);
  assert.ok(skips.some((s) => s.step === 'confirmation' && /outside/.test(s.reason)));
});

test('reward ÷ risk below the minimum is skipped', () => {
  const { trades, skips } = run(segments(50, UP), { entry: 'close', minRR: 50 });
  assert.equal(trades.length, 0);
  assert.ok(skips.some((s) => /reward ÷ risk/.test(s.reason)));
});

test('no look-ahead: any prefix gives the same checklist and the same finished trades', () => {
  const bars = segments(50, [...UP, [150, 6], [240, 8], [170, 7], [260, 6]]);
  // the candle after each confirmation dips to its middle, so pull-back orders fill
  for (const t of run(bars, { entry: 'close' }).trades) {
    const c = bars[t.confirmIndex];
    const n = bars[t.confirmIndex + 1];
    if (n) bars[t.confirmIndex + 1] = bar(n.time, n.open, n.high, (c.high + c.low) / 2 - 1, n.close);
  }
  for (const entry of ['close', 'pullback']) {
    const full = run(bars, { entry });
    assert.ok(full.trades.length >= 1, `${entry}: the scenario must trade`);
    for (let cut = 10; cut <= bars.length; cut += 1) {
      const part = run(bars.slice(0, cut), { entry });
      assert.deepEqual(part.reads, full.reads.slice(0, cut), `${entry}: reads differ at cut ${cut}`);
      const done = (ts) => ts.filter((t) => t.exitIndex != null && t.exitIndex < cut);
      assert.deepEqual(done(part.trades), done(full.trades), `${entry}: trades differ at cut ${cut}`);
    }
  }
});

test('a still-forming last candle never confirms', () => {
  const bars = segments(50, UP);
  const confirm = run(bars, { entry: 'close' }).trades[0].confirmIndex;
  const live = runArStrategy(bars.slice(0, confirm + 1), {
    timeZone: TZ,
    chartSecs: H,
    formingLast: true,
    cfg: { ...ALL_DAY, entry: 'close' },
  });
  assert.equal(live.trades.length, 0);
  assert.equal(live.reads[confirm].checks.confirmation.state, 'wait');
});

test('NSE: the window is 09:15–10:45 India time and the Asia-range path step is skipped, not failed', () => {
  assert.deepEqual(arStrategyDefaults('America/New_York'), AR_STRATEGY_DEFAULTS);
  const d = arStrategyDefaults('Asia/Kolkata');
  assert.deepEqual([d.windowStart, d.windowEnd, d.flatAt], [555, 645, 915]);

  const IST = 'Asia/Kolkata';
  const ist = (t) => {
    const p = new Intl.DateTimeFormat('en-GB', { timeZone: IST, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(t * 1000));
    return Number(p.find((x) => x.type === 'hour').value) * 60 + Number(p.find((x) => x.type === 'minute').value);
  };
  const bars = segments(50, UP);
  // only the window is the default here: every hour is otherwise allowed and nothing is closed at a time of day
  const { trades, skips, reads } = runArStrategy(bars, {
    timeZone: IST,
    chartSecs: H,
    cfg: { flatAt: 99999, envWindow: 6, entry: 'close' },
  });
  const confirmations = [...trades.map((t) => t.confirmIndex), ...skips.filter((k) => k.step === 'confirmation').map((k) => k.index)];
  assert.ok(confirmations.length > 0, 'the scenario must reach confirmation');
  for (const t of trades) {
    const m = ist(bars[t.confirmIndex].time);
    assert.ok(m >= 555 && m < 645, `trade confirmed at ${m} min IST, outside 09:15–10:45`);
  }
  for (const k of skips.filter((k) => k.step === 'confirmation' && /outside/.test(k.reason))) {
    assert.match(k.reason, /09:15–10:45 India time/);
  }
  assert.ok(reads.some((r) => r.checks.path.state === 'warn' && /NSE/.test(r.checks.path.text)));
  assert.ok(!reads.some((r) => r.checks.path.state === 'fail'));
});

test('stacked imbalances: each trade records a zone at the fill; "require" skips without one; later zones do not count', () => {
  const bars = segments(50, UP);
  const base = run(bars, { entry: 'close' }).trades[0];
  assert.equal(base.stack, null, 'no stacked data given → null');
  const zone = (index) => ({ index, side: 'buy', lo: base.entry - 1, hi: base.entry + 1, rows: 3, usedAt: null, estimated: true });
  const covered = bars.map(() => true);
  const withStacks = (zones, cov = covered, cfg = {}) =>
    runArStrategy(bars, { timeZone: TZ, chartSecs: H, stacks: { zones, covered: cov }, cfg: { ...ALL_DAY, entry: 'close', ...cfg } });

  const inZone = withStacks([zone(base.confirmIndex)]);
  assert.equal(inZone.trades[0].stack, 'zone');
  assert.equal(withStacks([zone(base.confirmIndex)], covered, { requireStack: true }).trades.length, 1);

  // the same zone printed one candle AFTER the decision is unknown at the fill
  assert.equal(withStacks([zone(base.confirmIndex + 1)]).trades[0].stack, 'none');
  // a sell zone does not help a long
  assert.equal(withStacks([{ ...zone(base.confirmIndex), side: 'sell' }]).trades[0].stack, 'none');
  // already traded through before the fill
  assert.equal(withStacks([{ ...zone(base.confirmIndex - 2), usedAt: base.confirmIndex - 1 }]).trades[0].stack, 'none');

  const none = withStacks([], covered, { requireStack: true });
  assert.equal(none.trades.length, 0);
  assert.ok(none.skips.some((s) => s.step === 'entry' && /no stacked-imbalance zone/.test(s.reason)));
  const blind = withStacks([zone(base.confirmIndex)], bars.map(() => false), { requireStack: true });
  assert.equal(blind.trades.length, 0);
  assert.ok(blind.skips.some((s) => /no footprint data/.test(s.reason)));
  // off by default: the zone is only recorded
  assert.equal(withStacks([]).trades.length, 1);
});
