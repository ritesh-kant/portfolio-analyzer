import mongoose from 'mongoose';

import { requireAuth } from '../lib/auth.js';
import { json } from '../lib/http.js';
import { config } from '../lib/config.js';

async function connect() {
  if (!config.mongodbUri) throw new Error('MONGODB_URI is not set');
  if (mongoose.connection.readyState === 1) return;
  await mongoose.connect(config.mongodbUri, { serverSelectionTimeoutMS: 3000 });
}

const DELAY_MS = parseInt(process.env.NT_NEWS_DELAY_SECONDS ?? '900', 10) * 1000; // 15-min SQS delay
const RUN_TTL_MS = DELAY_MS + 7 * 60 * 1000; // stale after delay + 7 min buffer

export const handler = requireAuth(async () => {
  await connect();
  const db = mongoose.connection.db!;

  const run = await db
    .collection('nt_pipeline_runs')
    .findOne({}, { sort: { triggered_at: -1 } });

  if (!run) return json(200, { run: null, is_active: false });

  const triggeredAt = new Date(run.triggered_at as Date);
  const now = new Date();
  const elapsedMs = now.getTime() - triggeredAt.getTime();

  // Count new docs in each collection since this run was triggered
  const [newArticles, newSignals, newPositions] = await Promise.all([
    db.collection('nt_news_raw').countDocuments({ ingested_at: { $gte: triggeredAt } }),
    db.collection('nt_signals').countDocuments({ created_at: { $gte: triggeredAt } }),
    db.collection('nt_positions').countDocuments({
      entry_at: { $gte: new Date(triggeredAt.getTime() + DELAY_MS) },
    }),
  ]);

  const delayWindowEndMs = triggeredAt.getTime() + DELAY_MS;
  const delayRemainMs = Math.max(0, delayWindowEndMs - now.getTime());

  // Derive stage statuses ─────────────────────────────────────────────────────
  // Ingester: done if articles appeared, or 3 min elapsed (may have found no new news)
  const ingesterDone = newArticles > 0 || elapsedMs > 3 * 60 * 1000;
  const ingesterStatus = ingesterDone ? 'done' : 'running';

  // Classifier: done if signals appeared, or 6 min elapsed since trigger
  const classifierDone = newSignals > 0 || elapsedMs > 6 * 60 * 1000;
  const classifierStatus = !ingesterDone
    ? 'pending'
    : classifierDone
      ? 'done'
      : 'running';

  // If classifier finished with 0 signals, the SQS queue is empty — skip delay + trade
  const nothingToTrade = classifierDone && newSignals === 0;

  // SQS delay: waiting with countdown, done once window passes (or skipped if no signals)
  const delayStatus = !classifierDone
    ? 'pending'
    : nothingToTrade || delayRemainMs === 0
      ? 'done'
      : 'waiting';

  // Trade decision: done if positions appeared, or skipped if no signals ever queued
  const tradeDone = newPositions > 0 || nothingToTrade;
  const tradeStatus =
    delayStatus !== 'done' ? 'pending' : tradeDone ? 'done' : 'running';

  const isActive =
    elapsedMs < RUN_TTL_MS && !(tradeDone || (delayStatus === 'done' && elapsedMs > 18 * 60 * 1000));

  const runStatus = (run.status as string) ?? 'running';
  const runError = (run.error as string | null) ?? null;

  // When the run itself is marked failed, determine which stage to flag red
  let stages;
  if (runStatus === 'failed') {
    const failedStage =
      newArticles === 0 ? 'ingester' :
      newSignals === 0  ? 'classifier' :
                          'trade';
    stages = {
      ingester:  { status: failedStage === 'ingester'  ? 'failed' : 'done',    count: newArticles },
      classifier: { status: failedStage === 'classifier' ? 'failed' : newArticles > 0 ? 'done' : 'pending', count: newSignals },
      sqs_delay:  { status: 'pending' as const, remain_ms: 0 },
      trade:      { status: failedStage === 'trade'    ? 'failed' : 'pending',  count: newPositions },
    };
  } else {
    stages = {
      ingester:   { status: ingesterStatus,  count: newArticles },
      classifier: { status: classifierStatus, count: newSignals },
      sqs_delay:  { status: delayStatus,      remain_ms: delayRemainMs },
      trade:      { status: tradeStatus,      count: newPositions },
    };
  }

  return json(200, {
    run: {
      _id: String(run._id),
      triggered_at: triggeredAt.toISOString(),
      source: run.source ?? 'manual',
      status: runStatus,
      error: runError,
    },
    stages,
    is_active: runStatus === 'failed' ? false : isActive,
    elapsed_ms: elapsedMs,
  });
});
