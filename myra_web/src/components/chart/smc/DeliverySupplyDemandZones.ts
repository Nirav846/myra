/**
 * Delivery-Based Supply/Demand Zones — EXPERIMENTAL / UNVALIDATED
 *
 * Zones derived from delivery concentration rather than pure OHLC swing structure.
 * Identifies areas where institutions accumulated (demand) or distributed (supply)
 * based on abnormal delivery intensity.
 *
 * NOTE: This indicator is experimental and has NOT been backtested or calibrated.
 * Thresholds are preliminary guesses pending empirical validation.
 */

import type { Candle } from '../../../core/technical-analysis/types';

export interface DeliveryZone {
  id: string;
  type: 'demand' | 'supply';
  startIdx: number;
  endIdx: number;
  startDate: string;
  endDate: string;
  priceLow: number;  // min_close during zone
  priceHigh: number; // max_close during zone
  avgDeliveryIntensity: number;
  avgDeliveryPct: number;
  zoneDays: number;
  strengthScore: number;
}

export interface DeliveryZonesConfig {
  deliveryIntensityThreshold: number;
  deliveryPctThreshold: number;
  minZoneDays: number;
  priceFlatThreshold: number; // max price change ratio to qualify as "flat"
  lookbackPeriod: number; // for delivery_ma_20 computation
  isExperimental: boolean; // Always true
}

const DEFAULT_CONFIG: DeliveryZonesConfig = {
  deliveryIntensityThreshold: 1.2,
  deliveryPctThreshold: 55,
  minZoneDays: 3,
  priceFlatThreshold: 0.02,
  lookbackPeriod: 20,
  isExperimental: true,
};

/**
 * Compute rolling delivery moving average client-side
 */
function computeDeliveryMA20(candles: Candle[]): number[] {
  const result: number[] = [];
  const period = 20;

  for (let i = 0; i < candles.length; i++) {
    if (i < period - 1) {
      result.push(candles[i].delivery || 0);
      continue;
    }
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) {
      sum += candles[j].delivery || 0;
    }
    result.push(sum / period);
  }
  return result;
}

/**
 * Detect delivery-based supply/demand zones
 */
export function detectDeliveryZones(
  candles: Candle[],
  config: Partial<DeliveryZonesConfig> = {}
): DeliveryZone[] {
  const fullConfig = { ...DEFAULT_CONFIG, ...config };
  if (candles.length < fullConfig.lookbackPeriod + fullConfig.minZoneDays) return [];

  const deliveryMA = computeDeliveryMA20(candles);
  const zones: DeliveryZone[] = [];

  // Track consecutive accumulation/distribution days
  let streakStart = -1;
  let streakType: 'demand' | 'supply' | null = null;
  let streakCloseLow = Infinity;
  let streakCloseHigh = -Infinity;
  let streakDeliverySum = 0;
  let streakDeliveryPctSum = 0;
  let streakCount = 0;

  for (let i = fullConfig.lookbackPeriod; i < candles.length; i++) {
    const c = candles[i];
    const delivery = c.delivery || 0;
    const deliveryPct = c.delivery_pct || 0;
    const deliveryMAVal = deliveryMA[i] || 1;

    const intensity = delivery / Math.max(1, deliveryMAVal);
    const isHighIntensity = intensity >= fullConfig.deliveryIntensityThreshold;
    const isHighDeliveryPct = deliveryPct >= fullConfig.deliveryPctThreshold;

    if (!isHighIntensity || !isHighDeliveryPct) {
      // End any active streak
      if (streakStart >= 0 && streakCount >= fullConfig.minZoneDays) {
        const closes = candles.slice(streakStart, i).map(cc => cc.close);
        zones.push({
          id: `${streakType}-${candles[streakStart].date}`,
          type: streakType!,
          startIdx: streakStart,
          endIdx: i - 1,
          startDate: candles[streakStart].date,
          endDate: candles[i - 1].date,
          priceLow: Math.min(...closes),
          priceHigh: Math.max(...closes),
          avgDeliveryIntensity: streakDeliverySum / streakCount,
          avgDeliveryPct: streakDeliveryPctSum / streakCount,
          zoneDays: streakCount,
          strengthScore: (streakDeliverySum / streakCount) * streakCount,
        });
      }
      streakStart = -1;
      streakType = null;
      streakCount = 0;
      streakDeliverySum = 0;
      streakDeliveryPctSum = 0;
      continue;
    }

    // Determine zone type based on price action
    const priceChange5d = c.close / Math.max(1, candles[Math.max(0, i - 5)].close) - 1;
    const isFlat = Math.abs(priceChange5d) <= fullConfig.priceFlatThreshold;
    const isDown = priceChange5d < -fullConfig.priceFlatThreshold;
    const isUp = priceChange5d > fullConfig.priceFlatThreshold;

    let dayType: 'demand' | 'supply' | null = null;
    if (isDown || isFlat) {
      dayType = 'demand'; // Accumulation: delivery high, price flat/down
    } else if (isUp) {
      dayType = 'supply'; // Distribution: delivery high, price up
    }

    if (dayType === null) {
      // End streak
      if (streakStart >= 0 && streakCount >= fullConfig.minZoneDays) {
        const closes = candles.slice(streakStart, i).map(cc => cc.close);
        zones.push({
          id: `${streakType}-${candles[streakStart].date}`,
          type: streakType!,
          startIdx: streakStart,
          endIdx: i - 1,
          startDate: candles[streakStart].date,
          endDate: candles[i - 1].date,
          priceLow: Math.min(...closes),
          priceHigh: Math.max(...closes),
          avgDeliveryIntensity: streakDeliverySum / streakCount,
          avgDeliveryPct: streakDeliveryPctSum / streakCount,
          zoneDays: streakCount,
          strengthScore: (streakDeliverySum / streakCount) * streakCount,
        });
      }
      streakStart = -1;
      streakType = null;
      streakCount = 0;
      streakDeliverySum = 0;
      streakDeliveryPctSum = 0;
      continue;
    }

    // Continue or start streak
    if (streakType === dayType) {
      // Continue existing streak
      streakCount++;
      streakDeliverySum += intensity;
      streakDeliveryPctSum += deliveryPct;
    } else {
      // Type changed — end old streak, start new one
      if (streakStart >= 0 && streakCount >= fullConfig.minZoneDays) {
        const closes = candles.slice(streakStart, i).map(cc => cc.close);
        zones.push({
          id: `${streakType}-${candles[streakStart].date}`,
          type: streakType!,
          startIdx: streakStart,
          endIdx: i - 1,
          startDate: candles[streakStart].date,
          endDate: candles[i - 1].date,
          priceLow: Math.min(...closes),
          priceHigh: Math.max(...closes),
          avgDeliveryIntensity: streakDeliverySum / streakCount,
          avgDeliveryPct: streakDeliveryPctSum / streakCount,
          zoneDays: streakCount,
          strengthScore: (streakDeliverySum / streakCount) * streakCount,
        });
      }
      streakStart = i;
      streakType = dayType;
      streakCount = 1;
      streakDeliverySum = intensity;
      streakDeliveryPctSum = deliveryPct;
    }
  }

  // Close final streak
  if (streakStart >= 0 && streakCount >= fullConfig.minZoneDays) {
    const closes = candles.slice(streakStart, candles.length).map(cc => cc.close);
    zones.push({
      id: `${streakType}-${candles[streakStart].date}`,
      type: streakType!,
      startIdx: streakStart,
      endIdx: candles.length - 1,
      startDate: candles[streakStart].date,
      endDate: candles[candles.length - 1].date,
      priceLow: Math.min(...closes),
      priceHigh: Math.max(...closes),
      avgDeliveryIntensity: streakDeliverySum / streakCount,
      avgDeliveryPct: streakDeliveryPctSum / streakCount,
      zoneDays: streakCount,
      strengthScore: (streakDeliverySum / streakCount) * streakCount,
    });
  }

  return zones.sort((a, b) => b.strengthScore - a.strengthScore);
}

/**
 * Build Plotly rectangle shapes for supply/demand zones
 */
export function buildDeliveryZoneShapes(
  zones: DeliveryZone[],
  dates: string[],
  dateToIndex?: Map<string, number>
): any[] {
  const shapes: any[] = [];
  if (dates.length === 0 || zones.length === 0) return shapes;

  const d2i = dateToIndex ?? new Map(dates.map((d, i) => [d, i]));

  for (const z of zones) {
    // NOTE: V1 uses xaxis type:'linear' with integer candleIndexes, V2 uses
    // type:'category' with date strings (ChartLayout.ts + CandlestickRenderer.ts).
    // This module is V2-only — integer positions via d2i work through Plotly's
    // category-axis index fallback, but future SMC modules should pass date
    // strings directly (as CandlestickRenderer does) to avoid the index/date
    // confusion that caused the pivot/OB/FVG bugs earlier this session.
    const x0i = d2i.get(z.startDate);
    const x1i = d2i.get(z.endDate);
    if (x0i === undefined || x1i === undefined) continue;

    const color = z.type === 'demand'
      ? 'rgba(34, 197, 94, 0.25)'
      : 'rgba(239, 68, 68, 0.25)';
    const lineColor = z.type === 'demand'
      ? 'rgba(34, 197, 94, 0.6)'
      : 'rgba(239, 68, 68, 0.6)';

    shapes.push({
      type: 'rect',
      xref: 'x', yref: 'y',
      x0: x0i - 0.5, x1: x1i + 0.5,
      y0: z.priceLow, y1: z.priceHigh,
      fillcolor: color,
      line: { width: 1, color: lineColor },
      layer: 'below',
    });
  }

  return shapes;
}

/**
 * Build annotations for supply/demand zones
 */
export function buildDeliveryZoneAnnotations(
  zones: DeliveryZone[],
  dates: string[],
  dateToIndex?: Map<string, number>
): any[] {
  const annotations: any[] = [];
  if (dates.length === 0 || zones.length === 0) return annotations;

  const d2i = dateToIndex ?? new Map(dates.map((d, i) => [d, i]));

  for (const z of zones) {
    const x0i = d2i.get(z.startDate);
    if (x0i === undefined) continue;

    const label = z.type === 'demand' ? 'D' : 'S';
    annotations.push({
      x: x0i,
      y: z.priceHigh,
      xref: 'x', yref: 'y',
      text: `${label}${z.zoneDays}`,
      showarrow: false,
      font: {
        size: 9,
        color: z.type === 'demand' ? '#22c55e' : '#ef4444',
      },
      bgcolor: 'rgba(0,0,0,0.7)',
      borderpad: 2,
    });
  }

  return annotations;
}

export { DEFAULT_CONFIG };