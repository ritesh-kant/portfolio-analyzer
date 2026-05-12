import { ZerodhaAdapter } from '@portfolio-analyzer/broker-sdk';

import { json } from '../lib/http.js';
import { loadHoldings, saveHoldings } from '../lib/persistence.js';

export async function handler() {
  const apiKey = process.env.ZERODHA_API_KEY;
  const accessToken = process.env.ZERODHA_ACCESS_TOKEN;

  if (!apiKey || !accessToken) {
    const { holdings, mode } = await loadHoldings();
    return json(200, {
      source: holdings.length > 0 ? 'imported' : 'mock',
      storage: mode,
      holdings,
      message: holdings.length === 0
        ? 'No holdings found. Import a Groww CSV or set ZERODHA_API_KEY + ZERODHA_ACCESS_TOKEN.'
        : `${holdings.length} holdings loaded from last import.`,
    });
  }

  const adapter = new ZerodhaAdapter({ apiKey, accessToken });
  const holdings = await adapter.getHoldings();
  const mode = await saveHoldings('zerodha', holdings);

  return json(200, {
    source: 'zerodha',
    storage: mode,
    holdings,
  });
}
