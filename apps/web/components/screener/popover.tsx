'use client';

import {
  type ReactNode,
  type RefObject,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';

interface Props {
  /** The button that opened it: clicks on it are not "outside", and the popover hangs below it. */
  anchor: RefObject<HTMLElement | null>;
  onClose: () => void;
  width?: number;
  children: ReactNode;
}

/**
 * A menu under its anchor. Rendered in a portal with fixed positioning so the table's and the side panel's
 * `overflow` never clips it; closes on Escape or a click anywhere else.
 */
export function Popover({ anchor, onClose, width = 280, children }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const close = useRef(onClose);
  close.current = onClose;

  useLayoutEffect(() => {
    const el = anchor.current;
    if (!el) return;
    const place = () => {
      const r = el.getBoundingClientRect();
      const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8));
      setPos({ left, top: r.bottom + 6 });
    };
    place();
    window.addEventListener('resize', place);
    return () => window.removeEventListener('resize', place);
  }, [anchor, width]);

  useEffect(() => {
    const down = (e: MouseEvent) => {
      const t = e.target as Node;
      if (box.current?.contains(t) || anchor.current?.contains(t)) return;
      close.current();
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close.current();
    };
    document.addEventListener('mousedown', down);
    document.addEventListener('keydown', key);
    return () => {
      document.removeEventListener('mousedown', down);
      document.removeEventListener('keydown', key);
    };
  }, [anchor]);

  if (!pos || typeof document === 'undefined') return null;
  return createPortal(
    <div
      ref={box}
      style={{
        position: 'fixed',
        left: pos.left,
        top: pos.top,
        width,
        maxHeight: `calc(100vh - ${pos.top + 12}px)`,
      }}
      className="z-[200] flex flex-col overflow-hidden rounded-lg border border-[#363a45] bg-[#1e222d] text-[13px] text-[#d1d4dc] shadow-2xl"
    >
      {children}
    </div>,
    document.body,
  );
}
