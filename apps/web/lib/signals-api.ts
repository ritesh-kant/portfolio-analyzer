export const SIGNALS_API_BASE = process.env.NEXT_PUBLIC_SIGNALS_API_BASE ?? 'http://localhost:5001';

export interface SettingsResponse {
  provider: string;
  model: string;
  isLocal: boolean;
}

export interface NewsArticle {
  title: string;
  source: string;
  publishedAt: string;
  url: string;
}

export interface NewsResponse {
  articles: NewsArticle[];
  error?: string;
}

export interface TechnicalIndicator {
  value: number | null;
  interpretation: string;
}

export interface TechnicalsResponse {
  symbol: string;
  currentPrice: number;
  rsi: TechnicalIndicator;
  macd: TechnicalIndicator;
  bollingerBands: TechnicalIndicator;
  ema20: TechnicalIndicator;
  ema50: TechnicalIndicator;
  volumeRatio: TechnicalIndicator;
  error?: string;
}

export interface FlowData {
  buy: number;
  sell: number;
  net: number;
}

export interface FiiDiiResponse {
  fii: FlowData;
  dii: FlowData;
  date: string;
  error?: string;
}

export interface OptionsResponse {
  pcr: number;
  maxPain: number;
  interpretation: string;
  error?: string;
}

export interface MacroIndicator {
  symbol: string;
  label: string;
  value: number;
  changePercent: number;
  context: string;
}

export interface MacroResponse {
  indicators: MacroIndicator[];
  error?: string;
}

export interface AnalyzeRequest {
  symbol: string;
  exchange: string;
  companyName?: string;
  sector?: string;
}

export interface ConfluenceScore {
  newsSentiment: -1 | 0 | 1;
  technicals: -1 | 0 | 1;
  volume: -1 | 0 | 1;
  fiiDii: -1 | 0 | 1;
  options: -1 | 0 | 1;
  macro: -1 | 0 | 1;
}

export interface SignalResponse {
  signal: 'BUY' | 'SELL' | 'HOLD';
  confidence: number;
  estimatedMovePercent: { low: number; high: number; direction: 'up' | 'down' };
  timeHorizon: string;
  stopLoss: { price: number; percentFromCurrent: number };
  positionSizeSuggestion: string;
  confluenceScore: ConfluenceScore;
  reasoning: string;
  riskWarning: string;
  sources?: {
    news: boolean;
    technicals: boolean;
    fiiDii: boolean;
    options: boolean;
    macro: boolean;
  };
}

let cachedToken: string | null = null;

async function getToken(): Promise<string> {
  if (cachedToken) return cachedToken;
  const res = await fetch('/api/token');
  if (!res.ok) throw new Error('Not authenticated');
  const data = (await res.json()) as { token: string };
  cachedToken = data.token;
  return cachedToken;
}

async function fetchJson<T>(url: string): Promise<T> {
  const token = await getToken();
  const response = await fetch(url, {
    method: 'GET',
    headers: { 'content-type': 'application/json', Authorization: `Bearer ${token}` },
    cache: 'no-store',
  });
  if (response.status === 401) {
    cachedToken = null;
    throw new Error('Session expired — please refresh');
  }
  if (!response.ok) throw new Error(`Request failed (${response.status}) for ${url}`);
  return (await response.json()) as T;
}

export async function fetchSignalsSettings(): Promise<SettingsResponse> {
  return fetchJson<SettingsResponse>(`${SIGNALS_API_BASE}/signals/settings`);
}

export async function fetchSignalsNews(
  symbol?: string,
  companyName?: string,
): Promise<NewsResponse> {
  const params = new URLSearchParams();
  if (symbol) params.append('symbol', symbol);
  if (companyName) params.append('companyName', companyName);
  const queryString = params.toString();
  return fetchJson<NewsResponse>(
    `${SIGNALS_API_BASE}/signals/news${queryString ? `?${queryString}` : ''}`,
  );
}

export async function fetchSignalsTechnicals(
  symbol: string,
  exchange: string,
): Promise<TechnicalsResponse> {
  const params = new URLSearchParams({ symbol, exchange });
  return fetchJson<TechnicalsResponse>(`${SIGNALS_API_BASE}/signals/technicals?${params}`);
}

export async function fetchSignalsFiiDii(): Promise<FiiDiiResponse> {
  return fetchJson<FiiDiiResponse>(`${SIGNALS_API_BASE}/signals/fii-dii`);
}

export async function fetchSignalsOptions(symbol: string): Promise<OptionsResponse> {
  const params = new URLSearchParams({ symbol });
  return fetchJson<OptionsResponse>(`${SIGNALS_API_BASE}/signals/options?${params}`);
}

export async function fetchSignalsMacro(): Promise<MacroResponse> {
  return fetchJson<MacroResponse>(`${SIGNALS_API_BASE}/signals/macro`);
}

export async function analyzeSignal(body: AnalyzeRequest): Promise<SignalResponse> {
  const token = await getToken();
  const response = await fetch(`${SIGNALS_API_BASE}/signals/analyze`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', Authorization: `Bearer ${token}` },
    cache: 'no-store',
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const err = (await response.json().catch(() => ({ error: 'Unknown error' }))) as {
      error?: string;
    };
    throw new Error(err.error ?? `Analysis failed (${response.status})`);
  }
  return (await response.json()) as SignalResponse;
}
