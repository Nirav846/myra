/**
 * Order Blocks with Delivery Conviction
 *
 * Identifies Bullish and Bearish Order Blocks and validates them
 * with delivery data to filter out fake/institutional-low-conviction zones.
 */

import type { Candle } from '../../../core/technical-analysis/types';

export interface OrderBlock {
  id: string;
  type: 'bullish' | 'bearish';
  startIdx: number;
  endIdx: number;
  startPrice: number;
  endPrice: number;
  highPrice: number;
  lowPrice: number;
  convictionLevel: 'high' | 'medium' | 'low';
  deliveryMultiplier: number; // How many times above average delivery
}

/**
 * Detect Order Blocks in price data
 * An Order Block is the last candle before a break of structure
 */
function detectOrderBlocks(candles: Candle[], lookback: number = 5): OrderBlock[] {
  const blocks: OrderBlock[] = [];

  for (let i = lookback; i < candles.length - lookback; i++) {
    const currentCandle = candles[i];

    // Check for bullish order block (last down candle before strong up move)
    if (currentCandle.close < currentCandle.open) {
      // Look for break of structure to the upside
      let broken = false;
      let breakoutCandle: Candle | null = null;

      for (let j = i + 1; j <= i + lookback && j < candles.length; j++) {
        const futureCandle = candles[j];
        const prevHigh = Math.max(
          ...candles.slice(i - lookback, i).map(c => c.high)
        );

        if (futureCandle.high > prevHigh && futureCandle.close > futureCandle.open) {
          broken = true;
          breakoutCandle = futureCandle;
          break;
        }
      }

      if (broken && breakoutCandle) {
        // Calculate delivery conviction
        const avgDelivery = calculateAverageDelivery(candles, i, 20);
        const deliveryMultiplier = currentCandle.delivery / avgDelivery;

        let convictionLevel: 'high' | 'medium' | 'low' = 'low';
        if (deliveryMultiplier > 2.0) {
          convictionLevel = 'high';
        } else if (deliveryMultiplier > 1.5) {
          convictionLevel = 'medium';
        }

        blocks.push({
          id: `ob-bull-${i}`,
          type: 'bullish',
          startIdx: i,
          endIdx: i,
          startPrice: currentCandle.open,
          endPrice: currentCandle.close,
          highPrice: currentCandle.high,
          lowPrice: currentCandle.low,
          convictionLevel,
          deliveryMultiplier,
        });
      }
    }

    // Check for bearish order block (last up candle before strong down move)
    if (currentCandle.close > currentCandle.open) {
      // Look for break of structure to the downside
      let broken = false;
      let breakoutCandle: Candle | null = null;

      for (let j = i + 1; j <= i + lookback && j < candles.length; j++) {
        const futureCandle = candles[j];
        const prevLow = Math.min(
          ...candles.slice(i - lookback, i).map(c => c.low)
        );

        if (futureCandle.low < prevLow && futureCandle.close < futureCandle.open) {
          broken = true;
          breakoutCandle = futureCandle;
          break;
        }
      }

      if (broken && breakoutCandle) {
        // Calculate delivery conviction
        const avgDelivery = calculateAverageDelivery(candles, i, 20);
        const deliveryMultiplier = currentCandle.delivery / avgDelivery;

        let convictionLevel: 'high' | 'medium' | 'low' = 'low';
        if (deliveryMultiplier > 2.0) {
          convictionLevel = 'high';
        } else if (deliveryMultiplier > 1.5) {
          convictionLevel = 'medium';
        }

        blocks.push({
          id: `ob-bear-${i}`,
          type: 'bearish',
          startIdx: i,
          endIdx: i,
          startPrice: currentCandle.open,
          endPrice: currentCandle.close,
          highPrice: currentCandle.high,
          lowPrice: currentCandle.low,
          convictionLevel,
          deliveryMultiplier,
        });
      }
    }
  }

  return blocks;
}

/**
 * Calculate average delivery over a period
 */
function calculateAverageDelivery(candles: Candle[], currentIndex: number, period: number): number {
  const start = Math.max(0, currentIndex - period);
  const slice = candles.slice(start, currentIndex);

  if (slice.length === 0) return 1;

  const sum = slice.reduce((acc, c) => acc + (c.delivery || 0), 0);
  return sum / slice.length;
}

/**
 * Build Order Block rectangle shapes for Plotly
 */
export function buildOrderBlockShapes(blocks: OrderBlock[]) {
  const shapes: any[] = [];

  blocks.forEach(block => {
    const color = block.type === 'bullish'
      ? getConvictionColor('bullish', block.convictionLevel)
      : getConvictionColor('bearish', block.convictionLevel);

    shapes.push({
      type: 'rect',
      xref: 'paper',
      yref: 'y',
      x0: block.startIdx - 0.5,
      x1: block.endIdx + 0.5,
      y0: block.lowPrice,
      y1: block.highPrice,
      fillcolor: color.fill,
      line: {
        color: color.stroke,
        width: block.convictionLevel === 'high' ? 2 : 1,
        dash: block.convictionLevel === 'high' ? 'solid' : 'dot',
      },
      layer: 'below',
    });
  });

  return shapes;
}

/**
 * Build Order Block annotations
 */
export function buildOrderBlockAnnotations(blocks: OrderBlock[]) {
  const annotations: any[] = [];

  blocks.forEach(block => {
    const midPrice = (block.highPrice + block.lowPrice) / 2;
    const label = block.convictionLevel === 'high'
      ? `OB ${block.type === 'bullish' ? 'B' : 'S'} ★`
      : `OB ${block.type === 'bullish' ? 'B' : 'S'}`;

    annotations.push({
      x: block.startIdx,
      y: midPrice,
      xref: 'x',
      yref: 'y',
      text: label,
      showarrow: false,
      font: {
        size: 9,
        color: block.convictionLevel === 'high' ? '#fbbf24' : '#888899',
        weight: block.convictionLevel === 'high' ? 'bold' : 'normal',
      },
      bgcolor: 'rgba(0,0,0,0.7)',
      borderpad: 2,
    });
  });

  return annotations;
}

/**
 * Get color based on type and conviction level
 */
function getConvictionColor(type: 'bullish' | 'bearish', conviction: 'high' | 'medium' | 'low') {
  if (type === 'bullish') {
    switch (conviction) {
      case 'high':
        return { fill: 'rgba(34, 197, 94, 0.4)', stroke: '#22c55e' };
      case 'medium':
        return { fill: 'rgba(34, 197, 94, 0.25)', stroke: '#4ade80' };
      case 'low':
        return { fill: 'rgba(34, 197, 94, 0.15)', stroke: '#86efac' };
    }
  } else {
    switch (conviction) {
      case 'high':
        return { fill: 'rgba(239, 68, 68, 0.4)', stroke: '#ef4444' };
      case 'medium':
        return { fill: 'rgba(239, 68, 68, 0.25)', stroke: '#f87171' };
      case 'low':
        return { fill: 'rgba(239, 68, 68, 0.15)', stroke: '#fca5a5' };
    }
  }
}

/**
 * Detect Order Blocks from candle data
 */
export function detectOrderBlocksFromData(candles: Candle[], lookback: number = 5): OrderBlock[] {
  return detectOrderBlocks(candles, lookback);
}

export default detectOrderBlocksFromData;
