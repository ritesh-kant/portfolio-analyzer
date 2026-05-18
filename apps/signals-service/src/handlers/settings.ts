import { requireAuth } from '../lib/auth.js';
import { json } from '../lib/http.js';
import { config } from '../lib/config.js';

const MODEL_BY_PROVIDER: Record<string, string> = {
  anthropic: config.anthropicModel,
  openai: config.openaiModel,
  gemini: config.geminiModel,
  kimi: config.kimiModel,
  ollama: config.ollamaModel,
};

export const handler = requireAuth(async (): Promise<ReturnType<typeof json>> => {
  const provider = config.aiProvider;
  const model = MODEL_BY_PROVIDER[provider] ?? 'unknown';

  return json(200, {
    provider,
    model,
    isLocal: provider === 'ollama',
  });
});
