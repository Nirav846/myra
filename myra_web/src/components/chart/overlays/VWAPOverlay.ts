/**
 * VWAP Overlay Indicator
 *
 * Volume Weighted Average Price rendered as a line overlay.
 * Uses precomputed DB vwap when available (correct daily-reset behavior);
 * falls back to client-side cumulative calculation for fixture/mock data.
 */

import type { IndicatorConfig, IndicatorModule } from '../registry/IndicatorRegistry';
import type { Candle } from '../../../core/technical-analysis/types';

export interface VWAPConfig extends IndicatorConfig {
  showDeviationBands?: boolean;
  deviationMultiplier?: number;
}

const DEFAULT_VWAP_CONFIG: VWAPConfig = {
  id: 'vwap',
  type: 'line',
  visible: true,
  color: '#ff6d00',
  zIndex: 11,
  showDeviationBands: false,
  deviationMultiplier: 2,
  settings: {},
};

/**
 * Return VWAP values — prefers precomputed DB column, falls back to
 * client-side daily-reset VWAP (resets at each new calendar day).
 */
function calculateVWAP(candles: Candle[]): (number | null)[] {
  const hasPrecomputed = candles.some(c => (c as any).vwap != null && (c as any).vwap !== 0);
  if (hasPrecomputed) {
    return candles.map(c => {
      const v = (c as any).vwap;
      return v != null && v !== 0 ? v : null;
    });
  }

  // Fallback: daily-reset VWAP (resets cumulative at each new date)
  const result: (number | null)[] = [];
  let cumulativePV = 0;
  let cumulativeV = 0;
  let prevDate = '';

  for (let i = 0; i < candles.length; i++) {
    const candle = candles[i];
    const currentDate = candle.date.slice(0, 10);

    // Reset at day boundary
    if (currentDate !== prevDate) {
      cumulativePV = 0;
      cumulativeV = 0;
      prevDate = currentDate;
    }

    const typicalPrice = (candle.high + candle.low + candle.close) / 3;
    const volume = candle.volume || (candle as any).volume_final || 0;

    cumulativePV += typicalPrice * volume;
    cumulativeV += volume;

    if (cumulativeV === 0) {
      result.push(null);
    } else {
      result.push(cumulativePV / cumulativeV);
    }
  }

  return result;
}

/**
 * Calculate VWAP standard deviation bands
 */
function calculateVWAPBands(candles: Candle[], multiplier: number = 2) {
  const vwapValues = calculateVWAP(candles);
  const upperBand: (number | null)[] = [];
  const lowerBand: (number | null)[] = [];

  // Calculate rolling standard deviation
  for (let i = 0; i < candles.length; i++) {
    if (vwapValues[i] === null) {
      upperBand.push(null);
      lowerBand.push(null);
      continue;
    }

    // Use all candles up to current point for std dev
    const prices = candles.slice(0, i + 1).map(c => c.close);
    const mean = prices.reduce((a, b) => a + b, 0) / prices.length;
    const variance = prices.reduce((sum, p) => sum + Math.pow(p - mean, 2), 0) / prices.length;
    const stdDev = Math.sqrt(variance);

    upperBand.push(vwapValues[i]! + (multiplier * stdDev));
    lowerBand.push(vwapValues[i]! - (multiplier * stdDev));
  }

  return { upper: upperBand, lower: lowerBand };
}

/**
 * Build VWAP trace for Plotly
 */
export function buildVWAPTrace(candles: Candle[], config: VWAPConfig) {
  const vwapValues = calculateVWAP(candles);
  const dates = candles.map(c => c.date);

  // Filter out null values
  const validPoints = dates.filter((_, i) => vwapValues[i] !== null);
  const validValues = vwapValues.filter(v => v !== null) as number[];

  const traces: any[] = [{
    type: 'scatter' as const,
    mode: 'lines' as const,
    x: validPoints,
    y: validValues,
    name: 'VWAP',
    line: {
      color: config.color,
      width: 1.5,
    },
    hovertemplate: 'VWAP: %{y:.2f}<extra></extra>',
    showlegend: true,
    xaxis: 'x',
    yaxis: 'y',
  }];

  // Add deviation bands if enabled
  if (config.showDeviationBands) {
    const bands = calculateVWAPBands(candles, config.deviationMultiplier);
    const bandUpper = bands.upper.filter(v => v !== null) as number[];
    const bandLower = bands.lower.filter(v => v !== null) as number[];

    // Upper band
    traces.push({
      type: 'scatter' as const,
      mode: 'lines' as const,
      x: validPoints,
      y: bandUpper,
      name: `VWAP +${config.deviationMultiplier}σ`,
      line: {
        color: config.color,
        width: 1,
        dash: 'dot',
      },
      hoverinfo: 'skip',
      showlegend: true,
      xaxis: 'x',
      yaxis: 'y',
    });

    // Lower band
    traces.push({
      type: 'scatter' as const,
      mode: 'lines' as const,
      x: validPoints,
      y: bandLower,
      name: `VWAP -${config.deviationMultiplier}σ`,
      line: {
        color: config.color,
        width: 1,
        dash: 'dot',
      },
      hoverinfo: 'skip',
      showlegend: true,
      xaxis: 'x',
      yaxis: 'y',
    });
  }

  return traces;
}

/**
 * VWAP indicator module for registry
 */
export const VWAPIndicator: IndicatorModule<VWAPConfig> = {
  id: 'vwap',
  config: { ...DEFAULT_VWAP_CONFIG },
  render: (data: any[], config: VWAPConfig) => {
    const candles = data as Candle[];
    if (candles.length === 0 || !config.visible) return [];
    return buildVWAPTrace(candles, config);
  },
};

export default VWAPIndicator;
