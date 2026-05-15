import { json } from '../lib/http.js';
import { getTradingDb } from '../lib/db.js';

export async function handler(): Promise<ReturnType<typeof json>> {
  try {
    const db = await getTradingDb();
    const portfolio = await db.virtualPortfolio().findOne({ portfolio_id: 'main' });
    return json(200, portfolio ?? null);
  } catch (error) {
    return json(500, { error: 'Failed to fetch portfolio', detail: error instanceof Error ? error.message : String(error) });
  }
}
