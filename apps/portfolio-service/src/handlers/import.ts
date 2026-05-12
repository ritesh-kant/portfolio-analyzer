import { GrowwCsvAdapter } from '@portfolio-analyzer/broker-sdk';

import { json } from '../lib/http.js';
import { setHoldings } from '../lib/store.js';

export async function handler(event: { body?: string }) {
  if (!event.body) {
    return json(400, { error: 'CSV payload expected in request body' });
  }

  const adapter = new GrowwCsvAdapter(event.body);
  const holdings = await adapter.getHoldings();

  // Persist into in-memory store so /portfolio/holdings reflects imported data
  setHoldings(holdings);

  return json(200, {
    source: 'groww_csv',
    count: holdings.length,
    holdings,
  });
}
