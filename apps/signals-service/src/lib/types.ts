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

export interface SettingsResponse {
  provider: string;
  model: string;
  isLocal: boolean;
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
