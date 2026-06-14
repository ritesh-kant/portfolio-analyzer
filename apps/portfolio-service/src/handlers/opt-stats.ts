import mongoose from 'mongoose';

import { requireAuth } from '../lib/auth.js';
import { json } from '../lib/http.js';
import { config } from '../lib/config.js';

async function connect() {
  if (!config.mongodbUri) throw new Error('MONGODB_URI is not set');
  if (mongoose.connection.readyState === 1) return;
  await mongoose.connect(config.mongodbUri, { serverSelectionTimeoutMS: 3000 });
}

export const handler = requireAuth(async (event) => {
  await connect();
  const db = mongoose.connection.db!;

  const days = Number(event.queryStringParameters?.days);
  const closedFilter: Record<string, unknown> = { status: 'closed' };
  if (Number.isFinite(days) && days > 0) {
    closedFilter.exit_at = { $gte: new Date(Date.now() - days * 86_400_000) };
  }

  const [open, closed] = await Promise.all([
    db.collection('opt_paper_positions').find({ status: 'open' }).toArray(),
    db.collection('opt_paper_positions').find(closedFilter).sort({ exit_at: 1 }).toArray(),
  ]);

  // Open book: unrealized P&L = entry_premium_received - current_premium_to_close
  const openPremiumReceived = open.reduce(
    (s, p) => s + (p.entry_total_prem ?? 0) * (p.lots ?? 1) * (p.lot_size ?? 1),
    0,
  );
  const openCurrentValue = open.reduce(
    (s, p) => s + (p.current_total_prem ?? 0) * (p.lots ?? 1) * (p.lot_size ?? 1),
    0,
  );
  const unrealizedPnl = Math.round((openPremiumReceived - openCurrentValue) * 100) / 100;

  const wins = closed.filter((p) => (p.gross_pnl ?? 0) > 0);
  const losses = closed.filter((p) => (p.gross_pnl ?? 0) <= 0);
  const totalGross = closed.reduce((s, p) => s + (p.gross_pnl ?? 0), 0);
  const totalNet = closed.reduce((s, p) => s + (p.net_pnl ?? 0), 0);
  const totalCosts = closed.reduce((s, p) => s + (p.costs?.total ?? 0), 0);

  // Premium stats
  const totalPremiumReceived = closed.reduce(
    (s, p) => s + (p.entry_total_prem ?? 0) * (p.lots ?? 1) * (p.lot_size ?? 1),
    0,
  );

  // Avg hold
  const avgHoldMs =
    closed.length > 0
      ? closed.reduce((s, p) => {
          if (!p.entry_at || !p.exit_at) return s;
          return s + (new Date(p.exit_at).getTime() - new Date(p.entry_at).getTime());
        }, 0) / closed.length
      : 0;

  // By exit reason
  const byExitReason: Record<string, { count: number; total_net_pnl: number }> = {};
  for (const p of closed) {
    const r = p.exit_reason ?? 'unknown';
    if (!byExitReason[r]) byExitReason[r] = { count: 0, total_net_pnl: 0 };
    byExitReason[r].count += 1;
    byExitReason[r].total_net_pnl += p.net_pnl ?? 0;
  }

  // Expectancy
  const grossWins = wins.reduce((s, p) => s + (p.net_pnl ?? 0), 0);
  const grossLosses = losses.reduce((s, p) => s + (p.net_pnl ?? 0), 0);
  const avgWin = wins.length > 0 ? grossWins / wins.length : null;
  const avgLoss = losses.length > 0 ? grossLosses / losses.length : null;
  const expectancy =
    closed.length > 0 && avgWin !== null && avgLoss !== null
      ? (wins.length / closed.length) * avgWin + (losses.length / closed.length) * avgLoss
      : null;

  // Equity curve
  let cumul = 0;
  let peak = 0;
  let maxDrawdown = 0;
  const equityCurve: { date: string; cumul: number }[] = [];
  for (const p of closed) {
    cumul += p.net_pnl ?? 0;
    const d = p.exit_at instanceof Date ? p.exit_at.toISOString() : String(p.exit_at);
    equityCurve.push({ date: d, cumul: Math.round(cumul * 100) / 100 });
    if (cumul > peak) peak = cumul;
    const dd = peak - cumul;
    if (dd > maxDrawdown) maxDrawdown = dd;
  }

  // IV source breakdown from recent chain snapshots
  const recentSnapshots = await db
    .collection('opt_chain_snapshots')
    .find({ snapshot_at: { $gte: new Date(Date.now() - 7 * 86_400_000) } })
    .limit(500)
    .toArray();
  const ivSourceCounts = recentSnapshots.reduce(
    (acc, s) => {
      const src = s.iv_source ?? 'synthetic';
      acc[src] = (acc[src] ?? 0) + 1;
      return acc;
    },
    {} as Record<string, number>,
  );

  return json(200, {
    strategy: 'straddle_sell',
    // Open book
    open_count: open.length,
    unrealized_pnl: unrealizedPnl,
    open_premium_received: Math.round(openPremiumReceived * 100) / 100,
    open_current_value: Math.round(openCurrentValue * 100) / 100,
    // Closed
    closed_count: closed.length,
    win_count: wins.length,
    loss_count: losses.length,
    win_rate_pct:
      closed.length > 0 ? Math.round((wins.length / closed.length) * 1000) / 10 : null,
    total_premium_received: Math.round(totalPremiumReceived * 100) / 100,
    total_gross_pnl: Math.round(totalGross * 100) / 100,
    total_net_pnl: Math.round(totalNet * 100) / 100,
    total_costs: Math.round(totalCosts * 100) / 100,
    avg_win_inr: avgWin !== null ? Math.round(avgWin * 100) / 100 : null,
    avg_loss_inr: avgLoss !== null ? Math.round(avgLoss * 100) / 100 : null,
    expectancy_inr: expectancy !== null ? Math.round(expectancy * 100) / 100 : null,
    avg_hold_min:
      avgHoldMs > 0 ? Math.round((avgHoldMs / 60_000) * 10) / 10 : null,
    by_exit_reason: byExitReason,
    equity_curve: equityCurve,
    max_drawdown_inr: Math.round(maxDrawdown * 100) / 100,
    // Data quality
    chain_snapshots_7d: recentSnapshots.length,
    iv_source_breakdown: ivSourceCounts,
  });
});
