import { NseIndia } from 'stock-nse-india';
import { json } from '../lib/http.js';
import type { FlowData } from '../lib/types.js';

const nse = new NseIndia();

interface FiiDiiRow {
  category?: string;
  buyValue?: number;
  sellValue?: number;
  netValue?: number;
  date?: string;
}

export async function handler(): Promise<ReturnType<typeof json>> {
  try {
    const data = await nse.getDataByEndpoint('/api/fiidiiTradeReact') as unknown;
    const rows = (Array.isArray(data) ? data : []) as FiiDiiRow[];

    const toFlow = (row: FiiDiiRow | undefined): FlowData => ({
      buy: row?.buyValue ?? 0,
      sell: row?.sellValue ?? 0,
      net: row?.netValue ?? (row ? (row.buyValue ?? 0) - (row.sellValue ?? 0) : 0),
    });

    const fiiRow = rows.find(
      (r) => r.category?.toUpperCase().includes('FII') || r.category?.toUpperCase().includes('FPI'),
    );
    const diiRow = rows.find((r) => r.category?.toUpperCase().includes('DII'));

    if (!fiiRow && !diiRow) {
      return json(200, {
        fii: { buy: 0, sell: 0, net: 0 },
        dii: { buy: 0, sell: 0, net: 0 },
        date: new Date().toISOString().slice(0, 10),
        error: 'No FII/DII rows found in NSE response',
      });
    }

    const date = fiiRow?.date ?? diiRow?.date ?? new Date().toISOString().slice(0, 10);
    return json(200, { fii: toFlow(fiiRow), dii: toFlow(diiRow), date });
  } catch (err) {
    return json(200, {
      fii: { buy: 0, sell: 0, net: 0 },
      dii: { buy: 0, sell: 0, net: 0 },
      date: new Date().toISOString().slice(0, 10),
      error: `NSE FII/DII fetch failed: ${String(err)}`,
    });
  }
}
