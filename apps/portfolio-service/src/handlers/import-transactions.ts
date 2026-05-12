import type { Transaction } from '@portfolio-analyzer/shared-types';

import { json } from '../lib/http.js';
import { saveTransactions } from '../lib/persistence.js';

export async function handler(event: { body?: string }) {
  if (!event.body) {
    return json(400, { error: 'CSV payload expected in request body' });
  }

  try {
    const transactions = parseTransactionsCsv(event.body);
    const mode = await saveTransactions('manual_csv', transactions);

    return json(200, {
      source: 'manual_csv',
      storage: mode,
      count: transactions.length,
      transactions,
    });
  } catch (error) {
    return json(400, {
      error: error instanceof Error ? error.message : 'Invalid transaction CSV',
    });
  }
}

function parseTransactionsCsv(csv: string): Transaction[] {
  const [headerLine, ...lines] = csv.split(/\r?\n/).filter(Boolean);
  if (!headerLine) {
    return [];
  }

  const headers = headerLine.split(',').map((h) => h.trim().toLowerCase());
  const i = {
    symbol: headers.indexOf('symbol'),
    quantity: headers.indexOf('quantity'),
    price: headers.indexOf('price'),
    side: headers.indexOf('side'),
    executedAt: Math.max(headers.indexOf('executedat'), headers.indexOf('executed_at')),
    broker: headers.indexOf('broker'),
  };

  if (Object.values(i).some((idx) => idx < 0)) {
    throw new Error(
      'Transaction CSV must include headers: symbol,quantity,price,side,executedAt(or executed_at),broker',
    );
  }

  return lines.map((line) => {
    const cols = line.split(',').map((c) => c.trim());

    const side = cols[i.side]?.toLowerCase();
    if (side !== 'buy' && side !== 'sell') {
      throw new Error(`Invalid side value: ${cols[i.side]}`);
    }

    const broker = cols[i.broker]?.toLowerCase();
    if (broker !== 'zerodha' && broker !== 'groww') {
      throw new Error(`Invalid broker value: ${cols[i.broker]}`);
    }

    return {
      symbol: cols[i.symbol] ?? '',
      quantity: Number(cols[i.quantity] ?? '0'),
      price: Number(cols[i.price] ?? '0'),
      side,
      executedAt: new Date(cols[i.executedAt] ?? '').toISOString(),
      broker,
    };
  });
}
