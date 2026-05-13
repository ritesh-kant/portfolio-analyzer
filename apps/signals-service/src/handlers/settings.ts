import { json } from '../lib/http.js';

const MODEL_ENV_MAP: Record<string, string> = {
  anthropic: 'ANTHROPIC_MODEL',
  openai: 'OPENAI_MODEL',
  gemini: 'GEMINI_MODEL',
  kimi: 'KIMI_MODEL',
  ollama: 'OLLAMA_MODEL',
};

const MODEL_DEFAULTS: Record<string, string> = {
  anthropic: 'claude-sonnet-4-5',
  openai: 'gpt-4o',
  gemini: 'gemini-2.0-flash',
  kimi: 'moonshotai/kimi-k2-instruct',
  ollama: 'llama3.1',
};

export async function handler(): Promise<ReturnType<typeof json>> {
  const provider = process.env.AI_PROVIDER ?? 'anthropic';
  const modelEnvKey = MODEL_ENV_MAP[provider];
  const model = (modelEnvKey ? process.env[modelEnvKey] : undefined) ?? MODEL_DEFAULTS[provider] ?? 'unknown';

  return json(200, {
    provider,
    model,
    isLocal: provider === 'ollama',
  });
}
