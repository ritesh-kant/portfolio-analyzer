import mongoose from 'mongoose';

import { requireAuth } from '../lib/auth.js';
import { json } from '../lib/http.js';
import { config } from '../lib/config.js';

async function connect() {
  if (!config.mongodbUri) throw new Error('MONGODB_URI is not set');
  if (mongoose.connection.readyState === 1) return;
  await mongoose.connect(config.mongodbUri, { serverSelectionTimeoutMS: 3000 });
}

/**
 * US momentum arm. Separate collections from `mt_*`, for the same reason
 * `us_universe.py` is separate from `universe.py`: the two arms run different
 * screens under different market structure, and pooling them would make any
 * combined number meaningless. `mt_us_positions` stores USD, not rupees.
 */
const POSITIONS = 'mt_us_positions';
const WATCHLIST = 'mt_us_watchlist';

/** Paper US trades and their post-trade candle snapshots. */
export const handler = requireAuth(async (event) => {
  await connect();
  const db = mongoose.connection.db!;
  const requested = Number(event.queryStringParameters?.limit ?? 200);
  const limit = Number.isFinite(requested) ? Math.min(Math.max(Math.floor(requested), 1), 500) : 200;
  const status = event.queryStringParameters?.status;
  const filter = status === 'open' || status === 'closed' ? { status } : {};

  const docs = await db
    .collection(POSITIONS)
    .find(filter)
    .sort({ entry_time: -1 })
    .limit(limit)
    .toArray();

  return json(200, {
    trades: docs.map((doc) => ({ ...doc, _id: String(doc._id) })),
    count: docs.length,
  });
});

/**
 * The screen funnel for recent sessions.
 *
 * Deliberately returns rejections as well as survivors. An empty watchlist is
 * ambiguous — a quiet market and a broken input look identical — and the NSE
 * arm spent months with its supply criterion silently disabled because nothing
 * ever surfaced the difference. `missing_criteria` names any of the five that
 * did not run, so the page can say so rather than implying a full screen.
 */
export const watchlist = requireAuth(async (event) => {
  await connect();
  const db = mongoose.connection.db!;
  const requested = Number(event.queryStringParameters?.days ?? 5);
  const days = Number.isFinite(requested) ? Math.min(Math.max(Math.floor(requested), 1), 30) : 5;

  const docs = await db
    .collection(WATCHLIST)
    .find({})
    .sort({ date: -1 })
    .limit(days)
    .toArray();

  return json(200, {
    sessions: docs.map((doc) => ({ ...doc, _id: String(doc._id) })),
    count: docs.length,
  });
});
