import { GoogleGenerativeAI } from '@google/generative-ai';
import { timeoutPromise } from '../timeout.js';

const genAI = new GoogleGenerativeAI(process.env.GEMINI_API_KEY ?? '');

export async function call(systemPrompt: string, userPrompt: string): Promise<string> {
  const modelName = process.env.GEMINI_MODEL ?? 'gemini-2.0-flash';
  const model = genAI.getGenerativeModel({
    model: modelName,
    systemInstruction: systemPrompt,
  });
  const result = await Promise.race([model.generateContent(userPrompt), timeoutPromise(30_000)]);
  return result.response.text();
}
