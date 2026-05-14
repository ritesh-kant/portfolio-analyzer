import { json } from '../lib/http.js';
import { callLLM } from '../lib/llm/llmProvider.js';
import { sanitizeJson } from '../lib/llm/sanitizeJson.js';
import { SYSTEM_PROMPT } from '../lib/llm/systemPrompt.js';
import { buildSignalPrompt } from '../lib/signalPromptBuilder.js';
import type {
  AnalyzeRequest,
  NewsResponse,
  TechnicalsResponse,
  FiiDiiResponse,
  OptionsResponse,
  MacroResponse,
  SignalResponse,
} from '../lib/types.js';

// Internal fetchers that call sibling handler logic directly (no HTTP round-trip)
import { handler as newsHandler } from './news.js';
import { handler as technicalsHandler } from './technicals.js';
import { handler as fiiDiiHandler } from './fiiDii.js';
import { handler as optionsHandler } from './options.js';
import { handler as macroHandler } from './macro.js';

async function parseHandlerResponse<T>(
  handlerFn: () => Promise<ReturnType<typeof json>>,
): Promise<T | null> {
  try {
    const result = await handlerFn();
    return JSON.parse(result.body) as T;
  } catch {
    return null;
  }
}

export async function handler(event: { body?: string }): Promise<ReturnType<typeof json>> {
  let body: Partial<AnalyzeRequest>;
  try {
    body = JSON.parse(event.body ?? '{}') as Partial<AnalyzeRequest>;
  } catch {
    return json(400, { error: 'Invalid JSON body' });
  }

  const { symbol, exchange = 'NSE', companyName, sector } = body;
  if (!symbol) {
    return json(400, { error: 'symbol is required in request body' });
  }

  // Fetch all signals in parallel — failures return null, analysis continues
  const [newsResult, techResult, fiiResult, optResult, macroResult] = await Promise.allSettled([
    parseHandlerResponse<NewsResponse>(() =>
      newsHandler({
        queryStringParameters: { symbol, companyName },
      }),
    ),
    parseHandlerResponse<TechnicalsResponse>(() =>
      technicalsHandler({ queryStringParameters: { symbol, exchange } }),
    ),
    parseHandlerResponse<FiiDiiResponse>(() => fiiDiiHandler()),
    parseHandlerResponse<OptionsResponse>(() =>
      optionsHandler({ queryStringParameters: { symbol } }),
    ),
    parseHandlerResponse<MacroResponse>(() => macroHandler()),
  ]);

  const news = newsResult.status === 'fulfilled' ? newsResult.value : null;
  const technicals = techResult.status === 'fulfilled' ? techResult.value : null;
  const fiiDii = fiiResult.status === 'fulfilled' ? fiiResult.value : null;
  const options = optResult.status === 'fulfilled' ? optResult.value : null;
  const macro = macroResult.status === 'fulfilled' ? macroResult.value : null;

  const sources = {
    news: news !== null && !news?.error,
    technicals: technicals !== null && !technicals?.error,
    fiiDii: fiiDii !== null && !fiiDii?.error,
    options: options !== null && !options?.error,
    macro: macro !== null && !macro?.error,
  };

  const userMessage = buildSignalPrompt({
    symbol: symbol.toUpperCase(),
    exchange: exchange.toUpperCase(),
    companyName,
    sector,
    news,
    technicals,
    fiiDii,
    options,
    macro,
  });

  let raw: string;
  try {
    raw = await callLLM(SYSTEM_PROMPT, userMessage);
  } catch (err) {
    return json(500, { error: 'LLM call failed', detail: String(err) });
  }

  let signal: SignalResponse;
  try {
    signal = sanitizeJson<SignalResponse>(raw);
  } catch {
    // Retry once with explicit JSON instruction
    try {
      const retryMessage =
        userMessage +
        '\n\nYour previous response was not valid JSON. Return ONLY the JSON object, no other text.';
      raw = await callLLM(SYSTEM_PROMPT, retryMessage);
      signal = sanitizeJson<SignalResponse>(raw);
    } catch (retryErr) {
      return json(500, {
        error: 'LLM returned malformed JSON after retry',
        detail: String(retryErr),
        rawResponse: raw,
      });
    }
  }

  return json(200, { ...signal, sources });
}
