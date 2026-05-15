import mongoose from 'mongoose';
import { TradingDb, ensureIndexes } from '@portfolio-analyzer/db';

const MONGODB_URI = process.env.MONGODB_URI;

let connectionPromise: Promise<typeof mongoose> | null = null;

async function connectMongo(): Promise<typeof mongoose> {
  if (!MONGODB_URI) {
    throw new Error('MONGODB_URI env var is not set');
  }

  if (mongoose.connection.readyState === 1) {
    return mongoose;
  }

  if (!connectionPromise) {
    connectionPromise = mongoose.connect(MONGODB_URI, {
      serverSelectionTimeoutMS: 3000,
    });
  }

  try {
    const conn = await connectionPromise;
    return conn;
  } catch (error) {
    connectionPromise = null;
    throw error;
  }
}

export async function getTradingDb(): Promise<TradingDb> {
  await connectMongo();
  const db = mongoose.connection.db;
  if (!db) throw new Error('MongoDB db not available after connect');
  return new TradingDb(db);
}

export async function getTradingDbWithIndexes(): Promise<TradingDb> {
  const tdb = await getTradingDb();
  await ensureIndexes(mongoose.connection.db!);
  return tdb;
}
