import YahooFinance from 'yahoo-finance2';
import { json } from '../lib/http.js';
import { computeTechnicals } from '../lib/technicalCalculator.js';

const yahooFinance = new YahooFinance({
  validation: { logErrors: false },
  suppressNotices: ['yahooSurvey'],
});

function sleep(ms: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, ms));
}

async function fetchHistoricalWithRetry(ticker: string, period1: string, maxAttempts = 3) {
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await yahooFinance.historical(ticker, { period1, interval: '1d' });
    } catch (err) {
      const msg = String(err);
      const isRateLimit =
        msg.includes('Too Many Requests') ||
        msg.includes('429') ||
        msg.includes('rate') ||
        msg.includes('Unexpected token');
      if (isRateLimit && attempt < maxAttempts) {
        await sleep(attempt * 1500); // 1.5s, 3s between retries
        continue;
      }
      throw err;
    }
  }
  throw new Error('All retry attempts exhausted');
}

export async function handler(event: {
  queryStringParameters?: Record<string, string | undefined> | null;
}): Promise<ReturnType<typeof json>> {
  const qs = event.queryStringParameters ?? {};
  const symbol = qs.symbol?.toUpperCase();
  const exchange = qs.exchange?.toUpperCase() ?? 'NSE';

  if (!symbol) {
    return json(400, { error: 'symbol query param is required' });
  }

  const suffix = exchange === 'BSE' ? '.BO' : '.NS';
  const ticker = `${symbol}${suffix}`;

  const fallback = (error: string) =>
    json(200, {
      symbol,
      currentPrice: 0,
      rsi: { value: null, interpretation: 'Data unavailable' },
      macd: { value: null, interpretation: 'Data unavailable' },
      bollingerBands: { value: null, interpretation: 'Data unavailable' },
      ema20: { value: null, interpretation: 'Data unavailable' },
      ema50: { value: null, interpretation: 'Data unavailable' },
      volumeRatio: { value: null, interpretation: 'Data unavailable' },
      error,
    });

  try {
    const period1 = new Date();
    period1.setDate(period1.getDate() - 120);
    const period1Str = period1.toISOString().slice(0, 10); // 'YYYY-MM-DD' — v3 rejects Date objects

    const historical = await fetchHistoricalWithRetry(ticker, period1Str);

    if (historical.length < 30) {
      return fallback(`Only ${historical.length} days of data — insufficient for indicators`);
    }

    const closes = historical.map((d) => d.close);
    const volumes = historical.map((d) => d.volume ?? 0);
    const highs = historical.map((d) => d.high);
    const lows = historical.map((d) => d.low);

    const result = computeTechnicals(symbol, closes, volumes, highs, lows);
    return json(200, result);
  } catch (err) {
    return fallback(String(err));
  }
}

