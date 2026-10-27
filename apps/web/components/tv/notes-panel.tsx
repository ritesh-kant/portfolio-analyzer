'use client';

import { useEffect, useRef, useState } from 'react';
import { load, save } from '../../lib/tv/storage';
import { Icons } from './icons';

const NOTES_KEY = 'tv:notes';
const GLOBAL_ID = '*'; // not a valid symbol, so it can't collide with one
const SCOPE_KEY = 'tv:notesScope';
const HEIGHT_KEY = 'tv:notesHeight';
const MIN_HEIGHT = 120;
const MAX_HEIGHT = 600;
const DEFAULT_HEIGHT = 220;
const clampHeight = (h: number) => Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, Math.round(h)));

interface Props {
  symbol: string;
  onClose: () => void;
}

/** Free-text notes, per symbol or global (one tab each), docked at the bottom of the right column and kept in localStorage. */
export function NotesPanel({ symbol, onClose }: Props) {
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [scope, setScope] = useState<'symbol' | 'global'>('symbol');
  const [height, setHeight] = useState(DEFAULT_HEIGHT);
  const [dragging, setDragging] = useState(false);
  const heightRef = useRef(DEFAULT_HEIGHT);
  heightRef.current = height;

  useEffect(() => {
    setNotes(load<Record<string, string>>(NOTES_KEY, {}));
    setScope(load<string>(SCOPE_KEY, 'symbol') === 'global' ? 'global' : 'symbol');
    setHeight(clampHeight(load<number>(HEIGHT_KEY, DEFAULT_HEIGHT)));
  }, []);

  const id = scope === 'global' ? GLOBAL_ID : symbol;
  const text = notes[id] ?? '';
  const edit = (value: string) => {
    setNotes((cur) => {
      const next = { ...cur };
      if (value) next[id] = value;
      else delete next[id];
      save(NOTES_KEY, next);
      return next;
    });
  };

  const startResize = (e: React.MouseEvent) => {
    e.preventDefault();
    const startY = e.clientY;
    const startHeight = heightRef.current;
    setDragging(true);
    const move = (ev: MouseEvent) => setHeight(clampHeight(startHeight - (ev.clientY - startY)));
    const up = () => {
      setDragging(false);
      save(HEIGHT_KEY, heightRef.current);
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  };

  return (
    <div
      className="relative flex w-0 min-w-full shrink-0 flex-col border-l border-t border-[#2a2e39] bg-[#131722]"
      style={{ height }}
      data-tv-ui
    >
      <div
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize notes"
        title="Drag to resize · double-click to reset"
        onMouseDown={startResize}
        onDoubleClick={() => {
          setHeight(DEFAULT_HEIGHT);
          save(HEIGHT_KEY, DEFAULT_HEIGHT);
        }}
        className={`absolute -top-[3px] left-0 z-20 h-[6px] w-full cursor-row-resize transition-colors hover:bg-[#2962ff]/60 ${
          dragging ? 'bg-[#2962ff]/80' : ''
        }`}
      />
      {dragging ? <div className="fixed inset-0 z-50 cursor-row-resize" /> : null}
      <div className="flex h-9 shrink-0 items-center justify-between border-b border-[#2a2e39] px-3">
        <div className="flex items-center gap-1 text-[12px]">
          {(['symbol', 'global'] as const).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => {
                setScope(k);
                save(SCOPE_KEY, k);
              }}
              className={`rounded px-2 py-0.5 ${
                scope === k ? 'bg-[#2a2e39] text-white' : 'text-[#787b86] hover:text-[#d1d4dc]'
              }`}
            >
              {k === 'global' ? 'Global' : symbol.replace(/\.(NS|BO)$/, '')}
            </button>
          ))}
        </div>
        <button
          type="button"
          title="Close notes"
          onClick={onClose}
          className="flex h-6 w-6 items-center justify-center rounded text-[#b2b5be] hover:bg-[#2a2e39] hover:text-white"
        >
          <Icons.close size={16} />
        </button>
      </div>
      <textarea
        value={text}
        onChange={(e) => edit(e.target.value)}
        placeholder={
          scope === 'global' ? 'Notes for every symbol…' : 'Write notes for this symbol…'
        }
        spellCheck={false}
        className="min-h-0 flex-1 resize-none bg-transparent p-3 text-[13px] leading-5 text-[#d1d4dc] outline-none placeholder:text-[#5d606b]"
      />
    </div>
  );
}
