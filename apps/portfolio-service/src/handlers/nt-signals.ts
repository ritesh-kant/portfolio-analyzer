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
  const limit = Math.min(Number(event.queryStringParameters?.limit ?? 50), 100);
  await connect();
  const db = mongoose.connection.db!;
  const col = db.collection('nt_signals');

  // Optional rolling window (?days=N): the funnel counts must reflect the window,
  // not just the loaded page — ~400 signals/day means client-side counting would
  // badly undercount. So filter both the docs query and the counts server-side.
  const days = Number(event.queryStringParameters?.days);
  const dateFilter: Record<string, unknown> =
    Number.isFinite(days) && days > 0
      ? { created_at: { $gte: new Date(Date.now() - days * 86_400_000) } }
      : {};

  const [docs, total, gatePassed] = await Promise.all([
    col.find(dateFilter).sort({ created_at: -1 }).limit(limit).toArray(),
    col.countDocuments(dateFilter),
    col.countDocuments({ ...dateFilter, gate_result: 'ok' }),
  ]);
  return json(200, {
    signals: docs.map((d) => ({ ...d, _id: String(d._id) })),
    count: total,
    gate_passed_count: gatePassed,
  });
});
