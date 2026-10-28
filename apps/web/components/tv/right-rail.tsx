'use client';

import { Icons } from './icons';

interface Props {
  screenerOpen: boolean;
  onScreener: () => void;
  watchlistOpen: boolean;
  onWatchlist: () => void;
  deltaOpen: boolean;
  onDelta: () => void;
  cvdOpen: boolean;
  onCvd: () => void;
  fvgOpen: boolean;
  onFvg: () => void;
  pocOpen: boolean;
  onPoc: () => void;
  notesOpen: boolean;
  onNotes: () => void;
  hideDivs: boolean;
  onHideDivs: () => void;
}

const button = (open: boolean) =>
  `flex h-[38px] w-[38px] items-center justify-center rounded-md transition-colors ${
    open ? 'bg-[#2a2e39] text-[#2962ff]' : 'text-[#b2b5be] hover:bg-[#2a2e39] hover:text-white'
  }`;

/** TradingView's right-hand icon rail: the watchlist, screener, POC scanner, delta-divergence, CVD-divergence and fair-value-gap panels share the one docked slot. */
export function RightRail({
  screenerOpen,
  onScreener,
  watchlistOpen,
  onWatchlist,
  deltaOpen,
  onDelta,
  cvdOpen,
  onCvd,
  fvgOpen,
  onFvg,
  pocOpen,
  onPoc,
  notesOpen,
  onNotes,
  hideDivs,
  onHideDivs,
}: Props) {
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
        title="Stock screener"
        onClick={onScreener}
        className={button(screenerOpen)}
      >
        <Icons.screener size={22} />
      </button>
      <button
        type="button"
        title="POC retest scanner (volume profile)"
        onClick={onPoc}
        className={button(pocOpen)}
      >
        <Icons.poc size={22} />
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
      <button
        type="button"
        title="Fair value gaps (volumetric)"
        onClick={onFvg}
        className={button(fvgOpen)}
      >
        <Icons.fvg size={22} />
      </button>
      <button
        type="button"
        title={hideDivs ? 'Show all indicators on the chart' : 'Hide all indicators on the chart'}
        onClick={onHideDivs}
        className={`${button(hideDivs)} mt-1`}
      >
        {hideDivs ? <Icons.eyeOff size={22} /> : <Icons.eye size={22} />}
      </button>
      <button
        type="button"
        title="Notes"
        onClick={onNotes}
        className={`${button(notesOpen)} mt-auto`}
      >
        <Icons.notes size={22} />
      </button>
    </div>
  );
}
