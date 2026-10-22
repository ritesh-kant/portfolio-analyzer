'use client';

import { Icons } from './icons';

interface Props {
  watchlistOpen: boolean;
  onWatchlist: () => void;
  deltaOpen: boolean;
  onDelta: () => void;
  cvdOpen: boolean;
  onCvd: () => void;
}

const button = (open: boolean) =>
  `flex h-[38px] w-[38px] items-center justify-center rounded-md transition-colors ${
    open ? 'bg-[#2a2e39] text-[#2962ff]' : 'text-[#b2b5be] hover:bg-[#2a2e39] hover:text-white'
  }`;

/** TradingView's right-hand icon rail: the watchlist, the delta-divergence panel and the CVD-divergence panel share the one docked slot. */
export function RightRail({ watchlistOpen, onWatchlist, deltaOpen, onDelta, cvdOpen, onCvd }: Props) {
  return (
    <div className="flex w-[48px] shrink-0 flex-col items-center gap-1 border-l border-[#2a2e39] bg-[#131722] py-1.5">
      <button
        type="button"
        title="Watchlist"
        onClick={onWatchlist}
        className={button(watchlistOpen)}
      >
        <Icons.list size={22} />
      </button>
      <button
        type="button"
        title="Delta divergence (estimated)"
        onClick={onDelta}
        className={button(deltaOpen)}
      >
        <Icons.delta size={22} />
      </button>
      <button
        type="button"
        title="CVD divergences (estimated)"
        onClick={onCvd}
        className={button(cvdOpen)}
      >
        <Icons.cvd size={22} />
      </button>
    </div>
  );
}
