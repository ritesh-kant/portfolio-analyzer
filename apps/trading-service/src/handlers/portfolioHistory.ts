import { json } from '../lib/http.js';
import { getTradingDb } from '../lib/db.js';

export interface PortfolioSnapshot {
  date: string;
  daily_pnl: number;
  cumulative_pnl: number;
  portfolio_value: number;
  trades: number;
  wins: number;
  win_rate_pct: number;       // cumulative win rate up to this day
  avg_return_pct: number;     // average return per closed trade up to this day
}

export async function handler(): Promise<ReturnType<typeof json>> {
  try {
    const db = await getTradingDb();

    const portfolio = await db.virtualPortfolio().findOne({ portfolio_id: 'main' });
    const initialCapital = portfolio?.initial_capital ?? 100_000;

    const closedOrders = await db
      .paperOrders()
      .find({ status: { $in: ['CLOSED', 'STOPPED'] } })
      .sort({ exit_date: 1 })
      .toArray();

    // Group realized P&L by exit date
    const dailyMap = new Map<string, { pnl: number; trades: number; wins: number; returns: number[] }>();
    for (const order of closedOrders) {
      const date = order.exit_date ?? order.date;
      const pnl = order.position_value * ((order.actual_return_pct ?? 0) / 100);
      const prev = dailyMap.get(date) ?? { pnl: 0, trades: 0, wins: 0, returns: [] };
      dailyMap.set(date, {
        pnl: prev.pnl + pnl,
        trades: prev.trades + 1,
        wins: prev.wins + (order.was_correct ? 1 : 0),
        returns: [...prev.returns, order.actual_return_pct ?? 0],
      });
    }

    let cumPnl = 0;
    let cumTrades = 0;
    let cumWins = 0;
    let cumReturns: number[] = [];

    const snapshots: PortfolioSnapshot[] = [...dailyMap.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, { pnl, trades, wins, returns }]) => {
        cumPnl += pnl;
        cumTrades += trades;
        cumWins += wins;
        cumReturns = cumReturns.concat(returns);
        const winRate = cumTrades > 0 ? Math.round((cumWins / cumTrades) * 10000) / 100 : 0;
        const avgReturn = cumReturns.length > 0
          ? Math.round((cumReturns.reduce((s, r) => s + r, 0) / cumReturns.length) * 100) / 100
          : 0;
        return {
          date,
          daily_pnl: Math.round(pnl * 100) / 100,
          cumulative_pnl: Math.round(cumPnl * 100) / 100,
          portfolio_value: Math.round((initialCapital + cumPnl) * 100) / 100,
          trades,
          wins,
          win_rate_pct: winRate,
          avg_return_pct: avgReturn,
        };
      });

    // Prepend the starting point for a clean chart
    if (snapshots.length > 0) {
      snapshots.unshift({
        date: 'Start',
        daily_pnl: 0,
        cumulative_pnl: 0,
        portfolio_value: initialCapital,
        trades: 0,
        wins: 0,
        win_rate_pct: 0,
        avg_return_pct: 0,
      });
    }

    return json(200, { snapshots, initial_capital: initialCapital });
  } catch (error) {
    return json(500, {
      error: 'Failed to fetch portfolio history',
      detail: error instanceof Error ? error.message : String(error),
    });
  }
}
