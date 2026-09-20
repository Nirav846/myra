/**
 * Swing Points Detector
 *
 * Identifies Pivot Highs and Pivot Lows using configurable lookback windows.
 * These form the foundation for market structure analysis.
 */

import type { Candle } from '../../../core/technical-analysis/types';

export interface SwingPoint {
  id: string;
  type: 'pivot_high' | 'pivot_low';
  index: number;
  price: number;
  strength: number; // How many candles confirmed the pivot (1-lookback)
  isMajor: boolean; // True if swing is significant (>2x average range)
}

export interface SwingHighLow {
  highIndex: number;
  lowIndex: number;
  highPrice: number;
  lowPrice: number;
}

/**
 * Detect swing points in price data
 */
function detectSwingPoints(candles: Candle[], lookback: number = 5): SwingPoint[] {
  const swings: SwingPoint[] = [];

  if (candles.length < lookback * 2 + 1) return swings;

  // Calculate average candle range for major swing detection
  const avgRange = calculateAverageRange(candles, 20);

  for (let i = lookback; i < candles.length - lookback; i++) {
    const currentCandle = candles[i];

    // Check for pivot high
    const isPivotHigh = checkPivotHigh(candles, i, lookback);
    if (isPivotHigh) {
      const candleRange = currentCandle.high - currentCandle.low;
      const isMajor = candleRange > avgRange * 2;

      // Count confirmation candles
      let strength = 0;
      for (let j = 1; j <= lookback; j++) {
        if (candles[i + j].high < currentCandle.high) {
          strength++;
        } else {
          break;
        }
      }

      swings.push({
        id: `ph-${i}`,
        type: 'pivot_high',
        index: i,
        price: currentCandle.high,
        strength,
        isMajor,
      });
    }

    // Check for pivot low
    const isPivotLow = checkPivotLow(candles, i, lookback);
    if (isPivotLow) {
      const candleRange = currentCandle.high - currentCandle.low;
      const isMajor = candleRange < avgRange * 0.5; // Unusually small range at lows

      // Count confirmation candles
      let strength = 0;
      for (let j = 1; j <= lookback; j++) {
        if (candles[i + j].low > currentCandle.low) {
          strength++;
        } else {
          break;
        }
      }

      swings.push({
        id: `pl-${i}`,
        type: 'pivot_low',
        index: i,
        price: currentCandle.low,
        strength,
        isMajor,
      });
    }
  }

  return swings;
}

/**
 * Check if candle at index is a pivot high
 */
function checkPivotHigh(candles: Candle[], index: number, lookback: number): boolean {
  const currentHigh = candles[index].high;

  // Check left side
  for (let i = 1; i <= lookback; i++) {
    if (candles[index - i].high >= currentHigh) {
      return false;
    }
  }

  // Check right side
  for (let i = 1; i <= lookback; i++) {
    if (candles[index + i].high >= currentHigh) {
      return false;
    }
  }

  return true;
}

/**
 * Check if candle at index is a pivot low
 */
function checkPivotLow(candles: Candle[], index: number, lookback: number): boolean {
  const currentLow = candles[index].low;

  // Check left side
  for (let i = 1; i <= lookback; i++) {
    if (candles[index - i].low <= currentLow) {
      return false;
    }
  }

  // Check right side
  for (let i = 1; i <= lookback; i++) {
    if (candles[index + i].low <= currentLow) {
      return false;
    }
  }

  return true;
}

/**
 * Calculate average candle range over a period
 */
function calculateAverageRange(candles: Candle[], period: number): number {
  const slice = candles.slice(-period);
  if (slice.length === 0) return 0;

  const sum = slice.reduce((acc, c) => acc + (c.high - c.low), 0);
  return sum / slice.length;
}

/**
 * Build swing point markers for Plotly
 */
export function buildSwingPointMarkers(swings: SwingPoint[], candles: Candle[]) {
  const traces: any[] = [];

  // Separate highs and lows
  const highs = swings.filter(s => s.type === 'pivot_high');
  const lows = swings.filter(s => s.type === 'pivot_low');

  // Pivot High markers
  if (highs.length > 0) {
    traces.push({
      type: 'scatter',
      mode: 'markers',
      x: highs.map(s => candles[s.index]?.date ?? ''),
      y: highs.map(s => s.price),
      name: 'Pivot High',
      marker: {
        symbol: 'triangle-down',
        size: highs.map(s => s.isMajor ? 12 : 8),
        color: highs.map(s => s.isMajor ? '#ef4444' : '#f87171'),
        opacity: 0.8,
      },
      hovertemplate: 'Pivot High: %{y:.2f}<br>Strength: %{customdata}<extra></extra>',
      customdata: highs.map(s => s.strength),
      showlegend: true,
      xaxis: 'x',
      yaxis: 'y',
    });
  }

  // Pivot Low markers
  if (lows.length > 0) {
    traces.push({
      type: 'scatter',
      mode: 'markers',
      x: lows.map(s => candles[s.index]?.date ?? ''),
      y: lows.map(s => s.price),
      name: 'Pivot Low',
      marker: {
        symbol: 'triangle-up',
        size: lows.map(s => s.isMajor ? 12 : 8),
        color: lows.map(s => s.isMajor ? '#22c55e' : '#4ade80'),
        opacity: 0.8,
      },
      hovertemplate: 'Pivot Low: %{y:.2f}<br>Strength: %{customdata}<extra></extra>',
      customdata: lows.map(s => s.strength),
      showlegend: true,
      xaxis: 'x',
      yaxis: 'y',
    });
  }

  return traces;
}

/**
 * Get highest high and lowest low in a range
 */
export function getSwingHighLow(candles: Candle[], startIndex: number, endIndex: number): SwingHighLow | null {
  if (startIndex < 0 || endIndex >= candles.length || startIndex > endIndex) {
    return null;
  }

  const slice = candles.slice(startIndex, endIndex + 1);
  if (slice.length === 0) return null;

  let highIdx = startIndex;
  let lowIdx = startIndex;
  let highPrice = slice[0].high;
  let lowPrice = slice[0].low;

  for (let i = 1; i < slice.length; i++) {
    if (slice[i].high > highPrice) {
      highPrice = slice[i].high;
      highIdx = startIndex + i;
    }
    if (slice[i].low < lowPrice) {
      lowPrice = slice[i].low;
      lowIdx = startIndex + i;
    }
  }

  return {
    highIndex: highIdx,
    lowIndex: lowIdx,
    highPrice,
    lowPrice,
  };
}

/**
 * Detect swing points from candle data
 */
export function detectSwingPointsFromData(candles: Candle[], lookback: number = 5): SwingPoint[] {
  return detectSwingPoints(candles, lookback);
}

export default detectSwingPointsFromData;
