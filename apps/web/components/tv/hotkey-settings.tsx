'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  DEFAULT_HOTKEYS,
  HOTKEY_ACTIONS,
  type HotkeyAction,
  type HotkeySettings,
  type ReservedKey,
  comboLabel,
  comboOf,
  findClashes,
} from '../../lib/tv/hotkeys';
import { SVP_KEY, TOOL_KEYS, ZOOM_IN_KEY } from './left-toolbar';

/** The chart's own shortcuts, so a trading hotkey that shadows one is flagged. */
export const CHART_KEYS: ReservedKey[] = [
  ...Object.entries(TOOL_KEYS).map(([tool, k]) => ({
    combo: { code: k.code, shift: k.shift },
    label: `drawing tool "${tool}"`,
    anyAlt: true,
  })),
  { combo: { code: SVP_KEY.code, shift: true }, label: 'session volume profile', anyAlt: true },
  { combo: { code: ZOOM_IN_KEY.code }, label: 'zoom in' },
  { combo: { code: 'ArrowRight', shift: true }, label: 'replay step / nudge drawing' },
  { combo: { code: 'ArrowDown', shift: true }, label: 'replay play / nudge drawing' },
  { combo: { code: 'ArrowLeft', shift: true }, label: 'nudge drawing' },
  { combo: { code: 'ArrowUp', shift: true }, label: 'nudge drawing' },
  { combo: { code: 'Escape' }, label: 'cancel drawing' },
  { combo: { code: 'Delete' }, label: 'delete drawing' },
  { combo: { code: 'Backspace' }, label: 'delete drawing' },
  { combo: { code: 'KeyZ', ctrl: true }, label: 'undo' },
  { combo: { code: 'KeyZ', meta: true }, label: 'undo' },
  { combo: { code: 'KeyZ', ctrl: true, shift: true }, label: 'redo' },
  { combo: { code: 'KeyZ', meta: true, shift: true }, label: 'redo' },
  { combo: { code: 'KeyY', ctrl: true }, label: 'redo' },
  { combo: { code: 'KeyY', meta: true }, label: 'redo' },
];

const isMac = () => typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform);

export function HotkeySettingsDialog({
  value,
  onChange,
  onClose,
}: {
  value: HotkeySettings;
  onChange: (next: HotkeySettings) => void;
  onClose: () => void;
}) {
  const mac = useMemo(isMac, []);
  const [capture, setCapture] = useState<HotkeyAction | null>(null);
  const [ticks, setTicks] = useState(String(value.limitTicks));
  useEffect(() => setTicks(String(value.limitTicks)), [value.limitTicks]);
  const clashes = useMemo(() => findClashes(value, CHART_KEYS, mac), [value, mac]);

  // While capturing, the next non-modifier key (with its modifiers) becomes the binding; a bare Esc cancels.
  useEffect(() => {
    if (!capture) return;
    const onKey = (e: KeyboardEvent) => {
      e.preventDefault();
      e.stopImmediatePropagation();
      const c = comboOf(e);
      if (!c) return;
      if (c.code === 'Escape' && !c.shift && !c.alt && !c.ctrl && !c.meta) {
        setCapture(null);
        return;
      }
      onChange({ ...value, bindings: { ...value.bindings, [capture]: c } });
      setCapture(null);
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [capture, value, onChange]);

  const commitTicks = () => {
    const n = Number(ticks);
    if (Number.isInteger(n) && n >= 1 && n <= 1000) onChange({ ...value, limitTicks: n });
    else setTicks(String(value.limitTicks));
  };

  return (
    <div
      data-tv-ui
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
      // keep the chart's own shortcuts (drawing tools, Esc) from firing while this dialog has focus
      onKeyDown={(e) => {
        e.nativeEvent.stopPropagation();
        if (e.key === 'Escape' && !capture) onClose();
      }}
    >
      <div className="w-[460px] max-w-[calc(100vw-32px)] rounded-lg border border-[#363a45] bg-[#1e222d] p-4 text-[#d1d4dc] shadow-2xl">
        <div className="mb-1 flex items-center justify-between">
          <div className="text-[15px] font-semibold text-white">Trading hotkeys</div>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="px-1 text-[18px] text-[#b2b5be] hover:text-white"
          >
            ×
          </button>
        </div>
        <p className="mb-3 text-[12px] leading-snug text-[#787b86]">
          Paper account only. Keys work on the active chart while no text box has focus. Buy/sell use the
          panel quantity; with <span className="text-[#b2b5be]">Instant orders placement</span> off they open
          the order dialog instead of sending. Held keys don&apos;t repeat.
        </p>
        <label className="flex cursor-pointer items-center gap-2 py-1 text-[13px]">
          <input
            type="checkbox"
            className="accent-[#2962ff]"
            checked={value.enabled}
            onChange={(e) => onChange({ ...value, enabled: e.target.checked })}
          />
          Enable trading hotkeys
        </label>
        <label className="flex items-center justify-between py-1 text-[13px]">
          Limit offset (ticks from last)
          <input
            value={ticks}
            onChange={(e) => setTicks(e.target.value)}
            onBlur={commitTicks}
            onKeyDown={(e) => e.key === 'Enter' && commitTicks()}
            className="h-7 w-20 rounded border border-[#363a45] bg-[#131722] px-2 text-right text-[13px] outline-none focus:border-[#2962ff]"
          />
        </label>
        <div className={`mt-2 max-h-[55vh] overflow-auto ${value.enabled ? '' : 'opacity-50'}`}>
          {HOTKEY_ACTIONS.map((a) => {
            const c = value.bindings[a.id];
            const clash = clashes[a.id];
            const dup = clash?.some((x) => x.startsWith('hotkey '));
            return (
              <div key={a.id} className="border-t border-[#2a2e39] py-1.5">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-[13px]">{a.label}</div>
                    <div className="truncate text-[11px] text-[#787b86]">{a.hint}</div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      onClick={() => setCapture(capture === a.id ? null : a.id)}
                      className={`h-7 min-w-[120px] rounded border px-2 text-[12px] tabular-nums ${
                        capture === a.id
                          ? 'border-[#2962ff] bg-[#2962ff]/15 text-white'
                          : 'border-[#363a45] bg-[#131722] hover:border-[#50535e]'
                      }`}
                    >
                      {capture === a.id ? 'Press keys… (Esc)' : comboLabel(c, mac)}
                    </button>
                    <button
                      type="button"
                      title="Unbind"
                      disabled={!c}
                      onClick={() => onChange({ ...value, bindings: { ...value.bindings, [a.id]: null } })}
                      className="px-1 text-[14px] text-[#787b86] hover:text-white disabled:opacity-30"
                    >
                      ×
                    </button>
                  </div>
                </div>
                {clash ? (
                  <div className={`mt-0.5 text-[11px] ${dup ? 'text-[#f23645]' : 'text-[#f5a623]'}`}>
                    {dup ? 'Same keys as ' : 'Overrides '}
                    {clash.join(', ')}
                    {dup ? ' — only the first one in this list fires' : ''}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
        <div className="mt-3 flex justify-between">
          <button
            type="button"
            onClick={() => onChange(DEFAULT_HOTKEYS)}
            className="rounded border border-[#363a45] px-3 py-1 text-[12px] text-[#b2b5be] hover:text-white"
          >
            Reset defaults
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded bg-[#2962ff] px-4 py-1 text-[12px] font-semibold text-white"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
