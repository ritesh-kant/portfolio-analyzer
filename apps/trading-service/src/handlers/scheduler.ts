import { json } from '../lib/http.js';
import { triggerPipelineRun } from '../lib/orchestrator.js';

// EventBridge scheduled event — no HTTP response needed
export async function handler(): Promise<void> {
  try {
    const result = await triggerPipelineRun();
    if (result.skipped) {
      console.log(`[scheduler] skipped — ${result.reason}`);
    } else {
      console.log(`[scheduler] triggered run_id=${result.run_id} date=${result.date}`);
    }
  } catch (err) {
    console.error('[scheduler] failed:', err instanceof Error ? err.message : String(err));
    throw err; // re-throw so Lambda marks invocation as failed
  }
}

// Manual HTTP trigger for testing (same logic, returns JSON)
export async function httpHandler(): Promise<ReturnType<typeof json>> {
  try {
    const result = await triggerPipelineRun();
    const status = result.skipped ? 200 : 202;
    return json(status, result);
  } catch (err) {
    return json(500, { error: 'Scheduler failed', detail: err instanceof Error ? err.message : String(err) });
  }
}
