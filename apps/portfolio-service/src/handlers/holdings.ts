import { ZerodhaAdapter } from '@portfolio-analyzer/broker-sdk';

import { json } from '../lib/http.js';
import { getHoldings, setHoldings } from '../lib/store.js';

export async function handler() {
  const apiKey = process.env.ZERODHA_API_KEY;
  const accessToken = process.env.ZERODHA_ACCESS_TOKEN;

  if (!apiKey || !accessToken) {
    const cached = getHoldings();
    return json(200, {
      source: cached.length > 0 ? 'imported' : 'mock',
      holdings: cached,
      message: cached.length === 0
        ? 'No holdings found. Import a Groww CSV or set ZERODHA_API_KEY + ZERODHA_ACCESS_TOKEN.'
        : `${cached.length} holdings loaded from last import.`,
    });
  }

  const adapter = new ZerodhaAdapter({ apiKey, accessToken });
  const holdings = await adapter.getHoldings();
  setHoldings(holdings);

  return json(200, {
    source: 'zerodha',
    holdings,
  });
}
