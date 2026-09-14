import type { Candle } from '../types';
import type { SwingPoint } from './swings';

export interface ChangeOfCharacter {
  id: string;
  type: 'bullish' | 'bearish';
  index: number;
  price: number;
  brokenSwingIndex: number;
  brokenSwingPrice: number;
  trendLength: number; // Number of swings in the prior trend
  confidence: number;
  isEarlySignal: boolean;
}

export interface ChangeOfCharacterResult {
  chochSignals: ChangeOfCharacter[];
  metadata: {
    totalSignals: number;
    bullishCount: number;
    bearishCount: number;
    lastSignalType?: 'bullish' | 'bearish';
  };
}

/**
 * Detects Change of Character (CHoCH) - Early reversal signal
 * CHoCH occurs when price breaks the most recent swing in the opposite direction
 * It's an earlier signal than MSS, indicating potential trend change
 */
export function detectChangeOfCharacter(
  candles: Candle[],
  swings: SwingPoint[]
): ChangeOfCharacterResult {
  if (candles.length < 15 || swings.length < 3) {
    return {
      chochSignals: [],
      metadata: { totalSignals: 0, bullishCount: 0, bearishCount: 0 },
    };
  }

  const chochSignals: ChangeOfCharacter[] = [];
  let signalId = 0;

  // Analyze swing breaks to identify change of character
  for (let i = 2; i < swings.length; i++) {
    const currentSwing = swings[i];
    const prevSwing = swings[i - 1];
    
    // Count trend length (consecutive higher highs/lows or lower highs/lows)
    const trendLength = countTrendLength(swings, i);

    // Detect Bullish CHoCH: Breaking the most recent lower high
    if (currentSwing.type === 'high' && prevSwing.type === 'low') {
      // Check if we broke above the previous lower high
      const brokenHigh = findMostRecentBrokenHigh(swings, i, currentSwing.price);
      
      if (brokenHigh && trendLength >= 2) {
        const confidence = calculateCHoCHConfidence(candles, brokenHigh.index, currentSwing.index, 'bullish', trendLength);
        
        chochSignals.push({
          id: `choch_bullish_${signalId++}`,
          type: 'bullish',
          index: currentSwing.index,
          price: currentSwing.price,
          brokenSwingIndex: brokenHigh.index,
          brokenSwingPrice: brokenHigh.price,
          trendLength,
          confidence,
          isEarlySignal: trendLength === 2, // Earliest possible signal
        });
      }
    }

    // Detect Bearish CHoCH: Breaking the most recent higher low
    if (currentSwing.type === 'low' && prevSwing.type === 'high') {
      // Check if we broke below the previous higher low
      const brokenLow = findMostRecentBrokenLow(swings, i, currentSwing.price);
      
      if (brokenLow && trendLength >= 2) {
        const confidence = calculateCHoCHConfidence(candles, brokenLow.index, currentSwing.index, 'bearish', trendLength);
        
        chochSignals.push({
          id: `choch_bearish_${signalId++}`,
          type: 'bearish',
          index: currentSwing.index,
          price: currentSwing.price,
          brokenSwingIndex: brokenLow.index,
          brokenSwingPrice: brokenLow.price,
          trendLength,
          confidence,
          isEarlySignal: trendLength === 2, // Earliest possible signal
        });
      }
    }
  }

  return {
    chochSignals,
    metadata: {
      totalSignals: chochSignals.length,
      bullishCount: chochSignals.filter(s => s.type === 'bullish').length,
      bearishCount: chochSignals.filter(s => s.type === 'bearish').length,
      lastSignalType: chochSignals.length > 0 ? chochSignals[chochSignals.length - 1].type : undefined,
    },
  };
}

function findMostRecentBrokenHigh(swings: SwingPoint[], currentIndex: number, breakPrice: number): SwingPoint | null {
  // Look back for the most recent high that was broken
  for (let i = currentIndex - 2; i >= 0; i--) {
    if (swings[i].type === 'high' && breakPrice > swings[i].price) {
      return swings[i];
    }
  }
  return null;
}

function findMostRecentBrokenLow(swings: SwingPoint[], currentIndex: number, breakPrice: number): SwingPoint | null {
  // Look back for the most recent low that was broken
  for (let i = currentIndex - 2; i >= 0; i--) {
    if (swings[i].type === 'low' && breakPrice < swings[i].price) {
      return swings[i];
    }
  }
  return null;
}

function countTrendLength(swings: SwingPoint[], currentIndex: number): number {
  if (currentIndex < 2) return 0;
  
  const currentSwing = swings[currentIndex];
  let count = 1;
  
  // Count consecutive swings in the same direction
  for (let i = currentIndex - 2; i >= 0; i -= 2) {
    const prevSwing = swings[i];
    
    if (currentSwing.type === 'high' && prevSwing.type === 'high') {
      if (prevSwing.price < swings[i + 1].price) {
        // Downtrend: lower highs
        count++;
      } else {
        break;
      }
    } else if (currentSwing.type === 'low' && prevSwing.type === 'low') {
      if (prevSwing.price > swings[i + 1].price) {
        // Uptrend: higher lows
        count++;
      } else {
        break;
      }
    } else {
      break;
    }
  }
  
  return count;
}

function calculateCHoCHConfidence(
  candles: Candle[],
  brokenIndex: number,
  currentSwingIndex: number,
  type: 'bullish' | 'bearish',
  trendLength: number
): number {
  let confidence = 45; // Base confidence lower than MSS as it's an early signal
  
  const brokenCandle = candles[brokenIndex];
  const currentCandle = candles[currentSwingIndex];
  
  // Higher confidence with longer prior trend
  if (trendLength >= 3) confidence += 10;
  if (trendLength >= 4) confidence += 15;
  if (trendLength >= 5) confidence += 20;
  
  // Higher confidence if the break is decisive
  const breakRange = currentCandle.h - currentCandle.l;
  const avgRange = getAverageRange(candles, currentSwingIndex, 10);
  
  if (breakRange > avgRange * 1.2) confidence += 10;
  if (breakRange > avgRange * 1.5) confidence += 20;
  
  // Higher confidence if close is near extreme
  if (type === 'bullish') {
    const closePosition = (currentCandle.c - currentCandle.l) / breakRange;
    if (closePosition > 0.6) confidence += 10;
    if (closePosition > 0.8) confidence += 15;
  } else {
    const closePosition = (currentCandle.h - currentCandle.c) / breakRange;
    if (closePosition > 0.6) confidence += 10;
    if (closePosition > 0.8) confidence += 15;
  }
  
  // Bonus for breaking multiple levels
  const percentMove = type === 'bullish'
    ? (currentCandle.h - brokenCandle.l) / brokenCandle.c * 100
    : (brokenCandle.h - currentCandle.l) / brokenCandle.c * 100;
  
  if (percentMove > 3) confidence += 5;
  if (percentMove > 5) confidence += 10;
  
  return Math.min(confidence, 100);
}

function getAverageRange(candles: Candle[], currentIndex: number, lookback: number): number {
  const start = Math.max(0, currentIndex - lookback);
  const slice = candles.slice(start, currentIndex + 1);
  
  const totalRange = slice.reduce((sum, c) => sum + (c.h - c.l), 0);
  return totalRange / slice.length;
}
