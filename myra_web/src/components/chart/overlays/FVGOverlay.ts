/**
 * FVG (Fair Value Gap) Overlay Indicator
 *
 * Highlights imbalances in price action where there's a gap between
 * the wick of one candle and the body of another.
 */

import type { IndicatorConfig, IndicatorModule } from '../registry/IndicatorRegistry';
import type { Candle } from '../../../core/technical-analysis/types';

export interface FVGConfig extends IndicatorConfig {
  minGapSize?: number; // Minimum gap size as percentage of candle range
  showBullish: boolean;
  showBearish: boolean;
}

const DEFAULT_FVG_CONFIG: FVGConfig = {
  id: 'fvg',
  type: 'highlight',
  visible: true,
  color: '#ffd700',
  zIndex: 5,
  minGapSize: 0.001, // 0.1% of price
  showBullish: true,
  showBearish: true,
  settings: {},
};

export interface FVGZone {
  startIdx: number;
  endIdx: number;
  startPrice: number;
  endPrice: number;
  isBullish: boolean;
}

/**
 * Detect Fair Value Gaps in price data
 */
function detectFVGs(candles: Candle[], minGapSize: number): FVGZone[] {
  const fvgZones: FVGZone[] = [];

  for (let i = 2; i < candles.length; i++) {
    const prevCandle = candles[i - 2];
    const middleCandle = candles[i - 1];
    const currentCandle = candles[i];

    // Bullish FVG: Current low > Previous high with gap in middle candle
    if (currentCandle.low > prevCandle.high) {
      const gapStart = prevCandle.high;
      const gapEnd = currentCandle.low;
      const gapSize = (gapEnd - gapStart) / gapStart;

      if (gapSize >= minGapSize) {
        fvgZones.push({
          startIdx: i - 1,
          endIdx: i,
          startPrice: gapStart,
          endPrice: gapEnd,
          isBullish: true,
        });
      }
    }

    // Bearish FVG: Current high < Previous low with gap in middle candle
    if (currentCandle.high < prevCandle.low) {
      const gapStart = prevCandle.low;
      const gapEnd = currentCandle.high;
      const gapSize = (gapStart - gapEnd) / gapStart;

      if (gapSize >= minGapSize) {
        fvgZones.push({
          startIdx: i - 1,
          endIdx: i,
          startPrice: gapStart,
          endPrice: gapEnd,
          isBullish: false,
        });
      }
    }
  }

  return fvgZones;
}

/**
 * Build FVG rectangle shapes for Plotly
 */
export function buildFVGShapes(candles: Candle[], config: FVGConfig) {
  const fvgZones = detectFVGs(candles, config.minGapSize || 0.001);
  const shapes: any[] = [];

  fvgZones.forEach((zone, index) => {
    // Skip if not matching visibility filter
    if (zone.isBullish && !config.showBullish) return;
    if (!zone.isBullish && !config.showBearish) return;

    const color = zone.isBullish ? 'rgba(38, 166, 154, 0.3)' : 'rgba(239, 83, 80, 0.3)';
    const borderColor = zone.isBullish ? '#26a69a' : '#ef5350';

    shapes.push({
      type: 'rect',
      xref: 'paper',
      yref: 'y',
      x0: zone.startIdx - 0.5,
      x1: zone.endIdx + 0.5,
      y0: Math.min(zone.startPrice, zone.endPrice),
      y1: Math.max(zone.startPrice, zone.endPrice),
      fillcolor: color,
      line: {
        color: borderColor,
        width: 1,
        dash: 'dot',
      },
      layer: 'below',
    });
  });

  return shapes;
}

/**
 * Build FVG annotation labels
 */
export function buildFVGAnnotations(candles: Candle[], config: FVGConfig) {
  const fvgZones = detectFVGs(candles, config.minGapSize || 0.001);
  const annotations: any[] = [];

  fvgZones.forEach((zone) => {
    // Skip if not matching visibility filter
    if (zone.isBullish && !config.showBullish) return;
    if (!zone.isBullish && !config.showBearish) return;

    const midIndex = (zone.startIdx + zone.endIdx) / 2;
    const midPrice = (zone.startPrice + zone.endPrice) / 2;
    const gapSize = Math.abs(zone.endPrice - zone.startPrice);
    const gapPct = (gapSize / zone.startPrice) * 100;

    annotations.push({
      x: midIndex,
      y: midPrice,
      xref: 'x',
      yref: 'y',
      text: `FVG ${gapPct.toFixed(2)}%`,
      showarrow: false,
      font: {
        size: 9,
        color: zone.isBullish ? '#26a69a' : '#ef5350',
      },
      bgcolor: 'rgba(0,0,0,0.7)',
      borderpad: 2,
    });
  });

  return annotations;
}

/**
 * FVG indicator module for registry
 */
export const FVGIndicator: IndicatorModule<FVGConfig> = {
  id: 'fvg',
  config: { ...DEFAULT_FVG_CONFIG },
  render: (data: any[], config: FVGConfig) => {
    const candles = data as Candle[];
    if (candles.length === 0 || !config.visible) return [];

    // FVG uses shapes and annotations, not standard traces
    // These will be extracted by the chart component
    const shapes = buildFVGShapes(candles, config);
    const annotations = buildFVGAnnotations(candles, config);

    // Return as special trace objects that chart can interpret
    return [
      {
        type: 'shape_collection',
        shapes,
        annotations,
        _fvgData: { shapes, annotations },
      },
    ];
  },
};

export default FVGIndicator;
