import mongoose from 'mongoose';

import { requireAuth } from '../lib/auth.js';
import { json } from '../lib/http.js';
import { config } from '../lib/config.js';

async function connect() {
  if (!config.mongodbUri) throw new Error('MONGODB_URI is not set');
  if (mongoose.connection.readyState === 1) return;
  await mongoose.connect(config.mongodbUri, { serverSelectionTimeoutMS: 3000 });
}

export const handler = requireAuth(async () => {
  await connect();
  const db = mongoose.connection.db!;

  const [open, closed] = await Promise.all([
    db.collection('nt_positions').find({ status: 'open' }).toArray(),
    db.collection('nt_positions').find({ status: 'closed' }).toArray(),
  ]);

  const totalInvestedInr = open.reduce((s, p) => s + (p.entry_value ?? p.entry_price * p.qty), 0);

  const unrealizedPnl = open.reduce((s, p) => {
    if (p.current_price == null) return s;
    return s + (p.current_price - p.entry_price) * p.qty;
  }, 0);

  const totalRealizedNetPnl = closed.reduce((s, p) => s + (p.net_pnl ?? 0), 0);
  const wins = closed.filter((p) => (p.net_pnl ?? 0) > 0);
  const losses = closed.filter((p) => (p.net_pnl ?? 0) <= 0);

  const avgHoldMs =
    closed.length > 0
      ? closed.reduce((s, p) => {
          if (!p.entry_at || !p.exit_at) return s;
          return s + (new Date(p.exit_at).getTime() - new Date(p.entry_at).getTime());
        }, 0) / closed.length
      : 0;

  const byExitReason: Record<string, { count: number; total_net_pnl: number }> = {};
  for (const p of closed) {
    const r = p.exit_reason ?? 'unknown';
    if (!byExitReason[r]) byExitReason[r] = { count: 0, total_net_pnl: 0 };
    byExitReason[r].count += 1;
    byExitReason[r].total_net_pnl += p.net_pnl ?? 0;
  }

  return json(200, {
    open_count: open.length,
    total_invested_inr: Math.round(totalInvestedInr),
    unrealized_pnl: Math.round(unrealizedPnl * 100) / 100,
    has_live_prices: open.some((p) => p.current_price != null),
    closed_count: closed.length,
    win_count: wins.length,
    loss_count: losses.length,
    win_rate_pct: closed.length > 0 ? Math.round((wins.length / closed.length) * 1000) / 10 : null,
    total_realized_net_pnl: Math.round(totalRealizedNetPnl * 100) / 100,
    avg_hold_days: avgHoldMs > 0 ? Math.round((avgHoldMs / 86_400_000) * 10) / 10 : null,
    by_exit_reason: byExitReason,
  });
});
