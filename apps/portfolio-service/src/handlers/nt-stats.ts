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

  // Optional rolling window (?days=N): restricts the *closed/realized* metrics to
  // trades exited within the last N×24h. Open positions are always current state.
  const days = Number(event.queryStringParameters?.days);
  const closedFilter: Record<string, unknown> = { status: 'closed' };
  if (Number.isFinite(days) && days > 0) {
    closedFilter.exit_at = { $gte: new Date(Date.now() - days * 86_400_000) };
  }

  const [open, closed] = await Promise.all([
    db.collection('nt_positions').find({ status: 'open' }).toArray(),
    db.collection('nt_positions').find(closedFilter).sort({ exit_at: 1 }).toArray(),
  ]);

  const totalInvestedInr = open.reduce((s, p) => s + (p.entry_value ?? p.entry_price * p.qty), 0);

  const unrealizedPnl = open.reduce((s, p) => {
    if (p.current_price == null) return s;
    // Shorts profit when price falls; positions without direction are longs.
    const sign = p.direction === 'short' ? -1 : 1;
    return s + sign * (p.current_price - p.entry_price) * p.qty;
  }, 0);

  const wins = closed.filter((p) => (p.net_pnl ?? 0) > 0);
  const losses = closed.filter((p) => (p.net_pnl ?? 0) <= 0);

  const totalRealizedNetPnl = closed.reduce((s, p) => s + (p.net_pnl ?? 0), 0);
  const totalGrossPnl = closed.reduce((s, p) => s + (p.gross_pnl ?? 0), 0);

  // Cost aggregation — use stored breakdown when available, fallback to gross-net diff
  const totalCostsInr = closed.reduce((s, p) => {
    if (p.costs?.total != null) return s + p.costs.total;
    return s + ((p.gross_pnl ?? 0) - (p.net_pnl ?? 0));
  }, 0);

  const costBreakdown = closed.reduce(
    (acc, p) => {
      if (!p.costs) return acc;
      return {
        brokerage: acc.brokerage + (p.costs.brokerage ?? 0),
        stt: acc.stt + (p.costs.stt ?? 0),
        exchange: acc.exchange + (p.costs.exchange ?? 0),
        stamp: acc.stamp + (p.costs.stamp ?? 0),
        gst: acc.gst + (p.costs.gst ?? 0),
        slippage: acc.slippage + (p.costs.slippage ?? 0),
      };
    },
    { brokerage: 0, stt: 0, exchange: 0, stamp: 0, gst: 0, slippage: 0 },
  );
  const hasCostBreakdown = closed.some((p) => p.costs != null);

  // Avg hold time
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

  // Expectancy & profit factor
  const grossWins = wins.reduce((s, p) => s + (p.net_pnl ?? 0), 0);
  const grossLosses = losses.reduce((s, p) => s + (p.net_pnl ?? 0), 0);
  const avgWinInr = wins.length > 0 ? grossWins / wins.length : null;
  const avgLossInr = losses.length > 0 ? grossLosses / losses.length : null;
  const expectancyInr =
    closed.length > 0 && avgWinInr !== null && avgLossInr !== null
      ? (wins.length / closed.length) * avgWinInr + (losses.length / closed.length) * avgLossInr
      : null;
  const profitFactor =
    grossLosses !== 0 ? Math.round((grossWins / Math.abs(grossLosses)) * 100) / 100 : null;

  // Equity curve + max drawdown (positions already sorted by exit_at)
  let cumul = 0;
  let peak = 0;
  let maxDrawdownInr = 0;
  const equityCurve: { date: string; cumul: number }[] = [];

  for (const p of closed) {
    cumul += p.net_pnl ?? 0;
    const dateStr =
      p.exit_at instanceof Date ? p.exit_at.toISOString() : String(p.exit_at);
    equityCurve.push({ date: dateStr, cumul: Math.round(cumul * 100) / 100 });
    if (cumul > peak) peak = cumul;
    const dd = peak - cumul;
    if (dd > maxDrawdownInr) maxDrawdownInr = dd;
  }

  return json(200, {
    // Positions
    open_count: open.length,
    total_invested_inr: Math.round(totalInvestedInr),
    unrealized_pnl: Math.round(unrealizedPnl * 100) / 100,
    has_live_prices: open.some((p) => p.current_price != null),
    // Closed summary
    closed_count: closed.length,
    win_count: wins.length,
    loss_count: losses.length,
    win_rate_pct: closed.length > 0 ? Math.round((wins.length / closed.length) * 1000) / 10 : null,
    avg_hold_days: avgHoldMs > 0 ? Math.round((avgHoldMs / 86_400_000) * 10) / 10 : null,
    by_exit_reason: byExitReason,
    // P&L breakdown
    total_gross_pnl: Math.round(totalGrossPnl * 100) / 100,
    total_realized_net_pnl: Math.round(totalRealizedNetPnl * 100) / 100,
    total_costs_inr: Math.round(totalCostsInr * 100) / 100,
    cost_breakdown: hasCostBreakdown
      ? {
          brokerage: Math.round(costBreakdown.brokerage * 100) / 100,
          stt: Math.round(costBreakdown.stt * 100) / 100,
          exchange: Math.round(costBreakdown.exchange * 100) / 100,
          stamp: Math.round(costBreakdown.stamp * 100) / 100,
          gst: Math.round(costBreakdown.gst * 100) / 100,
          slippage: Math.round(costBreakdown.slippage * 100) / 100,
        }
      : null,
    // Expectancy & efficiency
    avg_win_inr: avgWinInr !== null ? Math.round(avgWinInr * 100) / 100 : null,
    avg_loss_inr: avgLossInr !== null ? Math.round(avgLossInr * 100) / 100 : null,
    expectancy_inr: expectancyInr !== null ? Math.round(expectancyInr * 100) / 100 : null,
    profit_factor: profitFactor,
    // Equity curve
    equity_curve: equityCurve,
    max_drawdown_inr: Math.round(maxDrawdownInr * 100) / 100,
  });
});
