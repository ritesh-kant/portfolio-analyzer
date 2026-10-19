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
import { BottomBar, type RangeId } from './bottom-bar';
import { DrawingsPrimitive } from './drawings-primitive';
import { FootprintView, type FootprintData } from './footprint-series';
import { Icons } from './icons';
import { IndicatorPicker, IndicatorSettings } from './indicator-dialog';
import { LeftToolbar, type Tool } from './left-toolbar';
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
}

const DEFAULT_SETTINGS: Settings = {
  symbol: 'RELIANCE.NS',
  tfId: '5m',
  chartType: 'candles',
  indicators: DEFAULT_INDICATORS,
  fp: { on: false, auto: true, step: 0.05, imbalance: 3, showDelta: true },
};

// ── indicator computation ────────────────────────────────────────────────

interface Line {
  key: string;
  label?: string;
  color: string;
  values: Series;
  kind: 'line' | 'hist';
}

interface Computed {
  cfg: IndicatorCfg;
  /** Pane index the indicator draws in (0 = price pane). */
  pane: number;
  lines: Line[];
}

function computeIndicators(indicators: IndicatorCfg[], bars: Bar[], timeZone: string): Computed[] {
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
        lines = [{ key: 'rvol', color: '#26a69a', values: relativeVolume(volumes, cfg.length), kind: 'hist' }];
        break;
      case 'ema':
        lines = [{ key: 'ema', color: cfg.color, values: ema(closes, cfg.length), kind: 'line' }];
        break;
      case 'sma':
        lines = [{ key: 'sma', color: cfg.color, values: sma(closes, cfg.length), kind: 'line' }];
        break;
      case 'vwap':
        lines = [{ key: 'vwap', color: cfg.color, values: vwap(bars, (t) => sessionDay(t, timeZone)), kind: 'line' }];
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
    }
    return { cfg, pane, lines };
  });
}

const fmtVolume = (v: number) =>
  v >= 1e7 ? `${(v / 1e6).toFixed(2)}M` : v >= 1e6 ? `${(v / 1e6).toFixed(2)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(2)}K` : `${Math.round(v)}`;

const histColor = (cur: number, prev: number | null) =>
  cur >= 0 ? (prev != null && cur >= prev ? '#26a69a' : '#b2dfdb') : prev != null && cur <= prev ? '#ff5252' : '#ffcdd2';

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
  | { mode: 'move'; id: string; orig: Drawing; startLogical: number; startPrice: number; moved: boolean }
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
  const indStructRef = useRef<{ key: string; entries: { line: Line; cfgId: string; series: ISeriesApi<SeriesType, Time> }[] }>({
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

  // scale
  const [log, setLog] = useState(false);
  const [percent, setPercent] = useState(false);
  const [autoScale, setAutoScale] = useState(true);

  // data
  const [raw, setRaw] = useState<{ key: string; bars: Bar[] }>({ key: '', bars: [] });
  const [subRaw, setSubRaw] = useState<Bar[]>([]);
  const [meta, setMeta] = useState<SymbolMeta | null>(null);
  const [status, setStatus] = useState<'loading' | 'ok' | 'error'>('loading');
  const [error, setError] = useState('');

  // interaction
  const [tool, setTool] = useState<Tool>('cursor');
  const [magnet, setMagnet] = useState(false);
  const [keep, setKeep] = useState(false);
  const [lockAll, setLockAll] = useState(false);
  const [hideAll, setHideAll] = useState(false);
  const [drawState, setDrawState] = useState<{ symbol: string; list: Drawing[] }>({ symbol: '', list: [] });
  const [draft, setDraft] = useState<Drawing | null>(null);
  const [measure, setMeasure] = useState<Drawing | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const [histTick, setHistTick] = useState(0);
  const history = useRef<{ past: Drawing[][]; future: Drawing[][] }>({ past: [], future: [] });

  // dialogs / panes
  const [showSearch, setShowSearch] = useState(false);
  const [showIndicators, setShowIndicators] = useState(false);
  const [settingsId, setSettingsId] = useState<string | null>(null);
  const [paneEls, setPaneEls] = useState<(HTMLElement | null)[]>([]);

  const tf = timeframeById(tfId);
  const timeZone = meta?.timezone ?? 'UTC';
  intradayRef.current = tf.intraday;

  // ── derived data ──────────────────────────────────────────────────────
  const bars = useMemo(
    () => (tf.aggregateSecs ? aggregateBars(raw.bars, tf.aggregateSecs, timeZone) : raw.bars),
    [raw.bars, tf.aggregateSecs, timeZone],
  );
  const wall = useMemo(() => bars.map((b) => wallClock(b.time, timeZone) as UTCTimestamp), [bars, timeZone]);
  const decimals = pricePrecision(bars[bars.length - 1]?.close ?? meta?.price ?? 0);
  const subBars = tf.source === '1m' ? raw.bars : subRaw;
  const effectiveStep = fp.auto ? autoStep(bars) : fp.step;
  const footprints = useMemo(
    () => (fp.on ? buildFootprints(bars, subBars, tf.secs, effectiveStep) : []),
    [fp.on, bars, subBars, tf.secs, effectiveStep],
  );
  const computed = useMemo(() => computeIndicators(indicators, bars, timeZone), [indicators, bars, timeZone]);
  const axis = useMemo(() => makeTimeAxis(bars.map((b) => b.time), tf.secs), [bars, tf.secs]);
  const drawings = useMemo(() => (drawState.symbol === symbol ? drawState.list : []), [drawState, symbol]);
  const footprintCoverage = fp.on && footprints.length ? footprints.filter(Boolean).length / footprints.length : 1;

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
    if (saved.fp) setFp({ ...DEFAULT_SETTINGS.fp, ...saved.fp, on: false });
    setReady(true);
  }, []);

  useEffect(() => {
    if (ready) save('tv:settings', { symbol, tfId, chartType, indicators, fp });
  }, [ready, symbol, tfId, chartType, indicators, fp]);

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
    if (drawState.symbol && drawState.symbol === symbol) save(`tv:drawings:${symbol}`, drawState.list);
  }, [drawState, symbol]);

  const setDrawings = useCallback(
    (next: Drawing[], record = true) => {
      setDrawState((cur) => {
        if (record) {
          history.current.past.push(cur.list);
          if (history.current.past.length > 100) history.current.past.shift();
          history.current.future = [];
        }
        return { symbol: cur.symbol, list: next };
      });
      setHistTick((n) => n + 1);
    },
    [],
  );

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

  // ── data fetching ─────────────────────────────────────────────────────
  const fetchBars = useCallback(async (sym: string, interval: string, range: string, signal?: AbortSignal) => {
    const res = await fetch(
      `/api/chart/bars?symbol=${encodeURIComponent(sym)}&interval=${interval}&range=${range}`,
      { signal, cache: 'no-store' },
    );
    const body = (await res.json()) as BarsResponse & { error?: string };
    if (!res.ok) throw new Error(body.error ?? `HTTP ${res.status}`);
    return body;
  }, []);

  useEffect(() => {
    if (!ready) return;
    const ctl = new AbortController();
    setStatus('loading');
    setError('');
    fetchBars(symbol, tf.source, tf.range, ctl.signal)
      .then((res) => {
        setRaw({ key: `${symbol}|${tf.source}|${tf.range}`, bars: res.bars });
        setMeta(res.meta);
        setStatus('ok');
      })
      .catch((e: unknown) => {
        if (ctl.signal.aborted) return;
        setStatus('error');
        setError(e instanceof Error ? e.message : 'Failed to load');
      });
    return () => ctl.abort();
  }, [ready, symbol, tf.source, tf.range, fetchBars]);

  // Footprint needs 1-minute bars underneath coarser candles.
  useEffect(() => {
    if (!ready || !fp.on || tf.source === '1m') return;
    const ctl = new AbortController();
    fetchBars(symbol, '1m', '7d', ctl.signal)
      .then((res) => setSubRaw(res.bars))
      .catch(() => setSubRaw([]));
    return () => ctl.abort();
  }, [ready, fp.on, symbol, tf.source, fetchBars]);

  // Live: re-pull the tail every 15 s and merge it in.
  useEffect(() => {
    if (!ready) return;
    let stopped = false;
    const timer = setInterval(async () => {
      if (document.hidden) return;
      try {
        const res = await fetchBars(symbol, tf.source, tf.intraday ? '1d' : '5d');
        if (stopped) return;
        setRaw((cur) => (cur.key.startsWith(`${symbol}|`) ? { key: cur.key, bars: mergeBars(cur.bars, res.bars) } : cur));
      } catch {
        // transient — next tick retries
      }
    }, 15000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [ready, symbol, tf.source, tf.intraday, fetchBars]);

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
        panes: { separatorColor: '#2a2e39', separatorHoverColor: 'rgba(41,98,255,0.3)', enableResize: true },
      },
      grid: { vertLines: { color: 'rgba(42,46,57,0.6)' }, horzLines: { color: 'rgba(42,46,57,0.6)' } },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#9598a1', width: 1, style: LineStyle.Dashed, labelBackgroundColor: '#363a45' },
        horzLine: { color: '#9598a1', width: 1, style: LineStyle.Dashed, labelBackgroundColor: '#363a45' },
      },
      rightPriceScale: { borderColor: '#2a2e39', scaleMargins: { top: 0.08, bottom: 0.08 }, minimumWidth: 72 },
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
    footRef.current = chart.addCustomSeries(view, { priceLineVisible: false, lastValueVisible: false }, 0);
    primRef.current = new DrawingsPrimitive();

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
      const common = { lastValueVisible: true, priceLineVisible: true, priceLineColor: '#787b86', priceLineStyle: LineStyle.Dotted };
      let series: ISeriesApi<SeriesType, Time>;
      if (effective === 'line') {
        series = chart.addSeries(LineSeries, { ...common, color: '#2962ff', lineWidth: 2 }, 0);
      } else if (effective === 'area') {
        series = chart.addSeries(
          AreaSeries,
          { ...common, lineColor: '#2962ff', topColor: 'rgba(41,98,255,0.35)', bottomColor: 'rgba(41,98,255,0.02)', lineWidth: 2 },
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
      isValue ? { time: wall[i]!, value: b.close } : { time: wall[i]!, open: b.open, high: b.high, low: b.low, close: b.close },
    );
    main.series.applyOptions({
      priceFormat: { type: 'price', precision: decimals, minMove: 1 / 10 ** decimals },
      title: symbol.replace(/\.(NS|BO)$/, ''),
    });
    main.series.setData(data as never);
    const viewKey = `${raw.key}|${tfId}`;
    if (viewKeyRef.current !== viewKey) {
      viewKeyRef.current = viewKey;
      const n = bars.length;
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(-2, n - 140), to: n + 7 });
      chart.priceScale('right').applyOptions({ autoScale: true });
    }
  }, [bars, wall, chartType, fp.on, decimals, symbol, raw.key, tfId]);

  // Footprint series.
  useEffect(() => {
    const series = footRef.current;
    const view = footViewRef.current;
    if (!series || !view) return;
    view.setSettings({ imbalance: fp.imbalance, showDelta: fp.showDelta });
    series.applyOptions({ priceFormat: { type: 'price', precision: decimals, minMove: 1 / 10 ** decimals } } as never);
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
  const structKey = JSON.stringify(indicators);
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const st = indStructRef.current;
    if (st.key === structKey) return;
    for (const entry of st.entries) chart.removeSeries(entry.series);
    const entries: typeof st.entries = [];
    const flags = { lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false };
    for (const c of computed) {
      for (const line of c.lines) {
        let series: ISeriesApi<SeriesType, Time>;
        if (c.cfg.type === 'volume') {
          series = chart.addSeries(
            HistogramSeries,
            { priceFormat: { type: 'volume' }, priceScaleId: 'vol', lastValueVisible: false, priceLineVisible: false },
            0,
          );
          series.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
        } else if (line.kind === 'hist') {
          series = chart.addSeries(
            HistogramSeries,
            { priceFormat: { type: 'price', precision: c.cfg.type === 'rvol' ? 2 : 3, minMove: 0.001 }, lastValueVisible: false, priceLineVisible: false },
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
              priceFormat: c.pane > 0 ? { type: 'price', precision: c.cfg.type === 'rsi' ? 2 : 3, minMove: 0.001 } : undefined,
            },
            c.pane,
          );
        }
        if (c.cfg.type === 'rsi') {
          for (const level of [70, 50, 30]) {
            series.createPriceLine({ price: level, color: '#363a45', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false });
          }
        }
        if (c.cfg.type === 'rvol') {
          series.createPriceLine({ price: 1, color: '#363a45', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false });
        }
        entries.push({ line, cfgId: c.cfg.id, series });
      }
    }
    indStructRef.current = { key: structKey, entries };
    chart.panes().forEach((pane, i) => pane.setStretchFactor(i === 0 ? 4 : 1));
    // Pane DOM nodes host the per-pane legends; they exist only after the first layout, so retry briefly.
    let attempts = 0;
    const syncPanes = () => {
      const live = chartRef.current;
      if (!live) return;
      const els = live.panes().map((pane) => pane.getHTMLElement());
      if (els.some((node) => !node) && attempts < 20) {
        attempts += 1;
        window.setTimeout(syncPanes, 50);
        return;
      }
      for (const node of els) if (node) node.style.position = 'relative';
      setPaneEls(els);
    };
    syncPanes();
  }, [structKey, computed]);

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
            bars.map((b, idx) => ({ time: wall[idx]!, value: b.volume, color: b.close >= b.open ? VOL_UP : VOL_DOWN })) as never,
          );
        } else if (c.cfg.type === 'rvol') {
          const out: { time: UTCTimestamp; value: number; color: string }[] = [];
          line.values.forEach((v, idx) => {
            if (v == null) return;
            const b = bars[idx]!;
            out.push({ time: wall[idx]!, value: v, color: b.close >= b.open ? VOL_UP : VOL_DOWN });
          });
          entry.series.setData(out as never);
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
      mode: percent ? PriceScaleMode.Percentage : log ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal,
    });
  }, [percent, log]);

  // Drawings → primitive.
  useEffect(() => {
    const all = measure ? [...drawings, measure] : drawings;
    primRef.current?.setState({ drawings: all, draft, selectedId, hidden: hideAll, axis, decimals });
  }, [drawings, measure, draft, selectedId, hideAll, axis, decimals]);

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
        const d: Drawing = {
          id: kind === 'measure' ? 'm_live' : newId(),
          kind,
          pts: need === 1 ? [pt] : [pt, pt],
          color: DEFAULT_COLOR[kind],
          width: 2,
          text: kind === 'text' ? 'Text' : undefined,
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
        drag = { mode: 'move', id: d.id, orig: d, startLogical: logical, startPrice: price, moved: false };
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
        if (drag?.mode === 'create' && Math.hypot(x - drag.startX, y - drag.startY) > 6) drag.moved = true;
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
          setDrawState((cur) => ({ symbol: cur.symbol, list: cur.list.map((v) => (v.id === id ? moved : v)) }));
        } else {
          const pt = toPt(x, y, true);
          if (!pt) return;
          const { id, handle } = drag;
          setDrawState((cur) => ({
            symbol: cur.symbol,
            list: cur.list.map((v) => {
              if (v.id !== id) return v;
              const pts = v.pts.map((q, i) => (i === handle ? pt : q));
              return { ...v, pts };
            }),
          }));
        }
        return;
      }
      if (latest.current.tool === 'cursor') {
        const hit = inPlot(x, y) ? prim?.findHit(x, y) ?? null : null;
        prim?.setState({ hoverId: hit?.drawingId ?? null });
        wrap.classList.toggle('tv-over-drawing', !!hit);
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
          const before = drag.mode === 'move' ? drag.orig : latest.current.drawings.find((v) => v.id === (drag as Drag & { id: string }).id);
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
      if (d?.kind !== 'text') return;
      const input = window.prompt('Text', d.text ?? '');
      if (input === null) return;
      setDrawings(latest.current.drawings.map((v) => (v.id === d.id ? { ...v, text: input || v.text } : v)));
    };

    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.closest('input, textarea, select')) return;
      if (e.key === 'Escape') {
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

    wrap.addEventListener('mousedown', onDown, true);
    wrap.addEventListener('mousemove', onMove, true);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    wrap.addEventListener('dblclick', onDbl, true);
    window.addEventListener('keydown', onKey);
    return () => {
      wrap.removeEventListener('mousedown', onDown, true);
      wrap.removeEventListener('mousemove', onMove, true);
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      wrap.removeEventListener('dblclick', onDbl, true);
      window.removeEventListener('keydown', onKey);
    };
  }, [axis, setDrawings, undo, redo]);

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
  const idx = hoverIdx != null && hoverIdx >= 0 && hoverIdx < bars.length ? hoverIdx : bars.length - 1;
  const bar = bars[idx];
  const prevClose = idx > 0 ? bars[idx - 1]!.close : (meta?.prevClose ?? bar?.open ?? 0);
  const change = bar ? bar.close - prevClose : 0;
  const changePct = prevClose ? (change / prevClose) * 100 : 0;
  const candleColor = bar && bar.close >= bar.open ? UP : DOWN;

  const valueText = (c: Computed, line: Line) => {
    const v = line.values[idx];
    if (v == null) return '';
    if (c.cfg.type === 'volume') return fmtVolume(v);
    if (c.cfg.type === 'rvol' || c.cfg.type === 'rsi') return v.toFixed(2);
    if (c.cfg.type === 'atr') return v.toFixed(decimals);
    if (c.cfg.type === 'macd') return v.toFixed(3);
    return v.toFixed(decimals);
  };

  const legendRow = (c: Computed): ReactNode => (
    <div key={c.cfg.id} className="group pointer-events-auto flex items-center gap-2 text-[12px] leading-5" data-tv-ui>
      <span className="text-[#b2b5be]">{indicatorLabel(c.cfg)}</span>
      {c.lines.map((l) => (
        <span key={l.key} style={{ color: c.cfg.type === 'volume' ? (bar && bar.close >= bar.open ? UP : DOWN) : l.color }}>
          {valueText(c, l)}
        </span>
      ))}
      <span className="flex gap-1 opacity-0 transition-opacity group-hover:opacity-100">
        {c.cfg.type !== 'volume' && c.cfg.type !== 'vwap' ? (
          <button type="button" title="Settings" onClick={() => setSettingsId(c.cfg.id)} className="text-[#787b86] hover:text-white">
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
    <div className="pointer-events-none absolute left-2 top-1.5 z-10 select-none" style={{ fontFamily: FONT }}>
      <div className="flex flex-wrap items-center gap-x-2 text-[13px]">
        <span className="font-semibold text-[#d1d4dc]">{meta?.name ?? symbol}</span>
        <span className="text-[#787b86]">
          · {tf.label} · {meta?.exchange}
        </span>
        {bar ? (
          <span className="flex items-center gap-2 tabular-nums" style={{ color: candleColor }}>
            <span>O<b className="ml-0.5 font-medium">{bar.open.toFixed(decimals)}</b></span>
            <span>H<b className="ml-0.5 font-medium">{bar.high.toFixed(decimals)}</b></span>
            <span>L<b className="ml-0.5 font-medium">{bar.low.toFixed(decimals)}</b></span>
            <span>C<b className="ml-0.5 font-medium">{bar.close.toFixed(decimals)}</b></span>
            <span>
              {change >= 0 ? '+' : ''}
              {change.toFixed(decimals)} ({changePct >= 0 ? '+' : ''}
              {changePct.toFixed(2)}%)
            </span>
          </span>
        ) : null}
      </div>
      {computed.filter((c) => c.pane === 0).map(legendRow)}
    </div>
  );

  const portals = paneEls.map((el, paneIdx) => {
    if (!el || paneIdx === 0) return null;
    const rows = computed.filter((c) => c.pane === paneIdx);
    if (!rows.length) return null;
    return createPortal(
      <div className="pointer-events-none absolute left-2 top-1 z-10 select-none" style={{ fontFamily: FONT }}>
        {rows.map(legendRow)}
      </div>,
      el,
      `pane-${paneIdx}`,
    );
  });

  const canUndo = histTick >= 0 && history.current.past.length > 0;
  const canRedo = histTick >= 0 && history.current.future.length > 0;

  return (
    <div ref={rootRef} className="fixed inset-0 z-50 flex flex-col bg-[#131722] text-[#d1d4dc]" style={{ fontFamily: FONT }}>
      <style>{`.tv-over-drawing, .tv-over-drawing * { cursor: pointer !important; } .tv-tool, .tv-tool * { cursor: crosshair !important; }`}</style>
      <TopBar
        symbol={symbol.replace(/\.(NS|BO)$/, '')}
        onOpenSearch={() => setShowSearch(true)}
        tfId={tfId}
        onTf={setTfId}
        chartType={chartType}
        onChartType={setChartType}
        onIndicators={() => setShowIndicators(true)}
        footprint={{ ...fp, step: effectiveStep }}
        onFootprint={(patch) => setFp((cur) => ({ ...cur, ...patch, ...(patch.auto === false ? { step: effectiveStep } : {}) }))}
        onFootprintStep={(dir) => setFp((cur) => ({ ...cur, auto: false, step: stepLadder(effectiveStep, dir) }))}
        canUndo={canUndo}
        canRedo={canRedo}
        onUndo={undo}
        onRedo={redo}
        onScreenshot={screenshot}
        onFullscreen={fullscreen}
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
          <div ref={wrapRef} className={`relative min-h-0 flex-1 ${tool !== 'cursor' ? 'tv-tool' : ''}`}>
            <div ref={containerRef} className="absolute inset-0" />
            {priceLegend}
            {portals}
            {status === 'loading' ? (
              <div className="pointer-events-none absolute right-20 top-2 z-10 rounded bg-[#1e222d] px-2 py-1 text-xs text-[#b2b5be]">
                Loading {symbol}…
              </div>
            ) : null}
            {status === 'error' ? (
              <div className="absolute left-1/2 top-1/2 z-10 -translate-x-1/2 -translate-y-1/2 rounded-lg border border-[#363a45] bg-[#1e222d] px-6 py-5 text-center" data-tv-ui>
                <div className="text-sm font-semibold text-[#f23645]">Couldn’t load {symbol}</div>
                <div className="mt-1 max-w-xs text-xs text-[#9598a1]">{error}</div>
                <button type="button" onClick={() => setShowSearch(true)} className="mt-3 rounded bg-[#2962ff] px-3 py-1 text-xs font-semibold text-white">
                  Pick another symbol
                </button>
              </div>
            ) : null}
            {status === 'ok' && !bars.length ? (
              <div className="pointer-events-none absolute left-1/2 top-1/2 z-10 -translate-x-1/2 -translate-y-1/2 text-sm text-[#787b86]">
                No data for {symbol} on {tf.label}.
              </div>
            ) : null}
            {fp.on && bars.length && footprintCoverage < 1 ? (
              <div className="pointer-events-none absolute bottom-3 left-3 z-10 max-w-sm rounded bg-[#1e222d]/90 px-3 py-1.5 text-[11px] text-[#b2b5be]">
                Footprint is estimated from 1-minute bars and only exists for the last ~7 sessions
                {tf.source !== '1m' && !subRaw.length ? ' — loading…' : ` (${Math.round(footprintCoverage * 100)}% of candles covered)`}.
                Zoom in on a recent candle to read the rows.
              </div>
            ) : null}
            {selected ? (
              <div
                className="absolute left-1/2 top-2 z-20 flex -translate-x-1/2 items-center gap-1.5 rounded-md border border-[#363a45] bg-[#1e222d] px-2 py-1 shadow-lg"
                data-tv-ui
              >
                {selected.kind === 'fib' || selected.kind === 'measure' ? null : SWATCHES.map((c) => (
                  <button
                    key={c}
                    type="button"
                    aria-label={`Colour ${c}`}
                    onClick={() => patchSelected({ color: c })}
                    className={`h-4 w-4 rounded-full ${selected.color === c ? 'ring-2 ring-white ring-offset-1 ring-offset-[#1e222d]' : ''}`}
                    style={{ background: c }}
                  />
                ))}
                {[1, 2, 3, 4].map((w) => (
                  <button
                    key={w}
                    type="button"
                    onClick={() => patchSelected({ width: w })}
                    title={`${w}px`}
                    className={`flex h-6 w-6 items-center justify-center rounded hover:bg-[#2a2e39] ${selected.width === w ? 'bg-[#2a2e39]' : ''}`}
                  >
                    <span className="block w-3.5 bg-[#d1d4dc]" style={{ height: w }} />
                  </button>
                ))}
                <button type="button" title="Clone" className="rounded p-1 text-[#b2b5be] hover:bg-[#2a2e39]" onClick={() => {
                  const copy = { ...translate(selected, 0, 0), id: newId(), pts: selected.pts.map((p) => ({ t: p.t + tf.secs * 3, p: p.p })) };
                  setDrawings([...drawings, copy]);
                  setSelectedId(copy.id);
                }}>
                  <Icons.copy size={16} />
                </button>
                <button type="button" title={selected.locked ? 'Unlock' : 'Lock'} className="rounded p-1 text-[#b2b5be] hover:bg-[#2a2e39]" onClick={() => patchSelected({ locked: !selected.locked })}>
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
      </div>
      {showSearch ? <SymbolSearch onPick={setSymbol} onClose={() => setShowSearch(false)} /> : null}
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
      {settingsId && indicators.find((i) => i.id === settingsId) ? (
        <IndicatorSettings
          cfg={indicators.find((i) => i.id === settingsId)!}
          onChange={(next) => setIndicators((list) => list.map((i) => (i.id === next.id ? next : i)))}
          onClose={() => setSettingsId(null)}
        />
      ) : null}
      {/* keeps CATALOG referenced for tree-shaken dev builds */}
      <span hidden>{CATALOG.length}</span>
    </div>
  );
}
