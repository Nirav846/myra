import { Candle } from '../core/technical-analysis/types';

export interface AxisRefs {
  xaxis: any;
  yaxis: any;
}

export interface DataCoords {
  dataX: number;
  dataY: number | null;
}

export function getGraphDiv(plotRef: React.RefObject<any>): any | null {
  return plotRef?.current?.el || plotRef?.current || null;
}

export function getAxisRefs(gd: any): AxisRefs | null {
  if (!gd || !gd._fullLayout) return null;
  return {
    xaxis: gd._fullLayout.xaxis,
    yaxis: gd._fullLayout.yaxis,
  };
}

export function clientToDataCoords(
  gd: any,
  clientX: number,
  clientY: number,
): DataCoords | null {
  if (!gd || !gd._fullLayout) return null;

  const rect = gd.getBoundingClientRect();
  const xaxis = gd._fullLayout.xaxis;
  const yaxis = gd._fullLayout.yaxis;

  if (!xaxis || !yaxis) return null;

  const graphX = clientX - rect.left;
  const graphY = clientY - rect.top;

  const axisX = graphX - xaxis._offset;
  const axisY = graphY - yaxis._offset;

  const dataX = xaxis.p2d(axisX);
  const dataY = yaxis.p2d(axisY);

  if (dataX == null || isNaN(dataX)) return null;

  return { dataX, dataY: dataY != null && !isNaN(dataY) ? dataY : null };
}

export function dataXToClientX(
  gd: any,
  dataX: number,
): number | null {
  if (!gd || !gd._fullLayout) return null;

  const rect = gd.getBoundingClientRect();
  const xaxis = gd._fullLayout.xaxis;
  if (!xaxis) return null;

  const pixelInAxis = xaxis.d2p(dataX);
  if (pixelInAxis == null || isNaN(pixelInAxis)) return null;

  return rect.left + xaxis._offset + pixelInAxis;
}

export function snapToCandleIndex(dataX: number, dataLength: number): number {
  return Math.max(0, Math.min(dataLength - 1, Math.round(dataX)));
}

export function getCandleCenterX(
  gd: any,
  index: number,
): number | null {
  return dataXToClientX(gd, index);
}

export function getVisibleIndexRange(
  gd: any,
  dataLength: number,
): { start: number; end: number } {
  if (!gd || !gd._fullLayout || !gd._fullLayout.xaxis) {
    return { start: 0, end: dataLength - 1 };
  }
  const xaxis = gd._fullLayout.xaxis;
  const range = xaxis._r || xaxis.range;
  if (range && range.length === 2) {
    return {
      start: Math.max(0, Math.floor(range[0])),
      end: Math.min(dataLength - 1, Math.ceil(range[1])),
    };
  }
  return { start: 0, end: dataLength - 1 };
}

export function nearestVisibleCandle(
  gd: any,
  dataX: number,
  dataLength: number,
): number {
  const { start, end } = getVisibleIndexRange(gd, dataLength);
  const snapped = Math.round(dataX);
  return Math.max(start, Math.min(end, snapped));
}

export function getPriceAtMouseY(
  gd: any,
  clientY: number,
): number | null {
  if (!gd || !gd._fullLayout) return null;

  const rect = gd.getBoundingClientRect();
  const yaxis = gd._fullLayout.yaxis;
  if (!yaxis) return null;

  const graphY = clientY - rect.top;
  const axisY = graphY - yaxis._offset;
  const price = yaxis.p2d(axisY);

  return price != null && !isNaN(price) ? price : null;
}

export function getPlotBoundingBox(gd: any): DOMRect | null {
  if (!gd || !gd._fullLayout) return null;
  const xaxis = gd._fullLayout.xaxis;
  const yaxis = gd._fullLayout.yaxis;
  if (!xaxis || !yaxis) return null;

  const rect = gd.getBoundingClientRect();
  return new DOMRect(
    rect.left + xaxis._offset,
    rect.top + yaxis._offset,
    xaxis._length,
    yaxis._length,
  );
}

export interface CandlestickDataArrays {
  dates: string[];
  opens: number[];
  highs: number[];
  lows: number[];
  closes: number[];
  volumes: number[];
  deliveryPct: number[];
}

export function extractCandleData(data: Candle[]): CandlestickDataArrays {
  return {
    dates: data.map(d => d.date),
    opens: data.map(d => d.open),
    highs: data.map(d => d.high),
    lows: data.map(d => d.low),
    closes: data.map(d => d.close),
    volumes: data.map(d => {
      const vol = d.volume_final != null ? Number(d.volume_final) : Number(d.volume);
      return isNaN(vol) ? 0 : vol;
    }),
    deliveryPct: data.map(d => {
      if (d.delivery_pct != null && !isNaN(Number(d.delivery_pct))) return Number(d.delivery_pct);
      const delVal = d.delivery_final ? Number(d.delivery_final) : 0;
      const vol = Math.max(1, Number(d.volume) || 0);
      return (delVal / vol) * 100;
    }),
  };
}

export function createCandleIndexes(length: number): number[] {
  const indexes = new Array<number>(length);
  for (let i = 0; i < length; i++) indexes[i] = i;
  return indexes;
}

export function buildDateToIndexMap(dates: string[]): Map<string, number> {
  const map = new Map<string, number>();
  for (let i = 0; i < dates.length; i++) {
    map.set(dates[i], i);
  }
  return map;
}

export function calculateVisiblePriceRange(
  data: Candle[],
  startIndex: number,
  endIndex: number,
  paddingFraction: number = 0.05,
): { min: number; max: number } {
  let min = Infinity;
  let max = -Infinity;
  for (let i = Math.max(0, startIndex); i <= Math.min(data.length - 1, endIndex); i++) {
    const d = data[i];
    if (d.low < min) min = d.low;
    if (d.high > max) max = d.high;
  }
  const padding = (max - min) * paddingFraction;
  return { min: min - padding, max: max + padding };
}

export function calculateOverlayBounds(
  overlayY: (number | null)[],
  startIndex: number,
  endIndex: number,
): { min: number; max: number } | null {
  let min = Infinity;
  let max = -Infinity;
  let found = false;
  for (let i = Math.max(0, startIndex); i <= Math.min(overlayY.length - 1, endIndex); i++) {
    const v = overlayY[i];
    if (v != null && isFinite(v)) {
      if (v < min) min = v;
      if (v > max) max = v;
      found = true;
    }
  }
  return found ? { min, max } : null;
}

export function autoPriceRange(
  data: Candle[],
  candleIndexes: number[],
  overlays?: { y: (number | null)[] }[],
  paddingFraction: number = 0.05,
): { min: number; max: number } {
  if (data.length === 0) return { min: 0, max: 100 };

  let min = Infinity;
  let max = -Infinity;

  for (let i = 0; i < data.length; i++) {
    const d = data[i];
    if (d.low < min) min = d.low;
    if (d.high > max) max = d.high;
  }

  if (overlays) {
    for (const ov of overlays) {
      for (let i = 0; i < ov.y.length; i++) {
        const v = ov.y[i];
        if (v != null && isFinite(v)) {
          if (v < min) min = v;
          if (v > max) max = v;
        }
      }
    }
  }

  const padding = (max - min) * paddingFraction;
  return { min: min - padding, max: max + padding };
}
