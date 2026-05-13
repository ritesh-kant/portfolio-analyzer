'use client';

import type { ConfluenceScore } from '../../lib/signals-api';

interface ConfluenceGridProps {
  scores: ConfluenceScore;
}

const TILE_LABELS: [keyof ConfluenceScore, string][] = [
  ['newsSentiment', 'News'],
  ['technicals', 'Technicals'],
  ['volume', 'Volume'],
  ['fiiDii', 'FII / DII'],
  ['options', 'Options'],
  ['macro', 'Macro'],
];

function ScoreIcon({ score }: { score: -1 | 0 | 1 }) {
  if (score === 1) return <span className="text-xl text-green-600">↑</span>;
  if (score === -1) return <span className="text-xl text-red-500">↓</span>;
  return <span className="text-xl text-ink/30">—</span>;
}

const TILE_BG: Record<number, string> = {
  1: 'bg-green-50 border-green-200',
  0: 'bg-panel border-black/5',
  [-1]: 'bg-red-50 border-red-200',
};

export function ConfluenceGrid({ scores }: ConfluenceGridProps) {
  return (
    <div>
      <h3 className="mb-3 text-sm font-semibold text-ink/70">Signal Confluence</h3>
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
        {TILE_LABELS.map(([key, label]) => {
          const score = scores[key];
          return (
            <div
              key={key}
              className={`flex flex-col items-center gap-1 rounded-xl border p-3 ${TILE_BG[score]}`}
            >
              <ScoreIcon score={score} />
              <span className="text-center text-xs font-medium text-ink/70">{label}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
