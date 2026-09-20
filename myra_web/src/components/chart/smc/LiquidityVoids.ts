/**
 * Liquidity Voids — SMC Indicator
 *
 * Detects liquidity voids (breakaway gaps, high-volume thrust candles)
 * using ATR-based volatility thresholds and volume filters.
 * Ported from V1: myra_web/src/lib/liquidityVoid.ts
 */

import type { Candle } from '../../../core/technical-analysis/types';

export type VoidFillStatus = 'unfilled' | 'partial' | 'filled';

export interface LiquidityVoid {
  id: string;
  startDate: string;
  endDate: string | null;
  direction: 'bullish' | 'bearish';
  topPrice: number;
  bottomPrice: number;
  status: VoidFillStatus;
  strength: number;
}

export interface LiquidityVoidsConfig {
  minAtrMultiplier: number;
  maxVolumeMultiplier: number;
  minStrengthScore: number;
  hideFilledVoids: boolean;
  volatilityScaling: boolean;
  atrPeriod: number;
  volPeriod: number;
}

const DEFAULT_CONFIG: LiquidityVoidsConfig = {
  minAtrMultiplier: 1.5,
  maxVolumeMultiplier: 0.8,
  minStrengthScore: 50,
  hideFilledVoids: false,
  volatilityScaling: true,
  atrPeriod: 14,
  volPeriod: 20,
};

const VOID_THRESHOLDS: Record<string, { minAtrMultiplier: number; maxVolumeMultiplier: number }> = {
  'Large Cap (N50)': { minAtrMultiplier: 1.2, maxVolumeMultiplier: 0.9 },
  'Large Cap (N100)': { minAtrMultiplier: 1.3, maxVolumeMultiplier: 0.85 },
  'Broader Market (N500)': { minAtrMultiplier: 1.5, maxVolumeMultiplier: 0.8 },
  'Nifty Small Cap 250': { minAtrMultiplier: 1.8, maxVolumeMultiplier: 0.7 },
  'Deep Frontier': { minAtrMultiplier: 2.0, maxVolumeMultiplier: 0.6 },
};

type VolatilityRegime = 'low' | 'normal' | 'high';

function getVolatilityRegime(currentAtr: number, historicalAtr: number): VolatilityRegime {
  if (currentAtr < historicalAtr * 0.8) return 'low';
  if (currentAtr > historicalAtr * 1.5) return 'high';
  return 'normal';
}

function applyVolatilityScalar(value: number, regime: VolatilityRegime, scalingEnabled: boolean): number {
  if (!scalingEnabled) return value;
  if (regime === 'low') return value * 0.8;
  if (regime === 'high') return value * 1.2;
  return value;
}

/**
 * Calculate simple rolling ATR
 */
function calculateATR(data: Candle[], startIdx: number, period: number): number {
  let trSum = 0;
  const endIdx = startIdx;
  const actualStart = Math.max(1, startIdx - period);
  for (let j = actualStart; j < endIdx; j++) {
    const h = data[j].high;
    const l = data[j].low;
    const pc = data[j - 1] ? data[j - 1].close : l;
    const tr = Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc));
    trSum += tr;
  }
  const actualPeriod = endIdx - actualStart;
  return actualPeriod > 0 ? trSum / actualPeriod : 0;
}

/**
 * Calculate average volume over a period
 */
function calculateAvgVolume(data: Candle[], startIdx: number, period: number): number {
  let volSum = 0;
  const actualStart = Math.max(0, startIdx - period);
  for (let j = actualStart; j < startIdx; j++) {
    volSum += (data[j].volume || 0);
  }
  const actualPeriod = startIdx - actualStart;
  return actualPeriod > 0 ? volSum / actualPeriod : 0;
}

/**
 * Detect liquidity voids from candle data
 */
export function detectLiquidityVoids(
  candles: Candle[],
  config: Partial<LiquidityVoidsConfig> = {},
  bucket: string = 'Broader Market (N500)'
): LiquidityVoid[] {
  const fullConfig = { ...DEFAULT_CONFIG, ...config };
  const voids: LiquidityVoid[] = [];

  if (candles.length < Math.max(fullConfig.atrPeriod, fullConfig.volPeriod)) return voids;

  const defaults = VOID_THRESHOLDS[bucket] || VOID_THRESHOLDS['Broader Market (N500)'];

  for (let i = Math.max(fullConfig.atrPeriod, fullConfig.volPeriod); i < candles.length; i++) {
    const bar = candles[i];
    const prev = candles[i - 1];

    // Current ATR
    const currentAtr = calculateATR(candles, i, fullConfig.atrPeriod);

    // Historical ATR (3x period lookback)
    const historicalAtr = calculateATR(candles, i - fullConfig.atrPeriod, fullConfig.atrPeriod * 3);

    const regime = getVolatilityRegime(currentAtr, historicalAtr);

    const atrMult = applyVolatilityScalar(
      fullConfig.minAtrMultiplier !== undefined ? fullConfig.minAtrMultiplier : defaults.minAtrMultiplier,
      regime,
      fullConfig.volatilityScaling
    );
    const maxVolMult = applyVolatilityScalar(
      fullConfig.maxVolumeMultiplier !== undefined ? fullConfig.maxVolumeMultiplier : defaults.maxVolumeMultiplier,
      regime,
      fullConfig.volatilityScaling
    );

    // Average volume
    const avgVol = calculateAvgVolume(candles, i, fullConfig.volPeriod);

    // Breakaway gap check
    const isBullGap = bar.low > prev.high;
    const isBearGap = bar.high < prev.low;

    const bodySize = Math.abs(bar.close - bar.open);
    const requiredBody = currentAtr * atrMult;
    const isLowVolume = (bar.volume || 0) < (avgVol * maxVolMult);

    if ((isBullGap || isBearGap) || (bodySize > requiredBody && isLowVolume)) {
      let topPrice = 0;
      let bottomPrice = 0;
      let direction: 'bullish' | 'bearish' = 'bullish';

      if (isBullGap) {
        topPrice = bar.low;
        bottomPrice = prev.high;
        direction = 'bullish';
      } else if (isBearGap) {
        topPrice = prev.low;
        bottomPrice = bar.high;
        direction = 'bearish';
      } else if (bar.close > bar.open) { // solid bullish candle
        topPrice = Math.min(bar.close, bar.high - (bar.high - bar.low) * 0.1);
        bottomPrice = Math.max(bar.open, bar.low + (bar.high - bar.low) * 0.1);
        direction = 'bullish';
      } else {
        topPrice = Math.max(bar.open, bar.low + (bar.high - bar.low) * 0.1);
        bottomPrice = Math.min(bar.close, bar.high - (bar.high - bar.low) * 0.1);
        direction = 'bearish';
        if (topPrice < bottomPrice) {
          const tmp = topPrice;
          topPrice = bottomPrice;
          bottomPrice = tmp;
        }
      }

      if (topPrice > bottomPrice) {
        const bodyContrib = Math.min(100, (bodySize / Math.max(0.001, currentAtr)) * 50);
        const volContrib = Math.min(100, (avgVol / Math.max(1, bar.volume || 1)) * 50);
        const strength = Math.min(100, bodyContrib + volContrib);

        if (strength >= fullConfig.minStrengthScore) {
          voids.push({
            id: direction + '-' + bar.date,
            startDate: bar.date,
            endDate: null,
            direction,
            topPrice,
            bottomPrice,
            status: 'unfilled',
            strength,
          });
        }
      }
    }
  }

  // Fill tracking
  for (let i = 0; i < voids.length; i++) {
    const vd = voids[i];
    const startIdx = candles.findIndex(d => d.date === vd.startDate);
    if (startIdx === -1) continue;

    let currentTop = vd.topPrice;
    let currentBottom = vd.bottomPrice;

    for (let j = startIdx + 1; j < candles.length; j++) {
      const bar = candles[j];
      if (vd.direction === 'bullish') {
        if (bar.low < currentTop) {
          vd.status = 'partial';
          currentTop = bar.low;
        }
        if (currentTop <= currentBottom) {
          vd.status = 'filled';
          vd.endDate = bar.date;
          break;
        }
      } else {
        if (bar.high > currentBottom) {
          vd.status = 'partial';
          currentBottom = bar.high;
        }
        if (currentBottom >= currentTop) {
          vd.status = 'filled';
          vd.endDate = bar.date;
          break;
        }
      }
    }
  }

  if (fullConfig.hideFilledVoids) {
    return voids.filter(v => v.status !== 'filled').sort((a, b) => b.strength - a.strength);
  }

  return voids.sort((a, b) => {
    const s1 = a.status === 'unfilled' ? 2 : a.status === 'partial' ? 1 : 0;
    const s2 = b.status === 'unfilled' ? 2 : b.status === 'partial' ? 1 : 0;
    if (s1 !== s2) return s2 - s1;
    return b.strength - a.strength;
  });
}

/**
 * Build Plotly rectangle shapes for liquidity voids
 */
export function buildLiquidityVoidShapes(
  voids: LiquidityVoid[],
  dates: string[],
  config: Partial<LiquidityVoidsConfig> = {},
  dateToIndex?: Map<string, number>
): any[] {
  const shapes: any[] = [];
  if (dates.length === 0 || voids.length === 0) return shapes;

  const latestIndex = dates.length - 1;
  const d2i = dateToIndex ?? new Map(dates.map((d, i) => [d, i]));

  voids.forEach(vd => {
    const x0i = d2i.get(vd.startDate);
    if (x0i === undefined) return;
    const x1i = vd.endDate ? (d2i.get(vd.endDate) ?? latestIndex) : latestIndex;

    const opacities = {
      'unfilled': 0.8,
      'partial': 0.4,
      'filled': 0.1,
    };
    const opacity = opacities[vd.status];
    const color = vd.direction === 'bullish' ? `rgba(34,197,94,${opacity})` : `rgba(239,68,68,${opacity})`;
    const lineThick = vd.strength > 80 ? 2 : vd.strength > 50 ? 1 : 0;
    const isOpen = vd.endDate === null;

    shapes.push({
      type: 'rect',
      xref: 'x', yref: 'y',
      x0: dates[x0i] ?? '',
      x1: dates[x1i] ?? dates[x0i] ?? '',
      y0: vd.bottomPrice, y1: vd.topPrice,
      fillcolor: color,
      line: {
        width: lineThick,
        color: color,
        dash: isOpen ? 'dash' : 'solid',
      },
      layer: 'below',
    });
  });
  return shapes;
}

/**
 * Build annotations for liquidity voids (strength labels)
 */
export function buildLiquidityVoidAnnotations(
  voids: LiquidityVoid[],
  dates: string[],
  config: Partial<LiquidityVoidsConfig> = {},
  dateToIndex?: Map<string, number>
): any[] {
  const annotations: any[] = [];
  if (dates.length === 0 || voids.length === 0) return annotations;

  const d2i = dateToIndex ?? new Map(dates.map((d, i) => [d, i]));

  voids.forEach(vd => {
    const x0i = d2i.get(vd.startDate);
    if (x0i === undefined) return;

    const isOpen = vd.endDate === null;
    const strengthLabel = `${vd.direction[0].toUpperCase()}${Math.round(vd.strength)}`;
    const label = isOpen ? `${strengthLabel} (open)` : strengthLabel;

    annotations.push({
      x: dates[x0i] ?? '',
      y: vd.topPrice,
      xref: 'x', yref: 'y',
      text: label,
      showarrow: false,
      font: {
        size: 10,
        color: vd.direction === 'bullish' ? '#22c55e' : '#ef4444',
      },
      bgcolor: 'rgba(0,0,0,0.7)',
      borderpad: 2,
      opacity: vd.status === 'filled' ? 0.3 : 1,
    });
  });
  return annotations;
}

export { DEFAULT_CONFIG, type VolatilityRegime };