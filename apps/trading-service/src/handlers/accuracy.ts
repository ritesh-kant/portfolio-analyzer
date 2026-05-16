import { json } from '../lib/http.js';
import { getTradingDb } from '../lib/db.js';

interface AccuracyBreakdown {
  total: number;
  correct: number;
  win_rate_pct: number;
  avg_return_pct: number;
  avg_win_pct: number;
  avg_loss_pct: number;
}

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

export async function handler(event: {
  queryStringParameters?: Record<string, string | undefined>;
}): Promise<ReturnType<typeof json>> {
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
      .find(filter, { projection: { was_correct: 1, actual_return_pct: 1, prompt_version: 1, symbol: 1, date: 1, confidence: 1 } })
      .toArray() as Array<{ was_correct: boolean; actual_return_pct: number; prompt_version?: string; symbol: string; date: string; confidence: number }>;

    // Overall breakdown
    const overall = breakdown(closed as Array<{ was_correct: boolean; actual_return_pct: number }>);

    // Breakdown by prompt_version
    const byVersion: Record<string, AccuracyBreakdown> = {};
    const versions = [...new Set(closed.map(s => s.prompt_version ?? 'unknown'))];
    for (const v of versions) {
      const subset = closed.filter(s => (s.prompt_version ?? 'unknown') === v);
      byVersion[v] = breakdown(subset as Array<{ was_correct: boolean; actual_return_pct: number }>);
    }

    // Breakdown by confidence bucket (0-49, 50-59, 60-69, 70-79, 80+)
    const buckets: Record<string, AccuracyBreakdown> = {};
    const bucketDefs: Array<[string, number, number]> = [
      ['0-49', 0, 49], ['50-59', 50, 59], ['60-69', 60, 69],
      ['70-79', 70, 79], ['80+', 80, 100],
    ];
    for (const [label, lo, hi] of bucketDefs) {
      const subset = closed.filter(s => s.confidence >= lo && s.confidence <= hi);
      if (subset.length) {
        buckets[label] = breakdown(subset as Array<{ was_correct: boolean; actual_return_pct: number }>);
      }
    }

    return json(200, { overall, by_prompt_version: byVersion, by_confidence_bucket: buckets, sample_size: closed.length });
  } catch (error) {
    return json(500, { error: 'Failed to compute accuracy', detail: error instanceof Error ? error.message : String(error) });
  }
}
