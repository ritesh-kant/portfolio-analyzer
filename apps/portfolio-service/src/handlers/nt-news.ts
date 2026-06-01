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
  const limit = Math.min(Number(event.queryStringParameters?.limit ?? 20), 100);
  await connect();
  const db = mongoose.connection.db!;
  const col = db.collection('nt_news_raw');
  const [docs, total] = await Promise.all([
    col.find({}).sort({ ingested_at: -1 }).limit(limit).toArray(),
    col.countDocuments(),
  ]);
  return json(200, {
    articles: docs.map((d) => ({ ...d, _id: String(d._id) })),
    count: total,
  });
});
