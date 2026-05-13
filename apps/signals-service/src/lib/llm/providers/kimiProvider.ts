import OpenAI from 'openai';
import { timeoutPromise } from '../timeout.js';

const client = new OpenAI({
  apiKey: process.env.NVIDIA_API_KEY,
  baseURL: process.env.NVIDIA_BASE_URL ?? 'https://integrate.api.nvidia.com/v1',
});

export async function call(systemPrompt: string, userPrompt: string): Promise<string> {
  const model = process.env.KIMI_MODEL ?? 'moonshotai/kimi-k2-instruct';
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
