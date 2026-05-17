import { json } from '../lib/http.js';
import { requireAuth } from '../lib/auth.js';
import { getTradingDb } from '../lib/db.js';

export const handler = requireAuth(async (event): Promise<ReturnType<typeof json>> => {
  try {
    const db = await getTradingDb();
    const { limit = '10' } = event.queryStringParameters ?? {};

    const runs = await db
      .pipelineRuns()
      .find({})
      .sort({ started_at: -1 })
      .limit(parseInt(limit, 10))
      .toArray();

    return json(200, { runs, count: runs.length });
  } catch (error) {
    return json(500, { error: 'Failed to fetch runs', detail: error instanceof Error ? error.message : String(error) });
  }
});
