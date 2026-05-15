import crypto from 'crypto';
import { json } from '../lib/http.js';
import { getTradingDb } from '../lib/db.js';

const SIGNAL_ENGINE_URL = process.env.SIGNAL_ENGINE_URL ?? 'http://localhost:8000';
const SIGNAL_ENGINE_API_KEY = process.env.SIGNAL_ENGINE_API_KEY ?? '';
const AI_PROVIDER = process.env.AI_PROVIDER ?? 'anthropic';

async function triggerDailyRun(): Promise<{ run_id: string; skipped: boolean; reason?: string }> {
  const today = new Date().toISOString().slice(0, 10);
  const db = await getTradingDb();

  // Idempotency: skip if a run already exists for today
  const existing = await db.pipelineRuns().findOne({
    date: today,
    status: { $in: ['pending', 'running', 'completed'] },
  });
  if (existing) {
    return { run_id: existing.run_id, skipped: true, reason: `run already exists with status=${existing.status}` };
  }

  const runId = crypto.randomUUID();
  const now = new Date();
  await db.pipelineRuns().insertOne({
    run_id: runId,
    date: today,
    status: 'pending',
    ai_provider: AI_PROVIDER,
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

  fetch(`${SIGNAL_ENGINE_URL}/pipeline/run`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-api-key': SIGNAL_ENGINE_API_KEY,
    },
    body: JSON.stringify({ run_id: runId, date: today, ai_provider: AI_PROVIDER }),
  }).catch((err: unknown) => {
    console.error('[scheduler] Signal engine unreachable:', err instanceof Error ? err.message : String(err));
  });

  console.log(`[scheduler] triggered run_id=${runId} date=${today}`);
  return { run_id: runId, skipped: false };
}

// EventBridge scheduled event — no HTTP response needed
export async function handler(): Promise<void> {
  try {
    const result = await triggerDailyRun();
    if (result.skipped) {
      console.log(`[scheduler] skipped — ${result.reason}`);
    }
  } catch (err) {
    console.error('[scheduler] failed:', err instanceof Error ? err.message : String(err));
    throw err; // re-throw so Lambda marks invocation as failed
  }
}

// Manual HTTP trigger for testing (same logic, returns JSON)
export async function httpHandler(): Promise<ReturnType<typeof json>> {
  try {
    const result = await triggerDailyRun();
    const status = result.skipped ? 200 : 202;
    return json(status, result);
  } catch (err) {
    return json(500, { error: 'Scheduler failed', detail: err instanceof Error ? err.message : String(err) });
  }
}
