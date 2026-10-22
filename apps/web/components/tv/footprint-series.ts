import {
  type CustomData,
  type CustomSeriesOptions,
  type CustomSeriesPricePlotValues,
  type CustomSeriesWhitespaceData,
  type IChartApi,
  type Logical,
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
const POC_FILL = '#d1d4dc';
const VA_LINE = 'rgba(255, 255, 255, 0.85)';
/** Share of a candle's volume the value area holds. */
const VALUE_AREA = 0.7;

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
  v >= 1e6
    ? `${(v / 1e6).toFixed(1)}M`
    : v >= 1e4
      ? `${(v / 1e3).toFixed(0)}K`
      : v >= 1e3
        ? `${(v / 1e3).toFixed(1)}K`
        : `${Math.round(v)}`;

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

/**
 * The candle's value area: rows around the POC, taking the busier neighbour each time, until they hold
 * `VALUE_AREA` of the candle's volume. Returns the lowest and highest row index, or null when there are
 * too few rows for it to say anything.
 */
function valueArea(rows: Rows): { lo: number; hi: number } | null {
  const { levels, poc } = rows;
  if (levels.length < 3) return null;
  const vol = (i: number) => levels[i]!.bid + levels[i]!.ask;
  let total = 0;
  for (let i = 0; i < levels.length; i += 1) total += vol(i);
  if (!(total > 0)) return null;
  let lo = poc;
  let hi = poc;
  let held = vol(poc);
  while (held < VALUE_AREA * total && (lo > 0 || hi < levels.length - 1)) {
    const up = hi < levels.length - 1 ? vol(hi + 1) : -1;
    const down = lo > 0 ? vol(lo - 1) : -1;
    if (up >= down) {
      hi += 1;
      held += up;
    } else {
      lo -= 1;
      held += down;
    }
  }
  return { lo, hi };
}

class FootprintRenderer implements ICustomSeriesPaneRenderer {
  data: PaneRendererCustomData<Time, FootprintData> | null = null;
  settings: FootprintSettings = { imbalance: 3, showDelta: true };
  chart: IChartApi | null = null;

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
        // `bar.x` is only refreshed for bars on screen: one just past the edge of the visible range keeps the x it
        // had in an earlier view and would be drawn there as a stray candle. The time scale always knows (`bar.time` is the index).
        const tx = this.chart ? this.chart.timeScale().logicalToCoordinate(bar.time as unknown as Logical) : bar.x;
        if (tx == null) continue;
        const x = tx * hr;
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
    ctx.fillRect(
      Math.round(x - wick / 2),
      Math.round(yh * vr),
      wick,
      Math.max(1, Math.round((yl - yh) * vr)),
    );
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
    const mid = Math.round(x);
    const imbalance = this.settings.imbalance;
    // The candle runs down the middle; bid cells sit on its left, ask cells on its right.
    // About 16% of the footprint's width, between 8 and 14 css px: TradingView's body is a solid block, not a sliver.
    const bodyW = Math.round(Math.min(14 * hr, Math.max(8 * hr, w * 0.16)));
    const half = Math.floor(bodyW / 2);
    const leftW = Math.max(1, mid - half - left);
    const rightX = mid + half;
    const rightW = Math.max(1, left + w - rightX);

    // How tall is one base row on screen? Merge rows until each is ~12px.
    const base = fp.levels[0]!;
    const by0 = priceToY(base.price + fp.step);
    const by1 = priceToY(base.price);
    const baseH = by0 != null && by1 != null ? Math.abs(by1 - by0) : 0;
    const k = baseH > 0 ? Math.max(1, Math.ceil(12 / baseH)) : 1;
    const rows = coarsen(fp, k);
    const levels = rows.levels;
    const maxSide = Math.max(...levels.map((l) => Math.max(l.ask, l.bid)), 1);

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
      const isPoc = idx === rows.poc;
      const below = levels[idx - 1];
      const above = levels[idx + 1];
      // Diagonal imbalance: asks against the bids one row down, bids against the asks one row up.
      const askImb = below ? level.ask > 0 && level.ask >= imbalance * Math.max(below.bid, 1) : false;
      const bidImb = above ? level.bid > 0 && level.bid >= imbalance * Math.max(above.ask, 1) : false;
      const tint = (side: number) => 0.12 + 0.6 * (side / maxSide);

      ctx.fillStyle = isPoc ? POC_FILL : `rgba(242,54,69,${tint(level.bid)})`;
      ctx.fillRect(left, top, leftW, h);
      ctx.fillStyle = isPoc ? POC_FILL : `rgba(8,153,129,${tint(level.ask)})`;
      ctx.fillRect(rightX, top, rightW, h);

      // Imbalance: a small bar on the outer edge of the row (red on the bid side, green on the ask side).
      // Rows that follow one another join into one bar.
      if (bidImb || askImb) {
        const barW = Math.max(2, Math.round(3 * hr));
        const barH = Math.max(1, Math.round((yb - yt) * vr));
        if (bidImb) {
          ctx.fillStyle = DOWN;
          ctx.fillRect(left, top, barW, barH);
        }
        if (askImb) {
          ctx.fillStyle = UP;
          ctx.fillRect(left + w - barW, top, barW, barH);
        }
      }

      if (showText) {
        const cy = top + h / 2;
        ctx.textBaseline = 'middle';
        const gap = 4 * hr;
        ctx.font = `${bidImb ? '700 ' : ''}${fontPx * vr}px ${FONT}`;
        ctx.textAlign = 'right';
        ctx.fillStyle = bidImb ? '#ffffff' : isPoc ? '#131722' : '#d1d4dc';
        ctx.fillText(fmtVol(level.bid), mid - half - gap, cy);
        ctx.font = `${askImb ? '700 ' : ''}${fontPx * vr}px ${FONT}`;
        ctx.textAlign = 'left';
        ctx.fillStyle = askImb ? '#ffffff' : isPoc ? '#131722' : '#d1d4dc';
        ctx.fillText(fmtVol(level.ask), rightX + gap, cy);
      }
    });

    // Value area: dotted lines across the candle at the top of its highest row (VAH) and the bottom of its lowest (VAL).
    const va = valueArea(rows);
    if (va) {
      const yVah = rowTop(levels[va.hi]!.price);
      const yVal = rowBottom(levels[va.lo]!.price);
      ctx.save();
      ctx.strokeStyle = VA_LINE;
      ctx.lineWidth = Math.max(1, Math.round(hr));
      ctx.setLineDash([3 * hr, 3 * hr]);
      for (const y of [yVah, yVal]) {
        if (y == null) continue;
        const py = Math.round(y * vr) + 0.5;
        ctx.beginPath();
        ctx.moveTo(left, py);
        ctx.lineTo(left + w, py);
        ctx.stroke();
      }
      ctx.restore();
    }

    // Candle through the middle: wick high to low, body open to close.
    const yh = priceToY(d.high);
    const yl = priceToY(d.low);
    const yo = priceToY(d.open);
    const yc = priceToY(d.close);
    if (yh != null && yl != null && yo != null && yc != null) {
      const wick = Math.max(2, Math.round(1.5 * hr));
      ctx.fillStyle = color;
      ctx.fillRect(mid - Math.floor(wick / 2), Math.round(yh * vr), wick, Math.max(1, Math.round((yl - yh) * vr)));
      const bodyTop = Math.round(Math.min(yo, yc) * vr);
      const bodyH = Math.max(1, Math.round(Math.abs(yc - yo) * vr));
      ctx.fillRect(mid - half, bodyTop, bodyW, bodyH);
      ctx.lineWidth = Math.max(1, Math.round(hr * 0.8));
      ctx.strokeStyle = '#131722';
      ctx.strokeRect(mid - half + 0.5, bodyTop + 0.5, bodyW - 1, Math.max(1, bodyH - 1));
    }

    // Bid / ask totals and the Delta / Total box under the candle.
    if (this.settings.showDelta && spacing >= 30 && yl != null) {
      // Rows span [price, price + step], so the bottom row reaches below the candle's low: start under it.
      const bottomEdge = priceToY(levels[0]!.price);
      const baseY = Math.round(Math.max(yl, bottomEdge ?? yl) * vr) + 4 * vr;
      const deltaText = `${fp.delta >= 0 ? '+' : '−'}${fmtVol(Math.abs(fp.delta))}`;
      const deltaColor = fp.delta >= 0 ? '#5eead4' : '#ff8a96';
      ctx.textBaseline = 'top';
      if (w < 64 * hr) {
        // Narrow candle: two plain lines.
        ctx.textAlign = 'center';
        ctx.font = `700 ${11 * vr}px ${FONT}`;
        ctx.fillStyle = deltaColor;
        ctx.fillText(`Δ ${deltaText}`, mid, baseY);
        ctx.font = `${11 * vr}px ${FONT}`;
        ctx.fillStyle = '#b2b5be';
        ctx.fillText(fmtVol(fp.total), mid, baseY + 13 * vr);
        return;
      }
      let bidSum = 0;
      let askSum = 0;
      for (const l of fp.levels) {
        bidSum += l.bid;
        askSum += l.ask;
      }
      ctx.font = `${10.5 * vr}px ${FONT}`;
      ctx.textAlign = 'right';
      ctx.fillStyle = DOWN;
      ctx.fillText(fmtVol(bidSum), mid - half - 4 * hr, baseY);
      ctx.textAlign = 'left';
      ctx.fillStyle = UP;
      ctx.fillText(fmtVol(askSum), rightX + 4 * hr, baseY);

      const boxY = baseY + 15 * vr;
      const boxH = 36 * vr;
      const pad = 7 * hr;
      ctx.beginPath();
      const r = 4 * Math.min(hr, vr);
      ctx.moveTo(left + r, boxY);
      ctx.arcTo(left + w, boxY, left + w, boxY + boxH, r);
      ctx.arcTo(left + w, boxY + boxH, left, boxY + boxH, r);
      ctx.arcTo(left, boxY + boxH, left, boxY, r);
      ctx.arcTo(left, boxY, left + w, boxY, r);
      ctx.closePath();
      ctx.fillStyle = 'rgba(19,23,34,0.94)';
      ctx.fill();
      ctx.lineWidth = Math.max(1, hr);
      ctx.strokeStyle = '#2a2e39';
      ctx.stroke();
      ctx.font = `${11 * vr}px ${FONT}`;
      ctx.fillStyle = '#d1d4dc';
      ctx.textAlign = 'left';
      ctx.fillText('Delta', left + pad, boxY + 5 * vr);
      ctx.fillText('Total', left + pad, boxY + 20 * vr);
      ctx.font = `700 ${12 * vr}px ${FONT}`;
      ctx.textAlign = 'right';
      ctx.fillStyle = deltaColor;
      ctx.fillText(deltaText, left + w - pad, boxY + 4.5 * vr);
      ctx.fillStyle = '#ffffff';
      ctx.fillText(fmtVol(fp.total), left + w - pad, boxY + 19.5 * vr);
    }
  }
}

export class FootprintView implements ICustomSeriesPaneView<
  Time,
  FootprintData,
  CustomSeriesOptions
> {
  private readonly rend = new FootprintRenderer();

  setSettings(settings: FootprintSettings) {
    this.rend.settings = settings;
  }

  setChart(chart: IChartApi) {
    this.rend.chart = chart;
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

  isWhitespace(
    row: FootprintData | CustomSeriesWhitespaceData<Time>,
  ): row is CustomSeriesWhitespaceData<Time> {
    return (row as FootprintData).close === undefined;
  }

  defaultOptions(): CustomSeriesOptions {
    return { ...customSeriesDefaultOptions };
  }
}
