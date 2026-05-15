import { json } from '../lib/http.js';
import { getTradingDb } from '../lib/db.js';

export async function handler(event: {
  queryStringParameters?: Record<string, string | undefined>;
}): Promise<ReturnType<typeof json>> {
  try {
    const db = await getTradingDb();
    const { status, symbol } = event.queryStringParameters ?? {};

    const filter: Record<string, unknown> = {};
    if (status) filter['status'] = status;
    if (symbol) filter['symbol'] = symbol.toUpperCase();

    const orders = await db.paperOrders().find(filter).sort({ createdAt: -1 }).toArray();
    return json(200, { orders, count: orders.length });
  } catch (error) {
    return json(500, { error: 'Failed to fetch orders', detail: error instanceof Error ? error.message : String(error) });
  }
}
