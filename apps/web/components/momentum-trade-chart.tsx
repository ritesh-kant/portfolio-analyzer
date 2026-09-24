'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  type AutoscaleInfo,
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  type IChartApi,
  type IPriceLine,
  type IPrimitivePaneRenderer,
  type IPrimitivePaneView,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type ISeriesPrimitive,
  LineSeries,
  LineStyle,
  type SeriesAttachedParameter,
  type SeriesMarker,
  type SeriesType,
  type Time,
  type UTCTimestamp,
  createChart,
  createSeriesMarkers,
} from 'lightweight-charts';

import type { MomentumTrade } from '../lib/momentum-api';
import { containingBarIndex, fiveMinuteBars } from '../lib/momentum-bars';
import { type Point, points, sessionBars, tradeLevels } from '../lib/momentum-session';

// Mirrors candles.STRENGTH_WEAK_BELOW: a formation whose confirming candle
// spans less than this multiple of the recent average range is drawn faint.
const WEAK_STRENGTH = 0.75;
const DEFAULT_ZOOM_1M = 8;
const DEFAULT_ZOOM_5M = 2;
const MIN_VISIBLE_BARS = 15;
/** Room the OHLC legend takes at the top-left of the price pane. */
const LEGEND_HEIGHT = 44;

/**
 * TradingView's own dark theme and indicator defaults, so these charts read
 * the same as the charts the operator already uses there.
 */
const TV = {
  background: '#131722',
  grid: 'rgba(42, 46, 57, 0.6)',
  border: '#2a2e39',
  text: '#b2b5be',
  textStrong: '#d1d4dc',
  crosshair: '#9598a1',
  crosshairLabel: '#363a45',
  up: '#089981',
  down: '#f23645',
  volumeUp: 'rgba(8, 153, 129, 0.45)',
  volumeDown: 'rgba(242, 54, 69, 0.45)',
  ema9: '#ffb74d',
  ema20: '#ab47bc',
  vwap: '#2962ff',
  macd: '#2962ff',
  signal: '#ff6d00',
  histUpGrowing: '#26a69a',
  histUpFalling: '#b2dfdb',
  histDownFalling: '#ff5252',
  histDownRising: '#ffcdd2',
  pattern: '#fbbf24',
  patternText: '#fde68a',
  resistance: '#f472b6',
  support: '#38bdf8',
  buy: '#86efac',
  sell: '#fca5a5',
  stop: '#f23645',
  target: '#089981',
  flag: '#f59e0b',
  flagText: '#fcd34d',
  news: '#38bdf8',
  newsText: '#7dd3fc',
};
const FONT = '-apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, Ubuntu, sans-serif';

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

const fmtVolume = (value: number) =>
  value >= 1_000_000 ? `${(value / 1_000_000).toFixed(2)}M` : value >= 1_000 ? `${(value / 1_000).toFixed(2)}K` : `${Math.round(value)}`;

const zoneClocks = new Map<string, Intl.DateTimeFormat>();

/**
 * lightweight-charts draws every timestamp as if it were UTC. Handing it each
 * bar's wall-clock time in the market's own zone makes the axis read 09:15
 * for an NSE open and 09:30 for a US one, on any viewer's machine — the
 * library's documented way to show a local session.
 */
function wallClock(ms: number, timeZone: string): UTCTimestamp {
  let clock = zoneClocks.get(timeZone);
  if (!clock) {
    clock = new Intl.DateTimeFormat('en-US', {
      timeZone,
      hourCycle: 'h23',
      year: 'numeric',
      month: 'numeric',
      day: 'numeric',
      hour: 'numeric',
      minute: 'numeric',
      second: 'numeric',
    });
    zoneClocks.set(timeZone, clock);
  }
  const part: Record<string, number> = {};
  for (const { type, value } of clock.formatToParts(ms)) if (type !== 'literal') part[type] = Number(value);
  return (Date.UTC(part.year ?? 1970, (part.month ?? 1) - 1, part.day ?? 1, part.hour ?? 0, part.minute ?? 0, part.second ?? 0) / 1000) as UTCTimestamp;
}

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

type Attached = SeriesAttachedParameter<Time, SeriesType>;
type Paint = (ctx: CanvasRenderingContext2D, size: { width: number; height: number }, api: Attached) => void;

/**
 * A canvas layer inside one series' pane, for the drawings the library has no
 * built-in for (pattern brackets, stacked level labels, watch lines, the MACD
 * legend). It repaints with the chart, so pans and zooms stay in register.
 */
class CanvasOverlay implements ISeriesPrimitive<Time> {
  private api: Attached | null = null;
  private readonly views: readonly IPrimitivePaneView[];

  constructor(paint: Paint, layer: 'bottom' | 'normal' | 'top' = 'top') {
    const renderer: IPrimitivePaneRenderer = {
      draw: (target) => {
        const api = this.api;
        if (!api) return;
        target.useMediaCoordinateSpace(({ context, mediaSize }) => paint(context, mediaSize, api));
      },
    };
    this.views = [{ zOrder: () => layer, renderer: () => renderer }];
  }

  attached(api: Attached) {
    this.api = api;
  }

  detached() {
    this.api = null;
  }

  paneViews() {
    return this.views;
  }

  redraw() {
    this.api?.requestUpdate();
  }
}

function haloText(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  color: string,
  { align = 'left', alpha = 1, size = 11 }: { align?: CanvasTextAlign; alpha?: number; size?: number } = {},
) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.font = `${size}px ${FONT}`;
  ctx.textAlign = align;
  ctx.lineJoin = 'round';
  ctx.lineWidth = 3;
  ctx.strokeStyle = TV.background;
  ctx.strokeText(text, x, y);
  ctx.fillStyle = color;
  ctx.fillText(text, x, y);
  ctx.restore();
}

/** A moment on a watchlist chart: when the scanner flagged the name, and why. */
export interface WatchMarker {
  time: string;
  label: string;
  /** A scanner flag (amber) or a headline published during the session (blue). */
  kind?: 'flag' | 'news';
}

/** Everything the canvas overlays draw, rebuilt whenever the trade changes. */
interface Scene {
  patterns: Array<{ start: Time; end: Time; label: string; weak: boolean }>;
  labels: Array<{ key: string; price: number; text: string; color: string; opacity: number }>;
  watch: Array<{ time: Time; label: string; news: boolean }>;
  /** Prices the vertical autoscale must always keep in view: the fill, stop and exit. */
  anchorPrices: number[];
  data: Point[];
}

interface ChartHandles {
  chart: IChartApi;
  candles: ISeriesApi<'Candlestick'>;
  volume: ISeriesApi<'Histogram'>;
  ema9: ISeriesApi<'Line'>;
  ema20: ISeriesApi<'Line'>;
  vwap: ISeriesApi<'Line'>;
  histogram: ISeriesApi<'Histogram'>;
  macd: ISeriesApi<'Line'>;
  signal: ISeriesApi<'Line'>;
  markers: ISeriesMarkersPluginApi<Time>;
  overlays: CanvasOverlay[];
  priceLines: IPriceLine[];
}

const hexAlpha = (hex: string, alpha: number) =>
  `${hex}${Math.round(alpha * 255).toString(16).padStart(2, '0')}`;

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
  const fmt = useMemo(() => formatter(locale), [locale]);
  const allData = useMemo(() => {
    // The stored series can begin before the opening bell — see `sessionBars`.
    const raw = sessionBars(trade.chart?.bars ?? [], trade.entry_time);
    return interval === '5m' ? points(fiveMinuteBars(raw)) : points(raw);
  }, [interval, trade.chart?.bars, trade.entry_time]);
  const defaultZoom = interval === '5m' ? DEFAULT_ZOOM_5M : DEFAULT_ZOOM_1M;
  const levels = useMemo(() => (watching ? [] : tradeLevels(trade)), [trade, watching]);

  const [hover, setHover] = useState<number | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const handlesRef = useRef<ChartHandles | null>(null);
  const sceneRef = useRef<Scene>({ patterns: [], labels: [], watch: [], anchorPrices: [], data: [] });
  const hoverRef = useRef<number | null>(null);
  const timeIndexRef = useRef(new Map<number, number>());
  const framedRef = useRef<string | null>(null);
  const frameRef = useRef<() => void>(() => {});
  const pendingFrameRef = useRef(false);
  const hasData = allData.length > 0;

  useEffect(() => {
    const sync = () => setIsFullscreen(document.fullscreenElement === containerRef.current);
    document.addEventListener('fullscreenchange', sync);
    return () => document.removeEventListener('fullscreenchange', sync);
  }, []);

  // The chart itself: built once per mount, restyled never, fed by the effect below.
  useEffect(() => {
    const host = hostRef.current;
    if (!host || !hasData) return;
    const chart = createChart(host, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: TV.background },
        textColor: TV.text,
        fontSize: 11,
        fontFamily: FONT,
        attributionLogo: true,
        panes: { separatorColor: TV.border, separatorHoverColor: 'rgba(41, 98, 255, 0.25)', enableResize: true },
      },
      grid: { vertLines: { color: TV.grid }, horzLines: { color: TV.grid } },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: TV.crosshair, width: 1, style: LineStyle.Dashed, labelBackgroundColor: TV.crosshairLabel },
        horzLine: { color: TV.crosshair, width: 1, style: LineStyle.Dashed, labelBackgroundColor: TV.crosshairLabel },
      },
      rightPriceScale: { borderColor: TV.border },
      timeScale: { borderColor: TV.border, timeVisible: true, secondsVisible: false, rightOffset: 4 },
      localization: {
        locale: locale.numberLocale,
        // Times are already shifted to the market's wall clock (see `wallClock`), so read them back as UTC.
        timeFormatter: (time: Time) =>
          typeof time === 'number'
            ? new Date(time * 1000).toLocaleString('en-GB', {
                timeZone: 'UTC',
                weekday: 'short',
                day: '2-digit',
                month: 'short',
                year: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
                hour12: false,
              })
            : String(time),
      },
      // Plain vertical scroll is left to the page — several charts stack on one
      // screen. Horizontal swipe pans; pinch / ctrl+wheel zooms (listener below).
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    });

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: TV.up,
      downColor: TV.down,
      wickUpColor: TV.up,
      wickDownColor: TV.down,
      borderVisible: false,
      priceLineVisible: false,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
      // Keep the fill, stop and exit on screen however far the view is panned
      // — a trade chart whose stop is off the top edge hides the risk it took.
      autoscaleInfoProvider: (original: () => AutoscaleInfo | null) => {
        const base = original();
        const extra = sceneRef.current.anchorPrices;
        if (!base?.priceRange || extra.length === 0) return base;
        return {
          ...base,
          priceRange: {
            minValue: Math.min(base.priceRange.minValue, ...extra),
            maxValue: Math.max(base.priceRange.maxValue, ...extra),
          },
        };
      },
    });
    candles.priceScale().applyOptions({ scaleMargins: { top: 0.14, bottom: 0.22 } });
    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
      lastValueVisible: false,
      priceLineVisible: false,
    });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    const line = (color: string, lineWidth: 1 | 2, paneIndex = 0) =>
      chart.addSeries(LineSeries, {
        color,
        lineWidth,
        priceLineVisible: false,
        lastValueVisible: paneIndex === 1,
        crosshairMarkerVisible: false,
      }, paneIndex);
    const ema9 = line(TV.ema9, 1);
    const ema20 = line(TV.ema20, 1);
    const vwap = line(TV.vwap, 2);
    const histogram = chart.addSeries(HistogramSeries, {
      priceLineVisible: false,
      lastValueVisible: false,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    }, 1);
    const macd = line(TV.macd, 1, 1);
    const signal = line(TV.signal, 1, 1);
    macd.createPriceLine({ price: 0, color: TV.crosshairLabel, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: '' });
    const panes = chart.panes();
    panes[0]?.setStretchFactor(3);
    panes[1]?.setStretchFactor(1);
    const markers = createSeriesMarkers(candles, []);

    const patternBands = new CanvasOverlay((ctx, size, api) => {
      const ts = api.chart.timeScale();
      const half = ts.options().barSpacing / 2;
      for (const pattern of sceneRef.current.patterns) {
        const a = ts.timeToCoordinate(pattern.start);
        const b = ts.timeToCoordinate(pattern.end);
        if (a === null || b === null) continue;
        ctx.fillStyle = hexAlpha(TV.pattern, pattern.weak ? 0.04 : 0.1);
        ctx.fillRect(a - half, 0, Math.max(2, b - a + half * 2), size.height);
      }
    }, 'bottom');

    const priceOverlay = new CanvasOverlay((ctx, size, api) => {
      const ts = api.chart.timeScale();
      const half = ts.options().barSpacing / 2;
      const scene = sceneRef.current;
      for (const pattern of scene.patterns) {
        const a = ts.timeToCoordinate(pattern.start);
        const b = ts.timeToCoordinate(pattern.end);
        if (a === null || b === null) continue;
        const startX = a - half;
        const endX = b + half;
        // Panned out of view: the label would otherwise clamp to an edge and name nothing on screen.
        if (endX < 0 || startX > size.width) continue;
        const top = LEGEND_HEIGHT + 6;
        ctx.save();
        ctx.globalAlpha = pattern.weak ? 0.45 : 1;
        ctx.strokeStyle = TV.pattern;
        ctx.lineWidth = 1.2;
        ctx.setLineDash(pattern.weak ? [3, 3] : []);
        ctx.beginPath();
        ctx.moveTo(startX, top + 9);
        ctx.lineTo(startX, top);
        ctx.lineTo(endX, top);
        ctx.lineTo(endX, top + 9);
        ctx.stroke();
        ctx.restore();
        const midX = Math.min(Math.max((startX + endX) / 2, 80), size.width - 80);
        haloText(ctx, pattern.label, midX, top + 22, TV.patternText, { align: 'center', alpha: pattern.weak ? 0.45 : 1, size: 10 });
      }

      scene.watch.forEach((marker, i) => {
        const x = ts.timeToCoordinate(marker.time);
        if (x === null) return;
        ctx.save();
        ctx.strokeStyle = marker.news ? TV.news : TV.flag;
        ctx.globalAlpha = 0.7;
        ctx.setLineDash(marker.news ? [1, 3] : [4, 3]);
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, size.height);
        ctx.stroke();
        ctx.restore();
        // Flags label from the bottom of the price pane, news from the top,
        // so the two families never print over each other.
        const y = marker.news ? LEGEND_HEIGHT + 14 + (i % 3) * 13 : size.height - 6 - (i % 3) * 13;
        haloText(ctx, `${marker.news ? '📰' : '👀'} ${marker.label}`, Math.min(x + 4, size.width - 200), y, marker.news ? TV.newsText : TV.flagText, { size: 10 });
      });

      // Every label shares the same left-anchored x, so stack them vertically
      // instead of letting close lines overwrite each other. The line itself
      // never moves; a displaced label gets a leader tick. A label whose line
      // is panned out of the pane has nothing to point at and is hidden.
      const items = scene.labels
        .map((label) => ({ ...label, lineY: api.series.priceToCoordinate(label.price) }))
        .filter((item): item is typeof item & { lineY: number } =>
          item.lineY !== null && item.lineY >= 0 && item.lineY <= size.height);
      const ys = stackLabelYs(items.map((item) => item.lineY - 4), 13, LEGEND_HEIGHT + 34, size.height - 4);
      items.forEach((item, i) => {
        const y = ys[i]!;
        if (Math.abs(y - (item.lineY - 4)) > 0.5) {
          ctx.save();
          ctx.strokeStyle = item.color;
          ctx.globalAlpha = 0.55;
          ctx.beginPath();
          ctx.moveTo(4, item.lineY);
          ctx.lineTo(4, y);
          ctx.stroke();
          ctx.restore();
        }
        haloText(ctx, item.text, 8, y, item.color, { alpha: item.opacity });
      });
    });

    const watchLinesMacd = new CanvasOverlay((ctx, size, api) => {
      const ts = api.chart.timeScale();
      for (const marker of sceneRef.current.watch) {
        const x = ts.timeToCoordinate(marker.time);
        if (x === null) continue;
        ctx.save();
        ctx.strokeStyle = marker.news ? TV.news : TV.flag;
        ctx.globalAlpha = 0.5;
        ctx.setLineDash(marker.news ? [1, 3] : [4, 3]);
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, size.height);
        ctx.stroke();
        ctx.restore();
      }
      // TradingView's per-pane indicator legend: name, inputs, then the values at the cursor.
      const data = sceneRef.current.data;
      const bar = data[hoverRef.current ?? data.length - 1];
      ctx.save();
      ctx.font = `12px ${FONT}`;
      ctx.textBaseline = 'top';
      let x = 8;
      const put = (text: string, color: string) => {
        ctx.fillStyle = color;
        ctx.fillText(text, x, 6);
        x += ctx.measureText(text).width + 8;
      };
      put('MACD 12 26 close 9', TV.textStrong);
      const num = (value: number | null | undefined) => (value == null ? '∅' : value.toFixed(2));
      if (bar) {
        put(num(bar.histogram), (bar.histogram ?? 0) >= 0 ? TV.histUpGrowing : TV.histDownFalling);
        put(num(bar.macd), TV.macd);
        put(num(bar.signal), TV.signal);
      }
      ctx.restore();
    });

    candles.attachPrimitive(patternBands);
    candles.attachPrimitive(priceOverlay);
    macd.attachPrimitive(watchLinesMacd);

    chart.subscribeCrosshairMove((param) => {
      const index = typeof param.time === 'number' ? timeIndexRef.current.get(param.time) ?? null : null;
      if (index === hoverRef.current) return;
      hoverRef.current = index;
      setHover(index);
      watchLinesMacd.redraw();
    });

    chart.timeScale().subscribeSizeChange((width) => {
      if (width <= 0 || !pendingFrameRef.current) return;
      pendingFrameRef.current = false;
      frameRef.current();
    });

    // Trackpad pinch and ctrl+wheel both report ctrlKey; zoom around the pointer.
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      const ts = chart.timeScale();
      const anchor = ts.coordinateToLogical(event.clientX - host.getBoundingClientRect().left);
      zoomTime(Math.exp(event.deltaY * 0.01), anchor ?? undefined);
    };
    host.addEventListener('wheel', onWheel, { passive: false });

    handlesRef.current = {
      chart, candles, volume, ema9, ema20, vwap, histogram, macd, signal, markers,
      overlays: [patternBands, priceOverlay, watchLinesMacd],
      priceLines: [],
    };
    framedRef.current = null;
    return () => {
      host.removeEventListener('wheel', onWheel);
      handlesRef.current = null;
      chart.remove();
    };
    // zoomTime only reads refs, so it never needs to rebuild the chart.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasData, locale]);

  // Feed the chart. Re-runs on every data refresh without touching the view,
  // which only re-frames when a different trade (or timeframe) is shown.
  useEffect(() => {
    const handles = handlesRef.current;
    if (!handles || !allData.length) return;
    const { candles, volume, ema9, ema20, vwap, histogram, macd, signal, markers } = handles;
    const times = allData.map((bar) => wallClock(Date.parse(bar.time), locale.timeZone));
    timeIndexRef.current = new Map(times.map((time, index) => [time as number, index]));

    candles.setData(allData.map((bar, i) => ({ time: times[i]!, open: bar.open, high: bar.high, low: bar.low, close: bar.close })));
    volume.setData(allData.map((bar, i) => ({ time: times[i]!, value: bar.volume, color: bar.close >= bar.open ? TV.volumeUp : TV.volumeDown })));
    const lineData = (pick: (bar: Point) => number | null) =>
      allData.map((bar, i) => {
        const value = pick(bar);
        return value === null || !Number.isFinite(value) ? { time: times[i]! } : { time: times[i]!, value };
      });
    ema9.setData(lineData((bar) => bar.ema9));
    ema20.setData(lineData((bar) => bar.ema20));
    vwap.setData(lineData((bar) => bar.vwap));
    macd.setData(lineData((bar) => bar.macd));
    signal.setData(lineData((bar) => bar.signal));
    // TradingView's four-shade histogram: a bar is dark while momentum builds, pale while it fades.
    histogram.setData(allData.map((bar, i) => {
      const value = bar.histogram;
      if (value === null) return { time: times[i]! };
      const prev = allData[i - 1]?.histogram ?? null;
      const color = value >= 0
        ? (prev === null || value >= prev ? TV.histUpGrowing : TV.histUpFalling)
        : (prev === null || value <= prev ? TV.histDownFalling : TV.histDownRising);
      return { time: times[i]!, value, color };
    }));

    for (const priceLine of handles.priceLines) candles.removePriceLine(priceLine);
    handles.priceLines = [];
    const addLine = (price: number, color: string, style: LineStyle, title = '', axis = true) =>
      handles.priceLines.push(candles.createPriceLine({
        price,
        color: hexAlpha(color, 0.75),
        lineWidth: 1,
        lineStyle: style,
        axisLabelVisible: axis,
        axisLabelColor: color,
        axisLabelTextColor: TV.background,
        title,
      }));

    const labels: Scene['labels'] = [];
    const entryIndex = containingBarIndex(allData, trade.entry_time, interval);
    const exitIndex = containingBarIndex(allData, trade.exit_time, interval);
    const tradeMarkers: SeriesMarker<Time>[] = [];
    if (!watching) {
      addLine(trade.stop, TV.stop, LineStyle.Dashed, 'Stop');
      if (trade.target) addLine(trade.target, TV.target, LineStyle.Dashed, 'Target');
      // Levels within 0.3% of the fill are noise against the BUY/SELL lines —
      // merge them into the trade lines instead of stacking near-duplicate
      // labels (SELL ₹456.1 vs Resistance ₹456.1 · pivot high).
      for (const level of levels) {
        if (Math.abs(level.price - trade.entry_price) / trade.entry_price <= 0.003) continue;
        if (trade.exit_price != null && Math.abs(level.price - trade.exit_price) / trade.exit_price <= 0.003) continue;
        const color = level.side === 'resistance' ? TV.resistance : TV.support;
        addLine(level.price, color, level.faint ? LineStyle.Dashed : LineStyle.Solid, '', !level.faint);
        labels.push({
          key: level.label,
          price: level.price,
          text: `${level.label} ${fmt(level.price)}${level.kind ? ` · ${level.kind.replaceAll('_', ' ')}` : ''}`,
          color,
          opacity: level.faint ? 0.7 : 1,
        });
      }
      addLine(trade.entry_price, TV.buy, LineStyle.Dotted);
      labels.push({ key: 'BUY', price: trade.entry_price, text: `BUY ${fmt(trade.entry_price)}`, color: TV.buy, opacity: 1 });
      if (trade.exit_price != null) {
        addLine(trade.exit_price, TV.sell, LineStyle.Dotted);
        labels.push({ key: 'SELL', price: trade.exit_price, text: `SELL ${fmt(trade.exit_price)}`, color: TV.sell, opacity: 1 });
      }
      if (entryIndex >= 0) tradeMarkers.push({ time: times[entryIndex]!, position: 'belowBar', shape: 'arrowUp', color: TV.up, text: 'Buy' });
      if (exitIndex >= 0) tradeMarkers.push({ time: times[exitIndex]!, position: 'aboveBar', shape: 'arrowDown', color: TV.down, text: 'Sell' });
    }
    markers.setMarkers(tradeMarkers);

    // A chart only renders patterns made on its own timeframe: a 1m formation
    // cannot be mistaken for a 5m signal during review.
    const patterns: Scene['patterns'] = (trade.pattern_matches ?? []).flatMap((match) => {
      if (match.timeframe !== interval) return [];
      const start = allData.findIndex((bar) => Date.parse(bar.time) === Date.parse(match.start));
      const end = allData.findIndex((bar) => Date.parse(bar.time) === Date.parse(match.end));
      if (start < 0 || end < 0) return [];
      // A correctly-named formation on a candle much smaller than this
      // stock's recent average is drawn faint: the label is right, the
      // candle is not worth acting on. See candles.STRENGTH_WEAK_BELOW.
      const size = match.strength === undefined ? '' : ` · ${match.strength.toFixed(2)}×`;
      return [{
        start: times[start]!,
        end: times[end]!,
        label: `${match.name.replaceAll('_', ' ')} · ${match.timeframe}${size}`,
        weak: match.strength !== undefined && match.strength < WEAK_STRENGTH,
      }];
    });
    const watch: Scene['watch'] = (watchMarkers ?? []).flatMap((marker) => {
      const index = containingBarIndex(allData, marker.time, interval);
      return index >= 0 ? [{ time: times[index]!, label: marker.label, news: marker.kind === 'news' }] : [];
    });
    const anchorPrices = watching
      ? []
      : [trade.entry_price, trade.stop, ...(trade.exit_price != null ? [trade.exit_price] : [])].filter(Number.isFinite);
    sceneRef.current = { patterns, labels, watch, anchorPrices, data: allData };
    for (const overlay of handles.overlays) overlay.redraw();

    // Opens zoomed onto the fill, as the review always starts there.
    frameRef.current = () => {
      const count = Math.min(allData.length, Math.max(MIN_VISIBLE_BARS, Math.ceil(allData.length / defaultZoom)));
      const start = Math.min(Math.max(0, entryIndex - Math.floor(count / 2)), allData.length - count);
      handles.chart.timeScale().setVisibleLogicalRange({ from: start - 0.5, to: start + count - 0.5 });
      candles.priceScale().applyOptions({ autoScale: true });
      handles.histogram.priceScale().applyOptions({ autoScale: true });
    };
    const frameKey = `${trade._id}|${interval}`;
    if (framedRef.current !== frameKey) {
      framedRef.current = frameKey;
      // A range set before the chart has a width collapses the candles to
      // the minimum bar spacing, so an unsized chart frames on first resize.
      if (handles.chart.timeScale().width() > 0) frameRef.current();
      else pendingFrameRef.current = true;
    }
  }, [allData, trade, interval, watching, watchMarkers, levels, fmt, locale, defaultZoom]);

  /** Scale the visible time window by `factor` around `anchor` (a logical bar index). */
  function zoomTime(factor: number, anchor?: number) {
    const handles = handlesRef.current;
    if (!handles) return;
    const ts = handles.chart.timeScale();
    const range = ts.getVisibleLogicalRange();
    if (!range) return;
    const width = range.to - range.from;
    const total = sceneRef.current.data.length;
    const next = Math.min(Math.max(width * factor, MIN_VISIBLE_BARS), total + 10);
    const center = anchor ?? (range.from + range.to) / 2;
    const ratio = next / width;
    ts.setVisibleLogicalRange({ from: center - (center - range.from) * ratio, to: center + (range.to - center) * ratio });
  }

  function pan(direction: -1 | 1) {
    const ts = handlesRef.current?.chart.timeScale();
    const range = ts?.getVisibleLogicalRange();
    if (!ts || !range) return;
    const step = (range.to - range.from) * 0.7 * direction;
    ts.setVisibleLogicalRange({ from: range.from + step, to: range.to + step });
  }

  const toggleFullscreen = async () => {
    if (!containerRef.current) return;
    if (document.fullscreenElement === containerRef.current) await document.exitFullscreen();
    else await containerRef.current.requestFullscreen();
  };

  if (!hasData) {
    return (
      <div className="rounded-xl border border-dashed border-black/15 bg-black/[0.02] px-4 py-10 text-center text-sm text-ink/55">
        {watching
          ? 'No candles are available for this name on this session.'
          : 'No candle snapshot is available for this older trade. New closed trades automatically retain their one-minute bars for chart review.'}
      </div>
    );
  }

  const shown = hover !== null && allData[hover] ? hover : allData.length - 1;
  const bar = allData[shown]!;
  const prevClose = allData[shown - 1]?.close ?? bar.open;
  const change = bar.close - prevClose;
  const barColor = bar.close >= bar.open ? TV.up : TV.down;
  const num = (value: number | null) => (value === null ? '∅' : value.toLocaleString(locale.numberLocale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const button = 'rounded px-2 py-1 text-[#b2b5be] hover:bg-[#2a2e39] hover:text-[#d1d4dc]';

  return (
    <div
      ref={containerRef}
      tabIndex={0}
      onPointerDown={(event) => event.currentTarget.focus()}
      onKeyDown={(event) => {
        if (event.key === '+' || event.key === '=') { event.preventDefault(); zoomTime(0.5); }
        else if (event.key === '-') { event.preventDefault(); zoomTime(2); }
        else if (event.key === '0') { event.preventDefault(); frameRef.current(); }
        else if (event.key === 'ArrowLeft') { event.preventDefault(); pan(-1); }
        else if (event.key === 'ArrowRight') { event.preventDefault(); pan(1); }
      }}
      className={`overflow-hidden rounded-xl border border-black/10 bg-[#131722] outline-none focus:ring-2 focus:ring-accent ${isFullscreen ? 'flex flex-col rounded-none' : ''}`}
      aria-label={`${trade.symbol} ${interval} chart; plus or minus to zoom, zero to reset, arrow left/right to pan`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#2a2e39] px-2 py-1 text-xs">
        <div className="flex items-center gap-2 px-1">
          <span className="font-semibold text-[#d1d4dc]">{trade.symbol}</span>
          <span className="rounded bg-[#2a2e39] px-1.5 py-0.5 text-[#d1d4dc]">{interval}</span>
        </div>
        <div className="flex items-center gap-0.5">
          <button type="button" onClick={() => pan(-1)} className={button} aria-label="Show earlier candles">‹</button>
          <button type="button" onClick={() => zoomTime(2)} className={button} aria-label="Zoom out">−</button>
          <button type="button" onClick={() => zoomTime(0.5)} className={button} aria-label="Zoom in">+</button>
          <button type="button" onClick={() => pan(1)} className={button} aria-label="Show later candles">›</button>
          <span className="mx-1 h-4 w-px bg-[#2a2e39]" aria-hidden="true" />
          <button type="button" onClick={() => frameRef.current()} className={button}>Reset</button>
          <button type="button" onClick={() => { void toggleFullscreen(); }} className={button}>{isFullscreen ? 'Exit full screen' : 'Full screen'}</button>
        </div>
      </div>
      <div className={`relative ${isFullscreen ? 'min-h-0 flex-1' : 'h-[540px]'}`}>
        <div ref={hostRef} className="absolute inset-0" />
        <div className="pointer-events-none absolute left-2 top-1.5 z-10 text-xs leading-5" style={{ fontFamily: FONT }}>
          <div className="flex flex-wrap gap-x-2 text-[#b2b5be]">
            <span className="font-semibold text-[#d1d4dc]">{trade.symbol} · {interval}</span>
            <span>O <span style={{ color: barColor }}>{num(bar.open)}</span></span>
            <span>H <span style={{ color: barColor }}>{num(bar.high)}</span></span>
            <span>L <span style={{ color: barColor }}>{num(bar.low)}</span></span>
            <span>C <span style={{ color: barColor }}>{num(bar.close)}</span></span>
            <span style={{ color: change >= 0 ? TV.up : TV.down }}>
              {change >= 0 ? '+' : ''}{num(change)} ({change >= 0 ? '+' : ''}{prevClose ? ((change / prevClose) * 100).toFixed(2) : '0.00'}%)
            </span>
            <span>Vol <span style={{ color: barColor }}>{fmtVolume(bar.volume)}</span></span>
          </div>
          <div className="flex flex-wrap gap-x-3 text-[#b2b5be]">
            <span>EMA 9 <span style={{ color: TV.ema9 }}>{num(bar.ema9)}</span></span>
            <span>EMA 20 <span style={{ color: TV.ema20 }}>{num(bar.ema20)}</span></span>
            <span>VWAP <span style={{ color: TV.vwap }}>{num(bar.vwap)}</span></span>
          </div>
        </div>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-[#2a2e39] px-3 py-1.5 text-xs text-[#b2b5be]">
        <span className="text-amber-200">▱ completed pattern</span>
        {levels.length > 0 && <><span style={{ color: TV.resistance }}>— resistance</span><span style={{ color: TV.support }}>— support</span><span className="text-slate-500">dashed = recorded only, gates nothing</span></>}
        {watching
          ? <><span className="text-amber-300">┆ 👀 flagged by scanner</span>{watchMarkers?.some((m) => m.kind === 'news') && <span className="text-sky-300">┆ 📰 headline published</span>}</>
          : <><span style={{ color: TV.stop }}>- - stop</span><span style={{ color: TV.target }}>- - target</span></>}
        <span className="text-slate-500">Drag to pan · drag the price or time axis to stretch it (double-click it to reset) · pinch or ctrl+scroll to zoom · keys: +/− zoom, 0 reset, ←/→ pan</span>
      </div>
    </div>
  );
}
