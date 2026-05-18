/**
 * OrchestratorService — creates a pipeline_run document and dispatches it to
 * the signal engine via the configured QueueService.
 *
 * Used by both the HTTP trigger handler (triggerRun.ts) and the scheduled
 * EventBridge handler (scheduler.ts) so the logic lives in one place.
 */

import crypto from 'crypto';
import { getTradingDb } from './db.js';
import { createQueueService } from './queue.js';
import { config } from './config.js';

export interface TriggerResult {
  run_id: string;
  date: string;
  ai_provider: string;
  skipped: boolean;
  reason?: string;
}

/**
 * Idempotent pipeline trigger — skips if a run for the given date already
 * exists in a non-failed state. Returns the existing run_id when skipped.
 */
export async function triggerPipelineRun(opts?: {
  date?: string;
  ai_provider?: string;
}): Promise<TriggerResult> {
  const date = opts?.date ?? new Date().toISOString().slice(0, 10);
  const aiProvider = opts?.ai_provider ?? config.aiProvider;

  const db = await getTradingDb();

  // Idempotency: one successful run per trading day
  const existing = await db.pipelineRuns().findOne({
    date,
    status: { $in: ['pending', 'running', 'completed'] },
  });
  if (existing) {
    return {
      run_id: existing.run_id,
      date,
      ai_provider: existing.ai_provider,
      skipped: true,
      reason: `run already exists with status=${existing.status}`,
    };
  }

  const runId = crypto.randomUUID();
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

  const queue = createQueueService();
  try {
    await queue.dispatch({ run_id: runId, date, ai_provider: aiProvider });
  } catch (err: unknown) {
    // Clean up the pending document so it doesn't linger
    await db.pipelineRuns().updateOne(
      { run_id: runId },
      { $set: { status: 'failed', error_summary: String(err), updatedAt: new Date() } },
    );
    throw err;
  }

  console.log(`[orchestrator] triggered run_id=${runId} date=${date} provider=${aiProvider}`);
  return { run_id: runId, date, ai_provider: aiProvider, skipped: false };
}

/**
 * Fire the monitor endpoint on the signal engine to scan open positions.
 * Non-fatal — logs on failure but does not throw.
 */
export async function triggerMonitorRun(): Promise<void> {
  const engineUrl = config.signalEngineUrl;
  const apiKey = config.signalEngineApiKey;
  try {
    const res = await fetch(`${engineUrl}/pipeline/monitor`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-api-key': apiKey },
    });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      console.error('[orchestrator] monitor returned %d: %s', res.status, text);
    }
  } catch (err: unknown) {
    console.error('[orchestrator] monitor unreachable:', err instanceof Error ? err.message : String(err));
  }
}
