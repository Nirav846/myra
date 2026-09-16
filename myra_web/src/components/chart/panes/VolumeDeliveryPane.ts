/**
 * Volume + Delivery Combined Pane Renderer
 *
 * Merges volume histogram with delivery intensity overlay.
 * Delivery shown as color intensity modulation on volume bars.
 *
 * @module VolumeDeliveryPane
 */

import type { PlotData } from 'plotly.js-dist-min';
import type { Candle } from '../../../core/technical-analysis/types';

export interface VolumeDeliveryRenderOptions {
  /** Show delivery quantity as separate overlay (default: false - uses intensity) */
  showDeliverySeparate?: boolean;
  /** Delivery intensity multiplier (0.5-2.0, default: 1.0) */
  deliveryIntensity?: number;
  /** Show 20-period SMA of delivery (default: true) */
  showDeliverySMA?: boolean;
  /** Volume bar opacity base (0.3-0.9, default: 0.6) */
  volumeOpacity?: number;
  /** Color scheme: 'standard' | 'inverse' | 'monochrome' */
  colorScheme?: 'standard' | 'inverse' | 'monochrome';
}

const DEFAULT_OPTIONS: Required<VolumeDeliveryRenderOptions> = {
  showDeliverySeparate: false,
  deliveryIntensity: 1.0,
  showDeliverySMA: true,
  volumeOpacity: 0.6,
  colorScheme: 'standard',
};

/**
 * Calculate simple moving average
 */
function calculateSMA(data: number[], period: number): (number | null)[] {
  const result: (number | null)[] = [];

  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      result.push(null);
      continue;
    }

    const sum = data.slice(i - period + 1, i + 1).reduce((a, b) => a + b, 0);
    result.push(sum / period);
  }

  return result;
}

/**
 * Build combined volume + delivery traces for Plotly
 */
export function buildVolumeDeliveryPane(
  candles: Candle[],
  options: Partial<VolumeDeliveryRenderOptions> = {}
): PlotData[] {
  const opts = { ...DEFAULT_OPTIONS, ...options };
  const traces: PlotData[] = [];

  if (candles.length === 0) return [];

  // Extract data arrays
  const dates = candles.map(c => c.date);
  const volumes = candles.map(c => c.volume);
  const deliveries = candles.map(c => c.delivery || 0);
  const closes = candles.map(c => c.close);
  const opens = candles.map(c => c.open);

  // Calculate delivery ratio and intensity
  const deliveryRatios = deliveries.map((d, i) => {
    if (volumes[i] === 0) return 0;
    return d / volumes[i];
  });

  const maxDeliveryRatio = Math.max(...deliveryRatios.filter(r => r > 0));
  const avgVolume = volumes.reduce((a, b) => a + b, 0) / volumes.length;

  // Build volume bars with delivery intensity overlay
  const volumeColors = candles.map((candle, i) => {
    const isBullish = candle.close >= candle.open;
    const deliveryRatio = deliveryRatios[i];
    const normalizedIntensity = deliveryRatio / maxDeliveryRatio;
    const isHighVol = volumes[i] > avgVolume * 1.5;

    // Base colors by scheme
    let baseR: number, baseG: number, baseB: number;

    if (opts.colorScheme === 'inverse') {
      // Inverse: green for bearish, red for bullish (as per existing deliveryBuilder)
      baseR = isBullish ? 239 : 34;
      baseG = isBullish ? 68 : 197;
      baseB = isBullish ? 68 : 94;
    } else if (opts.colorScheme === 'monochrome') {
      baseR = baseG = baseB = isBullish ? 100 : 150;
    } else {
      // Standard: green for bullish, red for bearish
      baseR = isBullish ? 34 : 239;
      baseG = isBullish ? 197 : 68;
      baseB = isBullish ? 94 : 68;
    }

    // Modulate opacity based on delivery intensity
    const baseOpacity = opts.volumeOpacity;
    const intensityBoost = normalizedIntensity * 0.4 * opts.deliveryIntensity;
    const finalOpacity = Math.min(baseOpacity + intensityBoost, 0.95);

    // Highlight high volume days
    const volBoost = isHighVol ? 0.1 : 0;
    const adjustedOpacity = Math.min(finalOpacity + volBoost, 1.0);

    return `rgba(${baseR}, ${baseG}, ${baseB}, ${adjustedOpacity})`;
  });

  // Main volume bar trace
  const volumeTrace: PlotData = {
    type: 'bar',
    x: dates,
    y: volumes,
    name: 'Volume',
    marker: {
      color: volumeColors,
      line: { width: 0 },
    },
    hovertemplate: [
      '<b>Volume</b>',
      'Date: %{x}',
      'Vol: %{y:.2s}',
      'Del: %{customdata[0]:.2s}',
      'Del%: %{customdata[1]:.1f}%<extra></extra>',
    ].join('<br>'),
    customdata: candles.map((_, i) => [deliveries[i], deliveryRatios[i] * 100]),
    showlegend: false,
    yaxis: 'y2',
  };

  traces.push(volumeTrace);

  // Optional: Add delivery SMA line
  if (opts.showDeliverySMA) {
    const deliverySMA = calculateSMA(deliveries, 20);

    const smaTrace: PlotData = {
      type: 'scatter',
      mode: 'lines',
      x: dates,
      y: deliverySMA.map(v => v ?? null),
      name: 'Del SMA(20)',
      line: {
        color: '#f59e0b',
        width: 2,
        dash: 'solid',
      },
      hovertemplate: 'Del SMA(20): %{y:.2s}<extra></extra>',
      showlegend: true,
      legendgroup: 'delivery',
      yaxis: 'y2',
    };

    traces.push(smaTrace);
  }

  // Optional: Show delivery as separate bars (alternative view)
  if (opts.showDeliverySeparate) {
    const deliveryColors = candles.map(c => {
      const isBullish = c.close >= c.open;
      return isBullish ? '#22c55e' : '#ef4444';
    });

    const deliveryTrace: PlotData = {
      type: 'bar',
      x: dates,
      y: deliveries,
      name: 'Delivery Qty',
      marker: {
        color: deliveryColors,
        opacity: 0.7,
        line: { width: 0 },
      },
      hovertemplate: 'Delivery: %{y:.2s}<extra></extra>',
      showlegend: false,
      yaxis: 'y2',
    };

    traces.push(deliveryTrace);
  }

  return traces;
}

/**
 * Build layout configuration for volume pane (sub-chart below main price chart)
 */
export function buildVolumePaneLayout() {
  return {
    domain: { y: [0, 0.25] }, // Bottom 25% of chart area
    title: {
      text: 'Volume + Delivery',
      font: { size: 11, color: '#888899' },
      standoff: 5,
    },
    gridcolor: '#2a2c34',
    zeroline: false,
    showline: false,
    tickfont: {
      family: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
      size: 10,
      color: '#888899',
    },
    tickformat: '.2s',
    automargin: true,
  };
}

/**
 * Get volume statistics for display
 */
export function getVolumeStats(candles: Candle[]) {
  if (candles.length === 0) {
    return {
      avgVolume: 0,
      avgDelivery: 0,
      avgDeliveryPct: 0,
      maxVolume: 0,
      maxDelivery: 0,
    };
  }

  const volumes = candles.map(c => c.volume);
  const deliveries = candles.map(c => c.delivery || 0);
  const deliveryPcts = candles.map(c => (c.delivery_pct || 0) / 100);

  return {
    avgVolume: Math.round(volumes.reduce((a, b) => a + b, 0) / volumes.length),
    avgDelivery: Math.round(deliveries.reduce((a, b) => a + b, 0) / deliveries.length),
    avgDeliveryPct: Math.round(deliveryPcts.reduce((a, b) => a + b, 0) / deliveryPcts.length * 100),
    maxVolume: Math.max(...volumes),
    maxDelivery: Math.max(...deliveries),
  };
}

export default buildVolumeDeliveryPane;
