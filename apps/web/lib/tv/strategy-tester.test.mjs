import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  COST_DEFAULTS,
  costPoints,
  groupSkips,
  instrumentFor,
  median,
  roundTripCost,
  testerStats,
  tradeOverlap,
  tradeResults,
} from './strategy-tester.ts';

const close = (a, b, msg) => assert.ok(Math.abs(a - b) < 1e-9, `${msg ?? ''} ${a} ≠ ${b}`);
const trade = (id, dir, entry, stop, exit, outcome, exitIndex = id * 10 + 5) => ({
  id,
  dir,
  entryIndex: id * 10,
  entry,
  stop,
  target: 0,
  exitIndex,
  exit,
  outcome,
});

test('instrument from the chart symbol', () => {
  assert.equal(instrumentFor('NQ=F').id, 'NQ');
  assert.equal(instrumentFor('mnq=f').id, 'MNQ');
  assert.equal(instrumentFor('GC=F').pointValue, 100);
  assert.equal(instrumentFor('RELIANCE.NS').id, 'custom');
});

test('NQ round trip = 1 tick slippage each side + commission each side', () => {
  // 2 × 0.25 + 2 × 2.25 / 20 = 0.5 + 0.225
  close(costPoints(COST_DEFAULTS), 0.725);
  close(costPoints({ ...COST_DEFAULTS, slipTicks: 0, commission: 0 }), 0);
  close(costPoints({ instrument: 'MNQ', contracts: 3, commission: null, slipTicks: 2 }), 1 + 1.24 / 2);
});

test('long and short results, in points, money and R, after costs; open trades are left out', () => {
  const cfg = { ...COST_DEFAULTS, contracts: 2 };
  const rs = tradeResults(
    [
      trade(1, 'long', 100, 90, 120, 'target'),
      trade(2, 'short', 100, 110, 110, 'stop'),
      { ...trade(3, 'long', 100, 90, null, 'open'), exitIndex: null },
    ],
    cfg,
  );
  assert.equal(rs.length, 2);
  close(rs[0].grossPts, 20);
  close(rs[0].netPts, 20 - 0.725);
  close(rs[0].gross, 20 * 20 * 2);
  close(rs[0].net, (20 - 0.725) * 40);
  close(rs[0].cost, 0.725 * 40);
  close(rs[0].netR, (20 - 0.725) / 10);
  close(rs[1].grossPts, -10);
  close(rs[1].netR, (-10 - 0.725) / 10);
});

test('stats: win rate, median beside mean, profit factor, drawdown in exit order, without the top 3', () => {
  const free = { ...COST_DEFAULTS, instrument: 'custom', slipTicks: 0 };
  // nets in exit order: +10, −5, −5, +30, −10, +1
  const ts = [
    trade(1, 'long', 100, 95, 110, 'target'),
    trade(2, 'long', 100, 95, 95, 'stop'),
    trade(3, 'short', 100, 105, 105, 'stop'),
    trade(4, 'short', 100, 110, 70, 'target'),
    trade(5, 'long', 100, 90, 90, 'stop'),
    trade(6, 'long', 100, 90, 101, 'time'),
  ];
  const s = testerStats(tradeResults(ts, free));
  assert.equal(s.trades, 6);
  assert.equal(s.wins, 3);
  close(s.winRate, 0.5);
  close(s.net, 21);
  close(s.avgNet, 3.5);
  close(s.medianNet, (-5 + 1) / 2);
  close(s.profitFactor, 41 / 20);
  close(s.maxDrawdown, 10); // 10 → 0 after two −5s; 30 → 20 after −10
  close(s.netWithoutTop3, 21 - 41);
  assert.deepEqual(s.equity, [10, 5, 0, 30, 20, 21]);
  assert.equal(s.byOutcome.stop.n, 3);
  close(s.byOutcome.target.net, 40);
  assert.equal(s.byDir.short.n, 2);
  close(s.byDir.long.net, -4);
});

test('stats are ordered by exit, not by the order trades arrive', () => {
  const free = { ...COST_DEFAULTS, instrument: 'custom', slipTicks: 0 };
  const a = trade(1, 'long', 100, 90, 90, 'stop', 50);
  const b = trade(2, 'long', 100, 90, 120, 'target', 30);
  assert.deepEqual(testerStats(tradeResults([a, b], free)).equity, [20, 10]);
});

test('empty and all-winning runs', () => {
  const none = testerStats([]);
  assert.equal(none.trades, 0);
  assert.equal(none.profitFactor, 0);
  assert.equal(none.medianNet, 0);
  const free = { ...COST_DEFAULTS, instrument: 'custom', slipTicks: 0 };
  assert.equal(testerStats(tradeResults([trade(1, 'long', 1, 0, 2, 'target')], free)).profitFactor, Infinity);
  assert.equal(median([3, 1, 2]), 2);
});

test('overlap of two runs by direction and fill candle', () => {
  const a = [trade(1, 'long', 1, 0, 2, 'target'), trade(2, 'short', 1, 2, 0, 'target')];
  const b = [trade(2, 'short', 1, 2, 0, 'target'), trade(3, 'long', 1, 0, 2, 'target')];
  const o = tradeOverlap(a, b);
  assert.equal(o.both, 1);
  assert.deepEqual(
    o.onlyA.map((t) => t.id),
    [1],
  );
  assert.deepEqual(
    o.onlyB.map((t) => t.id),
    [3],
  );
});

test('skip reasons group without their numbers, times kept', () => {
  const g = groupSkips([
    { step: 'entry', reason: 'reward ÷ risk 1.20 below 1.5' },
    { step: 'entry', reason: 'reward ÷ risk 0.80 below 1.5' },
    { step: 'confirmation', reason: 'confirmation outside 09:30–11:00 New York time' },
    { step: 'environment', reason: '1h trend up; choppy' },
  ]);
  assert.deepEqual(g[0], { step: 'entry', reason: 'reward ÷ risk … below …', n: 2 });
  assert.equal(g[1].reason, 'confirmation outside 09:30–11:00 New York time');
  assert.equal(g[2].reason, '1h trend up; choppy');
});

test('NSE futures: lot and tick from the contract, ₹20 a order, STT on the sell side, stamp on the buy side, GST on fees', () => {
  assert.equal(instrumentFor('NIFTY1!.NFO').id, 'NSEFUT');
  assert.equal(instrumentFor('BANKNIFTY26NOVFUT.NFO').id, 'NSEFUT');
  const cfg = { ...COST_DEFAULTS, instrument: 'NSEFUT' };
  const nifty = { lotSize: 65, tickSize: 0.1 };
  const c = roundTripCost(cfg, 'long', 25000, 25100, nifty);
  close(c.slippage, 13); // 2 sides × 1 tick × ₹0.10 × 65
  close(c.commission, 40); // ₹20 an order × 2, whatever the lots
  close(c.stt, 0.0005 * 25100 * 65);
  close(c.stamp, 0.00002 * 25000 * 65);
  close(c.exchange, 0.0000183 * (25000 + 25100) * 65);
  close(c.sebi, 0.000001 * (25000 + 25100) * 65);
  close(c.gst, 0.18 * (40 + c.exchange + c.sebi));
  close(c.total, c.slippage + c.commission + c.stt + c.exchange + c.sebi + c.stamp + c.gst);
  // a short sells first: the same two legs, so the same cost
  close(roundTripCost(cfg, 'short', 25100, 25000, nifty).total, c.total);
  // 3 lots: value-based costs ×3, brokerage still ₹20 an order
  const three = roundTripCost({ ...cfg, contracts: 3 }, 'long', 25000, 25100, nifty);
  close(three.commission, 40);
  close(three.stt, 3 * c.stt);
  // a hand-set lot size wins over the contract's
  close(roundTripCost({ ...cfg, lot: 75 }, 'long', 25000, 25100, nifty).stt, 0.0005 * 25100 * 75);
  // and the trade result is in rupees for the whole lot
  const [r] = tradeResults([{ id: 1, dir: 'long', entryIndex: 0, entry: 25000, stop: 24950, target: 25100, exitIndex: 3, exit: 25100, outcome: 'target' }], cfg, nifty);
  close(r.gross, 100 * 65);
  close(r.net, 100 * 65 - c.total);
  close(r.netPts, 100 - c.total / 65);
});
