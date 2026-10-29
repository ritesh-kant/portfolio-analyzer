'use client';

import { type ReactNode, useEffect, useRef, useState } from 'react';
import { type DrawingKind, KIND_LABEL } from '../../lib/tv/drawings';
import { Icons } from './icons';

export type Tool = 'cursor' | DrawingKind;

/** Entries that switch an indicator on and off rather than start a drawing. */
type Action = 'svp';

const GROUPS: { id: string; tools: Tool[]; actions?: Action[] }[] = [
  { id: 'cursor', tools: ['cursor'] },
  { id: 'lines', tools: ['trend', 'ray', 'hline', 'hray', 'vline'] },
  { id: 'fib', tools: ['fib'] },
  { id: 'forecast', tools: ['long', 'short'] },
  {
    id: 'shapes',
    tools: ['brush', 'highlighter', 'arrowmarker', 'arrow', 'arrowup', 'arrowdown', 'rect', 'circle', 'ellipse', 'triangle'],
  },
  { id: 'text', tools: ['text'] },
  { id: 'measure', tools: ['measure'] },
  { id: 'volume', tools: ['vprofile', 'avprofile'], actions: ['svp'] },
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
  vprofile: Icons.vprofile,
  avprofile: Icons.vprofile,
  long: Icons.long,
  short: Icons.short,
  text: Icons.text,
  brush: Icons.brush,
  highlighter: Icons.highlighter,
  arrow: Icons.arrow,
  arrowmarker: Icons.arrowmarker,
  arrowup: Icons.arrowup,
  arrowdown: Icons.arrowdown,
  circle: Icons.circle,
  ellipse: Icons.ellipse,
  triangle: Icons.triangle,
};

const LABEL: Record<Tool, string> = { cursor: 'Cross', ...KIND_LABEL };
const ACTION_LABEL: Record<Action, string> = { svp: 'Session volume profile' };
const ACTION_ICON: Record<Action, (p: { size?: number }) => ReactNode> = { svp: Icons.vprofile };

/** Single-key shortcuts (Alt/Option held is also accepted); matched on `code` so layout and Option don't matter. */
export const TOOL_KEYS: Record<Tool, { code: string; shift?: boolean; label: string }> = {
  cursor: { code: 'KeyC', label: 'C' },
  trend: { code: 'KeyT', label: 'T' },
  ray: { code: 'KeyY', label: 'Y' },
  hline: { code: 'KeyH', label: 'H' },
  hray: { code: 'KeyJ', label: 'J' },
  vline: { code: 'KeyV', label: 'V' },
  fib: { code: 'KeyF', label: 'F' },
  long: { code: 'KeyL', label: 'L' },
  short: { code: 'KeyS', label: 'S' },
  rect: { code: 'KeyR', label: 'R' },
  text: { code: 'KeyT', shift: true, label: 'Shift+T' },
  measure: { code: 'KeyM', label: 'M' },
  vprofile: { code: 'KeyV', shift: true, label: 'Shift+V' },
  avprofile: { code: 'KeyA', shift: true, label: 'Shift+A' },
  brush: { code: 'KeyB', label: 'B' },
  highlighter: { code: 'KeyH', shift: true, label: 'Shift+H' },
  arrowmarker: { code: 'KeyM', shift: true, label: 'Shift+M' },
  arrow: { code: 'KeyA', label: 'A' },
  arrowup: { code: 'KeyU', label: 'U' },
  arrowdown: { code: 'KeyD', label: 'D' },
  circle: { code: 'KeyC', shift: true, label: 'Shift+C' },
  ellipse: { code: 'KeyE', label: 'E' },
  triangle: { code: 'KeyG', label: 'G' },
};
export const SVP_KEY = { code: 'KeyP', shift: true, label: 'Shift+P' };
export const ZOOM_IN_KEY = { code: 'KeyZ', label: 'Z' };

export function toolForKey(e: KeyboardEvent): Tool | null {
  if (e.ctrlKey || e.metaKey) return null;
  for (const [tool, k] of Object.entries(TOOL_KEYS) as [Tool, (typeof TOOL_KEYS)[Tool]][]) {
    if (k.code === e.code && !!k.shift === e.shiftKey) return tool;
  }
  return null;
}

const GROUP_TITLE: Record<string, string> = {
  lines: 'Lines',
  forecast: 'Forecasting',
}

/** Groups whose flyout is split into titled sub-sections (TradingView's brushes / arrows / shapes menu). */
const SECTIONS: Record<string, { title: string; tools: Tool[] }[]> = {
  shapes: [
    { title: 'Brushes', tools: ['brush', 'highlighter'] },
    { title: 'Arrows', tools: ['arrowmarker', 'arrow', 'arrowup', 'arrowdown'] },
    { title: 'Shapes', tools: ['rect', 'circle', 'ellipse', 'triangle'] },
  ],
};

/** Groups that start below a separator. */
const SEP_BEFORE = new Set(['fib', 'shapes', 'text', 'measure', 'volume']);

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
  svpOn: boolean;
  onToggleSvp: () => void;
}

const btn = (active: boolean) =>
  `relative flex h-[34px] w-[38px] items-center justify-center rounded-md md:h-[38px] md:w-[44px] transition-colors ${
    active ? 'bg-[#2a2e39] text-[#2962ff]' : 'text-[#b2b5be] hover:bg-[#2a2e39] hover:text-white'
  }`;

function Sep() {
  return <div className="my-1.5 h-px w-8 bg-[#2a2e39]" />;
}

export function LeftToolbar(p: Props) {
  // The visible tool of a multi-tool group is the last one picked from it.
  const [picked, setPicked] = useState<Record<string, Tool | Action>>({ lines: 'trend' });
  const [open, setOpen] = useState<string | null>(null);
  // The toolbar scrolls, which would clip an absolutely positioned flyout, so it is placed in viewport coordinates.
  const [flyTop, setFlyTop] = useState(0);
  const [flyLeft, setFlyLeft] = useState(52);
  const root = useRef<HTMLDivElement>(null);

  // Keep the visible tool of a group in step with a tool chosen by keyboard.
  useEffect(() => {
    const g = GROUPS.find((v) => v.tools.includes(p.tool));
    if (g && (g.tools.length > 1 || g.actions)) setPicked((s) => (s[g.id] === p.tool ? s : { ...s, [g.id]: p.tool }));
  }, [p.tool]);

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
  const act = (group: string, action: Action) => {
    setPicked((s) => ({ ...s, [group]: action }));
    setOpen(null);
    if (action === 'svp') p.onToggleSvp();
  };

  return (
    <div
      ref={root}
      className="flex w-[42px] shrink-0 flex-col items-center overflow-y-auto md:w-[52px] border-r border-[#2a2e39] bg-[#131722] py-1.5 scrollbar-none"
    >
      {GROUPS.map((g, gi) => {
        const current = picked[g.id] ?? g.tools[0]!;
        const isAction = (g.actions as string[] | undefined)?.includes(current) ?? false;
        const Icon = isAction ? ACTION_ICON[current as Action] : ICON[current as Tool];
        const multi = g.tools.length + (g.actions?.length ?? 0) > 1;
        const active = isAction ? p.svpOn : g.tools.includes(p.tool);
        const title = isAction
          ? `${ACTION_LABEL[current as Action]}${p.svpOn ? ' (on)' : ''}`
          : TOOL_KEYS[current as Tool].label
            ? `${LABEL[current as Tool]} (${TOOL_KEYS[current as Tool].label})`
            : LABEL[current as Tool];
        return (
          <div key={g.id} className="relative">
            {gi > 0 && SEP_BEFORE.has(g.id) ? <Sep /> : null}
            <div className="flex items-center">
              <button
                type="button"
                title={title}
                className={btn(active)}
                onClick={() =>
                  isAction
                    ? act(g.id, current as Action)
                    : g.id === 'cursor'
                      ? p.onTool('cursor')
                      : choose(g.id, current as Tool)
                }
              >
                <Icon size={22} />
              </button>
              {multi ? (
                <button
                  type="button"
                  aria-label={`${g.id} tools`}
                  className="absolute -right-1 top-1/2 h-5 w-3 -translate-y-1/2 rounded-sm text-[#787b86] hover:bg-[#2a2e39] hover:text-white"
                  onClick={(e) => {
                    setFlyTop(e.currentTarget.getBoundingClientRect().top - 6);
                    setFlyLeft(root.current?.getBoundingClientRect().right ?? 52);
                    setOpen(open === g.id ? null : g.id);
                  }}
                >
                  <span className="block -rotate-90 scale-50">
                    <Icons.chevron size={16} />
                  </span>
                </button>
              ) : null}
            </div>
            {multi && open === g.id ? (
              <div
                className="fixed z-50 max-h-[calc(100vh-24px)] w-60 max-w-[calc(100vw-60px)] overflow-y-auto rounded-md border border-[#363a45] bg-[#1e222d] py-1 shadow-xl"
                style={{ left: flyLeft, top: flyTop }}
              >
                {(SECTIONS[g.id] ?? [{ title: GROUP_TITLE[g.id] ?? 'Tools', tools: g.tools }]).map((sec, si) => (
                  <div key={sec.title}>
                    {si > 0 ? <div className="my-1 h-px bg-[#363a45]" /> : null}
                    <div className="px-3 pb-1 pt-1 text-[11px] font-semibold uppercase tracking-wide text-[#787b86]">
                      {sec.title}
                    </div>
                    {sec.tools.map((t) => {
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
                          <span className="flex-1">{LABEL[t]}</span>
                          <span className="text-[11px] text-[#787b86]">{TOOL_KEYS[t].label}</span>
                        </button>
                      );
                    })}
                  </div>
                ))}
                {g.actions?.map((a) => {
                  const I = ACTION_ICON[a];
                  return (
                    <button
                      key={a}
                      type="button"
                      onClick={() => act(g.id, a)}
                      className={`flex w-full items-center gap-3 px-3 py-1.5 text-left text-[13px] hover:bg-[#2a2e39] ${
                        p.svpOn ? 'text-[#2962ff]' : 'text-[#d1d4dc]'
                      }`}
                    >
                      <I size={18} />
                      <span className="flex-1">{ACTION_LABEL[a]}</span>
                      <span className="text-[11px] text-[#787b86]">
                        {p.svpOn ? 'On' : 'Off'} · {SVP_KEY.label}
                      </span>
                    </button>
                  );
                })}
              </div>
            ) : null}
          </div>
        );
      })}
      <Sep />
      <button type="button" title={`Zoom in (${ZOOM_IN_KEY.label})`} className={btn(false)} onClick={p.onZoomIn}>
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
