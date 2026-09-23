import type { Candle } from '../types';

export interface EqualHighLow {
  type: 'EQH' | 'EQL';
  indices: number[];
  priceLevel: number;
  tolerancePercent: number;
  touchCount: number;
}

export interface EqualHighLowResult {
  equalHighs: EqualHighLow[];
  equalLows: EqualHighLow[];
}

/**
 * Detects Equal Highs and Equal Lows (liquidity pools)
 * EQH: Multiple peaks at similar price levels (within tolerance)
 * EQL: Multiple troughs at similar price levels (within tolerance)
 */
export function detectEqualHighsLows(
  data: Candle[],
  tolerancePercent: number = 0.5,
  minTouches: number = 2
): EqualHighLowResult {
  const equalHighs: EqualHighLow[] = [];
  const equalLows: EqualHighLow[] = [];

  if (data.length < 5) {
    return { equalHighs, equalLows };
  }

  // Find all swing highs and lows first
  const swingHighs: { index: number; price: number }[] = [];
  const swingLows: { index: number; price: number }[] = [];

  for (let i = 2; i < data.length - 2; i++) {
    // Swing High: Higher than 2 candles on each side
    if (
      data[i].high > data[i - 1].high &&
      data[i].high > data[i - 2].high &&
      data[i].high > data[i + 1].high &&
      data[i].high > data[i + 2].high
    ) {
      swingHighs.push({ index: i, price: data[i].high });
    }

    // Swing Low: Lower than 2 candles on each side
    if (
      data[i].low < data[i - 1].low &&
      data[i].low < data[i - 2].low &&
      data[i].low < data[i + 1].low &&
      data[i].low < data[i + 2].low
    ) {
      swingLows.push({ index: i, price: data[i].low });
    }
  }

  // Group swing highs by price level
  const groupedHighs = groupByPriceLevel(swingHighs, tolerancePercent);
  
  // Group swing lows by price level
  const groupedLows = groupByPriceLevel(swingLows, tolerancePercent);

  // Create Equal Highs from groups with minTouches
  groupedHighs.forEach((group) => {
    if (group.length >= minTouches) {
      const avgPrice = group.reduce((sum, p) => sum + p.price, 0) / group.length;
      equalHighs.push({
        type: 'EQH',
        indices: group.map(p => p.index),
        priceLevel: avgPrice,
        tolerancePercent,
        touchCount: group.length,
      });
    }
  });

  // Create Equal Lows from groups with minTouches
  groupedLows.forEach((group) => {
    if (group.length >= minTouches) {
      const avgPrice = group.reduce((sum, p) => sum + p.price, 0) / group.length;
      equalLows.push({
        type: 'EQL',
        indices: group.map(p => p.index),
        priceLevel: avgPrice,
        tolerancePercent,
        touchCount: group.length,
      });
    }
  });

  // Sort by most recent and limit to avoid clutter
  equalHighs.sort((a, b) => Math.max(...b.indices) - Math.max(...a.indices));
  equalLows.sort((a, b) => Math.max(...b.indices) - Math.max(...a.indices));

  return {
    equalHighs: equalHighs.slice(0, 5),
    equalLows: equalLows.slice(0, 5),
  };
}

/**
 * Groups price points by similarity within tolerance
 */
function groupByPriceLevel(
  points: { index: number; price: number }[],
  tolerancePercent: number
): { index: number; price: number }[][] {
  const groups: { index: number; price: number }[][] = [];

  for (const point of points) {
    let added = false;

    for (const group of groups) {
      const avgPrice = group.reduce((sum, p) => sum + p.price, 0) / group.length;
      const tolerance = (avgPrice * tolerancePercent) / 100;

      if (Math.abs(point.price - avgPrice) <= tolerance) {
        group.push(point);
        added = true;
        break;
      }
    }

    if (!added) {
      groups.push([point]);
    }
  }

  return groups;
}
