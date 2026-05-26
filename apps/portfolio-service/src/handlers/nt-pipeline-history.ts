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

  const qs = (event as { queryStringParameters?: Record<string, string> })
    .queryStringParameters ?? {};
  const page = Math.max(1, parseInt(qs.page ?? '1', 10));
  const pageSize = 8;
  const skip = (page - 1) * pageSize;

  const [runs, total] = await Promise.all([
    db
      .collection('nt_pipeline_runs')
      .find({})
      .sort({ triggered_at: -1 })
      .skip(skip)
      .limit(pageSize)
      .toArray(),
    db.collection('nt_pipeline_runs').countDocuments({}),
  ]);

  return json(200, {
    runs: runs.map((r) => ({
      _id: String(r._id),
      triggered_at: r.triggered_at,
      completed_at: r.completed_at ?? null,
      source: r.source ?? 'unknown',
      status: r.status ?? 'completed',
      new_articles: r.new_articles ?? null,
      signals_created: r.signals_created ?? null,
      positions_opened: r.positions_opened ?? null,
      error: r.error ?? null,
    })),
    total,
    page,
    pages: Math.ceil(total / pageSize),
  });
});
