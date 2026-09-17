/**
 * Candlestick Renderer
 *
 * Pure function module for building Plotly candlestick traces.
 * No React dependencies - can be used in workers or tests.
 */

import type { PlotData } from 'plotly.js-dist-min';
import { Candle } from '../../core/technical-analysis/types';

export interface CandlestickRenderOptions {
  candleColorUp: string;
  candleColorDown: string;
  wickColorUp: string;
  wickColorDown: string;
  candleWidth: number;
  showWicks: boolean;
}

const DEFAULT_OPTIONS: CandlestickRenderOptions = {
  candleColorUp: '#26a69a',
  candleColorDown: '#ef5350',
  wickColorUp: '#26a69a',
  wickColorDown: '#ef5350',
  candleWidth: 0.8,
  showWicks: true,
};

/**
 * Build candlestick trace data for Plotly
 */
export function buildCandlestickTrace(
  candles: Candle[],
  options: Partial<CandlestickRenderOptions> = {}
): PlotData[] {
  const opts = { ...DEFAULT_OPTIONS, ...options };

  if (candles.length === 0) return [];

  // Extract arrays for Plotly
  const xValues: string[] = [];
  const open: number[] = [];
  const high: number[] = [];
  const low: number[] = [];
  const close: number[] = [];

  candles.forEach((candle, index) => {
    xValues.push(candle.date);
    open.push(candle.open);
    high.push(candle.high);
    low.push(candle.low);
    close.push(candle.close);
  });

  // Build increasing candles (close >= open)
  const increasing: PlotData = {
    type: 'candlestick' as const,
    x: xValues,
    open,
    high,
    low,
    close,
    name: 'Price',
    increasing: {
      line: { color: opts.candleColorUp, width: opts.candleWidth },
      fillcolor: opts.candleColorUp,
    },
    decreasing: {
      line: { color: 'transparent', width: 0 },
      fillcolor: 'transparent',
    },
    hovertemplate: [
      '<b>%{fullData.name}</b>',
      'Date: %{x}',
      'Open: %{open:.2f}',
      'High: %{high:.2f}',
      'Low: %{low:.2f}',
      'Close: %{close:.2f}<extra></extra>',
    ].join('<br>'),
    showlegend: false,
    xaxis: 'x',
    yaxis: 'y',
  };

  // Build decreasing candles (close < open)
  const decreasing: PlotData = {
    type: 'candlestick' as const,
    x: xValues,
    open,
    high,
    low,
    close,
    name: '',
    increasing: {
      line: { color: 'transparent', width: 0 },
      fillcolor: 'transparent',
    },
    decreasing: {
      line: { color: opts.candleColorDown, width: opts.candleWidth },
      fillcolor: opts.candleColorDown,
    },
    hoverinfo: 'skip',
    showlegend: false,
    xaxis: 'x',
    yaxis: 'y',
  };

  // Optional: Add wick lines for better visibility
  const wickTraces: PlotData[] = [];

  if (opts.showWicks) {
    // Increasing wicks
    wickTraces.push({
      type: 'scatter' as const,
      mode: 'lines' as const,
      x: xValues.flatMap((_, i) => [xValues[i], xValues[i], null]),
      y: high.flatMap((h, i) => [h, Math.max(open[i], close[i]), null]),
      name: '',
      line: {
        color: opts.wickColorUp,
        width: 1,
      },
      hoverinfo: 'skip',
      showlegend: false,
      xaxis: 'x',
      yaxis: 'y',
    });

    // Decreasing wicks
    wickTraces.push({
      type: 'scatter' as const,
      mode: 'lines' as const,
      x: xValues.flatMap((_, i) => [xValues[i], xValues[i], null]),
      y: low.flatMap((l, i) => [l, Math.min(open[i], close[i]), null]),
      name: '',
      line: {
        color: opts.wickColorDown,
        width: 1,
      },
      hoverinfo: 'skip',
      showlegend: false,
      xaxis: 'x',
      yaxis: 'y',
    });
  }

  return [increasing, decreasing, ...wickTraces];
}

/**
 * Extract date array from candles for x-axis mapping
 */
export function extractDates(candles: Candle[]): string[] {
  return candles.map(c => c.date);
}

/**
 * Get price range for y-axis auto-scaling
 */
export function getPriceRange(candles: Candle[]): { min: number; max: number } {
  if (candles.length === 0) {
    return { min: 0, max: 100 };
  }

  let min = Infinity;
  let max = -Infinity;

  candles.forEach(candle => {
    if (candle.low < min) min = candle.low;
    if (candle.high > max) max = candle.high;
  });

  // Add 5% padding
  const range = max - min;
  const padding = range * 0.05;

  return {
    min: Math.floor((min - padding) * 100) / 100,
    max: Math.ceil((max + padding) * 100) / 100,
  };
}
