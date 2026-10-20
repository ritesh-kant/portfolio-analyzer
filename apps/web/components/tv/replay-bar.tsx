'use client';

import { REPLAY_SPEEDS } from '../../lib/tv/replay';
import { Icons } from './icons';

interface Props {
  /** Waiting for the user to click the starting candle. */
  picking: boolean;
  playing: boolean;
  speed: number;
  /** Exchange-clock label of the last visible candle. */
  label: string;
  atEnd: boolean;
  onPlay: () => void;
  onStep: () => void;
  onSpeed: (speed: number) => void;
  onSelect: () => void;
  onRandom: () => void;
  onExit: () => void;
}

const btn =
  'flex h-8 items-center whitespace-nowrap gap-1.5 rounded px-2.5 text-[13px] font-medium text-[#d1d4dc] transition-colors hover:bg-[#2a2e39] disabled:opacity-35 disabled:hover:bg-transparent';

export function ReplayBar(p: Props) {
  return (
    <div
      className="absolute bottom-4 left-1/2 z-30 flex -translate-x-1/2 items-center gap-1 rounded-lg border border-[#363a45] bg-[#1e222d] px-2 py-1.5 shadow-2xl"
      data-tv-ui
    >
      <span className="mr-1 flex items-center gap-1.5 px-1 text-[12px] font-semibold uppercase tracking-wide text-[#2962ff]">
        <Icons.replay size={16} />
        Replay
      </span>
      {p.picking ? (
        <>
          <span className="px-2 text-[13px] text-[#b2b5be]">Click the candle to start from</span>
          <button
            type="button"
            className={btn}
            onClick={p.onRandom}
            title="Start from a random candle"
          >
            Random bar
          </button>
        </>
      ) : (
        <>
          <button
            type="button"
            className={btn}
            onClick={p.onSelect}
            title="Pick a different starting candle"
          >
            Select bar
          </button>
          <div className="mx-1 h-5 w-px bg-[#2a2e39]" />
          <button
            type="button"
            className={`${btn} min-w-[34px] justify-center`}
            onClick={p.onPlay}
            disabled={p.atEnd && !p.playing}
            title={p.playing ? 'Pause (Shift+↓)' : 'Play (Shift+↓)'}
          >
            {p.playing ? <Icons.pause size={18} /> : <Icons.play size={18} />}
          </button>
          <button
            type="button"
            className={`${btn} min-w-[34px] justify-center`}
            onClick={p.onStep}
            disabled={p.atEnd}
            title="Forward one candle (Shift+→)"
          >
            <Icons.stepForward size={18} />
          </button>
          <select
            value={p.speed}
            onChange={(e) => p.onSpeed(Number(e.target.value))}
            title="Candles per second"
            className="h-8 rounded border border-[#363a45] bg-[#131722] px-1.5 text-[13px] text-[#d1d4dc] outline-none"
          >
            {REPLAY_SPEEDS.map((s) => (
              <option key={s} value={s}>
                {s}x
              </option>
            ))}
          </select>
          <span className="ml-2 min-w-[150px] px-1 text-[12px] tabular-nums text-[#9598a1]">
            {p.atEnd ? 'End of data' : p.label}
          </span>
        </>
      )}
      <div className="mx-1 h-5 w-px bg-[#2a2e39]" />
      <button
        type="button"
        className={`${btn} px-1.5 hover:text-[#f23645]`}
        onClick={p.onExit}
        title="Exit replay (Esc while picking)"
      >
        <Icons.close size={18} />
      </button>
    </div>
  );
}
