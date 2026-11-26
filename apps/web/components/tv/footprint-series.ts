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
import type { FootprintSweep } from '../../lib/tv/footprint';
import type { FootprintCandle } from '../../lib/tv/types';
import { diagonalImbalances, stackedOnly as stackedRows } from '../../lib/tv/stacked-imbalance';

const FONT = '-apple-system, BlinkMacSystemFont, "Trebuchet MS", Roboto, Ubuntu, sans-serif';
const UP = '#089981';
const DOWN = '#f23645';
const POC_FILL = '#d1d4dc';
const VA_LINE = 'rgba(255, 255, 255, 0.85)';
/** POC row outline in the profile style. */
const POC_LINE = '#ffe600';
/** Big-order sweep bars and bubbles: yellow for buying, red for selling (the order-flow platforms' colours). */
const SWEEP_COLOR: Record<FootprintSweep['side'], string> = {
  buy: '245,217,10',
  sell: '242,54,69',
};
/** Height of one row of the Delta / Volume strip at the bottom of the pane, css px. */
export const STRIP_ROW = 20;
/** Same magenta as the Delta Divergence badges. */
const DIVERGENCE = '#e879f9';

interface StripCol {
  left: number;
  w: number;
  fp: FootprintCandle;
  up: boolean;
  down: boolean;
}

export interface FootprintData extends CustomData<Time> {
  open: number;
  high: number;
  low: number;
  close: number;
  fp: FootprintCandle | null;
  /** Estimated big-order sweeps in this candle (profile style), from lib/tv/footprint.ts footprintSweeps. */
  sweeps?: FootprintSweep[];
}

export interface FootprintSettings {
  /** Diagonal imbalance threshold, e.g. 3 = 300%. */
  imbalance: number;
  showDelta: boolean;
  /** Share of a candle's volume the value area holds, in percent (TradingView's default is 70). */
  valueAreaPct: number;
  /** Mark imbalanced rows (edge bar + bold text). */
  highlight: boolean;
  /** 0 = off; N >= 2 = only mark imbalances that stack N or more rows in a row on the same side. */
  stackedLevels: number;
  /** profile: one column of `bid x ask` over a per-candle volume profile, body as an outline (order-flow platforms);
   *  split: bid cells left of the candle, ask cells right (TradingView). */
  style: FootprintStyle;
  /** Estimated big-order sweeps (profile style): 1-minute volume at least this many times the last hour's median;
   *  0 = off. The sweeps themselves come with the data (FootprintData.sweeps). */
  sweepX: number;
}

export type FootprintStyle = 'profile' | 'split';

export const DEFAULT_FOOTPRINT_SETTINGS: FootprintSettings = {
  imbalance: 3,
  showDelta: true,
  valueAreaPct: 70,
  highlight: true,
  stackedLevels: 0,
  style: 'profile',
  sweepX: 4,
};

type Target = Parameters<ICustomSeriesPaneRenderer['draw']>[0];

const fmtVol = (v: number) =>
  v >= 1e6
    ? `${(v / 1e6).toFixed(1)}M`
    : v >= 1e4
      ? `${(v / 1e3).toFixed(0)}K`
      : v >= 1e3
        ? `${(v / 1e3).toFixed(1)}K`
        : `${Math.round(v)}`;

/** Narrow columns: whole thousands / millions ("1K", "12K", "2M"), as order-flow platforms print them. */
const fmtCompact = (v: number) =>
  v >= 1e6 ? `${Math.round(v / 1e6)}M` : v >= 1e3 ? `${Math.round(v / 1e3)}K` : `${Math.round(v)}`;

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

/** The rows as drawn: base rows merged until each is ~12px tall on screen. */
function displayRows(fp: FootprintCandle, priceToY: PriceToCoordinateConverter): Rows {
  const base = fp.levels[0]!;
  const by0 = priceToY(base.price + fp.step);
  const by1 = priceToY(base.price);
  const baseH = by0 != null && by1 != null ? Math.abs(by1 - by0) : 0;
  return coarsen(fp, baseH > 0 ? Math.max(1, Math.ceil(12 / baseH)) : 1);
}

/**
 * The candle's value area: rows around the POC, taking the busier neighbour each time, until they hold
 * `share` (0-1) of the candle's volume. Returns the lowest and highest row index, or null when there are
 * too few rows for it to say anything.
 */
function valueArea(rows: Rows, share: number): { lo: number; hi: number } | null {
  const { levels, poc } = rows;
  if (levels.length < 3) return null;
  const vol = (i: number) => levels[i]!.bid + levels[i]!.ask;
  let total = 0;
  for (let i = 0; i < levels.length; i += 1) total += vol(i);
  if (!(total > 0)) return null;
  let lo = poc;
  let hi = poc;
  let held = vol(poc);
  while (held < share * total && (lo > 0 || hi < levels.length - 1)) {
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
  settings: FootprintSettings = DEFAULT_FOOTPRINT_SETTINGS;
  chart: IChartApi | null = null;
  /** The largest sweep on screen this frame (bubble size). */
  private biggestSweep = 0;

  draw(target: Target, priceToY: PriceToCoordinateConverter) {
    target.useBitmapCoordinateSpace((scope) => {
      const data = this.data;
      if (!data || !data.visibleRange) return;
      const ctx = scope.context;
      const hr = scope.horizontalPixelRatio;
      const vr = scope.verticalPixelRatio;
      const spacing = data.barSpacing; // media px
      const { from, to } = data.visibleRange;
      const cols: StripCol[] = [];
      // Bubbles are sized against the biggest cluster on screen.
      this.biggestSweep = 0;
      for (let i = Math.max(0, from); i <= Math.min(data.bars.length - 1, to); i += 1) {
        for (const sw of data.bars[i]!.originalData.sweeps ?? []) {
          this.biggestSweep = Math.max(this.biggestSweep, sw.volume);
        }
      }
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
        const col =
          this.settings.style === 'split'
            ? this.footprintCandle(ctx, d, d.fp, x, spacing * hr, color, priceToY, hr, vr, spacing)
            : this.profileCandle(ctx, d, d.fp, x, spacing * hr, color, priceToY, hr, vr);
        cols.push({ ...col, fp: d.fp, up: d.close > d.open, down: d.close < d.open });
      }
      if (this.settings.showDelta && cols.length) {
        this.summaryStrip(ctx, cols, scope.bitmapSize.width, scope.bitmapSize.height, hr, vr);
      }
    });
  }

  /**
   * The order-flow platforms' look: one column per candle with `bid x ask` on each row, a blue bar behind each row
   * as long as its volume (the candle's own volume profile), the body as an outline from the open row to the close
   * row, the wick as a thin line down the middle, and the POC row boxed in yellow. Imbalances print bold, tinted
   * by side.
   */
  private profileCandle(
    ctx: CanvasRenderingContext2D,
    d: FootprintData,
    fp: FootprintCandle,
    x: number,
    spacingPx: number,
    color: string,
    priceToY: PriceToCoordinateConverter,
    hr: number,
    vr: number,
  ) {
    const w = Math.max(6, Math.floor(spacingPx * 0.94));
    const left = Math.round(x - w / 2);
    const mid = Math.round(x);

    const rows = displayRows(fp, priceToY);
    const levels = rows.levels;
    const step = rows.step;
    const rowTop = (price: number) => priceToY(price + step);
    const rowBottom = (price: number) => priceToY(price);
    const sy0 = rowTop(levels[0]!.price);
    const sy1 = rowBottom(levels[0]!.price);
    const rowH = sy0 != null && sy1 != null ? Math.abs(sy1 - sy0) : 0; // media px
    const maxRow = Math.max(...levels.map((l) => l.bid + l.ask), 1);

    const flags = diagonalImbalances(levels, this.settings.imbalance, step);
    const stackedOnly = (f: boolean[]) => stackedRows(f, this.settings.stackedLevels);
    const askMarks = this.settings.highlight ? stackedOnly(flags.ask) : flags.ask.map(() => false);
    const bidMarks = this.settings.highlight ? stackedOnly(flags.bid) : flags.bid.map(() => false);

    // Text on each row whose "bid x ask" fits the column (measured bold, the widest it can print).
    const fontPx = Math.min(12, Math.max(8, Math.floor(rowH - 2)));
    ctx.font = `700 ${fontPx * vr}px ${FONT}`;
    const gap = 3 * hr;
    const xW = ctx.measureText('x').width;
    const half = (w - xW) / 2 - gap - 2 * hr; // room for one side's number
    // Each row's numbers at the largest font (down to 7px) that fits the column; if the full form ("1.4K") is too
    // wide even then, whole thousands ("1K") as the order-flow platforms print them in narrow columns.
    const fitText = (l: { bid: number; ask: number }): { px: number; bid: string; ask: string } | null => {
      if (rowH < 9) return null;
      for (const fmt of [fmtVol, fmtCompact]) {
        const bid = fmt(l.bid);
        const ask = fmt(l.ask);
        const widest = Math.max(ctx.measureText(bid).width, ctx.measureText(ask).width);
        const px = Math.min(fontPx, Math.floor((fontPx * half) / Math.max(widest, 1)));
        if (px >= 7) return { px, bid, ask };
      }
      return null;
    };
    const rowText = levels.map(fitText);
    const showText = rowText.some(Boolean);

    // 1. Volume profile: a bar from the left edge, as long as the row's volume against the candle's busiest row.
    const box = (price: number) => {
      const yt = rowTop(price);
      const yb = rowBottom(price);
      if (yt == null || yb == null) return null;
      const top = Math.round(yt * vr);
      return { top, h: Math.max(1, Math.round((yb - yt) * vr) - (rowH > 4 ? 1 : 0)) };
    };
    // Estimated big-order sweeps: the profile bars of the rows a spike minute traded through take its colour (yellow
    // buy, red sell), each still as long as its row's volume, so the run reads as a coloured slice of the profile with
    // its shape (the volume at each level); the minute's volume goes in a bubble (step 7).
    const sweeps = this.settings.sweepX > 0 ? (d.sweeps ?? []) : [];
    const inSweep = levels.map(
      (l) => [...sweeps].reverse().find((sw) => l.price <= sw.high + 1e-9 && l.price + step > sw.low + 1e-9) ?? null,
    );
    const barW = levels.map((level) => Math.max(1, Math.round(((level.bid + level.ask) / maxRow) * w)));
    levels.forEach((level, idx) => {
      const b = box(level.price);
      if (!b) return;
      const share = (level.bid + level.ask) / maxRow;
      const sw = inSweep[idx];
      ctx.fillStyle = sw
        ? `rgba(${SWEEP_COLOR[sw.side]},0.92)`
        : `rgba(41,98,255,${(0.16 + 0.5 * share).toFixed(3)})`;
      ctx.fillRect(left, b.top, barW[idx]!, b.h);
    });

    // 2. Wick: only outside the body (high down to the body's top, body's bottom down to the low); inside the body
    // the volume numbers stay unobstructed.
    const rowOf = (price: number) => Math.floor(price / step + 1e-9) * step;
    const bodyTop = rowTop(rowOf(Math.max(d.open, d.close)));
    const bodyBottom = rowBottom(rowOf(Math.min(d.open, d.close)));
    const yh = priceToY(d.high);
    const yl = priceToY(d.low);
    if (yh != null && yl != null) {
      ctx.fillStyle = color;
      const wick = Math.max(2, Math.round(2 * hr));
      const wx = mid - Math.floor(wick / 2);
      const segment = (from: number, to: number) => {
        const y0 = Math.round(from * vr);
        const y1 = Math.round(to * vr);
        if (y1 > y0) ctx.fillRect(wx, y0, wick, y1 - y0);
      };
      if (bodyTop != null && bodyBottom != null) {
        segment(yh, bodyTop);
        segment(bodyBottom, yl);
      } else {
        segment(yh, yl);
      }
    }

    // 3. Body: an outline from the open's row to the close's row, the full column wide.
    if (bodyTop != null && bodyBottom != null) {
      const line = Math.max(2, Math.round(2.5 * hr));
      ctx.strokeStyle = color;
      ctx.lineWidth = line;
      const t = Math.round(bodyTop * vr) + line / 2;
      const b = Math.round(bodyBottom * vr) - line / 2;
      ctx.strokeRect(left + line / 2, t, w - line, Math.max(line, b - t));
    }

    // 4. POC row: boxed in yellow.
    const poc = levels[rows.poc];
    const pb = poc ? box(poc.price) : null;
    if (pb) {
      const line = Math.max(2, Math.round(2 * hr));
      const rx = left + 1.5 * line;
      const ry = pb.top + line / 2;
      const rw = w - 3 * line;
      const rh = Math.max(line, pb.h - line);
      // A dark halo under the outline keeps it readable on top of the yellow buy-sweep bars; on a buy-sweep row
      // itself the outline turns white, since yellow on yellow disappears.
      const onBuySweep = inSweep[rows.poc]?.side === 'buy';
      ctx.strokeStyle = '#131722';
      ctx.lineWidth = line + 2 * Math.max(1, Math.round(hr));
      ctx.strokeRect(rx, ry, rw, rh);
      ctx.strokeStyle = onBuySweep ? '#ffffff' : POC_LINE;
      ctx.lineWidth = line;
      ctx.strokeRect(rx, ry, rw, rh);
    }

    // 5. Value area: dotted lines at its top and bottom row edges.
    const va = valueArea(rows, Math.min(100, Math.max(1, this.settings.valueAreaPct)) / 100);
    if (va) {
      ctx.save();
      ctx.strokeStyle = VA_LINE;
      ctx.lineWidth = Math.max(1, Math.round(hr));
      ctx.setLineDash([3 * hr, 3 * hr]);
      for (const y of [rowTop(levels[va.hi]!.price), rowBottom(levels[va.lo]!.price)]) {
        if (y == null) continue;
        const py = Math.round(y * vr) + 0.5;
        ctx.beginPath();
        ctx.moveTo(left, py);
        ctx.lineTo(left + w, py);
        ctx.stroke();
      }
      ctx.restore();
    }

    // 6. "bid x ask" on each row. On a sweep row the text is drawn twice: in the usual colours, then again clipped
    // to the coloured bar in plain dark (on yellow) or white (on red), so every glyph stays readable whether it sits
    // on the bar or past its end.
    if (showText) {
      ctx.textBaseline = 'middle';
      const paint = (idx: number, cy: number, onSweep: FootprintSweep['side'] | null) => {
        const txt = rowText[idx]!;
        const px = txt.px;
        const bidImb = bidMarks[idx]!;
        const askImb = askMarks[idx]!;
        const ink = onSweep === 'buy' ? '#131722' : '#ffffff';
        ctx.textAlign = 'right';
        ctx.font = `${bidImb ? '700 ' : ''}${px * vr}px ${FONT}`;
        ctx.fillStyle = onSweep ? ink : bidImb ? '#ff8a96' : '#d1d4dc';
        ctx.fillText(txt.bid, mid - xW / 2 - gap, cy);
        ctx.textAlign = 'center';
        ctx.font = `${px * vr}px ${FONT}`;
        ctx.fillStyle = onSweep === 'buy' ? '#3a3e4a' : onSweep ? '#f2f2f2' : '#787b86';
        ctx.fillText('x', mid, cy);
        ctx.textAlign = 'left';
        ctx.font = `${askImb ? '700 ' : ''}${px * vr}px ${FONT}`;
        ctx.fillStyle = onSweep ? ink : askImb ? '#5eead4' : '#d1d4dc';
        ctx.fillText(txt.ask, mid + xW / 2 + gap, cy);
      };
      levels.forEach((level, idx) => {
        const b = box(level.price);
        if (!b || !rowText[idx]) return;
        const cy = b.top + b.h / 2;
        paint(idx, cy, null);
        const sw = inSweep[idx];
        if (!sw) return;
        ctx.save();
        ctx.beginPath();
        ctx.rect(left, b.top, barW[idx]!, b.h);
        ctx.clip();
        paint(idx, cy, sw.side);
        ctx.restore();
      });
    }

    // 7. Sweep bubbles: the minute's volume in a see-through circle at the middle of its span, sized against the
    // biggest on screen.
    for (const sw of sweeps) {
      const yt = priceToY(sw.high);
      const yb = priceToY(sw.low);
      if (yt == null || yb == null || !(this.biggestSweep > 0)) continue;
      const cy = ((yt + yb) / 2) * vr;
      const r = (9 + 13 * Math.sqrt(Math.min(1, sw.volume / this.biggestSweep))) * Math.min(hr, vr);
      ctx.beginPath();
      ctx.arc(mid, cy, r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${SWEEP_COLOR[sw.side]},0.45)`;
      ctx.fill();
      ctx.font = `700 ${Math.max(9, Math.min(15, (r / Math.min(hr, vr)) * 0.75)) * vr}px ${FONT}`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = '#ffffff';
      ctx.fillText(fmtVol(sw.volume), mid, cy);
    }
    return { left, w };
  }

  /**
   * Delta and Volume for every candle in a strip along the bottom of the pane, one cell under each candle, with
   * the row names pinned on the left (the order-flow platforms' summary table). Delta cells are tinted by
   * size against the biggest delta on screen; a blue top edge marks a candle built from recorded ticks, and a
   * magenta outline (plus a notch above) a candle whose delta disagrees with its colour.
   */
  private summaryStrip(
    ctx: CanvasRenderingContext2D,
    cols: StripCol[],
    width: number,
    height: number,
    hr: number,
    vr: number,
  ) {
    const rowH = Math.round(STRIP_ROW * vr);
    const top = height - 2 * rowH;
    const gap = Math.max(1, Math.round(hr));
    const maxDelta = Math.max(...cols.map((c) => Math.abs(c.fp.delta)), 1);
    ctx.fillStyle = 'rgba(19,23,34,0.92)';
    ctx.fillRect(0, top - gap, width, 2 * rowH + gap);
    ctx.font = `${10 * vr}px ${FONT}`;
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'center';
    for (const { left, w, fp, up, down } of cols) {
      const x = left + gap;
      const cw = Math.max(1, w - gap);
      const strength = Math.abs(fp.delta) / maxDelta;
      ctx.fillStyle =
        fp.delta >= 0
          ? `rgba(8,153,129,${0.25 + 0.6 * strength})`
          : `rgba(242,54,69,${0.25 + 0.6 * strength})`;
      ctx.fillRect(x, top, cw, rowH - gap);
      ctx.fillStyle = '#1e222d';
      ctx.fillRect(x, top + rowH, cw, rowH - gap);
      // Delta divergence: a green candle sellers out-traded (delta < 0) or a red one buyers did (delta > 0).
      if ((up && fp.delta < 0) || (down && fp.delta > 0)) {
        const line = Math.max(2, Math.round(1.5 * hr));
        ctx.strokeStyle = DIVERGENCE;
        ctx.lineWidth = line;
        ctx.strokeRect(x + line / 2, top + line / 2, cw - line, rowH - gap - line);
        // a small notch above the cell, so it can be spotted when the cell is too narrow for text
        ctx.fillStyle = DIVERGENCE;
        ctx.beginPath();
        ctx.moveTo(x + cw / 2 - 4 * hr, top - gap);
        ctx.lineTo(x + cw / 2 + 4 * hr, top - gap);
        ctx.lineTo(x + cw / 2, top - gap - 5 * vr);
        ctx.closePath();
        ctx.fill();
      }
      if (fp.source === 'ticks') {
        ctx.fillStyle = '#2962ff';
        ctx.fillRect(x, top, cw, Math.max(1, Math.round(2 * vr)));
      }
      const deltaText = `${fp.delta >= 0 ? '' : '−'}${fmtVol(Math.abs(fp.delta))}`;
      const volText = fmtVol(fp.total);
      const cx = x + cw / 2;
      if (ctx.measureText(deltaText).width + 2 * hr <= cw) {
        ctx.fillStyle = '#ffffff';
        ctx.fillText(deltaText, cx, top + (rowH - gap) / 2);
      }
      if (ctx.measureText(volText).width + 2 * hr <= cw) {
        ctx.fillStyle = '#d1d4dc';
        ctx.fillText(volText, cx, top + rowH + (rowH - gap) / 2);
      }
    }
    // Row names over the left edge, on a solid background so the cells scrolling under them don't show through.
    const labelW = Math.round(52 * hr);
    ctx.fillStyle = '#131722';
    ctx.fillRect(0, top - gap, labelW, 2 * rowH + gap);
    ctx.fillStyle = '#b2b5be';
    ctx.textAlign = 'left';
    ctx.font = `${11 * vr}px ${FONT}`;
    ctx.fillText('Delta', 6 * hr, top + (rowH - gap) / 2);
    ctx.fillText('Volume', 6 * hr, top + rowH + (rowH - gap) / 2);
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

    // Diagonal imbalance: asks against the bids one row down, bids against the asks one row up; stacked levels keep
    // only runs of at least N rows on the same side (same rule as the Stacked Imbalances indicator).
    const flags = diagonalImbalances(levels, imbalance, rows.step);
    const askFlags = flags.ask;
    const bidFlags = flags.bid;
    const stackedOnly = (f: boolean[]) => stackedRows(f, this.settings.stackedLevels);
    const askMarks = this.settings.highlight ? stackedOnly(askFlags) : askFlags.map(() => false);
    const bidMarks = this.settings.highlight ? stackedOnly(bidFlags) : bidFlags.map(() => false);

    levels.forEach((level, idx) => {
      const yt = rowTop(level.price);
      const yb = rowBottom(level.price);
      if (yt == null || yb == null) return;
      const top = Math.round(yt * vr);
      const h = Math.max(1, Math.round((yb - yt) * vr) - (rowH > 4 ? 1 : 0));
      const isPoc = idx === rows.poc;
      const askImb = askMarks[idx]!;
      const bidImb = bidMarks[idx]!;
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
    const va = valueArea(rows, Math.min(100, Math.max(1, this.settings.valueAreaPct)) / 100);
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
    return { left, w };
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
