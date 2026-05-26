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

async function recordRun(source: string) {
  await connect();
  await mongoose.connection.db!.collection('nt_pipeline_runs').insertOne({
    triggered_at: new Date(),
    source,
  });
}

export const handler = requireAuth(async () => {
  // serverless-offline sets IS_OFFLINE; call signal-engine's local HTTP endpoint instead
  if (process.env.IS_OFFLINE === 'true') {
    const base = process.env.SIGNAL_ENGINE_LOCAL_URL ?? 'http://localhost:8000';
    try {
      await recordRun('manual_local');
      const res = await fetch(`${base}/trigger/ingester`, { method: 'POST' });
      if (!res.ok) throw new Error(`signal-engine returned ${res.status}`);
      return json(202, { status: 'triggered', message: 'Local ingester started.' });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return json(503, {
        error: `Could not reach signal-engine at ${base}: ${msg}. Is it running? (pnpm dev in apps/signal-engine/)`,
      });
    }
  }

  try {
    await recordRun('manual');
    const client = new LambdaClient({});
    await client.send(
      new InvokeCommand({
        FunctionName: FN_NAME,
        InvocationType: 'Event',
        Payload: JSON.stringify({}),
      }),
    );
    return json(202, { status: 'triggered', function: FN_NAME });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return json(500, { error: `Failed to invoke Lambda: ${msg}` });
  }
});
