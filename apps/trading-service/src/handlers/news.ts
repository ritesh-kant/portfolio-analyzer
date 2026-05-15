import { json } from '../lib/http.js';
import { getTradingDb } from '../lib/db.js';

export async function handler(event: {
  queryStringParameters?: Record<string, string | undefined>;
}): Promise<ReturnType<typeof json>> {
  try {
    const db = await getTradingDb();
    const { sector, symbol, run_id, limit = '20' } = event.queryStringParameters ?? {};

    const filter: Record<string, unknown> = { is_market_relevant: true };
    if (sector) filter['affected_sectors'] = sector;
    if (symbol) filter['affected_stocks'] = symbol.toUpperCase();
    if (run_id) filter['run_id'] = run_id;

    const articles = await db
      .newsArticles()
      .find(filter)
      .sort({ createdAt: -1 })
      .limit(parseInt(limit, 10))
      .toArray();

    return json(200, { articles, count: articles.length });
  } catch (error) {
    return json(500, { error: 'Failed to fetch news', detail: error instanceof Error ? error.message : String(error) });
  }
}
