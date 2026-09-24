import type { Candle } from '../types';
import type { SwingPoint } from './swings';

export interface BreakerBlock {
  id: string;
  type: 'bullish' | 'bearish';
  startIndex: number;
  endIndex: number;
  high: number;
  low: number;
  open: number;
  close: number;
  mitigationPrice?: number;
  isMitigated: boolean;
  sweptLiquidity: boolean;
  confidence: number;
}

export interface BreakerBlockResult {
  breakerBlocks: BreakerBlock[];
  metadata: {
    totalBlocks: number;
    bullishCount: number;
    bearishCount: number;
    mitigatedCount: number;
  };
}

/**
 * Detects Breaker Blocks - Failed order blocks that reverse after liquidity sweeps
 * A breaker block forms when price sweeps a swing point then reverses through the originating OB
 */
export function detectBreakerBlocks(
  candles: Candle[],
  swings: SwingPoint[]
): BreakerBlockResult {
  if (candles.length < 10 || swings.length < 3) {
    return {
      breakerBlocks: [],
      metadata: { totalBlocks: 0, bullishCount: 0, bearishCount: 0, mitigatedCount: 0 },
    };
  }

  const breakerBlocks: BreakerBlock[] = [];
  let blockId = 0;

  // Find potential breaker blocks by analyzing swing failures
  for (let i = 1; i < swings.length - 1; i++) {
    const prevSwing = swings[i - 1];
    const currentSwing = swings[i];
    const nextSwing = swings[i + 1];

    // Detect failed swing highs (potential bearish breaker)
    if (currentSwing.type === 'high') {
      // Check if price swept above previous high
      const sweptPreviousHigh = currentSwing.price > prevSwing.price && prevSwing.type === 'high';
      
      // Check if price reversed and broke below the low that formed the current high
      const reversalLow = findReversalLow(candles, currentSwing.index, nextSwing.index);
      
      if (sweptPreviousHigh && reversalLow) {
        // Find the candle that formed the low before the sweep
        const obCandle = findOriginatingOB(candles, currentSwing.index, 'low');
        
        if (obCandle) {
          const isMitigated = checkMitigation(candles, nextSwing.index, obCandle.high, 'up');
          
          breakerBlocks.push({
            id: `breaker_bearish_${blockId++}`,
            type: 'bearish',
            startIndex: obCandle.index,
            endIndex: currentSwing.index,
            high: Math.max(candles[obCandle.index].high, candles[currentSwing.index].high),
            low: obCandle.low,
            open: candles[obCandle.index].open,
            close: candles[currentSwing.index].close,
            mitigationPrice: isMitigated.price,
            isMitigated: isMitigated.status,
            sweptLiquidity: true,
            confidence: calculateBreakerConfidence(candles, obCandle.index, currentSwing.index),
          });
        }
      }
    }

    // Detect failed swing lows (potential bullish breaker)
    if (currentSwing.type === 'low') {
      // Check if price swept below previous low
      const sweptPreviousLow = currentSwing.price < prevSwing.price && prevSwing.type === 'low';
      
      // Check if price reversed and broke above the high that formed the current low
      const reversalHigh = findReversalHigh(candles, currentSwing.index, nextSwing.index);
      
      if (sweptPreviousLow && reversalHigh) {
        // Find the candle that formed the high before the sweep
        const obCandle = findOriginatingOB(candles, currentSwing.index, 'high');
        
        if (obCandle) {
          const isMitigated = checkMitigation(candles, nextSwing.index, obCandle.low, 'down');
          
          breakerBlocks.push({
            id: `breaker_bullish_${blockId++}`,
            type: 'bullish',
            startIndex: obCandle.index,
            endIndex: currentSwing.index,
            high: obCandle.high,
            low: Math.min(candles[obCandle.index].low, candles[currentSwing.index].low),
            open: candles[obCandle.index].open,
            close: candles[currentSwing.index].close,
            mitigationPrice: isMitigated.price,
            isMitigated: isMitigated.status,
            sweptLiquidity: true,
            confidence: calculateBreakerConfidence(candles, obCandle.index, currentSwing.index),
          });
        }
      }
    }
  }

  return {
    breakerBlocks,
    metadata: {
      totalBlocks: breakerBlocks.length,
      bullishCount: breakerBlocks.filter(b => b.type === 'bullish').length,
      bearishCount: breakerBlocks.filter(b => b.type === 'bearish').length,
      mitigatedCount: breakerBlocks.filter(b => b.isMitigated).length,
    },
  };
}

function findReversalLow(candles: Candle[], startIndex: number, endIndex: number): number | null {
  let lowest = Infinity;
  let lowestIndex = -1;
  
  for (let i = startIndex; i <= Math.min(endIndex, candles.length - 1); i++) {
    if (candles[i].low < lowest) {
      lowest = candles[i].low;
      lowestIndex = i;
    }
  }
  
  return lowestIndex !== -1 ? lowest : null;
}

function findReversalHigh(candles: Candle[], startIndex: number, endIndex: number): number | null {
  let highest = -Infinity;
  let highestIndex = -1;
  
  for (let i = startIndex; i <= Math.min(endIndex, candles.length - 1); i++) {
    if (candles[i].high > highest) {
      highest = candles[i].high;
      highestIndex = i;
    }
  }
  
  return highestIndex !== -1 ? highest : null;
}

function findOriginatingOB(candles: Candle[], swingIndex: number, type: 'high' | 'low'): Candle | null {
  // Look back 3-10 candles to find the originating candle
  const lookback = Math.min(10, swingIndex);
  const start = Math.max(0, swingIndex - 10);
  
  if (type === 'high') {
    // For bearish breaker, find the high candle before the sweep
    let highest = -Infinity;
    let highestCandle: Candle | null = null;
    
    for (let i = start; i < swingIndex; i++) {
      if (candles[i].high > highest) {
        highest = candles[i].high;
        highestCandle = { ...candles[i], index: i };
      }
    }
    
    return highestCandle;
  } else {
    // For bullish breaker, find the low candle before the sweep
    let lowest = Infinity;
    let lowestCandle: Candle | null = null;
    
    for (let i = start; i < swingIndex; i++) {
      if (candles[i].low < lowest) {
        lowest = candles[i].low;
        lowestCandle = { ...candles[i], index: i };
      }
    }
    
    return lowestCandle;
  }
}

function checkMitigation(
  candles: Candle[],
  startIndex: number,
  level: number,
  direction: 'up' | 'down'
): { status: boolean; price?: number } {
  for (let i = startIndex; i < candles.length; i++) {
    if (direction === 'up' && candles[i].high >= level) {
      return { status: true, price: candles[i].high };
    }
    if (direction === 'down' && candles[i].low <= level) {
      return { status: true, price: candles[i].low };
    }
  }
  return { status: false };
}

function calculateBreakerConfidence(candles: Candle[], obIndex: number, sweepIndex: number): number {
  const obCandle = candles[obIndex];
  const sweepCandle = candles[sweepIndex];
  
  let confidence = 50;
  
  // Higher confidence if sweep candle has large range
  const sweepRange = sweepCandle.high - sweepCandle.low;
  const avgRange = (candles.slice(Math.max(0, sweepIndex - 10), sweepIndex + 1)
    .reduce((sum, c) => sum + (c.high - c.low), 0) / 10);
  
  if (sweepRange > avgRange * 1.5) confidence += 15;
  if (sweepRange > avgRange * 2) confidence += 25;
  
  // Higher confidence if reversal is strong
  const reversalStrength = Math.abs(sweepCandle.close - sweepCandle.open);
  if (reversalStrength > sweepRange * 0.6) confidence += 10;
  
  // Higher confidence if OB candle is significant
  const obRange = obCandle.high - obCandle.low;
  if (obRange > avgRange * 1.3) confidence += 10;
  
  return Math.min(confidence, 100);
}
