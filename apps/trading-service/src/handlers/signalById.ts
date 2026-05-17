import { json } from '../lib/http.js';
import { requireAuth } from '../lib/auth.js';
import { getTradingDb } from '../lib/db.js';
import mongoose from 'mongoose';

const { ObjectId } = mongoose.Types;

export const handler = requireAuth(async (event): Promise<ReturnType<typeof json>> => {
  try {
    const id = event.pathParameters?.['id'];
    if (!id) return json(400, { error: 'id path parameter is required' });

    const db = await getTradingDb();
    const isObjectId = /^[a-f\d]{24}$/i.test(id);
    const signal = isObjectId
      ? await db.tradingSignals().findOne({ _id: new ObjectId(id) as unknown as never })
      : await db.tradingSignals().findOne({ run_id: id });

    if (!signal) return json(404, { error: 'Signal not found' });
    return json(200, signal);
  } catch (error) {
    return json(500, { error: 'Failed to fetch signal', detail: error instanceof Error ? error.message : String(error) });
  }
});
