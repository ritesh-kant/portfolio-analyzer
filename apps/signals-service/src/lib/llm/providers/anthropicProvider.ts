import Anthropic from '@anthropic-ai/sdk';
import { timeoutPromise } from '../timeout.js';

const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

export async function call(systemPrompt: string, userPrompt: string): Promise<string> {
  const model = process.env.ANTHROPIC_MODEL ?? 'claude-sonnet-4-5';
  const result = await Promise.race([
    client.messages.create({
      model,
      max_tokens: 1024,
      system: systemPrompt,
      messages: [{ role: 'user', content: userPrompt }],
    }),
    timeoutPromise(30_000),
  ]);
  const msg = result as Anthropic.Message;
  for (const block of msg.content) {
    if (block.type === 'text') return block.text;
  }
  throw new Error('No text content block in Anthropic response');
}
