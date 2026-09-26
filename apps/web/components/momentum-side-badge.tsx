import { sideOf } from '../lib/momentum-side';

/** "LONG" / "SHORT" pill. Shorts are rose so they stand out in a mostly-long list. */
export function SideBadge({ trade }: { trade: { side?: string | null } }) {
  const short = sideOf(trade) === 'short';
  return (
    <span
      title={short ? 'Short sale: sold first, bought back at exit' : 'Long: bought first, sold at exit'}
      className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ${
        short ? 'bg-rose-100 text-rose-700' : 'bg-emerald-100 text-emerald-700'
      }`}
    >
      {short ? 'Short' : 'Long'}
    </span>
  );
}
