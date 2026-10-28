'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import type { Quote } from '../../app/api/chart/quotes/route';
import { Popover } from '../screener/popover';
import { load, save } from '../../lib/tv/storage';
import { PANEL_WIDTH_KEY } from './panel-width';
import { Icons } from './icons';

export interface WatchItem {
  symbol: string;
  name: string;
}

export const DEFAULT_WATCHLIST: WatchItem[] = [
  { symbol: '^NSEI', name: 'NIFTY 50' },
  { symbol: '^NSEBANK', name: 'NIFTY BANK' },
  { symbol: 'RELIANCE.NS', name: 'Reliance Industries' },
  { symbol: 'HDFCBANK.NS', name: 'HDFC Bank' },
  { symbol: 'TCS.NS', name: 'Tata Consultancy Services' },
  { symbol: 'INFY.NS', name: 'Infosys' },
  { symbol: 'AAPL', name: 'Apple Inc.' },
  { symbol: 'NVDA', name: 'NVIDIA Corporation' },
  { symbol: 'GC=F', name: 'Gold — COMEX futures' },
];

export interface WatchList {
  id: string;
  name: string;
  items: WatchItem[];
}

/** Every named list and the one on show. */
interface Book {
  activeId: string;
  lists: WatchList[];
}

const STORAGE_KEY = 'tv:watchlists';
/** The single list saved before there were several: it becomes the first list (and is left in place). */
const LEGACY_KEY = 'tv:watchlist';

function loadBook(): Book {
  const book = load<Book | null>(STORAGE_KEY, null);
  if (book?.lists?.length) {
    return book.lists.some((l) => l.id === book.activeId)
      ? book
      : { ...book, activeId: book.lists[0]!.id };
  }
  const items = load<WatchItem[]>(LEGACY_KEY, DEFAULT_WATCHLIST);
  return { activeId: 'main', lists: [{ id: 'main', name: 'Watchlist', items }] };
}

const activeList = (book: Book): WatchList =>
  book.lists.find((l) => l.id === book.activeId) ?? book.lists[0]!;

/** "Watchlist N", the first N not already taken. */
function freshName(lists: WatchList[]): string {
  const taken = new Set(lists.map((l) => l.name));
  let n = lists.length + 1;
  while (taken.has(`Watchlist ${n}`)) n += 1;
  return `Watchlist ${n}`;
}

const withItem = (items: WatchItem[], item: WatchItem) =>
  items.some((i) => i.symbol === item.symbol) ? items : [...items, item];

/** The list on show, for panels shown while this one is closed (only one right panel is open at a time). */
export const watchlistSymbols = (): Set<string> =>
  new Set(activeList(loadBook()).items.map((i) => i.symbol));

/** Appends to the list on show; the panel reads it when it next opens. */
export function addToWatchlist(item: WatchItem): void {
  const book = loadBook();
  save(STORAGE_KEY, {
    ...book,
    lists: book.lists.map((l) =>
      l.id === book.activeId ? { ...l, items: withItem(l.items, item) } : l,
    ),
  });
}
const WIDTH_KEY = PANEL_WIDTH_KEY;
export const MIN_WIDTH = 260;
const MAX_WIDTH = 640;
const DEFAULT_WIDTH = 300;
const clampWidth = (w: number) => Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(w)));
// False until the first mount has hydrated: that one starts at the default (matching the server render), later
// mounts (switching back from another panel) read the saved width at once so the chart doesn't resize twice.
let hydrated = false;
const short = (symbol: string) => symbol.replace(/\.(NS|BO)$/, '');

const fmtPrice = (p: number) =>
  p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: p < 1 ? 4 : 2 });

interface Props {
  active: string;
  onSelect: (symbol: string) => void;
  onAdd: () => void;
  /** Symbols to add from the search dialog arrive here (set by the parent). */
  pending: WatchItem | null;
  onPendingHandled: () => void;
  /** Phones: a drawer over the chart instead of a docked, resizable column. */
  overlay?: boolean;
  onClose?: () => void;
}

export function WatchlistPanel({
  active,
  onSelect,
  onAdd,
  pending,
  onPendingHandled,
  overlay = false,
  onClose,
}: Props) {
  const [book, setBook] = useState<Book>({
    activeId: 'main',
    lists: [{ id: 'main', name: 'Watchlist', items: DEFAULT_WATCHLIST }],
  });
  const list = activeList(book);
  const items = list.items;
  const setItems = (fn: (cur: WatchItem[]) => WatchItem[]) =>
    setBook((b) => ({
      ...b,
      lists: b.lists.map((l) => (l.id === b.activeId ? { ...l, items: fn(l.items) } : l)),
    }));
  const [menuOpen, setMenuOpen] = useState(false);
  const menuAnchor = useRef<HTMLButtonElement>(null);
  /** The name being typed while renaming the list on show; null when not renaming. */
  const [draft, setDraft] = useState<string | null>(null);
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [width, setWidth] = useState(() =>
    hydrated ? clampWidth(load<number>(WIDTH_KEY, DEFAULT_WIDTH)) : DEFAULT_WIDTH,
  );
  const [dragging, setDragging] = useState(false);
  const widthRef = useRef(DEFAULT_WIDTH);
  widthRef.current = width;

  useEffect(() => {
    setBook(loadBook());
    setWidth(clampWidth(load<number>(WIDTH_KEY, DEFAULT_WIDTH)));
    setLoaded(true);
    hydrated = true;
  }, []);

  useEffect(() => {
    if (loaded) save(STORAGE_KEY, book);
  }, [book, loaded]);

  useEffect(() => {
    if (!pending) return;
    setBook((b) => ({
      ...b,
      lists: b.lists.map((l) =>
        l.id === b.activeId ? { ...l, items: withItem(l.items, pending) } : l,
      ),
    }));
    onPendingHandled();
  }, [pending, onPendingHandled]);

  const newList = () => {
    const id = `l${Date.now().toString(36)}`;
    const name = freshName(book.lists);
    setBook((b) => ({ activeId: id, lists: [...b.lists, { id, name, items: [] }] }));
    setMenuOpen(false);
    setDraft(name);
  };
  const commitRename = () => {
    const name = draft?.trim();
    if (name)
      setBook((b) => ({
        ...b,
        lists: b.lists.map((l) => (l.id === b.activeId ? { ...l, name } : l)),
      }));
    setDraft(null);
  };
  const deleteList = () => {
    setMenuOpen(false);
    if (book.lists.length < 2) return;
    if (!window.confirm(`Delete the list "${list.name}" and its ${items.length} symbol(s)?`))
      return;
    setBook((b) => {
      const lists = b.lists.filter((l) => l.id !== b.activeId);
      return { activeId: lists[0]!.id, lists };
    });
  };

  /** The row whose "move to" menu is open, and its button (the menu hangs under it). */
  const [moving, setMoving] = useState<WatchItem | null>(null);
  const moveAnchor = useRef<HTMLElement | null>(null);
  /** Takes `item` out of the list on show and adds it to `targetId` (a new list when null), staying on this list. */
  const moveTo = (item: WatchItem, targetId: string | null) => {
    setBook((b) => {
      let lists = b.lists;
      let to = targetId;
      if (to == null) {
        to = `l${Date.now().toString(36)}`;
        lists = [...lists, { id: to, name: freshName(lists), items: [] }];
      }
      return {
        ...b,
        lists: lists.map((l) =>
          l.id === b.activeId
            ? { ...l, items: l.items.filter((i) => i.symbol !== item.symbol) }
            : l.id === to
              ? { ...l, items: withItem(l.items, item) }
              : l,
        ),
      };
    });
    setMoving(null);
  };

  const symbolsKey = items.map((i) => i.symbol).join(',');
  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      if (!symbolsKey) return;
      try {
        const res = await fetch(`/api/chart/quotes?symbols=${encodeURIComponent(symbolsKey)}`, {
          signal,
          cache: 'no-store',
        });
        if (!res.ok) throw new Error(String(res.status));
        const body = (await res.json()) as { quotes: Record<string, Quote> };
        setQuotes((cur) => ({ ...cur, ...body.quotes }));
        setFailed(false);
      } catch {
        if (!signal?.aborted) setFailed(true);
      }
    },
    [symbolsKey],
  );

  useEffect(() => {
    if (!loaded) return;
    const ctl = new AbortController();
    void refresh(ctl.signal);
    const timer = setInterval(() => {
      if (!document.hidden) void refresh(ctl.signal);
    }, 5000);
    return () => {
      ctl.abort();
      clearInterval(timer);
    };
  }, [loaded, refresh]);

  /** Drag the panel's left edge: moving left widens it. */
  const startResize = (e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = widthRef.current;
    setDragging(true);
    const move = (ev: MouseEvent) => setWidth(clampWidth(startWidth + (startX - ev.clientX)));
    const up = () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
      setDragging(false);
      save(WIDTH_KEY, widthRef.current);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  };

  return (
    <div
      className={
        overlay
          ? 'fixed bottom-0 right-0 top-11 z-40 flex flex-col border-l border-[#2a2e39] bg-[#131722] shadow-2xl'
          : 'relative flex shrink-0 flex-col border-l border-[#2a2e39] bg-[#131722]'
      }
      style={{ width: overlay ? 'min(88vw, 340px)' : width }}
      data-tv-ui
    >
      <div
        hidden={overlay}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize watchlist"
        title="Drag to resize · double-click to reset"
        onMouseDown={startResize}
        onDoubleClick={() => {
          setWidth(DEFAULT_WIDTH);
          save(WIDTH_KEY, DEFAULT_WIDTH);
        }}
        className={`absolute -left-[3px] top-0 z-20 h-full w-[6px] cursor-col-resize transition-colors hover:bg-[#2962ff]/60 ${
          dragging ? 'bg-[#2962ff]/80' : ''
        }`}
      />
      {dragging ? <div className="fixed inset-0 z-50 cursor-col-resize" /> : null}
      <div className="flex h-11 items-center justify-between border-b border-[#2a2e39] px-3">
        {draft != null ? (
          <input
            autoFocus
            value={draft}
            maxLength={40}
            aria-label="List name"
            onChange={(e) => setDraft(e.target.value)}
            onFocus={(e) => e.target.select()}
            onBlur={commitRename}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitRename();
              if (e.key === 'Escape') setDraft(null);
            }}
            className="min-w-0 flex-1 rounded border border-[#2962ff] bg-[#131722] px-2 py-1 text-[14px] font-semibold text-[#d1d4dc] outline-none"
          />
        ) : (
          <button
            ref={menuAnchor}
            type="button"
            onClick={() => setMenuOpen((o) => !o)}
            onDoubleClick={() => {
              setMenuOpen(false);
              setDraft(list.name);
            }}
            title="Switch, add or rename lists · double-click to rename"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            className="-ml-1.5 flex min-w-0 items-center gap-1 rounded px-1.5 py-1 text-[14px] font-semibold text-[#d1d4dc] hover:bg-[#2a2e39]"
          >
            <span className="truncate">{list.name}</span>
            <Icons.chevron size={16} />
          </button>
        )}
        {menuOpen ? (
          <Popover anchor={menuAnchor} onClose={() => setMenuOpen(false)} width={240}>
            <div role="menu" className="py-1 text-[13px] text-[#d1d4dc]">
              {book.lists.map((l) => (
                <button
                  key={l.id}
                  type="button"
                  role="menuitemradio"
                  aria-checked={l.id === book.activeId}
                  onClick={() => {
                    setBook((b) => ({ ...b, activeId: l.id }));
                    setMenuOpen(false);
                  }}
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-[#2a2e39]"
                >
                  <span className="flex w-4 justify-center text-[#2962ff]">
                    {l.id === book.activeId ? <Icons.check size={14} /> : null}
                  </span>
                  <span className="min-w-0 flex-1 truncate">{l.name}</span>
                  <span className="tabular-nums text-[11px] text-[#787b86]">{l.items.length}</span>
                </button>
              ))}
              <div className="my-1 border-t border-[#2a2e39]" />
              <button
                type="button"
                role="menuitem"
                onClick={newList}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-[#2a2e39]"
              >
                <span className="flex w-4 justify-center">
                  <Icons.plus size={14} />
                </span>
                New list
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  setDraft(list.name);
                }}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-[#2a2e39]"
              >
                <span className="w-4" />
                Rename &ldquo;{list.name}&rdquo;
              </button>
              <button
                type="button"
                role="menuitem"
                disabled={book.lists.length < 2}
                title={book.lists.length < 2 ? 'The last list cannot be deleted' : undefined}
                onClick={deleteList}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[#f23645] hover:bg-[#2a2e39] disabled:cursor-not-allowed disabled:text-[#5d606b] disabled:hover:bg-transparent"
              >
                <span className="flex w-4 justify-center">
                  <Icons.trash size={14} />
                </span>
                Delete list
              </button>
            </div>
          </Popover>
        ) : null}
        {overlay ? (
          <button
            type="button"
            onClick={onClose}
            title="Close watchlist"
            className="ml-auto mr-1 flex h-7 w-7 items-center justify-center rounded text-[#b2b5be] hover:bg-[#2a2e39] hover:text-white"
          >
            <Icons.close size={18} />
          </button>
        ) : null}
        <button
          type="button"
          onClick={onAdd}
          title="Add symbol"
          className="flex h-7 w-7 items-center justify-center rounded text-[#b2b5be] hover:bg-[#2a2e39] hover:text-white"
        >
          <Icons.plus size={18} />
        </button>
      </div>
      <div className="grid grid-cols-[1fr_auto_auto_auto] gap-x-3 border-b border-[#2a2e39] px-3 py-1.5 text-[11px] font-medium text-[#787b86]">
        <span>Symbol</span>
        <span className="w-16 text-right">Last</span>
        <span className="w-14 text-right">Chg</span>
        <span className="w-14 text-right">Chg%</span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {items.map((item) => {
          const q = quotes[item.symbol];
          const up = q ? q.change >= 0 : true;
          const color = !q ? 'text-[#787b86]' : up ? 'text-[#089981]' : 'text-[#f23645]';
          return (
            <div
              key={item.symbol}
              role="button"
              tabIndex={0}
              onClick={() => onSelect(item.symbol)}
              onKeyDown={(e) => e.key === 'Enter' && onSelect(item.symbol)}
              title={item.name}
              className={`group relative grid cursor-pointer grid-cols-[1fr_auto_auto_auto] items-center gap-x-3 px-3 py-1.5 text-[13px] hover:bg-[#2a2e39] ${
                item.symbol === active ? 'bg-[#2a2e39]' : ''
              }`}
            >
              <span className="min-w-0">
                <span className="block truncate font-semibold text-[#d1d4dc]">
                  {short(item.symbol)}
                </span>
                <span className="block truncate text-[11px] text-[#787b86]">{item.name}</span>
              </span>
              <span className="w-16 text-right tabular-nums text-[#d1d4dc]">
                {q ? fmtPrice(q.price) : '—'}
              </span>
              <span className={`w-14 text-right tabular-nums ${color}`}>
                {q ? `${up ? '+' : ''}${q.change.toFixed(2)}` : ''}
              </span>
              <span className={`w-14 text-right tabular-nums ${color}`}>
                {q ? `${up ? '+' : ''}${q.changePct.toFixed(2)}%` : ''}
              </span>
              <button
                type="button"
                title="Move to another list"
                aria-label={`Move ${item.symbol} to another list`}
                aria-haspopup="menu"
                onClick={(e) => {
                  e.stopPropagation();
                  moveAnchor.current = e.currentTarget;
                  setMoving((m) => (m?.symbol === item.symbol ? null : item));
                }}
                className={`absolute right-7 top-1 h-5 w-5 items-center justify-center rounded bg-[#1e222d] text-[#787b86] hover:text-white group-hover:flex ${
                  moving?.symbol === item.symbol ? 'flex' : 'hidden'
                }`}
              >
                <Icons.moveTo size={12} />
              </button>
              <button
                type="button"
                title="Remove from this list"
                aria-label={`Remove ${item.symbol}`}
                onClick={(e) => {
                  e.stopPropagation();
                  setItems((cur) => cur.filter((i) => i.symbol !== item.symbol));
                }}
                className="absolute right-1 top-1 hidden h-5 w-5 items-center justify-center rounded bg-[#1e222d] text-[#787b86] hover:text-[#f23645] group-hover:flex"
              >
                <Icons.close size={12} />
              </button>
            </div>
          );
        })}
        {moving ? (
          <Popover anchor={moveAnchor} onClose={() => setMoving(null)} width={220}>
            <div role="menu" className="py-1 text-[13px] text-[#d1d4dc]">
              <div className="px-3 pb-1 pt-0.5 text-[11px] text-[#787b86]">
                Move {short(moving.symbol)} to
              </div>
              {book.lists
                .filter((l) => l.id !== book.activeId)
                .map((l) => {
                  const there = l.items.some((i) => i.symbol === moving.symbol);
                  return (
                    <button
                      key={l.id}
                      type="button"
                      role="menuitem"
                      onClick={() => moveTo(moving, l.id)}
                      title={
                        there ? 'Already in that list: this just removes it from here' : undefined
                      }
                      className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-[#2a2e39]"
                    >
                      <span className="min-w-0 flex-1 truncate">{l.name}</span>
                      <span className="text-[11px] text-[#787b86]">
                        {there ? 'already there' : l.items.length}
                      </span>
                    </button>
                  );
                })}
              <button
                type="button"
                role="menuitem"
                onClick={() => moveTo(moving, null)}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-[#2a2e39]"
              >
                <Icons.plus size={14} />
                New list
              </button>
            </div>
          </Popover>
        ) : null}
        {!items.length ? (
          <div className="px-4 py-10 text-center text-[13px] text-[#787b86]">
            This list is empty. Use + to add a symbol.
          </div>
        ) : null}
      </div>
      {failed ? (
        <div className="border-t border-[#2a2e39] px-3 py-1.5 text-[11px] text-[#ffb74d]">
          Prices unavailable — retrying…
        </div>
      ) : null}
    </div>
  );
}
