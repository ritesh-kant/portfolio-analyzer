'use client';

import { Icons } from './icons';

interface Props {
  watchlistOpen: boolean;
  onWatchlist: () => void;
}

/** TradingView's right-hand icon rail; the watchlist is the only panel for now. */
export function RightRail({ watchlistOpen, onWatchlist }: Props) {
  return (
    <div className="flex w-[48px] shrink-0 flex-col items-center border-l border-[#2a2e39] bg-[#131722] py-1.5">
      <button
        type="button"
        title="Watchlist"
        onClick={onWatchlist}
        className={`flex h-[38px] w-[38px] items-center justify-center rounded-md transition-colors ${
          watchlistOpen
            ? 'bg-[#2a2e39] text-[#2962ff]'
            : 'text-[#b2b5be] hover:bg-[#2a2e39] hover:text-white'
        }`}
      >
        <Icons.list size={22} />
      </button>
    </div>
  );
}
