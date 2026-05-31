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
  const runId = event.pathParameters?.id;
  if (!runId) return json(400, { error: 'missing run id' });

  await connect();
  const db = mongoose.connection.db!;
  const docs = await db
    .collection('nt_positions')
    .find({ pipeline_run_id: runId })
    .sort({ entry_at: -1 })
    .toArray();

  return json(200, {
    positions: docs.map((d) => ({ ...d, _id: String(d._id) })),
    count: docs.length,
  });
});
