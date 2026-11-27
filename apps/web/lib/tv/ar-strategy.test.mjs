import assert from 'node:assert/strict';
import { test } from 'node:test';
import { AR_STRATEGY_DEFAULTS, arStrategyDefaults, arSummary, runArStrategy } from './ar-strategy.ts';

const TZ = 'America/New_York';
const H = 3600;
const T0 = Date.parse('2026-11-02T00:00:00-05:00') / 1000;
const bar = (time, o, h, l, c, v = 100) => ({ time, open: o, high: h, low: l, close: c, volume: v });

/** Lead-in candles before T0 (see `segments`), so the hourly swings already show a trend when the scenario starts. */
const W = 30;

/**
 * Hourly candles moving in straight segments: [[price, candles to get there], …] from `start`, at T0. Before them
 * come W lead-in candles that set up the trend the scenario starts in (for a rally first: a peak, then a fall to
 * `start`, so the swings read lower low then higher low; mirrored for a fall), because the strategy takes no trade
 * while the 1h trend is unclear. Indexes in the scenario are therefore W + its own index.
 */
function segments(start, legs) {
  const d = Math.sign(legs[0][0] - start) || 1;
  const lead = [
    [start + 60 * d, W / 2],
    [start, W / 2],
  ];
  const out = [];
  let px = start + 30 * d;
  let t = T0 - W * H;
  for (const [to, n] of [...lead, ...legs]) {
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
// Sideways only warns here: the turn at the bottom of a 6-candle window reads as rotation, and these tests are about
// the other rules (the sideways block has its own test).
// They confirm from candles (the default is order flow since 2026-11-26; order flow has its own tests with footprints).
// These scenarios are built around swing legs, so they run the older `range: 'swing'` rule (the week range has its own tests).
const ALL_DAY = { windowStart: 0, windowEnd: 1440, flatAt: 99999, confirm: 'candles', envRule: 'efficiency', envWindow: 6, envWindow4h: 3, sideways: 'warn', range: 'swing' };

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

test('environment: an unclear 1h trend or a sideways market takes no trade', () => {
  // without the lead-in the swings never show a direction before the setup (one low, a higher high): unclear
  const bare = segments(50, UP).slice(W);
  const unclear = run(bare, { entry: 'close' });
  assert.equal(unclear.trades.length, 0);
  assert.ok(unclear.skips.some((x) => x.step === 'environment' && /1h trend unclear/.test(x.reason)), JSON.stringify(unclear.skips));
  // at the turn the last 6 hourly candles read sideways, the 4-hour ones trending: by default sideways needs both,
  // so it trades; 'either' (stricter) blocks it
  const both = run(segments(50, UP), { entry: 'close', sideways: 'block' });
  assert.equal(both.trades.length, 1);
  const okRead = both.reads[both.trades[0].confirmIndex].checks.environment;
  assert.equal(okRead.state, 'pass');
  assert.match(okRead.text, /^1h trend up; 1h sideways, 4h trending$/);
  const either = run(segments(50, UP), { entry: 'close', sideways: 'block', sidewaysWhen: 'either' });
  assert.equal(either.trades.length, 0);
  assert.ok(either.skips.some((x) => x.step === 'environment' && /1h sideways, 4h trending: sideways, no trade/.test(x.reason)));
  // since 2026-11-26 the default read is 4h structure, where sideways only warns (his "fade the extremes")
  assert.equal(AR_STRATEGY_DEFAULTS.envRule, 'structure');
  // and since the same day confirmation is his order flow, read by his words only
  assert.equal(AR_STRATEGY_DEFAULTS.confirm, 'orderflow');
  assert.equal(AR_STRATEGY_DEFAULTS.flowRule, 'tree');
  assert.equal(AR_STRATEGY_DEFAULTS.sideways, 'warn');
  assert.equal(AR_STRATEGY_DEFAULTS.sidewaysWhen, 'both');
  assert.equal(AR_STRATEGY_DEFAULTS.trendRule, 'swings');
  // the older break-of-structure rule is still there to compare against
  assert.equal(run(segments(50, UP), { entry: 'close', trendRule: 'break' }).trades.length, 1);
});

test('checklist times are written on the chart\'s clock (displayTimeZone); the rules stay on the market\'s', () => {
  const bars = segments(50, UP);
  const ny = run(bars, { entry: 'close' });
  const ist = runArStrategy(bars, { timeZone: TZ, displayTimeZone: 'Asia/Kolkata', chartSecs: H, cfg: { ...ALL_DAY, entry: 'close' } });
  // same trades: the clock only changes what is written (warnings quote the checklist, so leave them out)
  const strip = (ts) => ts.map(({ warnings, ...t }) => t);
  assert.deepEqual(strip(ist.trades), strip(ny.trades));
  const i = ny.trades[0].confirmIndex;
  const at = (r) => r.reads[i].checks.confirmation.text.match(/flipped at (\d\d):(\d\d)/);
  const [, h1, m1] = at(ny);
  const [, h2, m2] = at(ist);
  // November: New York is UTC−5, India UTC+5:30 → 10 h 30 min ahead
  assert.equal((((+h2 * 60 + +m2) - (+h1 * 60 + +m1)) % 1440 + 1440) % 1440, 630);
  const leg = (r) => r.reads[i].checks.location.detail ?? r.reads[i].checks.location.text;
  assert.notEqual(leg(ist), leg(ny));
  assert.match(leg(ist), /swing 80\.00 \(\d\d-\d\d \d\d:\d\d\) → 200\.00/);
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

test('NSE: the window is the whole session, 09:15–15:15 India time and the Asia-range path step is skipped, not failed', () => {
  assert.deepEqual(arStrategyDefaults('America/New_York'), AR_STRATEGY_DEFAULTS);
  const d = arStrategyDefaults('Asia/Kolkata');
  assert.deepEqual([d.windowStart, d.windowEnd, d.flatAt], [555, 915, 915]);

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
    cfg: { flatAt: 99999, confirm: 'candles', envRule: 'efficiency', envWindow: 6, entry: 'close', range: 'swing' },
  });
  const confirmations = [...trades.map((t) => t.confirmIndex), ...skips.filter((k) => k.step === 'confirmation').map((k) => k.index)];
  assert.ok(confirmations.length > 0, 'the scenario must reach confirmation');
  for (const t of trades) {
    const m = ist(bars[t.confirmIndex].time);
    assert.ok(m >= 555 && m < 915, `trade confirmed at ${m} min IST, outside 09:15–15:15`);
  }
  for (const k of skips.filter((k) => k.step === 'confirmation' && /outside/.test(k.reason))) {
    assert.match(k.reason, /09:15–15:15 India time/);
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

// ── phase 4: order-flow confirmation ──
/** Footprint from [price, bid, ask] rows (low → high). */
function fpOf(rows, step, source) {
  const levels = rows.map(([price, bid, ask]) => ({ price, bid, ask }));
  let poc = 0;
  levels.forEach((l, i) => {
    if (l.bid + l.ask > levels[poc].bid + levels[poc].ask) poc = i;
  });
  const delta = levels.reduce((s, l) => s + l.ask - l.bid, 0);
  const total = levels.reduce((s, l) => s + l.ask + l.bid, 0);
  return { poc, levels, delta, total, step, ...(source ? { source } : {}) };
}

/**
 * The UP scenario with an absorption candle at the low (index 39: 3× volume, POC at the low, closes in the top half)
 * and a buyer-delta flip candle after it (index 40). `flip` sets candle 40's [bid, ask] split per row.
 */
function flowScenario({ absorbVol = 300, flipBid = 30, flipAsk = 70, source } = {}) {
  const bars = segments(50, UP);
  const a = bars[W + 39];
  bars[W + 39] = bar(a.time, a.open, a.high, a.low, 116, absorbVol); // 119 → low 110 → close 116
  const footprints = bars.map(() => null);
  footprints[W + 39] = fpOf([[110, 150, 60], [113, 30, 20], [116, 20, 20]], 3, source);
  // candle 40: 110 → 129; row 119 is thin (the LVN)
  footprints[W + 40] = fpOf(
    [
      [110, flipBid * 0.3, flipAsk * 0.3],
      [113, flipBid * 0.2, flipAsk * 0.2],
      [116, flipBid * 0.2, flipAsk * 0.2],
      [119, 1, 1],
      [122, flipBid * 0.15, flipAsk * 0.15],
      [125, flipBid * 0.15, flipAsk * 0.15],
      [128, 1, 1],
    ],
    3,
    source,
  );
  return { bars, footprints };
}
const runFlow = (bars, footprints, cfg = {}) =>
  runArStrategy(bars, { timeZone: TZ, chartSecs: H, footprints, cfg: { ...ALL_DAY, confirm: 'orderflow', flowRule: 'ours', ...cfg } });

test('order flow: absorption at the low, then a delta flip → a trade that says where its footprint came from', () => {
  const { bars, footprints } = flowScenario();
  const { trades, reads } = runFlow(bars, footprints, { entry: 'close' });
  assert.equal(trades.length, 1, JSON.stringify(reads.slice(W + 38, W + 42).map((r) => r.checks.confirmation)));
  const t = trades[0];
  assert.equal(t.dir, 'long');
  assert.equal(t.confirmIndex, W + 40);
  assert.equal(t.flow.source, 'estimated');
  assert.equal(t.flow.absorbIndex, W + 39);
  assert.equal(t.flow.absorbPrice, 110);
  assert.equal(t.flow.volRatio, 3);
  assert.ok(Math.abs(t.flow.flipShare - 40 / 104) < 1e-9); // the two thin rows add 4 contracts, no delta
  assert.match(reads[W + 39].checks.confirmation.text, /absorption at .*3\.0× volume.*waiting for the delta to flip/);
  assert.match(reads[W + 40].checks.confirmation.text, /delta flipped \+\d+ .*estimated footprint/);
  // the candle rule's trades carry no order-flow record
  assert.equal(run(bars, { entry: 'close' }).trades[0].flow, null);
  // recorded ticks on both candles
  const ticks = flowScenario({ source: 'ticks' });
  assert.equal(runFlow(ticks.bars, ticks.footprints, { entry: 'close' }).trades[0].flow.source, 'ticks');
});

test('order flow: no trade without absorption, without a flip, or without a footprint, each with its reason', () => {
  const quiet = flowScenario({ absorbVol: 100 });
  const q = runFlow(quiet.bars, quiet.footprints, { entry: 'close' });
  assert.equal(q.trades.length, 0);
  assert.match(q.reads[W + 40].checks.confirmation.text, /not absorption: volume 1\.0× usual/);

  const sellers = flowScenario({ flipBid: 70, flipAsk: 30 });
  const s = runFlow(sellers.bars, sellers.footprints, { entry: 'close' });
  assert.equal(s.trades.length, 0);
  assert.match(s.reads[W + 40].checks.confirmation.text, /waiting for the flip: delta −/);

  const blind = runFlow(quiet.bars, undefined, { entry: 'close', confirmWithin: 2 });
  assert.equal(blind.trades.length, 0);
  assert.match(blind.reads[W + 40].checks.confirmation.text, /no footprint for the low candle/);
  assert.ok(blind.skips.some((k) => k.reason === 'no footprint data for the order-flow check'));
});

test('LVN entry: the order rests at the confirmation candle’s thinnest row and fills on the pull-back', () => {
  const { bars, footprints } = flowScenario();
  const n = bars[W + 41];
  bars[W + 41] = bar(n.time, n.open, n.high, 120, n.close); // dips to 120: through the LVN row's middle (120.5)
  const { trades } = runFlow(bars, footprints, { entry: 'lvn', entryWait: 2 });
  assert.equal(trades.length, 1);
  assert.equal(trades[0].entryRule, 'lvn');
  assert.equal(trades[0].entry, 120.5);
  assert.equal(trades[0].entryIndex, W + 41);
  // without a footprint on the confirmation candle it falls back to mid-candle and says so
  const candles = run(bars, { entry: 'lvn', entryWait: 2 });
  for (const t of candles.trades) assert.equal(t.entryRule, 'mid');
});

test('order flow, no look-ahead: any prefix gives the same checklist and finished trades', () => {
  const { bars, footprints } = flowScenario();
  const n = bars[W + 41];
  bars[W + 41] = bar(n.time, n.open, n.high, 120, n.close);
  for (const entry of ['close', 'lvn']) {
    const full = runFlow(bars, footprints, { entry });
    assert.ok(full.trades.length >= 1);
    for (let cut = 10; cut <= bars.length; cut += 1) {
      const part = runFlow(bars.slice(0, cut), footprints.slice(0, cut), { entry });
      assert.deepEqual(part.reads, full.reads.slice(0, cut), `${entry}: reads differ at cut ${cut}`);
      const done = (ts) => ts.filter((t) => t.exitIndex != null && t.exitIndex < cut);
      assert.deepEqual(done(part.trades), done(full.trades), `${entry}: trades differ at cut ${cut}`);
    }
  }
});

// ── "dark pool decoder" stand-in as the zone ──────────────────────────────────────

// Leg 80 → 200: his zone is 115.4 … 93.7; the decoder box is 125.6 (0.62) … 110 (0.75) … 102.8 (0.81).
test('decoder zone: a pull-back to 118 reaches the decoder box but not 0.705, so only the decoder run trades', () => {
  const legs = [
    [120, 10],
    [80, 10],
    [200, 10],
    [118, 10],
    [205, 5],
  ];
  const fib = run(segments(50, legs), { entry: 'close' });
  assert.equal(fib.trades.length, 0, 'never reached 115.4');
  const dec = run(segments(50, legs), { entry: 'close', zone: 'decoder' });
  assert.equal(dec.trades.length, 1, JSON.stringify(dec.skips));
  const t = dec.trades[0];
  assert.equal(t.dir, 'long');
  assert.equal(t.target, 200);
  assert.ok(t.stop < 118);
  assert.ok(dec.reads.some((r) => /decoder box 102\.80–125\.60/.test(r.checks.location.text)));
});

test("decoder zone: a close past its far edge (102.8) cancels it, while his 0.886 (93.7) still stands", () => {
  const legs = [
    [120, 10],
    [80, 10],
    [200, 10],
    [100, 10],
    [205, 5],
  ];
  const dec = run(segments(50, legs), { entry: 'close', zone: 'decoder' });
  assert.equal(dec.trades.length, 0);
  assert.ok(dec.reads.some((r) => r.checks.location.state === 'fail' && /decoder's far edge \(102\.80\)/.test(r.checks.location.text)));
  const fib = run(segments(50, legs), { entry: 'close' });
  // after the 80 → 200 leg ends (candle 30) price never closes under 93.7, so his zone stands and trades
  assert.ok(!fib.reads.slice(W + 31).some((r) => r.checks.location.state === 'fail'), '0.886 never closed past');
  assert.equal(fib.trades.length, 1);
});

test('decoder zone, no look-ahead: any prefix gives the same checklist and finished trades', () => {
  const bars = segments(50, [[120, 10], [80, 10], [200, 10], [118, 10], [205, 5], [150, 6], [240, 8], [185, 7], [260, 6]]);
  const full = run(bars, { entry: 'close', zone: 'decoder' });
  assert.ok(full.trades.length >= 1);
  for (let cut = 10; cut <= bars.length; cut += 1) {
    const part = run(bars.slice(0, cut), { entry: 'close', zone: 'decoder' });
    assert.deepEqual(part.reads, full.reads.slice(0, cut), `reads differ at cut ${cut}`);
    const done = (ts) => ts.filter((t) => t.exitIndex != null && t.exitIndex < cut);
    assert.deepEqual(done(part.trades), done(full.trades), `trades differ at cut ${cut}`);
  }
});

// ── gamma regime (phase 5) ───────────────────────────────────────────────────────
// The UP long enters around 110–130 with the leg's end (200) as target. Levels are in chart prices (scale 1).
const snapOf = (asOf, { flip = 100, callWalls = [160, 220], putWalls = [90], netGex = 1e9 } = {}) => ({
  asOf,
  symbol: 'NQ=F',
  spot: 150,
  scale: 1,
  convention: 'dealer_long_calls',
  days: 45,
  expiries: [],
  all: { flip, callWalls, putWalls, gammaWall: null, netGex },
  nearest: null,
});
const runG = (bars, gamma, cfg = {}) =>
  runArStrategy(bars, { timeZone: TZ, chartSecs: H, gamma, cfg: { ...ALL_DAY, entry: 'close', ...cfg } });

test('gamma off: trades and reads carry the regime but keep the leg’s target', () => {
  const bars = segments(50, UP);
  const { trades, reads } = runG(bars, [snapOf(T0 - H)]);
  assert.equal(trades.length, 1);
  assert.equal(trades[0].target, 200);
  assert.deepEqual(
    { regime: trades[0].gamma.regime, basis: trades[0].gamma.basis, level: trades[0].gamma.level, legTarget: trades[0].gamma.legTarget },
    { regime: 'positive', basis: 'flip', level: null, legTarget: 200 },
  );
  // each candle's read uses its own close: the first candles sit under the flip (100), the confirmation above it
  assert.equal(reads[W].gamma.regime, 'negative');
  assert.equal(reads[trades[0].confirmIndex].gamma.regime, 'positive');
  // without snapshots nothing changes: no gamma on reads, null on trades
  const plain = run(bars, { entry: 'close' });
  assert.equal('gamma' in plain.reads[W], false);
  assert.equal(plain.trades[0].gamma, null);
});

test('gamma targets: positive gamma takes profit at the first level before the leg’s end; negative keeps it', () => {
  const bars = segments(50, UP);
  const pos = runG(bars, [snapOf(T0 - H)], { gamma: 'targets' }).trades[0];
  assert.equal(pos.target, 160);
  assert.equal(pos.gamma.level, 'C1');
  assert.equal(pos.outcome, 'target');
  assert.equal(pos.exit, 160);
  // flip above the entry: negative gamma, hold toward the leg's end
  const neg = runG(bars, [snapOf(T0 - H, { flip: 150 })], { gamma: 'targets' }).trades[0];
  assert.equal(neg.gamma.regime, 'negative');
  assert.equal(neg.target, 200);
  assert.equal(neg.gamma.level, null);
  // no flip in range: the net GEX sign decides
  const net = runG(bars, [snapOf(T0 - H, { flip: null, netGex: -5 })], { gamma: 'targets' }).trades[0];
  assert.deepEqual([net.gamma.basis, net.gamma.regime, net.target], ['net', 'negative', 200]);
});

test('gamma targets: the reward ÷ risk check uses the moved target, and says so', () => {
  const bars = segments(50, UP);
  const t = runG(bars, [snapOf(T0 - H)], { gamma: 'targets' }).trades[0];
  const rrAt = (target) => Math.abs(target - t.entry) / Math.abs(t.entry - t.stop);
  const minRR = (rrAt(160) + rrAt(200)) / 2;
  const off = runG(bars, [snapOf(T0 - H)], { minRR });
  assert.equal(off.trades.length, 1);
  const on = runG(bars, [snapOf(T0 - H)], { gamma: 'targets', minRR });
  assert.equal(on.trades.length, 0);
  assert.ok(on.skips.some((s) => /target moved to C1 160\.00: positive gamma/.test(s.reason)));
});

test('gamma, no look-ahead: a snapshot read after the confirmation candle closed is not used; stale ones expire', () => {
  const bars = segments(50, UP);
  const t = runG(bars, [snapOf(T0 - H)]).trades[0];
  const closeOf = (i) => bars[i].time + H;
  const late = runG(bars, [snapOf(closeOf(t.confirmIndex) + 1)], { gamma: 'targets' }).trades[0];
  assert.equal(late.gamma, null);
  assert.equal(late.target, 200);
  const onTime = runG(bars, [snapOf(closeOf(t.confirmIndex))], { gamma: 'targets' }).trades[0];
  assert.equal(onTime.gamma.level, 'C1');
  const stale = runG(bars, [snapOf(closeOf(t.confirmIndex) - 5 * 86400)], { gamma: 'targets' }).trades[0];
  assert.equal(stale.gamma, null);
  // the newest snapshot read in time wins: a later one moves the flip above the price
  const two = runG(bars, [snapOf(T0 - H), snapOf(closeOf(t.confirmIndex) - 60, { flip: 150 })], { gamma: 'targets' }).trades[0];
  assert.equal(two.gamma.regime, 'negative');
});

test('gamma targets, no look-ahead: any prefix gives the same checklist and finished trades', () => {
  const bars = segments(50, [...UP, [150, 6], [240, 8], [170, 7], [260, 6]]);
  const snaps = [snapOf(T0 - H), snapOf(T0 + 30 * H, { flip: 160, callWalls: [230, 250], putWalls: [140] })];
  const full = runG(bars, snaps, { gamma: 'targets' });
  assert.ok(full.trades.length >= 1);
  for (let cut = 10; cut <= bars.length; cut += 1) {
    const part = runG(bars.slice(0, cut), snaps, { gamma: 'targets' });
    assert.deepEqual(part.reads, full.reads.slice(0, cut), `reads differ at cut ${cut}`);
    const done = (ts) => ts.filter((x) => x.exitIndex != null && x.exitIndex < cut);
    assert.deepEqual(done(part.trades), done(full.trades), `trades differ at cut ${cut}`);
  }
});

// ── the week range (his "most active range", range: 'week') ──
/** Hourly candles in straight segments from `start` at T0, after a 7-day lead-in that swings 140 ↔ 160 (never an extreme). */
function weekScenario(start, legs) {
  const lead = [];
  for (let k = 0; k < 7; k++) lead.push([160, 12], [140, 12]);
  const out = [];
  let px = 150;
  let t = T0 - 168 * H;
  for (const [to, n] of [...lead, [start, 1], ...legs]) {
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
const WEEK = { windowStart: 0, windowEnd: 1440, flatAt: 99999, confirm: 'candles', envWindow: 6, envWindow4h: 3, sideways: 'warn', range: 'week', entry: 'close' };
const runWeek = (bars, cfg = {}) => runArStrategy(bars, { timeZone: TZ, chartSecs: H, cfg: { ...WEEK, ...cfg } });
// up to a 200 high, down to a 100 low (high first: a down range, premium 170.5–188.6), back up into premium, then down
const DROP = [
  [200, 30],
  [100, 50],
  [180, 20],
  [95, 20],
];

test('week range: high first = down trend; a bounce into its premium (0.705–0.886) and a red close → short to the low', () => {
  const bars = weekScenario(150, DROP);
  const { trades, skips, reads } = runWeek(bars);
  assert.equal(trades.length, 1, JSON.stringify(skips));
  const t = trades[0];
  assert.equal(t.dir, 'short');
  assert.ok(Math.abs(t.target - 100) < 1e-6, 'target = the week low (draw on liquidity)');
  assert.ok(bars[t.touchIndex].high >= 170.5 && bars[t.touchIndex - 1].high < 170.5, 'first touch of 0.705');
  assert.equal(t.outcome, 'target');
  const loc = reads[t.confirmIndex].checks.location.text;
  assert.match(loc, /week range 200\.00 .* → 100\.00/);
  assert.match(reads[t.confirmIndex].checks.environment.text, /^week range down/);
});

test('week range: a close past its 0.886 makes the trend unclear: no short', () => {
  const bars = weekScenario(150, [
    [200, 30],
    [100, 50],
    [195, 20], // closes above 188.6
    [95, 20],
  ]);
  const { trades, reads } = runWeek(bars);
  assert.equal(trades.length, 0);
  // the bounce after the 100 low (lead-in 168 + 1 + 30 up + 50 down)
  const after = reads.findIndex((r, i) => i > 250 && bars[i].close > 188.6);
  assert.ok(after > 0);
  assert.equal(reads[after].checks.environment.state, 'fail');
  assert.match(reads[after].checks.environment.text, /week range unclear/);
});

test('week range: while price waits in the zone the setup is watched again (no "no confirmation" skip), then trades', () => {
  const bars = weekScenario(150, [
    [200, 30],
    [100, 50],
    [178, 20],
    [178, 10], // flat inside the zone for longer than the 6-candle confirmation window
    [95, 20],
  ]);
  const { trades, skips } = runWeek(bars);
  assert.equal(trades.length, 1, JSON.stringify(skips));
  assert.equal(trades[0].dir, 'short');
  assert.equal(skips.filter((k) => /no confirmation within/.test(k.reason)).length, 0);
  // the older swing rule gives up after one window
  const old = runWeek(bars, { range: 'swing' });
  assert.ok(old.trades.every((t) => t.dir !== 'short' || t.confirmIndex !== trades[0].confirmIndex));
});

test('week range: at most legTrades trades per range', () => {
  // two bounces into the same premium without reaching the low in between: the first short is stopped by the higher
  // second bounce (182 is still below 0.886 = 188.6), which then confirms a second short
  const legs = [
    [200, 30],
    [100, 50],
    [175, 20],
    [150, 10],
    [182, 10],
    [95, 20],
  ];
  const bars = weekScenario(150, legs);
  const two = runWeek(bars, { minRR: 0.1 });
  const one = runWeek(bars, { minRR: 0.1, legTrades: 1 });
  assert.ok(two.trades.length >= 2, `expected a re-entry, got ${two.trades.length}`);
  assert.equal(one.trades.length, 1);
});

test('week range, no look-ahead: any prefix gives the same checklist and the same finished trades', () => {
  const bars = weekScenario(150, DROP);
  const full = runWeek(bars);
  for (let k = 170; k <= bars.length; k += 3) {
    const part = runWeek(bars.slice(0, k));
    assert.deepEqual(part.reads[k - 1].checks, full.reads[k - 1].checks, `checklist at ${k - 1}`);
    const done = (r) => r.trades.filter((t) => t.exitIndex != null && t.exitIndex < k - 1).map((t) => JSON.stringify(t));
    assert.deepEqual(done(part), done(full), `finished trades before ${k - 1}`);
  }
});

// ── trade management: breakeven and the trail (his live trades, 3:33–3:48) ──
/** The UP long (entry at the confirmation close), then hand-made hourly candles after the fill. */
function afterLong(next) {
  const base = segments(50, UP);
  const t = run(base, { entry: 'close', breakeven: 0, trail: 'off' }).trades[0];
  const bars = base.slice(0, t.entryIndex + 1);
  const e = t.entry;
  let time = bars[bars.length - 1].time;
  for (const [o, h, l, c] of next(e)) bars.push(bar((time += H), o, h, l, c));
  return { bars, e, entryIndex: t.entryIndex };
}

test('breakeven: once a completed candle is 25 points up, the stop moves to the entry from the next candle', () => {
  const { bars, e, entryIndex } = afterLong((e) => [
    [e, e + 30, e + 2, e + 28], // +30: arms breakeven at its close
    [e + 28, e + 29, e - 1, e + 1], // back through the entry: out at breakeven
  ]);
  const { trades } = run(bars, { entry: 'close', breakeven: 25, trail: 'off' });
  const t = trades[0];
  assert.equal(t.outcome, 'breakeven');
  assert.equal(t.exitIndex, entryIndex + 2);
  assert.ok(Math.abs(t.exit - e) < 1e-9 && Math.abs(t.points) < 1e-9);
  assert.deepEqual(t.stopMoves, [{ index: entryIndex + 1, price: e, why: 'breakeven' }]);
  assert.ok(t.stop < e, 'the initial stop is kept, so R is still measured against it');
});

test('breakeven: no same-candle look-ahead — the candle that reaches +25 cannot also stop out at the entry', () => {
  const { bars, e, entryIndex } = afterLong((e) => [
    [e, e + 30, e - 1, e + 5], // up 30 and down through the entry in one candle: the stop there is still the initial one
    [e + 5, e + 6, e - 1, e], // the next candle crosses the entry: now it is breakeven
  ]);
  const t = run(bars, { entry: 'close', breakeven: 25, trail: 'off' }).trades[0];
  assert.equal(t.exitIndex, entryIndex + 2);
  assert.equal(t.outcome, 'breakeven');
});

test('breakeven in R, and never at a price the close is already past', () => {
  const { bars, entryIndex } = afterLong((e) => [
    [e, e + 30, e - 3, e - 2], // far enough, but it closed below the entry: no stop can rest at the entry yet
    [e - 2, e + 4, e - 2.5, e + 3], // closes above the entry: breakeven now
  ]);
  const t0 = run(bars, { entry: 'close', breakeven: 25, trail: 'off' }).trades[0];
  assert.equal(t0.breakevenAt, entryIndex + 2);
  const risk = Math.abs(t0.entry - t0.stop);
  const inR = run(bars, { entry: 'close', breakeven: 30 / risk + 0.01, breakevenUnit: 'R', trail: 'off' }).trades[0];
  assert.equal(inR.breakevenAt, null, 'just short of the R distance: no breakeven');
});

test('trail: after breakeven the stop follows each completed candle’s low, only upward, and exits as "trail"', () => {
  const { bars, e, entryIndex } = afterLong((e) => [
    [e, e + 30, e + 2, e + 28], // breakeven, and the trail moves just under this low
    [e + 28, e + 45, e + 20, e + 40], // trail up under 20
    [e + 40, e + 41, e + 15, e + 18], // a lower low: hits the trailed stop (just under +20)
  ]);
  const t = run(bars, { entry: 'close', breakeven: 25, trail: 'candle' }).trades[0];
  assert.equal(t.outcome, 'trail');
  assert.equal(t.exitIndex, entryIndex + 3);
  assert.ok(t.points > 15 && t.points < 20, `points ${t.points}`);
  assert.deepEqual(t.stopMoves.map((m) => m.why), ['breakeven', 'trail', 'trail']);
  for (let k = 1; k < t.stopMoves.length; k++) assert.ok(t.stopMoves[k].price > t.stopMoves[k - 1].price, 'only tightens');
  // with breakeven off the trail starts from the first candle after the fill
  const noBe = run(bars, { entry: 'close', breakeven: 0, trail: 'candle' }).trades[0];
  assert.equal(noBe.stopMoves[0].why, 'trail');
  assert.equal(noBe.stopMoves[0].index, entryIndex + 1);
});

test('management, no look-ahead: any prefix gives the same checklist and finished trades', () => {
  const { bars } = afterLong((e) => [
    [e, e + 30, e + 2, e + 28],
    [e + 28, e + 45, e + 20, e + 40],
    [e + 40, e + 41, e + 15, e + 18],
    [e + 18, e + 20, e + 10, e + 12],
  ]);
  const cfg = { entry: 'close', breakeven: 25, trail: 'candle' };
  const full = run(bars, cfg);
  for (let k = 40; k <= bars.length; k++) {
    const part = run(bars.slice(0, k), cfg);
    assert.deepEqual(part.reads[k - 1].checks, full.reads[k - 1].checks);
    const done = (r) => r.trades.filter((t) => t.exitIndex != null && t.exitIndex < k - 1).map((t) => JSON.stringify(t));
    assert.deepEqual(done(part), done(full));
  }
});

// ── environment from 4h structure (envRule: 'structure', the default since 2026-11-26) ──

/** Hourly candles along straight legs [[price, candles], …] from `start` at T0, with a little wick on each. */
function path(start, legs) {
  const out = [];
  let px = start;
  let t = T0;
  for (const [to, n] of legs) {
    const step = (to - px) / n;
    for (let s = 0; s < n; s++) {
      const a = px;
      const b = px + step;
      out.push(bar(t, a, Math.max(a, b) + 0.3, Math.min(a, b) - 0.3, b));
      px = b;
      t += H;
    }
  }
  return out;
}
const STRUCT = { ...ALL_DAY, envRule: 'structure' };
const envAt = (bars, k = bars.length - 1, cfg = {}) =>
  runArStrategy(bars, { timeZone: TZ, chartSecs: H, cfg: { ...STRUCT, ...cfg } }).reads[k].checks.environment;
// a staircase: up 60 over a day, back 30 over half a day, six times
const STAIRS = Array.from({ length: 6 }, (_, k) => [
  [100 + 30 * k + 60, 24],
  [100 + 30 * k + 30, 12],
]).flat();

test('structure environment: a staircase reads trending on 4h, its mirror trending down', () => {
  const up = envAt(path(100, STAIRS));
  assert.match(up.text, /4h trending up: higher highs and higher lows/);
  const down = envAt(path(400, STAIRS.map(([p, n]) => [500 - p, n])));
  assert.match(down.text, /4h trending down: lower highs and lower lows/);
  // the efficiency read on the same staircase: the pull-backs make it look much less like a trend
  const eff = runArStrategy(path(100, STAIRS), { timeZone: TZ, chartSecs: H, cfg: { ...ALL_DAY, envWindow: 12, envWindow4h: 6 } });
  assert.ok(eff.reads.at(-1).checks.environment.text.includes('1h'), 'the older rule still runs with envRule: efficiency');
});

test('structure environment: overlapping swings are sideways (warn, or no trade with sideways: block)', () => {
  // an expanding range: each swing high higher and each swing low lower
  const legs = [];
  for (let k = 0; k < 6; k++) legs.push([150 + 8 * k, 24], [100 - 8 * k, 24]);
  legs.push([125, 12]);
  const bars = path(125, legs);
  // on the week range (high 158 then low 60, price back at 125): the 1h swings of an expanding range are unclear,
  // which the older swing rule refuses before it ever gets to the environment
  const warn = envAt(bars, bars.length - 1, { range: 'week' });
  assert.equal(warn.state, 'warn', warn.text);
  assert.match(warn.text, /4h sideways: the swings overlap \(higher high, lower low\), so only a fade of the range's edge/);
  const block = envAt(bars, bars.length - 1, { range: 'week', sideways: 'block' });
  assert.equal(block.state, 'fail');
  assert.match(block.text, /4h sideways: .*no trade/);
});

test('structure environment: a quiet last day is choppy, no trade', () => {
  const bars = path(100, [...STAIRS, [281, 24]]);
  const quiet = path(bars.at(-1).close, [[bars.at(-1).close + 0.2, 24]]).map((b, k) => ({ ...b, time: bars.at(-1).time + (k + 1) * H }));
  const env = envAt([...bars, ...quiet]);
  assert.equal(env.state, 'fail');
  assert.match(env.text, /choppy: the last day's range is 0\.\d\d× a normal day's/);
});

test('structure environment, no look-ahead: any prefix gives the same checklist', () => {
  const bars = path(100, [...STAIRS, [200, 30], [260, 20]]);
  const full = runArStrategy(bars, { timeZone: TZ, chartSecs: H, cfg: STRUCT });
  for (let k = 40; k <= bars.length; k += 13) {
    const part = runArStrategy(bars.slice(0, k), { timeZone: TZ, chartSecs: H, cfg: STRUCT });
    assert.deepEqual(part.reads[k - 1].checks.environment, full.reads[k - 1].checks.environment, `k=${k}`);
  }
});

// ── location: week range + latest intraday swing (range: 'both', the default since 2026-11-26) ──

// the staircase, then a rally 280 → 340 and a pull-back to 292 (inside that leg's 0.705–0.886 zone, 297.7–286.8),
// then up again: far above the week range's own discount zone
const LEG_PULLBACK = path(100, [...STAIRS, [340, 24], [292, 12], [345, 10]]);

test('range both: trades the latest intraday swing zone the week range alone never reaches', () => {
  const both = runArStrategy(LEG_PULLBACK, { timeZone: TZ, chartSecs: H, cfg: { ...STRUCT, range: 'both', entry: 'close' } });
  const week = runArStrategy(LEG_PULLBACK, { timeZone: TZ, chartSecs: H, cfg: { ...STRUCT, range: 'week', entry: 'close' } });
  const onLeg = (x) => x.target > 340 && x.target < 341; // the rally's high, 340.30 with its wick
  const t = both.trades.find(onLeg);
  assert.ok(t, JSON.stringify(both.skips.slice(-5)));
  assert.equal(t.dir, 'long');
  assert.ok(t.entry > 286 && t.entry < 305, `entry ${t.entry}`);
  assert.ok(!week.trades.some(onLeg), 'the week range alone has no zone there');
  const touch = both.reads[t.touchIndex].checks;
  assert.match(touch.location.text, /swing 279\.70 .* also watching the week range discount zone/);
  assert.match(touch.environment.text, /^intraday swing up; 4h trending up/);
});

test('range both, no look-ahead: any prefix gives the same checklist and finished trades', () => {
  const cfg = { ...STRUCT, range: 'both', entry: 'close' };
  const full = runArStrategy(LEG_PULLBACK, { timeZone: TZ, chartSecs: H, cfg });
  for (let k = 60; k <= LEG_PULLBACK.length; k += 11) {
    const part = runArStrategy(LEG_PULLBACK.slice(0, k), { timeZone: TZ, chartSecs: H, cfg });
    assert.deepEqual(part.reads[k - 1].checks, full.reads[k - 1].checks, `k=${k}`);
    const done = (r) => r.trades.filter((t) => t.exitIndex != null && t.exitIndex < k - 1).map((t) => JSON.stringify(t));
    assert.deepEqual(done(part), done(full));
  }
});

test('candleRule midpoint: confirms on a close past the high/low candle’s middle, never later than the strict rule', () => {
  const strict = run(segments(50, UP), { entry: 'close' });
  const mid = run(segments(50, UP), { entry: 'close', candleRule: 'midpoint' });
  assert.equal(strict.trades.length, 1);
  assert.equal(mid.trades.length, 1);
  assert.ok(mid.trades[0].confirmIndex <= strict.trades[0].confirmIndex);
  const t = mid.trades[0];
  const ext = segments(50, UP)[t.touchIndex];
  assert.ok(t.confirmIndex > t.touchIndex, 'never on the touch candle itself');
  assert.ok(ext, 'touch candle exists');
  assert.equal(AR_STRATEGY_DEFAULTS.candleRule, 'beyond');
});

test('absorbAt zone + video rule, no look-ahead: any prefix gives the same checklist and finished trades', () => {
  const { bars, footprints } = flowScenario();
  const cfg = { absorbAt: 'zone', flowRule: 'video', confirmWithin: 12, entry: 'close' };
  const full = runFlow(bars, footprints, cfg);
  for (let cut = 10; cut <= bars.length; cut += 1) {
    const part = runFlow(bars.slice(0, cut), footprints.slice(0, cut), cfg);
    assert.deepEqual(part.reads, full.reads.slice(0, cut), `reads differ at cut ${cut}`);
    const done = (ts) => ts.filter((t) => t.exitIndex != null && t.exitIndex < cut);
    assert.deepEqual(done(part.trades), done(full.trades), `trades differ at cut ${cut}`);
  }
});

test('legRule structure: an inner pull-back swing does not replace the day’s leg; latest does', () => {
  // the staircase, a rally 280 → 340, a pull-back to 310, a lower high at 330 (inside the leg), then a fall to 296:
  // into the 280 → 340 leg's zone (297.7–286.8), straight through the 310 → 330 swing's zone
  const bars = path(100, [...STAIRS, [340, 24], [310, 8], [330, 8], [296, 12]]);
  const loc = (legRule) =>
    runArStrategy(bars, { timeZone: TZ, chartSecs: H, cfg: { ...STRUCT, legRule, entry: 'close' } }).reads.at(-1).checks.location.text;
  assert.match(loc('structure'), /price reached the discount zone \(long\).* swing 279\.70 .*→ 340\.30/, loc('structure'));
  assert.match(loc('latest'), /long zone cancelled · swing 309\.70 .*→ 330\.30/, loc('latest'));
});

test("flowRule tree, no look-ahead: any prefix gives the same checklist and finished trades", () => {
  const { bars, footprints } = flowScenario();
  for (const cfg of [{ flowRule: 'tree', entry: 'close' }, { flowRule: 'tree', entry: 'pullback', confirmWithin: 12 }]) {
    const full = runFlow(bars, footprints, cfg);
    for (let cut = 10; cut <= bars.length; cut += 1) {
      const part = runFlow(bars.slice(0, cut), footprints.slice(0, cut), cfg);
      assert.deepEqual(part.reads, full.reads.slice(0, cut), `reads differ at cut ${cut}`);
      const done = (ts) => ts.filter((t) => t.exitIndex != null && t.exitIndex < cut);
      assert.deepEqual(done(part.trades), done(full.trades), `trades differ at cut ${cut}`);
    }
  }
});
