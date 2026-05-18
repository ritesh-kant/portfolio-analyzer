/**
 * SQS consumer Lambda — receives pipeline-run messages from the SQS FIFO queue
 * and dispatches them to the signal-engine Lambda via async invocation.
 *
 * InvocationType='Event' returns 202 immediately; signal-engine runs independently
 * with its own 900 s timeout. This Lambda completes in ~200 ms.
 *
 * Triggered automatically by the SQS event source defined in serverless.yml.
 * Deploy condition: QUEUE_PROVIDER=sqs
 */

import { InvokeCommand, LambdaClient } from '@aws-sdk/client-lambda';
import { config } from '../lib/config.js';

interface SqsRecord {
  messageId: string;
  body: string;
}

interface SqsEvent {
  Records: SqsRecord[];
}

const lambda = new LambdaClient({ region: config.awsRegion });

export async function handler(event: SqsEvent): Promise<void> {
  const functionName = config.signalEngineFunctionName;
  if (!functionName) throw new Error('SIGNAL_ENGINE_FUNCTION_NAME is not set');

  for (const record of event.Records) {
    let payload: { run_id?: string; date?: string; ai_provider?: string };
    try {
      payload = JSON.parse(record.body) as typeof payload;
    } catch {
      console.error('[pipelineConsumer] Invalid SQS message body:', record.body);
      continue;
    }

    console.log('[pipelineConsumer] Async-invoking signal-engine run_id=%s', payload.run_id);

    const response = await lambda.send(new InvokeCommand({
      FunctionName: functionName,
      InvocationType: 'Event', // async — Lambda returns 202, pipeline runs independently
      Payload: Buffer.from(JSON.stringify(payload)),
    }));

    if (response.StatusCode !== 202) {
      throw new Error(
        `[pipelineConsumer] Lambda invoke returned status ${response.StatusCode} for run_id=${payload.run_id ?? 'unknown'}`,
      );
    }

    console.log('[pipelineConsumer] Accepted run_id=%s status=202', payload.run_id);
  }
}
