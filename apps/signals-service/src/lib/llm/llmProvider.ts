import * as anthropic from './providers/anthropicProvider.js';
import * as openai from './providers/openaiProvider.js';
import * as gemini from './providers/geminiProvider.js';
import * as kimi from './providers/kimiProvider.js';
import * as ollama from './providers/ollamaProvider.js';

const providers = { anthropic, openai, gemini, kimi, ollama } as const;
type ProviderName = keyof typeof providers;

export async function callLLM(systemPrompt: string, userPrompt: string): Promise<string> {
  const name = (process.env.AI_PROVIDER ?? 'anthropic') as ProviderName;
  const provider = providers[name];
  if (!provider) throw new Error(`Unknown AI_PROVIDER: ${name}. Valid values: anthropic, openai, gemini, kimi, ollama`);
  return provider.call(systemPrompt, userPrompt);
}
