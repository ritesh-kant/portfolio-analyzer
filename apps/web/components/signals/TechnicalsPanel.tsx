'use client';

import React, { useState } from 'react';
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

const DESCRIPTIONS: Record<string, string> = {
  rsi: "Measures how fast and strongly a stock price is moving.\n\nRange: 0 to 100\n• Above 70 → stock may be overbought (price went up too fast)\n• Below 30 → stock may be oversold (price fell too fast)",
  macd: "Shows the difference between two moving averages.\n\n• Above 0 → bullish momentum (price trending up)\n• Below 0 → bearish momentum (price trending down)",
  bollingerBands: "Shows where the current price is relative to its recent average range.\n\n• 100% → upper band (potentially overextended)\n• 0% → lower band (potentially undervalued)",
  ema20: "The 20-day Exponential Moving Average. Short-term trend indicator.\n\n• Price above EMA 20 usually suggests a short-term uptrend.",
  ema50: "The 50-day Exponential Moving Average. Medium-term trend indicator.\n\n• Often acts as a support or resistance level for the stock.",
  volumeRatio: "Compares today's volume to the average volume.\n\n• Above 1.0 → higher than usual trading activity, suggesting strong interest in the stock.",
 };

export function TechnicalsPanel({ data }: TechnicalsPanelProps) {
  const [infoKey, setInfoKey] = useState<string | null>(null);

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
                const isExpanded = infoKey === key;

                return (
                  <React.Fragment key={key}>
                    <tr className="border-b border-black/[0.04] last:border-0">
                      <td className="px-4 py-2 font-medium text-ink/80">
                        <div className="flex items-center gap-1">
                          {label}
                          <button
                            onClick={() => setInfoKey(isExpanded ? null : key)}
                            className={`rounded-full p-0.5 transition-colors hover:bg-black/5 ${
                              isExpanded ? 'text-accent' : 'text-ink/30'
                            }`}
                            title="Show info"
                          >
                            <svg
                              xmlns="http://www.w3.org/2000/svg"
                              width="14"
                              height="14"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            >
                              <circle cx="12" cy="12" r="10" />
                              <path d="M12 16v-4" />
                              <path d="M12 8h.01" />
                            </svg>
                          </button>
                        </div>
                      </td>
                      <td className="px-4 py-2 text-right font-mono text-ink">
                        {ind.value !== null ? ind.value : '—'}
                      </td>
                      <td className="px-4 py-2 text-xs text-ink/60">{ind.interpretation}</td>
                    </tr>
                    {isExpanded && (
                      <tr className="bg-accent/5">
                        <td colSpan={3} className="px-4 py-3 text-xs leading-relaxed text-ink/80">
                          <div className="whitespace-pre-line">{DESCRIPTIONS[key]}</div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </details>
  );
}
