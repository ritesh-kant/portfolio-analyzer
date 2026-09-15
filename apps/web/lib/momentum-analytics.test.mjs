import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  DIMENSIONS,
  STRESS_SLIP,
  applyFilters,
  breakdown,
  byDay,
  costsUnder,
  equityCurve,
  holdMinutes,
  inDateRange,
  istParts,
  maxDrawdown,
  rDistribution,
  rMultiple,
  resolveRange,
  shiftDate,
  stopDistancePct,
  streaks,
  summarise,
  tradeDateSpan,
  tradingDayCount,
} from './momentum-analytics.ts';

/** A closed long, priced so every derived measure has a round value. */
function trade(overrides = {}) {
  const entry = overrides.entry_price ?? 100;
  const exit = overrides.exit_price ?? 102;
  const qty = overrides.qty ?? 100;
  return {
    _id: overrides._id ?? 'x',
    symbol: 'ACME',
    setup: 'attention_1m_confirmation',
    entry_time: '2026-09-11T04:00:00.000Z', // 09:30 IST, a Friday
    exit_time: '2026-09-11T04:20:00.000Z', // +20 minutes
    entry_price: entry,
    exit_price: exit,
    stop: 99,
    qty,
    notional_inr: entry * qty,
    gross_inr: (exit - entry) * qty,
    costs_inr: 50,
    net_inr: (exit - entry) * qty - 50,
    exit_reason: 'target_hit',
    ...overrides,
  };
}

test('IST parts come from the trading session, not UTC', () => {
  // 04:00Z is 09:30 the same day in IST; 19:00Z is 00:30 the NEXT day.
  assert.deepEqual(istParts('2026-09-11T04:00:00.000Z'), {
    date: '2026-09-11', weekday: 'Fri', hour: 9, minute: 30,
  });
  assert.equal(istParts('2026-09-11T19:00:00.000Z').date, '2026-09-12');
});

test('derived per-trade measures', () => {
  assert.equal(holdMinutes(trade()), 20);
  assert.equal(holdMinutes(trade({ exit_time: undefined })), undefined);
  assert.equal(stopDistancePct(trade()), 1);
  assert.equal(rMultiple(trade()), 2); // +2 on a 1 risk
  assert.equal(rMultiple(trade({ exit_price: 99.5 })), -0.5);
  assert.equal(rMultiple(trade({ stop: 100 })), undefined); // no risk = no R
});

test('the cost model converts between the live and backtest schedules', () => {
  // A backtest row: costs_inr already includes its stress component.
  const backtest = trade({ costs_inr: 850, stress_inr: 800 });
  assert.equal(costsUnder(backtest, 'recorded'), 850);
  assert.equal(costsUnder(backtest, 'real'), 50);
  assert.equal(costsUnder(backtest, 'stress'), 850); // already stressed, not doubled

  // A live row: real costs only, so stressing it must add the 40 bps/side.
  const live = trade({ costs_inr: 50 });
  const turnover = (100 + 102) * 100;
  assert.equal(costsUnder(live, 'recorded'), 50);
  assert.equal(costsUnder(live, 'real'), 50);
  assert.equal(costsUnder(live, 'stress'), 50 + turnover * STRESS_SLIP);
});

test('summarise separates the gross move from what costs did to it', () => {
  const stats = summarise([trade(), trade({ exit_price: 99, gross_inr: -100, net_inr: -150 })], 'recorded');
  assert.equal(stats.trades, 2);
  assert.equal(stats.gross, 100); // +200 then -100
  assert.equal(stats.costs, 100);
  assert.equal(stats.net, 0);
  assert.equal(stats.wins, 1);
  assert.equal(stats.losses, 1);
  assert.equal(stats.winRate, 0.5);
  assert.equal(stats.avgWin, 150);
  assert.equal(stats.avgLoss, 150);
  assert.equal(stats.profitFactor, 1);
  assert.equal(stats.avgHoldMinutes, 20);
});

test('a gross winner turned loser by costs counts as a loss, and is visible as one', () => {
  const stats = summarise([trade({ exit_price: 100.2, gross_inr: 20, net_inr: -30 })], 'recorded');
  assert.equal(stats.winRate, 0); // net
  assert.equal(stats.grossWinRate, 1); // the move itself was up
  assert.equal(stats.net, -30);
  assert.equal(stats.gross, 20);
});

test('per-trade percentages are measured against money put to work', () => {
  const stats = summarise([trade({ entry_price: 100, qty: 100, gross_inr: 200, costs_inr: 100 })], 'recorded');
  assert.equal(stats.turnover, 10_000);
  assert.equal(stats.grossPctPerTrade, 2);
  assert.equal(stats.netPctPerTrade, 1);
});

test('drawdown is the worst fall from a running peak, not the final loss', () => {
  const up = trade({ gross_inr: 1000, costs_inr: 0 });
  const down = trade({ gross_inr: -400, costs_inr: 0 });
  // +1000, -400, -400, +1000 ⇒ peak 1000, trough 200, final 1200.
  assert.equal(maxDrawdown([up, down, down, up], 'recorded'), 800);
});

test('streaks count consecutive net winners and losers', () => {
  const win = trade({ gross_inr: 100, costs_inr: 0 });
  const loss = trade({ gross_inr: -100, costs_inr: 0 });
  assert.deepEqual(streaks([win, win, win, loss, loss, win], 'recorded'), {
    longestWin: 3, longestLoss: 2,
  });
});

test('breakdowns bucket on the recorded field and count what was never recorded', () => {
  const dimension = DIMENSIONS.find((d) => d.id === 'rvol');
  const result = breakdown(
    [trade({ rvol: 1.5 }), trade({ rvol: 4 }), trade({ rvol: 4.5 }), trade({ rvol: undefined })],
    dimension,
    'recorded',
  );
  assert.equal(result.unrecorded, 1); // not silently bucketed as zero
  assert.deepEqual(
    result.buckets.map((b) => [b.key, b.stats.trades]),
    [['< 2×', 1], ['3×–5×', 2]],
  );
  // Fixed-order dimensions keep session order regardless of which buckets fill.
  assert.deepEqual(result.buckets.map((b) => b.key), ['< 2×', '3×–5×']);
});

test('time-of-day buckets follow the NSE session and cover the close', () => {
  const dimension = DIMENSIONS.find((d) => d.id === 'time');
  const at = (iso) => dimension.bucket(trade({ entry_time: iso }));
  assert.equal(at('2026-09-11T03:45:00.000Z'), '09:15'); // 09:15 IST, the open
  assert.equal(at('2026-09-11T04:14:00.000Z'), '09:15'); // 09:44 IST, same slot
  assert.equal(at('2026-09-11T04:15:00.000Z'), '09:45'); // 09:45 IST, next slot
  assert.equal(at('2026-09-11T09:44:00.000Z'), '14:45'); // 15:14 IST, inside 14:45–15:15
  assert.equal(at('2026-09-11T09:50:00.000Z'), '15:15'); // 15:20 IST, the closing slot
  assert.equal(at('2026-09-11T10:00:00.000Z'), '15:15'); // 15:30 IST, the close itself
});

test('a trade with several candle tags is counted under each of them', () => {
  const dimension = DIMENSIONS.find((d) => d.id === 'candles');
  const result = breakdown(
    [trade({ candle_tags: ['hammer', 'bullish_engulfing'] }), trade({ candle_tags: [] })],
    dimension,
    'recorded',
  );
  assert.deepEqual(
    result.buckets.map((b) => b.key).sort(),
    ['bullish engulfing', 'hammer', 'no pattern'],
  );
});

test('filters compose across dimensions', () => {
  const trades = [
    trade({ _id: 'a', symbol: 'ACME', rvol: 4 }),
    trade({ _id: 'b', symbol: 'ACME', rvol: 1 }),
    trade({ _id: 'c', symbol: 'OTHER', rvol: 4 }),
  ];
  const filtered = applyFilters(trades, [
    { dimensionId: 'symbol', key: 'ACME' },
    { dimensionId: 'rvol', key: '3×–5×' },
  ]);
  assert.deepEqual(filtered.map((t) => t._id), ['a']);
  assert.equal(applyFilters(trades, []).length, 3);
});

test('the equity curve and daily totals agree with the trade list', () => {
  const trades = [
    trade({ gross_inr: 100, costs_inr: 20 }),
    trade({ gross_inr: -50, costs_inr: 20, entry_time: '2026-09-14T04:00:00.000Z' }),
  ];
  const curve = equityCurve(trades, 'recorded');
  assert.deepEqual(curve.map((p) => p.net), [80, 10]);
  assert.deepEqual(curve.map((p) => p.gross), [100, 50]);
  assert.deepEqual(byDay(trades, 'recorded'), [
    { date: '2026-09-11', trades: 1, net: 80, gross: 100 },
    { date: '2026-09-14', trades: 1, net: -70, gross: -50 },
  ]);
});

test('the R histogram bins by risk taken and ignores trades with no risk', () => {
  const bins = rDistribution([
    trade({ exit_price: 102 }), // +2R
    trade({ exit_price: 98 }), // -2R
    trade({ stop: 100 }), // no measurable risk
  ]);
  const filled = bins.filter((b) => b.count > 0);
  assert.deepEqual(filled.map((b) => [b.label, b.count]), [
    ['-2 to -1.5R', 1],
    ['2 to 3R', 1],
  ]);
  assert.equal(bins.reduce((sum, b) => sum + b.count, 0), 2);
});

test('every dimension is uniquely identified and safe on a bare trade', () => {
  assert.equal(new Set(DIMENSIONS.map((d) => d.id)).size, DIMENSIONS.length);
  const bare = { _id: 'b', symbol: 'ACME', entry_time: '2026-09-11T04:00:00.000Z', entry_price: 100 };
  for (const dimension of DIMENSIONS) {
    assert.doesNotThrow(() => dimension.bucket(bare), `${dimension.id} threw on a bare trade`);
  }
});

// ── date range ───────────────────────────────────────────────────────────────

/** A trade on a given IST date, at 09:30 IST (04:00 UTC). */
const onDate = (date, id = date) => trade({ _id: id, entry_time: `${date}T04:00:00.000Z` });

test('shifting a date back clamps the day into the target month', () => {
  assert.equal(shiftDate('2024-03-31', { months: 1 }), '2024-02-29'); // leap year, not Mar 2
  assert.equal(shiftDate('2023-03-31', { months: 1 }), '2023-02-28');
  assert.equal(shiftDate('2024-05-31', { months: 3 }), '2024-02-29');
  assert.equal(shiftDate('2024-01-15', { months: 1 }), '2023-12-15'); // crosses the year
  assert.equal(shiftDate('2024-03-01', { days: 1 }), '2024-02-29');
  assert.equal(shiftDate('2024-01-01', { days: 1 }), '2023-12-31');
  assert.equal(shiftDate('2024-06-10', { days: 6 }), '2024-06-04');
});

test('the span of a trade list is measured in IST dates', () => {
  assert.deepEqual(tradeDateSpan([onDate('2024-06-10'), onDate('2024-06-03'), onDate('2024-06-07')]), {
    from: '2024-06-03', to: '2024-06-10',
  });
  assert.equal(tradeDateSpan([]), null);
});

test('presets anchor to the last trade in the set, not to today', () => {
  // Deliberately historical: anchoring to the clock would return nothing.
  const trades = ['2022-05-02', '2022-05-30', '2022-06-01', '2022-06-08', '2022-06-10'].map((d) => onDate(d));
  assert.deepEqual(resolveRange(trades, 'all'), { from: '2022-05-02', to: '2022-06-10' });
  assert.deepEqual(resolveRange(trades, '1d'), { from: '2022-06-10', to: '2022-06-10' });
  assert.deepEqual(resolveRange(trades, '3d'), { from: '2022-06-08', to: '2022-06-10' });
  assert.deepEqual(resolveRange(trades, '7d'), { from: '2022-06-04', to: '2022-06-10' });
  assert.deepEqual(resolveRange(trades, '1m'), { from: '2022-05-10', to: '2022-06-10' });
  assert.equal(resolveRange([], '7d'), null);
});

test('a preset reaching past the first trade is clipped to the data', () => {
  const trades = [onDate('2024-06-01'), onDate('2024-06-10')];
  // Three months back is well before the set begins, so the set's own start wins.
  assert.deepEqual(resolveRange(trades, '3m'), { from: '2024-06-01', to: '2024-06-10' });
});

test('a custom range falls back to the data edges and tolerates reversed dates', () => {
  const trades = [onDate('2024-06-01'), onDate('2024-06-10')];
  assert.deepEqual(resolveRange(trades, 'custom', { from: '2024-06-05' }), {
    from: '2024-06-05', to: '2024-06-10',
  });
  assert.deepEqual(resolveRange(trades, 'custom', { to: '2024-06-05' }), {
    from: '2024-06-01', to: '2024-06-05',
  });
  assert.deepEqual(resolveRange(trades, 'custom', {}), { from: '2024-06-01', to: '2024-06-10' });
  // Entered backwards by hand — read as the range the user meant.
  assert.deepEqual(resolveRange(trades, 'custom', { from: '2024-06-08', to: '2024-06-02' }), {
    from: '2024-06-02', to: '2024-06-08',
  });
});

test('date filtering is inclusive at both ends and counts trading days', () => {
  const trades = [onDate('2024-06-01', 'a'), onDate('2024-06-05', 'b'), onDate('2024-06-05', 'c'),
                  onDate('2024-06-10', 'd')];
  const kept = inDateRange(trades, { from: '2024-06-01', to: '2024-06-05' });
  assert.deepEqual(kept.map((t) => t._id), ['a', 'b', 'c']);
  assert.equal(tradingDayCount(kept), 2); // three trades, two days
  assert.equal(inDateRange(trades, null).length, 4);
});

test('a late-evening UTC timestamp belongs to the next IST day for ranges too', () => {
  // 19:00Z on the 4th is 00:30 IST on the 5th — a range ending on the 4th must not keep it.
  const late = trade({ _id: 'late', entry_time: '2024-06-04T19:00:00.000Z' });
  assert.equal(inDateRange([late], { from: '2024-06-01', to: '2024-06-04' }).length, 0);
  assert.equal(inDateRange([late], { from: '2024-06-05', to: '2024-06-05' }).length, 1);
});
