import type { AnalyticsTrade, TradeSource } from './momentum-analytics';
import { PORTFOLIO_API_BASE as BASE } from './api-base';

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
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      cache: 'no-store',
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch (err) {
    // A network-level failure on a cross-origin fetch (DNS, blocked route,
    // or missing CORS headers on a gateway 4xx/5xx) surfaces here as a
    // generic TypeError — translate it into something actionable.
    throw new Error(
      `Network error reaching ${BASE}${path} — the API did not return CORS headers. ` +
        `Check the API base URL / stage and the service logs. (${err instanceof Error ? err.message : String(err)})`,
    );
  }
  if (res.status === 401) {
    cachedToken = null;
    throw new Error('Session expired — please refresh');
  }
  if (!res.ok) {
    let detail = '';
    try {
      const body = (await res.json()) as { error?: string };
      if (body?.error) detail = `: ${body.error}`;
    } catch {
      /* non-JSON error body — keep status only */
    }
    throw new Error(`HTTP ${res.status}${detail}: ${path}`);
  }
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
  trend?: {
    timeframe: string;
    bar_start: string;
    close: number;
    ema9: number;
    ema20: number;
    vwap: number;
    ema200?: number | null;
  };
  confirmation?: {
    timeframe: string;
    bar_start: string;
    formed_at: string;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
    close_position: number;
    minimum_close_position: number;
    volume_ratio: number;
    minimum_volume_ratio: number;
    macd_hist?: number | null;
  };
  pending_minutes?: number;
}

/**
 * Descriptive only — attached in the background when the trade opens by
 * scanning RSS/NSE/BSE for headlines mentioning the symbol. Never used by the
 * engine's entry/exit logic; unrelated to `catalyst`/`event_type` below,
 * which come from the separate (unresolved) catalyst-gate hypothesis.
 */
export interface NewsContextItem {
  headline: string;
  source: string;
  publisher?: string;
  tier?: string;
  url?: string | null;
  published_at: string;
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
  exit_time?: string | null;
  exit_price?: number | null;
  exit_reason?: string | null;
  stop: number;
  target?: number | null;
  qty: number;
  notional_inr?: number | null;
  risk_inr?: number | null;
  gross_inr?: number | null;
  costs_inr?: number | null;
  net_inr?: number | null;
  day_chg_pct?: number;
  rvol?: number;
  catalyst?: number;
  event_type?: string;
  candle_tags?: string[];
  news_context?: NewsContextItem[];
  pattern_matches?: PatternMatch[];
  entry_evidence?: EntryEvidence;
  pattern_rules_version?: string;
  quality_reason?: string;
  pullback_ord?: number | null;
  atr_pct?: number | null;
  macd_hist?: number | null;
  resist_head_pct?: number | null;
  support_drop_pct?: number | null;
  /** The levels above in rupees, and the price they were measured from (the
   * setup's trigger, not the fill). Recorded from 2026-09-22; older rows carry
   * only the percentages and are rebuilt from `trigger_px`. */
  level_anchor_px?: number | null;
  resist_px?: number | null;
  support_px?: number | null;
  resist_kind?: string;
  support_kind?: string;
  /** What the exit rules actually hold for the life of the trade, fixed at
   * entry by `exits.initial_state`. Recorded from 2026-09-22. */
  structural_resistance?: number | null;
  structural_resistance_kind?: string;
  structural_support?: number | null;
  structural_support_kind?: string;
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

// ── attention watchlist ──────────────────────────────────────────────────────

/** One time the scanner promoted a name onto the watchlist, and why. */
export interface WatchlistFlag {
  time: string;
  strategy?: string;
  reason: string;
  day_chg_pct: number;
  rvol: number;
  candle_tags: string[];
  pattern_matches: PatternMatch[];
}

export interface WatchlistName {
  symbol: string;
  first_seen: string;
  last_seen: string;
  max_day_chg_pct: number;
  max_rvol: number;
  strategies: string[];
  flags: WatchlistFlag[];
  /** Paper trades opened on this name in the same session, if any. US rows
   * carry `net_usd` instead of `net_inr`. */
  trades: (Pick<MomentumTrade, '_id' | 'symbol' | 'entry_time' | 'exit_time' | 'entry_price' | 'exit_price' | 'net_inr' | 'status' | 'strategy'> & {
    net_usd?: number | null;
  })[];
  /** Extra per-name facts a market wants shown (the US screen's price and float). */
  details?: { label: string; value: string }[];
}

export interface WatchlistSession {
  /** IST session date, YYYY-MM-DD. */
  date: string;
  names: WatchlistName[];
}

export const fetchMomentumWatchlist = (days = 15) =>
  get<{ sessions: WatchlistSession[]; count: number }>(`/mt/watchlist?days=${days}`);

export const fetchWatchlistBars = (symbol: string, date: string) =>
  get<{ symbol: string; date: string; interval: '1m'; bars: MomentumBar[] }>(
    `/mt/watchlist/bars?symbol=${encodeURIComponent(symbol)}&date=${date}`,
  );

export interface WatchlistNewsItem {
  headline: string;
  publisher: string;
  url: string | null;
  published_at: string;
}

/** Per-symbol headlines for one session; a symbol whose lookup failed carries `error`. */
export const fetchWatchlistNews = (date: string, symbols: string[], market: 'NSE' | 'US' = 'NSE') =>
  get<{ date: string; source: string; news: Record<string, WatchlistNewsItem[] | { error: string }> }>(
    `/mt/watchlist/news?date=${date}&market=${market}&symbols=${symbols.map(encodeURIComponent).join(',')}`,
  );
