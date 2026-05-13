import YahooFinance from 'yahoo-finance2';
import { json } from '../lib/http.js';
import type { MacroIndicator } from '../lib/types.js';

const yahooFinance = new YahooFinance();

const MACRO_SYMBOLS: { symbol: string; label: string; context: string }[] = [
  { symbol: 'USDINR=X', label: 'USD/INR', context: 'Weaker INR is inflationary and headwind for FII inflows' },
  { symbol: 'CL=F', label: 'Crude Oil (WTI)', context: 'Higher crude raises import costs, pressures CAD and inflation' },
  { symbol: '^NSEI', label: 'Nifty 50', context: 'Benchmark Indian index — market direction' },
  { symbol: '^DJI', label: 'Dow Jones', context: 'Global risk sentiment indicator' },
  { symbol: '^N225', label: 'Nikkei 225', context: 'Asian market sentiment' },
];

export async function handler(): Promise<ReturnType<typeof json>> {
  try {
    const symbols = MACRO_SYMBOLS.map((m) => m.symbol);
    const quotes = await yahooFinance.quote(symbols);

    const quotesArr = Array.isArray(quotes) ? quotes : [quotes];
    const quoteMap = new Map(quotesArr.map((q) => [q.symbol, q]));

    const indicators: MacroIndicator[] = MACRO_SYMBOLS.map(({ symbol, label, context }) => {
      const q = quoteMap.get(symbol);
      return {
        symbol,
        label,
        value: q?.regularMarketPrice ?? 0,
        changePercent: q?.regularMarketChangePercent ?? 0,
        context,
      };
    });

    return json(200, { indicators });
  } catch (err) {
    return json(200, { indicators: [], error: `Macro data fetch failed: ${String(err)}` });
  }
}
