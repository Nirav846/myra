/**
 * Delivery Thrust Candles Overlay
 *
 * Visual overlay that paints specific candles to highlight sudden accumulation/distribution.
 *
 * Logic:
 * - Calculate 20-period SMA of Delivery Volume
 * - If candle's Delivery Volume > 200% of SMA AND close in top 25% of range → Paint Cyan/Gold (bullish thrust)
 * - If candle's Delivery Volume > 200% of SMA AND close in bottom 25% of range → Paint Magenta/Purple (bearish thrust)
 *
 * Allows instant visual scanning for days with massive uncharacteristic delivery.
 */

import type { IndicatorConfig } from '../registry/IndicatorRegistry';
import type { Candle } from '../../../core/technical-analysis/types';

export interface DeliveryThrustConfig extends IndicatorConfig {
  type: 'highlight'; // Must match base IndicatorConfig type union
  visible: boolean;
  bullishColor: string;
  bearishColor: string;
  smaPeriod: number;
  thresholdMultiplier: number;
  label: string;
  zIndex: number; // Required by base interface
}

export const DEFAULT_DELIVERY_THRUST_CONFIG: DeliveryThrustConfig = {
  id: 'delivery-thrust',
  type: 'highlight',
  visible: false,
  color: '#00FFFF', // Base color (bullish)
  bullishColor: '#00FFFF', // Cyan for bullish thrust
  bearishColor: '#FF00FF', // Magenta for bearish thrust
  smaPeriod: 20,
  thresholdMultiplier: 2.0, // 200% of SMA
  label: 'Delivery Thrust',
  zIndex: 10,
};

export interface ThrustCandle {
  date: string;
  isBullish: boolean;
  deliveryVolume: number;
  smaDelivery: number;
  percentileInRange: number; // 0-1, where 1 = close at high, 0 = close at low
}

/**
 * Calculate Simple Moving Average of delivery volume
 */
function calculateSMA(values: number[], period: number): number {
  if (values.length < period) {
    return values.reduce((a, b) => a + b, 0) / values.length;
  }
  const slice = values.slice(-period);
  return slice.reduce((a, b) => a + b, 0) / period;
}

/**
 * Calculate percentile position of close within day's range
 * Returns 0-1 where:
 * - 1 = close at high (top of range)
 * - 0 = close at low (bottom of range)
 * - 0.5 = close in middle
 */
function calculateClosePercentile(candle: Candle): number {
  const range = candle.high - candle.low;
  if (range === 0) return 0.5;
  return (candle.close - candle.low) / range;
}

/**
 * Identify delivery thrust candles
 */
export function identifyThrustCandles(
  candles: Candle[],
  config: DeliveryThrustConfig = DEFAULT_DELIVERY_THRUST_CONFIG
): ThrustCandle[] {
  if (!candles || candles.length < config.smaPeriod) {
    return [];
  }

  const result: ThrustCandle[] = [];

  for (let i = config.smaPeriod - 1; i < candles.length; i++) {
    const candle = candles[i];
    const deliveryVolume = candle.deliveryQuantity || 0;

    // Get historical delivery volumes for SMA calculation
    const historicalVolumes = candles
      .slice(0, i + 1)
      .map((c) => c.deliveryQuantity || 0);

    const smaDelivery = calculateSMA(historicalVolumes, config.smaPeriod);
    const threshold = smaDelivery * config.thresholdMultiplier;

    // Check if this is a thrust candle
    if (deliveryVolume > threshold) {
      const percentile = calculateClosePercentile(candle);
      const isBullish = percentile >= 0.75; // Close in top 25%
      const isBearish = percentile <= 0.25; // Close in bottom 25%

      if (isBullish || isBearish) {
        result.push({
          date: candle.date,
          isBullish,
          deliveryVolume,
          smaDelivery,
          percentileInRange: percentile,
        });
      }
    }
  }

  return result;
}

/**
 * Render thrust candles as marker overlays on the price chart
 * Uses Plotly markers to highlight specific candles
 */
export function renderDeliveryThrust(
  candles: Candle[],
  config: DeliveryThrustConfig = DEFAULT_DELIVERY_THRUST_CONFIG
): Array<Partial<Plotly.PlotData>> {
  const thrustCandles = identifyThrustCandles(candles, config);

  if (thrustCandles.length === 0) {
    return [];
  }

  const bullishCandles = thrustCandles.filter((t) => t.isBullish);
  const bearishCandles = thrustCandles.filter((t) => !t.isBullish);

  const traces: Array<Partial<Plotly.PlotData>> = [];

  // Bullish thrust markers (above candles)
  if (bullishCandles.length > 0) {
    const bullishCandle = candles.filter((c) =>
      bullishCandles.some((t) => t.date === c.date)
    );

    traces.push({
      x: bullishCandle.map((c) => c.date),
      y: bullishCandle.map((c) => c.high),
      mode: 'markers',
      marker: {
        symbol: 'triangle-up',
        size: 12,
        color: config.bullishColor,
        line: { width: 2, color: '#FFFFFF' },
      },
      name: `${config.label} (Bullish)`,
      hoverinfo: 'x+y+name',
      hovertemplate: '<b>Bullish Delivery Thrust</b><br>Date: %{x}<br>High: %{y}<extra></extra>',
      yaxis: 'y',
      showlegend: config.visible,
    });
  }

  // Bearish thrust markers (below candles)
  if (bearishCandles.length > 0) {
    const bearishCandle = candles.filter((c) =>
      bearishCandles.some((t) => t.date === c.date)
    );

    traces.push({
      x: bearishCandle.map((c) => c.date),
      y: bearishCandle.map((c) => c.low),
      mode: 'markers',
      marker: {
        symbol: 'triangle-down',
        size: 12,
        color: config.bearishColor,
        line: { width: 2, color: '#FFFFFF' },
      },
      name: `${config.label} (Bearish)`,
      hoverinfo: 'x+y+name',
      hovertemplate: '<b>Bearish Delivery Thrust</b><br>Date: %{x}<br>Low: %{y}<extra></extra>',
      yaxis: 'y',
      showlegend: config.visible,
    });
  }

  return traces;
}

/**
 * Get thrust candle info for a specific date (for tooltips)
 */
export function getThrustInfoAtDate(
  candles: Candle[],
  targetDate: string,
  config: DeliveryThrustConfig = DEFAULT_DELIVERY_THRUST_CONFIG
): ThrustCandle | null {
  const thrustCandles = identifyThrustCandles(candles, config);
  return thrustCandles.find((t) => t.date === targetDate) || null;
}

/**
 * Generate summary statistics for delivery thrust analysis
 */
export function getThrustStatistics(
  candles: Candle[],
  config: DeliveryThrustConfig = DEFAULT_DELIVERY_THRUST_CONFIG
): {
  totalThrustDays: number;
  bullishCount: number;
  bearishCount: number;
  avgDeliveryVsSMA: number;
  lastThrustDate: string | null;
} {
  const thrustCandles = identifyThrustCandles(candles, config);

  if (thrustCandles.length === 0) {
    return {
      totalThrustDays: 0,
      bullishCount: 0,
      bearishCount: 0,
      avgDeliveryVsSMA: 0,
      lastThrustDate: null,
    };
  }

  const bullishCount = thrustCandles.filter((t) => t.isBullish).length;
  const bearishCount = thrustCandles.length - bullishCount;

  const avgDeliveryVsSMA =
    thrustCandles.reduce((sum, t) => sum + t.deliveryVolume / t.smaDelivery, 0) /
    thrustCandles.length;

  return {
    totalThrustDays: thrustCandles.length,
    bullishCount,
    bearishCount,
    avgDeliveryVsSMA,
    lastThrustDate: thrustCandles[thrustCandles.length - 1]?.date || null,
  };
}

export default {
  identifyThrustCandles,
  renderDeliveryThrust,
  getThrustInfoAtDate,
  getThrustStatistics,
  DEFAULT_DELIVERY_THRUST_CONFIG,
};
