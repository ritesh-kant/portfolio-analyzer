import { requireAuth } from '../lib/auth.js';
import { json } from '../lib/http.js';
import { loadTransactions } from '../lib/persistence.js';

export const handler = requireAuth(async () => {
  const { transactions, mode } = await loadTransactions();

  return json(200, {
    source: transactions.length > 0 ? 'stored' : 'empty',
    storage: mode,
    count: transactions.length,
    transactions,
  });
});
