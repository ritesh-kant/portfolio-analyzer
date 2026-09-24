'use client';

import { type PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from 'react';

import type { MomentumTrade } from '../lib/momentum-api';
import { containingBarIndex, fiveMinuteBars } from '../lib/momentum-bars';
import { points, sessionBars, tradeLevels } from '../lib/momentum-session';

// Mirrors candles.STRENGTH_WEAK_BELOW: a formation whose confirming candle
// spans less than this multiple of the recent average range is drawn faint.
const WEAK_STRENGTH = 0.75;
const DEFAULT_ZOOM_1M = 8;
const DEFAULT_ZOOM_5M = 2;
const DEFAULT_PRICE_ZOOM = 4;

/**
 * Where the chart is currency- and session-specific. Everything else in this
 * file is arithmetic on bars and works unchanged for either market.
 */
export interface ChartLocale {
  /** Prefix drawn before every price. */
  symbol: string;
  /** Number locale, for digit grouping only. */
  numberLocale: string;
  /** IANA zone the session's clock is drawn in. */
  timeZone: string;
}

export const NSE_LOCALE: ChartLocale = {
  symbol: '₹',
  numberLocale: 'en-IN',
  timeZone: 'Asia/Kolkata',
};

export const US_LOCALE: ChartLocale = {
  symbol: '$',
  numberLocale: 'en-US',
  timeZone: 'America/New_York',
};

const formatter = (locale: ChartLocale) => (value: number) =>
  `${locale.symbol}${value.toLocaleString(locale.numberLocale, { maximumFractionDigits: 2 })}`;

// Vertical de-collision for the horizontal price-line labels. Levels, BUY and
// SELL are all left-anchored at the same x, so when two lines sit within a few
// pixels (structural vs 5m-nearest, entry vs resistance) the texts print on top
// of each other. Sort by desired y, enforce a minimum gap, then shift the
// rigid block into the price panel. Lines stay where they are — only labels
// move, with a short leader joining a displaced label back to its line.
function stackLabelYs(desired: number[], gap = 13, minY = -Infinity, maxY = Infinity): number[] {
  if (desired.length === 0) return [];
  const order = desired.map((y, i) => i).sort((a, b) => desired[a]! - desired[b]!);
  const placed = new Array<number>(desired.length);
  // Pass 1: top-down, enforce the minimum gap.
  let prev = -Infinity;
  for (const i of order) {
    const next = Math.max(desired[i]!, prev + gap);
    placed[i] = next;
    prev = next;
  }
  // Pass 2: shift the whole block into the panel WITHOUT touching the gaps —
  // clamping each label independently would crush a stack sitting above the
  // top edge back onto a single y (SELL ₹456.1 on top of Resistance ₹456.1).
  const lo = Math.min(...placed);
  const hi = Math.max(...placed);
  let shift = 0;
  if (hi > maxY) shift = maxY - hi;
  if (lo + shift < minY) shift = minY - lo;
  for (let i = 0; i < placed.length; i++) placed[i]! += shift;
  // Pass 3: if the stack is taller than the panel, the shift above still
  // leaves one end hanging out — walk top-down from the top edge so labels
  // stay separated (a label may spill past the bottom instead of overprinting).
  let cursor = minY;
  for (const i of order) {
    if (placed[i]! < cursor) placed[i] = cursor;
    cursor = placed[i]! + gap;
  }
  return placed;
}

function linePath(values: Array<number | null>, x: (index: number) => number, y: (value: number) => number) {
  let started = false;
  return values.reduce((path, value, index) => {
    if (value === null || !Number.isFinite(value)) {
      started = false;
      return path;
    }
    const command = started ? 'L' : 'M';
    started = true;
    return `${path}${command}${x(index).toFixed(1)},${y(value).toFixed(1)} `;
  }, '');
}

/** A moment on a watchlist chart: when the scanner flagged the name, and why. */
export interface WatchMarker {
  time: string;
  label: string;
  /** A scanner flag (amber) or a headline published during the session (blue). */
  kind?: 'flag' | 'news';
}

export function MomentumTradeChart({
  trade,
  interval,
  locale = NSE_LOCALE,
  watchMarkers,
}: {
  trade: MomentumTrade;
  interval: '1m' | '5m';
  /** Defaults to NSE so every existing call site is unchanged. */
  locale?: ChartLocale;
  /**
   * Watchlist mode. The name was only watched, never traded, so the fill,
   * stop, target and level lines are meaningless and are not drawn; each
   * marker is drawn as a vertical line on the candle it falls in instead.
   */
  watchMarkers?: WatchMarker[];
}) {
  const watching = watchMarkers !== undefined;
  // The 4× default exists to pull tightly packed trade lines apart; a watched
  // name draws none, so it opens with the whole session's range in view.
  const defaultPriceZoom = watching ? 1 : DEFAULT_PRICE_ZOOM;
  const fmt = useMemo(() => formatter(locale), [locale]);
  const allData = useMemo(() => {
    // The stored series can begin before the opening bell — see `sessionBars`.
    const raw = sessionBars(trade.chart?.bars ?? [], trade.entry_time);
    return interval === '5m' ? points(fiveMinuteBars(raw)) : points(raw);
  }, [interval, trade.chart?.bars, trade.entry_time]);
  const defaultZoom = interval === '5m' ? DEFAULT_ZOOM_5M : DEFAULT_ZOOM_1M;
  const [zoom, setZoom] = useState(defaultZoom);
  const [requestedStart, setRequestedStart] = useState<number | null>(null);
  const [priceZoom, setPriceZoom] = useState(defaultPriceZoom);
  const [priceCenter, setPriceCenter] = useState<number | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const dragRef = useRef<{ pointerId: number; startX: number; startView: number; startY: number; startCenter: number } | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  // Pinch/ctrl+wheel zoom reads the latest handler through a ref so the native
  // (non-passive) listener can be attached once per SVG mount, not re-bound every render.
  const pinchZoomRef = useRef<(deltaY: number, clientX: number, clientY: number, rect: DOMRect) => void>(() => {});
  const wheelCleanupRef = useRef<(() => void) | null>(null);
  const setSvgRef = (node: SVGSVGElement | null) => {
    wheelCleanupRef.current?.();
    wheelCleanupRef.current = null;
    if (!node) return;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey) return; // trackpad pinch and ctrl+wheel report ctrlKey; plain scroll is left alone
      event.preventDefault();
      pinchZoomRef.current(event.deltaY, event.clientX, event.clientY, node.getBoundingClientRect());
    };
    node.addEventListener('wheel', onWheel, { passive: false });
    wheelCleanupRef.current = () => node.removeEventListener('wheel', onWheel);
  };

  useEffect(() => {
    setZoom(defaultZoom);
    setRequestedStart(null);
    setPriceZoom(defaultPriceZoom);
    setPriceCenter(null);
  }, [trade._id, defaultZoom, defaultPriceZoom]);

  useEffect(() => {
    const sync = () => setIsFullscreen(document.fullscreenElement === chartRef.current);
    document.addEventListener('fullscreenchange', sync);
    return () => document.removeEventListener('fullscreenchange', sync);
  }, []);

  // Pointer capture normally keeps the drag alive outside the SVG; this releases it even when a
  // release lands somewhere the chart never hears about, so a drag can never stick.
  useEffect(() => {
    if (!isDragging) return;
    const stop = (event: PointerEvent) => {
      dragRef.current = null;
      setIsDragging(false);
      if (event.target instanceof Node && !chartRef.current?.contains(event.target)) setCursor(null);
    };
    window.addEventListener('pointerup', stop);
    window.addEventListener('pointercancel', stop);
    return () => {
      window.removeEventListener('pointerup', stop);
      window.removeEventListener('pointercancel', stop);
    };
  }, [isDragging]);

  const fullEntryIndex = containingBarIndex(allData, trade.entry_time, interval);
  const visibleCount = Math.min(allData.length, Math.max(15, Math.ceil(allData.length / zoom)));
  const maxStart = Math.max(0, allData.length - visibleCount);
  const focusedStart = Math.min(
    Math.max(0, fullEntryIndex - Math.floor(visibleCount / 2)),
    maxStart,
  );
  const viewStart = Math.min(Math.max(0, requestedStart ?? focusedStart), maxStart);
  const data = allData.slice(viewStart, viewStart + visibleCount);
  const zoomIn = () => {
    setZoom((value) => Math.min(8, value * 2));
    setRequestedStart(null);
  };
  const zoomOut = () => {
    setZoom((value) => Math.max(1, value / 2));
    setRequestedStart(null);
  };
  const pan = (direction: -1 | 1) => {
    setRequestedStart(Math.min(maxStart, Math.max(0, viewStart + direction * Math.ceil(visibleCount * 0.7))));
  };
  const priceZoomIn = () => setPriceZoom((value) => Math.min(8, value * 2));
  const priceZoomOut = () => setPriceZoom((value) => Math.max(1, value / 2));
  const resetZoom = () => {
    setZoom(defaultZoom);
    setRequestedStart(null);
    setPriceZoom(defaultPriceZoom);
    setPriceCenter(null);
  };
  const toggleFullscreen = async () => {
    if (!chartRef.current) return;
    if (document.fullscreenElement === chartRef.current) await document.exitFullscreen();
    else await chartRef.current.requestFullscreen();
  };

  if (!allData.length) {
    return (
      <div className="rounded-xl border border-dashed border-black/15 bg-black/[0.02] px-4 py-10 text-center text-sm text-ink/55">
        {watching
          ? 'No candles are available for this name on this session.'
          : 'No candle snapshot is available for this older trade. New closed trades automatically retain their one-minute bars for chart review.'}
      </div>
    );
  }

  const width = 1000;
  const height = 580;
  const left = 64;
  const right = 18;
  const priceTop = 20;
  const priceHeight = 292;
  const macdTop = 350;
  const macdHeight = 92;
  const volumeTop = 478;
  const volumeHeight = 66;
  const plotWidth = width - left - right;
  const x = (index: number) => left + (index / Math.max(data.length - 1, 1)) * plotWidth;
  const candleWidth = Math.max(1, Math.min(7, (plotWidth / Math.max(data.length, 1)) * 0.68));
  const priceValues = data.flatMap((bar) => [bar.low, bar.high, bar.ema9, bar.ema20, bar.vwap])
    .filter((value): value is number => value !== null && Number.isFinite(value));
  if (!watching) {
    priceValues.push(trade.entry_price, trade.stop);
    if (trade.target) priceValues.push(trade.target);
    if (trade.exit_price) priceValues.push(trade.exit_price);
  }
  const levels = watching ? [] : tradeLevels(trade);
  for (const level of levels) priceValues.push(level.price);
  const rawMin = Math.min(...priceValues);
  const rawMax = Math.max(...priceValues);
  const pricePad = Math.max((rawMax - rawMin) * 0.08, rawMax * 0.001);
  const minPrice = rawMin - pricePad;
  const maxPrice = rawMax + pricePad;
  // Vertical zoom narrows the visible price band around a center so tightly-packed
  // lines (stop/target/support/resistance/entry) can be told apart.
  const priceSpan = (maxPrice - minPrice) / priceZoom;
  const priceCenterClamped = Math.min(
    maxPrice - priceSpan / 2,
    Math.max(minPrice + priceSpan / 2, priceCenter ?? (watching ? (rawMin + rawMax) / 2 : trade.entry_price)),
  );
  const viewMinPrice = priceCenterClamped - priceSpan / 2;
  const viewMaxPrice = priceCenterClamped + priceSpan / 2;
  const panPrice = (direction: -1 | 1) => {
    setPriceCenter(priceCenterClamped + priceSpan * 0.3 * direction);
  };
  const yPrice = (value: number) => priceTop + ((viewMaxPrice - value) / (viewMaxPrice - viewMinPrice || 1)) * priceHeight;
  // Zooms both axes together around the cursor's bar/price, so the pinched-in area stays under the pointer.
  const pinchZoom = (deltaY: number, clientX: number, clientY: number, rect: DOMRect) => {
    const factor = Math.exp(-deltaY * 0.01);
    const cursorX = ((clientX - rect.left) / rect.width) * width;
    const cursorY = ((clientY - rect.top) / rect.height) * height;

    const newZoom = Math.round(Math.min(8, Math.max(1, zoom * factor)) * 100) / 100;
    const fractionX = Math.min(1, Math.max(0, (cursorX - left) / plotWidth));
    const fullIndexAtCursor = viewStart + fractionX * (visibleCount - 1);
    const newVisibleCount = Math.min(allData.length, Math.max(15, Math.ceil(allData.length / newZoom)));
    const newMaxStart = Math.max(0, allData.length - newVisibleCount);
    const newViewStart = Math.min(newMaxStart, Math.max(0, Math.round(fullIndexAtCursor - fractionX * (newVisibleCount - 1))));
    setZoom(newZoom);
    setRequestedStart(newViewStart);

    if (cursorY >= priceTop && cursorY <= priceTop + priceHeight) {
      const newPriceZoom = Math.round(Math.min(8, Math.max(1, priceZoom * factor)) * 100) / 100;
      const fractionY = (cursorY - priceTop) / priceHeight;
      const priceAtCursor = viewMaxPrice - fractionY * (viewMaxPrice - viewMinPrice);
      const newPriceSpan = (maxPrice - minPrice) / newPriceZoom;
      const newCenter = priceAtCursor + newPriceSpan * (fractionY - 0.5);
      setPriceZoom(newPriceZoom);
      setPriceCenter(Math.min(maxPrice - newPriceSpan / 2, Math.max(minPrice + newPriceSpan / 2, newCenter)));
    }
  };
  pinchZoomRef.current = pinchZoom;
  const macdValues = data.flatMap((bar) => [bar.macd, bar.signal, bar.histogram])
    .filter((value): value is number => value !== null && Number.isFinite(value));
  const macdMin = Math.min(0, ...macdValues);
  const macdMax = Math.max(0, ...macdValues);
  const macdPad = Math.max((macdMax - macdMin) * 0.15, 0.01);
  const yMacd = (value: number) => macdTop + ((macdMax + macdPad - value) / (macdMax - macdMin + macdPad * 2 || 1)) * macdHeight;
  const volumeMax = Math.max(...data.map((bar) => bar.volume), 1);
  const fmtVolume = (value: number) =>
    value >= 1_000_000 ? `${(value / 1_000_000).toFixed(1)}M` : value >= 1_000 ? `${(value / 1_000).toFixed(1)}K` : `${Math.round(value)}`;
  const entryIndex = fullEntryIndex >= viewStart && fullEntryIndex < viewStart + data.length
    ? fullEntryIndex - viewStart : -1;
  const fullExitIndex = containingBarIndex(allData, trade.exit_time, interval);
  const exitIndex = fullExitIndex >= viewStart && fullExitIndex < viewStart + data.length
    ? fullExitIndex - viewStart : -1;
  // A chart only renders patterns made on its own timeframe: a 1m formation
  // cannot be mistaken for a 5m signal during review.
  const visiblePatterns = (trade.pattern_matches ?? []).flatMap((match) => {
    if (match.timeframe !== interval) return [];
    const start = allData.findIndex((bar) => Date.parse(bar.time) === Date.parse(match.start));
    const end = allData.findIndex((bar) => Date.parse(bar.time) === Date.parse(match.end));
    if (start < 0 || end < 0) return [];
    if (end < viewStart || start >= viewStart + data.length) return [];
    return [{
      ...match,
      start: Math.max(0, start - viewStart),
      end: Math.min(data.length - 1, end - viewStart),
    }];
  });
  const visibleMarkers = (watchMarkers ?? []).flatMap((marker) => {
    const index = containingBarIndex(allData, marker.time, interval);
    return index >= viewStart && index < viewStart + data.length ? [{ ...marker, index: index - viewStart }] : [];
  });
  const entryY = yPrice(trade.entry_price);
  const exitY = trade.exit_price == null ? null : yPrice(trade.exit_price);
  const tickCount = Math.min(6, data.length);
  const cursorIndex = cursor
    ? Math.min(
        data.length - 1,
        Math.max(0, Math.round(((cursor.x - left) / plotWidth) * Math.max(data.length - 1, 1))),
      )
    : -1;
  const cursorBar = cursorIndex >= 0 ? data[cursorIndex] : undefined;
  // The horizontal readout reports whichever panel the pointer sits in; between panels it is hidden.
  const cursorReadout = ((y) => {
    if (y === undefined) return null;
    if (y >= priceTop && y <= priceTop + priceHeight)
      return { y, label: fmt(viewMaxPrice - ((y - priceTop) / priceHeight) * (viewMaxPrice - viewMinPrice)) };
    if (y >= macdTop && y <= macdTop + macdHeight)
      return {
        y,
        label: (macdMax + macdPad - ((y - macdTop) / macdHeight) * (macdMax - macdMin + macdPad * 2)).toFixed(2),
      };
    if (y >= volumeTop && y <= volumeTop + volumeHeight)
      return { y, label: fmtVolume(((volumeTop + volumeHeight - y) / volumeHeight) * volumeMax) };
    return null;
  })(cursor?.y);
  const timeBadgeX = cursorBar ? Math.min(Math.max(x(cursorIndex) - 24, 2), width - 50) : 0;
  const trackCursor = (event: ReactPointerEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    setCursor({
      x: ((event.clientX - rect.left) / rect.width) * width,
      y: ((event.clientY - rect.top) / rect.height) * height,
    });
    const drag = dragRef.current;
    if (!drag) return;
    // Offsets are measured from where the drag began, so a round trip lands back on the same candle/price.
    const barsPerPixel = Math.max(data.length - 1, 1) / (plotWidth * (rect.width / width));
    const shift = Math.round((event.clientX - drag.startX) * barsPerPixel);
    setRequestedStart(Math.min(maxStart, Math.max(0, drag.startView - shift)));
    const pricePerPixel = priceSpan / (priceHeight * (rect.height / height));
    const priceShift = (event.clientY - drag.startY) * pricePerPixel;
    setPriceCenter(Math.min(maxPrice - priceSpan / 2, Math.max(minPrice + priceSpan / 2, drag.startCenter + priceShift)));
  };
  const startDrag = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (event.button !== 0 && event.pointerType === 'mouse') return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startView: viewStart,
      startY: event.clientY,
      startCenter: priceCenterClamped,
    };
    setIsDragging(true);
  };
  const endDrag = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
    dragRef.current = null;
    setIsDragging(false);
  };

  return (
    <div
      ref={chartRef}
      tabIndex={0}
      onPointerDown={(event) => event.currentTarget.focus()}
      onKeyDown={(event) => {
        if (event.key === '+' || event.key === '=') { event.preventDefault(); zoomIn(); }
        else if (event.key === '-') { event.preventDefault(); zoomOut(); }
        else if (event.key === '0') { event.preventDefault(); resetZoom(); }
        else if (event.key === 'ArrowLeft') { event.preventDefault(); pan(-1); }
        else if (event.key === 'ArrowRight') { event.preventDefault(); pan(1); }
        else if (event.key === 'ArrowUp') { event.preventDefault(); panPrice(1); }
        else if (event.key === 'ArrowDown') { event.preventDefault(); panPrice(-1); }
      }}
      className="rounded-xl border border-black/10 bg-[#101922] p-2 shadow-inner outline-none focus:ring-2 focus:ring-accent fullscreen:flex fullscreen:flex-col fullscreen:justify-center fullscreen:rounded-none fullscreen:p-5"
      aria-label={`${trade.symbol} ${interval} chart; use plus or minus to zoom, zero to reset, arrow left/right to pan, and arrow up/down to pan the price axis when vertically zoomed`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2 px-2 pb-2 text-xs text-slate-300">
        <span>{zoom <= 1 ? 'Full session' : `${zoom % 1 === 0 ? zoom : zoom.toFixed(1)}× zoom · ${data.length} ${interval} candles`}{priceZoom > 1 ? ` · ${priceZoom % 1 === 0 ? priceZoom : priceZoom.toFixed(1)}× price zoom` : ''}</span>
        <div className="flex flex-wrap items-center gap-1">
          <button type="button" onClick={() => pan(-1)} disabled={viewStart === 0} className="rounded border border-white/20 px-2 py-1 disabled:cursor-not-allowed disabled:opacity-35" aria-label="Show earlier candles">←</button>
          <button type="button" onClick={zoomOut} disabled={zoom <= 1} className="rounded border border-white/20 px-2 py-1 disabled:cursor-not-allowed disabled:opacity-35">− Zoom</button>
          <button type="button" onClick={zoomIn} disabled={zoom >= 8 || visibleCount <= 15} className="rounded border border-white/20 px-2 py-1 disabled:cursor-not-allowed disabled:opacity-35">+ Zoom</button>
          <button type="button" onClick={() => pan(1)} disabled={viewStart === maxStart} className="rounded border border-white/20 px-2 py-1 disabled:cursor-not-allowed disabled:opacity-35" aria-label="Show later candles">→</button>
          <span className="mx-1 h-4 w-px bg-white/15" aria-hidden="true" />
          <button type="button" onClick={() => panPrice(1)} disabled={priceZoom <= 1} className="rounded border border-white/20 px-2 py-1 disabled:cursor-not-allowed disabled:opacity-35" aria-label="Show higher prices">↑</button>
          <button type="button" onClick={priceZoomOut} disabled={priceZoom <= 1} className="rounded border border-white/20 px-2 py-1 disabled:cursor-not-allowed disabled:opacity-35">− V-Zoom</button>
          <button type="button" onClick={priceZoomIn} disabled={priceZoom >= 8} className="rounded border border-white/20 px-2 py-1 disabled:cursor-not-allowed disabled:opacity-35">+ V-Zoom</button>
          <button type="button" onClick={() => panPrice(-1)} disabled={priceZoom <= 1} className="rounded border border-white/20 px-2 py-1 disabled:cursor-not-allowed disabled:opacity-35" aria-label="Show lower prices">↓</button>
          <span className="mx-1 h-4 w-px bg-white/15" aria-hidden="true" />
          <button type="button" onClick={resetZoom} disabled={zoom === defaultZoom && requestedStart === null && priceZoom === defaultPriceZoom && priceCenter === null} className="rounded border border-white/20 px-2 py-1 disabled:cursor-not-allowed disabled:opacity-35">Reset</button>
          <button type="button" onClick={() => { void toggleFullscreen(); }} className="rounded border border-white/20 px-2 py-1">{isFullscreen ? 'Exit full screen' : 'Full screen'}</button>
        </div>
      </div>
      <svg
        ref={setSvgRef}
        viewBox={`0 0 ${width} ${height}`}
        className={`w-full touch-pan-y select-none ${isDragging ? 'cursor-grabbing' : 'cursor-crosshair'}`}
        role="img"
        aria-label={`${trade.symbol} ${interval} candle chart`}
        onPointerMove={trackCursor}
        onPointerDown={startDrag}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onPointerLeave={() => { if (!dragRef.current) setCursor(null); }}
      >
        <rect width={width} height={height} rx="8" fill="#101922" />
        <text x={left + 6} y={priceTop + 16} fill="#e2e8f0" fontSize="13" fontWeight="600" opacity=".85">{trade.symbol}</text>
        {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
          const yy = priceTop + priceHeight * ratio;
          const value = viewMaxPrice - (viewMaxPrice - viewMinPrice) * ratio;
          return <g key={ratio}><line x1={left} x2={width - right} y1={yy} y2={yy} stroke="#ffffff" strokeOpacity=".10" /><text x={4} y={yy + 4} fill="#a9bac9" fontSize="11">{fmt(value)}</text></g>;
        })}
        {data.map((bar, index) => {
          const up = bar.close >= bar.open;
          const color = up ? '#2dd4bf' : '#fb7185';
          const cx = x(index);
          const bodyTop = yPrice(Math.max(bar.open, bar.close));
          const bodyBottom = yPrice(Math.min(bar.open, bar.close));
          return <g key={bar.time}><line x1={cx} x2={cx} y1={yPrice(bar.high)} y2={yPrice(bar.low)} stroke={color} strokeWidth="1" /><rect x={cx - candleWidth / 2} y={bodyTop} width={candleWidth} height={Math.max(1, bodyBottom - bodyTop)} fill={color} /></g>;
        })}
        {visiblePatterns.map((match) => {
          const startX = x(match.start) - candleWidth;
          const endX = x(match.end) + candleWidth;
          // A correctly-named formation on a candle much smaller than this
          // stock's recent average is drawn faint: the label is right, the
          // candle is not worth acting on. See candles.STRENGTH_WEAK_BELOW.
          const weak = match.strength !== undefined && match.strength < WEAK_STRENGTH;
          const size = match.strength === undefined ? '' : ` · ${match.strength.toFixed(2)}×`;
          const midX = Math.min(Math.max((startX + endX) / 2, left + 70), width - right - 70);
          return <g key={`${match.name}-${match.start}-${match.end}`} pointerEvents="none" opacity={weak ? 0.45 : 1}>
            <rect x={startX} y={priceTop} width={Math.max(2, endX - startX)} height={priceHeight} fill="#fbbf24" fillOpacity={weak ? '.04' : '.10'} />
            <path d={`M ${startX} ${priceTop + 16} V ${priceTop + 7} H ${endX} V ${priceTop + 16}`} fill="none" stroke="#fbbf24" strokeWidth="1.2" strokeDasharray={weak ? '3 3' : undefined} />
            <text x={midX} y={priceTop + 31} textAnchor="middle" fill="#fde68a" fontSize="10" stroke="#101922" strokeWidth="3" paintOrder="stroke">{match.name.replaceAll('_', ' ')} · {match.timeframe}{size}</text>
          </g>;
        })}
        <path d={linePath(data.map((bar) => bar.ema9), x, yPrice)} fill="none" stroke="#fbbf24" strokeWidth="1.5" /><path d={linePath(data.map((bar) => bar.ema20), x, yPrice)} fill="none" stroke="#a78bfa" strokeWidth="1.5" /><path d={linePath(data.map((bar) => bar.vwap), x, yPrice)} fill="none" stroke="#60a5fa" strokeWidth="1.5" strokeDasharray="4 3" />
        {visibleMarkers.map((marker, i) => {
          const news = marker.kind === 'news';
          // Flags label from the bottom of the price panel, news from the top,
          // so the two families never print over each other.
          const labelY = news ? priceTop + 46 + (i % 3) * 13 : priceTop + priceHeight - 6 - (i % 3) * 13;
          return <g key={`watch-${marker.kind ?? 'flag'}-${marker.time}-${i}`} pointerEvents="none">
            <line x1={x(marker.index)} x2={x(marker.index)} y1={priceTop} y2={volumeTop + volumeHeight} stroke={news ? '#38bdf8' : '#f59e0b'} strokeOpacity=".7" strokeDasharray={news ? '1 3' : '4 3'} />
            <text x={Math.min(x(marker.index) + 4, width - right - 200)} y={labelY} fill={news ? '#7dd3fc' : '#fcd34d'} fontSize="10" stroke="#101922" strokeWidth="3" paintOrder="stroke">{news ? '📰' : '👀'} {marker.label}</text>
          </g>;
        })}
        {!watching && [['Stop', trade.stop, '#fb7185'], ['Target', trade.target, '#34d399']].map(([label, value, color]) => value ? <g key={label as string}><line x1={left} x2={width - right} y1={yPrice(value as number)} y2={yPrice(value as number)} stroke={color as string} strokeOpacity=".75" strokeDasharray="5 4" /><text x={width - right - 2} y={yPrice(value as number) - 4} textAnchor="end" fill={color as string} fontSize="11">{label as string} {fmt(value as number)}</text></g> : null)}
        {!watching && (() => {
          // All of these labels share the same left-anchored x, so stack them
          // vertically instead of letting close lines overwrite each other.
          // The line itself never moves; a displaced label gets a leader tick.
          type LeftLabel = { key: string; lineY: number; text: string; color: string; opacity?: number };
          // Levels within 0.3% of the fill are noise against the BUY/SELL lines —
          // merge them into the trade lines instead of stacking near-duplicate
          // labels (SELL ₹456.1 vs Resistance ₹456.1 · pivot high).
          const merged = levels.filter(
            (level) =>
              Math.abs(level.price - trade.entry_price) / trade.entry_price > 0.003 &&
              (trade.exit_price == null ||
                Math.abs(level.price - trade.exit_price) / trade.exit_price > 0.003),
          );
          const items: LeftLabel[] = [
            ...merged.map((level) => {
              const color = level.side === 'resistance' ? '#f472b6' : '#38bdf8';
              return {
                key: level.label,
                lineY: yPrice(level.price),
                text: `${level.label} ${fmt(level.price)}${level.kind ? ` · ${level.kind.replaceAll('_', ' ')}` : ''}`,
                color,
                opacity: level.faint ? 0.7 : 1,
              };
            }),
            { key: 'BUY', lineY: entryY, text: `BUY ${fmt(trade.entry_price)}`, color: '#bbf7d0' },
            ...(exitY !== null && trade.exit_price != null
              ? [{ key: 'SELL', lineY: exitY, text: `SELL ${fmt(trade.exit_price)}`, color: '#fecaca' }]
              : []),
            // A label whose line is panned/zoomed out of the price panel has
            // nothing to point at — clamping it to the panel edge is what
            // stacked SELL ₹456.1 on top of Resistance ₹456.1. Hide it with
            // its (already clipped) line instead.
          ].filter((item) => item.lineY >= priceTop && item.lineY <= priceTop + priceHeight);
          const labelYs = stackLabelYs(
            items.map((item) => item.lineY - 4),
            13,
            priceTop + 38,
            priceTop + priceHeight - 4,
          );
          return items.map((item, i) => {
            const labelY = labelYs[i]!;
            const displaced = Math.abs(labelY - (item.lineY - 4)) > 0.5;
            return (
              <g key={item.key}>
                <line x1={left} x2={width - right} y1={item.lineY} y2={item.lineY} stroke={item.color} strokeOpacity=".55" strokeDasharray={item.key === 'BUY' || item.key === 'SELL' ? '2 3' : undefined} />
                {displaced && <line x1={left + 4} x2={left + 4} y1={item.lineY} y2={labelY} stroke={item.color} strokeOpacity=".55" strokeWidth="1" />}
                <text x={left + 6} y={labelY} fill={item.color} fontSize="11" fillOpacity={item.opacity ?? 1} stroke="#101922" strokeWidth="3" paintOrder="stroke">{item.text}</text>
              </g>
            );
          });
        })()}
        {!watching && entryIndex >= 0 && <path d={`M ${x(entryIndex) - 6} ${entryY + 13} L ${x(entryIndex) + 6} ${entryY + 13} L ${x(entryIndex)} ${entryY + 3} Z`} fill="#4ade80" />}
        {!watching && exitIndex >= 0 && exitY !== null && <path d={`M ${x(exitIndex) - 6} ${exitY - 13} L ${x(exitIndex) + 6} ${exitY - 13} L ${x(exitIndex)} ${exitY - 3} Z`} fill="#f87171" />}
        {[0, 0.5, 1].map((ratio) => {
          const yy = macdTop + macdHeight * ratio;
          const value = (macdMax + macdPad) - (macdMax - macdMin + macdPad * 2) * ratio;
          return <g key={`macd-tick-${ratio}`}><line x1={left} x2={width - right} y1={yy} y2={yy} stroke="#ffffff" strokeOpacity=".08" /><text x={4} y={yy + 3} fill="#a9bac9" fontSize="10">{value.toFixed(2)}</text></g>;
        })}
        <line x1={left} x2={width - right} y1={yMacd(0)} y2={yMacd(0)} stroke="#ffffff" strokeOpacity=".25" />{data.map((bar, index) => bar.histogram === null ? null : <rect key={`hist-${bar.time}`} x={x(index) - candleWidth / 2} y={Math.min(yMacd(0), yMacd(bar.histogram))} width={candleWidth} height={Math.max(1, Math.abs(yMacd(bar.histogram) - yMacd(0)))} fill={bar.histogram >= 0 ? '#2dd4bf' : '#fb7185'} opacity=".75" />)}<path d={linePath(data.map((bar) => bar.macd), x, yMacd)} fill="none" stroke="#fbbf24" strokeWidth="1.4" /><path d={linePath(data.map((bar) => bar.signal), x, yMacd)} fill="none" stroke="#a78bfa" strokeWidth="1.4" /><text x={left + 4} y={macdTop + 10} fill="#a9bac9" fontSize="11">MACD</text>
        {[0, 0.5, 1].map((ratio) => {
          const yy = volumeTop + volumeHeight * (1 - ratio);
          return <g key={`vol-tick-${ratio}`}><line x1={left} x2={width - right} y1={yy} y2={yy} stroke="#ffffff" strokeOpacity=".08" /><text x={4} y={yy + 3} fill="#a9bac9" fontSize="10">{fmtVolume(volumeMax * ratio)}</text></g>;
        })}
        {data.map((bar, index) => <rect key={`vol-${bar.time}`} x={x(index) - candleWidth / 2} y={volumeTop + volumeHeight - (bar.volume / volumeMax) * volumeHeight} width={candleWidth} height={(bar.volume / volumeMax) * volumeHeight} fill={bar.close >= bar.open ? '#2dd4bf' : '#fb7185'} opacity=".65" />)}
        <text x={left + 4} y={volumeTop + 10} fill="#a9bac9" fontSize="11">Volume</text>
        {Array.from({ length: tickCount }, (_, tick) => Math.round((tick / Math.max(tickCount - 1, 1)) * (data.length - 1))).map((index) => <text key={index} x={x(index)} y={570} textAnchor="middle" fill="#a9bac9" fontSize="11">{new Date(data[index]!.time).toLocaleTimeString(locale.numberLocale, { timeZone: locale.timeZone, hour: '2-digit', minute: '2-digit', hour12: false })}</text>)}
        {cursorBar && (
          <g pointerEvents="none">
            <line x1={x(cursorIndex)} x2={x(cursorIndex)} y1={priceTop} y2={volumeTop + volumeHeight} stroke="#cbd5e1" strokeOpacity=".55" strokeDasharray="3 3" />
            {cursorReadout && (
              <g>
                <line x1={left} x2={width - right} y1={cursorReadout.y} y2={cursorReadout.y} stroke="#cbd5e1" strokeOpacity=".55" strokeDasharray="3 3" />
                <rect x={2} y={cursorReadout.y - 9} width={60} height={18} rx="3" fill="#22303f" stroke="#cbd5e1" strokeOpacity=".4" />
                <text x={32} y={cursorReadout.y + 4} textAnchor="middle" fill="#e2e8f0" fontSize="11">{cursorReadout.label}</text>
              </g>
            )}
            <rect x={timeBadgeX} y={557} width={48} height={18} rx="3" fill="#22303f" stroke="#cbd5e1" strokeOpacity=".4" />
            <text x={timeBadgeX + 24} y={570} textAnchor="middle" fill="#e2e8f0" fontSize="11">{new Date(cursorBar.time).toLocaleTimeString(locale.numberLocale, { timeZone: locale.timeZone, hour: '2-digit', minute: '2-digit', hour12: false })}</text>
          </g>
        )}
      </svg>
      <div className="flex flex-wrap gap-x-4 gap-y-1 px-2 pb-1 text-xs text-slate-300"><span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-[#2dd4bf]" />{interval} up candle</span><span className="text-[#fbbf24]">EMA 9 / MACD</span><span className="text-[#a78bfa]">EMA 20 / signal</span><span className="text-[#60a5fa]">VWAP</span><span className="text-amber-200">▱ completed pattern</span>{levels.length > 0 && <><span className="text-[#f472b6]">— resistance</span><span className="text-[#38bdf8]">— support</span><span className="text-slate-400">faint = recorded only, gates nothing</span></>}{watching ? <><span className="text-amber-300">┆ 👀 flagged by scanner</span>{watchMarkers?.some((m) => m.kind === 'news') && <span className="text-sky-300">┆ 📰 headline published</span>}</> : <><span className="text-emerald-300">▲ entry</span><span className="text-rose-300">▼ exit</span></>}<span className="text-slate-400">Hover for price/time · drag to pan (vertical too, once V-zoomed) · ctrl+scroll or trackpad pinch to zoom · focus chart: +/− zoom, 0 reset, ←/→ pan, ↑/↓ pan price axis</span></div>
    </div>
  );
}
