import { json } from '../lib/http.js';
import { requireAuth } from '../lib/auth.js';
import { getTradingDb } from '../lib/db.js';

interface AccuracyBreakdown {
  total: number;
  correct: number;
  win_rate_pct: number;
  avg_return_pct: number;
  avg_win_pct: number;
  avg_loss_pct: number;
}

interface SignalAccuracy {
  fired_count: number;
  win_rate_pct: number;
  avg_return_pct: number;
  lift_pp: number; // win rate when fired minus overall win rate
}

type ClosedSignal = {
  was_correct: boolean;
  actual_return_pct: number;
  prompt_version?: string;
  symbol: string;
  date: string;
  confidence: number;
  rsi?: number;
  macd_hist?: number;
  above_ema20?: boolean;
  above_ema50?: boolean;
  volume_ratio?: number;
};

function breakdown(signals: Array<{ was_correct: boolean; actual_return_pct: number }>): AccuracyBreakdown {
  if (!signals.length) {
    return { total: 0, correct: 0, win_rate_pct: 0, avg_return_pct: 0, avg_win_pct: 0, avg_loss_pct: 0 };
  }
  const wins = signals.filter(s => s.was_correct);
  const losses = signals.filter(s => !s.was_correct);
  const avgReturn = signals.reduce((s, x) => s + x.actual_return_pct, 0) / signals.length;
  const avgWin = wins.length ? wins.reduce((s, x) => s + x.actual_return_pct, 0) / wins.length : 0;
  const avgLoss = losses.length ? losses.reduce((s, x) => s + x.actual_return_pct, 0) / losses.length : 0;
  return {
    total: signals.length,
    correct: wins.length,
    win_rate_pct: Math.round((wins.length / signals.length) * 10000) / 100,
    avg_return_pct: Math.round(avgReturn * 100) / 100,
    avg_win_pct: Math.round(avgWin * 100) / 100,
    avg_loss_pct: Math.round(avgLoss * 100) / 100,
  };
}

function signalAccuracy(
  signals: ClosedSignal[],
  predicate: (s: ClosedSignal) => boolean,
  overallWinRate: number,
): SignalAccuracy {
  const fired = signals.filter(predicate);
  if (!fired.length) return { fired_count: 0, win_rate_pct: 0, avg_return_pct: 0, lift_pp: 0 };
  const wins = fired.filter(s => s.was_correct);
  const winRate = Math.round((wins.length / fired.length) * 10000) / 100;
  const avgRet = Math.round(fired.reduce((a, s) => a + s.actual_return_pct, 0) / fired.length * 100) / 100;
  return {
    fired_count: fired.length,
    win_rate_pct: winRate,
    avg_return_pct: avgRet,
    lift_pp: Math.round((winRate - overallWinRate) * 100) / 100,
  };
}

export const handler = requireAuth(async (event): Promise<ReturnType<typeof json>> => {
  try {
    const db = await getTradingDb();
    const { from, to, prompt_version, symbol } = event.queryStringParameters ?? {};

    const filter: Record<string, unknown> = { was_correct: { $exists: true } };
    if (from || to) {
      const dateFilter: Record<string, string> = {};
      if (from) dateFilter['$gte'] = from;
      if (to) dateFilter['$lte'] = to;
      filter['date'] = dateFilter;
    }
    if (prompt_version) filter['prompt_version'] = prompt_version;
    if (symbol) filter['symbol'] = symbol;

    const closed = await db
      .tradingSignals()
      .find(filter, {
        projection: {
          was_correct: 1, actual_return_pct: 1, prompt_version: 1,
          symbol: 1, date: 1, confidence: 1,
          rsi: 1, macd_hist: 1, above_ema20: 1, above_ema50: 1, volume_ratio: 1,
        },
      })
      .toArray() as ClosedSignal[];

    // Overall breakdown
    const overall = breakdown(closed);
    const overallWinRate = overall.win_rate_pct;

    // Breakdown by prompt_version
    const byVersion: Record<string, AccuracyBreakdown> = {};
    const versions = [...new Set(closed.map(s => s.prompt_version ?? 'unknown'))];
    for (const v of versions) {
      const subset = closed.filter(s => (s.prompt_version ?? 'unknown') === v);
      byVersion[v] = breakdown(subset);
    }

    // Breakdown by confidence bucket
    const buckets: Record<string, AccuracyBreakdown> = {};
    const bucketDefs: Array<[string, number, number]> = [
      ['0-49', 0, 49], ['50-59', 50, 59], ['60-69', 60, 69],
      ['70-79', 70, 79], ['80+', 80, 100],
    ];
    for (const [label, lo, hi] of bucketDefs) {
      const subset = closed.filter(s => s.confidence >= lo && s.confidence <= hi);
      if (subset.length) buckets[label] = breakdown(subset);
    }

    // Per-signal accuracy (which individual signals correlate with correct calls)
    const bySignal: Record<string, SignalAccuracy> = {
      rsi_oversold:    signalAccuracy(closed, s => typeof s.rsi === 'number' && s.rsi < 40, overallWinRate),
      macd_positive:   signalAccuracy(closed, s => typeof s.macd_hist === 'number' && s.macd_hist > 0, overallWinRate),
      above_ema20:     signalAccuracy(closed, s => s.above_ema20 === true, overallWinRate),
      above_ema50:     signalAccuracy(closed, s => s.above_ema50 === true, overallWinRate),
      volume_elevated: signalAccuracy(closed, s => typeof s.volume_ratio === 'number' && s.volume_ratio > 1.5, overallWinRate),
    };

    return json(200, {
      overall,
      by_prompt_version: byVersion,
      by_confidence_bucket: buckets,
      by_signal: bySignal,
      sample_size: closed.length,
    });
  } catch (error) {
    return json(500, { error: 'Failed to compute accuracy', detail: error instanceof Error ? error.message : String(error) });
  }
});
