'use client';

import { type PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from 'react';

import type { MomentumBar, MomentumTrade } from '../lib/momentum-api';

const DEFAULT_ZOOM_1M = 8;
const DEFAULT_ZOOM_5M = 2;

type Point = MomentumBar & {
  ema9: number | null;
  ema20: number | null;
  vwap: number | null;
  macd: number | null;
  signal: number | null;
  histogram: number | null;
};

const fmt = (value: number) => `₹${value.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;

function ema(values: number[], span: number) {
  const alpha = 2 / (span + 1);
  let value: number | null = null;
  return values.map((close, index) => {
    value = value === null ? close : close * alpha + value * (1 - alpha);
    return index < span - 1 ? null : value;
  });
}

function fiveMinuteBars(bars: MomentumBar[]): MomentumBar[] {
  return Array.from({ length: Math.floor(bars.length / 5) }, (_, bucket) => {
    const chunk = bars.slice(bucket * 5, bucket * 5 + 5);
    return {
      time: chunk[chunk.length - 1]!.time,
      open: chunk[0]!.open,
      high: Math.max(...chunk.map((bar) => bar.high)),
      low: Math.min(...chunk.map((bar) => bar.low)),
      close: chunk[chunk.length - 1]!.close,
      volume: chunk.reduce((sum, bar) => sum + bar.volume, 0),
    };
  });
}

function points(bars: MomentumBar[]): Point[] {
  const closes = bars.map((bar) => bar.close);
  const ema9 = ema(closes, 9);
  const ema20 = ema(closes, 20);
  const fast = ema(closes, 12);
  const slow = ema(closes, 26);
  const macd = fast.map((value, i) => {
    const slowValue = slow[i] ?? null;
    return value === null || slowValue === null ? null : value - slowValue;
  });
  const signal = ema(macd.map((value) => value ?? 0), 9).map((value, i) =>
    (macd[i] ?? null) === null ? null : value,
  );
  let cumPv = 0;
  let cumVol = 0;
  const indicators = bars.map((bar, i) => {
    cumPv += ((bar.high + bar.low + bar.close) / 3) * bar.volume;
    cumVol += bar.volume;
    return {
      ema9: ema9[i] ?? null,
      ema20: ema20[i] ?? null,
      vwap: cumVol ? cumPv / cumVol : null,
      macd: macd[i] ?? null,
      signal: signal[i] ?? null,
      histogram: (macd[i] ?? null) === null || (signal[i] ?? null) === null
        ? null
        : macd[i]! - signal[i]!,
    };
  });
  return bars.map((bar, i) => {
    const indicator = indicators[i] ?? null;
    return {
      ...bar,
      ema9: indicator?.ema9 ?? null,
      ema20: indicator?.ema20 ?? null,
      vwap: indicator?.vwap ?? null,
      macd: indicator?.macd ?? null,
      signal: indicator?.signal ?? null,
      histogram: indicator?.histogram ?? null,
    };
  });
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

function nearestBarIndex(bars: MomentumBar[], time: string | undefined) {
  if (!time || !bars.length) return -1;
  const needle = new Date(time).getTime();
  return bars.reduce(
    (best, bar, index) =>
      Math.abs(new Date(bar.time).getTime() - needle) < best.diff
        ? { index, diff: Math.abs(new Date(bar.time).getTime() - needle) }
        : best,
    { index: 0, diff: Number.POSITIVE_INFINITY },
  ).index;
}

export function MomentumTradeChart({
  trade,
  interval,
}: {
  trade: MomentumTrade;
  interval: '1m' | '5m';
}) {
  const allData = useMemo(() => {
    const raw = trade.chart?.bars ?? [];
    return interval === '5m' ? points(fiveMinuteBars(raw)) : points(raw);
  }, [interval, trade.chart?.bars]);
  const defaultZoom = interval === '5m' ? DEFAULT_ZOOM_5M : DEFAULT_ZOOM_1M;
  const [zoom, setZoom] = useState(defaultZoom);
  const [requestedStart, setRequestedStart] = useState<number | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const dragRef = useRef<{ pointerId: number; startX: number; startView: number } | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setZoom(defaultZoom);
    setRequestedStart(null);
  }, [trade._id, defaultZoom]);

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

  const fullEntryIndex = nearestBarIndex(allData, trade.entry_time);
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
  const toggleFullscreen = async () => {
    if (!chartRef.current) return;
    if (document.fullscreenElement === chartRef.current) await document.exitFullscreen();
    else await chartRef.current.requestFullscreen();
  };

  if (!allData.length) {
    return (
      <div className="rounded-xl border border-dashed border-black/15 bg-black/[0.02] px-4 py-10 text-center text-sm text-ink/55">
        No candle snapshot is available for this older trade. New closed trades automatically retain their
        one-minute bars for chart review.
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
  priceValues.push(trade.entry_price, trade.stop);
  if (trade.target) priceValues.push(trade.target);
  if (trade.exit_price) priceValues.push(trade.exit_price);
  const rawMin = Math.min(...priceValues);
  const rawMax = Math.max(...priceValues);
  const pricePad = Math.max((rawMax - rawMin) * 0.08, rawMax * 0.001);
  const minPrice = rawMin - pricePad;
  const maxPrice = rawMax + pricePad;
  const yPrice = (value: number) => priceTop + ((maxPrice - value) / (maxPrice - minPrice || 1)) * priceHeight;
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
  const fullExitIndex = nearestBarIndex(allData, trade.exit_time);
  const exitIndex = fullExitIndex >= viewStart && fullExitIndex < viewStart + data.length
    ? fullExitIndex - viewStart : -1;
  // A chart only renders patterns made on its own timeframe: a 1m formation
  // cannot be mistaken for a 5m signal during review.
  const visiblePatterns = (trade.pattern_matches ?? []).flatMap((match) => {
    if (match.timeframe !== interval) return [];
    const start = nearestBarIndex(allData, match.start);
    const end = nearestBarIndex(allData, match.end);
    if (end < viewStart || start >= viewStart + data.length) return [];
    return [{
      ...match,
      start: Math.max(0, start - viewStart),
      end: Math.min(data.length - 1, end - viewStart),
    }];
  });
  const entryY = yPrice(trade.entry_price);
  const exitY = trade.exit_price === undefined ? null : yPrice(trade.exit_price);
  // Park the exit label below the entry one when the two price levels almost coincide.
  const exitLabelY = exitY === null ? null : Math.abs(exitY - entryY) >= 13 ? exitY - 5 : entryY + 13;
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
      return { y, label: fmt(maxPrice - ((y - priceTop) / priceHeight) * (maxPrice - minPrice)) };
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
    // Offsets are measured from where the drag began, so a round trip lands back on the same candle.
    const barsPerPixel = Math.max(data.length - 1, 1) / (plotWidth * (rect.width / width));
    const shift = Math.round((event.clientX - drag.startX) * barsPerPixel);
    setRequestedStart(Math.min(maxStart, Math.max(0, drag.startView - shift)));
  };
  const startDrag = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (event.button !== 0 && event.pointerType === 'mouse') return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = { pointerId: event.pointerId, startX: event.clientX, startView: viewStart };
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
        else if (event.key === '0') { event.preventDefault(); setZoom(defaultZoom); setRequestedStart(null); }
        else if (event.key === 'ArrowLeft') { event.preventDefault(); pan(-1); }
        else if (event.key === 'ArrowRight') { event.preventDefault(); pan(1); }
      }}
      className="overflow-x-auto rounded-xl border border-black/10 bg-[#101922] p-2 shadow-inner outline-none focus:ring-2 focus:ring-accent fullscreen:flex fullscreen:flex-col fullscreen:justify-center fullscreen:rounded-none fullscreen:p-5"
      aria-label={`${trade.symbol} ${interval} chart; use plus or minus to zoom, zero to reset, and arrow keys to pan`}
    >
      <div className="flex min-w-[720px] items-center justify-between gap-3 px-2 pb-2 text-xs text-slate-300">
        <span>{zoom === 1 ? 'Full session' : `${zoom}× zoom · ${data.length} ${interval} candles`}</span>
        <div className="flex items-center gap-1">
          <button type="button" onClick={() => pan(-1)} disabled={viewStart === 0} className="rounded border border-white/20 px-2 py-1 disabled:cursor-not-allowed disabled:opacity-35" aria-label="Show earlier candles">←</button>
          <button type="button" onClick={zoomOut} disabled={zoom === 1} className="rounded border border-white/20 px-2 py-1 disabled:cursor-not-allowed disabled:opacity-35">− Zoom</button>
          <button type="button" onClick={zoomIn} disabled={zoom === 8 || visibleCount <= 15} className="rounded border border-white/20 px-2 py-1 disabled:cursor-not-allowed disabled:opacity-35">+ Zoom</button>
          <button type="button" onClick={() => { setZoom(defaultZoom); setRequestedStart(null); }} disabled={zoom === defaultZoom && requestedStart === null} className="rounded border border-white/20 px-2 py-1 disabled:cursor-not-allowed disabled:opacity-35">Reset</button>
          <button type="button" onClick={() => pan(1)} disabled={viewStart === maxStart} className="rounded border border-white/20 px-2 py-1 disabled:cursor-not-allowed disabled:opacity-35" aria-label="Show later candles">→</button>
          <button type="button" onClick={() => { void toggleFullscreen(); }} className="rounded border border-white/20 px-2 py-1">{isFullscreen ? 'Exit full screen' : 'Full screen'}</button>
        </div>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className={`min-w-[720px] w-full touch-pan-y select-none ${isDragging ? 'cursor-grabbing' : 'cursor-crosshair'}`}
        role="img"
        aria-label={`${trade.symbol} ${interval} candle chart`}
        onPointerMove={trackCursor}
        onPointerDown={startDrag}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onPointerLeave={() => { if (!dragRef.current) setCursor(null); }}
      >
        <rect width={width} height={height} rx="8" fill="#101922" />
        {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
          const yy = priceTop + priceHeight * ratio;
          const value = maxPrice - (maxPrice - minPrice) * ratio;
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
          return <g key={`${match.name}-${match.start}-${match.end}`} pointerEvents="none">
            <rect x={startX} y={priceTop} width={Math.max(2, endX - startX)} height={priceHeight} fill="#fbbf24" fillOpacity=".10" />
            <path d={`M ${startX} ${priceTop + 16} V ${priceTop + 7} H ${endX} V ${priceTop + 16}`} fill="none" stroke="#fbbf24" strokeWidth="1.2" />
            <text x={(startX + endX) / 2} y={priceTop + 31} textAnchor="middle" fill="#fde68a" fontSize="10">{match.name.replaceAll('_', ' ')} · {match.timeframe}</text>
          </g>;
        })}
        <path d={linePath(data.map((bar) => bar.ema9), x, yPrice)} fill="none" stroke="#fbbf24" strokeWidth="1.5" /><path d={linePath(data.map((bar) => bar.ema20), x, yPrice)} fill="none" stroke="#a78bfa" strokeWidth="1.5" /><path d={linePath(data.map((bar) => bar.vwap), x, yPrice)} fill="none" stroke="#60a5fa" strokeWidth="1.5" strokeDasharray="4 3" />
        {[['Stop', trade.stop, '#fb7185'], ['Target', trade.target, '#34d399']].map(([label, value, color]) => value ? <g key={label as string}><line x1={left} x2={width - right} y1={yPrice(value as number)} y2={yPrice(value as number)} stroke={color as string} strokeOpacity=".75" strokeDasharray="5 4" /><text x={width - right - 2} y={yPrice(value as number) - 4} textAnchor="end" fill={color as string} fontSize="11">{label as string} {fmt(value as number)}</text></g> : null)}
        <g><line x1={left} x2={width - right} y1={entryY} y2={entryY} stroke="#4ade80" strokeOpacity=".8" strokeDasharray="2 3" /><text x={left + 4} y={entryY - 5} fill="#bbf7d0" fontSize="11">BUY {fmt(trade.entry_price)}</text></g>
        {exitY !== null && trade.exit_price !== undefined && <g><line x1={left} x2={width - right} y1={exitY} y2={exitY} stroke="#f87171" strokeOpacity=".8" strokeDasharray="2 3" /><text x={left + 4} y={exitLabelY as number} fill="#fecaca" fontSize="11">SELL {fmt(trade.exit_price)}</text></g>}
        {entryIndex >= 0 && <path d={`M ${x(entryIndex) - 6} ${entryY + 13} L ${x(entryIndex) + 6} ${entryY + 13} L ${x(entryIndex)} ${entryY + 3} Z`} fill="#4ade80" />}
        {exitIndex >= 0 && exitY !== null && <path d={`M ${x(exitIndex) - 6} ${exitY - 13} L ${x(exitIndex) + 6} ${exitY - 13} L ${x(exitIndex)} ${exitY - 3} Z`} fill="#f87171" />}
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
        {Array.from({ length: tickCount }, (_, tick) => Math.round((tick / Math.max(tickCount - 1, 1)) * (data.length - 1))).map((index) => <text key={index} x={x(index)} y={570} textAnchor="middle" fill="#a9bac9" fontSize="11">{new Date(data[index]!.time).toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false })}</text>)}
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
            <text x={timeBadgeX + 24} y={570} textAnchor="middle" fill="#e2e8f0" fontSize="11">{new Date(cursorBar.time).toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false })}</text>
          </g>
        )}
      </svg>
      <div className="flex flex-wrap gap-x-4 gap-y-1 px-2 pb-1 text-xs text-slate-300"><span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-[#2dd4bf]" />{interval} up candle</span><span className="text-[#fbbf24]">EMA 9 / MACD</span><span className="text-[#a78bfa]">EMA 20 / signal</span><span className="text-[#60a5fa]">VWAP</span><span className="text-amber-200">▱ completed pattern</span><span className="text-emerald-300">▲ entry</span><span className="text-rose-300">▼ exit</span><span className="text-slate-400">Hover for price/time · drag to pan · focus chart: +/− zoom, 0 reset, ←/→ pan</span></div>
    </div>
  );
}
