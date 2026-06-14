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
  const status = event.queryStringParameters?.status ?? 'open';
  await connect();
  const db = mongoose.connection.db!;

  const days = Number(event.queryStringParameters?.days);
  const cutoff =
    Number.isFinite(days) && days > 0 ? new Date(Date.now() - days * 86_400_000) : null;
  const closedFilter = cutoff
    ? { status: 'closed', exit_at: { $gte: cutoff } }
    : { status: 'closed' };

  let filter: Record<string, unknown>;
  if (status === 'open') filter = { status: 'open' };
  else if (status === 'closed') filter = closedFilter;
  else filter = cutoff ? { $or: [{ status: 'open' }, closedFilter] } : {};

  const docs = await db
    .collection('opt_paper_positions')
    .find(filter)
    .sort({ entry_at: -1 })
    .limit(100)
    .toArray();

  return json(200, {
    positions: docs.map((d) => ({ ...d, _id: String(d._id) })),
    count: docs.length,
  });
});
