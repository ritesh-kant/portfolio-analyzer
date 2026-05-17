import { requireAuth } from '../lib/auth.js';
import { json } from '../lib/http.js';
import { loadSnapshots } from '../lib/persistence.js';

export const handler = requireAuth(async () => {
  const { points, mode } = await loadSnapshots();

  return json(200, {
    source: points.length > 0 ? 'stored' : 'empty',
    storage: mode,
    count: points.length,
    points,
  });
});
