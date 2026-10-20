'use client';

import { type ReactNode, useEffect, useRef, useState } from 'react';
import { type DrawingKind, KIND_LABEL } from '../../lib/tv/drawings';
import { Icons } from './icons';

export type Tool = 'cursor' | DrawingKind;

const GROUPS: { id: string; tools: Tool[] }[] = [
  { id: 'cursor', tools: ['cursor'] },
  { id: 'lines', tools: ['trend', 'ray', 'hline', 'hray', 'vline'] },
  { id: 'fib', tools: ['fib'] },
  { id: 'shapes', tools: ['rect'] },
  { id: 'text', tools: ['text'] },
  { id: 'measure', tools: ['measure'] },
];

const ICON: Record<Tool, (p: { size?: number }) => ReactNode> = {
  cursor: Icons.cross,
  trend: Icons.trend,
  ray: Icons.ray,
  hline: Icons.hline,
  hray: Icons.hray,
  vline: Icons.vline,
  rect: Icons.rect,
  fib: Icons.fib,
  measure: Icons.measure,
  text: Icons.text,
};

const LABEL: Record<Tool, string> = { cursor: 'Cross', ...KIND_LABEL };

interface Props {
  tool: Tool;
  onTool: (tool: Tool) => void;
  magnet: boolean;
  onMagnet: () => void;
  keepDrawing: boolean;
  onKeepDrawing: () => void;
  lockAll: boolean;
  onLockAll: () => void;
  hideAll: boolean;
  onHideAll: () => void;
  onRemoveAll: () => void;
  onZoomIn: () => void;
}

const btn = (active: boolean) =>
  `relative flex h-[38px] w-[44px] items-center justify-center rounded-md transition-colors ${
    active ? 'bg-[#2a2e39] text-[#2962ff]' : 'text-[#b2b5be] hover:bg-[#2a2e39] hover:text-white'
  }`;

function Sep() {
  return <div className="my-1.5 h-px w-8 bg-[#2a2e39]" />;
}

export function LeftToolbar(p: Props) {
  // The visible tool of a multi-tool group is the last one picked from it.
  const [picked, setPicked] = useState<Record<string, Tool>>({ lines: 'trend' });
  const [open, setOpen] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(null);
    };
    window.addEventListener('mousedown', close);
    return () => window.removeEventListener('mousedown', close);
  }, []);

  const choose = (group: string, tool: Tool) => {
    setPicked((s) => ({ ...s, [group]: tool }));
    setOpen(null);
    p.onTool(tool);
  };

  return (
    <div
      ref={root}
      className="flex w-[52px] shrink-0 flex-col items-center overflow-y-auto border-r border-[#2a2e39] bg-[#131722] py-1.5 scrollbar-none"
    >
      {GROUPS.map((g, gi) => {
        const current = picked[g.id] ?? g.tools[0]!;
        const Icon = ICON[current];
        const multi = g.tools.length > 1;
        const active = g.tools.includes(p.tool);
        return (
          <div key={g.id} className="relative">
            {gi === 2 || gi === 5 ? <Sep /> : null}
            <div className="flex items-center">
              <button
                type="button"
                title={LABEL[current]}
                className={btn(active)}
                onClick={() => (g.id === 'cursor' ? p.onTool('cursor') : choose(g.id, current))}
              >
                <Icon size={22} />
              </button>
              {multi ? (
                <button
                  type="button"
                  aria-label={`${g.id} tools`}
                  className="absolute -right-1 top-1/2 h-5 w-3 -translate-y-1/2 rounded-sm text-[#787b86] hover:bg-[#2a2e39] hover:text-white"
                  onClick={() => setOpen(open === g.id ? null : g.id)}
                >
                  <span className="block -rotate-90 scale-50">
                    <Icons.chevron size={16} />
                  </span>
                </button>
              ) : null}
            </div>
            {multi && open === g.id ? (
              <div className="absolute left-[52px] top-0 z-50 w-52 rounded-md border border-[#363a45] bg-[#1e222d] py-1 shadow-xl">
                <div className="px-3 pb-1 pt-1 text-[11px] font-semibold uppercase tracking-wide text-[#787b86]">
                  Lines
                </div>
                {g.tools.map((t) => {
                  const I = ICON[t];
                  return (
                    <button
                      key={t}
                      type="button"
                      onClick={() => choose(g.id, t)}
                      className={`flex w-full items-center gap-3 px-3 py-1.5 text-left text-[13px] hover:bg-[#2a2e39] ${
                        p.tool === t ? 'text-[#2962ff]' : 'text-[#d1d4dc]'
                      }`}
                    >
                      <I size={18} />
                      {LABEL[t]}
                    </button>
                  );
                })}
              </div>
            ) : null}
          </div>
        );
      })}
      <Sep />
      <button type="button" title="Zoom in" className={btn(false)} onClick={p.onZoomIn}>
        <Icons.zoom size={22} />
      </button>
      <div className="flex-1" />
      <button
        type="button"
        title="Weak magnet — snap to OHLC"
        className={btn(p.magnet)}
        onClick={p.onMagnet}
      >
        <Icons.magnet size={22} />
      </button>
      <button
        type="button"
        title="Stay in drawing mode"
        className={btn(p.keepDrawing)}
        onClick={p.onKeepDrawing}
      >
        <Icons.keep size={22} />
      </button>
      <button
        type="button"
        title="Lock all drawings"
        className={btn(p.lockAll)}
        onClick={p.onLockAll}
      >
        {p.lockAll ? <Icons.lock size={22} /> : <Icons.unlock size={22} />}
      </button>
      <button
        type="button"
        title="Hide all drawings"
        className={btn(p.hideAll)}
        onClick={p.onHideAll}
      >
        {p.hideAll ? <Icons.eyeOff size={22} /> : <Icons.eye size={22} />}
      </button>
      <button
        type="button"
        title="Remove all drawings"
        className={btn(false)}
        onClick={p.onRemoveAll}
      >
        <Icons.trash size={22} />
      </button>
    </div>
  );
}
