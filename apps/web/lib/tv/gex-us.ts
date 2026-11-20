import { mapPool, optionChain } from '../screener/yahoo.ts';
import {
  computeLevels,
  type GexConvention,
  type GexResponse,
  paritySpot,
  thinOpenInterest,
  usExpiryInstant,
  usRows,
} from './gex.ts';

/**
 * US gamma levels from Yahoo's option chains (~15 minutes delayed), as a snapshot of *current* open interest.
 * Server only (Yahoo session cookie + crumb). Shared by `/api/chart/gex` and the snapshot recorder
 * (`pnpm bt:gex`, research/backtests/gex_snapshot.mjs), so the chart and the recorded history use the same maths.
 *
 * Futures have no options on Yahoo, so ES/NQ/RTY/GC/SI use their index or ETF (`US_PROXY`) and the levels are
 * scaled onto the future's price, the way MenthorQ "converts" SPX levels to ES.
 */

/** Chart symbol → the Yahoo chain its levels come from, when it is not its own. */
export const US_PROXY: Record<string, string> = {
  '^GSPC': '^SPX',
  'ES=F': '^SPX',
  'MES=F': '^SPX',
  // NDX / RUT options barely have bid/ask quotes on Yahoo; QQQ and IWM are liquid and track them.
  'NQ=F': 'QQQ',
  'MNQ=F': 'QQQ',
  'RTY=F': 'IWM',
  'M2K=F': 'IWM',
  'YM=F': 'DIA',
  'MYM=F': 'DIA',
  'GC=F': 'GLD',
  'MGC=F': 'GLD',
  'SI=F': 'SLV',
};
/** At most this many expiries per chain: SPX lists one every trading day, and each is a request. */
const MAX_US_EXPIRIES = 20;

export const noGex = (reason: string): GexResponse => ({ supported: false, reason });

/** `usLevels`' reason when the chain has no open interest yet (pre-market); the route then serves the last good one. */
export const NO_OPEN_INTEREST =
  'Yahoo shows no open interest before the US open, so no levels until 9:30 ET';

/** Last price of a chart symbol (the chart endpoint needs no session). */
async function yahooPrice(symbol: string): Promise<number | undefined> {
  const res = await fetch(
    `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=1d&interval=1d`,
    { headers: { 'User-Agent': 'Mozilla/5.0' }, cache: 'no-store' },
  );
  if (!res.ok) return undefined;
  const body = (await res.json()) as {
    chart?: { result?: { meta?: { regularMarketPrice?: number } }[] };
  };
  return body.chart?.result?.[0]?.meta?.regularMarketPrice;
}

export async function usLevels(symbol: string, days: number, convention: GexConvention): Promise<GexResponse> {
  if (symbol.endsWith('=F') && !US_PROXY[symbol]) {
    return noGex(`no options source for ${symbol}: Yahoo has no options on futures`);
  }
  const chainSymbol = US_PROXY[symbol] ?? symbol;
  const first = await optionChain(chainSymbol);
  if (!first || !first.expirationDates?.length) return noGex(`${chainSymbol} has no listed options`);
  const quoteSpot = first.quote?.regularMarketPrice;
  if (!quoteSpot) return noGex(`no price for ${chainSymbol}`);

  const now = Date.now() / 1000;
  // The quote can be yesterday's close while the options already trade off pre-market: take the
  // price the nearest expiry's quotes imply (put–call parity), falling back to the quote.
  const front = first.options[0];
  const spot =
    (front &&
      paritySpot(front.calls, front.puts, usExpiryInstant(front.expirationDate), quoteSpot, now)) ||
    quoteSpot;
  const horizon = now + days * 86_400;
  const dates = first.expirationDates
    .filter((d) => usExpiryInstant(d) > now && usExpiryInstant(d) <= horizon)
    .slice(0, MAX_US_EXPIRIES);
  if (!dates.length) return noGex('no live expiries in the window');

  const tally = { mid: 0, last: 0, yahoo: 0 };
  let listed = 0;
  let withOi = 0;
  const chunks = await mapPool(dates, 4, async (d) => {
    const chain = first.options[0]?.expirationDate === d ? first : await optionChain(chainSymbol, d);
    const leg = chain?.options[0];
    if (!leg) return [];
    for (const c of [...leg.calls, ...leg.puts]) {
      listed += 1;
      if ((c.openInterest ?? 0) > 0) withOi += 1;
    }
    return usRows(leg.calls, leg.puts, usExpiryInstant(d), spot, now, tally);
  });
  if (thinOpenInterest(listed, withOi)) return noGex(NO_OPEN_INTEREST);
  const rows = chunks.flat();
  if (!rows.length) return noGex('empty option chain');

  const chartPrice = chainSymbol === symbol ? undefined : await yahooPrice(symbol);
  return {
    supported: true,
    source: 'yahoo',
    currency: 'USD',
    symbol,
    asOf: Math.floor(now),
    spot,
    ...(chainSymbol !== symbol ? { proxy: chainSymbol } : {}),
    ...(chartPrice ? { futurePrice: chartPrice, scale: chartPrice / spot } : {}),
    convention,
    expiries: dates.map((d) => new Date(d * 1000).toISOString().slice(0, 10)),
    ivSources: tally,
    all: computeLevels(rows, spot, now, convention),
    nearest: computeLevels(chunks[0] ?? [], spot, now, convention),
  };
}
