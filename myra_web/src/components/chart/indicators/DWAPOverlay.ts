/**
 * DWAP (Delivery-Weighted Average Price) Overlay
 *
 * Like VWAP, but ignores intraday speculative volume.
 * Calculates average price based only on shares taken for delivery.
 *
 * Formula:
 * Typical Price = (High + Low + Close) / 3
 * DWAP = Cumulative Sum(Typical Price * Delivery Volume) / Cumulative Sum(Delivery Volume)
 *
 * Reveals true average cost basis of "strong hands" (investors/institutions)
 */

import type { IndicatorConfig } from '../registry/IndicatorRegistry';
import type { Candle } from '../../../core/technical-analysis/types';

export interface DWAPConfig extends IndicatorConfig {
  type: 'line'; // Must match base IndicatorConfig type union
  visible: boolean;
  color: string;
  lineWidth: number;
  label: string;
  zIndex: number; // Required by base interface
}

export const DEFAULT_DWAP_CONFIG: DWAPConfig = {
  id: 'dwap',
  type: 'line',
  visible: false,
  color: '#FF6B35', // Distinctive orange - different from VWAP's blue
  lineWidth: 2,
  label: 'DWAP',
  zIndex: 5,
};

export interface DWAPPoint {
  date: string;
  value: number;
}

/**
 * Calculate DWAP values for a dataset
 */
export function calculateDWAP(
  candles: Candle[],
  config: DWAPConfig = DEFAULT_DWAP_CONFIG
): DWAPPoint[] {
  if (!candles || candles.length === 0) {
    return [];
  }

  let cumulativeTPxDV = 0;
  let cumulativeDV = 0;
  const result: DWAPPoint[] = [];

  for (const candle of candles) {
    const typicalPrice = (candle.high + candle.low + candle.close) / 3;
    const deliveryVolume = candle.delivery || 0;

    cumulativeTPxDV += typicalPrice * deliveryVolume;
    cumulativeDV += deliveryVolume;

    if (cumulativeDV > 0) {
      const dwap = cumulativeTPxDV / cumulativeDV;
      result.push({
        date: candle.date,
        value: dwap,
      });
    } else {
      // No delivery volume yet, skip or use previous value
      result.push({
        date: candle.date,
        value: result.length > 0 ? result[result.length - 1].value : candle.close,
      });
    }
  }

  return result;
}

/**
 * Render DWAP line trace for Plotly
 */
export function renderDWAP(
  candles: Candle[],
  config: DWAPConfig = DEFAULT_DWAP_CONFIG
): Partial<Plotly.Data> {
  const points = calculateDWAP(candles, config);

  if (points.length === 0) {
    return { x: [], y: [] };
  }

  return {
    x: points.map((p) => p.date),
    y: points.map((p) => p.value),
    mode: 'lines',
    line: {
      color: config.color,
      width: config.lineWidth,
      dash: 'solid',
    },
    name: config.label,
    hoverinfo: 'y+name',
    hoverlabel: {
      bgcolor: config.color,
      font: { color: '#FFFFFF' },
    },
    yaxis: 'y',
    showlegend: config.visible,
  };
}

/**
 * Get DWAP value for a specific date (for crosshair tooltips)
 */
export function getDWAPAtDate(
  candles: Candle[],
  targetDate: string,
  config: DWAPConfig = DEFAULT_DWAP_CONFIG
): number | null {
  const points = calculateDWAP(candles, config);
  const point = points.find((p) => p.date === targetDate);
  return point ? point.value : null;
}

/**
 * Calculate DWAP deviation bands (similar to VWAP bands)
 * Shows standard deviation envelopes around DWAP
 */
export interface DWAPBand {
  date: string;
  upper: number;
  middle: number;
  lower: number;
}

export function calculateDWAPBands(
  candles: Candle[],
  stdDevMultiplier: number = 2,
  lookbackPeriod: number = 20
): DWAPBand[] {
  if (!candles || candles.length < lookbackPeriod) {
    return [];
  }

  const result: DWAPBand[] = [];

  for (let i = lookbackPeriod - 1; i < candles.length; i++) {
    const slice = candles.slice(i - lookbackPeriod + 1, i + 1);
    const dwapPoints = calculateDWAP(slice);

    if (dwapPoints.length === 0) continue;

    const currentDWAP = dwapPoints[dwapPoints.length - 1].value;

    // Calculate standard deviation of price from DWAP
    const deviations = slice.map((candle, idx) => {
      const tp = (candle.high + candle.low + candle.close) / 3;
      const dwap = dwapPoints[idx]?.value || tp;
      return Math.pow(tp - dwap, 2);
    });

    const avgDeviation = deviations.reduce((a, b) => a + b, 0) / deviations.length;
    const stdDev = Math.sqrt(avgDeviation);

    result.push({
      date: candles[i].date,
      upper: currentDWAP + (stdDev * stdDevMultiplier),
      middle: currentDWAP,
      lower: currentDWAP - (stdDev * stdDevMultiplier),
    });
  }

  return result;
}

/**
 * Render DWAP bands as area traces
 */
export function renderDWAPBands(
  candles: Candle[],
  stdDevMultiplier: number = 2,
  lookbackPeriod: number = 20,
  fillColor: string = 'rgba(255, 107, 53, 0.1)'
): Array<Partial<Plotly.Data>> {
  const bands = calculateDWAPBands(candles, stdDevMultiplier, lookbackPeriod);

  if (bands.length === 0) {
    return [];
  }

  // Upper band
  const upperTrace: Partial<Plotly.Data> = {
    x: bands.map((b) => b.date),
    y: bands.map((b) => b.upper),
    mode: 'lines',
    line: { color: '#FF6B35', width: 1, dash: 'dot' },
    name: 'DWAP Upper',
    hoverinfo: 'y+name',
    yaxis: 'y',
    showlegend: false,
  };

  // Lower band (reverse for area fill)
  const lowerTrace: Partial<Plotly.Data> = {
    x: [...bands.map((b) => b.date)].reverse(),
    y: [...bands.map((b) => b.lower)].reverse(),
    mode: 'lines',
    line: { color: '#FF6B35', width: 0 },
    name: 'DWAP Lower',
    hoverinfo: 'none',
    yaxis: 'y',
    showlegend: false,
    fill: 'tonexty',
    fillcolor: fillColor,
  };

  return [upperTrace, lowerTrace];
}

export default {
  calculateDWAP,
  renderDWAP,
  getDWAPAtDate,
  calculateDWAPBands,
  renderDWAPBands,
  DEFAULT_DWAP_CONFIG,
};
