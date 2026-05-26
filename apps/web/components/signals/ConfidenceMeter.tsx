'use client';

interface ConfidenceMeterProps {
  confidence: number;
}

export function ConfidenceMeter({ confidence }: ConfidenceMeterProps) {
  const clipped = Math.min(100, Math.max(0, confidence));
  const colorClass = clipped >= 70 ? 'bg-green-500' : clipped >= 40 ? 'bg-amber-400' : 'bg-red-400';

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs text-ink/60">
        <span>Confidence</span>
        <span className="font-semibold text-ink">{clipped}%</span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-black/10">
        <div
          className={`h-full rounded-full transition-all ${colorClass}`}
          style={{ width: `${clipped}%` }}
        />
      </div>
    </div>
  );
}
