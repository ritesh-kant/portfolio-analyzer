import type { TaxDashboardSummary, Transaction } from '@portfolio-analyzer/shared-types';

interface Lot {
  quantity: number;
  price: number;
  date: Date;
}

const LTCG_EXEMPTION_LIMIT = 125000;
const LTCG_DAYS_THRESHOLD = 365;

export function calculateTaxSummary(transactions: Transaction[]): TaxDashboardSummary {
  const sorted = [...transactions].sort(
    (a, b) => new Date(a.executedAt).getTime() - new Date(b.executedAt).getTime(),
  );

  const lotsBySymbol = new Map<string, Lot[]>();
  let realizedLtcg = 0;
  let realizedStcg = 0;

  for (const tx of sorted) {
    const txDate = new Date(tx.executedAt);
    const lots = lotsBySymbol.get(tx.symbol) ?? [];

    if (tx.side === 'buy') {
      lots.push({ quantity: tx.quantity, price: tx.price, date: txDate });
      lotsBySymbol.set(tx.symbol, lots);
      continue;
    }

    let remainingSell = tx.quantity;

    while (remainingSell > 0 && lots.length > 0) {
      const lot = lots[0];
      if (!lot) {
        break;
      }
      const matchedQty = Math.min(remainingSell, lot.quantity);
      const gain = (tx.price - lot.price) * matchedQty;
      const holdingDays = daysBetween(lot.date, txDate);

      if (holdingDays > LTCG_DAYS_THRESHOLD) {
        realizedLtcg += gain;
      } else {
        realizedStcg += gain;
      }

      lot.quantity -= matchedQty;
      remainingSell -= matchedQty;

      if (lot.quantity <= 0) {
        lots.shift();
      }
    }

    lotsBySymbol.set(tx.symbol, lots);
  }

  const ltcgExemptionUsed = Math.max(0, realizedLtcg);
  const ltcgExemptionRemaining = Math.max(0, LTCG_EXEMPTION_LIMIT - ltcgExemptionUsed);

  return {
    realized: {
      ltcg: realizedLtcg,
      stcg: realizedStcg,
    },
    // TODO: Calculate unrealized P&L by comparing current holdings prices
    //  against their remaining lot cost basis.
    unrealized: {
      profits: 0,
      losses: 0,
    },
    ltcgExemptionUsed,
    ltcgExemptionLimit: LTCG_EXEMPTION_LIMIT,
    ltcgExemptionRemaining,
    // TODO: Identify harvestable lots where unrealized loss can offset LTCG.
    harvestable: [],
  };
}

function daysBetween(start: Date, end: Date): number {
  return Math.floor((end.getTime() - start.getTime()) / (1000 * 60 * 60 * 24));
}
