import { GrowwCsvAdapter } from '@portfolio-analyzer/broker-sdk';

import { json } from '../lib/http.js';
import { saveHoldings } from '../lib/persistence.js';

export async function handler(event: { body?: string }) {
  if (!event.body) {
    return json(400, { error: 'CSV payload expected in request body' });
  }

  const adapter = new GrowwCsvAdapter(event.body);
  const holdings = await adapter.getHoldings();

  const mode = await saveHoldings('groww_csv', holdings);

  return json(200, {
    source: 'groww_csv',
    storage: mode,
    count: holdings.length,
    holdings,
  });
}
