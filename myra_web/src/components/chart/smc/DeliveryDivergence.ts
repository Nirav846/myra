/**
 * Price-Delivery Divergence Oscillator
 *
 * Detects when price trends are losing genuine backing from delivery volume.
 * Acts as an early warning system for trend reversals.
 */

import type { Candle } from '../../../core/technical-analysis/types';

export interface DivergenceSignal {
  id: string;
  index: number;
  date: string;
  type: 'bullish' | 'bearish';
  strength: 'strong' | 'moderate' | 'weak';
  priceSlope: number;
  deliverySlope: number;
  divergenceMagnitude: number;
}

export interface DivergencePoint {
  index: number;
  price: number;
  delivery: number;
  isPriceHigherHigh: boolean;
  isPriceLowerLow: boolean;
  isDeliveryHigherHigh: boolean;
  isDeliveryLowerLow: boolean;
}

/**
 * Detect divergence between price and delivery volume
 */
function detectDivergences(candles: Candle[], lookback: number = 10): DivergenceSignal[] {
  const signals: DivergenceSignal[] = [];

  if (candles.length < lookback * 2 + 5) return signals;

  // Calculate slopes using linear regression
  const priceSlopes = calculateSlopes(candles.map(c => c.close), lookback);
  const deliverySlopes = calculateSlopes(candles.map(c => c.delivery || 0), lookback);

  // Find pivot points in price and delivery
  const pricePivots = findPivots(candles.map(c => c.close), lookback);
  const deliveryPivots = findPivots(candles.map(c => c.delivery || 0), lookback);

  // Check for divergences at each point
  for (let i = lookback * 2; i < candles.length; i++) {
    const currentPrice = candles[i].close;
    const currentDelivery = candles[i].delivery || 0;
    const priceSlope = priceSlopes[i] || 0;
    const deliverySlope = deliverySlopes[i] || 0;

    // Skip if either slope is near zero (no clear trend)
    if (Math.abs(priceSlope) < 0.0001 && Math.abs(deliverySlope) < 0.0001) continue;

    // Check for bearish divergence: Price making higher highs, delivery making lower highs
    if (priceSlope > 0.001 && deliverySlope < -0.001) {
      const divergenceMag = Math.abs(priceSlope - deliverySlope);

      let strength: 'strong' | 'moderate' | 'weak' = 'weak';
      if (divergenceMag > 0.01) strength = 'strong';
      else if (divergenceMag > 0.005) strength = 'moderate';

      signals.push({
        id: `div-bear-${i}`,
        index: i,
        date: candles[i].date,
        type: 'bearish',
        strength,
        priceSlope,
        deliverySlope,
        divergenceMagnitude: divergenceMag,
      });
    }

    // Check for bullish divergence: Price making lower lows, delivery drying up (lower lows too but steeper)
    if (priceSlope < -0.001 && deliverySlope > 0.001) {
      const divergenceMag = Math.abs(priceSlope - deliverySlope);

      let strength: 'strong' | 'moderate' | 'weak' = 'weak';
      if (divergenceMag > 0.01) strength = 'strong';
      else if (divergenceMag > 0.005) strength = 'moderate';

      signals.push({
        id: `div-bull-${i}`,
        index: i,
        date: candles[i].date,
        type: 'bullish',
        strength,
        priceSlope,
        deliverySlope,
        divergenceMagnitude: divergenceMag,
      });
    }
  }

  return signals;
}

/**
 * Calculate rolling slope using simple linear regression
 */
function calculateSlopes(data: number[], window: number): (number | null)[] {
  const slopes: (number | null)[] = [];

  for (let i = 0; i < data.length; i++) {
    if (i < window - 1) {
      slopes.push(null);
      continue;
    }

    const slice = data.slice(i - window + 1, i + 1);
    const n = slice.length;

    // Simple linear regression slope
    let sumX = 0;
    let sumY = 0;
    let sumXY = 0;
    let sumXX = 0;

    for (let j = 0; j < n; j++) {
      sumX += j;
      sumY += slice[j];
      sumXY += j * slice[j];
      sumXX += j * j;
    }

    const denominator = n * sumXX - sumX * sumX;
    if (denominator === 0) {
      slopes.push(0);
    } else {
      const slope = (n * sumXY - sumX * sumY) / denominator;
      slopes.push(slope);
    }
  }

  return slopes;
}

/**
 * Find pivot points (local highs/lows) in a series
 */
function findPivots(data: number[], lookback: number): number[] {
  const pivots: number[] = [];

  for (let i = lookback; i < data.length - lookback; i++) {
    const current = data[i];

    // Check if local high
    let isHigh = true;
    for (let j = 1; j <= lookback; j++) {
      if (data[i - j] >= current || data[i + j] >= current) {
        isHigh = false;
        break;
      }
    }

    if (isHigh) {
      pivots.push(i);
      continue;
    }

    // Check if local low
    let isLow = true;
    for (let j = 1; j <= lookback; j++) {
      if (data[i - j] <= current || data[i + j] <= current) {
        isLow = false;
        break;
      }
    }

    if (isLow) {
      pivots.push(i);
    }
  }

  return pivots;
}

/**
 * Build divergence oscillator trace for Plotly
 */
export function buildDivergenceOscillatorTrace(candles: Candle[], lookback: number = 10) {
  const priceSlopes = calculateSlopes(candles.map(c => c.close), lookback);
  const deliverySlopes = calculateSlopes(candles.map(c => c.delivery || 0), lookback);
  const dates = candles.map(c => c.date);

  // Calculate divergence score (price slope - normalized delivery slope)
  const maxDelivery = Math.max(...candles.map(c => c.delivery || 1));
  const divergenceScore = priceSlopes.map((ps, i) => {
    const ds = deliverySlopes[i] || 0;
    const normalizedDeliverySlope = ds / maxDelivery;
    return (ps || 0) - normalizedDeliverySlope;
  });

  const traces: any[] = [];

  // Main divergence line
  traces.push({
    type: 'scatter',
    mode: 'lines',
    x: dates,
    y: divergenceScore,
    name: 'Price-Del Divergence',
    line: {
      color: '#f59e0b',
      width: 2,
    },
    hovertemplate: 'Divergence: %{y:.6f}<extra></extra>',
    showlegend: true,
    fill: 'tozeroy',
    fillcolor: 'rgba(245, 158, 11, 0.2)',
  });

  // Zero line
  traces.push({
    type: 'scatter',
    mode: 'lines',
    x: dates,
    y: dates.map(() => 0),
    name: 'Zero Line',
    line: {
      color: '#888899',
      width: 1,
      dash: 'dot',
    },
    hoverinfo: 'skip',
    showlegend: false,
  });

  return traces;
}

/**
 * Build divergence signal markers
 */
export function buildDivergenceMarkers(signals: DivergenceSignal[]) {
  if (signals.length === 0) return [];

  const traces: any[] = [];

  // Filter for strong/moderate signals only
  const significantSignals = signals.filter(s => s.strength !== 'weak');

  if (significantSignals.length > 0) {
    const bullishSignals = significantSignals.filter(s => s.type === 'bullish');
    const bearishSignals = significantSignals.filter(s => s.type === 'bearish');

    // Bullish divergence markers
    if (bullishSignals.length > 0) {
      traces.push({
        type: 'scatter',
        mode: 'markers',
        x: bullishSignals.map(s => s.index),
        y: bullishSignals.map(s => s.divergenceMagnitude * -1), // Below zero line
        name: 'Bullish Div',
        marker: {
          symbol: 'triangle-up',
          size: bullishSignals.map(s => s.strength === 'strong' ? 12 : 8),
          color: bullishSignals.map(s => s.strength === 'strong' ? '#22c55e' : '#4ade80'),
          opacity: 0.8,
        },
        hovertemplate: 'Bullish Div (%{customdata})<extra></extra>',
        customdata: bullishSignals.map(s => s.strength),
        showlegend: true,
      });
    }

    // Bearish divergence markers
    if (bearishSignals.length > 0) {
      traces.push({
        type: 'scatter',
        mode: 'markers',
        x: bearishSignals.map(s => s.index),
        y: bearishSignals.map(s => s.divergenceMagnitude), // Above zero line
        name: 'Bearish Div',
        marker: {
          symbol: 'triangle-down',
          size: bearishSignals.map(s => s.strength === 'strong' ? 12 : 8),
          color: bearishSignals.map(s => s.strength === 'strong' ? '#ef4444' : '#f87171'),
          opacity: 0.8,
        },
        hovertemplate: 'Bearish Div (%{customdata})<extra></extra>',
        customdata: bearishSignals.map(s => s.strength),
        showlegend: true,
      });
    }
  }

  return traces;
}

/**
 * Get divergence statistics
 */
export function getDivergenceStats(candles: Candle[], lookback: number = 10) {
  const signals = detectDivergences(candles, lookback);

  const bullishCount = signals.filter(s => s.type === 'bullish').length;
  const bearishCount = signals.filter(s => s.type === 'bearish').length;
  const strongCount = signals.filter(s => s.strength === 'strong').length;

  return {
    totalSignals: signals.length,
    bullishCount,
    bearishCount,
    strongCount,
    recentSignal: signals.length > 0 ? signals[signals.length - 1] : null,
  };
}

/**
 * Detect divergences from candle data
 */
export function detectDivergencesFromData(candles: Candle[], lookback: number = 10): DivergenceSignal[] {
  return detectDivergences(candles, lookback);
}

export default detectDivergencesFromData;
