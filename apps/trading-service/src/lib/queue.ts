/**
 * QueueService — abstracts pipeline-run dispatch so the same handler works
 * locally (direct HTTP to signal-engine) and on AWS (SQS → Lambda consumer).
 *
 * Select provider via QUEUE_PROVIDER env var:
 *   "local" (default) — direct HTTP POST to SIGNAL_ENGINE_URL
 *   "sqs"             — publish JSON message to SQS_QUEUE_URL
 */

export interface PipelineRunPayload {
  run_id: string;
  date: string;
  ai_provider: string;
}

export interface QueueService {
  /** Fire-and-forget dispatch. Throws on hard errors (not network timeouts). */
  dispatch(payload: PipelineRunPayload): Promise<void>;
}

// ─── Local provider (direct HTTP) ────────────────────────────────────────────

class LocalQueueService implements QueueService {
  private readonly url: string;
  private readonly apiKey: string;

  constructor() {
    this.url = process.env.SIGNAL_ENGINE_URL ?? 'http://localhost:8000';
    this.apiKey = process.env.SIGNAL_ENGINE_API_KEY ?? '';
  }

  async dispatch(payload: PipelineRunPayload): Promise<void> {
    try {
      const res = await fetch(`${this.url}/pipeline/run`, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'x-api-key': this.apiKey,
        },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const text = await res.text().catch(() => '');
        console.error('[QueueService/local] Signal engine returned %d: %s', res.status, text);
      }
    } catch (err: unknown) {
      console.error(
        '[QueueService/local] Signal engine unreachable:',
        err instanceof Error ? err.message : String(err),
      );
    }
  }
}

// ─── SQS provider ─────────────────────────────────────────────────────────────
// Requires: npm install @aws-sdk/client-sqs
// A separate Lambda function subscribed to the SQS queue calls the signal
// engine (see src/handlers/pipelineConsumer.ts).

class SqsQueueService implements QueueService {
  private readonly queueUrl: string;

  constructor() {
    const queueUrl = process.env.SQS_QUEUE_URL;
    if (!queueUrl) throw new Error('SQS_QUEUE_URL is required when QUEUE_PROVIDER=sqs');
    this.queueUrl = queueUrl;
  }

  async dispatch(payload: PipelineRunPayload): Promise<void> {
    // Dynamic import so the package is only required when this provider is active.
    // Install: pnpm add @aws-sdk/client-sqs --filter trading-service
    const { SQSClient, SendMessageCommand } = await import('@aws-sdk/client-sqs');

    const client = new SQSClient({ region: process.env.AWS_REGION ?? 'ap-south-1' });
    await client.send(
      new SendMessageCommand({
        QueueUrl: this.queueUrl,
        MessageBody: JSON.stringify(payload),
        MessageGroupId: 'pipeline-runs',         // required for FIFO queues
        MessageDeduplicationId: payload.run_id,  // idempotency
      }),
    );
    console.log('[QueueService/sqs] Dispatched run_id=%s to SQS', payload.run_id);
  }
}

// ─── Factory ──────────────────────────────────────────────────────────────────

export function createQueueService(): QueueService {
  const provider = process.env.QUEUE_PROVIDER ?? 'local';
  switch (provider) {
    case 'sqs':
      return new SqsQueueService();
    case 'local':
    default:
      return new LocalQueueService();
  }
}
