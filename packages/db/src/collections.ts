import type {
  AgentDecisionDoc,
  AgentLogDoc,
  NewsArticleDoc,
  PaperOrderDoc,
  PipelineRunDoc,
  StockAnalysisDoc,
  TradingSignalDoc,
  VirtualPortfolioDoc,
} from '@portfolio-analyzer/trading-types';
import type { Db } from 'mongodb';

export const COLLECTION_NAMES = {
  NEWS_ARTICLES: 'trading_news_articles',
  STOCK_ANALYSES: 'trading_stock_analyses',
  TRADING_SIGNALS: 'trading_signals',
  PAPER_ORDERS: 'trading_paper_orders',
  VIRTUAL_PORTFOLIO: 'trading_virtual_portfolio',
  PIPELINE_RUNS: 'trading_pipeline_runs',
  AGENT_LOGS: 'trading_agent_logs',
  AGENT_DECISIONS: 'trading_agent_decisions',
} as const;

export class TradingDb {
  constructor(private readonly db: Db) {}

  newsArticles() {
    return this.db.collection<NewsArticleDoc>(COLLECTION_NAMES.NEWS_ARTICLES);
  }

  stockAnalyses() {
    return this.db.collection<StockAnalysisDoc>(COLLECTION_NAMES.STOCK_ANALYSES);
  }

  tradingSignals() {
    return this.db.collection<TradingSignalDoc>(COLLECTION_NAMES.TRADING_SIGNALS);
  }

  paperOrders() {
    return this.db.collection<PaperOrderDoc>(COLLECTION_NAMES.PAPER_ORDERS);
  }

  virtualPortfolio() {
    return this.db.collection<VirtualPortfolioDoc>(COLLECTION_NAMES.VIRTUAL_PORTFOLIO);
  }

  pipelineRuns() {
    return this.db.collection<PipelineRunDoc>(COLLECTION_NAMES.PIPELINE_RUNS);
  }

  agentLogs() {
    return this.db.collection<AgentLogDoc>(COLLECTION_NAMES.AGENT_LOGS);
  }

  agentDecisions() {
    return this.db.collection<AgentDecisionDoc>(COLLECTION_NAMES.AGENT_DECISIONS);
  }
}

export async function ensureIndexes(db: Db): Promise<void> {
  // TTL index: agent_logs expire after 30 days
  await db.collection(COLLECTION_NAMES.AGENT_LOGS).createIndex(
    { createdAt: 1 },
    { expireAfterSeconds: 2_592_000, background: true },
  );

  // Dedup index: news articles by topic_hash within 24h window
  await db.collection(COLLECTION_NAMES.NEWS_ARTICLES).createIndex(
    { topic_hash: 1, createdAt: -1 },
    { background: true },
  );

  // Lookup indexes
  await db.collection(COLLECTION_NAMES.TRADING_SIGNALS).createIndex(
    { run_id: 1, symbol: 1 },
    { background: true },
  );
  await db.collection(COLLECTION_NAMES.PAPER_ORDERS).createIndex(
    { status: 1, symbol: 1 },
    { background: true },
  );
  await db.collection(COLLECTION_NAMES.PIPELINE_RUNS).createIndex(
    { run_id: 1 },
    { unique: true, background: true },
  );
  await db.collection(COLLECTION_NAMES.PIPELINE_RUNS).createIndex(
    { status: 1, started_at: -1 },
    { background: true },
  );
}
