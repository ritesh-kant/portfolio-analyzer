'use client';

import type { SignalResponse } from '../../lib/signals-api';
import { ConfidenceMeter } from './ConfidenceMeter';

interface SignalCardProps {
  signal: SignalResponse;
}

const SIGNAL_STYLES = {
  BUY: {
    badge: 'bg-green-100 text-green-800 border border-green-200',
    dot: 'bg-green-500',
  },
  SELL: {
    badge: 'bg-red-100 text-red-800 border border-red-200',
    dot: 'bg-red-500',
  },
  HOLD: {
    badge: 'bg-gray-100 text-gray-700 border border-gray-200',
    dot: 'bg-gray-400',
  },
};

export function SignalCard({ signal }: SignalCardProps) {
  const styles = SIGNAL_STYLES[signal.signal];
  const { estimatedMovePercent: move, stopLoss } = signal;

  return (
    <div className="metric-chip space-y-5">
      {/* Header row */}
      <div className="flex items-center gap-4">
        <span className={`rounded-xl px-5 py-2 text-2xl font-bold tracking-wide ${styles.badge}`}>
          {signal.signal}
        </span>
        <div className="flex-1">
          <ConfidenceMeter confidence={signal.confidence} />
        </div>
      </div>

      {/* Move & horizon */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <div>
          <p className="text-xs text-ink/50">Estimated Move</p>
          <p className="font-semibold">
            {move.direction === 'up' ? '+' : '-'}
            {move.low}% – {move.direction === 'up' ? '+' : '-'}
            {move.high}%
          </p>
        </div>
        <div>
          <p className="text-xs text-ink/50">Time Horizon</p>
          <p className="font-semibold capitalize">{signal.timeHorizon}</p>
        </div>
        <div>
          <p className="text-xs text-ink/50">Position Size</p>
          <p className="font-semibold">{signal.positionSizeSuggestion}</p>
        </div>
        <div>
          <p className="text-xs text-ink/50">Stop-Loss</p>
          <p className="font-semibold">
            ₹{stopLoss.price.toFixed(2)}{' '}
            <span className="text-xs text-red-500">(-{stopLoss.percentFromCurrent}%)</span>
          </p>
        </div>
      </div>

      {/* Reasoning */}
      <div className="rounded-lg bg-black/[0.03] p-3 text-sm leading-relaxed text-ink/80">
        {signal.reasoning}
      </div>

      {/* Risk warning */}
      <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
        ⚠ {signal.riskWarning}
      </div>

      {/* Source availability */}
      {signal.sources && (
        <div className="flex flex-wrap gap-2 text-[10px]">
          {Object.entries(signal.sources).map(([key, ok]) => (
            <span
              key={key}
              className={`rounded-full px-2 py-0.5 font-medium ${ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-600 line-through'}`}
            >
              {key}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
