import { NseIndia } from 'stock-nse-india';
import type { Datum } from 'stock-nse-india';
import { json } from '../lib/http.js';

const nse = new NseIndia();

const INDEX_SYMBOLS = new Set(['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'NIFTYIT']);

function computeMaxPain(data: Datum[]): number {
  const strikes = [...new Set(data.map((d) => d.strikePrice))].sort((a, b) => a - b);
  let minLoss = Infinity;
  let maxPainStrike = strikes[0] ?? 0;

  for (const testStrike of strikes) {
    let totalLoss = 0;
    for (const entry of data) {
      const s = entry.strikePrice;
      const ceOi = entry.CE?.openInterest ?? 0;
      const peOi = entry.PE?.openInterest ?? 0;
      if (testStrike > s) totalLoss += (testStrike - s) * ceOi;
      if (testStrike < s) totalLoss += (s - testStrike) * peOi;
    }
    if (totalLoss < minLoss) {
      minLoss = totalLoss;
      maxPainStrike = testStrike;
    }
  }

  return maxPainStrike;
}

export async function handler(event: {
  queryStringParameters?: Record<string, string | undefined> | null;
}): Promise<ReturnType<typeof json>> {
  const symbol = (event.queryStringParameters?.symbol ?? 'NIFTY').toUpperCase();

  try {
    let entries: Datum[] = [];

    if (INDEX_SYMBOLS.has(symbol)) {
      const data = await nse.getIndexOptionChain(symbol);
      entries = data.records?.data ?? [];
    } else {
      // For equities, build Datum-like objects from EquityOptionChainItem
      const data = await nse.getEquityOptionChain(symbol);
      const strikeMap = new Map<string, Datum>();
      for (const item of data.data) {
        const key = item.strikePrice;
        if (!strikeMap.has(key)) {
          strikeMap.set(key, { strikePrice: parseFloat(key), expiryDates: item.expiryDate });
        }
        const entry = strikeMap.get(key)!;
        const optDetails: import('stock-nse-india').OptionsDetails = {
          strikePrice: parseFloat(key),
          expiryDate: item.expiryDate,
          underlying: item.underlying,
          identifier: item.identifier,
          openInterest: item.openInterest,
          changeinOpenInterest: item.changeinOpenInterest,
          pchangeinOpenInterest: item.pchangeinOpenInterest,
          totalTradedVolume: item.totalTradedVolume,
          impliedVolatility: 0,
          lastPrice: item.lastPrice,
          change: 0,
          totalBuyQuantity: 0,
          totalSellQuantity: 0,
          underlyingValue: item.underlyingValue,
          optionType: item.optionType,
        };
        if (item.optionType === 'CE') {
          entry.CE = optDetails;
        } else if (item.optionType === 'PE') {
          entry.PE = optDetails;
        }
      }
      entries = [...strikeMap.values()];
    }

    if (entries.length === 0) {
      return json(200, {
        pcr: 0,
        maxPain: 0,
        interpretation: 'No option chain data available',
        error: 'Empty option chain',
      });
    }

    const totalCallOi = entries.reduce((sum, e) => sum + (e.CE?.openInterest ?? 0), 0);
    const totalPutOi = entries.reduce((sum, e) => sum + (e.PE?.openInterest ?? 0), 0);
    const pcr = totalCallOi > 0 ? parseFloat((totalPutOi / totalCallOi).toFixed(4)) : 0;
    const maxPain = computeMaxPain(entries);

    let interpretation: string;
    if (pcr > 1.2) interpretation = `PCR ${pcr} — bullish sentiment (high put writing)`;
    else if (pcr < 0.8) interpretation = `PCR ${pcr} — bearish sentiment (high call writing)`;
    else interpretation = `PCR ${pcr} — neutral market sentiment`;

    return json(200, { pcr, maxPain, interpretation });
  } catch (err) {
    return json(200, {
      pcr: 0,
      maxPain: 0,
      interpretation: 'Options data unavailable',
      error: `NSE options fetch failed: ${String(err)}`,
    });
  }
}
