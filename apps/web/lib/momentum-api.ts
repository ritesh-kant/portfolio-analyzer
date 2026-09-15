import type { AnalyticsTrade, TradeSource } from './momentum-analytics';

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
  direction?: 'bullish' | 'bearish' | 'neutral';
  kind?: 'reversal' | 'continuation' | 'indecision';
  prior_trend?: string;
  formed_at?: string;
  status?: string;
  rules_version?: string;
  /**
   * Range of the formation's confirming candle over the mean range of the ten
   * candles before it. 1 = an ordinary candle for this stock at this time of
   * day; below ~0.75 the name is still correct but the candle is too small to
   * act on. Descriptive only — it never suppresses a detection. Absent on
   * trades stored before 2026-09-13.
   */
  strength?: number;
  evidence?: {
    candles?: MomentumBar[];
    trend_closes?: number[];
    mean_prior_body?: number;
    mean_prior_range?: number;
    signal_candle_range?: number;
  };
}

export interface EntryEvidence {
  pattern_rules_version: string;
  promotion?: {
    bar_start: string;
    observed_at: string;
    day_chg_pct: number;
    rvol: number;
    minimum_day_chg_pct: number;
    minimum_rvol: number;
    reason: string;
    pattern_matches: PatternMatch[];
  };
  trend?: { timeframe: string; bar_start: string; close: number; ema9: number; ema20: number; vwap: number };
  confirmation?: {
    timeframe: string; bar_start: string; formed_at: string;
    open: number; high: number; low: number; close: number; volume: number;
    close_position: number; minimum_close_position: number;
    volume_ratio: number; minimum_volume_ratio: number;
  };
  pending_minutes?: number;
}

export interface MomentumTrade {
  _id: string;
  symbol: string;
  /** Paper strategy that produced this row. Older records predate strategy labels. */
  strategy?: string;
  status: 'open' | 'closed';
  setup: string;
  trigger_px?: number;
  stop_px?: number;
  level?: number | null;
  setup_meta?: Record<string, number | string>;
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
  entry_evidence?: EntryEvidence;
  pattern_rules_version?: string;
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

// ── analytics dashboard ──────────────────────────────────────────────────────

/**
 * Trade sets the analytics dashboard can read: the live paper ledger, plus any
 * backtest run imported with research/backtests/import_trades_to_mongo.py.
 */
export const fetchAnalyticsSources = () => get<{ sources: TradeSource[] }>('/mt/analytics/sources');

/** Closed trades from one source, slimmed to the fields the breakdowns use. */
export const fetchAnalyticsTrades = (source: string) =>
  get<{ source: string; kind: 'live' | 'backtest'; count: number; trades: AnalyticsTrade[] }>(
    `/mt/analytics?source=${encodeURIComponent(source)}`,
  );
