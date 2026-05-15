/**
 * SQS consumer Lambda — receives pipeline-run messages from the SQS FIFO queue
 * and forwards them to the signal-engine container.
 *
 * Triggered automatically by the SQS event source defined in serverless.yml.
 * Deploy condition: QUEUE_PROVIDER=sqs
 */

interface SqsRecord {
  messageId: string;
  body: string;
}

interface SqsEvent {
  Records: SqsRecord[];
}

export async function handler(event: SqsEvent): Promise<void> {
  const SIGNAL_ENGINE_URL = process.env.SIGNAL_ENGINE_URL ?? 'http://localhost:8000';
  const SIGNAL_ENGINE_API_KEY = process.env.SIGNAL_ENGINE_API_KEY ?? '';

  for (const record of event.Records) {
    let payload: { run_id?: string; date?: string; ai_provider?: string };
    try {
      payload = JSON.parse(record.body) as typeof payload;
    } catch {
      console.error('[pipelineConsumer] Invalid SQS message body:', record.body);
      continue;
    }

    console.log('[pipelineConsumer] Processing run_id=%s', payload.run_id);

    const res = await fetch(`${SIGNAL_ENGINE_URL}/pipeline/run`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-api-key': SIGNAL_ENGINE_API_KEY,
      },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      throw new Error(
        `[pipelineConsumer] Signal engine returned ${res.status} for run_id=${payload.run_id ?? 'unknown'}`,
      );
    }
  }
}
