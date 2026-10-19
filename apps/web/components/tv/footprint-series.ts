import {
  type CustomData,
  type CustomSeriesOptions,
  type CustomSeriesPricePlotValues,
  type CustomSeriesWhitespaceData,
  type ICustomSeriesPaneRenderer,
  type ICustomSeriesPaneView,
  type PaneRendererCustomData,
  type PriceToCoordinateConverter,
  type Time,
  customSeriesDefaultOptions,
} from 'lightweight-charts';
import type { FootprintCandle } from '../../lib/tv/types';

const FONT = '-apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, Ubuntu, sans-serif';
const UP = '#089981';
const DOWN = '#f23645';
const POC = '#fbc02d';

export interface FootprintData extends CustomData<Time> {
  open: number;
  high: number;
  low: number;
  close: number;
  fp: FootprintCandle | null;
}

export interface FootprintSettings {
  /** Diagonal imbalance threshold, e.g. 3 = 300%. */
  imbalance: number;
  showDelta: boolean;
}

type Target = Parameters<ICustomSeriesPaneRenderer['draw']>[0];

const fmtVol = (v: number) =>
  v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e4 ? `${(v / 1e3).toFixed(0)}K` : v >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : `${Math.round(v)}`;

interface Rows {
  levels: FootprintCandle['levels'];
  step: number;
  poc: number;
}

/** Merge `k` adjacent base rows into one so a row is tall enough to read. */
function coarsen(fp: FootprintCandle, k: number): Rows {
  if (k <= 1) return { levels: fp.levels, step: fp.step, poc: fp.poc };
  const groups = new Map<number, { price: number; bid: number; ask: number }>();
  for (const level of fp.levels) {
    const g = Math.floor(Math.round(level.price / fp.step) / k);
    const cur = groups.get(g) ?? { price: g * k * fp.step, bid: 0, ask: 0 };
    cur.bid += level.bid;
    cur.ask += level.ask;
    groups.set(g, cur);
  }
  const levels = [...groups.entries()].sort(([a], [b]) => a - b).map(([, v]) => v);
  let poc = 0;
  levels.forEach((l, i) => {
    if (l.ask + l.bid > levels[poc]!.ask + levels[poc]!.bid) poc = i;
  });
  return { levels, step: fp.step * k, poc };
}

class FootprintRenderer implements ICustomSeriesPaneRenderer {
  data: PaneRendererCustomData<Time, FootprintData> | null = null;
  settings: FootprintSettings = { imbalance: 3, showDelta: true };

  draw(target: Target, priceToY: PriceToCoordinateConverter) {
    target.useBitmapCoordinateSpace((scope) => {
      const data = this.data;
      if (!data || !data.visibleRange) return;
      const ctx = scope.context;
      const hr = scope.horizontalPixelRatio;
      const vr = scope.verticalPixelRatio;
      const spacing = data.barSpacing; // media px
      const { from, to } = data.visibleRange;
      for (let i = Math.max(0, from); i <= Math.min(data.bars.length - 1, to); i += 1) {
        const bar = data.bars[i]!;
        const d = bar.originalData;
        const x = bar.x * hr;
        const color = d.close >= d.open ? UP : DOWN;
        if (!d.fp || spacing < 8) {
          this.plainCandle(ctx, d, x, spacing * hr, color, priceToY, vr);
          continue;
        }
        this.footprintCandle(ctx, d, d.fp, x, spacing * hr, color, priceToY, hr, vr, spacing);
      }
    });
  }

  private plainCandle(
    ctx: CanvasRenderingContext2D,
    d: FootprintData,
    x: number,
    spacingPx: number,
    color: string,
    priceToY: PriceToCoordinateConverter,
    vr: number,
  ) {
    const yh = priceToY(d.high);
    const yl = priceToY(d.low);
    const yo = priceToY(d.open);
    const yc = priceToY(d.close);
    if (yh == null || yl == null || yo == null || yc == null) return;
    ctx.fillStyle = color;
    const wick = Math.max(1, Math.round(vr));
    ctx.fillRect(Math.round(x - wick / 2), Math.round(yh * vr), wick, Math.max(1, Math.round((yl - yh) * vr)));
    const bw = Math.max(1, Math.floor(spacingPx * 0.7));
    const top = Math.round(Math.min(yo, yc) * vr);
    ctx.fillRect(Math.round(x - bw / 2), top, bw, Math.max(1, Math.round(Math.abs(yc - yo) * vr)));
  }

  private footprintCandle(
    ctx: CanvasRenderingContext2D,
    d: FootprintData,
    fp: FootprintCandle,
    x: number,
    spacingPx: number,
    color: string,
    priceToY: PriceToCoordinateConverter,
    hr: number,
    vr: number,
    spacing: number,
  ) {
    const w = Math.max(6, Math.floor(spacingPx * 0.94));
    const left = Math.round(x - w / 2);
    const imbalance = this.settings.imbalance;

    // How tall is one base row on screen? Merge rows until each is ~12px.
    const base = fp.levels[0]!;
    const by0 = priceToY(base.price + fp.step);
    const by1 = priceToY(base.price);
    const baseH = by0 != null && by1 != null ? Math.abs(by1 - by0) : 0;
    const k = baseH > 0 ? Math.max(1, Math.ceil(12 / baseH)) : 1;
    const rows = coarsen(fp, k);
    const levels = rows.levels;
    const maxLevel = Math.max(...levels.map((l) => l.ask + l.bid), 1);

    // Row geometry: a row spans [price, price + step].
    const rowTop = (price: number) => priceToY(price + rows.step);
    const rowBottom = (price: number) => priceToY(price);

    const sample = levels[0]!;
    const sy0 = rowTop(sample.price);
    const sy1 = rowBottom(sample.price);
    const rowH = sy0 != null && sy1 != null ? Math.abs(sy1 - sy0) : 0; // media px
    const showText = spacing >= 44 && rowH >= 9;
    const fontPx = Math.min(12, Math.max(8, Math.floor(rowH - 2)));

    levels.forEach((level, idx) => {
      const yt = rowTop(level.price);
      const yb = rowBottom(level.price);
      if (yt == null || yb == null) return;
      const top = Math.round(yt * vr);
      const h = Math.max(1, Math.round((yb - yt) * vr) - (rowH > 4 ? 1 : 0));
      const total = level.ask + level.bid;
      const intensity = 0.1 + 0.5 * (total / maxLevel);
      const buyDominant = level.ask >= level.bid;
      ctx.fillStyle = buyDominant ? `rgba(8,153,129,${intensity})` : `rgba(242,54,69,${intensity})`;
      ctx.fillRect(left, top, w, h);

      if (showText) {
        const below = levels[idx - 1];
        const above = levels[idx + 1];
        const askImb = below ? level.ask >= imbalance * Math.max(below.bid, 1) && level.ask > 0 : false;
        const bidImb = above ? level.bid >= imbalance * Math.max(above.ask, 1) && level.bid > 0 : false;
        const cy = top + h / 2;
        ctx.textBaseline = 'middle';
        const mid = Math.round(x);
        const gap = 4 * hr;
        ctx.font = `${bidImb ? '700 ' : ''}${fontPx * vr}px ${FONT}`;
        ctx.textAlign = 'right';
        ctx.fillStyle = bidImb ? '#ff8a96' : '#d1d4dc';
        ctx.fillText(fmtVol(level.bid), mid - gap, cy);
        ctx.font = `${askImb ? '700 ' : ''}${fontPx * vr}px ${FONT}`;
        ctx.textAlign = 'left';
        ctx.fillStyle = askImb ? '#5eead4' : '#d1d4dc';
        ctx.fillText(fmtVol(level.ask), mid + gap, cy);
        if (bidImb || askImb) {
          ctx.lineWidth = Math.max(1, hr);
          ctx.strokeStyle = bidImb ? DOWN : UP;
          ctx.strokeRect(left + 0.5, top + 0.5, w - 1, h - 1);
        }
      }
    });

    // Point of control.
    const poc = levels[rows.poc]!;
    const pyt = rowTop(poc.price);
    const pyb = rowBottom(poc.price);
    if (pyt != null && pyb != null) {
      ctx.lineWidth = Math.max(1, Math.round(hr * 1.5));
      ctx.strokeStyle = POC;
      ctx.strokeRect(left, Math.round(pyt * vr), w, Math.max(1, Math.round((pyb - pyt) * vr)));
    }

    // Candle body + wick down the left edge, so open/close read at a glance.
    const yh = priceToY(d.high);
    const yl = priceToY(d.low);
    const yo = priceToY(d.open);
    const yc = priceToY(d.close);
    if (yh != null && yl != null && yo != null && yc != null) {
      ctx.fillStyle = color;
      const bodyW = Math.max(3, Math.round(3 * hr));
      ctx.fillRect(left - bodyW, Math.round(Math.min(yo, yc) * vr), bodyW, Math.max(1, Math.round(Math.abs(yc - yo) * vr)));
      ctx.fillRect(left - Math.round(bodyW / 2) - Math.round(hr / 2), Math.round(yh * vr), Math.max(1, Math.round(hr)), Math.round((yl - yh) * vr));
    }

    // Delta and total volume under the candle.
    if (this.settings.showDelta && spacing >= 30 && yl != null) {
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.font = `700 ${11 * vr}px ${FONT}`;
      ctx.fillStyle = fp.delta >= 0 ? '#5eead4' : '#ff8a96';
      const base = Math.round(yl * vr) + 4 * vr;
      ctx.fillText(`Δ ${fp.delta >= 0 ? '+' : '−'}${fmtVol(Math.abs(fp.delta))}`, Math.round(x), base);
      ctx.font = `${11 * vr}px ${FONT}`;
      ctx.fillStyle = '#b2b5be';
      ctx.fillText(fmtVol(fp.total), Math.round(x), base + 13 * vr);
    }
  }
}

export class FootprintView implements ICustomSeriesPaneView<Time, FootprintData, CustomSeriesOptions> {
  private readonly rend = new FootprintRenderer();

  setSettings(settings: FootprintSettings) {
    this.rend.settings = settings;
  }

  renderer() {
    return this.rend;
  }

  update(data: PaneRendererCustomData<Time, FootprintData>) {
    this.rend.data = data;
  }

  priceValueBuilder(row: FootprintData): CustomSeriesPricePlotValues {
    return [row.high, row.low, row.close];
  }

  isWhitespace(row: FootprintData | CustomSeriesWhitespaceData<Time>): row is CustomSeriesWhitespaceData<Time> {
    return (row as FootprintData).close === undefined;
  }

  defaultOptions(): CustomSeriesOptions {
    return { ...customSeriesDefaultOptions };
  }
}
