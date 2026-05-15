import { json } from '../lib/http.js';
import { getTradingDb } from '../lib/db.js';
import { createQueueService } from '../lib/queue.js';
import crypto from 'crypto';

export async function handler(event: { body?: string }): Promise<ReturnType<typeof json>> {
  const AI_PROVIDER = process.env.AI_PROVIDER ?? 'anthropic';

  let body: { date?: string; ai_provider?: string } = {};
  try {
    body = JSON.parse(event.body ?? '{}') as { date?: string; ai_provider?: string };
  } catch {
    return json(400, { error: 'Invalid JSON body' });
  }

  const runId = crypto.randomUUID();
  const date = body.date ?? new Date().toISOString().slice(0, 10);
  const aiProvider = body.ai_provider ?? AI_PROVIDER;

  const db = await getTradingDb();

  const now = new Date();
  await db.pipelineRuns().insertOne({
    run_id: runId,
    date,
    status: 'pending',
    ai_provider: aiProvider,
    started_at: now,
    agent_statuses: {
      news_agent: 'pending',
      sector_agent: 'pending',
      stock_selector: 'pending',
      technical_agent: 'pending',
      market_agent: 'pending',
      guard_agent: 'pending',
      signal_agent: 'pending',
      order_agent: 'pending',
      audit_agent: 'pending',
    },
    agent_timings: {},
    createdAt: now,
    updatedAt: now,
  });

  // Dispatch to signal-engine via the configured queue provider (local HTTP or SQS).
  const queue = createQueueService();
  void queue.dispatch({ run_id: runId, date, ai_provider: aiProvider });

  return json(202, { run_id: runId, date, ai_provider: aiProvider, status: 'pending' });
}
