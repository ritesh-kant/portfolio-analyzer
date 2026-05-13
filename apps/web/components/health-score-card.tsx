'use client';

import type { PortfolioHealthScore } from '@portfolio-analyzer/shared-types';

export function HealthScoreCard({ score }: { score: PortfolioHealthScore }) {
  const { score: total, breakdown } = score;

  const toneClass =
    total >= 70
      ? 'border-emerald-700/20 bg-emerald-50'
      : total >= 50
        ? 'border-amber-700/20 bg-amber-50'
        : 'border-rose-700/20 bg-rose-50';

  const scoreColor = total >= 70 ? '#047857' : total >= 50 ? '#b45309' : '#b91c1c';

  return (
    <div className={`rounded-2xl border p-4 shadow-card ${toneClass}`}>
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs uppercase tracking-wide text-ink/60">Portfolio Score</p>
          <p className="font-display text-5xl leading-none" style={{ color: scoreColor }}>
            {total}
            <span className="ml-0.5 font-display text-2xl text-ink/40">/100</span>
          </p>
        </div>
        <ScoreGauge value={total} color={scoreColor} />
      </div>

      <div className="mt-4 space-y-2">
        <ScoreBar
          label="Diversification"
          value={breakdown.diversification}
          max={35}
          color={scoreColor}
        />
        <ScoreBar
          label="Benchmark Performance"
          value={breakdown.benchmarkPerformance}
          max={35}
          color={scoreColor}
        />
        <ScoreBar
          label="Risk Concentration"
          value={breakdown.riskConcentration}
          max={30}
          color={scoreColor}
        />
      </div>
    </div>
  );
}

function ScoreBar({
  label,
  value,
  max,
  color,
}: {
  label: string;
  value: number;
  max: number;
  color: string;
}) {
  const pct = max === 0 ? 0 : Math.min((value / max) * 100, 100);

  return (
    <div>
      <div className="mb-1 flex justify-between text-xs">
        <span className="text-ink/70">{label}</span>
        <span className="font-semibold" style={{ color }}>
          {value.toFixed(1)} / {max}
        </span>
      </div>
      <div className="h-1.5 w-full rounded-full bg-black/10">
        <div
          className="h-1.5 rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
    </div>
  );
}

function ScoreGauge({ value, color }: { value: number; color: string }) {
  const r = 28;
  const circ = 2 * Math.PI * r;
  const offset = circ - (value / 100) * circ;

  return (
    <svg width={72} height={72} viewBox="0 0 72 72" className="-rotate-90">
      <circle cx={36} cy={36} r={r} fill="none" stroke="#00000010" strokeWidth={7} />
      <circle
        cx={36}
        cy={36}
        r={r}
        fill="none"
        stroke={color}
        strokeWidth={7}
        strokeDasharray={circ}
        strokeDashoffset={offset}
        strokeLinecap="round"
        style={{ transition: 'stroke-dashoffset 0.7s ease' }}
      />
    </svg>
  );
}
