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
  const a = bars[39];
  bars[39] = bar(a.time, a.open, a.high, a.low, 116, absorbVol); // 119 → low 110 → close 116
  const footprints = bars.map(() => null);
  footprints[39] = fpOf([[110, 150, 60], [113, 30, 20], [116, 20, 20]], 3, source);
  // candle 40: 110 → 129; row 119 is thin (the LVN)
  footprints[40] = fpOf(
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
  runArStrategy(bars, { timeZone: TZ, chartSecs: H, footprints, cfg: { ...ALL_DAY, confirm: 'orderflow', ...cfg } });

test('order flow: absorption at the low, then a delta flip → a trade that says where its footprint came from', () => {
  const { bars, footprints } = flowScenario();
  const { trades, reads } = runFlow(bars, footprints, { entry: 'close' });
  assert.equal(trades.length, 1, JSON.stringify(reads.slice(38, 42).map((r) => r.checks.confirmation)));
  const t = trades[0];
  assert.equal(t.dir, 'long');
  assert.equal(t.confirmIndex, 40);
  assert.equal(t.flow.source, 'estimated');
  assert.equal(t.flow.absorbIndex, 39);
  assert.equal(t.flow.absorbPrice, 110);
  assert.equal(t.flow.volRatio, 3);
  assert.ok(Math.abs(t.flow.flipShare - 40 / 104) < 1e-9); // the two thin rows add 4 contracts, no delta
  assert.match(reads[39].checks.confirmation.text, /absorption at .*3\.0× volume.*waiting for the delta to flip/);
  assert.match(reads[40].checks.confirmation.text, /delta flipped \+\d+ .*estimated footprint/);
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
  assert.match(q.reads[40].checks.confirmation.text, /not absorption: volume 1\.0× usual/);

  const sellers = flowScenario({ flipBid: 70, flipAsk: 30 });
  const s = runFlow(sellers.bars, sellers.footprints, { entry: 'close' });
  assert.equal(s.trades.length, 0);
  assert.match(s.reads[40].checks.confirmation.text, /waiting for the flip: delta −/);

  const blind = runFlow(quiet.bars, undefined, { entry: 'close', confirmWithin: 2 });
  assert.equal(blind.trades.length, 0);
  assert.match(blind.reads[40].checks.confirmation.text, /no footprint for the low candle/);
  assert.ok(blind.skips.some((k) => k.reason === 'no footprint data for the order-flow check'));
});

test('LVN entry: the order rests at the confirmation candle’s thinnest row and fills on the pull-back', () => {
  const { bars, footprints } = flowScenario();
  const n = bars[41];
  bars[41] = bar(n.time, n.open, n.high, 120, n.close); // dips to 120: through the LVN row's middle (120.5)
  const { trades } = runFlow(bars, footprints, { entry: 'lvn', entryWait: 2 });
  assert.equal(trades.length, 1);
  assert.equal(trades[0].entryRule, 'lvn');
  assert.equal(trades[0].entry, 120.5);
  assert.equal(trades[0].entryIndex, 41);
  // without a footprint on the confirmation candle it falls back to mid-candle and says so
  const candles = run(bars, { entry: 'lvn', entryWait: 2 });
  for (const t of candles.trades) assert.equal(t.entryRule, 'mid');
});

test('order flow, no look-ahead: any prefix gives the same checklist and finished trades', () => {
  const { bars, footprints } = flowScenario();
  const n = bars[41];
  bars[41] = bar(n.time, n.open, n.high, 120, n.close);
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
  assert.ok(!fib.reads.slice(31).some((r) => r.checks.location.state === 'fail'), '0.886 never closed past');
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
  assert.equal(reads[0].gamma.regime, 'negative');
  assert.equal(reads[trades[0].confirmIndex].gamma.regime, 'positive');
  // without snapshots nothing changes: no gamma on reads, null on trades
  const plain = run(bars, { entry: 'close' });
  assert.equal('gamma' in plain.reads[0], false);
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
