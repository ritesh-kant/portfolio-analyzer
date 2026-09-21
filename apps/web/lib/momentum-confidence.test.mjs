import assert from 'node:assert/strict';
import { test } from 'node:test';
import { calculateTradeConfidence } from './momentum-confidence.ts';

const baseTrade = {
  _id: 'trade-1',
  symbol: 'TEST',
  status: 'closed',
  setup: 'attention_1m_confirmation',
  time: '2026-09-21T04:45:00.000Z',
  entry_time: '2026-09-21T04:46:00.000Z',
  entry_price: 100,
  stop: 98,
  target: 104,
  qty: 10,
  day_chg_pct: 8,
  rvol: 5,
  pullback_ord: 1,
  macd_hist: 0.25,
  resist_head_pct: 2,
  support_drop_pct: 0.5,
  pattern_matches: [
    {
      name: 'bull_flag',
      timeframe: '1m',
      start: 'x',
      end: 'y',
      confirmation: 1,
      invalidation: 0,
      strength: 1.5,
    },
  ],
  entry_evidence: {
    pattern_rules_version: 'v1',
    trend: { timeframe: '5m', bar_start: 'x', close: 101, ema9: 100, ema20: 99, vwap: 100 },
    confirmation: {
      timeframe: '1m',
      bar_start: 'x',
      formed_at: 'y',
      open: 100,
      high: 102,
      low: 99,
      close: 101,
      volume: 100,
      close_position: 0.9,
      minimum_close_position: 0.6,
      volume_ratio: 5,
      minimum_volume_ratio: 2.5,
    },
  },
};

test('scores a fully recorded strong entry at 100 with complete coverage', () => {
  const confidence = calculateTradeConfidence(baseTrade);

  assert.equal(confidence.score, 100);
  assert.equal(confidence.band, 'strong');
  assert.equal(confidence.evidenceCoverage, 100);
  assert.equal(
    confidence.factors.every((item) => item.status === 'earned'),
    true,
  );
});

test('uses factor boundaries and preserves a low confidence result', () => {
  const confidence = calculateTradeConfidence({
    ...baseTrade,
    day_chg_pct: 1.5,
    rvol: 1.5,
    pullback_ord: 3,
    macd_hist: 0,
    resist_head_pct: 0.5,
    support_drop_pct: 3,
    pattern_matches: [{ ...baseTrade.pattern_matches[0], strength: 0.75 }],
    entry_evidence: {
      ...baseTrade.entry_evidence,
      trend: { ...baseTrade.entry_evidence.trend, ema9: 99, close: 99 },
      confirmation: {
        ...baseTrade.entry_evidence.confirmation,
        close_position: 0.6,
        volume_ratio: 2.5,
      },
    },
  });

  assert.equal(confidence.score, 28);
  assert.equal(confidence.band, 'developing');
  assert.equal(confidence.factors.find((item) => item.id === 'day-change')?.earned, 3);
  assert.equal(confidence.factors.find((item) => item.id === 'confirmation-volume')?.earned, 6);
});

test('normalizes legacy rows against only their recorded evidence', () => {
  const confidence = calculateTradeConfidence({
    ...baseTrade,
    entry_evidence: undefined,
    pattern_matches: undefined,
    pullback_ord: undefined,
    macd_hist: undefined,
    resist_head_pct: undefined,
    support_drop_pct: undefined,
  });

  assert.equal(confidence.score, 100);
  assert.equal(confidence.evidenceCoverage, 31);
  assert.equal(
    confidence.factors.find((item) => item.id === 'trend-alignment')?.status,
    'not_recorded',
  );
});

test('does not use realized P&L or exit data', () => {
  const winning = calculateTradeConfidence({
    ...baseTrade,
    net_inr: 10_000,
    exit_reason: 'target',
  });
  const losing = calculateTradeConfidence({ ...baseTrade, net_inr: -10_000, exit_reason: 'stop' });

  assert.deepEqual(winning, losing);
});

test('rejects malformed numeric inputs without producing an invalid score', () => {
  const confidence = calculateTradeConfidence({
    ...baseTrade,
    day_chg_pct: Number.NaN,
    rvol: Number.POSITIVE_INFINITY,
    entry_evidence: {
      ...baseTrade.entry_evidence,
      confirmation: { ...baseTrade.entry_evidence.confirmation, volume_ratio: Number.NaN },
    },
  });

  assert.equal(confidence.score !== null && confidence.score >= 0 && confidence.score <= 100, true);
  assert.equal(confidence.factors.find((item) => item.id === 'day-change')?.status, 'not_recorded');
  assert.equal(
    confidence.factors.find((item) => item.id === 'relative-volume')?.status,
    'not_recorded',
  );
});
