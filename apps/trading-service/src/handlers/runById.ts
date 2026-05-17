import { json } from '../lib/http.js';
import { requireAuth } from '../lib/auth.js';
import { getTradingDb } from '../lib/db.js';

export const handler = requireAuth(async (event): Promise<ReturnType<typeof json>> => {
  try {
    const id = event.pathParameters?.['id'];
    if (!id) return json(400, { error: 'id path parameter is required' });

    const db = await getTradingDb();
    const run = await db.pipelineRuns().findOne({ run_id: id });
    if (!run) return json(404, { error: 'Pipeline run not found' });

    const agentLogs = await db
      .agentLogs()
      .find({ run_id: id })
      .sort({ createdAt: 1 })
      .toArray();

    return json(200, { run, agent_logs: agentLogs });
  } catch (error) {
    return json(500, { error: 'Failed to fetch run', detail: error instanceof Error ? error.message : String(error) });
  }
});
