import { config as loadEnv } from 'dotenv';
import path from 'path';

// Load .env from root directory
const rootDir = path.resolve(__dirname, '../../../..');
loadEnv({ path: path.join(rootDir, '.env') });

import type { Holding, Transaction } from '@portfolio-analyzer/shared-types';
import mongoose from 'mongoose';

import {
  getHoldings as getMemoryHoldings,
  getTransactions as getMemoryTransactions,
  setHoldings as setMemoryHoldings,
  setTransactions as setMemoryTransactions,
} from './store.js';

const MONGODB_URI = process.env.MONGODB_URI;

interface HoldingDoc {
  userKey: string;
  symbol: string;
  quantity: number;
  averagePrice: number;
  currentPrice: number;
  investedAmount: number;
  currentValue: number;
  sector?: string;
  assetType: 'stock' | 'mf' | 'etf';
  broker: 'zerodha' | 'groww';
  asOf: string;
  importedAt: Date;
  source: 'zerodha' | 'groww_csv';
}

interface SnapshotDoc {
  userKey: string;
  investedAmount: number;
  currentValue: number;
  pnl: number;
  pnlPct: number;
  source: 'zerodha' | 'groww_csv';
  capturedAt: Date;
}

export interface SnapshotPoint {
  date: string;
  portfolio: number;
}

interface TransactionDoc {
  userKey: string;
  symbol: string;
  quantity: number;
  price: number;
  side: 'buy' | 'sell';
  executedAt: string;
  broker: 'zerodha' | 'groww';
  importedAt: Date;
  source: 'zerodha' | 'groww_csv' | 'manual_csv';
}

let connectionPromise: Promise<typeof mongoose> | null = null;

async function connectMongo(): Promise<typeof mongoose> {
  if (!MONGODB_URI) {
    throw new Error('MONGODB_URI is not set');
  }

  if (mongoose.connection.readyState === 1) {
    return mongoose;
  }

  if (!connectionPromise) {
    connectionPromise = mongoose.connect(MONGODB_URI, {
      serverSelectionTimeoutMS: 3000,
    });
  }

  return connectionPromise;
}

function toHolding(doc: HoldingDoc): Holding {
  return {
    symbol: doc.symbol,
    quantity: doc.quantity,
    averagePrice: doc.averagePrice,
    currentPrice: doc.currentPrice,
    investedAmount: doc.investedAmount,
    currentValue: doc.currentValue,
    sector: doc.sector,
    assetType: doc.assetType,
    broker: doc.broker,
    asOf: doc.asOf,
  };
}

function holdingCollection() {
  const db = mongoose.connection.db;
  if (!db) {
    throw new Error('MongoDB connection is not initialized');
  }
  return db.collection<HoldingDoc>('holdings');
}

function snapshotCollection() {
  const db = mongoose.connection.db;
  if (!db) {
    throw new Error('MongoDB connection is not initialized');
  }
  return db.collection<SnapshotDoc>('portfolio_snapshots');
}

function transactionCollection() {
  const db = mongoose.connection.db;
  if (!db) {
    throw new Error('MongoDB connection is not initialized');
  }
  return db.collection<TransactionDoc>('transactions');
}

function calcSummary(holdings: Holding[]) {
  const investedAmount = holdings.reduce((sum, h) => sum + h.investedAmount, 0);
  const currentValue = holdings.reduce((sum, h) => sum + h.currentValue, 0);
  const pnl = currentValue - investedAmount;
  const pnlPct = investedAmount === 0 ? 0 : (pnl / investedAmount) * 100;

  return {
    investedAmount: Math.round(investedAmount),
    currentValue: Math.round(currentValue),
    pnl: Math.round(pnl),
    pnlPct: Math.round(pnlPct * 100) / 100,
  };
}

function toTransaction(doc: TransactionDoc): Transaction {
  return {
    symbol: doc.symbol,
    quantity: doc.quantity,
    price: doc.price,
    side: doc.side,
    executedAt: doc.executedAt,
    broker: doc.broker,
  };
}

export async function saveHoldings(
  source: 'zerodha' | 'groww_csv',
  holdings: Holding[],
  userKey = 'local-user',
): Promise<'mongo' | 'memory'> {
  const importedAt = new Date();

  try {
    await connectMongo();

    await holdingCollection().deleteMany({ userKey });

    if (holdings.length > 0) {
      await holdingCollection().insertMany(
        holdings.map((h) => ({
          userKey,
          ...h,
          importedAt,
          source,
        })),
      );

      const summary = calcSummary(holdings);
      await snapshotCollection().insertOne({
        userKey,
        ...summary,
        source,
        capturedAt: importedAt,
      });
    }

    return 'mongo';
  } catch (error) {
    console.error('[Persistence] Failed to save to MongoDB:', error);
    console.error('[Persistence] MONGODB_URI:', process.env.MONGODB_URI ? 'SET' : 'NOT SET');
    setMemoryHoldings(holdings);
    return 'memory';
  }
}

export async function loadHoldings(userKey = 'local-user'): Promise<{
  holdings: Holding[];
  mode: 'mongo' | 'memory';
}> {
  try {
    await connectMongo();
    const docs = await holdingCollection().find({ userKey }).toArray();
    return {
      holdings: docs.map(toHolding),
      mode: 'mongo',
    };
  } catch {
    return {
      holdings: getMemoryHoldings(),
      mode: 'memory',
    };
  }
}

export async function saveTransactions(
  source: 'zerodha' | 'groww_csv' | 'manual_csv',
  transactions: Transaction[],
  userKey = 'local-user',
): Promise<'mongo' | 'memory'> {
  const importedAt = new Date();

  try {
    await connectMongo();
    await transactionCollection().deleteMany({ userKey });

    if (transactions.length > 0) {
      await transactionCollection().insertMany(
        transactions.map((t) => ({
          userKey,
          ...t,
          importedAt,
          source,
        })),
      );
    }

    return 'mongo';
  } catch {
    setMemoryTransactions(transactions);
    return 'memory';
  }
}

export async function loadTransactions(userKey = 'local-user'): Promise<{
  transactions: Transaction[];
  mode: 'mongo' | 'memory';
}> {
  try {
    await connectMongo();
    const docs = await transactionCollection()
      .find({ userKey })
      .sort({ executedAt: 1 })
      .toArray();

    return {
      transactions: docs.map(toTransaction),
      mode: 'mongo',
    };
  } catch {
    return {
      transactions: getMemoryTransactions(),
      mode: 'memory',
    };
  }
}

export async function loadSnapshots(userKey = 'local-user'): Promise<{
  points: SnapshotPoint[];
  mode: 'mongo' | 'memory';
}> {
  try {
    await connectMongo();

    const docs = await snapshotCollection()
      .find({ userKey })
      .sort({ capturedAt: 1 })
      .toArray();

    return {
      points: docs.map((d) => ({
        date: d.capturedAt.toISOString().slice(0, 10),
        portfolio: d.currentValue,
      })),
      mode: 'mongo',
    };
  } catch {
    return {
      points: [],
      mode: 'memory',
    };
  }
}
