import mongoose from 'mongoose';

import { requireAuth } from '../lib/auth.js';
import { json } from '../lib/http.js';
import { config } from '../lib/config.js';

async function connect() {
  if (!config.mongodbUri) throw new Error('MONGODB_URI is not set');
  if (mongoose.connection.readyState === 1) return;
  await mongoose.connect(config.mongodbUri, { serverSelectionTimeoutMS: 3000 });
}

const LIVE = 'live';
const LIVE_COLLECTION = 'mt_positions';
const BACKTEST_COLLECTION = 'mt_backtest_trades';
const RUNS_COLLECTION = 'mt_backtest_runs';

/**
 * Only the fields the analytics dashboard actually groups or measures by.
 *
 * `mt_positions` also carries a full day of one-minute bars and the entry
 * evidence tree per trade, which the review page needs and the dashboard never
 * reads — projecting them away keeps a few thousand trades inside a single
 * response instead of tens of megabytes.
 */
const ANALYSIS_FIELDS = {
  symbol: 1,
  setup: 1,
  strategy: 1,
  status: 1,
  entry_time: 1,
  exit_time: 1,
  entry_price: 1,
  exit_price: 1,
  stop: 1,
  target: 1,
  qty: 1,
  notional_inr: 1,
  risk_inr: 1,
  gross_inr: 1,
  costs_inr: 1,
  net_inr: 1,
  stress_inr: 1,
  exit_reason: 1,
  day_chg_pct: 1,
  rvol: 1,
  atr_pct: 1,
  macd_hist: 1,
  pullback_ord: 1,
  resist_head_pct: 1,
  support_drop_pct: 1,
  round_head_pct: 1,
  catalyst: 1,
  event_type: 1,
  candle_tags: 1,
  quality_reason: 1,
  prev_day_gainer: 1,
} as const;

/** Every trade set the dashboard can be pointed at, live one first. */
export const sources = requireAuth(async () => {
  await connect();
  const db = mongoose.connection.db!;

  const live = await db
    .collection(LIVE_COLLECTION)
    .aggregate([
      { $match: { status: 'closed' } },
      { $group: { _id: null, trades: { $sum: 1 }, from: { $min: '$entry_time' }, to: { $max: '$entry_time' } } },
    ])
    .toArray();

  const runs = await db.collection(RUNS_COLLECTION).find().sort({ from: -1 }).toArray();

  return json(200, {
    sources: [
      {
        id: LIVE,
        kind: 'live' as const,
        label: 'Live paper trades',
        trades: live[0]?.trades ?? 0,
        from: live[0]?.from ?? null,
        to: live[0]?.to ?? null,
        // Live paper costs are the real MIS schedule with no stress added, so
        // there is nothing for the cost-model switch to back out.
        stressSlip: 0,
      },
      ...runs.map((run) => ({
        id: String(run._id),
        kind: 'backtest' as const,
        label: String(run.label ?? run._id),
        trades: Number(run.trades ?? 0),
        from: run.from ?? null,
        to: run.to ?? null,
        sourceFile: run.source_file ?? null,
        importedAt: run.imported_at ?? null,
        stressSlip: Number(run.stress_slip ?? 0),
      })),
    ],
  });
});

/** Closed trades from one source, slimmed to the analysis fields. */
export const handler = requireAuth(async (event) => {
  await connect();
  const db = mongoose.connection.db!;
  const source = event.queryStringParameters?.source ?? LIVE;

  const [collection, filter] =
    source === LIVE
      ? [LIVE_COLLECTION, { status: 'closed' }]
      : [BACKTEST_COLLECTION, { run_tag: source, status: 'closed' }];

  const docs = await db
    .collection(collection)
    .find(filter, { projection: ANALYSIS_FIELDS })
    .sort({ entry_time: 1 })
    .limit(20000)
    .toArray();

  return json(200, {
    source,
    kind: source === LIVE ? 'live' : 'backtest',
    count: docs.length,
    trades: docs.map((doc) => ({ ...doc, _id: String(doc._id) })),
  });
});
