import { json } from '../lib/http.js';

export async function handler(): Promise<ReturnType<typeof json>> {
  return json(200, {
    status: 'ok',
    service: 'trading-service',
    version: '0.1.0',
    timestamp: new Date().toISOString(),
  });
}
