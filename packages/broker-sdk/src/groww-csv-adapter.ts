import type { Holding } from '@portfolio-analyzer/shared-types';

import type { BrokerAdapter } from './types.js';

interface GrowwRow {
  symbol: string;
  quantity: number;
  investedAmount: number;
  currentValue: number;
  sector: string;
}

export class GrowwCsvAdapter implements BrokerAdapter {
  constructor(private readonly csv: string) {}

  async getHoldings(): Promise<Holding[]> {
    const rows = parseGrowwCsv(this.csv);

    return rows.map((row) => {
      const averagePrice = row.quantity > 0 ? row.investedAmount / row.quantity : 0;
      const currentPrice = row.quantity > 0 ? row.currentValue / row.quantity : 0;

      return {
        symbol: row.symbol,
        quantity: row.quantity,
        averagePrice,
        currentPrice,
        investedAmount: row.investedAmount,
        currentValue: row.currentValue,
        sector: row.sector,
        assetType: 'mf',
        broker: 'groww',
        asOf: new Date().toISOString(),
      };
    });
  }
}

/**
 * Parses the official Groww mutual fund CSV export.
 *
 * The file has a multi-section preamble (Personal Details, Holding Summary)
 * before the actual holdings table whose header row starts with "Scheme Name".
 * Multiple folios for the same scheme are aggregated into a single holding.
 */
export function parseGrowwCsv(csv: string): GrowwRow[] {
  const lines = csv.split(/\r?\n/);

  // Find the row that starts the holdings table
  const headerIdx = lines.findIndex((line) =>
    line.trim().toLowerCase().startsWith('scheme name'),
  );

  if (headerIdx === -1) {
    throw new Error(
      'Unrecognised Groww CSV format — expected a header row starting with "Scheme Name". ' +
        'Please export your holdings from Groww → Portfolio → Download.',
    );
  }

  const headers = (lines[headerIdx] ?? '').split(',').map((h) => h.trim().toLowerCase());

  const idx = {
    schemeName: headers.indexOf('scheme name'),
    category: headers.indexOf('category'),
    units: headers.indexOf('units'),
    investedValue: headers.indexOf('invested value'),
    currentValue: headers.indexOf('current value'),
  };

  if (idx.schemeName < 0 || idx.units < 0 || idx.investedValue < 0 || idx.currentValue < 0) {
    throw new Error(
      'Groww CSV is missing required columns: Scheme Name, Units, Invested Value, Current Value.',
    );
  }

  // Collect data rows — skip blank lines and the empty separator rows
  const dataLines = lines.slice(headerIdx + 1).filter((line) => {
    const stripped = line.replace(/,/g, '').trim();
    return stripped.length > 0;
  });

  // Aggregate multiple folios of the same scheme into one holding
  const byScheme = new Map<string, GrowwRow>();

  for (const line of dataLines) {
    const cols = line.split(',').map((c) => c.trim());
    const schemeName = cols[idx.schemeName] ?? '';

    if (!schemeName) continue;

    const units = Number(cols[idx.units] ?? '0');
    const investedValue = Number(cols[idx.investedValue] ?? '0');
    const currentVal = Number(cols[idx.currentValue] ?? '0');
    const category = idx.category >= 0 ? (cols[idx.category] ?? 'Other') : 'Other';

    if (!isFinite(units) || !isFinite(investedValue) || !isFinite(currentVal)) continue;

    const existing = byScheme.get(schemeName);
    if (existing) {
      existing.quantity += units;
      existing.investedAmount += investedValue;
      existing.currentValue += currentVal;
    } else {
      byScheme.set(schemeName, {
        symbol: schemeName,
        quantity: units,
        investedAmount: investedValue,
        currentValue: currentVal,
        sector: category,
      });
    }
  }

  return [...byScheme.values()];
}
