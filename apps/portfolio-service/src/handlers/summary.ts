import { json } from '../lib/http.js';
import { loadHoldings } from '../lib/persistence.js';

export async function handler() {
  const { holdings, mode } = await loadHoldings();

  if (holdings.length === 0) {
    // Return reasonable demo values when no holdings are imported yet
    return json(200, {
      investedAmount: 141209,
      currentValue: 160480,
      pnl: 19271,
      pnlPct: 13.64,
      source: 'demo',
      storage: mode,
    });
  }

  const investedAmount = holdings.reduce((sum, h) => sum + h.investedAmount, 0);
  const currentValue = holdings.reduce((sum, h) => sum + h.currentValue, 0);
  const pnl = currentValue - investedAmount;
  const pnlPct = investedAmount === 0 ? 0 : (pnl / investedAmount) * 100;

  return json(200, {
    investedAmount: Math.round(investedAmount),
    currentValue: Math.round(currentValue),
    pnl: Math.round(pnl),
    pnlPct: Math.round(pnlPct * 100) / 100,
    source: 'live',
    storage: mode,
    holdingsCount: holdings.length,
  });
}
