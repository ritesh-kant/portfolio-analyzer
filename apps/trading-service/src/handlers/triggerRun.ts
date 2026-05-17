import { json } from '../lib/http.js';
import { requireAuth } from '../lib/auth.js';
import { triggerPipelineRun } from '../lib/orchestrator.js';

export const handler = requireAuth(async (event: { body?: string }): Promise<ReturnType<typeof json>> => {
  let body: { date?: string; ai_provider?: string } = {};
  try {
    body = JSON.parse(event.body ?? '{}') as { date?: string; ai_provider?: string };
  } catch {
    return json(400, { error: 'Invalid JSON body' });
  }

  try {
    const result = await triggerPipelineRun({ date: body.date, ai_provider: body.ai_provider });
    const status = result.skipped ? 200 : 202;
    return json(status, result);
  } catch (err: unknown) {
    return json(500, { error: 'Failed to trigger run', detail: err instanceof Error ? err.message : String(err) });
  }
});
