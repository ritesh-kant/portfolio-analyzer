import type { SearchHit } from './types';

/**
 * TradingView symbols that are broker CFDs or spot quotes (OANDA XAUUSD, FX pairs)
 * have no Yahoo ticker of their own. This maps the name people type to the closest
 * Yahoo series and says so in the result, so a futures contract is never mistaken
 * for the spot price.
 */
const METALS: Record<string, SearchHit> = {
  XAUUSD: {
    symbol: 'GC=F',
    name: 'Gold — COMEX futures (proxy for XAUUSD spot)',
    exchange: 'COMEX',
    type: 'FUTURE',
  },
  XAGUSD: {
    symbol: 'SI=F',
    name: 'Silver — COMEX futures (proxy for XAGUSD spot)',
    exchange: 'COMEX',
    type: 'FUTURE',
  },
  XPTUSD: {
    symbol: 'PL=F',
    name: 'Platinum — NYMEX futures (proxy for XPTUSD spot)',
    exchange: 'NYMEX',
    type: 'FUTURE',
  },
  XPDUSD: {
    symbol: 'PA=F',
    name: 'Palladium — NYMEX futures (proxy for XPDUSD spot)',
    exchange: 'NYMEX',
    type: 'FUTURE',
  },
  USOIL: {
    symbol: 'CL=F',
    name: 'WTI crude oil — NYMEX futures',
    exchange: 'NYMEX',
    type: 'FUTURE',
  },
  UKOIL: { symbol: 'BZ=F', name: 'Brent crude oil — futures', exchange: 'NYMEX', type: 'FUTURE' },
  NATGAS: {
    symbol: 'NG=F',
    name: 'Natural gas — NYMEX futures',
    exchange: 'NYMEX',
    type: 'FUTURE',
  },
  XCUUSD: { symbol: 'HG=F', name: 'Copper — COMEX futures', exchange: 'COMEX', type: 'FUTURE' },
  US500: { symbol: '^GSPC', name: 'S&P 500 index', exchange: 'SNP', type: 'INDEX' },
  SPX: { symbol: '^GSPC', name: 'S&P 500 index', exchange: 'SNP', type: 'INDEX' },
  NAS100: { symbol: '^NDX', name: 'Nasdaq 100 index', exchange: 'NASDAQ', type: 'INDEX' },
  US30: { symbol: '^DJI', name: 'Dow Jones Industrial Average', exchange: 'DJI', type: 'INDEX' },
  NIFTY: { symbol: '^NSEI', name: 'NIFTY 50', exchange: 'NSE', type: 'INDEX' },
  BANKNIFTY: { symbol: '^NSEBANK', name: 'NIFTY BANK', exchange: 'NSE', type: 'INDEX' },
  SENSEX: { symbol: '^BSESN', name: 'S&P BSE SENSEX', exchange: 'BSE', type: 'INDEX' },
};

const CRYPTO = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'ADA', 'BNB', 'LTC'];
const FX = ['USD', 'EUR', 'GBP', 'JPY', 'INR', 'AUD', 'CAD', 'CHF', 'NZD', 'CNY', 'SGD', 'AED'];

/** Synthetic hits for a typed query, best first; empty when the query is not a known alias. */
export function aliasHits(query: string): SearchHit[] {
  const q = query
    .trim()
    .toUpperCase()
    .replace(/[\s/:_-]/g, '')
    .replace(/^(OANDA|FX|FOREX|TVC|CAPITALCOM|FXCM)/, '');
  const hits: SearchHit[] = [];
  const metal = METALS[q];
  if (metal) hits.push(metal);
  // BTCUSD / BTCUSDT → BTC-USD
  const crypto = CRYPTO.find((c) => q === `${c}USD` || q === `${c}USDT`);
  if (crypto)
    hits.push({
      symbol: `${crypto}-USD`,
      name: `${crypto} / US Dollar`,
      exchange: 'CCC',
      type: 'CRYPTOCURRENCY',
    });
  // EURUSD → EURUSD=X
  if (
    /^[A-Z]{6}$/.test(q) &&
    FX.includes(q.slice(0, 3)) &&
    FX.includes(q.slice(3)) &&
    q.slice(0, 3) !== q.slice(3)
  ) {
    hits.push({
      symbol: `${q}=X`,
      name: `${q.slice(0, 3)} / ${q.slice(3)} spot`,
      exchange: 'FX',
      type: 'CURRENCY',
    });
  }
  return hits;
}
