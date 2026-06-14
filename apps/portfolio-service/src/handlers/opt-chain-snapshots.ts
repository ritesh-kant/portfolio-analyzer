import mongoose from 'mongoose';

import { requireAuth } from '../lib/auth.js';
import { json } from '../lib/http.js';
import { config } from '../lib/config.js';

async function connect() {
  if (!config.mongodbUri) throw new Error('MONGODB_URI is not set');
  if (mongoose.connection.readyState === 1) return;
  await mongoose.connect(config.mongodbUri, { serverSelectionTimeoutMS: 3000 });
}

export const handler = requireAuth(async (event) => {
  await connect();
  const db = mongoose.connection.db!;

  const symbol = event.queryStringParameters?.symbol;
  const signalId = event.queryStringParameters?.signal_id;
  const hours = Math.min(Number(event.queryStringParameters?.hours ?? 24), 168); // max 7 days

  const filter: Record<string, unknown> = {
    snapshot_at: { $gte: new Date(Date.now() - hours * 3_600_000) },
  };
  if (symbol) filter.symbol = symbol;
  if (signalId) filter.signal_id = signalId;

  const docs = await db
    .collection('opt_chain_snapshots')
    .find(filter)
    .sort({ snapshot_at: 1 })
    .limit(500)
    .toArray();

  return json(200, {
    snapshots: docs.map((d) => ({ ...d, _id: String(d._id) })),
    count: docs.length,
    hours,
  });
});
