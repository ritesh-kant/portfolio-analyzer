const BASE = process.env.NEXT_PUBLIC_PORTFOLIO_API_BASE ?? 'http://localhost:3001';

let cachedToken: string | null = null;

async function getToken(): Promise<string> {
  if (cachedToken) return cachedToken;
  const res = await fetch('/api/token');
  if (!res.ok) throw new Error('Not authenticated');
  const data = (await res.json()) as { token: string };
  cachedToken = data.token;
  return cachedToken;
}

async function get<T>(path: string): Promise<T> {
  const token = await getToken();
  const res = await fetch(`${BASE}${path}`, {
    cache: 'no-store',
    headers: { Authorization: `Bearer ${token}` },
  });
  if (res.status === 401) {
    cachedToken = null;
    throw new Error('Session expired — please refresh');
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${path}`);
  return res.json() as Promise<T>;
}

export interface MomentumBar {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/** A named formation made entirely from completed candles. */
export interface PatternMatch {
  name: string;
  timeframe: '1m' | '5m';
  start: string;
  end: string;
  confirmation: number;
  invalidation: number;
}

export interface MomentumTrade {
  _id: string;
  symbol: string;
  status: 'open' | 'closed';
  setup: string;
  trigger_px?: number;
  stop_px?: number;
  level?: number | null;
  setup_meta?: Record<string, number>;
  time: string;
  entry_time: string;
  entry_price: number;
  exit_time?: string;
  exit_price?: number;
  exit_reason?: string;
  stop: number;
  target?: number | null;
  qty: number;
  notional_inr?: number;
  risk_inr?: number;
  gross_inr?: number;
  costs_inr?: number;
  net_inr?: number;
  day_chg_pct?: number;
  rvol?: number;
  catalyst?: number;
  event_type?: string;
  candle_tags?: string[];
  pattern_matches?: PatternMatch[];
  quality_reason?: string;
  pullback_ord?: number | null;
  atr_pct?: number | null;
  macd_hist?: number | null;
  resist_head_pct?: number | null;
  support_drop_pct?: number | null;
  chart?: { interval: '1m'; bars: MomentumBar[] };
}

export const fetchMomentumTrades = () =>
  get<{ trades: MomentumTrade[]; count: number }>('/mt/trades?limit=500');
