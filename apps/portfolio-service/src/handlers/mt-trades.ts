import mongoose from 'mongoose';

import { requireAuth } from '../lib/auth.js';
import { json } from '../lib/http.js';
import { config } from '../lib/config.js';

async function connect() {
  if (!config.mongodbUri) throw new Error('MONGODB_URI is not set');
  if (mongoose.connection.readyState === 1) return;
  await mongoose.connect(config.mongodbUri, { serverSelectionTimeoutMS: 3000 });
}

/** Paper momentum trades and their immutable post-trade candle snapshots. */
export const handler = requireAuth(async (event) => {
  await connect();
  const db = mongoose.connection.db!;
  const requested = Number(event.queryStringParameters?.limit ?? 200);
  const limit = Number.isFinite(requested) ? Math.min(Math.max(Math.floor(requested), 1), 500) : 200;
  const status = event.queryStringParameters?.status;
  const filter = status === 'open' || status === 'closed' ? { status } : {};

  const docs = await db
    .collection('mt_positions')
    .find(filter)
    .sort({ entry_time: -1 })
    .limit(limit)
    .toArray();

  return json(200, {
    trades: docs.map((doc) => ({ ...doc, _id: String(doc._id) })),
    count: docs.length,
  });
});
