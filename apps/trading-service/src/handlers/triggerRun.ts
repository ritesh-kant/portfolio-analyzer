import { json } from '../lib/http.js';
import { getTradingDb } from '../lib/db.js';
import crypto from 'crypto';

export async function handler(event: { body?: string }): Promise<ReturnType<typeof json>> {
  const SIGNAL_ENGINE_URL = process.env.SIGNAL_ENGINE_URL ?? 'http://localhost:8000';
  const SIGNAL_ENGINE_API_KEY = process.env.SIGNAL_ENGINE_API_KEY ?? '';
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

  // Fire-and-forget: POST to signal engine (non-blocking)
  fetch(`${SIGNAL_ENGINE_URL}/pipeline/run`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-api-key': SIGNAL_ENGINE_API_KEY,
    },
    body: JSON.stringify({ run_id: runId, date, ai_provider: aiProvider }),
  }).catch((err: unknown) => {
    console.error('[triggerRun] Signal engine unreachable:', err instanceof Error ? err.message : String(err));
  });

  return json(202, { run_id: runId, date, ai_provider: aiProvider, status: 'pending' });
}
