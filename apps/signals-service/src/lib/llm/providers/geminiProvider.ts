import { GoogleGenerativeAI } from '@google/generative-ai';
import { timeoutPromise } from '../timeout.js';
import { config } from '../../config.js';

const genAI = new GoogleGenerativeAI(config.geminiApiKey);

export async function call(systemPrompt: string, userPrompt: string): Promise<string> {
  const modelName = config.geminiModel;
  const model = genAI.getGenerativeModel({
    model: modelName,
    systemInstruction: systemPrompt,
  });
  const result = await Promise.race([model.generateContent(userPrompt), timeoutPromise(30_000)]);
  return result.response.text();
}
