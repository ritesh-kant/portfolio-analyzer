import { InvokeCommand, LambdaClient } from '@aws-sdk/client-lambda';
import mongoose from 'mongoose';

import { requireAuth } from '../lib/auth.js';
import { json } from '../lib/http.js';
import { config } from '../lib/config.js';

const FN_NAME =
  process.env.SIGNAL_ENGINE_INGESTER_FN ?? 'signal-engine-dev-newsIngester';

async function connect() {
  if (!config.mongodbUri) throw new Error('MONGODB_URI is not set');
  if (mongoose.connection.readyState === 1) return;
  await mongoose.connect(config.mongodbUri, { serverSelectionTimeoutMS: 3000 });
}

async function insertRun(source: string): Promise<string> {
  await connect();
  const result = await mongoose.connection.db!.collection('nt_pipeline_runs').insertOne({
    triggered_at: new Date(),
    source,
    status: 'running',
  });
  return String(result.insertedId);
}

async function finaliseRun(runId: string, result: Record<string, unknown>) {
  await connect();
  await mongoose.connection.db!.collection('nt_pipeline_runs').updateOne(
    { _id: new mongoose.Types.ObjectId(runId) },
    {
      $set: {
        status: 'completed',
        completed_at: new Date(),
        new_articles: result.new_articles ?? 0,
        signals_created: result.signals ?? 0,
        positions_opened: result.positions_opened ?? 0,
      },
    },
  );
}

async function failRun(runId: string, err: string) {
  await connect();
  await mongoose.connection.db!.collection('nt_pipeline_runs').updateOne(
    { _id: new mongoose.Types.ObjectId(runId) },
    { $set: { status: 'failed', completed_at: new Date(), error: err } },
  );
}

export const handler = requireAuth(async () => {
  // serverless-offline sets IS_OFFLINE; call signal-engine's local HTTP endpoint instead
  if (process.env.IS_OFFLINE === 'true') {
    const base = process.env.SIGNAL_ENGINE_LOCAL_URL ?? 'http://localhost:8000';

    // Peek first — if signal-engine is unreachable or market hours block, tell the UI immediately
    let peekRes: Response;
    try {
      peekRes = await fetch(`${base}/health`);
      if (!peekRes.ok) throw new Error('unhealthy');
    } catch {
      return json(503, {
        error: `Could not reach signal-engine at ${base}. Is it running? (pnpm dev in apps/signal-engine/)`,
      });
    }

    // Record the run before starting so the status panel shows it immediately
    const runId = await insertRun('manual_local');

    // Fire-and-forget: pipeline is slow (classifying ~300+ articles), return 202 right away.
    // Signal-engine owns finalization — it updates nt_pipeline_runs directly via run_id.
    fetch(`${base}/trigger/pipeline?run_id=${runId}`, { method: 'POST' })
      .then(async (res) => {
        if (!res.ok) {
          // Signal-engine already marked the run failed before returning the error response.
          // Log for observability but do not double-write.
          console.error(`[PIPELINE] signal-engine returned ${res.status} for run_id=${runId}`);
        }
        // On success or skipped: signal-engine called finalise_run / delete_run directly.
      })
      .catch(async (err) => {
        // Network-level failure — signal-engine never received the request, so it cannot
        // finalize. Portfolio-service must mark the run failed here.
        await failRun(runId, String(err));
      });

    return json(202, { status: 'triggered' });
  }

  try {
    const runId = await insertRun('manual');
    const client = new LambdaClient({});
    await client.send(
      new InvokeCommand({
        FunctionName: FN_NAME,
        InvocationType: 'Event',
        // run_id is threaded through the SQS chain so tradeDecision can finalize the run.
        Payload: JSON.stringify({ run_id: runId }),
      }),
    );
    return json(202, { status: 'triggered', function: FN_NAME });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return json(500, { error: `Failed to invoke Lambda: ${msg}` });
  }
});
