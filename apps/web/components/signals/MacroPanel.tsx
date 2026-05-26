'use client';

import type { MacroIndicator } from '../../lib/signals-api';

interface MacroPanelProps {
  indicators: MacroIndicator[];
}

export function MacroPanel({ indicators }: MacroPanelProps) {
  if (indicators.length === 0) {
    return (
      <div className="rounded-xl border border-black/5 bg-panel p-3 text-sm text-ink/50">
        Macro data unavailable
      </div>
    );
  }

  return (
    <div>
      <h3 className="mb-3 text-sm font-semibold text-ink/70">Macro Indicators</h3>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        {indicators.map((m) => {
          const positive = m.changePercent >= 0;
          return (
            <div key={m.symbol} className="metric-chip space-y-1" title={m.context}>
              <p className="text-xs text-ink/50">{m.label}</p>
              <p className="font-semibold">
                {m.value.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
              </p>
              <p className={`text-xs font-medium ${positive ? 'text-green-600' : 'text-red-500'}`}>
                {positive ? '+' : ''}
                {m.changePercent.toFixed(2)}%
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
