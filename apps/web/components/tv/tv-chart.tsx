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
  type ITimeScaleApi,
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
  FREEHAND,
  DEFAULT_WIDTH,
  PRICE_ONLY,
  type Pt,
  SWATCHES,
  makeTimeAxis,
  newId,
  translate,
  vpOf,
} from '../../lib/tv/drawings';
import { dragPositionHandle, isPosition, positionPts } from '../../lib/tv/position';
import { autoStep, buildFootprints, stepLadder } from '../../lib/tv/footprint';
import {
  CATALOG,
  DEFAULT_INDICATORS,
  type IndicatorCfg,
  AVP_VP,
  SVP_DEFAULTS,
  avpOf,
  catalogOf,
  indicatorLabel,
  makeIndicator,
  svpOf,
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
import {
  type CvdCandle,
  anchorKey,
  closingPrintCandles,
  computeCvd,
  nseClosingPrints,
} from '../../lib/tv/cvd';
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
import { NotesPanel } from './notes-panel';
import { WatchlistPanel, type WatchItem } from './watchlist-panel';
import { useCompact } from '../../lib/tv/use-compact';
import { BottomBar, type RangeId } from './bottom-bar';
import { ScreenerPanel } from '../screener/screener-panel';
import type { ScreenerRow } from '../../lib/screener/types';
import {
  bestSetup,
  liveRank,
  pocCandidates,
  POC_DEFAULTS,
  type PocConfig,
} from '../../lib/screener/poc-retest';
import { zoneClock } from '../../lib/screener/relvol';
import { PocSettingsDialog, POC_VIEW_DEFAULTS, type PocView } from './poc-settings';
import { PocScannerPanel } from './poc-scanner-panel';
import type { TradeMarks } from '../../lib/screener/poc-eval';
import { DrawingsPrimitive } from './drawings-primitive';
import { NewsPrimitive } from './news-primitive';
import { foldTrailingTick, intervalSecs } from '../../lib/tv/clean-bars';
import { DeltaPrimitive, type DeltaMark } from './delta-primitive';
import { CvdDivPrimitive, type CvdDivMark } from './cvd-div-primitive';
import {
  CVD_DIV_DEFAULTS,
  type CvdDivergence,
  findCvdDivergences,
  intrabarUsable,
  nearSetup,
} from '../../lib/tv/cvd-divergence';
import { DeltaPanel } from './delta-panel';
import { CvdPanel, type CvdFilter, type CvdItem } from './cvd-panel';
import { FvgPrimitive, type FvgMark } from './fvg-primitive';
import { PocPrimitive } from './poc-primitive';
import { FvgPanel, FVG_VIEW_DEFAULTS, type FvgView } from './fvg-panel';
import { FVG_ATR_LEN, FVG_DEFAULTS, type FvgConfig, findFvgs } from '../../lib/tv/fvg';
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
import {
  PaperOrderDialog,
  DEFAULT_PREFS,
  PaperPanel,
  PaperTicket,
  type DialogInit,
  type OrderRequest,
  type TradingPrefs,
} from './paper-panel';
import { PaperOverlay, type QuickOrder } from './paper-overlay';
import { BrokerPanel } from './broker-panel';
import { TradeDialog } from './trade-dialog';
import {
  cancelOrder as paperCancel,
  closePosition as paperClose,
  DEFAULT_CAPITAL,
  OLD_DEFAULT_CAPITAL,
  equityOf,
  newAccount,
  placeMarket,
  placePending,
  moveOrder as paperMove,
  updateOrder as paperUpdate,
  reversePosition as paperReverse,
  setBracket as paperBracket,
  step as paperStep,
  updateSettings as paperSettings,
  type PaperState,
} from '../../lib/tv/paper';
import { DEFAULT_ROWS } from '../../lib/tv/volume-profile';
import { completedCount, endAfter, nextEnd } from '../../lib/tv/replay';
import { groupByBar } from '../../lib/tv/news-markers';
import type { NewsItem, NewsResponse } from '../../lib/tv/news-types';
import { FootprintView, type FootprintData } from './footprint-series';
import { Icons } from './icons';
import { IndicatorPicker, IndicatorSettings } from './indicator-dialog';
import { LeftToolbar, type Tool, SVP_KEY, ZOOM_IN_KEY, toolForKey } from './left-toolbar';
import { FibSettingsDialog } from './fib-settings';
import { VpSettingsDialog } from './vp-settings';
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
  prints?: { sub: boolean[]; bars: boolean[] },
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
      case 'avp':
        // Drawn by SessionProfilePrimitive / DrawingsPrimitive, not as a series.
        lines = [];
        break;
      case 'cvd': {
        const anchor = cfg.anchor ?? 'session';
        const ohlc = computeCvd(
          bars,
          sub,
          secs,
          (t) => anchorKey(sessionDay(t, timeZone), anchor),
          regular,
          prints,
        );
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
    a >= 1e6
      ? `${(a / 1e6).toFixed(2)}M`
      : a >= 1e3
        ? `${(a / 1e3).toFixed(2)}K`
        : `${Math.round(a)}`;
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
  /** Which right-hand lists are showing, so a click on the chart can pick the matching row. */
  deltaOpen: boolean;
  cvdOpen: boolean;
  fvgOpen: boolean;
}

type Drag =
  | { mode: 'create'; startX: number; startY: number; moved: boolean }
  | {
      mode: 'move';
      id: string;
      pane: string | null;
      orig: Drawing;
      startLogical: number;
      startPrice: number;
      moved: boolean;
    }
  | { mode: 'handle'; id: string; pane: string | null; handle: number; moved: boolean };

export function TvChart() {
  const rootRef = useRef<HTMLDivElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const mainRef = useRef<{ key: string; series: ISeriesApi<SeriesType, Time> } | null>(null);
  const footRef = useRef<ISeriesApi<'Custom', Time> | null>(null);
  const footViewRef = useRef<FootprintView | null>(null);
  const primRef = useRef<DrawingsPrimitive | null>(null);
  /** Drawing layers of the indicator panes, by indicator id; rebuilt with the indicator series. */
  const subPrimsRef = useRef<
    Map<string, { prim: DrawingsPrimitive; series: ISeriesApi<SeriesType, Time> }>
  >(new Map());
  const newsPrimRef = useRef<NewsPrimitive | null>(null);
  const sessionsPrimRef = useRef<SessionsPrimitive | null>(null);
  const svpPrimRef = useRef<SessionProfilePrimitive | null>(null);
  const deltaPrimRef = useRef<DeltaPrimitive | null>(null);
  const fvgPrimRef = useRef<FvgPrimitive | null>(null);
  const pocPrimRef = useRef<PocPrimitive | null>(null);
  /** The POC retest indicator: every recent untested POC on whatever chart is open (the scanner pick has its own). */
  const pocIndPrimRef = useRef<PocPrimitive | null>(null);
  /** CVD divergence layers: one on the price pane, one in each CVD pane (by indicator id). */
  const cvdDivMainRef = useRef<CvdDivPrimitive | null>(null);
  const cvdDivSubRef = useRef<Map<string, CvdDivPrimitive>>(new Map());
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
  const [deltaTip, setDeltaTip] = useState<number | null>(null);
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
  /** Inline label editor for the text tool (replaces window.prompt): position in wrap pixels. */
  const [textEdit, setTextEdit] = useState<{ x: number; y: number; value: string } | null>(null);
  const textCommitRef = useRef<((value: string) => void) | null>(null);
  const textValueRef = useRef('');
  const [posEditId, setPosEditId] = useState<string | null>(null);
  const [fibEditId, setFibEditId] = useState<string | null>(null);
  const [vpEditId, setVpEditId] = useState<string | null>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const [histTick, setHistTick] = useState(0);
  const history = useRef<{ past: Drawing[][]; future: Drawing[][] }>({ past: [], future: [] });

  // dialogs / panes
  const [showSearch, setShowSearch] = useState<false | 'chart' | 'watchlist'>(false);
  const compact = useCompact();
  const [watchOpen, setWatchOpen] = useState(true);
  const [pendingWatch, setPendingWatch] = useState<WatchItem | null>(null);
  const clearPendingWatch = useCallback(() => setPendingWatch(null), []);
  const [screenerOpen, setScreenerOpen] = useState(false);
  useEffect(() => {
    setScreenerOpen(load<boolean>('tv:screenerOpen', false));
  }, []);
  const [pocOpen, setPocOpen] = useState(false);
  useEffect(() => {
    setPocOpen(load<boolean>('tv:pocOpen', false));
  }, []);
  const closePoc = () => {
    setPocOpen(false);
    save('tv:pocOpen', false);
  };
  /** The setup picked in the POC scanner: drawn on the chart while its symbol is shown and the panel is open. */
  const [pocPick, setPocPick] = useState<ScreenerRow | null>(null);
  /** With a pick from the Tracked tab: that setup's fill and exit, drawn over its zone. */
  const [pocTrade, setPocTrade] = useState<TradeMarks | null>(null);
  /** The pick the chart last centred on, so a refresh of the bars does not yank the view back. */
  const pocJumped = useRef<ScreenerRow | null>(null);
  /** First bar's time when it centred, and when the pick was clicked: see the pick effect. */
  const pocJumpedFirst = useRef(0);
  const pocOpenedAt = useRef(0);
  // POC retest indicator: on the chart (legend row) with its own eye, settings and dialog.
  const [pocOn, setPocOn] = useState(false);
  const [pocMarks, setPocMarks] = useState(true);
  const [pocCfg, setPocCfg] = useState<PocConfig>(POC_DEFAULTS);
  const [pocView, setPocView] = useState<PocView>(POC_VIEW_DEFAULTS);
  const [pocSettings, setPocSettings] = useState(false);
  useEffect(() => {
    setPocOn(load<boolean>('tv:pocOn', false));
    setPocMarks(load<boolean>('tv:pocMarks', true));
    setPocCfg({ ...POC_DEFAULTS, ...load<Partial<PocConfig>>('tv:pocCfg', {}) });
    setPocView({ ...POC_VIEW_DEFAULTS, ...load<Partial<PocView>>('tv:pocView', {}) });
  }, []);
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
  // CVD divergence list: docked in the same right slot (one panel open at a time).
  const [cvdOpen, setCvdOpen] = useState(false);
  const [cvdFilter, setCvdFilter] = useState<CvdFilter>('all');
  const [cvdShowFailed, setCvdShowFailed] = useState(true);
  const [cvdPick, setCvdPick] = useState<number | null>(null);
  // One switch that takes every delta- and CVD-divergence tag and line off the chart (the side panels still list them).
  const [hideDivs, setHideDivs] = useState(false);
  // While everything is hidden, the indicators the user brought back one by one (indicator ids, 'delta-div', 'fvg').
  const [revealed, setRevealed] = useState<string[]>([]);
  const hiddenAll = (id: string) => hideDivs && !revealed.includes(id);
  // Fair value gap list: docked in the same right slot (one panel open at a time).
  const [fvgOpen, setFvgOpen] = useState(false);
  // "On chart" (the indicator is listed in the legend and keeps drawing) is separate from its side panel being open.
  const [deltaOn, setDeltaOn] = useState(false);
  const [fvgOn, setFvgOn] = useState(false);
  const deltaActive = deltaOn || deltaOpen;
  const fvgActive = fvgOn || fvgOpen;
  const [fvgCfg, setFvgCfg] = useState<FvgConfig>(FVG_DEFAULTS);
  const [fvgView, setFvgView] = useState<FvgView>(FVG_VIEW_DEFAULTS);
  const [fvgMarks, setFvgMarks] = useState(true);
  const [fvgPick, setFvgPick] = useState<number | null>(null);
  // Text notes: docked at the bottom of the right column, under whichever panel is open.
  const [notesOpen, setNotesOpen] = useState(false);
  useEffect(() => {
    setNotesOpen(load<boolean>('tv:notesOpen', false));
  }, []);
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
    setCvdOpen(load<boolean>('tv:cvdOpen', false));
    setCvdFilter(load<CvdFilter>('tv:cvdFilter', 'all'));
    setCvdShowFailed(load<boolean>('tv:cvdShowFailed', true));
    setHideDivs(load<boolean>('tv:hideDivs', false));
    setFvgOpen(load<boolean>('tv:fvgOpen', false));
    setDeltaOn(load<boolean>('tv:deltaOn', false) || load<boolean>('tv:deltaOpen', false));
    setFvgOn(load<boolean>('tv:fvgOn', false) || load<boolean>('tv:fvgOpen', false));
    setFvgCfg({ ...FVG_DEFAULTS, ...load<Partial<FvgConfig>>('tv:fvgCfg', {}) });
    setFvgView({ ...FVG_VIEW_DEFAULTS, ...load<Partial<FvgView>>('tv:fvgView', {}) });
    setFvgMarks(load<boolean>('tv:fvgMarks', true));
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
      !deltaActive ||
      first == null ||
      older.key !== `${symbol}|${tf.source}` ||
      !raw.key.startsWith(`${symbol}|${tf.source}|`)
    ) {
      return raw.bars;
    }
    const before = older.bars.filter((b) => b.time < first);
    return before.length ? [...before, ...raw.bars] : raw.bars;
  }, [raw.bars, raw.key, older, deltaActive, symbol, tf.source]);
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
      : (deltaActive || cvdOn) && deltaSub.symbol === symbol && deltaSub.bars.length > subRaw.length
        ? deltaSub.bars
        : subRaw;
  // 1-minute candles for volume profiles (never past the replay cut-off); none when the chart already is 1-minute.
  const profileFine = useMemo(() => {
    if (tf.secs <= 60 || !subBars.length) return null;
    return replayEnd == null ? subBars : subBars.filter((b) => b.time < replayEnd);
  }, [subBars, tf.secs, replayEnd]);
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
  // Fair value gaps on the chart's own candles, shown while the panel is open.
  const fvgAll = useMemo(
    () =>
      fvgActive
        ? findFvgs(bars, fvgCfg, fvgCfg.filter === 'atr' ? atr(bars, FVG_ATR_LEN) : [])
        : [],
    [fvgActive, bars, fvgCfg],
  );
  const fvgShown = useMemo(
    () =>
      fvgAll.filter(
        (g) =>
          (fvgView.filter === 'all' || (fvgView.filter === 'bullish') === g.bullish) &&
          (fvgView.invalidated || g.status !== 'invalidated') &&
          (g.against == null || g.against <= fvgView.maxAgainst),
      ),
    [fvgAll, fvgView],
  );
  const footprints = useMemo(
    () => (fp.on ? buildFootprints(bars, subBars, tf.secs, effectiveStep) : []),
    [fp.on, bars, subBars, tf.secs, effectiveStep],
  );
  // US stocks: Yahoo reports no volume before 09:30 or after 16:00 (bar for bar, apart from the closing cross
  // stamped 16:00), so CVD only counts regular hours there.
  const regularHours = useMemo(() => {
    const def =
      meta &&
      hasExtendedBars({ symbol, instrumentType: meta.instrumentType, timezone: meta.timezone })
        ? sessionFor({ symbol, instrumentType: meta.instrumentType, timezone: meta.timezone })
        : null;
    if (!def) return undefined;
    return (t: number) => {
      const minute = Math.floor((wallClock(t, timeZone) % 86400) / 60);
      return minute >= def.start && minute < def.end;
    };
  }, [meta, symbol, timeZone]);
  // NSE: Yahoo's last bar of the day is often one flat print at the closing price, not trading (see cvd.ts).
  const closingPrints = useMemo(() => {
    if (timeZone !== 'Asia/Kolkata') return undefined;
    const minuteOf = (t: number) => Math.floor((wallClock(t, timeZone) % 86400) / 60);
    const dayOf = (t: number) => sessionDay(t, timeZone);
    const sub = nseClosingPrints(subBars, minuteOf, dayOf);
    return { sub, bars: closingPrintCandles(bars, subBars, sub, tf.secs, minuteOf, dayOf) };
  }, [bars, subBars, tf.secs, timeZone]);
  const computed = useMemo(
    () =>
      computeIndicators(indicators, bars, timeZone, subBars, tf.secs, regularHours, closingPrints),
    [indicators, bars, timeZone, subBars, tf.secs, regularHours, closingPrints],
  );
  // Price/CVD divergences of each CVD indicator, with the delta-divergence setups they sit next to.
  const cvdDivs = useMemo(() => {
    const out = new Map<string, { div: CvdDivergence; near: boolean }[]>();
    const setupRefs = divergences.map((d) => ({
      chartIndex: d.chartIndex,
      confirmChartIndex: d.confirmChartIndex,
      kind: d.kind,
    }));
    for (const c of computed) {
      if (c.cfg.type !== 'cvd' || (c.cfg.divMode ?? 'regular') === 'off') continue;
      const ohlc = c.lines[0]?.ohlc;
      if (!ohlc || ohlc.length !== bars.length) continue;
      const anchor = c.cfg.anchor ?? 'session';
      const keys = bars.map((b) => anchorKey(sessionDay(b.time, timeZone), anchor));
      const found = findCvdDivergences(
        bars,
        ohlc,
        keys,
        intrabarUsable(bars, subBars, tf.secs),
        {
          ...CVD_DIV_DEFAULTS,
          pivot: c.cfg.divPivot ?? CVD_DIV_DEFAULTS.pivot,
          maxGap: c.cfg.divGap ?? CVD_DIV_DEFAULTS.maxGap,
          hidden: c.cfg.divMode === 'all',
        },
        closingPrints?.bars,
      );
      out.set(
        c.cfg.id,
        found.map((div) => ({ div, near: nearSetup(div, setupRefs, 3) != null })),
      );
    }
    return out;
  }, [computed, bars, subBars, tf.secs, timeZone, divergences, closingPrints]);
  // The list's rows (and what the chart draws): the divergences with their times, minus failed ones when hidden.
  const cvdItems = useMemo<CvdItem[]>(() => {
    const out: CvdItem[] = [];
    for (const [id, list] of cvdDivs) {
      for (const { div, near } of list) {
        if (!cvdShowFailed && div.status === 'failed') continue;
        const t = bars[div.i2]?.time;
        if (t == null) continue;
        const ct = div.confirmIndex == null ? null : bars[div.confirmIndex]?.time;
        out.push({
          key: `${id}:${div.kind}:${div.i1}:${div.i2}`,
          div,
          near,
          time: t,
          confirmTime: ct == null ? null : ct + tf.secs,
        });
      }
    }
    return out;
  }, [cvdDivs, cvdShowFailed, bars, tf.secs]);
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

  const listOpen = {
    deltaOpen: deltaOpen && !compact,
    cvdOpen: cvdOpen && !deltaOpen && !screenerOpen && !compact,
    fvgOpen: fvgOpen && !screenerOpen && !compact,
  };
  const latest = useRef<Latest>({
    tool,
    magnet,
    keep,
    lockAll,
    drawings,
    selectedId,
    bars,
    ...listOpen,
  });
  latest.current = { tool, magnet, keep, lockAll, drawings, selectedId, bars, ...listOpen };
  const symbolRef = useRef(symbol);
  symbolRef.current = symbol;

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

  // Anchored profiles used to be saved as drawings; they are indicators now, so move any found into the indicator list.
  const migratedAvp = useRef(new Set<string>());
  useEffect(() => {
    if (!drawState.symbol || drawState.symbol !== symbol) return;
    const old = drawState.list.filter((d) => d.kind === 'avprofile' && !migratedAvp.current.has(d.id));
    if (!old.length) return;
    old.forEach((d) => migratedAvp.current.add(d.id));
    setDrawState((cur) => ({ symbol: cur.symbol, list: cur.list.filter((d) => d.kind !== 'avprofile') }));
    setIndicators((list) => {
      let next = list;
      for (const d of old)
        next = [
          ...next,
          {
            ...makeIndicator('avp', next),
            length: d.rows ?? DEFAULT_ROWS,
            vp: d.vp,
            anchors: { [symbol]: d.pts[0]!.t },
          },
        ];
      return next;
    });
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
    async (
      sym: string,
      interval: string,
      from: number,
      to: number,
      signal?: AbortSignal,
      prepost = false,
    ) => {
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

  // Footprint needs 1-minute bars underneath coarser candles; so do volume profiles, the POC retest's included.
  const svpOn =
    indicators.some((i) => i.type === 'svp' || i.type === 'avp') ||
    drawings.some((d) => d.kind === 'vprofile' || d.kind === 'avprofile');
  const pocFits = tf.intraday && tf.secs <= 900;
  const pocFine = pocOn && pocFits;
  const fineOn = fp.on || svpOn || cvdOn || pocFine;
  useEffect(() => {
    if (!ready || (!fp.on && !svpOn && !cvdOn && !pocFine) || tf.source === '1m') return;
    const ctl = new AbortController();
    fetchBars(symbol, '1m', '7d', ctl.signal, wantPrepost)
      .then((res) => setSubRaw(res.bars))
      .catch(() => setSubRaw([]));
    return () => ctl.abort();
  }, [ready, fp.on, svpOn, cvdOn, pocFine, symbol, tf.source, wantPrepost, fetchBars]);

  // Delta divergence reads 1-minute bars further back than the footprint does. NSE stocks: Upstox's exchange
  // candles, as far back as the view needs (3 months at least). Everything else: Yahoo serves them ~4 weeks back,
  // 7 days per request, so four date windows are stitched together, with 5-minute bars (60 days) behind them.
  const nse = symbol.endsWith('.NS');
  const deltaDays = deltaDepthDays(deltaView, Math.floor(Date.now() / 1000));
  useEffect(() => {
    if (!ready || (!deltaActive && !cvdOn)) return;
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
      const mid = deltaActive
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
  }, [
    ready,
    deltaActive,
    cvdOn,
    symbol,
    nse,
    deltaDays,
    wantPrepost,
    fetchWindow,
    fetchBars,
    fetchUpstox,
  ]);

  // The chart's own bars only go back as far as Yahoo's intraday history (60 days for 5m/15m/30m, 7 days for
  // 1m), so older setups would have nowhere to be drawn: fill in the earlier bars from Upstox.
  const rawFirst = raw.bars[0]?.time;
  useEffect(() => {
    const upstoxInterval = ['1m', '5m', '15m', '30m'].includes(tf.source);
    if (!ready || !deltaActive || !nse || !upstoxInterval || rawFirst == null) return;
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
  }, [ready, deltaActive, nse, symbol, tf.source, deltaDays, rawFirst, raw.key, fetchUpstox]);

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
          // The 1-minute bars under the volume profiles / footprint go stale otherwise: new volume would not reach them.
          if (tf.intraday && tf.source !== '1m' && fineOn) {
            const fineRes = await fetchBars(symbol, '1m', '1d', undefined, wantPrepost);
            if (stopped) return;
            setSubRaw((cur) => (cur.length ? mergeBars(cur, fineRes.bars) : cur));
          }
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
  }, [ready, replayEnd, symbol, tf.source, tf.intraday, wantPrepost, fetchBars, fineOn]);

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
    fvgPrimRef.current = new FvgPrimitive();
    pocPrimRef.current = new PocPrimitive();
    pocIndPrimRef.current = new PocPrimitive();
    cvdDivMainRef.current = new CvdDivPrimitive(true);
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
      subPrimsRef.current = new Map();
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
      if (fvgPrimRef.current) series.attachPrimitive(fvgPrimRef.current);
      if (pocIndPrimRef.current) series.attachPrimitive(pocIndPrimRef.current);
      if (pocPrimRef.current) series.attachPrimitive(pocPrimRef.current);
      if (cvdDivMainRef.current) series.attachPrimitive(cvdDivMainRef.current);
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
    const keptRange =
      viewKeyRef.current === viewKey ? chart.timeScale().getVisibleLogicalRange() : null;
    const prevFirst = firstWallRef.current;
    firstWallRef.current = { key: viewKey, time: wall[0]! };
    main.series.setData(data as never);
    if (keptRange && prevFirst && prevFirst.key === viewKey && wall[0]! < prevFirst.time) {
      const shift = wall.findIndex((t) => t >= prevFirst.time);
      if (shift > 0) {
        chart
          .timeScale()
          .setVisibleLogicalRange({ from: keptRange.from + shift, to: keptRange.to + shift });
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
  const structKey = JSON.stringify(
    indicators.map((cfg) => ({ ...cfg, hidden: undefined, collapsed: undefined, anchors: undefined })),
  );
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
    // Each indicator pane gets its own drawing layer, attached to one of its series so it reads that pane's scale.
    const subs: typeof subPrimsRef.current = new Map();
    for (const c of computed) {
      if (c.pane <= 0) continue;
      const host = entries.find((e) => e.cfgId === c.cfg.id);
      if (!host) continue;
      const prim = new DrawingsPrimitive(c.cfg.id);
      host.series.attachPrimitive(prim);
      subs.set(c.cfg.id, { prim, series: host.series });
    }
    subPrimsRef.current = subs;
    // CVD panes also get the divergence layer (drawn in CVD terms), fed by the effect further down.
    const divSubs = new Map<string, CvdDivPrimitive>();
    for (const c of computed) {
      if (c.cfg.type !== 'cvd') continue;
      const host = entries.find((e) => e.cfgId === c.cfg.id);
      if (!host) continue;
      const prim = new CvdDivPrimitive(false);
      host.series.attachPrimitive(prim);
      divSubs.set(c.cfg.id, prim);
    }
    cvdDivSubRef.current = divSubs;
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
      ? [0, ...computed.map((c) => c.pane)].filter(
          (i, k, all) => i !== maxPane && all.indexOf(i) === k,
        )
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
      entry.series.applyOptions({
        visible: !cfg?.hidden && !(hideDivs && !revealed.includes(entry.cfgId)),
      });
    }
  }, [indicators, structKey, hideDivs, revealed]);

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
    if (
      !cfg ||
      cfg.hidden ||
      (hideDivs && !revealed.includes(cfg.id)) ||
      !tf.intraday ||
      !bars.length
    ) {
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
    const fine = profileFine;
    prim.setState(bars, sessions, {
      rows: cfg.length,
      vp: svpOf(cfg),
      decimals,
      secs: tf.secs,
      fine: fine && fine.length ? fine : null,
    });
  }, [
    indicators,
    hideDivs,
    revealed,
    tf.intraday,
    tf.secs,
    bars,
    profileFine,
    chartType,
    fp.on,
    decimals,
  ]);

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
      deltaMarks && !hiddenAll('delta-div') ? divergences.map(deltaMark) : [],
      fp.on && fp.showDelta,
    );
  }, [
    divergences,
    deltaMarks,
    hideDivs,
    revealed,
    deltaMark,
    bars,
    chartType,
    fp.on,
    fp.showDelta,
  ]);
  // Fair value gaps → zones on the price pane (hidden while the panel's "Mark on chart" is off or it is closed).
  useEffect(() => {
    const marks: FvgMark[] =
      fvgActive && fvgMarks && !hiddenAll('fvg')
        ? fvgShown.map((g) => ({
            i1: g.index - 2,
            i2: g.index,
            endIndex: g.invalidIndex,
            top: g.top,
            bottom: g.bottom,
            bullish: g.bullish,
            tested: g.status !== 'open',
            volume: g.volume,
            against: g.against,
            focus: g.index === fvgPick,
          }))
        : [];
    fvgPrimRef.current?.setState(marks);
  }, [fvgShown, fvgActive, fvgMarks, hideDivs, revealed, bars, chartType, fvgPick]);
  useEffect(() => setFvgPick(null), [symbol, tfId]);
  // POC scanner pick → its zone on the price pane; the first time its bars are in, centre on it and pulse.
  // It ignores "hide all indicators": the pick is an explicit request and has no legend row to reveal it from.
  useEffect(() => {
    const p = pocPick;
    const shown =
      p &&
      pocOpen &&
      bars.length > 0 &&
      meta?.symbol?.toUpperCase() === p.symbol.toUpperCase() &&
      p.pocFrom != null &&
      p.pocZoneLo != null &&
      p.pocZoneHi != null &&
      p.pocPrice != null;
    if (!shown) {
      pocPrimRef.current?.setState(null);
      pocPrimRef.current?.setTrade(null);
      return;
    }
    // the tracked trade: fill and exit on the candles that contain them (the last one starting at or before)
    const upTo = (t: number) => {
      let i = -1;
      for (let k = 0; k < bars.length && bars[k]!.time <= t; k += 1) i = k;
      return i < 0 ? null : i;
    };
    const clockOf = (t: number) =>
      new Date(t * 1000).toLocaleString('en-GB', {
        timeZone,
        day: 'numeric',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      });
    const tr = pocTrade;
    const fillIndex = tr ? upTo(tr.fillTime) : null;
    const tradeMark =
      tr && fillIndex != null
        ? {
            long: tr.long,
            outcome: tr.outcome,
            fillIndex,
            fillPrice: tr.fillPrice,
            exitIndex: tr.exitTime == null ? bars.length - 1 : (upTo(tr.exitTime) ?? fillIndex),
            exitPrice: tr.exitPrice,
            stop: tr.stop,
            target: tr.target,
            r: tr.r,
            fillAt: clockOf(tr.fillTime),
            exitAt: tr.exitTime == null ? null : clockOf(tr.exitTime),
          }
        : null;
    pocPrimRef.current?.setTrade(tradeMark);
    const at = (t: number | null | undefined) => {
      if (t == null) return null;
      const i = bars.findIndex((b) => b.time >= t);
      return i < 0 ? null : i;
    };
    const fromIndex = at(p.pocFrom);
    if (fromIndex == null) {
      pocPrimRef.current?.setState(null);
      return;
    }
    const long = p.pocStage?.startsWith('Long')
      ? true
      : p.pocStage?.startsWith('Short')
        ? false
        : null;
    const departIndex = at(p.pocDepartAt);
    const touchIndex = at(p.pocTouchAt);
    const extremeIndex = at(p.pocExtremeAt);
    pocPrimRef.current?.setState({
      fromIndex,
      departIndex,
      touchIndex,
      lastIndex: bars.length - 1,
      zoneLo: p.pocZoneLo!,
      zoneHi: p.pocZoneHi!,
      poc: p.pocPrice!,
      entry: p.pocEntry ?? null,
      stop: p.pocStop ?? null,
      extremeIndex,
      extreme: p.pocExtreme ?? null,
      sessionsAgo: p.pocSessionsAgo ?? null,
      long,
    });
    // Older candles can arrive a few seconds after the stock loads (prepended, so every bar index shifts): soon
    // after the click, centre again on them; later, a user who has scrolled away is left alone.
    const firstTime = bars[0]?.time ?? 0;
    const shifted =
      pocJumpedFirst.current !== firstTime && Date.now() - pocOpenedAt.current < 20_000;
    if (pocJumped.current === p && !shifted) return;
    const ts = chartRef.current?.timeScale();
    const range = ts?.getVisibleLogicalRange();
    if (!ts || !range) return;
    pocJumped.current = p;
    pocJumpedFirst.current = firstTime;
    const last = bars.length - 1;
    if (tradeMark) {
      // a tracked trade: the trade itself, with some candles before the fill and after the exit
      showRange(
        ts,
        tradeMark.fillIndex - 30,
        Math.min(last + 6, Math.max(tradeMark.exitIndex, tradeMark.fillIndex) + 60),
      );
    } else {
      // show the whole story: the profile's session through the last bar, with a little room on the right
      const span = Math.max(range.to - range.from, last - fromIndex + 10);
      showRange(ts, last + 6 - span, last + 6);
    }
    pocPrimRef.current?.pulse();
  }, [pocPick, pocTrade, pocOpen, bars, meta, chartType, timeZone]);
  // POC retest indicator: the same detection as the scanner, run on this chart's regular-hours bars. A POC built
  // from candles longer than 15 minutes is too coarse, so it draws nothing there (the legend says so).
  const pocLevels = useMemo(() => {
    if (!pocOn || !pocFits || bars.length < 2) return [];
    const regular = regularHours ? bars.filter((b) => regularHours(b.time)) : bars;
    // the session profile's own 1-minute bars, so the POC here is the one the SVP draws
    const fine =
      profileFine && regularHours ? profileFine.filter((b) => regularHours(b.time)) : profileFine;
    return pocCandidates(regular, zoneClock(timeZone), null, pocCfg, fine);
  }, [pocOn, pocFits, bars, regularHours, timeZone, pocCfg, profileFine]);
  useEffect(() => {
    const prim = pocIndPrimRef.current;
    if (!prim) return;
    if (!pocOn || !pocMarks || hiddenAll('poc') || !pocLevels.length) {
      prim.setMarks([]);
      return;
    }
    const index = new Map(bars.map((b, i) => [b.time, i]));
    const at = (t: number | null) => (t == null ? null : (index.get(t) ?? null));
    // the scanner's pick of this symbol is drawn by its own layer: leave that zone to it
    const pickFrom =
      pocOpen && pocPick && meta?.symbol?.toUpperCase() === pocPick.symbol.toUpperCase()
        ? pocPick.pocFrom
        : null;
    const best = bestSetup(pocLevels);
    const marks = pocLevels.flatMap((p) => {
      const live = liveRank(p.stage) != null;
      if (!live && !(pocView.showTested && p.touchTime != null)) return [];
      if (p.fromTime === pickFrom) return [];
      const fromIndex = at(p.fromTime);
      if (fromIndex == null) return [];
      const long = p.stage.startsWith('Long')
        ? true
        : p.stage.startsWith('Short')
          ? false
          : p.entry == null
            ? null
            : p.entry > (p.stop ?? p.entry);
      return [
        {
          fromIndex,
          departIndex: at(p.departTime),
          touchIndex: at(p.touchTime),
          lastIndex: bars.length - 1,
          zoneLo: p.zoneLo,
          zoneHi: p.zoneHi,
          poc: p.poc,
          entry: p.entry,
          stop: p.stop,
          extremeIndex: at(p.extremeTime),
          extreme: p.extreme,
          sessionsAgo: p.sessionsAgo,
          long,
          // with a scanner pick on the chart, that pick is the one full setup: the rest stay quiet levels
          primary: p === best && pickFrom == null,
          stage: live ? (p.stage.split(' · ')[1] ?? p.stage) : p.stage.toLowerCase(),
          // a used-up POC only runs to where price came back to it
          endIndex: live ? null : at(p.touchTime),
        },
      ];
    });
    prim.setMarks(marks);
  }, [
    pocLevels,
    pocOn,
    pocMarks,
    pocView,
    hideDivs,
    revealed,
    bars,
    chartType,
    pocOpen,
    pocPick,
    meta,
  ]);
  useEffect(() => setDeltaPick(null), [symbol, tfId]);
  useEffect(() => setDeltaTip(null), [symbol, tfId]);
  useEffect(() => setCvdPick(null), [symbol, tfId]);

  // Price/CVD divergences → a layer in each CVD pane (CVD values) and on the price pane (prices).
  useEffect(() => {
    const onPrice: CvdDivMark[] = [];
    for (const c of computed) {
      if (c.cfg.type !== 'cvd') continue;
      const list =
        c.cfg.hidden || (hideDivs && !revealed.includes(c.cfg.id))
          ? []
          : (cvdDivs.get(c.cfg.id) ?? []).filter((x) => cvdShowFailed || x.div.status !== 'failed');
      const mark = (
        x: { div: CvdDivergence; near: boolean },
        i1: number,
        i2: number,
        v1: number,
        v2: number,
      ): CvdDivMark => ({
        i1,
        i2,
        v1,
        v2,
        bullish: x.div.bullish,
        hidden: x.div.hidden,
        status: x.div.status,
        confirmIndex: x.div.confirmIndex,
        level: x.div.level,
        near: x.near,
        focus: x.div.i2 === cvdPick,
      });
      cvdDivSubRef.current
        .get(c.cfg.id)
        ?.setState(list.map((x) => mark(x, x.div.ci1, x.div.ci2, x.div.c1, x.div.c2)));
      onPrice.push(...list.map((x) => mark(x, x.div.i1, x.div.i2, x.div.p1, x.div.p2)));
    }
    cvdDivMainRef.current?.setState(onPrice);
  }, [cvdDivs, computed, chartType, structKey, cvdShowFailed, cvdPick, hideDivs, revealed]);

  // Drawings → primitive.
  useEffect(() => {
    // Anchored volume profile indicators are drawn as `avprofile` drawings (id `avp:<indicator id>`) fed only to the primitive.
    const last = bars[bars.length - 1];
    const sessionStart = (() => {
      if (!bars.length) return 0;
      if (!tf.intraday) return Math.max(0, bars.length - 20);
      const br = sessionBreaks(bars.map((b) => b.time), tf.secs);
      return br[br.length - 1] ?? 0;
    })();
    const anchored: Drawing[] =
      last == null
        ? []
        : indicators
            .filter((i) => i.type === 'avp' && !i.hidden)
            .map((i) => ({
              id: `avp:${i.id}`,
              kind: 'avprofile' as const,
              pts: [{ t: i.anchors?.[symbol] ?? bars[sessionStart]!.time, p: last.close }],
              color: i.color,
              width: 2,
              rows: i.length,
              vp: avpOf(i),
            }));
    const all = [...drawings, ...anchored, ...(measure ? [measure] : [])];
    const next = {
      drawings: all,
      draft,
      selectedId,
      hidden: hideAll,
      axis,
      decimals,
      bars,
      fine: profileFine,
      secs: tf.secs,
    };
    primRef.current?.setState(next);
    for (const { prim } of subPrimsRef.current.values()) prim.setState(next);
  }, [
    drawings,
    measure,
    draft,
    selectedId,
    hideAll,
    axis,
    decimals,
    bars,
    profileFine,
    tf.secs,
    tf.intraday,
    structKey,
    indicators,
    symbol,
  ]);

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
    /** A pane's plot area in wrap coordinates: `key` is null for the price pane, else the indicator's id. */
    type Loc = { key: string | null; top: number; w: number; h: number };
    const seriesFor = (key: string | null) =>
      key == null ? mainRef.current?.series : subPrimsRef.current.get(key)?.series;
    const primFor = (key: string | null) =>
      key == null ? primRef.current : subPrimsRef.current.get(key)?.prim;
    const paneLoc = (key: string | null): Loc | null => {
      const chart = chartRef.current;
      const series = seriesFor(key);
      if (!chart || !series) return null;
      const index = series.getPane().paneIndex();
      const el = chart.panes()[index]?.getHTMLElement();
      const size = chart.paneSize(index);
      const top = el ? el.getBoundingClientRect().top - wrap.getBoundingClientRect().top : 0;
      return { key, top, w: size.width, h: size.height };
    };
    const locate = (x: number, y: number): Loc | null => {
      for (const key of [null, ...subPrimsRef.current.keys()]) {
        const loc = paneLoc(key);
        if (loc && x >= 0 && x <= loc.w && y >= loc.top && y <= loc.top + loc.h) return loc;
      }
      return null;
    };
    const toPt = (x: number, y: number, snap: boolean, loc: Loc): Pt | null => {
      const chart = chartRef.current;
      const series = seriesFor(loc.key);
      if (!chart || !series) return null;
      const yl = y - loc.top;
      const logical = chart.timeScale().coordinateToLogical(x);
      const raw = series.coordinateToPrice(yl);
      if (logical == null || raw == null) return null;
      let price: number = raw;
      let t = axis.toTime(logical);
      if (snap && latest.current.magnet) {
        const idx = Math.max(0, Math.min(latest.current.bars.length - 1, Math.round(logical)));
        const b = latest.current.bars[idx];
        if (b) {
          if (loc.key != null) {
            // An indicator pane has no candle prices to snap to, only the candle's time.
            t = b.time;
          } else {
            let best: { dy: number; p: number } = { dy: Infinity, p: price };
            for (const v of [b.open, b.high, b.low, b.close]) {
              const vy = series.priceToCoordinate(v);
              if (vy != null && Math.abs(vy - yl) < best.dy) best = { dy: Math.abs(vy - yl), p: v };
            }
            if (best.dy < 30) {
              price = best.p;
              t = b.time;
            }
          }
        }
      }
      return { t, p: price };
    };
    /** A freehand stroke grows by one point per move; every other drawing just moves its second anchor. */
    const nextPts = (d: Drawing, pt: Pt): Pt[] => {
      if (!FREEHAND.has(d.kind)) return [d.pts[0]!, pt];
      const last = d.pts[d.pts.length - 1]!;
      return last.t === pt.t && last.p === pt.p ? d.pts : [...d.pts, pt];
    };
    const finishDraft = () => {
      const d = draftRef;
      draftRef = null;
      setDraft(null);
      if (!d) return;
      if (d.kind === 'measure') {
        setMeasure(d);
      } else if (d.kind === 'avprofile') {
        // The anchored profile is an indicator (legend, settings, hide, remove); the tool just sets its anchor.
        const at = d.pts[0]!.t;
        const sym = symbolRef.current;
        setIndicators((list) => [...list, { ...makeIndicator('avp', list), anchors: { [sym]: at } }]);
        setSelectedId(null);
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
      if (textCommitRef.current) {
        // mousedown below is preventDefault-ed, so the open text editor never blurs: place it now.
        const commit = textCommitRef.current;
        textCommitRef.current = null;
        if (textValueRef.current.trim()) commit(textValueRef.current);
        setTextEdit(null);
        if (latest.current.tool === 'text') return;
      }
      const { x, y } = local(e);
      const here = locate(x, y);
      if (!here) return;
      const L = latest.current;

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

      if (L.tool === 'cursor' && here.key == null) {
        const dBar = deltaPrimRef.current?.findBar(x, y) ?? null;
        if (dBar != null) {
          e.stopImmediatePropagation();
          e.preventDefault();
          setDeltaTip((cur) => (cur === dBar ? null : dBar));
          // with the list open, point at the badge's row too
          if (L.deltaOpen) setDeltaPick(dBar);
          return;
        }
        setDeltaTip(null);
        // a click on a fair value gap or a CVD divergence line picks its row (the click still goes on to the chart)
        if (L.fvgOpen) {
          const g = fvgPrimRef.current?.findAt(x, y) ?? null;
          if (g != null) {
            setFvgPick(g);
            fvgPrimRef.current?.pulse(g);
          }
        }
        if (L.cvdOpen) {
          const c = cvdDivMainRef.current?.findAt(x, y) ?? null;
          if (c != null) {
            setCvdPick(c);
            cvdDivMainRef.current?.pulse(c);
            for (const prim of cvdDivSubRef.current.values()) prim.pulse(c);
          }
        }
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
        const kind = L.tool as DrawingKind;
        // A second click stays in the pane the drawing started in, wherever the cursor has wandered.
        const loc = (draftRef ? paneLoc(draftRef.pane ?? null) : null) ?? here;
        if (!draftRef && loc.key != null && PRICE_ONLY.has(kind)) return;
        const pt = toPt(x, y, true, loc);
        if (!pt) return;
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
          const above = main?.coordinateToPrice(y - loc.top - 55);
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
          width: DEFAULT_WIDTH[kind] ?? 2,
          text: kind === 'text' ? 'Text' : undefined,
          rows: kind === 'vprofile' || kind === 'avprofile' ? DEFAULT_ROWS : undefined,
          pane: loc.key ?? undefined,
        };
        if (kind === 'text') {
          textCommitRef.current = (value) => {
            d.text = value.trim() || 'Text';
            draftRef = d;
            finishDraft();
          };
          textValueRef.current = '';
          setTextEdit({ x, y, value: '' });
          return;
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
      const hit = primFor(here.key)?.findHit(x, y - here.top) ?? null;
      if (!hit) {
        setSelectedId(null);
        return;
      }
      e.stopImmediatePropagation();
      e.preventDefault();
      setSelectedId(hit.drawingId);
      if (hit.drawingId.startsWith('avp:')) {
        // An anchored profile indicator: its left line moves the anchor; the body only selects.
        if (hit.handle !== 'body' && !L.lockAll)
          drag = { mode: 'handle', id: hit.drawingId, pane: here.key, handle: 0, moved: false };
        return;
      }
      const d = L.drawings.find((v) => v.id === hit.drawingId);
      if (!d || d.locked || L.lockAll) return;
      const chart = chartRef.current;
      const paneSeries = seriesFor(here.key);
      if (!chart || !paneSeries) return;
      if (hit.handle === 'body') {
        const logical = chart.timeScale().coordinateToLogical(x);
        const price = paneSeries.coordinateToPrice(y - here.top);
        if (logical == null || price == null) return;
        drag = {
          mode: 'move',
          id: d.id,
          pane: here.key,
          orig: d,
          startLogical: logical,
          startPrice: price,
          moved: false,
        };
      } else {
        drag = { mode: 'handle', id: d.id, pane: here.key, handle: hit.handle, moved: false };
      }
    };

    const onMove = (e: MouseEvent) => {
      const { x, y } = local(e);
      if (draftRef) {
        const dloc = paneLoc(draftRef.pane ?? null);
        const pt = dloc ? toPt(x, y, !FREEHAND.has(draftRef.kind), dloc) : null;
        if (pt) {
          draftRef = { ...draftRef, pts: nextPts(draftRef, pt) };
          setDraft(draftRef);
        }
        if (drag?.mode === 'create' && Math.hypot(x - drag.startX, y - drag.startY) > 6)
          drag.moved = true;
        return;
      }
      if (drag && (drag.mode === 'move' || drag.mode === 'handle')) {
        const chart = chartRef.current;
        const dloc = paneLoc(drag.pane);
        const paneSeries = seriesFor(drag.pane);
        if (!chart || !dloc || !paneSeries) return;
        drag.moved = true;
        if (drag.mode === 'move') {
          const logical = chart.timeScale().coordinateToLogical(x);
          const price = paneSeries.coordinateToPrice(y - dloc.top);
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
          const pt = toPt(x, y, true, dloc);
          if (!pt) return;
          const { id, handle } = drag;
          if (id.startsWith('avp:')) {
            const sym = symbolRef.current;
            setIndicators((list) =>
              list.map((i) =>
                `avp:${i.id}` === id ? { ...i, anchors: { ...i.anchors, [sym]: pt.t } } : i,
              ),
            );
            return;
          }
          setDrawState((cur) => ({
            symbol: cur.symbol,
            list: cur.list.map((v) => {
              if (v.id !== id) return v;
              if (isPosition(v.kind)) return dragPositionHandle(v, handle, pt);
              // A volume profile's lines only move in time; its price anchors are irrelevant.
              const pts = v.pts.map((q, i) =>
                i === handle ? (v.kind === 'vprofile' || v.kind === 'avprofile' ? { t: pt.t, p: q.p } : pt) : q,
              );
              return { ...v, pts };
            }),
          }));
        }
        return;
      }
      if (latest.current.tool === 'cursor') {
        const here = locate(x, y);
        const bar = here?.key === null ? (newsPrimRef.current?.findBar(x, y) ?? null) : null;
        setNewsHover(bar);
        const hit =
          here && bar == null ? (primFor(here.key)?.findHit(x, y - here.top) ?? null) : null;
        const layers: [string | null, DrawingsPrimitive | null][] = [
          [null, primRef.current],
          ...[...subPrimsRef.current].map(([k, v]): [string, DrawingsPrimitive] => [k, v.prim]),
        ];
        for (const [key, p] of layers) {
          const next = hit && here?.key === key ? hit.drawingId : null;
          if (p && p.hoverId !== next) p.setState({ hoverId: next });
        }
        wrap.classList.toggle('tv-over-drawing', !!hit || bar != null);
      }
    };

    const onUp = (e: MouseEvent) => {
      if (draftRef && drag?.mode === 'create') {
        if (drag.moved) {
          const { x, y } = local(e);
          const uloc = paneLoc(draftRef.pane ?? null);
          const pt = uloc ? toPt(x, y, !FREEHAND.has(draftRef.kind), uloc) : null;
          if (pt) draftRef = { ...draftRef, pts: nextPts(draftRef, pt) };
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
      const here = locate(x, y);
      const hit = here ? primFor(here.key)?.findHit(x, y - here.top) : null;
      if (!hit) return;
      if (hit.drawingId.startsWith('avp:')) {
        setSelectedId(hit.drawingId);
        setSettingsId(hit.drawingId.slice(4));
        return;
      }
      const d = latest.current.drawings.find((v) => v.id === hit.drawingId);
      if (d && isPosition(d.kind)) {
        setSelectedId(d.id);
        setPosEditId(d.id);
        return;
      }
      if (d?.kind === 'vprofile' || d?.kind === 'avprofile') {
        setSelectedId(d.id);
        setVpEditId(d.id);
        return;
      }
      if (d?.kind === 'fib') {
        setSelectedId(d.id);
        setFibEditId(d.id);
        return;
      }
      if (d?.kind !== 'text') return;
      textCommitRef.current = (value) => {
        const next = value.trim();
        if (!next) return;
        setDrawings(latest.current.drawings.map((v) => (v.id === d.id ? { ...v, text: next } : v)));
      };
      setSelectedId(d.id);
      textValueRef.current = d.text ?? '';
      setTextEdit({ x, y, value: d.text ?? '' });
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
      if (!e.ctrlKey && !e.metaKey && e.shiftKey && e.code === SVP_KEY.code) {
        e.preventDefault();
        setIndicators((list) =>
          list.some((i) => i.type === 'svp')
            ? list.filter((i) => i.type !== 'svp')
            : [...list, makeIndicator('svp', list)],
        );
        return;
      }
      const keyTool = toolForKey(e);
      if (keyTool) {
        e.preventDefault();
        setTool(keyTool);
        setMeasure(null);
        return;
      }
      if (!e.ctrlKey && !e.metaKey && !e.shiftKey && e.code === ZOOM_IN_KEY.code) {
        e.preventDefault();
        zoomIn();
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
        if (id.startsWith('avp:')) {
          setIndicators((list) => list.filter((i) => `avp:${i.id}` !== id));
          setSelectedId(null);
          return;
        }
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
    // The library ignores a vertical drag while the price scale auto-fits; drop auto-fit as soon as a pane drag
    // turns vertical (this runs in the capture phase, before the library starts its scroll).
    let panStart: { x: number; y: number } | null = null;
    const panDown = (e: MouseEvent) => {
      const { x, y } = local(e);
      panStart =
        e.button === 0 && !(e.target as HTMLElement).closest('[data-tv-ui]') && locate(x, y)
          ? { x, y }
          : null;
    };
    const panMove = (e: MouseEvent) => {
      if (!panStart || !(e.buttons & 1) || drag || draftRef || latest.current.tool !== 'cursor')
        return;
      const { x, y } = local(e);
      const dy = Math.abs(y - panStart.y);
      if (dy < 3 || dy < Math.abs(x - panStart.x)) return;
      panStart = null;
      const chart = chartRef.current;
      if (chart?.priceScale('right').options().autoScale) {
        chart.priceScale('right').applyOptions({ autoScale: false });
        setAutoScale(false);
      }
    };
    wrap.addEventListener('mousedown', panDown, true);
    wrap.addEventListener('mousemove', panMove, true);
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
      wrap.removeEventListener('mousedown', panDown, true);
      wrap.removeEventListener('mousemove', panMove, true);
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

  /**
   * Show bars [from, to] (logical indices) for a side-list click. The price scale goes back to auto-fit as well:
   * after a manual price-axis drag it would keep its old range and the bars would sit off-screen vertically.
   */
  const showRange = (ts: ITimeScaleApi<Time>, from: number, to: number) => {
    const chart = chartRef.current;
    if (chart && !chart.priceScale('right').options().autoScale) {
      chart.priceScale('right').applyOptions({ autoScale: true });
      setAutoScale(true);
    }
    ts.setVisibleLogicalRange({ from, to });
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
    showRange(ts, mid - half, mid + half);
    setDeltaPick(index);
    if (d && bars[index]) deltaPrimRef.current?.focus(deltaMark(d));
  };

  /** Centre the chart on a fair value gap, keeping the zoom, and pulse its zone. */
  const jumpToFvg = (index: number) => {
    const ts = chartRef.current?.timeScale();
    const range = ts?.getVisibleLogicalRange();
    if (!ts || !range) return;
    const half = (range.to - range.from) / 2;
    showRange(ts, index - 1 - half, index - 1 + half);
    setFvgPick(index);
    fvgPrimRef.current?.pulse(index);
  };

  /** Centre the chart on a CVD divergence's second swing, keeping the zoom, and highlight it. */
  const jumpToCvd = (index: number) => {
    const ts = chartRef.current?.timeScale();
    const range = ts?.getVisibleLogicalRange();
    if (!ts || !range) return;
    const half = (range.to - range.from) / 2;
    showRange(ts, index - half, index + half);
    setCvdPick(index);
    cvdDivMainRef.current?.pulse(index);
    for (const prim of cvdDivSubRef.current.values()) prim.pulse(index);
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

  // Clicking an eye while everything is hidden: bring back just that one; the rail eye still brings back the rest.
  const revealOnly = (which: string) => {
    setRevealed((list) => [...list, which]);
    setIndicators((list) => list.map((i) => (i.id === which ? { ...i, hidden: false } : i)));
    if (which === 'delta-div') {
      setDeltaMarks(true);
      save('tv:deltaMarks', true);
    }
    if (which === 'fvg') {
      setFvgMarks(true);
      save('tv:fvgMarks', true);
    }
    if (which === 'poc') {
      setPocMarks(true);
      save('tv:pocMarks', true);
    }
  };

  const legendRow = (c: Computed): ReactNode => (
    <div
      key={c.cfg.id}
      className="group pointer-events-auto flex w-fit max-w-full items-center gap-2 text-[12px] leading-5"
      data-tv-ui
    >
      <span className={c.cfg.hidden || hiddenAll(c.cfg.id) ? 'text-[#5d606b]' : 'text-[#b2b5be]'}>
        {indicatorLabel(c.cfg)}
      </span>
      {c.cfg.hidden || hiddenAll(c.cfg.id)
        ? null
        : c.lines.map((l) => {
            const k = l.ohlc?.[idx];
            if (k?.missing) {
              return (
                <span
                  key={l.key}
                  className="text-[#787b86]"
                  title="Yahoo reports no volume outside regular hours"
                >
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
                  color:
                    c.cfg.type === 'volume' ? (bar && bar.close >= bar.open ? UP : DOWN) : l.color,
                }}
              >
                {valueText(c, l)}
              </span>
            );
          })}
      <span
        className={`flex gap-1 transition-opacity group-hover:opacity-100 ${c.cfg.hidden || hiddenAll(c.cfg.id) ? 'opacity-100' : 'opacity-0'}`}
      >
        <button
          type="button"
          title={c.cfg.hidden || hiddenAll(c.cfg.id) ? 'Show' : 'Hide'}
          onClick={() =>
            hiddenAll(c.cfg.id)
              ? revealOnly(c.cfg.id)
              : setIndicators((list) =>
                  list.map((i) => (i.id === c.cfg.id ? { ...i, hidden: !i.hidden } : i)),
                )
          }
          className="text-[#787b86] hover:text-white"
        >
          {c.cfg.hidden || hiddenAll(c.cfg.id) ? (
            <Icons.eyeOff size={14} />
          ) : (
            <Icons.eye size={14} />
          )}
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

  const toggleDelta = () => {
    const next = !deltaOpen;
    setDeltaOpen(next);
    save('tv:deltaOpen', next);
    if (next) {
      setDeltaOn(true);
      save('tv:deltaOn', true);
      setScreenerOpen(false);
      save('tv:screenerOpen', false);
      closePoc();
      setCvdOpen(false);
      save('tv:cvdOpen', false);
      setFvgOpen(false);
      save('tv:fvgOpen', false);
    }
  };
  const toggleFvg = () => {
    const next = !fvgOpen;
    setFvgOpen(next);
    save('tv:fvgOpen', next);
    if (next) {
      setFvgOn(true);
      save('tv:fvgOn', true);
      setScreenerOpen(false);
      save('tv:screenerOpen', false);
      closePoc();
      setDeltaOpen(false);
      save('tv:deltaOpen', false);
      setCvdOpen(false);
      save('tv:cvdOpen', false);
    }
  };

  const panelLegendRow = (
    key: string,
    label: string,
    marksOn: boolean,
    setMarks: (on: boolean) => void,
    marksKey: string,
    panelOpen: boolean,
    openPanel: () => void,
    remove: () => void,
  ): ReactNode => {
    const shown = marksOn && !hiddenAll(key);
    return (
      <div
        key={key}
        className="group pointer-events-auto flex w-fit max-w-full items-center gap-2 text-[12px] leading-5"
        data-tv-ui
      >
        <span className={shown ? 'text-[#b2b5be]' : 'text-[#5d606b]'}>{label}</span>
        <span
          className={`flex gap-1 transition-opacity group-hover:opacity-100 ${shown ? 'opacity-0' : 'opacity-100'}`}
        >
          <button
            type="button"
            title={shown ? 'Hide' : 'Show'}
            onClick={() => {
              if (hiddenAll(key)) return revealOnly(key);
              setMarks(!marksOn);
              save(marksKey, !marksOn);
            }}
            className="text-[#787b86] hover:text-white"
          >
            {shown ? <Icons.eye size={14} /> : <Icons.eyeOff size={14} />}
          </button>
          <button
            type="button"
            title={panelOpen ? 'Panel is open' : 'Settings'}
            onClick={() => {
              if (!panelOpen) openPanel();
            }}
            className="text-[#787b86] hover:text-white"
          >
            <Icons.gear size={14} />
          </button>
          <button
            type="button"
            title="Remove"
            onClick={remove}
            className="text-[#787b86] hover:text-[#f23645]"
          >
            <Icons.close size={14} />
          </button>
        </span>
      </div>
    );
  };
  const panelLegendRows = [
    deltaActive
      ? panelLegendRow(
          'delta-div',
          'Delta Divergence',
          deltaMarks,
          setDeltaMarks,
          'tv:deltaMarks',
          deltaOpen,
          toggleDelta,
          () => {
            setDeltaOn(false);
            save('tv:deltaOn', false);
            if (deltaOpen) toggleDelta();
          },
        )
      : null,
    fvgActive
      ? panelLegendRow(
          'fvg',
          'Fair Value Gap',
          fvgMarks,
          setFvgMarks,
          'tv:fvgMarks',
          fvgOpen,
          toggleFvg,
          () => {
            setFvgOn(false);
            save('tv:fvgOn', false);
            if (fvgOpen) toggleFvg();
          },
        )
      : null,
    pocOn
      ? panelLegendRow(
          'poc',
          pocFits ? 'POC Retest' : 'POC Retest (needs 1–15m)',
          pocMarks,
          setPocMarks,
          'tv:pocMarks',
          false,
          () => setPocSettings(true),
          () => {
            setPocOn(false);
            save('tv:pocOn', false);
          },
        )
      : null,
  ];

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
      {maxPane == null ? panelLegendRows : null}
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

  // ── paper trading ──────────────────────────────────────────────────────
  // Two accounts: the live one persists; the replay one is a sandbox that starts fresh with every replay.
  const [paperOpen, setPaperOpen] = useState(false);
  const [brokerOpen, setBrokerOpen] = useState(false);
  const [brokerSel, setBrokerSel] = useState<string | null>(null);
  const [tradeOpen, setTradeOpen] = useState(false);
  const [livePaper, setLivePaper] = useState<PaperState>(() => newAccount());
  const initialLivePaper = useRef(livePaper);
  const [replayPaper, setReplayPaper] = useState<PaperState>(() => newAccount());
  const paperHydrated = useRef(false);
  useEffect(() => {
    setPaperOpen(load<boolean>('tv:paperOpen', false));
    const saved = load<PaperState | null>('tv:paper', null);
    if (saved && saved.positions && saved.orders && saved.history) {
      const untouched =
        saved.initial === OLD_DEFAULT_CAPITAL &&
        saved.cash === OLD_DEFAULT_CAPITAL &&
        !saved.history.length &&
        !saved.orders.length &&
        !Object.keys(saved.positions).length;
      setLivePaper(
        untouched
          ? newAccount(DEFAULT_CAPITAL, saved.commissionPct)
          : { ...newAccount(), ...saved, log: saved.log ?? [] },
      );
    }
    paperHydrated.current = true;
  }, []);
  useEffect(() => {
    // never write the blank starter account: on mount (twice under StrictMode) it would overwrite the saved one before it is read
    if (paperHydrated.current && livePaper !== initialLivePaper.current)
      save('tv:paper', livePaper);
  }, [livePaper]);
  const paper = replayActive ? replayPaper : livePaper;
  const setPaper = replayActive ? setReplayPaper : setLivePaper;
  const wasReplayPaper = useRef(false);
  useEffect(() => {
    if (replayActive && !wasReplayPaper.current)
      setReplayPaper(newAccount(livePaper.initial, livePaper.commissionPct));
    wasReplayPaper.current = replayActive;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [replayActive]);
  const barsReady = raw.key.startsWith(`${symbol}|${tf.source}|`);
  useEffect(() => {
    if (!barsReady || !bars.length) return;
    setPaper((s) => paperStep(s, symbol, bars, tf.secs));
  }, [bars, barsReady, symbol, tf.secs, setPaper]);
  const lastBar = bars[bars.length - 1];
  const lastPrice = barsReady && lastBar ? lastBar.close : null;
  // a fill happens at the last close, i.e. when that candle ends
  const paperNow = lastBar ? lastBar.time + tf.secs : 0;
  const placeOrder = useCallback(
    (o: OrderRequest): string | null => {
      if (lastPrice == null) return 'No price yet';
      const bracket = { sl: o.sl, tp: o.tp };
      const res =
        o.type === 'market'
          ? placeMarket(paper, symbol, o.side, o.qty, lastPrice, paperNow, bracket)
          : placePending(
              paper,
              symbol,
              o.side,
              o.type,
              o.qty,
              o.price ?? 0,
              lastPrice,
              paperNow,
              bracket,
              o.tif,
            );
      if (res.error) return res.error;
      setPaper(res.state);
      return null;
    },
    [paper, symbol, lastPrice, paperNow, setPaper],
  );
  // TradingView's trading layer: quantity, one-click mode and the order dialog
  const [paperQty, setPaperQty] = useState('1');
  const [prefs, setPrefs] = useState<TradingPrefs>(DEFAULT_PREFS);
  const oneClick = prefs.instant;
  const [orderDlg, setOrderDlg] = useState<DialogInit | null>(null);
  useEffect(() => {
    setPaperQty(load<string>('tv:paperQty', '1'));
    setPrefs({ ...DEFAULT_PREFS, ...load<Partial<TradingPrefs>>('tv:paperPrefs', {}) });
  }, []);
  const updatePrefs = useCallback((next: TradingPrefs) => {
    setPrefs(next);
    save('tv:paperPrefs', next);
  }, []);
  const getMainSeries = useCallback(() => mainRef.current?.series ?? null, []);
  const quickOrder = useCallback(
    (o: QuickOrder): string | null =>
      placeOrder({ side: o.side, type: o.type, qty: Number(paperQty), price: o.price }),
    [placeOrder, paperQty],
  );
  const lastPriceRef = useRef(lastPrice);
  lastPriceRef.current = lastPrice;
  const setBrackets = useCallback(
    (sl: number | undefined | null, tp: number | undefined | null): string | null => {
      const r = paperBracket(paper, symbol, sl ?? undefined, tp ?? undefined);
      if (!r.error) setPaper(r.state);
      return r.error ?? null;
    },
    [paper, symbol, setPaper],
  );

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
        trade={tradeOpen || brokerOpen}
        onTrade={() => setTradeOpen(true)}
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
          svpOn={indicators.some((i) => i.type === 'svp')}
          onToggleSvp={() =>
            setIndicators((list) =>
              list.some((i) => i.type === 'svp')
                ? list.filter((i) => i.type !== 'svp')
                : [...list, makeIndicator('svp', list)],
            )
          }
        />
        <div className="flex min-w-0 flex-1 flex-col">
          <div
            ref={wrapRef}
            className={`relative min-h-0 flex-1 ${tool !== 'cursor' || replayPick ? 'tv-tool' : ''}`}
          >
            <div ref={containerRef} className="absolute inset-0" />
            {textEdit ? (
              <input
                data-tv-ui
                autoFocus
                value={textEdit.value}
                placeholder="Type text, Enter to place"
                onChange={(e) => {
                  textValueRef.current = e.target.value;
                  setTextEdit({ ...textEdit, value: e.target.value });
                }}
                onKeyDown={(e) => {
                  e.stopPropagation();
                  if (e.key === 'Enter') {
                    const commit = textCommitRef.current;
                    textCommitRef.current = null;
                    commit?.(textEdit.value);
                    setTextEdit(null);
                  } else if (e.key === 'Escape') {
                    textCommitRef.current = null;
                    setTextEdit(null);
                  }
                }}
                onBlur={() => {
                  const commit = textCommitRef.current;
                  textCommitRef.current = null;
                  if (commit && textEdit.value.trim()) commit(textEdit.value);
                  setTextEdit(null);
                }}
                style={{
                  left: Math.max(
                    4,
                    Math.min(textEdit.x, (wrapRef.current?.clientWidth ?? 800) - 224),
                  ),
                  top: Math.max(4, textEdit.y - 14),
                }}
                className="absolute z-30 w-52 rounded border border-sky-500 bg-neutral-900 px-2 py-1 text-sm text-neutral-100 shadow-lg outline-none placeholder:text-neutral-500"
              />
            ) : null}
            {priceLegend}
            {portals}
            {paperOpen ? (
              <>
                {prefs.showButtons ? (
                  <PaperTicket
                    price={lastPrice}
                    decimals={decimals}
                    shifted={!!selected}
                    qty={paperQty}
                    onQty={(v) => {
                      setPaperQty(v);
                      save('tv:paperQty', v);
                    }}
                    oneClick={oneClick}
                    onSide={(side) => {
                      if (oneClick) {
                        const err = placeOrder({ side, type: 'market', qty: Number(paperQty) });
                        if (err) window.alert(err);
                      } else setOrderDlg({ side, type: 'market' });
                    }}
                    onDialog={() => setOrderDlg({ side: 'buy', type: 'market' })}
                  />
                ) : null}
              </>
            ) : null}
            {/* the on-chart "+" / right-click menu works whether or not the paper panel is open */}
            <>
              <PaperOverlay
                chartRef={chartRef}
                getSeries={getMainSeries}
                wrap={wrapRef.current}
                state={paper}
                symbol={symbol}
                last={lastPrice}
                decimals={decimals}
                qty={Number(paperQty) > 0 ? Number(paperQty) : 1}
                enabled={tool === 'cursor' && !replayPick}
                showPositions={prefs.showPositions}
                showOrders={prefs.showOrders}
                onModifyOrder={(id) => {
                  const o = paper.orders.find((x) => x.id === id);
                  if (o)
                    setOrderDlg({
                      side: o.side,
                      type: o.type,
                      price: o.price,
                      edit: { id: o.id, qty: o.qty, tp: o.tp, sl: o.sl, tif: o.tif },
                    });
                }}
                onHLine={(price) =>
                  setDrawings([
                    ...drawings,
                    {
                      id: newId(),
                      kind: 'hline',
                      pts: [
                        {
                          t: bars.length ? bars[bars.length - 1]!.time : Date.now() / 1000,
                          p: price,
                        },
                      ],
                      color: DEFAULT_COLOR.hline,
                      width: 1,
                    },
                  ])
                }
                onMoveOrder={(id, price) => {
                  if (lastPrice == null) return 'No price yet';
                  const r = paperMove(paper, id, price, lastPrice);
                  if (!r.error) setPaper(r.state);
                  return r.error ?? null;
                }}
                onCancelOrder={(id) => setPaper(paperCancel(paper, id, paperNow))}
                onUpdateOrder={(id, patch) => {
                  if (lastPrice == null) return 'No price yet';
                  const r = paperUpdate(paper, id, patch, lastPrice);
                  if (!r.error) setPaper(r.state);
                  return r.error ?? null;
                }}
                onOrderMarket={(id) => {
                  const o = paper.orders.find((x) => x.id === id);
                  if (!o || lastPrice == null) return 'No price yet';
                  const r = placeMarket(
                    paperCancel(paper, id, paperNow),
                    symbol,
                    o.side,
                    o.qty,
                    lastPrice,
                    paperNow,
                    {
                      sl: o.sl,
                      tp: o.tp,
                    },
                  );
                  if (!r.error) setPaper(r.state);
                  return r.error ?? null;
                }}
                onBracket={setBrackets}
                onClosePos={() => {
                  if (lastPrice != null) setPaper(paperClose(paper, symbol, lastPrice, paperNow));
                }}
                onReverse={() => {
                  if (lastPrice == null) return 'No price yet';
                  const r = paperReverse(paper, symbol, lastPrice, paperNow);
                  if (!r.error) setPaper(r.state);
                  return r.error ?? null;
                }}
                onQuick={quickOrder}
                onDialog={(price) =>
                  setOrderDlg({
                    side: lastPrice != null && price < lastPrice ? 'buy' : 'sell',
                    type: 'limit',
                    price,
                  })
                }
              />
              {orderDlg && lastPrice != null ? (
                <PaperOrderDialog
                  key={`${orderDlg.edit?.id ?? ''}${orderDlg.side}${orderDlg.type}${orderDlg.price ?? ''}`}
                  init={orderDlg}
                  symbol={symbol}
                  last={lastPrice}
                  decimals={decimals}
                  currency={meta?.currency ?? ''}
                  qty={paperQty}
                  oneClick={oneClick}
                  equity={equityOf(paper)}
                  onModify={(id, patch) => {
                    if (lastPrice == null) return 'No price yet';
                    const r = paperUpdate(paper, id, patch, lastPrice);
                    if (!r.error) setPaper(r.state);
                    return r.error ?? null;
                  }}
                  onOneClick={(v) => updatePrefs({ ...prefs, instant: v })}
                  onSubmit={(o, q) => {
                    setPaperQty(q);
                    save('tv:paperQty', q);
                    return placeOrder(o);
                  }}
                  onClose={() => setOrderDlg(null)}
                />
              ) : null}
            </>
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
                  Footprint is estimated from 1-minute bars and only exists for the last ~
                  {subBars.length
                    ? Math.max(1, Math.round((Date.now() / 1000 - subBars[0]!.time) / 86400))
                    : 7}{' '}
                  days
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
              const d =
                deltaTip != null ? divergences.find((v) => v.chartIndex === deltaTip) : undefined;
              const pos = d ? deltaPrimRef.current?.position(d.chartIndex) : null;
              if (!d || !pos) return null;
              const buyers = d.kind === 'buyers';
              const W = 210;
              const cw = wrapRef.current?.clientWidth ?? 800;
              const ch = wrapRef.current?.clientHeight ?? 600;
              const left = Math.max(8, Math.min(pos.x - W / 2, cw - W - 72));
              // prefer the side away from the candle, but flip when that side has no room
              const H = 130;
              const above = pos.below ? ch - pos.y < H + 16 : pos.y >= H + 16;
              const status =
                d.status === 'confirmed'
                  ? 'confirmed'
                  : d.status === 'pending'
                    ? 'waiting for confirmation'
                    : 'no follow-through';
              return (
                <div
                  className="absolute z-30 rounded-lg border border-[#e879f9]/60 bg-[#1e222d] p-3 text-[12px] text-[#d1d4dc] shadow-2xl"
                  style={{
                    left,
                    width: W,
                    ...(above ? { bottom: ch - pos.y + 16 } : { top: pos.y + 16 }),
                  }}
                  data-tv-ui
                >
                  <div className="mb-1 flex items-center justify-between">
                    <span className="font-semibold text-[#e879f9]">
                      Δ {buyers ? 'buyers trapped' : 'sellers trapped'}
                    </span>
                    <button
                      type="button"
                      onClick={() => setDeltaTip(null)}
                      className="text-[#787b86] hover:text-white"
                    >
                      <Icons.close size={12} />
                    </button>
                  </div>
                  <div
                    className={`text-[18px] font-semibold tabular-nums ${d.delta >= 0 ? 'text-[#089981]' : 'text-[#f23645]'}`}
                  >
                    {d.delta >= 0 ? '+' : '−'}
                    {Math.abs(Math.round(d.delta)).toLocaleString('en-IN')}
                  </div>
                  <div className="text-[#787b86]">
                    {d.pct.toFixed(1)}% of volume · {buyers ? 'upper' : 'lower'} wick{' '}
                    {d.wick.toFixed(0)}%{d.rvol != null ? ` · RVOL ${d.rvol.toFixed(1)}×` : ''}
                  </div>
                  <div className="mt-1 text-[#787b86]">
                    {status}
                    {d.coarse ? ' · 5m estimate' : ''}
                  </div>
                </div>
              );
            })()}
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
                selected.kind === 'avprofile' ||
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
                {selected.kind === 'vprofile' || selected.kind === 'avprofile' ? (
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
                    <button
                      type="button"
                      title="Settings"
                      className="ml-1 rounded p-1 text-[#b2b5be] hover:bg-[#2a2e39]"
                      onClick={() => setVpEditId(selected.id)}
                    >
                      <Icons.gear size={16} />
                    </button>
                  </label>
                ) : selected.kind === 'fib' ? (
                  <button
                    type="button"
                    title="Settings"
                    className="rounded p-1 text-[#b2b5be] hover:bg-[#2a2e39]"
                    onClick={() => setFibEditId(selected.id)}
                  >
                    <Icons.gear size={16} />
                  </button>
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
          {brokerOpen ? (
            <BrokerPanel
              selected={brokerSel}
              symbol={symbol}
              lastPrice={lastPrice}
              onClose={() => setBrokerOpen(false)}
            />
          ) : null}
          {paperOpen ? (
            <PaperPanel
              state={paper}
              symbol={symbol}
              replay={replayActive}
              prices={lastPrice != null ? { [symbol]: lastPrice } : {}}
              decimals={decimals}
              currency={meta?.currency ?? ''}
              tz={timeZone}
              onClose={() => {
                setPaperOpen(false);
                save('tv:paperOpen', false);
              }}
              onClosePos={(sym) => {
                if (sym === symbol && lastPrice != null)
                  setPaper(paperClose(paper, sym, lastPrice, paperNow));
              }}
              onCancel={(id) => setPaper(paperCancel(paper, id, paperNow))}
              onBracket={(sym, sl, tp) => {
                const r = paperBracket(paper, sym, sl, tp);
                if (!r.error) setPaper(r.state);
                return r.error ?? null;
              }}
              onSettings={(i, c) => setPaper(paperSettings(paper, i, c))}
              onReset={() => setPaper(newAccount(paper.initial, paper.commissionPct))}
              prefs={prefs}
              onPrefs={updatePrefs}
            />
          ) : null}
          <BottomBar
            paperOpen={paperOpen}
            onPaper={() => {
              const next = !paperOpen;
              setPaperOpen(next);
              save('tv:paperOpen', next);
            }}
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
        <div className={compact ? 'contents' : 'flex min-h-0 shrink-0 flex-col'}>
          <div className={compact ? 'contents' : 'flex min-h-0 flex-1'}>
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
            {cvdOpen && !deltaOpen && !screenerOpen && !compact ? (
              <CvdPanel
                symbol={meta?.symbol ?? symbol}
                items={cvdItems}
                enabled={computed.some(
                  (c) => c.cfg.type === 'cvd' && (c.cfg.divMode ?? 'regular') !== 'off',
                )}
                missing={
                  !indicators.some((i) => i.type === 'cvd')
                    ? 'indicator'
                    : indicators.every((i) => i.type !== 'cvd' || i.divMode === 'off')
                      ? 'off'
                      : null
                }
                onAddIndicator={() =>
                  setIndicators((list) => [...list, makeIndicator('cvd', list)])
                }
                filter={cvdFilter}
                onFilter={(f) => {
                  setCvdFilter(f);
                  save('tv:cvdFilter', f);
                }}
                showFailed={cvdShowFailed}
                onShowFailed={(on) => {
                  setCvdShowFailed(on);
                  save('tv:cvdShowFailed', on);
                }}
                intraday={tf.intraday}
                timeZone={timeZone}
                active={cvdPick}
                onJump={jumpToCvd}
              />
            ) : null}
            {fvgOpen && !screenerOpen && !compact ? (
              <FvgPanel
                symbol={meta?.symbol ?? symbol}
                items={fvgShown}
                detected={fvgAll.length}
                cfg={fvgCfg}
                onCfg={(patch) =>
                  setFvgCfg((cur) => {
                    const next = { ...cur, ...patch };
                    save('tv:fvgCfg', next);
                    return next;
                  })
                }
                view={fvgView}
                onView={(patch) =>
                  setFvgView((cur) => {
                    const next = { ...cur, ...patch };
                    save('tv:fvgView', next);
                    return next;
                  })
                }
                marksOn={fvgMarks}
                onMarks={(on) => {
                  setFvgMarks(on);
                  save('tv:fvgMarks', on);
                }}
                intraday={tf.intraday}
                timeZone={timeZone}
                active={fvgPick}
                onJump={jumpToFvg}
              />
            ) : null}
            {pocOpen && !compact ? (
              <PocScannerPanel
                active={symbol}
                onOpen={(row, trade) => {
                  pocJumped.current = null;
                  pocOpenedAt.current = Date.now();
                  setPocPick(row);
                  setPocTrade(trade ?? null);
                  setSymbol(row.symbol);
                  // the zone is built from 5-minute bars: on a daily chart it would be one candle wide
                  if (!tf.intraday || tf.secs > 900) setTfId('5m');
                }}
              />
            ) : null}
            {screenerOpen && !compact ? (
              <ScreenerPanel
                mode="side"
                activeSymbol={symbol}
                onOpenSymbol={(row) => setSymbol(row.symbol)}
                onAddWatch={(row) => setPendingWatch({ symbol: row.symbol, name: row.name })}
                onClose={() => {
                  setScreenerOpen(false);
                  save('tv:screenerOpen', false);
                }}
              />
            ) : null}
            {watchOpen &&
            !screenerOpen &&
            !(pocOpen && !compact) &&
            !(deltaOpen && !compact) &&
            !(cvdOpen && !compact) &&
            !(fvgOpen && !compact) ? (
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
          </div>
          {notesOpen && !compact ? (
            <NotesPanel
              symbol={meta?.symbol ?? symbol}
              onClose={() => {
                setNotesOpen(false);
                save('tv:notesOpen', false);
              }}
            />
          ) : null}
        </div>
        {compact ? null : (
          <RightRail
            screenerOpen={screenerOpen}
            onScreener={() => {
              const next = !screenerOpen;
              setScreenerOpen(next);
              save('tv:screenerOpen', next);
              if (next) {
                closePoc();
                setDeltaOpen(false);
                save('tv:deltaOpen', false);
                setCvdOpen(false);
                save('tv:cvdOpen', false);
                setFvgOpen(false);
                save('tv:fvgOpen', false);
              }
            }}
            watchlistOpen={
              watchOpen && !deltaOpen && !cvdOpen && !fvgOpen && !screenerOpen && !pocOpen
            }
            fvgOpen={fvgOpen && !screenerOpen}
            deltaOpen={deltaOpen && !screenerOpen}
            cvdOpen={cvdOpen && !deltaOpen && !screenerOpen}
            notesOpen={notesOpen}
            onNotes={() => {
              const next = !notesOpen;
              setNotesOpen(next);
              save('tv:notesOpen', next);
            }}
            hideDivs={hideDivs}
            onHideDivs={() => {
              const next = !hideDivs;
              setRevealed([]);
              setHideDivs(next);
              save('tv:hideDivs', next);
            }}
            onWatchlist={() => {
              const next = !(
                watchOpen &&
                !deltaOpen &&
                !cvdOpen &&
                !fvgOpen &&
                !screenerOpen &&
                !pocOpen
              );
              setWatchOpen(next);
              save('tv:watchOpen', next);
              if (next) {
                setScreenerOpen(false);
                save('tv:screenerOpen', false);
                closePoc();
                setDeltaOpen(false);
                save('tv:deltaOpen', false);
                setCvdOpen(false);
                save('tv:cvdOpen', false);
                setFvgOpen(false);
                save('tv:fvgOpen', false);
              }
            }}
            onDelta={toggleDelta}
            onCvd={() => {
              const next = !(cvdOpen && !deltaOpen);
              setCvdOpen(next);
              save('tv:cvdOpen', next);
              if (next) {
                setScreenerOpen(false);
                save('tv:screenerOpen', false);
                closePoc();
                setDeltaOpen(false);
                save('tv:deltaOpen', false);
                setFvgOpen(false);
                save('tv:fvgOpen', false);
              }
            }}
            onFvg={toggleFvg}
            pocOpen={pocOpen}
            onPoc={() => {
              const next = !pocOpen;
              setPocOpen(next);
              save('tv:pocOpen', next);
              if (next) {
                setScreenerOpen(false);
                save('tv:screenerOpen', false);
                setDeltaOpen(false);
                save('tv:deltaOpen', false);
                setCvdOpen(false);
                save('tv:cvdOpen', false);
                setFvgOpen(false);
                save('tv:fvgOpen', false);
              }
            }}
          />
        )}
      </div>
      {tradeOpen ? (
        <TradeDialog
          onClose={() => setTradeOpen(false)}
          onPaper={() => {
            setPaperOpen(true);
            save('tv:paperOpen', true);
          }}
          onBroker={(id) => {
            setBrokerSel(id);
            setBrokerOpen(true);
          }}
        />
      ) : null}
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
          panels={[
            {
              id: 'delta',
              name: 'Delta Divergence',
              description: 'Price vs footprint delta divergences, with the setups they sit next to',
              open: deltaActive,
              onToggle: () => {
                const next = !deltaActive;
                setDeltaOn(next);
                save('tv:deltaOn', next);
                if (!next && deltaOpen) toggleDelta();
              },
            },
            {
              id: 'fvg',
              name: 'Fair Value Gap',
              description: 'Volumetric fair value gaps, drawn on the chart and listed in a panel',
              open: fvgActive,
              onToggle: () => {
                const next = !fvgActive;
                setFvgOn(next);
                save('tv:fvgOn', next);
                if (!next && fvgOpen) toggleFvg();
              },
            },
            {
              id: 'poc',
              name: 'POC Retest',
              description:
                'Untested points of control from recent sessions: moved away, pulling back, first touch',
              open: pocOn,
              onToggle: () => {
                setPocOn(!pocOn);
                save('tv:pocOn', !pocOn);
              },
            },
          ]}
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
          onChange={(next) => setDrawings(drawings.map((d) => (d.id === next.id ? next : d)))}
          onClose={() => setPosEditId(null)}
        />
      ) : null}
      {vpEditId && drawings.find((d) => d.id === vpEditId) ? (
        <VpSettingsDialog
          vp={vpOf(drawings.find((d) => d.id === vpEditId)!)}
          rows={drawings.find((d) => d.id === vpEditId)!.rows ?? DEFAULT_ROWS}
          widthLabel={
            drawings.find((d) => d.id === vpEditId)!.kind === 'avprofile' ? 'Width (% of pane)' : undefined
          }
          onChange={({ vp, rows }) =>
            setDrawings(drawings.map((d) => (d.id === vpEditId ? { ...d, vp, rows } : d)))
          }
          onClose={() => setVpEditId(null)}
        />
      ) : null}
      {fibEditId && drawings.find((d) => d.id === fibEditId) ? (
        <FibSettingsDialog
          drawing={drawings.find((d) => d.id === fibEditId)!}
          onChange={(next) => setDrawings(drawings.map((d) => (d.id === next.id ? next : d)))}
          onClose={() => setFibEditId(null)}
        />
      ) : null}
      {settingsId && indicators.find((i) => i.id === settingsId)?.type === 'avp' ? (
        <VpSettingsDialog
          title="Anchored volume profile"
          vp={avpOf(indicators.find((i) => i.id === settingsId)!)}
          rows={indicators.find((i) => i.id === settingsId)!.length}
          defaults={AVP_VP}
          widthLabel="Width (% of pane)"
          onChange={({ vp, rows }) =>
            setIndicators((list) =>
              list.map((i) => (i.id === settingsId ? { ...i, vp, length: rows } : i)),
            )
          }
          onClose={() => setSettingsId(null)}
        />
      ) : settingsId && indicators.find((i) => i.id === settingsId)?.type === 'svp' ? (
        <VpSettingsDialog
          title="Session volume profile"
          vp={svpOf(indicators.find((i) => i.id === settingsId)!)}
          rows={indicators.find((i) => i.id === settingsId)!.length}
          defaults={SVP_DEFAULTS}
          widthLabel="Width (% of session)"
          onChange={({ vp, rows }) =>
            setIndicators((list) =>
              list.map((i) => (i.id === settingsId ? { ...i, vp, length: rows } : i)),
            )
          }
          onClose={() => setSettingsId(null)}
        />
      ) : settingsId && indicators.find((i) => i.id === settingsId) ? (
        <IndicatorSettings
          cfg={indicators.find((i) => i.id === settingsId)!}
          onChange={(next) =>
            setIndicators((list) => list.map((i) => (i.id === next.id ? next : i)))
          }
          onClose={() => setSettingsId(null)}
        />
      ) : null}
      {pocSettings ? (
        <PocSettingsDialog
          cfg={pocCfg}
          view={pocView}
          onChange={(next) => {
            setPocCfg(next);
            save('tv:pocCfg', next);
          }}
          onView={(next) => {
            setPocView(next);
            save('tv:pocView', next);
          }}
          onClose={() => setPocSettings(false)}
        />
      ) : null}
      {/* keeps CATALOG referenced for tree-shaken dev builds */}
      <span hidden>{CATALOG.length}</span>
    </div>
  );
}
