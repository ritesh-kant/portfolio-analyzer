import OpenAI from 'openai';
import { timeoutPromise } from '../timeout.js';

const client = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });

export async function call(systemPrompt: string, userPrompt: string): Promise<string> {
  const model = process.env.OPENAI_MODEL ?? 'gpt-4o';
  const result = await Promise.race([
    client.chat.completions.create({
      model,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userPrompt },
      ],
      max_tokens: 1024,
      response_format: { type: 'json_object' },
    }),
    timeoutPromise(30_000),
  ]);
  const completion = result as OpenAI.Chat.ChatCompletion;
  const content = completion.choices[0]?.message.content;
  if (!content) throw new Error('Empty response from OpenAI');
  return content;
}
