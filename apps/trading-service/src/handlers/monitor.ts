import { json } from '../lib/http.js';
import { triggerMonitorRun } from '../lib/orchestrator.js';

// EventBridge scheduled event — fires every 30 min during NSE market hours
// cron(15,45 3-9 ? * MON-FRI *) = 09:15, 09:45, …, 15:15, 15:45 IST (UTC+5:30)
export async function handler(): Promise<void> {
  try {
    await triggerMonitorRun();
    console.log('[monitor] dispatched monitor run to signal-engine');
  } catch (err) {
    console.error('[monitor] failed:', err instanceof Error ? err.message : String(err));
    throw err;
  }
}

// Manual HTTP trigger for testing
export async function httpHandler(): Promise<ReturnType<typeof json>> {
  try {
    await triggerMonitorRun();
    return json(202, { status: 'accepted', message: 'Monitor run dispatched' });
  } catch (err) {
    return json(500, { error: 'Monitor dispatch failed', detail: err instanceof Error ? err.message : String(err) });
  }
}
