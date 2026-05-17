import { json } from '../lib/http.js';
import { requireAuth } from '../lib/auth.js';
import { getTradingDb } from '../lib/db.js';

export const handler = requireAuth(async (event): Promise<ReturnType<typeof json>> => {
  try {
    const db = await getTradingDb();
    const { direction, date, run_id, limit = '50', offset = '0' } = event.queryStringParameters ?? {};

    const filter: Record<string, unknown> = {};
    if (direction) filter['direction'] = direction;
    if (date) filter['date'] = date;
    if (run_id) filter['run_id'] = run_id;

    const signals = await db
      .tradingSignals()
      .find(filter)
      .sort({ createdAt: -1 })
      .skip(parseInt(offset, 10))
      .limit(parseInt(limit, 10))
      .toArray();

    return json(200, { signals, count: signals.length });
  } catch (error) {
    return json(500, { error: 'Failed to fetch signals', detail: error instanceof Error ? error.message : String(error) });
  }
});
