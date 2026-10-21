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

/** Futures roots as TradingView lists them: `GC` is "Gold Futures · COMEX". Yahoo's `GC=F` is the front month. */
const GOLD = '#d4a017';
const FUTURES: Record<string, { name: string; exchange: string; color: string }> = {
  GC: { name: 'Gold Futures', exchange: 'COMEX', color: GOLD },
  MGC: { name: 'Micro Gold Futures', exchange: 'COMEX', color: GOLD },
  SI: { name: 'Silver Futures', exchange: 'COMEX', color: '#9aa4b2' },
  HG: { name: 'Copper Futures', exchange: 'COMEX', color: '#c8643c' },
  PL: { name: 'Platinum Futures', exchange: 'NYMEX', color: '#9aa4b2' },
  PA: { name: 'Palladium Futures', exchange: 'NYMEX', color: '#9aa4b2' },
  CL: { name: 'Crude Oil WTI Futures', exchange: 'NYMEX', color: '#4b5563' },
  BZ: { name: 'Brent Crude Oil Futures', exchange: 'NYMEX', color: '#4b5563' },
  NG: { name: 'Natural Gas Futures', exchange: 'NYMEX', color: '#2f80ed' },
  ES: { name: 'E-mini S&P 500 Futures', exchange: 'CME', color: '#2962ff' },
  NQ: { name: 'E-mini Nasdaq-100 Futures', exchange: 'CME', color: '#2962ff' },
  YM: { name: 'E-mini Dow Futures', exchange: 'CBOT', color: '#2962ff' },
  RTY: { name: 'E-mini Russell 2000 Futures', exchange: 'CME', color: '#2962ff' },
  ZB: { name: 'U.S. Treasury Bond Futures', exchange: 'CBOT', color: '#089981' },
  ZN: { name: '10-Year T-Note Futures', exchange: 'CBOT', color: '#089981' },
  ZC: { name: 'Corn Futures', exchange: 'CBOT', color: '#d4a017' },
  ZS: { name: 'Soybean Futures', exchange: 'CBOT', color: '#7c9a3a' },
  ZW: { name: 'Wheat Futures', exchange: 'CBOT', color: '#d4a017' },
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
  const root = q.replace(/=F$/, '1!').replace(/1!$/, '');
  const fut = FUTURES[root];
  if (fut && /^[A-Z]{1,3}(=F|1!)?$/.test(q)) {
    hits.push({
      symbol: `${root}=F`,
      display: root,
      name: fut.name,
      exchange: fut.exchange,
      type: 'futures',
      color: fut.color,
    });
  }
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
