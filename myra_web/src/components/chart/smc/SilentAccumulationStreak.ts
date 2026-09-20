/**
 * Silent Accumulation Streak — EXPERIMENTAL / UNVALIDATED
 *
 * Flags consecutive days of high delivery% while price stays flat/down
 * (stealth accumulation signal). Independent from Delivery Thrust Candles —
 * both can fire on the same candle.
 *
 * NOTE: This indicator is experimental and has NOT been backtested or calibrated.
 * Thresholds are preliminary guesses pending empirical validation.
 */

import type { Candle } from '../../../core/technical-analysis/types';

export interface AccumulationStreak {
  id: string;
  startDate: string;
  endDate: string;
  startIdx: number;
  endIdx: number;
  streakLength: number;
  avgDeliveryPct: number;
  avgDeliveryQty: number;
  priceChangePct: number; // % change from first to last close
  strengthScore: number;  // avgDeliveryPct × streakLength
}

export interface AccumulationStreakConfig {
  deliveryPctThreshold: number;
  priceFlatLookback: number;
  minStreakLength: number;
  maxStreakGap: number; // max non-accumulation days allowed within a streak
  isExperimental: boolean; // Always true
}

const DEFAULT_CONFIG: AccumulationStreakConfig = {
  deliveryPctThreshold: 60,
  priceFlatLookback: 5,
  minStreakLength: 3,
  maxStreakGap: 2,
  isExperimental: true,
};

/**
 * Detect silent accumulation streaks
 */
export function detectAccumulationStreaks(
  candles: Candle[],
  config: Partial<AccumulationStreakConfig> = {}
): AccumulationStreak[] {
  const fullConfig = { ...DEFAULT_CONFIG, ...config };
  if (candles.length < fullConfig.priceFlatLookback + fullConfig.minStreakLength) return [];

  const streaks: AccumulationStreak[] = [];

  // For each potential streak start
  for (let i = 0; i < candles.length; i++) {
    const streak = tryBuildStreak(candles, i, fullConfig);
    if (streak !== null) {
      streaks.push(streak);
      // Skip past this streak to avoid overlapping detections
      i = streak.endIdx;
    }
  }

  return streaks.sort((a, b) => b.strengthScore - a.strengthScore);
}

/**
 * Try to build a streak starting at index startIdx
 */
function tryBuildStreak(
  candles: Candle[],
  startIdx: number,
  config: AccumulationStreakConfig
): AccumulationStreak | null {
  // Check if startIdx qualifies as accumulation day
  if (!isAccumulationDay(candles, startIdx, config)) return null;

  let streakLength = 0;
  let gapCount = 0;
  let deliveryPctSum = 0;
  let deliveryQtySum = 0;
  let endIdx = startIdx;

  // Extend streak forward, allowing up to maxStreakGap non-accumulation days
  for (let j = startIdx; j < candles.length; j++) {
    if (isAccumulationDay(candles, j, config)) {
      streakLength++;
      deliveryPctSum += candles[j].delivery_pct || 0;
      deliveryQtySum += candles[j].delivery || 0;
      endIdx = j;
      gapCount = 0; // reset gap counter
    } else {
      gapCount++;
      if (gapCount > config.maxStreakGap) {
        // Gap exceeded — streak ends at previous candle
        break;
      }
      // Allow gap day — don't increment streakLength but don't break either
      endIdx = j;
    }
  }

  if (streakLength < config.minStreakLength) return null;

  const avgDeliveryPct = deliveryPctSum / streakLength;
  const avgDeliveryQty = deliveryQtySum / streakLength;
  const priceChangePct = (candles[endIdx].close / candles[startIdx].close - 1) * 100;
  const strengthScore = avgDeliveryPct * streakLength;

  return {
    id: `acc-${candles[startIdx].date}`,
    startDate: candles[startIdx].date,
    endDate: candles[endIdx].date,
    startIdx,
    endIdx,
    streakLength,
    avgDeliveryPct,
    avgDeliveryQty,
    priceChangePct,
    strengthScore,
  };
}

/**
 * Check if a candle qualifies as an accumulation day
 */
function isAccumulationDay(
  candles: Candle[],
  idx: number,
  config: AccumulationStreakConfig
): boolean {
  const c = candles[idx];
  const deliveryPct = c.delivery_pct || 0;

  // High delivery%
  if (deliveryPct < config.deliveryPctThreshold) return false;

  // Price flat or declining
  const lookbackIdx = Math.max(0, idx - config.priceFlatLookback);
  const prevClose = candles[lookbackIdx].close;
  if (prevClose === 0) return false;

  const priceChange = c.close / prevClose - 1;
  // Allow slight decline (up to -5%) and flat (up to 0%)
  // This covers both "price declining while delivery rises" and "price flat while delivery rises"
  return priceChange <= 0.05; // close ≤ 5% above lookback close
}

/**
 * Build Plotly marker traces for accumulation streaks.
 * Shapes (buildAccumulationShapes) handle the visual display.
 * Annotations provide the labels. Traces are not needed.
 */
export function buildAccumulationTraces(
  _streaks: AccumulationStreak[],
  _dates: string[],
  _dateToIndex?: Map<string, number>
): any[] {
  return [];
}

/**
 * Build Plotly rectangle shapes for accumulation streaks
 */
export function buildAccumulationShapes(
  streaks: AccumulationStreak[],
  dates: string[],
  priceRange: { min: number; max: number },
  dateToIndex?: Map<string, number>
): any[] {
  const shapes: any[] = [];
  if (dates.length === 0 || streaks.length === 0) return shapes;

  const d2i = dateToIndex ?? new Map(dates.map((d, i) => [d, i]));

  for (const s of streaks) {
    const x0i = d2i.get(s.startDate);
    const x1i = d2i.get(s.endDate);
    if (x0i === undefined || x1i === undefined) continue;

    // Subtle purple background bar at bottom of price range
    shapes.push({
      type: 'rect',
      xref: 'x', yref: 'y',
      x0: x0i - 0.5, x1: x1i + 0.5,
      y0: priceRange.min, y1: priceRange.min + (priceRange.max - priceRange.min) * 0.05,
      fillcolor: 'rgba(168, 85, 247, 0.15)',
      line: { width: 0 },
      layer: 'below',
    });
  }

  return shapes;
}

/**
 * Build annotation labels for accumulation streaks
 */
export function buildAccumulationAnnotations(
  streaks: AccumulationStreak[],
  dates: string[],
  dateToIndex?: Map<string, number>
): any[] {
  const annotations: any[] = [];
  if (dates.length === 0 || streaks.length === 0) return annotations;

  const d2i = dateToIndex ?? new Map(dates.map((d, i) => [d, i]));

  for (const s of streaks) {
    const x0i = d2i.get(s.startDate);
    if (x0i === undefined) continue;

    annotations.push({
      x: x0i,
      y: 1, // Top of chart area
      xref: 'x', yref: 'paper',
      text: `⚡${s.streakLength}d`,
      showarrow: false,
      font: {
        size: 9,
        color: '#a855f7',
      },
      bgcolor: 'rgba(0,0,0,0.7)',
      borderpad: 2,
    });
  }

  return annotations;
}

export { DEFAULT_CONFIG };