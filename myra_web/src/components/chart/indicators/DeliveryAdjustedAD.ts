/**
 * Delivery-Adjusted Accumulation/Distribution (DA-AD)
 *
 * Modification of classic A/D Line powered by delivery data.
 *
 * Traditional A/D uses total volume, which gets skewed on high-volatility,
 * high-intraday-churn days. Using Delivery Volume isolates whether shares
 * being held overnight are being accumulated or distributed.
 *
 * Logic:
 * Money Flow Multiplier = ((Close - Low) - (High - Close)) / (High - Low)
 * Delivery Money Flow Volume = Money Flow Multiplier * Delivery Volume
 * DA-AD = Previous DA-AD + Delivery Money Flow Volume
 */

import type { IndicatorConfig } from '../registry/IndicatorRegistry';
import type { Candle } from '../../../core/technical-analysis/types';

export interface DAADConfig extends IndicatorConfig {
  type: 'line'; // Must match base IndicatorConfig type union
  visible: boolean;
  color: string;
  lineWidth: number;
  label: string;
  showHistogram?: boolean;
  zIndex: number; // Required by base interface
}

export const DEFAULT_DAAD_CONFIG: DAADConfig = {
  id: 'da-ad',
  type: 'line',
  visible: false,
  color: '#9C27B0', // Purple to distinguish from other indicators
  lineWidth: 2,
  label: 'DA-AD',
  showHistogram: false,
  zIndex: 6,
};

export interface DAADPoint {
  date: string;
  value: number;
  moneyFlowMultiplier: number;
  deliveryMoneyFlowVolume: number;
}

/**
 * Calculate Money Flow Multiplier
 * Range: -1 to +1
 * - Positive when close is in upper half of range (buying pressure)
 * - Negative when close is in lower half (selling pressure)
 */
function calculateMoneyFlowMultiplier(candle: Candle): number {
  const range = candle.high - candle.low;

  if (range === 0) {
    return 0;
  }

  return ((candle.close - candle.low) - (candle.high - candle.close)) / range;
}

/**
 * Calculate DA-AD line values
 */
export function calculateDAAD(
  candles: Candle[],
  config: DAADConfig = DEFAULT_DAAD_CONFIG
): DAADPoint[] {
  if (!candles || candles.length === 0) {
    return [];
  }

  const result: DAADPoint[] = [];
  let cumulativeAD = 0;

  for (const candle of candles) {
    const mfm = calculateMoneyFlowMultiplier(candle);
    const deliveryVolume = candle.delivery || 0;
    const deliveryMFV = mfm * deliveryVolume;

    cumulativeAD += deliveryMFV;

    result.push({
      date: candle.date,
      value: cumulativeAD,
      moneyFlowMultiplier: mfm,
      deliveryMoneyFlowVolume: deliveryMFV,
    });
  }

  return result;
}

/**
 * Render DA-AD line for a sub-pane
 */
export function renderDAAD(
  candles: Candle[],
  config: DAADConfig = DEFAULT_DAAD_CONFIG
): Array<Partial<Plotly.Data>> {
  const points = calculateDAAD(candles, config);

  if (points.length === 0) {
    return [];
  }

  const traces: Array<Partial<Plotly.Data>> = [];

  // Main DA-AD line
  traces.push({
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
    yaxis: 'y4', // Sub-pane y-axis
    showlegend: config.visible,
  });

  // Optional histogram showing daily delivery money flow
  if (config.showHistogram) {
    const positiveMFV = points.map((p) => (p.deliveryMoneyFlowVolume > 0 ? p.deliveryMoneyFlowVolume : 0));
    const negativeMFV = points.map((p) => (p.deliveryMoneyFlowVolume < 0 ? p.deliveryMoneyFlowVolume : 0));

    // Positive bars
    traces.push({
      x: points.map((p) => p.date),
      y: positiveMFV,
      type: 'bar',
      marker: {
        color: 'rgba(76, 175, 80, 0.7)', // Green
      },
      name: 'Delivery Inflow',
      hoverinfo: 'y+name',
      yaxis: 'y4',
      showlegend: config.visible,
    });

    // Negative bars
    traces.push({
      x: points.map((p) => p.date),
      y: negativeMFV,
      type: 'bar',
      marker: {
        color: 'rgba(244, 67, 54, 0.7)', // Red
      },
      name: 'Delivery Outflow',
      hoverinfo: 'y+name',
      yaxis: 'y4',
      showlegend: config.visible,
    });
  }

  return traces;
}

/**
 * Get DA-AD value for a specific date (for crosshair tooltips)
 */
export function getDAADAtDate(
  candles: Candle[],
  targetDate: string,
  config: DAADConfig = DEFAULT_DAAD_CONFIG
): DAADPoint | null {
  const points = calculateDAAD(candles, config);
  return points.find((p) => p.date === targetDate) || null;
}

/**
 * Detect divergences between DA-AD and price
 *
 * Bullish Divergence: Price makes Lower Low, DA-AD makes Higher Low
 * Bearish Divergence: Price makes Higher High, DA-AD makes Lower High
 */
export interface DivergenceSignal {
  date: string;
  type: 'bullish' | 'bearish';
  priceValue: number;
  daadValue: number;
  lookbackPeriod: number;
}

export function detectDivergences(
  candles: Candle[],
  lookbackPeriod: number = 10,
  config: DAADConfig = DEFAULT_DAAD_CONFIG
): DivergenceSignal[] {
  if (!candles || candles.length < lookbackPeriod * 2) {
    return [];
  }

  const daadPoints = calculateDAAD(candles, config);
  const signals: DivergenceSignal[] = [];

  for (let i = lookbackPeriod; i < candles.length; i++) {
    const currentPrice = candles[i].close;
    const currentDAAD = daadPoints[i].value;

    // Get lookback window
    const priceWindow = candles.slice(i - lookbackPeriod, i);
    const daadWindow = daadPoints.slice(i - lookbackPeriod, i);

    const maxPrice = Math.max(...priceWindow.map((c) => c.close));
    const minPrice = Math.min(...priceWindow.map((c) => c.close));
    const maxDAAD = Math.max(...daadWindow.map((p) => p.value));
    const minDAAD = Math.min(...daadWindow.map((p) => p.value));

    // Bearish Divergence: Price makes Higher High, DA-AD makes Lower High
    if (currentPrice > maxPrice && currentDAAD < maxDAAD) {
      signals.push({
        date: candles[i].date,
        type: 'bearish',
        priceValue: currentPrice,
        daadValue: currentDAAD,
        lookbackPeriod,
      });
    }

    // Bullish Divergence: Price makes Lower Low, DA-AD makes Higher Low
    if (currentPrice < minPrice && currentDAAD > minDAAD) {
      signals.push({
        date: candles[i].date,
        type: 'bullish',
        priceValue: currentPrice,
        daadValue: currentDAAD,
        lookbackPeriod,
      });
    }
  }

  return signals;
}

/**
 * Render divergence markers on the DA-AD pane
 */
export function renderDivergences(
  candles: Candle[],
  lookbackPeriod: number = 10,
  config: DAADConfig = DEFAULT_DAAD_CONFIG
): Array<Partial<Plotly.Data>> {
  const signals = detectDivergences(candles, lookbackPeriod, config);

  if (signals.length === 0) {
    return [];
  }

  const bullishSignals = signals.filter((s) => s.type === 'bullish');
  const bearishSignals = signals.filter((s) => s.type === 'bearish');

  const traces: Array<Partial<Plotly.Data>> = [];

  // Bullish divergence markers
  if (bullishSignals.length > 0) {
    const bullishDates = bullishSignals.map((s) => s.date);
    const bullishCandles = candles.filter((c) => bullishDates.includes(c.date));
    const bullishDAAD = calculateDAAD(bullishCandles, config);

    traces.push({
      x: bullishCandles.map((c) => c.date),
      y: bullishDAAD.map((p) => p.value),
      mode: 'text+markers',
      marker: {
        symbol: 'triangle-up',
        size: 14,
        color: '#4CAF50', // Green
        line: { width: 2, color: '#FFFFFF' },
      },
      text: bullishCandles.map(() => '▲'),
      textposition: 'top center',
      textfont: { color: '#4CAF50', size: 12 },
      name: 'DA-AD Bullish Div',
      hoverinfo: 'x+y+name',
      hovertemplate: '<b>Bullish Divergence</b><br>Date: %{x}<br>DA-AD: %{y}<extra></extra>',
      yaxis: 'y4',
      showlegend: config.visible,
    });
  }

  // Bearish divergence markers
  if (bearishSignals.length > 0) {
    const bearishDates = bearishSignals.map((s) => s.date);
    const bearishCandles = candles.filter((c) => bearishDates.includes(c.date));
    const bearishDAAD = calculateDAAD(bearishCandles, config);

    traces.push({
      x: bearishCandles.map((c) => c.date),
      y: bearishDAAD.map((p) => p.value),
      mode: 'text+markers',
      marker: {
        symbol: 'triangle-down',
        size: 14,
        color: '#F44336', // Red
        line: { width: 2, color: '#FFFFFF' },
      },
      text: bearishCandles.map(() => '▼'),
      textposition: 'bottom center',
      textfont: { color: '#F44336', size: 12 },
      name: 'DA-AD Bearish Div',
      hoverinfo: 'x+y+name',
      hovertemplate: '<b>Bearish Divergence</b><br>Date: %{x}<br>DA-AD: %{y}<extra></extra>',
      yaxis: 'y4',
      showlegend: config.visible,
    });
  }

  return traces;
}

/**
 * Calculate DA-AD trend strength (slope over recent period)
 */
export function calculateDAADTrend(
  candles: Candle[],
  lookbackPeriod: number = 10,
  config: DAADConfig = DEFAULT_DAAD_CONFIG
): {
  slope: number;
  direction: 'rising' | 'falling' | 'flat';
  strength: 'strong' | 'moderate' | 'weak';
} {
  const points = calculateDAAD(candles, config);

  if (points.length < lookbackPeriod) {
    return { slope: 0, direction: 'flat', strength: 'weak' };
  }

  const recent = points.slice(-lookbackPeriod);
  const firstValue = recent[0].value;
  const lastValue = recent[recent.length - 1].value;

  const slope = (lastValue - firstValue) / lookbackPeriod;
  const avgValue = Math.abs((firstValue + lastValue) / 2);
  const relativeSlope = avgValue > 0 ? slope / avgValue : slope;

  const direction = slope > 0.001 ? 'rising' : slope < -0.001 ? 'falling' : 'flat';

  const absRelativeSlope = Math.abs(relativeSlope);
  const strength = absRelativeSlope > 0.05 ? 'strong' : absRelativeSlope > 0.02 ? 'moderate' : 'weak';

  return { slope, direction, strength };
}

export default {
  calculateDAAD,
  renderDAAD,
  getDAADAtDate,
  detectDivergences,
  renderDivergences,
  calculateDAADTrend,
  DEFAULT_DAAD_CONFIG,
};
