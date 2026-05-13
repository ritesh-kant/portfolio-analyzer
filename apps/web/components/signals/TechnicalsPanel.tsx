'use client';

import type { TechnicalsResponse } from '../../lib/signals-api';

interface TechnicalsPanelProps {
  data: TechnicalsResponse | null;
}

const ROWS: { key: keyof Omit<TechnicalsResponse, 'symbol' | 'currentPrice' | 'error'>; label: string }[] = [
  { key: 'rsi', label: 'RSI (14)' },
  { key: 'macd', label: 'MACD Histogram' },
  { key: 'bollingerBands', label: 'Bollinger Position' },
  { key: 'ema20', label: 'EMA 20' },
  { key: 'ema50', label: 'EMA 50' },
  { key: 'volumeRatio', label: 'Volume Ratio' },
];

export function TechnicalsPanel({ data }: TechnicalsPanelProps) {
  return (
    <details className="group">
      <summary className="cursor-pointer select-none list-none py-2 text-sm font-semibold text-ink/70 hover:text-ink">
        <span className="mr-1 inline-block transition-transform group-open:rotate-90">▶</span>
        Technical Indicators
        {data?.currentPrice ? (
          <span className="ml-2 text-xs font-normal text-ink/50">
            Current: ₹{data.currentPrice.toFixed(2)}
          </span>
        ) : null}
      </summary>

      <div className="mt-2 overflow-x-auto rounded-xl border border-black/5">
        {!data ? (
          <p className="p-4 text-sm text-ink/50">Technicals unavailable</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-black/5 bg-black/[0.02]">
                <th className="px-4 py-2 text-left text-xs font-semibold text-ink/50">Indicator</th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-ink/50">Value</th>
                <th className="px-4 py-2 text-left text-xs font-semibold text-ink/50">Reading</th>
              </tr>
            </thead>
            <tbody>
              {ROWS.map(({ key, label }) => {
                const ind = data[key];
                return (
                  <tr key={key} className="border-b border-black/[0.04] last:border-0">
                    <td className="px-4 py-2 font-medium text-ink/80">{label}</td>
                    <td className="px-4 py-2 text-right font-mono text-ink">
                      {ind.value !== null ? ind.value : '—'}
                    </td>
                    <td className="px-4 py-2 text-xs text-ink/60">{ind.interpretation}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </details>
  );
}
