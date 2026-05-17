import YahooFinance from 'yahoo-finance2';
import { requireAuth } from '../lib/auth.js';
import { json } from '../lib/http.js';
import { computeTechnicals } from '../lib/technicalCalculator.js';

const yahooFinance = new YahooFinance({
  validation: { logErrors: false },
  suppressNotices: ['yahooSurvey'],
});

function sleep(ms: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, ms));
}

async function fetchChartWithRetry(ticker: string, period1: string, maxAttempts = 3) {
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await yahooFinance.chart(ticker, { period1, interval: '1d' });
    } catch (err) {
      const msg = String(err);
      const isRateLimit =
        msg.includes('Too Many Requests') ||
        msg.includes('429') ||
        msg.includes('rate') ||
        msg.includes('Unexpected token');
      if (isRateLimit && attempt < maxAttempts) {
        await sleep(attempt * 1500);
        continue;
      }
      throw err;
    }
  }
  throw new Error('All retry attempts exhausted');
}

export const handler = requireAuth(async (event): Promise<ReturnType<typeof json>> => {
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
    const period1Str = period1.toISOString().slice(0, 10);

    const chartResult = await fetchChartWithRetry(ticker, period1Str);
    const quotes = chartResult.quotes ?? [];

    if (quotes.length < 30) {
      return fallback(`Only ${quotes.length} days of data — insufficient for indicators`);
    }

    const closes = quotes.map((q) => q.close ?? 0).filter((v) => v > 0);
    const volumes = quotes.map((q) => q.volume ?? 0);
    const highs = quotes.map((q) => q.high ?? 0).filter((v) => v > 0);
    const lows = quotes.map((q) => q.low ?? 0).filter((v) => v > 0);

    const result = computeTechnicals(symbol, closes, volumes, highs, lows);
    return json(200, result);
  } catch (err) {
    return fallback(String(err));
  }
});
