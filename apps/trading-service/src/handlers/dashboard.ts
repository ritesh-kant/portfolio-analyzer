import { json } from '../lib/http.js';
import { requireAuth } from '../lib/auth.js';
import { getTradingDb } from '../lib/db.js';

export const handler = requireAuth(async (): Promise<ReturnType<typeof json>> => {
  try {
    const db = await getTradingDb();
    const [latestRun, signals, openOrders, portfolio, recentNews] = await Promise.all([
      db.pipelineRuns().findOne({}, { sort: { started_at: -1 } }),
      db.tradingSignals().find({}).sort({ createdAt: -1 }).limit(10).toArray(),
      db.paperOrders().find({ status: 'OPEN' }).toArray(),
      db.virtualPortfolio().findOne({ portfolio_id: 'main' }),
      db.newsArticles().find({}).sort({ createdAt: -1 }).limit(10).toArray(),
    ]);

    return json(200, { latest_run: latestRun, signals, open_orders: openOrders, portfolio, recent_news: recentNews });
  } catch (error) {
    return json(500, { error: 'Failed to fetch dashboard', detail: error instanceof Error ? error.message : String(error) });
  }
});
