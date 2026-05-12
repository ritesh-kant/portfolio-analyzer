import type { Transaction } from '@portfolio-analyzer/shared-types';
import mongoose from 'mongoose';

const MONGODB_URI = process.env.MONGODB_URI;

interface TransactionDoc {
  userKey: string;
  symbol: string;
  quantity: number;
  price: number;
  side: 'buy' | 'sell';
  executedAt: string;
  broker: 'zerodha' | 'groww';
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

export async function loadTransactionsFromMongo(userKey = 'local-user'): Promise<Transaction[]> {
  await connectMongo();

  const db = mongoose.connection.db;
  if (!db) {
    throw new Error('MongoDB connection is not initialized');
  }

  const docs = await db
    .collection<TransactionDoc>('transactions')
    .find({ userKey })
    .sort({ executedAt: 1 })
    .toArray();

  return docs.map(toTransaction);
}
