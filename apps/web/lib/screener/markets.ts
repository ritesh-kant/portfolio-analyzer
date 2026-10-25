import type { FilterState } from './types.ts';

/**
 * A market is one Yahoo `region`: it fixes the currency prices are quoted in, the exchange clock that
 * "rel vol at time" is measured on, and which venues the exchange filter offers.
 */
export interface Market {
  id: string;
  label: string;
  flag: string;
  /** Yahoo screener region code. */
  region: string;
  /** IANA zone of the exchange's session. */
  tz: string;
  /** What the price columns are in. Pence for London: Yahoo quotes LSE stocks in GBp, not GBP. */
  currency: string;
  /** Venue name shown in the UI → the Yahoo `exchange` codes behind it. */
  venues: Record<string, readonly string[]>;
  /** Venues in the universe by default. Several markets list the same company on many venues; the primary one avoids duplicates. */
  defaultVenues: readonly string[];
  /** Whether "common stock only" (drop warrants, rights, units) means anything here: it is a US-listing quirk. */
  commonStockOption: boolean;
}

const m = (x: Market): Market => x;

export const MARKET_LIST: readonly Market[] = [
  m({
    id: 'us',
    label: 'USA',
    flag: '🇺🇸',
    region: 'us',
    tz: 'America/New_York',
    currency: 'USD',
    venues: {
      NASDAQ: ['NMS', 'NGM', 'NCM', 'NAS'],
      NYSE: ['NYQ', 'NYS'],
      AMEX: ['ASE', 'AMX'],
      ARCA: ['PCX'],
      BATS: ['BTS', 'BATS'],
      OTC: ['PNK', 'OQB', 'OQX', 'OEM', 'OBB', 'OID'],
    },
    defaultVenues: ['NASDAQ', 'NYSE', 'AMEX', 'ARCA', 'BATS'],
    commonStockOption: true,
  }),
  m({
    id: 'in',
    label: 'India',
    flag: '🇮🇳',
    region: 'in',
    tz: 'Asia/Kolkata',
    currency: 'INR',
    venues: { NSE: ['NSI'], BSE: ['BSE'] },
    defaultVenues: ['NSE'],
    commonStockOption: false,
  }),
  m({
    id: 'gb',
    label: 'United Kingdom',
    flag: '🇬🇧',
    region: 'gb',
    tz: 'Europe/London',
    currency: 'GBp',
    venues: { LSE: ['LSE'], 'Cboe UK': ['CXE'], Aquis: ['AQS'], 'LSE IOB': ['IOB'] },
    defaultVenues: ['LSE'],
    commonStockOption: false,
  }),
  m({
    id: 'de',
    label: 'Germany',
    flag: '🇩🇪',
    region: 'de',
    tz: 'Europe/Berlin',
    currency: 'EUR',
    venues: { XETRA: ['GER'], Frankfurt: ['FRA'], Stuttgart: ['STU'], Munich: ['MUN'] },
    defaultVenues: ['XETRA'],
    commonStockOption: false,
  }),
  m({
    id: 'ca',
    label: 'Canada',
    flag: '🇨🇦',
    region: 'ca',
    tz: 'America/Toronto',
    currency: 'CAD',
    venues: { TSX: ['TOR'], TSXV: ['VAN'], CSE: ['CNQ'], NEO: ['NEO'] },
    defaultVenues: ['TSX', 'TSXV'],
    commonStockOption: false,
  }),
  m({
    id: 'jp',
    label: 'Japan',
    flag: '🇯🇵',
    region: 'jp',
    tz: 'Asia/Tokyo',
    currency: 'JPY',
    venues: { Tokyo: ['JPX'] },
    defaultVenues: ['Tokyo'],
    commonStockOption: false,
  }),
  m({
    id: 'hk',
    label: 'Hong Kong, China',
    flag: '🇭🇰',
    region: 'hk',
    tz: 'Asia/Hong_Kong',
    currency: 'HKD',
    venues: { HKEX: ['HKG'] },
    defaultVenues: ['HKEX'],
    commonStockOption: false,
  }),
  m({
    id: 'au',
    label: 'Australia',
    flag: '🇦🇺',
    region: 'au',
    tz: 'Australia/Sydney',
    currency: 'AUD',
    venues: { ASX: ['ASX'], 'Cboe AU': ['CXA'] },
    defaultVenues: ['ASX'],
    commonStockOption: false,
  }),
];

export const DEFAULT_MARKET = 'us';
export const MARKETS: Record<string, Market> = Object.fromEntries(
  MARKET_LIST.map((x) => [x.id, x]),
);

/** Unknown / missing ids fall back to the US: saved screens from before markets existed have none. */
export const marketOf = (id: string | undefined): Market =>
  (id ? MARKETS[id] : undefined) ?? MARKETS.us!;

/** Yahoo exchange code → the venue name the UI shows ("NMS" → "NASDAQ"); unknown codes pass through. */
export function venueOf(code: string | undefined, market: Market): string {
  if (!code) return '';
  const up = code.toUpperCase();
  for (const [venue, codes] of Object.entries(market.venues)) if (codes.includes(up)) return venue;
  return up;
}

/** The universe a fresh screen in this market starts with. */
export function universeFor(market: Market): FilterState {
  return {
    exchange: { in: [...market.defaultVenues] },
    ...(market.commonStockOption ? { commonOnly: { on: true } } : {}),
  };
}

/** Filters whose numbers are in the market's currency: meaningless after switching to another market. */
export const MONEY_FILTERS: readonly string[] = ['price', 'marketCap', 'dollarVol'];
