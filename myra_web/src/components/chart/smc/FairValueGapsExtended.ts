/**
 * Fair Value Gaps Extended with Liquidity Voids
 *
 * Extends basic FVG detection to include:
 * - Liquidity Voids (larger imbalances)
 * - Gap efficiency calculations
 * - Mitigation tracking
 */

import type { Candle } from '../../../core/technical-analysis/types';

export interface FairValueGap {
  id: string;
  type: 'fvg' | 'liquidity_void';
  isBullish: boolean;
  startIdx: number;
  endIdx: number;
  startPrice: number;
  endPrice: number;
  gapSize: number; // Absolute price difference
  gapPercent: number; // Percentage of price
  efficiency: number; // How much of the gap has been filled (0-1)
  isMitigated: boolean; // True if price has returned to fill the gap
}

/**
 * Detect Fair Value Gaps and Liquidity Voids
 */
function detectFVGsExtended(candles: Candle[], minGapPercent: number = 0.001): FairValueGap[] {
  const gaps: FairValueGap[] = [];

  for (let i = 2; i < candles.length; i++) {
    const prevCandle = candles[i - 2];
    const middleCandle = candles[i - 1];
    const currentCandle = candles[i];

    // Bullish FVG: Current low > Previous high
    if (currentCandle.low > prevCandle.high) {
      const gapStart = prevCandle.high;
      const gapEnd = currentCandle.low;
      const gapSize = gapEnd - gapStart;
      const gapPercent = gapSize / gapStart;

      if (gapPercent >= minGapPercent) {
        // Check if it's a liquidity void (extra large gap)
        const isLiquidityVoid = gapPercent >= minGapPercent * 3;

        // Calculate efficiency (how much has been filled)
        const efficiency = calculateGapEfficiency(candles, i, gapStart, gapEnd, 'bullish');

        gaps.push({
          id: `fvg-bull-${i}`,
          type: isLiquidityVoid ? 'liquidity_void' : 'fvg',
          isBullish: true,
          startIdx: i - 1,
          endIdx: i,
          startPrice: gapStart,
          endPrice: gapEnd,
          gapSize,
          gapPercent,
          efficiency,
          isMitigated: efficiency >= 0.95,
        });
      }
    }

    // Bearish FVG: Current high < Previous low
    if (currentCandle.high < prevCandle.low) {
      const gapStart = prevCandle.low;
      const gapEnd = currentCandle.high;
      const gapSize = gapStart - gapEnd;
      const gapPercent = gapSize / gapStart;

      if (gapPercent >= minGapPercent) {
        const isLiquidityVoid = gapPercent >= minGapPercent * 3;
        const efficiency = calculateGapEfficiency(candles, i, gapEnd, gapStart, 'bearish');

        gaps.push({
          id: `fvg-bear-${i}`,
          type: isLiquidityVoid ? 'liquidity_void' : 'fvg',
          isBullish: false,
          startIdx: i - 1,
          endIdx: i,
          startPrice: gapStart,
          endPrice: gapEnd,
          gapSize,
          gapPercent,
          efficiency,
          isMitigated: efficiency >= 0.95,
        });
      }
    }
  }

  return gaps;
}

/**
 * Calculate how much of a gap has been filled by subsequent price action
 */
function calculateGapEfficiency(
  candles: Candle[],
  startIndex: number,
  lowerBound: number,
  upperBound: number,
  type: 'bullish' | 'bearish'
): number {
  if (startIndex >= candles.length - 1) return 0;

  const futureCandles = candles.slice(startIndex + 1);
  if (futureCandles.length === 0) return 0;

  const gapRange = upperBound - lowerBound;

  if (type === 'bullish') {
    // For bullish gaps, check how much price has retraced down into the gap
    const lowestLow = Math.min(...futureCandles.map(c => c.low));

    if (lowestLow <= lowerBound) {
      return 1; // Fully mitigated
    }

    if (lowestLow >= upperBound) {
      return 0; // Not touched
    }

    return (upperBound - lowestLow) / gapRange;
  } else {
    // For bearish gaps, check how much price has rallied up into the gap
    const highestHigh = Math.max(...futureCandles.map(c => c.high));

    if (highestHigh >= upperBound) {
      return 1; // Fully mitigated
    }

    if (highestHigh <= lowerBound) {
      return 0; // Not touched
    }

    return (highestHigh - lowerBound) / gapRange;
  }
}

/**
 * Build FVG rectangle shapes for Plotly
 */
export function buildFVGExtendedShapes(gaps: FairValueGap[], candles: Candle[]) {
  const shapes: any[] = [];

  gaps.forEach(gap => {
    if (gap.isMitigated) return;
    if (gap.gapPercent < 0.0005) return;

    const color = getFVGColor(gap.type, gap.isBullish, gap.efficiency);

    const x0 = candles[gap.startIdx]?.date ?? '';
    const x1 = candles[gap.endIdx]?.date ?? x0;

    shapes.push({
      type: 'rect',
      xref: 'x',
      yref: 'y',
      x0,
      x1,
      y0: Math.min(gap.startPrice, gap.endPrice),
      y1: Math.max(gap.startPrice, gap.endPrice),
      fillcolor: color.fill,
      line: {
        color: color.stroke,
        width: gap.type === 'liquidity_void' ? 2 : 1,
        dash: gap.isMitigated ? 'dot' : 'solid',
      },
      layer: 'below',
    });
  });

  return shapes;
}

/**
 * Build FVG annotations
 */
export function buildFVGExtendedAnnotations(gaps: FairValueGap[], candles: Candle[]) {
  const annotations: any[] = [];

  gaps.forEach(gap => {
    if (gap.isMitigated) return;
    if (gap.gapPercent < 0.0005) return; // Would display as 0.00% — not useful

    const midIndex = (gap.startIdx + gap.endIdx) / 2;
    const midPrice = (gap.startPrice + gap.endPrice) / 2;
    const label = gap.type === 'liquidity_void'
      ? `LV ${(gap.gapPercent * 100).toFixed(2)}%`
      : `FVG ${(gap.gapPercent * 100).toFixed(2)}%`;

    annotations.push({
      x: candles[Math.round(midIndex)]?.date ?? '',
      y: midPrice,
      xref: 'x',
      yref: 'y',
      text: label,
      showarrow: false,
      font: {
        size: 9,
        color: gap.isBullish ? '#26a69a' : '#ef5350',
      },
      bgcolor: 'rgba(0,0,0,0.7)',
      borderpad: 2,
    });
  });

  return annotations;
}

/**
 * Get color based on FVG type and efficiency
 */
function getFVGColor(type: 'fvg' | 'liquidity_void', isBullish: boolean, efficiency: number) {
  const baseAlpha = 0.3 * (1 - efficiency * 0.5); // Fade as mitigated

  if (isBullish) {
    if (type === 'liquidity_void') {
      return {
        fill: `rgba(168, 85, 247, ${baseAlpha})`, // Purple for voids
        stroke: '#a855f7',
      };
    }
    return {
      fill: `rgba(38, 166, 154, ${baseAlpha})`,
      stroke: '#26a69a',
    };
  } else {
    if (type === 'liquidity_void') {
      return {
        fill: `rgba(236, 72, 153, ${baseAlpha})`, // Pink for voids
        stroke: '#ec4899',
      };
    }
    return {
      fill: `rgba(239, 83, 80, ${baseAlpha})`,
      stroke: '#ef5350',
    };
  }
}

/**
 * Detect FVGs from candle data
 */
export function detectFVGsFromData(candles: Candle[], minGapPercent: number = 0.001): FairValueGap[] {
  return detectFVGsExtended(candles, minGapPercent);
}

export default detectFVGsFromData;
