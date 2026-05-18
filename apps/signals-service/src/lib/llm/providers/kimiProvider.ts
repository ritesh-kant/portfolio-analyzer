import OpenAI from 'openai';
import { timeoutPromise } from '../timeout.js';
import { config } from '../../config.js';

const client = new OpenAI({
  apiKey: config.nvidiaApiKey,
  baseURL: config.nvidiaBaseUrl,
});

export async function call(systemPrompt: string, userPrompt: string): Promise<string> {
  const model = config.kimiModel;
  const result = await Promise.race([
    client.chat.completions.create({
      model,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userPrompt },
      ],
      max_tokens: 1024,
      temperature: 0.2,
    }),
    timeoutPromise(30_000),
  ]);
  const completion = result as OpenAI.Chat.ChatCompletion;
  const content = completion.choices[0]?.message.content;
  if (!content) throw new Error('Empty response from Kimi/NVIDIA');
  return content;
}
