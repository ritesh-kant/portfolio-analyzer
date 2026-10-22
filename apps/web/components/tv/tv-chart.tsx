'use client';

import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  AreaSeries,
  BarSeries,
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  LineSeries,
  LineStyle,
  PriceScaleMode,
  type SeriesType,
  type Time,
  type UTCTimestamp,
  createChart,
} from 'lightweight-charts';
import {
  DEFAULT_COLOR,
  type Drawing,
  type DrawingKind,
  POINTS_NEEDED,
  type Pt,
  SWATCHES,
  makeTimeAxis,
  newId,
  translate,
} from '../../lib/tv/drawings';
import { dragPositionHandle, isPosition, positionPts } from '../../lib/tv/position';
import { autoStep, buildFootprints, stepLadder } from '../../lib/tv/footprint';
import {
  CATALOG,
  DEFAULT_INDICATORS,
  type IndicatorCfg,
  catalogOf,
  indicatorLabel,
  makeIndicator,
} from '../../lib/tv/indicator-defs';
import {
  type Series,
  atr,
  bollinger,
  ema,
  macd,
  relativeVolume,
  rsi,
  sma,
  vwap,
} from '../../lib/tv/indicators';
import { type CvdCandle, anchorKey, computeCvd } from '../../lib/tv/cvd';
import { load, save } from '../../lib/tv/storage';
import {
  TIMEFRAMES,
  aggregateBars,
  formatCrosshairTime,
  heikinAshi,
  mergeBars,
  pricePrecision,
  sessionDay,
  timeframeById,
  wallClock,
} from '../../lib/tv/timeframes';
import type { Bar, BarsResponse, SymbolMeta } from '../../lib/tv/types';
import { RightRail } from './right-rail';
import { WatchlistPanel, type WatchItem } from './watchlist-panel';
import { useCompact } from '../../lib/tv/use-compact';
import { BottomBar, type RangeId } from './bottom-bar';
import { DrawingsPrimitive } from './drawings-primitive';
import { NewsPrimitive } from './news-primitive';
import { foldTrailingTick, intervalSecs } from '../../lib/tv/clean-bars';
import { DeltaPrimitive, type DeltaMark } from './delta-primitive';
import { DeltaPanel } from './delta-panel';
import {
  DELTA_DEFAULTS,
  type DeltaConfig,
  type DeltaView,
  DELTA_VIEW_DEFAULTS,
  inDeltaView,
  deltaDepthDays,
  type PlacedDivergence,
  barContaining,
  findDeltaDivergences,
} from '../../lib/tv/delta-divergence';
import { SessionsPrimitive } from './sessions-primitive';
import { SessionProfilePrimitive } from './session-profile-primitive';
import {
  extendedMask,
  hasExtendedBars,
  runs,
  sessionBreaks,
  sessionFor,
} from '../../lib/tv/sessions';
import { ReplayBar } from './replay-bar';
import { DEFAULT_ROWS } from '../../lib/tv/volume-profile';
import { completedCount, endAfter, nextEnd } from '../../lib/tv/replay';
import { groupByBar } from '../../lib/tv/news-markers';
import type { NewsItem, NewsResponse } from '../../lib/tv/news-types';
import { FootprintView, type FootprintData } from './footprint-series';
import { Icons } from './icons';
import { IndicatorPicker, IndicatorSettings } from './indicator-dialog';
import { LeftToolbar, type Tool } from './left-toolbar';
import { PositionSettings } from './position-settings';
import { SymbolSearch } from './symbol-search';
import { type ChartType, type FootprintUi, TopBar } from './top-bar';

const FONT = '-apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, Ubuntu, sans-serif';
const UP = '#089981';
const DOWN = '#f23645';
const VOL_UP = 'rgba(8, 153, 129, 0.5)';
const VOL_DOWN = 'rgba(242, 54, 69, 0.5)';
const CLEAR = 'rgba(0,0,0,0)';

interface Settings {
  symbol: string;
  tfId: string;
  chartType: ChartType;
  indicators: IndicatorCfg[];
  fp: FootprintUi;
  news: boolean;
  sessions: boolean;
}

const DEFAULT_SETTINGS: Settings = {
  symbol: 'RELIANCE.NS',
  tfId: '5m',
  chartType: 'candles',
  indicators: DEFAULT_INDICATORS,
  fp: { on: false, auto: true, step: 0.05, imbalance: 3, showDelta: true },
  news: true,
  sessions: true,
};

// ── indicator computation ────────────────────────────────────────────────

interface Line {
  key: string;
  label?: string;
  color: string;
  /** For a candle line: the close of each candle (what the legend and the axis follow). */
  values: Series;
  kind: 'line' | 'hist' | 'candle';
  ohlc?: CvdCandle[];
}

interface Computed {
  cfg: IndicatorCfg;
  /** Pane index the indicator draws in (0 = price pane). */
  pane: number;
  lines: Line[];
}

function computeIndicators(
  indicators: IndicatorCfg[],
  bars: Bar[],
  timeZone: string,
  sub: Bar[],
  secs: number,
  regular?: (time: number) => boolean,
): Computed[] {
  const closes = bars.map((b) => b.close);
  const volumes = bars.map((b) => b.volume);
  let nextPane = 1;
  return indicators.map((cfg) => {
    const where = catalogOf(cfg.type).where;
    const pane = where === 'pane' ? nextPane++ : 0;
    let lines: Line[] = [];
    switch (cfg.type) {
      case 'volume':
        lines = [{ key: 'vol', color: VOL_UP, values: volumes, kind: 'hist' }];
        break;
      case 'rvol':
        lines = [
          {
            key: 'rvol',
            color: '#26a69a',
            values: relativeVolume(volumes, cfg.length),
            kind: 'hist',
          },
        ];
        break;
      case 'ema':
        lines = [{ key: 'ema', color: cfg.color, values: ema(closes, cfg.length), kind: 'line' }];
        break;
      case 'sma':
        lines = [{ key: 'sma', color: cfg.color, values: sma(closes, cfg.length), kind: 'line' }];
        break;
      case 'vwap':
        lines = [
          {
            key: 'vwap',
            color: cfg.color,
            values: vwap(bars, (t) => sessionDay(t, timeZone)),
            kind: 'line',
          },
        ];
        break;
      case 'bb': {
        const bb = bollinger(closes, cfg.length, cfg.mult ?? 2);
        lines = [
          { key: 'basis', color: '#ff6d00', values: bb.basis, kind: 'line' },
          { key: 'upper', color: cfg.color, values: bb.upper, kind: 'line' },
          { key: 'lower', color: cfg.color, values: bb.lower, kind: 'line' },
        ];
        break;
      }
      case 'macd': {
        const m = macd(closes, cfg.length, cfg.length2 ?? 26, cfg.length3 ?? 9);
        lines = [
          { key: 'hist', color: '#26a69a', values: m.hist, kind: 'hist' },
          { key: 'macd', color: '#2962ff', values: m.macd, kind: 'line' },
          { key: 'signal', color: '#ff6d00', values: m.signal, kind: 'line' },
        ];
        break;
      }
      case 'atr':
        lines = [{ key: 'atr', color: '#f23645', values: atr(bars, cfg.length), kind: 'line' }];
        break;
      case 'rsi':
        lines = [{ key: 'rsi', color: '#7e57c2', values: rsi(closes, cfg.length), kind: 'line' }];
        break;
      case 'svp':
        // Drawn by SessionProfilePrimitive, not as a series.
        lines = [];
        break;
      case 'cvd': {
        const anchor = cfg.anchor ?? 'session';
        const ohlc = computeCvd(bars, sub, secs, (t) => anchorKey(sessionDay(t, timeZone), anchor), regular);
        lines = [{ key: 'cvd', color: UP, values: ohlc.map((c) => c.close), kind: 'candle', ohlc }];
        break;
      }
    }
    return { cfg, pane, lines };
  });
}

const fmtVolume = (v: number) =>
  v >= 1e7
    ? `${(v / 1e6).toFixed(2)}M`
    : v >= 1e6
      ? `${(v / 1e6).toFixed(2)}M`
      : v >= 1e3
        ? `${(v / 1e3).toFixed(2)}K`
        : `${Math.round(v)}`;

/** A signed volume as TradingView prints it: 1.23K, −45.6K, 2.1M. */
const fmtDelta = (v: number) => {
  const a = Math.abs(v);
  const s =
    a >= 1e6 ? `${(a / 1e6).toFixed(2)}M` : a >= 1e3 ? `${(a / 1e3).toFixed(2)}K` : `${Math.round(a)}`;
  return v < 0 ? `−${s}` : s;
};

const histColor = (cur: number, prev: number | null) =>
  cur >= 0
    ? prev != null && cur >= prev
      ? '#26a69a'
      : '#b2dfdb'
    : prev != null && cur <= prev
      ? '#ff5252'
      : '#ffcdd2';

// ── main component ───────────────────────────────────────────────────────

interface Latest {
  tool: Tool;
  magnet: boolean;
  keep: boolean;
  lockAll: boolean;
  drawings: Drawing[];
  selectedId: string | null;
  bars: Bar[];
}

type Drag =
  | { mode: 'create'; startX: number; startY: number; moved: boolean }
  | {
      mode: 'move';
      id: string;
      orig: Drawing;
      startLogical: number;
      startPrice: number;
      moved: boolean;
    }
  | { mode: 'handle'; id: string; handle: number; moved: boolean };

export function TvChart() {
  const rootRef = useRef<HTMLDivElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const mainRef = useRef<{ key: string; series: ISeriesApi<SeriesType, Time> } | null>(null);
  const footRef = useRef<ISeriesApi<'Custom', Time> | null>(null);
  const footViewRef = useRef<FootprintView | null>(null);
  const primRef = useRef<DrawingsPrimitive | null>(null);
  const newsPrimRef = useRef<NewsPrimitive | null>(null);
  const sessionsPrimRef = useRef<SessionsPrimitive | null>(null);
  const svpPrimRef = useRef<SessionProfilePrimitive | null>(null);
  const deltaPrimRef = useRef<DeltaPrimitive | null>(null);
  const indStructRef = useRef<{
    key: string;
    entries: { line: Line; cfgId: string; series: ISeriesApi<SeriesType, Time> }[];
  }>({
    key: '',
    entries: [],
  });
  const intradayRef = useRef(true);
  const viewKeyRef = useRef('');

  // persisted settings
  const [ready, setReady] = useState(false);
  const [symbol, setSymbol] = useState(DEFAULT_SETTINGS.symbol);
  const [tfId, setTfId] = useState(DEFAULT_SETTINGS.tfId);
  const [chartType, setChartType] = useState<ChartType>(DEFAULT_SETTINGS.chartType);
  const [indicators, setIndicators] = useState<IndicatorCfg[]>(DEFAULT_SETTINGS.indicators);
  const [fp, setFp] = useState<FootprintUi>(DEFAULT_SETTINGS.fp);
  const [newsOn, setNewsOn] = useState(DEFAULT_SETTINGS.news);
  const [sessionsOn, setSessionsOn] = useState(DEFAULT_SETTINGS.sessions);
  const [news, setNews] = useState<{ symbol: string; res: NewsResponse } | null>(null);
  const [newsHover, setNewsHover] = useState<number | null>(null);
  const [newsPinned, setNewsPinned] = useState<number | null>(null);
  const [newsExpanded, setNewsExpanded] = useState(false);

  // replay: `replayEnd` is the cut-off instant (unix s); null = live chart
  const [replayEnd, setReplayEnd] = useState<number | null>(null);
  const [replayPick, setReplayPick] = useState(false);
  const [replayPlaying, setReplayPlaying] = useState(false);
  const [replaySpeed, setReplaySpeed] = useState(3);
  const [replayStartTick, setReplayStartTick] = useState(0);
  const replayEndRef = useRef<number | null>(null);
  const replayPickRef = useRef(false);
  const fullTimesRef = useRef<number[]>([]);
  const tfSecsRef = useRef(300);

  // scale
  const [log, setLog] = useState(false);
  const [percent, setPercent] = useState(false);
  const [autoScale, setAutoScale] = useState(true);

  // data
  const [raw, setRaw] = useState<{ key: string; bars: Bar[] }>({ key: '', bars: [] });
  const [subRaw, setSubRaw] = useState<Bar[]>([]);
  const firstWallRef = useRef<{ key: string; time: number } | null>(null);
  // NSE stocks: chart bars older than Yahoo's intraday history (60 days), from Upstox, while the delta panel is open
  const [older, setOlder] = useState<{ key: string; bars: Bar[] }>({ key: '', bars: [] });
  // Bars under the delta panel's 15-minute candles, tagged with the symbol they belong to: 1-minute (four 7-day
  // windows, ~4 weeks) and 5-minute (60 days, used where 1-minute bars do not reach)
  const [deltaSub, setDeltaSub] = useState<{ symbol: string; bars: Bar[]; bars5: Bar[] }>({
    symbol: '',
    bars: [],
    bars5: [],
  });
  const [meta, setMeta] = useState<SymbolMeta | null>(null);
  const [status, setStatus] = useState<'loading' | 'ok' | 'error'>('loading');
  const [error, setError] = useState('');

  // interaction
  const [tool, setTool] = useState<Tool>('cursor');
  const [magnet, setMagnet] = useState(false);
  const [keep, setKeep] = useState(false);
  const [lockAll, setLockAll] = useState(false);
  const [hideAll, setHideAll] = useState(false);
  const [drawState, setDrawState] = useState<{ symbol: string; list: Drawing[] }>({
    symbol: '',
    list: [],
  });
  const [draft, setDraft] = useState<Drawing | null>(null);
  const [measure, setMeasure] = useState<Drawing | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [posEditId, setPosEditId] = useState<string | null>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const [histTick, setHistTick] = useState(0);
  const history = useRef<{ past: Drawing[][]; future: Drawing[][] }>({ past: [], future: [] });

  // dialogs / panes
  const [showSearch, setShowSearch] = useState<false | 'chart' | 'watchlist'>(false);
  const compact = useCompact();
  const [watchOpen, setWatchOpen] = useState(true);
  const [pendingWatch, setPendingWatch] = useState<WatchItem | null>(null);
  const clearPendingWatch = useCallback(() => setPendingWatch(null), []);
  useEffect(() => {
    // A docked watchlist would swallow a phone screen; there it is an on-demand drawer, closed to start.
    const phone = window.matchMedia('(max-width: 767px)').matches;
    setWatchOpen(phone ? false : load<boolean>('tv:watchOpen', true));
  }, []);
  // Delta divergence shares the docked right slot with the watchlist (one open at a time).
  const [deltaOpen, setDeltaOpen] = useState(false);
  const [deltaCfg, setDeltaCfg] = useState<DeltaConfig>(DELTA_DEFAULTS);
  const [deltaMarks, setDeltaMarks] = useState(true);
  const [deltaView, setDeltaView] = useState<DeltaView>(DELTA_VIEW_DEFAULTS);
  const [deltaPick, setDeltaPick] = useState<number | null>(null);
  useEffect(() => {
    const saved = load<Partial<DeltaConfig>>('tv:deltaCfg', {});
    setDeltaCfg({
      delta: saved.delta ?? DELTA_DEFAULTS.delta,
      wick: saved.wick ?? DELTA_DEFAULTS.wick,
      dominance: saved.dominance ?? DELTA_DEFAULTS.dominance,
      rvol: saved.rvol ?? DELTA_DEFAULTS.rvol,
      within: saved.within ?? DELTA_DEFAULTS.within,
      closeBreak: saved.closeBreak ?? DELTA_DEFAULTS.closeBreak,
      missed: saved.missed ?? DELTA_DEFAULTS.missed,
    });
    setDeltaView({ ...DELTA_VIEW_DEFAULTS, ...load<Partial<DeltaView>>('tv:deltaView', {}) });
    setDeltaMarks(load<boolean>('tv:deltaMarks', true));
    setDeltaOpen(load<boolean>('tv:deltaOpen', false));
  }, []);
  const [showIndicators, setShowIndicators] = useState(false);
  const [settingsId, setSettingsId] = useState<string | null>(null);
  /** Indicator whose pane is maximized (every other pane shrunk to its legend row); not persisted. */
  const [maxPaneId, setMaxPaneId] = useState<string | null>(null);
  const [fpNoteClosed, setFpNoteClosed] = useState(false);
  const [paneEls, setPaneEls] = useState<(HTMLElement | null)[]>([]);

  const tf = timeframeById(tfId);
  const timeZone = meta?.timezone ?? 'UTC';
  intradayRef.current = tf.intraday;

  // ── derived data ──────────────────────────────────────────────────────
  const rawBars = useMemo(() => {
    const first = raw.bars[0]?.time;
    if (
      !deltaOpen ||
      first == null ||
      older.key !== `${symbol}|${tf.source}` ||
      !raw.key.startsWith(`${symbol}|${tf.source}|`)
    ) {
      return raw.bars;
    }
    const before = older.bars.filter((b) => b.time < first);
    return before.length ? [...before, ...raw.bars] : raw.bars;
  }, [raw.bars, raw.key, older, deltaOpen, symbol, tf.source]);
  const fullBars = useMemo(
    () => (tf.aggregateSecs ? aggregateBars(rawBars, tf.aggregateSecs, timeZone) : rawBars),
    [rawBars, tf.aggregateSecs, timeZone],
  );
  // Replay: only candles complete at the cut-off exist as far as every indicator, drawing and badge is concerned.
  const bars = useMemo(
    () =>
      replayEnd == null
        ? fullBars
        : fullBars.slice(
            0,
            completedCount(
              fullBars.map((b) => b.time),
              tf.secs,
              replayEnd,
            ),
          ),
    [fullBars, replayEnd, tf.secs],
  );
  fullTimesRef.current = useMemo(() => fullBars.map((b) => b.time), [fullBars]);
  tfSecsRef.current = tf.secs;
  replayEndRef.current = replayEnd;
  replayPickRef.current = replayPick;
  const replayAtEnd = replayEnd != null && bars.length >= fullBars.length;
  const wall = useMemo(
    () => bars.map((b) => wallClock(b.time, timeZone) as UTCTimestamp),
    [bars, timeZone],
  );
  const decimals = pricePrecision(bars[bars.length - 1]?.close ?? meta?.price ?? 0);
  // The footprint's own 1-minute fetch reaches 7 days back; with the delta panel open, or CVD on the chart, the
  // ~4-week 1-minute bars (same symbol) are used instead, so the older candles have real intrabars too.
  const cvdOn = indicators.some((i) => i.type === 'cvd');
  const subBars =
    tf.source === '1m'
      ? rawBars
      : (deltaOpen || cvdOn) && deltaSub.symbol === symbol && deltaSub.bars.length > subRaw.length
        ? deltaSub.bars
        : subRaw;
  const effectiveStep = fp.auto ? autoStep(bars) : fp.step;
  // Delta divergences on 15-minute candles, using the footprint's own delta for each. Where 1-minute bars cover a
  // candle its delta is built from them; older candles (to ~60 days) fall back to 5-minute bars, flagged as
  // coarser. Stays empty until the bars load, and never uses another symbol's.
  const deltaOk = deltaSub.symbol === symbol;
  const deltaFirst = deltaOk
    ? Math.min(deltaSub.bars[0]?.time ?? Infinity, deltaSub.bars5[0]?.time ?? Infinity)
    : Infinity;
  const deltaLast = deltaOk
    ? Math.max(
        deltaSub.bars[deltaSub.bars.length - 1]?.time ?? 0,
        deltaSub.bars5[deltaSub.bars5.length - 1]?.time ?? 0,
      )
    : 0;
  const setups = useMemo<PlacedDivergence[]>(() => {
    const fine = deltaSub.symbol === symbol ? deltaSub.bars : [];
    const mid = deltaSub.symbol === symbol ? deltaSub.bars5 : [];
    if (!fine.length && !mid.length) return [];
    // the chart's own candles when it is on 15m, otherwise 15m candles built from the 5-minute bars
    const all = tf.id === '15m' ? bars : aggregateBars(mid.length ? mid : fine, 900, timeZone);
    // only candles whose last bar is known (and, in replay, already played)
    const known = Math.max(
      fine.length ? fine[fine.length - 1]!.time + 60 : 0,
      mid.length ? mid[mid.length - 1]!.time + 300 : 0,
    );
    const until = replayEnd == null ? known : Math.min(known, replayEnd);
    const candles = all.filter((c) => c.time + 900 <= until);
    const step = autoStep(candles);
    const fineFrom = fine.length ? fine[0]!.time : Infinity;
    const fineFps = fine.length ? buildFootprints(candles, fine, 900, step) : [];
    const midFps = mid.length ? buildFootprints(candles, mid, 900, step) : [];
    const deltas: (number | null)[] = [];
    const coarse: boolean[] = [];
    candles.forEach((c, i) => {
      const f = c.time >= fineFrom ? fineFps[i] : null; // a candle only partly covered by 1-minute bars is coarse
      if (f) {
        deltas.push(f.delta);
        coarse.push(false);
        return;
      }
      const m = midFps[i];
      deltas.push(m ? m.delta : null);
      coarse.push(!!m);
    });
    const times = bars.map((b) => b.time);
    return findDeltaDivergences(candles, deltas, deltaCfg, coarse).flatMap((d) => {
      if (d.status === 'missed' && !deltaCfg.missed) return [];
      // the chart bar that holds a candle's last minute: when that candle was known
      const chartIndex = barContaining(times, d.time + 840, tf.secs);
      // ...and the one holding its first minute (a 15-minute candle spans three bars on a 5m chart)
      const startChartIndex = barContaining(times, d.time, tf.secs);
      const confirmChartIndex =
        d.confirmTime == null ? null : barContaining(times, d.confirmTime + 840, tf.secs);
      return chartIndex < 0
        ? []
        : [
            {
              ...d,
              chartIndex,
              startChartIndex: startChartIndex < 0 ? chartIndex : startChartIndex,
              confirmChartIndex: confirmChartIndex === -1 ? null : confirmChartIndex,
            },
          ];
    });
  }, [tf.id, tf.secs, deltaSub, symbol, bars, timeZone, replayEnd, deltaCfg]);
  // What the list and the badges show: the chosen date range and minimum |delta| (relative to the newest candle).
  const divergences = useMemo(() => {
    const newest = bars[bars.length - 1]?.time ?? 0;
    return setups.filter((d) => inDeltaView(d, deltaView, newest, (t) => sessionDay(t, timeZone)));
  }, [setups, deltaView, bars, timeZone]);
  const footprints = useMemo(
    () => (fp.on ? buildFootprints(bars, subBars, tf.secs, effectiveStep) : []),
    [fp.on, bars, subBars, tf.secs, effectiveStep],
  );
  // US stocks: Yahoo reports no volume before 09:30 or after 16:00 (bar for bar, apart from the closing cross
  // stamped 16:00), so CVD only counts regular hours there.
  const regularHours = useMemo(() => {
    const def = meta && hasExtendedBars({ symbol, instrumentType: meta.instrumentType, timezone: meta.timezone })
      ? sessionFor({ symbol, instrumentType: meta.instrumentType, timezone: meta.timezone })
      : null;
    if (!def) return undefined;
    return (t: number) => {
      const minute = Math.floor((wallClock(t, timeZone) % 86400) / 60);
      return minute >= def.start && minute < def.end;
    };
  }, [meta, symbol, timeZone]);
  const computed = useMemo(
    () => computeIndicators(indicators, bars, timeZone, subBars, tf.secs, regularHours),
    [indicators, bars, timeZone, subBars, tf.secs, regularHours],
  );
  const axis = useMemo(
    () =>
      makeTimeAxis(
        bars.map((b) => b.time),
        tf.secs,
      ),
    [bars, tf.secs],
  );
  const drawings = useMemo(
    () => (drawState.symbol === symbol ? drawState.list : []),
    [drawState, symbol],
  );
  const footprintCoverage =
    fp.on && footprints.length ? footprints.filter(Boolean).length / footprints.length : 1;

  const latest = useRef<Latest>({ tool, magnet, keep, lockAll, drawings, selectedId, bars });
  latest.current = { tool, magnet, keep, lockAll, drawings, selectedId, bars };

  // ── settings load / save ──────────────────────────────────────────────
  useEffect(() => {
    const saved = load<Partial<Settings>>('tv:settings', {});
    const fromUrl = new URLSearchParams(window.location.search).get('symbol');
    setSymbol((fromUrl || saved.symbol || DEFAULT_SETTINGS.symbol).toUpperCase());
    if (saved.tfId && TIMEFRAMES.some((t) => t.id === saved.tfId)) setTfId(saved.tfId);
    if (saved.chartType) setChartType(saved.chartType);
    if (saved.indicators) setIndicators(saved.indicators);
    if (saved.fp) setFp({ ...DEFAULT_SETTINGS.fp, ...saved.fp });
    if (typeof saved.news === 'boolean') setNewsOn(saved.news);
    if (typeof saved.sessions === 'boolean') setSessionsOn(saved.sessions);
    setReady(true);
  }, []);

  useEffect(() => {
    if (ready)
      save('tv:settings', {
        symbol,
        tfId,
        chartType,
        indicators,
        fp,
        news: newsOn,
        sessions: sessionsOn,
      });
  }, [ready, symbol, tfId, chartType, indicators, fp, newsOn, sessionsOn]);

  // Drawings are stored per symbol.
  useEffect(() => {
    if (!ready) return;
    setDrawState({ symbol, list: load<Drawing[]>(`tv:drawings:${symbol}`, []) });
    history.current = { past: [], future: [] };
    setHistTick((n) => n + 1);
    setSelectedId(null);
    setDraft(null);
    setMeasure(null);
  }, [ready, symbol]);

  useEffect(() => {
    if (drawState.symbol && drawState.symbol === symbol)
      save(`tv:drawings:${symbol}`, drawState.list);
  }, [drawState, symbol]);

  const setDrawings = useCallback((next: Drawing[], record = true) => {
    setDrawState((cur) => {
      if (record) {
        history.current.past.push(cur.list);
        if (history.current.past.length > 100) history.current.past.shift();
        history.current.future = [];
      }
      return { symbol: cur.symbol, list: next };
    });
    setHistTick((n) => n + 1);
  }, []);

  const undo = useCallback(() => {
    const prev = history.current.past.pop();
    if (!prev) return;
    setDrawState((cur) => {
      history.current.future.push(cur.list);
      return { symbol: cur.symbol, list: prev };
    });
    setSelectedId(null);
    setHistTick((n) => n + 1);
  }, []);

  const redo = useCallback(() => {
    const next = history.current.future.pop();
    if (!next) return;
    setDrawState((cur) => {
      history.current.past.push(cur.list);
      return { symbol: cur.symbol, list: next };
    });
    setSelectedId(null);
    setHistTick((n) => n + 1);
  }, []);

  // ── sessions ──────────────────────────────────────────────────────────
  const sessionInfo = useMemo(
    () => (meta ? { symbol, instrumentType: meta.instrumentType, timezone: meta.timezone } : null),
    [meta, symbol],
  );
  // US stocks only have pre/post-market bars to shade when asked for them.
  const wantPrepost = sessionsOn && !!sessionInfo && hasExtendedBars(sessionInfo);

  // ── data fetching ─────────────────────────────────────────────────────
  // A date window rather than a named range (1-minute history older than 7 days).
  const fetchWindow = useCallback(
    async (sym: string, interval: string, from: number, to: number, signal?: AbortSignal, prepost = false) => {
      const res = await fetch(
        `/api/chart/bars?symbol=${encodeURIComponent(sym)}&interval=${interval}&period1=${from}&period2=${to}${prepost ? '&prepost=1' : ''}`,
        { signal, cache: 'no-store' },
      );
      const body = (await res.json()) as BarsResponse & { error?: string };
      if (!res.ok) throw new Error(body.error ?? `HTTP ${res.status}`);
      return foldTrailingTick(body.bars, intervalSecs(interval));
    },
    [],
  );
  const fetchBars = useCallback(
    async (sym: string, interval: string, range: string, signal?: AbortSignal, prepost = false) => {
      const res = await fetch(
        `/api/chart/bars?symbol=${encodeURIComponent(sym)}&interval=${interval}&range=${range}${prepost ? '&prepost=1' : ''}`,
        { signal, cache: 'no-store' },
      );
      const body = (await res.json()) as BarsResponse & { error?: string };
      if (!res.ok) throw new Error(body.error ?? `HTTP ${res.status}`);
      return { ...body, bars: foldTrailingTick(body.bars, intervalSecs(interval)) };
    },
    [],
  );

  // NSE stocks: exchange candles from Upstox, one ≤28-day window per request, a few at a time.
  const fetchUpstox = useCallback(
    async (sym: string, interval: string, from: number, to: number, signal?: AbortSignal) => {
      const step = 28 * 86400;
      const spans: [number, number][] = [];
      for (let end = to; end > from; end -= step) spans.push([Math.max(from, end - step), end]);
      const byTime = new Map<number, Bar>();
      let failed = 0;
      let next = 0;
      const worker = async () => {
        while (next < spans.length && !signal?.aborted) {
          const [a, b] = spans[next++]!;
          try {
            const res = await fetch(
              `/api/chart/upstox?symbol=${encodeURIComponent(sym)}&interval=${interval}&from=${a}&to=${b}`,
              { signal },
            );
            const body = (await res.json()) as { bars?: Bar[]; error?: string };
            if (!res.ok || !body.bars) throw new Error(body.error ?? `HTTP ${res.status}`);
            for (const bar of body.bars) byTime.set(bar.time, bar);
          } catch {
            failed += 1;
          }
        }
      };
      await Promise.all([worker(), worker(), worker(), worker()]);
      if (failed && !byTime.size) throw new Error('upstox unavailable');
      return [...byTime.values()].sort((x, y) => x.time - y.time);
    },
    [],
  );

  useEffect(() => {
    if (!ready) return;
    const ctl = new AbortController();
    setStatus('loading');
    setError('');
    fetchBars(symbol, tf.source, tf.range, ctl.signal, wantPrepost)
      .then((res) => {
        setRaw({ key: `${symbol}|${tf.source}|${tf.range}`, bars: res.bars });
        setMeta(res.meta);
        setStatus('ok');
        setUpdatedAt(Date.now());
      })
      .catch((e: unknown) => {
        if (ctl.signal.aborted) return;
        setStatus('error');
        setError(e instanceof Error ? e.message : 'Failed to load');
      });
    return () => ctl.abort();
  }, [ready, symbol, tf.source, tf.range, wantPrepost, fetchBars]);

  // Footprint needs 1-minute bars underneath coarser candles.
  const svpOn = indicators.some((i) => i.type === 'svp');
  useEffect(() => {
    if (!ready || (!fp.on && !svpOn && !cvdOn) || tf.source === '1m') return;
    const ctl = new AbortController();
    fetchBars(symbol, '1m', '7d', ctl.signal, wantPrepost)
      .then((res) => setSubRaw(res.bars))
      .catch(() => setSubRaw([]));
    return () => ctl.abort();
  }, [ready, fp.on, svpOn, cvdOn, symbol, tf.source, wantPrepost, fetchBars]);

  // Delta divergence reads 1-minute bars further back than the footprint does. NSE stocks: Upstox's exchange
  // candles, as far back as the view needs (3 months at least). Everything else: Yahoo serves them ~4 weeks back,
  // 7 days per request, so four date windows are stitched together, with 5-minute bars (60 days) behind them.
  const nse = symbol.endsWith('.NS');
  const deltaDays = deltaDepthDays(deltaView, Math.floor(Date.now() / 1000));
  useEffect(() => {
    if (!ready || (!deltaOpen && !cvdOn)) return;
    const ctl = new AbortController();
    const now = Math.floor(Date.now() / 1000);
    const yahoo = () => {
      const week = 7 * 86400;
      const windows = Promise.allSettled(
        [0, 1, 2, 3].map((k) =>
          fetchWindow(symbol, '1m', now - week * (k + 1), now - week * k, ctl.signal, wantPrepost),
        ),
      );
      // 5-minute bars reach 60 days: they carry the delta for candles older than the 1-minute bars (panel only)
      const mid = deltaOpen
        ? fetchBars(symbol, '5m', '60d', ctl.signal, wantPrepost)
            .then((res) => res.bars)
            .catch(() => [] as Bar[])
        : Promise.resolve([] as Bar[]);
      void Promise.all([windows, mid]).then(([results, bars5]) => {
        if (ctl.signal.aborted) return;
        const byTime = new Map<number, Bar>();
        for (const r of results) {
          if (r.status === 'fulfilled') for (const b of r.value) byTime.set(b.time, b);
        }
        if (!byTime.size && !bars5.length) return;
        setDeltaSub({
          symbol,
          bars: [...byTime.values()].sort((a, b) => a.time - b.time),
          bars5,
        });
      });
    };
    if (!nse) {
      yahoo();
    } else {
      fetchUpstox(symbol, '1m', now - deltaDays * 86400, now, ctl.signal)
        .then((bars) => {
          if (ctl.signal.aborted) return;
          if (bars.length) setDeltaSub({ symbol, bars, bars5: [] });
          else yahoo();
        })
        .catch(() => {
          if (!ctl.signal.aborted) yahoo();
        });
    }
    return () => ctl.abort();
  }, [ready, deltaOpen, cvdOn, symbol, nse, deltaDays, wantPrepost, fetchWindow, fetchBars, fetchUpstox]);

  // The chart's own bars only go back as far as Yahoo's intraday history (60 days for 5m/15m/30m, 7 days for
  // 1m), so older setups would have nowhere to be drawn: fill in the earlier bars from Upstox.
  const rawFirst = raw.bars[0]?.time;
  useEffect(() => {
    const upstoxInterval = ['1m', '5m', '15m', '30m'].includes(tf.source);
    if (!ready || !deltaOpen || !nse || !upstoxInterval || rawFirst == null) return;
    if (!raw.key.startsWith(`${symbol}|${tf.source}|`)) return;
    const from = Math.floor(Date.now() / 1000) - deltaDays * 86400;
    if (rawFirst <= from) return;
    const ctl = new AbortController();
    fetchUpstox(symbol, tf.source, from, rawFirst - 1, ctl.signal)
      .then((bars) => {
        if (!ctl.signal.aborted) setOlder({ key: `${symbol}|${tf.source}`, bars });
      })
      .catch(() => undefined);
    return () => ctl.abort();
  }, [ready, deltaOpen, nse, symbol, tf.source, deltaDays, rawFirst, raw.key, fetchUpstox]);

  // Live: re-pull the tail and merge it in. Yahoo caches each reply ~10 s, so polling faster than
  // every few seconds gains nothing; intraday polls at 3 s, longer timeframes at 30 s.
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  useEffect(() => {
    if (!ready || replayEnd != null) return;
    let stopped = false;
    let busy = false;
    const timer = setInterval(
      async () => {
        if (document.hidden || busy) return;
        busy = true;
        try {
          const res = await fetchBars(
            symbol,
            tf.source,
            tf.intraday ? '1d' : '5d',
            undefined,
            wantPrepost,
          );
          if (stopped) return;
          setUpdatedAt(Date.now());
          setRaw((cur) => {
            if (!cur.key.startsWith(`${symbol}|`)) return cur;
            const merged = mergeBars(cur.bars, res.bars);
            const a = merged[merged.length - 1];
            const b = cur.bars[cur.bars.length - 1];
            // Nothing new: keep the same array so no indicator or series is rebuilt.
            const same =
              merged.length === cur.bars.length &&
              a?.time === b?.time &&
              a?.close === b?.close &&
              a?.high === b?.high &&
              a?.low === b?.low &&
              a?.volume === b?.volume;
            return same ? cur : { key: cur.key, bars: merged };
          });
        } catch {
          // transient — next tick retries
        } finally {
          busy = false;
        }
      },
      tf.intraday ? 3000 : 30000,
    );
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [ready, replayEnd, symbol, tf.source, tf.intraday, wantPrepost, fetchBars]);

  // Company filings (NSE announcements / SEC EDGAR) across the loaded history.
  const firstBarTime = bars[0]?.time;
  useEffect(() => {
    if (!ready || !newsOn || !firstBarTime || status !== 'ok') return;
    const ctl = new AbortController();
    const day = (sec: number) => new Date(sec * 1000).toISOString().slice(0, 10);
    const cap = Date.now() / 1000 - 3 * 365 * 86400;
    const from = day(Math.max(firstBarTime - 86400, cap));
    const to = day(Date.now() / 1000 + 86400);
    fetch(`/api/chart/news?symbol=${encodeURIComponent(symbol)}&from=${from}&to=${to}`, {
      signal: ctl.signal,
      cache: 'no-store',
    })
      .then((r) => r.json() as Promise<NewsResponse>)
      .then((res) => setNews({ symbol, res }))
      .catch(() => {
        if (!ctl.signal.aborted)
          setNews({
            symbol,
            res: { source: 'none', supported: false, reason: 'news request failed', items: [] },
          });
      });
    return () => ctl.abort();
  }, [ready, newsOn, symbol, firstBarTime, status]);

  // Routine paperwork (trading-window notices, investor-call schedules) is not a catalyst: only news and offerings get a badge.
  const newsByBar = useMemo(() => {
    if (!newsOn || !news || news.symbol !== symbol) return new Map<number, NewsItem[]>();
    const cutoff = replayEnd == null ? Infinity : replayEnd * 1000;
    const items = news.res.items.filter(
      (i) => i.kind !== 'filing' && Date.parse(i.published_at) <= cutoff,
    );
    return groupByBar(
      items,
      bars.map((b) => b.time),
      tf.secs,
    );
  }, [newsOn, news, symbol, bars, tf.secs, replayEnd]);

  // ── replay controls ───────────────────────────────────────────────────
  const exitReplay = useCallback(() => {
    setReplayPick(false);
    setReplayPlaying(false);
    setReplayEnd(null);
  }, []);

  const startReplayAt = useCallback((index: number) => {
    const times = fullTimesRef.current;
    if (!times.length) return;
    setReplayEnd(endAfter(times, tfSecsRef.current, index));
    setReplayPick(false);
    setReplayPlaying(false);
    setSelectedId(null);
    setNewsPinned(null);
    setReplayStartTick((n) => n + 1);
  }, []);

  const stepReplay = useCallback(() => {
    const cur = replayEndRef.current;
    if (cur == null) return false;
    const next = nextEnd(fullTimesRef.current, tfSecsRef.current, cur);
    if (next == null) {
      setReplayPlaying(false);
      return false;
    }
    setReplayEnd(next);
    return true;
  }, []);

  const beginPick = () => {
    setReplayPlaying(false);
    setReplayPick(true);
    setTool('cursor');
  };

  const randomBar = () => {
    const n = fullTimesRef.current.length;
    if (n < 30) return;
    startReplayAt(Math.floor(20 + Math.random() * (n - 40)));
  };

  // Playback timer: one candle per tick.
  const replayActive = replayEnd != null;
  useEffect(() => {
    if (!replayPlaying || !replayActive) return;
    const timer = setInterval(() => {
      stepReplay();
    }, 1000 / replaySpeed);
    return () => clearInterval(timer);
  }, [replayPlaying, replayActive, replaySpeed, stepReplay]);

  // A different symbol is a different history: leave replay.
  useEffect(() => {
    exitReplay();
  }, [symbol, exitReplay]);

  // ── chart lifecycle ───────────────────────────────────────────────────
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#131722' },
        textColor: '#b2b5be',
        fontFamily: FONT,
        fontSize: 12,
        panes: {
          separatorColor: '#2a2e39',
          separatorHoverColor: 'rgba(41,98,255,0.3)',
          enableResize: true,
        },
      },
      grid: {
        vertLines: { color: 'rgba(42,46,57,0.6)' },
        horzLines: { color: 'rgba(42,46,57,0.6)' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: '#9598a1',
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: '#363a45',
        },
        horzLine: {
          color: '#9598a1',
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: '#363a45',
        },
      },
      rightPriceScale: {
        borderColor: '#2a2e39',
        scaleMargins: { top: 0.08, bottom: 0.08 },
        minimumWidth: 72,
      },
      timeScale: {
        borderColor: '#2a2e39',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 8,
        barSpacing: 9,
        minBarSpacing: 0.4,
      },
      localization: {
        timeFormatter: (t: Time) => formatCrosshairTime(Number(t), intradayRef.current),
      },
    });
    chartRef.current = chart;

    const view = new FootprintView();
    footViewRef.current = view;
    view.setChart(chart);
    footRef.current = chart.addCustomSeries(
      view,
      { priceLineVisible: false, lastValueVisible: false },
      0,
    );
    primRef.current = new DrawingsPrimitive();
    newsPrimRef.current = new NewsPrimitive();
    deltaPrimRef.current = new DeltaPrimitive();
    sessionsPrimRef.current = new SessionsPrimitive();
    svpPrimRef.current = new SessionProfilePrimitive();

    chart.subscribeCrosshairMove((param) => {
      setHoverIdx(param.logical == null ? null : Math.round(param.logical));
    });

    return () => {
      chart.remove();
      chartRef.current = null;
      mainRef.current = null;
      footRef.current = null;
      indStructRef.current = { key: '', entries: [] };
    };
  }, []);

  // Main price series: rebuilt only when its kind changes.
  useEffect(() => {
    const chart = chartRef.current;
    const prim = primRef.current;
    if (!chart || !prim) return;
    const effective: ChartType = fp.on ? 'candles' : chartType;
    const key = `${effective}|${fp.on}`;
    if (mainRef.current?.key !== key) {
      if (mainRef.current) chart.removeSeries(mainRef.current.series);
      const common = {
        lastValueVisible: true,
        priceLineVisible: true,
        priceLineColor: '#787b86',
        priceLineStyle: LineStyle.Dotted,
      };
      let series: ISeriesApi<SeriesType, Time>;
      if (effective === 'line') {
        series = chart.addSeries(LineSeries, { ...common, color: '#2962ff', lineWidth: 2 }, 0);
      } else if (effective === 'area') {
        series = chart.addSeries(
          AreaSeries,
          {
            ...common,
            lineColor: '#2962ff',
            topColor: 'rgba(41,98,255,0.35)',
            bottomColor: 'rgba(41,98,255,0.02)',
            lineWidth: 2,
          },
          0,
        );
      } else if (effective === 'bars') {
        series = chart.addSeries(BarSeries, { ...common, upColor: UP, downColor: DOWN }, 0);
      } else {
        const ghost = fp.on;
        series = chart.addSeries(
          CandlestickSeries,
          {
            ...common,
            upColor: ghost ? CLEAR : effective === 'hollow' ? CLEAR : UP,
            downColor: ghost ? CLEAR : DOWN,
            borderUpColor: ghost ? CLEAR : UP,
            borderDownColor: ghost ? CLEAR : DOWN,
            wickUpColor: ghost ? CLEAR : UP,
            wickDownColor: ghost ? CLEAR : DOWN,
            borderVisible: true,
          },
          0,
        );
      }
      series.attachPrimitive(prim);
      if (newsPrimRef.current) series.attachPrimitive(newsPrimRef.current);
      if (deltaPrimRef.current) series.attachPrimitive(deltaPrimRef.current);
      if (sessionsPrimRef.current) series.attachPrimitive(sessionsPrimRef.current);
      if (svpPrimRef.current) series.attachPrimitive(svpPrimRef.current);
      mainRef.current = { key, series };
    }
  }, [chartType, fp.on]);

  // Price data → main series (+ default view on a fresh load).
  useEffect(() => {
    const chart = chartRef.current;
    const main = mainRef.current;
    if (!chart || !main || !bars.length) return;
    const shown = chartType === 'ha' && !fp.on ? heikinAshi(bars) : bars;
    const isValue = chartType === 'line' || chartType === 'area';
    const data = shown.map((b, i) =>
      isValue
        ? { time: wall[i]!, value: b.close }
        : { time: wall[i]!, open: b.open, high: b.high, low: b.low, close: b.close },
    );
    main.series.applyOptions({
      priceFormat: { type: 'price', precision: decimals, minMove: 1 / 10 ** decimals },
      title: symbol.replace(/\.(NS|BO)$/, ''),
    });
    const viewKey = `${raw.key}|${tfId}`;
    // Earlier bars arriving in front of the same series shift every logical index: carry the view over by as many
    // bars as now precede the old first bar, so it stays on the same candles.
    const keptRange = viewKeyRef.current === viewKey ? chart.timeScale().getVisibleLogicalRange() : null;
    const prevFirst = firstWallRef.current;
    firstWallRef.current = { key: viewKey, time: wall[0]! };
    main.series.setData(data as never);
    if (keptRange && prevFirst && prevFirst.key === viewKey && wall[0]! < prevFirst.time) {
      const shift = wall.findIndex((t) => t >= prevFirst.time);
      if (shift > 0) {
        chart.timeScale().setVisibleLogicalRange({ from: keptRange.from + shift, to: keptRange.to + shift });
      }
    }
    if (viewKeyRef.current !== viewKey) {
      viewKeyRef.current = viewKey;
      const n = bars.length;
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(-2, n - 140), to: n + 7 });
      chart.priceScale('right').applyOptions({ autoScale: true });
    }
  }, [bars, wall, chartType, fp.on, decimals, symbol, raw.key, tfId]);

  // Replay: recentre when a replay starts; follow the newest candle while stepping; show the live edge on exit.
  const wasReplaying = useRef(false);
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || replayStartTick === 0) return;
    const n = bars.length;
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(-2, n - 120), to: n + 8 });
    chart.priceScale('right').applyOptions({ autoScale: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [replayStartTick]);
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    if (replayEnd == null) {
      if (wasReplaying.current) chart.timeScale().scrollToRealTime();
      wasReplaying.current = false;
      return;
    }
    wasReplaying.current = true;
    const range = chart.timeScale().getVisibleLogicalRange();
    if (range && range.to >= bars.length - 2) chart.timeScale().scrollToRealTime();
  }, [replayEnd, bars.length]);

  // Footprint series.
  useEffect(() => {
    const series = footRef.current;
    const view = footViewRef.current;
    if (!series || !view) return;
    view.setSettings({ imbalance: fp.imbalance, showDelta: fp.showDelta });
    series.applyOptions({
      priceFormat: { type: 'price', precision: decimals, minMove: 1 / 10 ** decimals },
    } as never);
    if (!fp.on || !bars.length) {
      series.setData([]);
      return;
    }
    const data: FootprintData[] = bars.map((b, i) => ({
      time: wall[i]!,
      open: b.open,
      high: b.high,
      low: b.low,
      close: b.close,
      fp: footprints[i] ?? null,
    }));
    series.setData(data);
  }, [fp.on, fp.imbalance, fp.showDelta, bars, wall, footprints, decimals]);

  // Indicator series structure: rebuilt only when the indicator set / params change.
  const structKey = JSON.stringify(indicators.map((cfg) => ({ ...cfg, hidden: undefined, collapsed: undefined })));
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const st = indStructRef.current;
    if (st.key === structKey) return;
    for (const entry of st.entries) chart.removeSeries(entry.series);
    const entries: typeof st.entries = [];
    const flags = {
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    };
    for (const c of computed) {
      for (const line of c.lines) {
        let series: ISeriesApi<SeriesType, Time>;
        if (c.cfg.type === 'volume') {
          series = chart.addSeries(
            HistogramSeries,
            {
              priceFormat: { type: 'volume' },
              priceScaleId: 'vol',
              lastValueVisible: false,
              priceLineVisible: false,
            },
            0,
          );
          series.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
        } else if (line.kind === 'candle') {
          series = chart.addSeries(
            CandlestickSeries,
            {
              upColor: UP,
              downColor: DOWN,
              borderUpColor: UP,
              borderDownColor: DOWN,
              wickUpColor: UP,
              wickDownColor: DOWN,
              priceFormat: { type: 'volume' },
              lastValueVisible: true,
              priceLineVisible: false,
            },
            c.pane,
          );
        } else if (line.kind === 'hist') {
          series = chart.addSeries(
            HistogramSeries,
            {
              priceFormat: {
                type: 'price',
                precision: c.cfg.type === 'rvol' ? 2 : 3,
                minMove: 0.001,
              },
              lastValueVisible: false,
              priceLineVisible: false,
            },
            c.pane,
          );
        } else {
          series = chart.addSeries(
            LineSeries,
            {
              ...flags,
              color: line.color,
              lineWidth: 1,
              lastValueVisible: c.pane > 0,
              priceFormat:
                c.pane > 0
                  ? { type: 'price', precision: c.cfg.type === 'rsi' ? 2 : 3, minMove: 0.001 }
                  : undefined,
            },
            c.pane,
          );
        }
        if (c.cfg.type === 'rsi') {
          for (const level of [70, 50, 30]) {
            series.createPriceLine({
              price: level,
              color: '#363a45',
              lineWidth: 1,
              lineStyle: LineStyle.Dashed,
              axisLabelVisible: false,
            });
          }
        }
        if (c.cfg.type === 'rvol') {
          series.createPriceLine({
            price: 1,
            color: '#363a45',
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: false,
          });
        }
        entries.push({ line, cfgId: c.cfg.id, series });
      }
    }
    indStructRef.current = { key: structKey, entries };
    // Pane DOM nodes host the per-pane legends; they exist only after the first layout, so retry briefly.
    let attempts = 0;
    const syncPanes = () => {
      const live = chartRef.current;
      if (!live) return;
      const els = live.panes().map((pane) => pane.getHTMLElement());
      if (els.some((node) => !node) && attempts < 150) {
        attempts += 1;
        window.setTimeout(syncPanes, 100);
        return;
      }
      // The chart library rebuilds a pane's cells, but keeps the pane row itself. React validates portals
      // against their container's tag, so each pane gets a plain <div> host rather than portaling into the row.
      const hosts = els.map((node) => {
        if (!node) return null;
        node.style.position = 'relative';
        let host = node.querySelector<HTMLElement>(':scope > [data-tv-legend-host]');
        if (!host) {
          host = document.createElement('div');
          host.dataset.tvLegendHost = '';
          host.style.cssText = 'position:absolute;inset:0;pointer-events:none;z-index:3';
          node.appendChild(host);
        }
        return host;
      });
      setPaneEls(hosts);
    };
    syncPanes();
  }, [structKey, computed]);

  // Pane heights: price pane 4 shares, indicator panes 1 each, a collapsed pane just its legend row.
  // Stretch factors are relative, so a collapsed pane's factor is re-derived whenever the chart resizes.
  const maxPane = computed.find((c) => c.cfg.id === maxPaneId && c.pane > 0)?.pane ?? null;
  const collapsedKey = (
    maxPane != null
      ? [0, ...computed.map((c) => c.pane)].filter((i, k, all) => i !== maxPane && all.indexOf(i) === k)
      : computed.filter((c) => c.pane > 0 && c.cfg.collapsed).map((c) => c.pane)
  ).join(',');
  useEffect(() => {
    const chart = chartRef.current;
    const el = containerRef.current;
    if (!chart || !el) return;
    const collapsed = new Set(collapsedKey ? collapsedKey.split(',').map(Number) : []);
    const COLLAPSED_PX = 26;
    const layout = () => {
      const panes = chart.panes();
      const open = panes.reduce((sum, _p, i) => sum + (collapsed.has(i) ? 0 : i === 0 ? 4 : 1), 0);
      const total = panes.reduce((sum, p) => sum + p.getHeight(), 0);
      const free = Math.max(1, total - COLLAPSED_PX * collapsed.size);
      const tiny = (COLLAPSED_PX * open) / free;
      panes.forEach((pane, i) => pane.setStretchFactor(collapsed.has(i) ? tiny : i === 0 ? 4 : 1));
    };
    layout();
    if (!collapsed.size) return;
    const ro = new ResizeObserver(layout);
    ro.observe(el);
    return () => ro.disconnect();
  }, [collapsedKey, structKey]);

  // Indicator visibility (eye toggle in the legend); runs after the rebuild above on a structure change.
  useEffect(() => {
    for (const entry of indStructRef.current.entries) {
      const cfg = indicators.find((i) => i.id === entry.cfgId);
      entry.series.applyOptions({ visible: !cfg?.hidden });
    }
  }, [indicators, structKey]);

  // Indicator data.
  useEffect(() => {
    const entries = indStructRef.current.entries;
    let i = 0;
    for (const c of computed) {
      for (const line of c.lines) {
        const entry = entries[i];
        i += 1;
        if (!entry || entry.cfgId !== c.cfg.id) continue;
        if (c.cfg.type === 'volume') {
          entry.series.setData(
            bars.map((b, idx) => ({
              time: wall[idx]!,
              value: b.volume,
              color: b.close >= b.open ? VOL_UP : VOL_DOWN,
            })) as never,
          );
        } else if (c.cfg.type === 'rvol') {
          const out: { time: UTCTimestamp; value: number; color: string }[] = [];
          line.values.forEach((v, idx) => {
            if (v == null) return;
            const b = bars[idx]!;
            out.push({ time: wall[idx]!, value: v, color: b.close >= b.open ? VOL_UP : VOL_DOWN });
          });
          entry.series.setData(out as never);
        } else if (line.kind === 'candle') {
          // A candle with no volume data is a gap, not a flat line at the running total
          entry.series.setData(
            (line.ohlc ?? []).map((k, idx) =>
              k.missing
                ? { time: wall[idx]! }
                : { time: wall[idx]!, open: k.open, high: k.high, low: k.low, close: k.close },
            ) as never,
          );
        } else if (line.kind === 'hist') {
          const out: { time: UTCTimestamp; value: number; color: string }[] = [];
          let prev: number | null = null;
          line.values.forEach((v, idx) => {
            if (v == null) return;
            out.push({ time: wall[idx]!, value: v, color: histColor(v, prev) });
            prev = v;
          });
          entry.series.setData(out as never);
        } else {
          const out: { time: UTCTimestamp; value: number }[] = [];
          line.values.forEach((v, idx) => {
            if (v != null) out.push({ time: wall[idx]!, value: v });
          });
          entry.series.setData(out as never);
        }
      }
    }
  }, [computed, bars, wall, structKey]);

  // Price scale mode.
  useEffect(() => {
    chartRef.current?.priceScale('right').applyOptions({
      mode: percent
        ? PriceScaleMode.Percentage
        : log
          ? PriceScaleMode.Logarithmic
          : PriceScaleMode.Normal,
    });
  }, [percent, log]);

  // Session volume profile indicator → primitive: one candle range per trading session (intraday only).
  useEffect(() => {
    const prim = svpPrimRef.current;
    if (!prim) return;
    const cfg = indicators.find((i) => i.type === 'svp');
    if (!cfg || cfg.hidden || !tf.intraday || !bars.length) {
      prim.setState([], []);
      return;
    }
    const starts = [
      0,
      ...sessionBreaks(
        bars.map((b) => b.time),
        tf.secs,
      ),
    ];
    const sessions = starts.map((s, i): [number, number] => [
      s,
      (starts[i + 1] ?? bars.length) - 1,
    ]);
    // Never let minutes after the replay cut-off into a profile.
    const fine =
      tf.secs <= 60
        ? null
        : replayEnd == null
          ? subBars
          : subBars.filter((b) => b.time < replayEnd);
    prim.setState(bars, sessions, {
      rows: cfg.length,
      width: (cfg.width ?? 30) / 100,
      style: cfg.style ?? 'split',
      secs: tf.secs,
      fine: fine && fine.length ? fine : null,
    });
  }, [indicators, tf.intraday, tf.secs, bars, subBars, replayEnd, chartType, fp.on]);

  // Session shading + day-start lines → primitive (intraday charts of instruments with a defined session).
  useEffect(() => {
    const prim = sessionsPrimRef.current;
    if (!prim) return;
    if (!sessionsOn || !tf.intraday || !bars.length) {
      prim.setState([], []);
      return;
    }
    const def = sessionInfo ? sessionFor(sessionInfo) : null;
    const shade = def ? runs(extendedMask(wall as number[], def)) : [];
    prim.setState(
      shade,
      sessionBreaks(
        bars.map((b) => b.time),
        tf.secs,
      ),
    );
  }, [sessionsOn, tf.intraday, tf.secs, bars, wall, sessionInfo, chartType, fp.on]);

  // News badges → primitive.
  useEffect(() => {
    newsPrimRef.current?.setState([...newsByBar.keys()], newsPinned ?? newsHover);
  }, [newsByBar, newsPinned, newsHover, chartType, fp.on]);

  // Absorption setups → badges on the candles (hidden while the panel's "Mark on chart" is off).
  const deltaMark = useCallback(
    (d: PlacedDivergence): DeltaMark => ({
      startIndex: d.startChartIndex,
      index: d.chartIndex,
      // the 15-minute candle's own extremes, so the badge sits at the wick tip even on a 5m or 1m chart
      high: d.high,
      low: d.low,
      kind: d.kind,
      confirmed: d.status === 'confirmed',
      confirmIndex: d.confirmChartIndex,
      // buyers trapped → the signal candle's low is what gets broken; sellers trapped → its high
      level: d.status === 'confirmed' ? (d.kind === 'buyers' ? d.low : d.high) : null,
    }),
    [bars],
  );
  useEffect(() => {
    deltaPrimRef.current?.setState(
      deltaMarks ? divergences.map(deltaMark) : [],
      fp.on && fp.showDelta,
    );
  }, [divergences, deltaMarks, deltaMark, bars, chartType, fp.on, fp.showDelta]);
  useEffect(() => setDeltaPick(null), [symbol, tfId]);

  // Drawings → primitive.
  useEffect(() => {
    const all = measure ? [...drawings, measure] : drawings;
    primRef.current?.setState({
      drawings: all,
      draft,
      selectedId,
      hidden: hideAll,
      axis,
      decimals,
      bars,
    });
  }, [drawings, measure, draft, selectedId, hideAll, axis, decimals, bars]);

  // ── pointer interaction with drawings ─────────────────────────────────
  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    let drag: Drag | null = null;
    let draftRef: Drawing | null = null;

    const local = (e: MouseEvent) => {
      const rect = wrap.getBoundingClientRect();
      return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    };
    const inPlot = (x: number, y: number) => {
      const size = chartRef.current?.paneSize(0);
      return !!size && x >= 0 && y >= 0 && x <= size.width && y <= size.height;
    };
    const toPt = (x: number, y: number, snap: boolean): Pt | null => {
      const chart = chartRef.current;
      const main = mainRef.current?.series;
      if (!chart || !main) return null;
      const logical = chart.timeScale().coordinateToLogical(x);
      const raw = main.coordinateToPrice(y);
      if (logical == null || raw == null) return null;
      let price: number = raw;
      let t = axis.toTime(logical);
      if (snap && latest.current.magnet) {
        const idx = Math.max(0, Math.min(latest.current.bars.length - 1, Math.round(logical)));
        const b = latest.current.bars[idx];
        if (b) {
          let best: { dy: number; p: number } = { dy: Infinity, p: price };
          for (const v of [b.open, b.high, b.low, b.close]) {
            const vy = main.priceToCoordinate(v);
            if (vy != null && Math.abs(vy - y) < best.dy) best = { dy: Math.abs(vy - y), p: v };
          }
          if (best.dy < 30) {
            price = best.p;
            t = b.time;
          }
        }
      }
      return { t, p: price };
    };
    const finishDraft = () => {
      const d = draftRef;
      draftRef = null;
      setDraft(null);
      if (!d) return;
      if (d.kind === 'measure') {
        setMeasure(d);
      } else {
        setDrawings([...latest.current.drawings, d]);
        setSelectedId(d.id);
      }
      if (!latest.current.keep) setTool('cursor');
    };
    const cancelDraft = () => {
      draftRef = null;
      setDraft(null);
      drag = null;
    };

    const onDown = (e: MouseEvent) => {
      if (e.button !== 0) return;
      if ((e.target as HTMLElement).closest('[data-tv-ui]')) return;
      const { x, y } = local(e);
      if (!inPlot(x, y)) return;
      const L = latest.current;
      const prim = primRef.current;

      if (replayPickRef.current) {
        e.stopImmediatePropagation();
        e.preventDefault();
        const logical = chartRef.current?.timeScale().coordinateToLogical(x);
        if (logical != null) {
          const last = fullTimesRef.current.length - 1;
          startReplayAt(Math.max(0, Math.min(last, Math.round(logical))));
        }
        return;
      }

      if (L.tool === 'cursor') {
        const bar = newsPrimRef.current?.findBar(x, y) ?? null;
        if (bar != null) {
          e.stopImmediatePropagation();
          e.preventDefault();
          setNewsExpanded(false);
          setNewsPinned((cur) => (cur === bar ? null : bar));
          return;
        }
        setNewsPinned(null);
      }

      if (L.tool !== 'cursor') {
        e.stopImmediatePropagation();
        e.preventDefault();
        const pt = toPt(x, y, true);
        if (!pt) return;
        const kind = L.tool as DrawingKind;
        setMeasure(null);
        if (draftRef) {
          // second click of a click-click drawing
          draftRef = { ...draftRef, pts: [draftRef.pts[0]!, pt] };
          finishDraft();
          drag = null;
          return;
        }
        const need = POINTS_NEEDED[kind];
        let pts: Pt[] = need === 1 ? [pt] : [pt, pt];
        if (isPosition(kind)) {
          // One click drops the whole tool: ~1/5 of the screen wide, stop 55px away and a 2R target.
          const chart = chartRef.current;
          const main = mainRef.current?.series;
          const range = chart?.timeScale().getVisibleLogicalRange();
          const above = main?.coordinateToPrice(y - 55);
          const spanBars = range ? Math.max(8, (range.to - range.from) * 0.2) : 20;
          if (above == null) return;
          const riskDist = Math.abs(above - pt.p);
          const widthSecs = axis.toTime(axis.toLogical(pt.t) + spanBars) - pt.t;
          pts = positionPts(kind, pt, widthSecs, riskDist, riskDist * 2);
        }
        const d: Drawing = {
          id: kind === 'measure' ? 'm_live' : newId(),
          kind,
          pts,
          color: DEFAULT_COLOR[kind],
          width: 2,
          text: kind === 'text' ? 'Text' : undefined,
          rows: kind === 'vprofile' ? DEFAULT_ROWS : undefined,
        };
        if (kind === 'text') {
          const input = window.prompt('Text', 'Text');
          if (input === null) return;
          d.text = input || 'Text';
        }
        draftRef = d;
        setDraft(d);
        if (need === 1) {
          finishDraft();
          return;
        }
        drag = { mode: 'create', startX: x, startY: y, moved: false };
        return;
      }

      setMeasure(null);
      const hit = prim?.findHit(x, y) ?? null;
      if (!hit) {
        setSelectedId(null);
        return;
      }
      e.stopImmediatePropagation();
      e.preventDefault();
      setSelectedId(hit.drawingId);
      const d = L.drawings.find((v) => v.id === hit.drawingId);
      if (!d || d.locked || L.lockAll) return;
      const chart = chartRef.current;
      const main = mainRef.current?.series;
      if (!chart || !main) return;
      if (hit.handle === 'body') {
        const logical = chart.timeScale().coordinateToLogical(x);
        const price = main.coordinateToPrice(y);
        if (logical == null || price == null) return;
        drag = {
          mode: 'move',
          id: d.id,
          orig: d,
          startLogical: logical,
          startPrice: price,
          moved: false,
        };
      } else {
        drag = { mode: 'handle', id: d.id, handle: hit.handle, moved: false };
      }
    };

    const onMove = (e: MouseEvent) => {
      const { x, y } = local(e);
      const prim = primRef.current;
      if (draftRef) {
        const pt = toPt(x, y, true);
        if (pt) {
          draftRef = { ...draftRef, pts: [draftRef.pts[0]!, pt] };
          setDraft(draftRef);
        }
        if (drag?.mode === 'create' && Math.hypot(x - drag.startX, y - drag.startY) > 6)
          drag.moved = true;
        return;
      }
      if (drag && (drag.mode === 'move' || drag.mode === 'handle')) {
        const chart = chartRef.current;
        const main = mainRef.current?.series;
        if (!chart || !main) return;
        drag.moved = true;
        if (drag.mode === 'move') {
          const logical = chart.timeScale().coordinateToLogical(x);
          const price = main.coordinateToPrice(y);
          if (logical == null || price == null) return;
          const dl = logical - drag.startLogical;
          const dp = price - drag.startPrice;
          const moved: Drawing = {
            ...drag.orig,
            pts: drag.orig.pts.map((pt) => ({
              t: axis.toTime(axis.toLogical(pt.t) + dl),
              p: pt.p + dp,
            })),
          };
          const id = drag.id;
          setDrawState((cur) => ({
            symbol: cur.symbol,
            list: cur.list.map((v) => (v.id === id ? moved : v)),
          }));
        } else {
          const pt = toPt(x, y, true);
          if (!pt) return;
          const { id, handle } = drag;
          setDrawState((cur) => ({
            symbol: cur.symbol,
            list: cur.list.map((v) => {
              if (v.id !== id) return v;
              if (isPosition(v.kind)) return dragPositionHandle(v, handle, pt);
              // A volume profile's lines only move in time; its price anchors are irrelevant.
              const pts = v.pts.map((q, i) =>
                i === handle ? (v.kind === 'vprofile' ? { t: pt.t, p: q.p } : pt) : q,
              );
              return { ...v, pts };
            }),
          }));
        }
        return;
      }
      if (latest.current.tool === 'cursor') {
        const bar = inPlot(x, y) ? (newsPrimRef.current?.findBar(x, y) ?? null) : null;
        setNewsHover(bar);
        const hit = inPlot(x, y) && bar == null ? (prim?.findHit(x, y) ?? null) : null;
        prim?.setState({ hoverId: hit?.drawingId ?? null });
        wrap.classList.toggle('tv-over-drawing', !!hit || bar != null);
      }
    };

    const onUp = (e: MouseEvent) => {
      if (draftRef && drag?.mode === 'create') {
        if (drag.moved) {
          const { x, y } = local(e);
          const pt = toPt(x, y, true);
          if (pt) draftRef = { ...draftRef, pts: [draftRef.pts[0]!, pt] };
          finishDraft();
        }
        drag = null;
        return;
      }
      if (drag && (drag.mode === 'move' || drag.mode === 'handle')) {
        if (drag.moved) {
          // The live edits were applied without history; record the pre-drag state once.
          const before =
            drag.mode === 'move'
              ? drag.orig
              : latest.current.drawings.find((v) => v.id === (drag as Drag & { id: string }).id);
          if (before) {
            const snapshot = latest.current.drawings.map((v) => (v.id === before.id ? before : v));
            history.current.past.push(snapshot);
            history.current.future = [];
            setHistTick((n) => n + 1);
          }
        }
        drag = null;
      }
      const chart = chartRef.current;
      if (chart) setAutoScale(chart.priceScale('right').options().autoScale);
    };

    const onDbl = (e: MouseEvent) => {
      const { x, y } = local(e);
      const hit = primRef.current?.findHit(x, y);
      if (!hit) return;
      const d = latest.current.drawings.find((v) => v.id === hit.drawingId);
      if (d && isPosition(d.kind)) {
        setSelectedId(d.id);
        setPosEditId(d.id);
        return;
      }
      if (d?.kind !== 'text') return;
      const input = window.prompt('Text', d.text ?? '');
      if (input === null) return;
      setDrawings(
        latest.current.drawings.map((v) => (v.id === d.id ? { ...v, text: input || v.text } : v)),
      );
    };

    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.closest('input, textarea, select')) return;
      if (e.shiftKey && e.key === 'ArrowRight' && replayEndRef.current != null) {
        e.preventDefault();
        stepReplay();
        return;
      }
      if (e.shiftKey && e.key === 'ArrowDown' && replayEndRef.current != null) {
        e.preventDefault();
        setReplayPlaying((v) => !v);
        return;
      }
      if (e.key === 'Escape' && replayPickRef.current) {
        setReplayPick(false);
        return;
      }
      if (e.key === 'Escape') {
        setNewsPinned(null);
        cancelDraft();
        setTool('cursor');
        setSelectedId(null);
        setMeasure(null);
      } else if ((e.key === 'Delete' || e.key === 'Backspace') && latest.current.selectedId) {
        const id = latest.current.selectedId;
        const d = latest.current.drawings.find((v) => v.id === id);
        if (d && !d.locked) {
          setDrawings(latest.current.drawings.filter((v) => v.id !== id));
          setSelectedId(null);
        }
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'y') {
        e.preventDefault();
        redo();
      }
    };

    // Touch: the same handlers, fed by the first finger. A touch that grabs a drawing, or any touch
    // while a drawing tool is active, is claimed (so the chart does not pan under it); otherwise the
    // chart's own touch pan / pinch / long-press crosshair run untouched.
    const asMouse = (t: TouchEvent): MouseEvent | null => {
      const touch = t.changedTouches[0];
      if (!touch) return null;
      return {
        clientX: touch.clientX,
        clientY: touch.clientY,
        button: 0,
        target: t.target,
        stopImmediatePropagation: () => t.stopImmediatePropagation(),
        preventDefault: () => {
          if (t.cancelable) t.preventDefault();
        },
      } as unknown as MouseEvent;
    };
    const onTouchStart = (t: TouchEvent) => {
      if (t.touches.length !== 1) return;
      const m = asMouse(t);
      if (m) onDown(m);
    };
    const onTouchMove = (t: TouchEvent) => {
      if (!draftRef && !(drag && (drag.mode === 'move' || drag.mode === 'handle'))) return;
      const m = asMouse(t);
      if (!m) return;
      t.stopImmediatePropagation();
      if (t.cancelable) t.preventDefault();
      onMove(m);
    };
    const onTouchEnd = (t: TouchEvent) => {
      const m = asMouse(t);
      if (m) onUp(m);
    };
    wrap.addEventListener('touchstart', onTouchStart, { capture: true, passive: false });
    wrap.addEventListener('touchmove', onTouchMove, { capture: true, passive: false });
    wrap.addEventListener('touchend', onTouchEnd, true);
    wrap.addEventListener('mousedown', onDown, true);
    wrap.addEventListener('mousemove', onMove, true);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    wrap.addEventListener('dblclick', onDbl, true);
    window.addEventListener('keydown', onKey);
    return () => {
      wrap.removeEventListener('touchstart', onTouchStart, true);
      wrap.removeEventListener('touchmove', onTouchMove, true);
      wrap.removeEventListener('touchend', onTouchEnd, true);
      wrap.removeEventListener('mousedown', onDown, true);
      wrap.removeEventListener('mousemove', onMove, true);
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      wrap.removeEventListener('dblclick', onDbl, true);
      window.removeEventListener('keydown', onKey);
    };
  }, [axis, setDrawings, undo, redo, startReplayAt, stepReplay]);

  // Drop an unfinished draft when the tool changes.
  useEffect(() => {
    if (tool === 'cursor') setDraft(null);
  }, [tool]);

  // ── toolbar actions ───────────────────────────────────────────────────
  const rangeTo = (id: RangeId) => {
    const chart = chartRef.current;
    if (!chart || !bars.length) return;
    const n = bars.length;
    if (id === 'All') {
      chart.timeScale().fitContent();
      return;
    }
    const last = bars[n - 1]!;
    let fromTime: number;
    if (id === '1D' || id === '5D') {
      const days = id === '1D' ? 1 : 5;
      const seen: string[] = [];
      let idx = n - 1;
      for (; idx >= 0; idx -= 1) {
        const d = sessionDay(bars[idx]!.time, timeZone);
        if (!seen.includes(d)) {
          if (seen.length === days) break;
          seen.push(d);
        }
      }
      chart.timeScale().setVisibleLogicalRange({ from: idx + 1 - 1, to: n + 5 });
      return;
    }
    if (id === 'YTD') {
      const year = new Date(wallClock(last.time, timeZone) * 1000).getUTCFullYear();
      fromTime = Date.UTC(year, 0, 1) / 1000;
      const idx = wall.findIndex((t) => t >= fromTime);
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(-2, idx - 1), to: n + 5 });
      return;
    }
    const days = { '1M': 30, '3M': 91, '6M': 182, '1Y': 365, '5Y': 1826 }[id];
    fromTime = wall[n - 1]! - days * 86400;
    const idx = wall.findIndex((t) => t >= fromTime);
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(-2, idx - 1), to: n + 5 });
  };

  /** Centre the chart on a bar, keeping the current zoom, and pulse its badge for a moment so it is easy to spot. */
  const jumpToBar = (index: number) => {
    const ts = chartRef.current?.timeScale();
    const range = ts?.getVisibleLogicalRange();
    if (!ts || !range) return;
    const half = (range.to - range.from) / 2;
    const d = divergences.find((x) => x.chartIndex === index);
    // centre on the middle of the 15-minute candle (several bars wide on a 5m or 1m chart)
    const mid = d ? (d.startChartIndex + d.chartIndex) / 2 : index;
    ts.setVisibleLogicalRange({ from: mid - half, to: mid + half });
    setDeltaPick(index);
    if (d && bars[index]) deltaPrimRef.current?.focus(deltaMark(d));
  };

  const zoomIn = () => {
    const ts = chartRef.current?.timeScale();
    const range = ts?.getVisibleLogicalRange();
    if (!ts || !range) return;
    ts.setVisibleLogicalRange({ from: range.to - (range.to - range.from) / 1.6, to: range.to });
  };

  const screenshot = () => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.takeScreenshot(true).toBlob((blob) => {
      if (!blob) return;
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${symbol}-${tfId}.png`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    });
  };

  const fullscreen = () => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void rootRef.current?.requestFullscreen?.();
  };

  const removeAll = () => {
    if (!drawings.length) return;
    if (window.confirm(`Remove all ${drawings.length} drawings on ${symbol}?`)) {
      setDrawings([]);
      setSelectedId(null);
    }
  };

  const selected = drawings.find((d) => d.id === selectedId) ?? null;
  const patchSelected = (patch: Partial<Drawing>) => {
    if (!selected) return;
    setDrawings(drawings.map((d) => (d.id === selected.id ? { ...d, ...patch } : d)));
  };

  // ── legends ───────────────────────────────────────────────────────────
  const idx =
    hoverIdx != null && hoverIdx >= 0 && hoverIdx < bars.length ? hoverIdx : bars.length - 1;
  const bar = bars[idx];
  const prevClose = idx > 0 ? bars[idx - 1]!.close : (meta?.prevClose ?? bar?.open ?? 0);
  const change = bar ? bar.close - prevClose : 0;
  const changePct = prevClose ? (change / prevClose) * 100 : 0;
  const candleColor = bar && bar.close >= bar.open ? UP : DOWN;

  const valueText = (c: Computed, line: Line) => {
    const v = line.values[idx];
    if (v == null) return '';
    if (c.cfg.type === 'cvd') return fmtDelta(v);
    if (c.cfg.type === 'volume') return fmtVolume(v);
    if (c.cfg.type === 'rvol' || c.cfg.type === 'rsi') return v.toFixed(2);
    if (c.cfg.type === 'atr') return v.toFixed(decimals);
    if (c.cfg.type === 'macd') return v.toFixed(3);
    return v.toFixed(decimals);
  };

  const legendRow = (c: Computed): ReactNode => (
    <div
      key={c.cfg.id}
      className="group pointer-events-auto flex w-fit max-w-full items-center gap-2 text-[12px] leading-5"
      data-tv-ui
    >
      <span className={c.cfg.hidden ? 'text-[#5d606b]' : 'text-[#b2b5be]'}>
        {indicatorLabel(c.cfg)}
      </span>
      {c.cfg.hidden ? null : c.lines.map((l) => {
        const k = l.ohlc?.[idx];
        if (k?.missing) {
          return (
            <span key={l.key} className="text-[#787b86]" title="Yahoo reports no volume outside regular hours">
              n/a
            </span>
          );
        }
        if (k) {
          // CVD candle: open / high / low / close of the running total, in the candle's colour
          return (
            <span
              key={l.key}
              className="flex items-center gap-2 tabular-nums"
              style={{ color: k.close >= k.open ? UP : DOWN }}
            >
              {(['open', 'high', 'low', 'close'] as const).map((f) => (
                <span key={f}>
                  {f[0]!.toUpperCase()}
                  <b className="ml-0.5 font-medium">{fmtDelta(k[f])}</b>
                </span>
              ))}
            </span>
          );
        }
        return (
          <span
            key={l.key}
            style={{
              color: c.cfg.type === 'volume' ? (bar && bar.close >= bar.open ? UP : DOWN) : l.color,
            }}
          >
            {valueText(c, l)}
          </span>
        );
      })}
      <span
        className={`flex gap-1 transition-opacity group-hover:opacity-100 ${c.cfg.hidden ? 'opacity-100' : 'opacity-0'}`}
      >
        <button
          type="button"
          title={c.cfg.hidden ? 'Show' : 'Hide'}
          onClick={() =>
            setIndicators((list) =>
              list.map((i) => (i.id === c.cfg.id ? { ...i, hidden: !i.hidden } : i)),
            )
          }
          className="text-[#787b86] hover:text-white"
        >
          {c.cfg.hidden ? <Icons.eyeOff size={14} /> : <Icons.eye size={14} />}
        </button>
        {c.cfg.type !== 'volume' && c.cfg.type !== 'vwap' ? (
          <button
            type="button"
            title="Settings"
            onClick={() => setSettingsId(c.cfg.id)}
            className="text-[#787b86] hover:text-white"
          >
            <Icons.gear size={14} />
          </button>
        ) : null}
        <button
          type="button"
          title="Remove"
          onClick={() => setIndicators((list) => list.filter((i) => i.id !== c.cfg.id))}
          className="text-[#787b86] hover:text-[#f23645]"
        >
          <Icons.close size={14} />
        </button>
      </span>
    </div>
  );

  const priceLegend = (
    <div
      className="pointer-events-none absolute left-2 top-1.5 z-10 select-none"
      style={{ fontFamily: FONT }}
    >
      <div className="flex flex-wrap items-center gap-x-2 text-[13px]">
        <span className="font-semibold text-[#d1d4dc]">{meta?.name ?? symbol}</span>
        <span className="text-[#787b86]">
          · {tf.label} · {meta?.exchange}
        </span>
        {bar ? (
          <span className="flex items-center gap-2 tabular-nums" style={{ color: candleColor }}>
            <span>
              O<b className="ml-0.5 font-medium">{bar.open.toFixed(decimals)}</b>
            </span>
            <span>
              H<b className="ml-0.5 font-medium">{bar.high.toFixed(decimals)}</b>
            </span>
            <span>
              L<b className="ml-0.5 font-medium">{bar.low.toFixed(decimals)}</b>
            </span>
            <span>
              C<b className="ml-0.5 font-medium">{bar.close.toFixed(decimals)}</b>
            </span>
            <span>
              {change >= 0 ? '+' : ''}
              {change.toFixed(decimals)} ({changePct >= 0 ? '+' : ''}
              {changePct.toFixed(2)}%)
            </span>
          </span>
        ) : null}
      </div>
      {maxPane == null ? computed.filter((c) => c.pane === 0).map(legendRow) : null}
    </div>
  );

  const portals = paneEls.map((el, paneIdx) => {
    if (!el || paneIdx === 0) return null;
    const rows = computed.filter((c) => c.pane === paneIdx);
    if (!rows.length) return null;
    const maximized = paneIdx === maxPane;
    const collapsed = !maximized && rows.every((c) => c.cfg.collapsed);
    const ids = new Set(rows.map((c) => c.cfg.id));
    const paneBtn =
      'flex h-5 w-5 items-center justify-center rounded border border-[#363a45] bg-[#1e222d] text-[#b2b5be] hover:text-white';
    // Pane order follows the order of own-pane indicators in the list: swap with the pane above.
    const above = computed.find((c) => c.pane === paneIdx - 1 && paneIdx > 1);
    const moveUp = () =>
      setIndicators((list) => {
        if (!above) return list;
        const a = list.findIndex((i) => i.id === above.cfg.id);
        const b = list.findIndex((i) => ids.has(i.id));
        if (a < 0 || b < 0) return list;
        const next = [...list];
        [next[a], next[b]] = [next[b]!, next[a]!];
        return next;
      });
    return createPortal(
      <>
        <div
          className="pointer-events-none absolute left-2 top-1 z-10 select-none"
          style={{ fontFamily: FONT }}
        >
          {rows.map(legendRow)}
        </div>
        <div
          className="pointer-events-auto absolute top-0.5 z-10 flex gap-1 opacity-60 transition-opacity hover:opacity-100"
          style={{ right: (chartRef.current?.priceScale('right').width() ?? 64) + 6 }}
          data-tv-ui
        >
          {above ? (
            <button type="button" title="Move pane up" onClick={moveUp} className={paneBtn}>
              <Icons.arrowUp size={14} />
            </button>
          ) : null}
          <button
            type="button"
            title={collapsed ? 'Expand pane' : 'Collapse pane'}
            onClick={() => {
              if (maximized) setMaxPaneId(null);
              setIndicators((list) =>
                list.map((i) => (ids.has(i.id) ? { ...i, collapsed: !collapsed } : i)),
              );
            }}
            className={paneBtn}
          >
            {collapsed ? <Icons.expand size={14} /> : <Icons.collapse size={14} />}
          </button>
          <button
            type="button"
            title={maximized ? 'Restore pane' : 'Maximize pane'}
            onClick={() => setMaxPaneId(maximized ? null : rows[0]!.cfg.id)}
            className={paneBtn}
          >
            {maximized ? <Icons.restore size={14} /> : <Icons.fullscreen size={14} />}
          </button>
          <button
            type="button"
            title="Delete pane"
            onClick={() => setIndicators((list) => list.filter((i) => !ids.has(i.id)))}
            className={`${paneBtn} hover:!text-[#f23645]`}
          >
            <Icons.trash size={14} />
          </button>
        </div>
      </>,
      el,
      `pane-${paneIdx}`,
    );
  });

  const canUndo = histTick >= 0 && history.current.past.length > 0;
  const canRedo = histTick >= 0 && history.current.future.length > 0;

  return (
    <div
      ref={rootRef}
      className="fixed inset-0 z-50 flex flex-col bg-[#131722] text-[#d1d4dc]"
      style={{ fontFamily: FONT }}
    >
      <style>{`.tv-over-drawing, .tv-over-drawing * { cursor: pointer !important; } .tv-tool, .tv-tool * { cursor: crosshair !important; }`}</style>
      <TopBar
        symbol={symbol.replace(/\.(NS|BO)$|=[FX]$/, '')}
        onOpenSearch={() => setShowSearch('chart')}
        tfId={tfId}
        onTf={setTfId}
        chartType={chartType}
        onChartType={setChartType}
        onIndicators={() => setShowIndicators(true)}
        news={newsOn}
        onNews={() => setNewsOn((v) => !v)}
        sessions={sessionsOn}
        onSessions={() => setSessionsOn((v) => !v)}
        replay={replayActive || replayPick}
        onReplay={() => (replayActive || replayPick ? exitReplay() : beginPick())}
        footprint={{ ...fp, step: effectiveStep }}
        onFootprint={(patch) =>
          setFp((cur) => ({
            ...cur,
            ...patch,
            ...(patch.auto === false ? { step: effectiveStep } : {}),
          }))
        }
        onFootprintStep={(dir) =>
          setFp((cur) => ({ ...cur, auto: false, step: stepLadder(effectiveStep, dir) }))
        }
        canUndo={canUndo}
        canRedo={canRedo}
        onUndo={undo}
        onRedo={redo}
        onScreenshot={screenshot}
        onFullscreen={fullscreen}
        watchlistOpen={watchOpen}
        onWatchlist={() => setWatchOpen((v) => !v)}
      />
      <div className="flex min-h-0 flex-1">
        <LeftToolbar
          tool={tool}
          onTool={(t) => {
            setTool(t);
            setMeasure(null);
          }}
          magnet={magnet}
          onMagnet={() => setMagnet((v) => !v)}
          keepDrawing={keep}
          onKeepDrawing={() => setKeep((v) => !v)}
          lockAll={lockAll}
          onLockAll={() => setLockAll((v) => !v)}
          hideAll={hideAll}
          onHideAll={() => setHideAll((v) => !v)}
          onRemoveAll={removeAll}
          onZoomIn={zoomIn}
        />
        <div className="flex min-w-0 flex-1 flex-col">
          <div
            ref={wrapRef}
            className={`relative min-h-0 flex-1 ${tool !== 'cursor' || replayPick ? 'tv-tool' : ''}`}
          >
            <div ref={containerRef} className="absolute inset-0" />
            {priceLegend}
            {portals}
            {status === 'loading' ? (
              <div className="pointer-events-none absolute right-16 top-2 z-10 rounded bg-[#1e222d] px-2 py-1 text-xs text-[#b2b5be]">
                Loading {symbol}…
              </div>
            ) : null}
            {status === 'error' ? (
              <div
                className="absolute left-1/2 top-1/2 z-10 -translate-x-1/2 -translate-y-1/2 rounded-lg border border-[#363a45] bg-[#1e222d] px-6 py-5 text-center"
                data-tv-ui
              >
                <div className="text-sm font-semibold text-[#f23645]">Couldn’t load {symbol}</div>
                <div className="mt-1 max-w-xs text-xs text-[#9598a1]">{error}</div>
                <button
                  type="button"
                  onClick={() => setShowSearch('chart')}
                  className="mt-3 rounded bg-[#2962ff] px-3 py-1 text-xs font-semibold text-white"
                >
                  Pick another symbol
                </button>
              </div>
            ) : null}
            {status === 'ok' && !bars.length ? (
              <div className="pointer-events-none absolute left-1/2 top-1/2 z-10 -translate-x-1/2 -translate-y-1/2 text-sm text-[#787b86]">
                No data for {symbol} on {tf.label}.
              </div>
            ) : null}
            {fp.on && bars.length && footprintCoverage < 1 && !fpNoteClosed ? (
              <div
                className="absolute bottom-3 left-3 z-10 flex max-w-sm items-start gap-2 rounded bg-[#1e222d]/90 py-1.5 pl-3 pr-1.5 text-[11px] text-[#b2b5be]"
                data-tv-ui
              >
                <span>
                Footprint is estimated from 1-minute bars and only exists for the last ~{subBars.length ? Math.max(1, Math.round((Date.now() / 1000 - subBars[0]!.time) / 86400)) : 7} days
                {tf.source !== '1m' && !subBars.length
                  ? ' — loading…'
                  : ` (${Math.round(footprintCoverage * 100)}% of candles covered)`}
                . Zoom in on a recent candle to read the rows.
                </span>
                <button
                  type="button"
                  title="Dismiss"
                  onClick={() => setFpNoteClosed(true)}
                  className="shrink-0 text-[#787b86] hover:text-white"
                >
                  <Icons.close size={14} />
                </button>
              </div>
            ) : null}
            {(() => {
              const idx = newsPinned ?? newsHover;
              const items = idx != null ? newsByBar.get(idx) : undefined;
              const pos = idx != null ? newsPrimRef.current?.position(idx) : null;
              if (!items?.length || !pos) return null;
              const pinned = newsPinned != null;
              const latestFirst = [...items].reverse();
              const shown = pinned && newsExpanded ? latestFirst : latestFirst.slice(0, 1);
              const fmt = (iso: string) =>
                new Intl.DateTimeFormat('en-US', {
                  month: 'short',
                  day: 'numeric',
                  timeZone,
                }).format(new Date(iso));
              const width = Math.min(320, (wrapRef.current?.clientWidth ?? 800) - 16);
              const left = Math.max(
                8,
                Math.min(pos.x - 24, (wrapRef.current?.clientWidth ?? 800) - width - 64),
              );
              return (
                <div
                  className={`absolute z-30 rounded-xl border-l-4 border-[#9c27b0] bg-[#1e222d] p-4 shadow-2xl ${pinned ? '' : 'pointer-events-none'}`}
                  style={{
                    left,
                    bottom: (wrapRef.current?.clientHeight ?? 600) - pos.y + 16,
                    width,
                    maxHeight: '60%',
                    overflowY: 'auto',
                  }}
                  data-tv-ui
                >
                  <div className="mb-3 flex items-center gap-2 text-[16px] font-semibold text-[#d1d4dc]">
                    <span className="flex h-7 w-7 items-center justify-center rounded-full bg-[#9c27b0] text-white">
                      <Icons.bolt size={16} />
                    </span>
                    Latest updates
                  </div>
                  {shown.map((item) => (
                    <div key={`${item.published_at}-${item.headline}`} className="mb-3 last:mb-0">
                      <div className="text-[12px] text-[#787b86]">
                        {fmt(item.published_at)} · {item.publisher}
                        {item.kind === 'offering' ? (
                          <span className="ml-2 rounded bg-[#f23645]/20 px-1.5 text-[#ff8a96]">
                            share offering
                          </span>
                        ) : null}
                      </div>
                      {pinned && item.url ? (
                        <a
                          href={item.url}
                          target="_blank"
                          rel="noreferrer"
                          className="mt-0.5 block text-[14px] font-medium leading-snug text-[#d1d4dc] hover:text-[#7ea4ff]"
                        >
                          {item.headline}
                        </a>
                      ) : (
                        <div className="mt-0.5 line-clamp-4 text-[14px] font-medium leading-snug text-[#d1d4dc]">
                          {item.headline}
                        </div>
                      )}
                    </div>
                  ))}
                  {pinned && !newsExpanded && items.length > 1 ? (
                    <button
                      type="button"
                      onClick={() => setNewsExpanded(true)}
                      className="text-[13px] text-[#b2b5be] hover:text-white"
                    >
                      See all ({items.length}) ›
                    </button>
                  ) : null}
                  {!pinned && items.length > 1 ? (
                    <div className="text-[13px] text-[#b2b5be]">See all ({items.length}) ›</div>
                  ) : null}
                </div>
              );
            })()}
            {newsOn && news && news.symbol === symbol && !news.res.supported ? (
              <div className="pointer-events-none absolute bottom-12 right-2 z-10 max-w-[calc(100%-16px)] md:right-20 md:max-w-xs rounded bg-[#1e222d]/90 px-3 py-1.5 text-[11px] text-[#b2b5be]">
                News unavailable: {news.res.reason}
              </div>
            ) : null}
            {replayActive || replayPick ? (
              <ReplayBar
                picking={replayPick}
                playing={replayPlaying}
                speed={replaySpeed}
                label={wall.length ? formatCrosshairTime(wall[wall.length - 1]!, tf.intraday) : ''}
                atEnd={replayAtEnd}
                onPlay={() => setReplayPlaying((v) => !v)}
                onStep={() => stepReplay()}
                onSpeed={setReplaySpeed}
                onSelect={beginPick}
                onRandom={randomBar}
                onExit={exitReplay}
              />
            ) : null}
            {selected ? (
              <div
                className="scrollbar-none absolute left-1/2 top-2 z-20 flex max-w-[calc(100%-16px)] -translate-x-1/2 items-center gap-1.5 overflow-x-auto rounded-md border border-[#363a45] bg-[#1e222d] px-2 py-1 shadow-lg"
                data-tv-ui
              >
                {selected.kind === 'fib' ||
                selected.kind === 'measure' ||
                selected.kind === 'vprofile' ||
                isPosition(selected.kind)
                  ? null
                  : SWATCHES.map((c) => (
                      <button
                        key={c}
                        type="button"
                        aria-label={`Colour ${c}`}
                        onClick={() => patchSelected({ color: c })}
                        className={`h-4 w-4 rounded-full ${selected.color === c ? 'ring-2 ring-white ring-offset-1 ring-offset-[#1e222d]' : ''}`}
                        style={{ background: c }}
                      />
                    ))}
                {selected.kind === 'vprofile' ? (
                  <label className="flex items-center gap-1.5 px-1 text-[12px] text-[#b2b5be]">
                    Rows
                    <select
                      value={selected.rows ?? DEFAULT_ROWS}
                      onChange={(e) => patchSelected({ rows: Number(e.target.value) })}
                      className="rounded border border-[#363a45] bg-[#131722] px-1.5 py-0.5 text-[#d1d4dc] outline-none"
                    >
                      {[12, 24, 48, 96].map((n) => (
                        <option key={n} value={n}>
                          {n}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : isPosition(selected.kind) ? (
                  <button
                    type="button"
                    title="Settings"
                    className="rounded p-1 text-[#b2b5be] hover:bg-[#2a2e39]"
                    onClick={() => setPosEditId(selected.id)}
                  >
                    <Icons.gear size={16} />
                  </button>
                ) : (
                  [1, 2, 3, 4].map((w) => (
                    <button
                      key={w}
                      type="button"
                      onClick={() => patchSelected({ width: w })}
                      title={`${w}px`}
                      className={`flex h-6 w-6 items-center justify-center rounded hover:bg-[#2a2e39] ${selected.width === w ? 'bg-[#2a2e39]' : ''}`}
                    >
                      <span className="block w-3.5 bg-[#d1d4dc]" style={{ height: w }} />
                    </button>
                  ))
                )}
                <button
                  type="button"
                  title="Clone"
                  className="rounded p-1 text-[#b2b5be] hover:bg-[#2a2e39]"
                  onClick={() => {
                    const copy = {
                      ...translate(selected, 0, 0),
                      id: newId(),
                      pts: selected.pts.map((p) => ({ t: p.t + tf.secs * 3, p: p.p })),
                    };
                    setDrawings([...drawings, copy]);
                    setSelectedId(copy.id);
                  }}
                >
                  <Icons.copy size={16} />
                </button>
                <button
                  type="button"
                  title={selected.locked ? 'Unlock' : 'Lock'}
                  className="rounded p-1 text-[#b2b5be] hover:bg-[#2a2e39]"
                  onClick={() => patchSelected({ locked: !selected.locked })}
                >
                  {selected.locked ? <Icons.lock size={16} /> : <Icons.unlock size={16} />}
                </button>
                <button
                  type="button"
                  title="Delete"
                  className="rounded p-1 text-[#b2b5be] hover:bg-[#2a2e39] hover:text-[#f23645]"
                  onClick={() => {
                    setDrawings(drawings.filter((d) => d.id !== selected.id));
                    setSelectedId(null);
                  }}
                >
                  <Icons.trash size={16} />
                </button>
              </div>
            ) : null}
          </div>
          <BottomBar
            onRange={rangeTo}
            lastBarEnd={bars.length ? bars[bars.length - 1]!.time + tf.secs : null}
            updatedAt={updatedAt}
            live={replayEnd == null}
            timeZone={timeZone}
            percent={percent}
            log={log}
            auto={autoScale}
            onPercent={() => {
              setPercent((v) => !v);
              setLog(false);
            }}
            onLog={() => {
              setLog((v) => !v);
              setPercent(false);
            }}
            onAuto={() => {
              chartRef.current?.priceScale('right').applyOptions({ autoScale: true });
              setAutoScale(true);
            }}
          />
        </div>
        {deltaOpen && !compact ? (
          <DeltaPanel
            symbol={meta?.symbol ?? symbol}
            items={divergences}
            chartTf={tf.label}
            chartIs15m={tf.secs === 900}
            hasData={deltaOk && (deltaSub.bars.length > 0 || deltaSub.bars5.length > 0)}
            cfg={deltaCfg}
            view={deltaView}
            onView={(patch) =>
              setDeltaView((cur) => {
                const next = { ...cur, ...patch };
                save('tv:deltaView', next);
                return next;
              })
            }
            dataFrom={Number.isFinite(deltaFirst) ? deltaFirst : null}
            dataTo={deltaLast > 0 ? deltaLast : null}
            fineFrom={deltaOk ? (deltaSub.bars[0]?.time ?? null) : null}
            onCfg={(patch) =>
              setDeltaCfg((cur) => {
                const next = { ...cur, ...patch };
                save('tv:deltaCfg', next);
                return next;
              })
            }
            marksOn={deltaMarks}
            onMarks={(on) => {
              setDeltaMarks(on);
              save('tv:deltaMarks', on);
            }}
            intraday={tf.intraday}
            timeZone={timeZone}
            active={deltaPick}
            onJump={jumpToBar}
          />
        ) : null}
        {watchOpen && !(deltaOpen && !compact) ? (
          <WatchlistPanel
            active={symbol}
            overlay={compact}
            onClose={() => setWatchOpen(false)}
            onSelect={(sym) => {
              setSymbol(sym);
              if (compact) setWatchOpen(false);
            }}
            onAdd={() => setShowSearch('watchlist')}
            pending={pendingWatch}
            onPendingHandled={clearPendingWatch}
          />
        ) : null}
        {compact ? null : (
          <RightRail
            watchlistOpen={watchOpen && !deltaOpen}
            deltaOpen={deltaOpen}
            onWatchlist={() => {
              const next = !(watchOpen && !deltaOpen);
              setWatchOpen(next);
              save('tv:watchOpen', next);
              if (next) {
                setDeltaOpen(false);
                save('tv:deltaOpen', false);
              }
            }}
            onDelta={() => {
              const next = !deltaOpen;
              setDeltaOpen(next);
              save('tv:deltaOpen', next);
            }}
          />
        )}
      </div>
      {showSearch ? (
        <SymbolSearch
          onPick={(sym, name) =>
            showSearch === 'watchlist'
              ? setPendingWatch({ symbol: sym, name: name ?? sym })
              : setSymbol(sym)
          }
          onClose={() => setShowSearch(false)}
        />
      ) : null}
      {showIndicators ? (
        <IndicatorPicker
          active={indicators}
          onAdd={(type) => {
            if (type === 'volume' && indicators.some((i) => i.type === 'volume')) return;
            setIndicators((list) => [...list, makeIndicator(type, list)]);
          }}
          onRemove={(id) => setIndicators((list) => list.filter((i) => i.id !== id))}
          onClose={() => setShowIndicators(false)}
        />
      ) : null}
      {posEditId && drawings.find((d) => d.id === posEditId) ? (
        <PositionSettings
          drawing={drawings.find((d) => d.id === posEditId)!}
          bars={bars}
          decimals={decimals}
          onChange={(next) =>
            setDrawings(drawings.map((d) => (d.id === next.id ? next : d)))
          }
          onClose={() => setPosEditId(null)}
        />
      ) : null}
      {settingsId && indicators.find((i) => i.id === settingsId) ? (
        <IndicatorSettings
          cfg={indicators.find((i) => i.id === settingsId)!}
          onChange={(next) =>
            setIndicators((list) => list.map((i) => (i.id === next.id ? next : i)))
          }
          onClose={() => setSettingsId(null)}
        />
      ) : null}
      {/* keeps CATALOG referenced for tree-shaken dev builds */}
      <span hidden>{CATALOG.length}</span>
    </div>
  );
}
