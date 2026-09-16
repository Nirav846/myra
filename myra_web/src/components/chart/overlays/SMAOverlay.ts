/**
 * SMA Overlay Indicator
 *
 * Simple Moving Average rendered as a line overlay.
 */

import type { IndicatorConfig, IndicatorModule } from '../registry/IndicatorRegistry';
import type { Candle } from '../../../core/technical-analysis/types';

export interface SMAConfig extends IndicatorConfig {
  period: number;
}

const DEFAULT_SMA_CONFIG: SMAConfig = {
  id: 'sma',
  type: 'line',
  visible: true,
  color: '#2962ff',
  zIndex: 10,
  period: 20,
  settings: {},
};

/**
 * Calculate SMA values for given period
 */
function calculateSMA(candles: Candle[], period: number): (number | null)[] {
  const result: (number | null)[] = [];

  for (let i = 0; i < candles.length; i++) {
    if (i < period - 1) {
      result.push(null);
      continue;
    }

    let sum = 0;
    for (let j = 0; j < period; j++) {
      sum += candles[i - j].close;
    }

    result.push(sum / period);
  }

  return result;
}

/**
 * Build SMA trace for Plotly
 */
export function buildSMATrace(candles: Candle[], config: SMAConfig) {
  const smaValues = calculateSMA(candles, config.period);
  const dates = candles.map(c => c.date);

  // Filter out null values for Plotly
  const validPoints = dates.filter((_, i) => smaValues[i] !== null);
  const validValues = smaValues.filter(v => v !== null) as number[];

  return {
    type: 'scatter' as const,
    mode: 'lines' as const,
    x: validPoints,
    y: validValues,
    name: `SMA(${config.period})`,
    line: {
      color: config.color,
      width: 1.5,
    },
    hovertemplate: `SMA(${config.period}): %{y:.2f}<extra></extra>`,
    showlegend: true,
    xaxis: 'x',
    yaxis: 'y',
  };
}

/**
 * SMA indicator module for registry
 */
export const SMAIndicator: IndicatorModule<SMAConfig> = {
  id: 'sma',
  config: { ...DEFAULT_SMA_CONFIG },
  render: (data: any[], config: SMAConfig) => {
    const candles = data as Candle[];
    if (candles.length === 0 || !config.visible) return [];
    return [buildSMATrace(candles, config)];
  },
};

export default SMAIndicator;
