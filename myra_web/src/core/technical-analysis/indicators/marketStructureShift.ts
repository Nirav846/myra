import type { Candle } from '../types';
import type { SwingPoint } from './swings';

export interface MarketStructureShift {
  id: string;
  type: 'bullish' | 'bearish';
  index: number;
  price: number;
  brokenSwingIndex: number;
  brokenSwingPrice: number;
  brokenSwingType: 'high' | 'low';
  confidence: number;
  momentum: number;
}

export interface MarketStructureShiftResult {
  shifts: MarketStructureShift[];
  metadata: {
    totalShifts: number;
    bullishCount: number;
    bearishCount: number;
    lastShiftType?: 'bullish' | 'bearish';
  };
}

/**
 * Detects Market Structure Shifts (MSS) - Key reversal points where trend changes
 * Bullish MSS: Price breaks above a significant swing high after downtrend
 * Bearish MSS: Price breaks below a significant swing low after uptrend
 */
export function detectMarketStructureShifts(
  candles: Candle[],
  swings: SwingPoint[]
): MarketStructureShiftResult {
  if (candles.length < 20 || swings.length < 4) {
    return {
      shifts: [],
      metadata: { totalShifts: 0, bullishCount: 0, bearishCount: 0 },
    };
  }

  const shifts: MarketStructureShift[] = [];
  let shiftId = 0;

  // Analyze swing breaks to identify market structure shifts
  for (let i = 2; i < swings.length; i++) {
    const currentSwing = swings[i];
    const prevSwing = swings[i - 1];
    const prevPrevSwing = swings[i - 2];

    // Detect Bullish MSS: Breaking a swing high after making higher lows
    if (currentSwing.type === 'high' && prevSwing.type === 'low') {
      // Check if we broke above a previous significant high
      const brokenHigh = findBrokenHigh(swings, i, currentSwing.price);
      
      if (brokenHigh && prevSwing.price > prevPrevSwing?.price) {
        const confidence = calculateMSSConfidence(candles, brokenHigh.index, currentSwing.index, 'bullish');
        const momentum = calculateMomentum(candles, brokenHigh.index, currentSwing.index);
        
        if (confidence >= 60) { // Only significant shifts
          shifts.push({
            id: `mss_bullish_${shiftId++}`,
            type: 'bullish',
            index: currentSwing.index,
            price: currentSwing.price,
            brokenSwingIndex: brokenHigh.index,
            brokenSwingPrice: brokenHigh.price,
            brokenSwingType: 'high',
            confidence,
            momentum,
          });
        }
      }
    }

    // Detect Bearish MSS: Breaking a swing low after making lower highs
    if (currentSwing.type === 'low' && prevSwing.type === 'high') {
      // Check if we broke below a previous significant low
      const brokenLow = findBrokenLow(swings, i, currentSwing.price);
      
      if (brokenLow && prevSwing.price < prevPrevSwing?.price) {
        const confidence = calculateMSSConfidence(candles, brokenLow.index, currentSwing.index, 'bearish');
        const momentum = calculateMomentum(candles, brokenLow.index, currentSwing.index);
        
        if (confidence >= 60) { // Only significant shifts
          shifts.push({
            id: `mss_bearish_${shiftId++}`,
            type: 'bearish',
            index: currentSwing.index,
            price: currentSwing.price,
            brokenSwingIndex: brokenLow.index,
            brokenSwingPrice: brokenLow.price,
            brokenSwingType: 'low',
            confidence,
            momentum,
          });
        }
      }
    }
  }

  return {
    shifts,
    metadata: {
      totalShifts: shifts.length,
      bullishCount: shifts.filter(s => s.type === 'bullish').length,
      bearishCount: shifts.filter(s => s.type === 'bearish').length,
      lastShiftType: shifts.length > 0 ? shifts[shifts.length - 1].type : undefined,
    },
  };
}

function findBrokenHigh(swings: SwingPoint[], currentIndex: number, breakPrice: number): SwingPoint | null {
  // Look back for the most recent significant high that was broken
  for (let i = currentIndex - 2; i >= 0; i--) {
    if (swings[i].type === 'high' && breakPrice > swings[i].price) {
      // Ensure it's a significant high (not just a minor swing)
      if (isSignificantSwing(swings, i)) {
        return swings[i];
      }
    }
  }
  return null;
}

function findBrokenLow(swings: SwingPoint[], currentIndex: number, breakPrice: number): SwingPoint | null {
  // Look back for the most recent significant low that was broken
  for (let i = currentIndex - 2; i >= 0; i--) {
    if (swings[i].type === 'low' && breakPrice < swings[i].price) {
      // Ensure it's a significant low (not just a minor swing)
      if (isSignificantSwing(swings, i)) {
        return swings[i];
      }
    }
  }
  return null;
}

function isSignificantSwing(swings: SwingPoint[], index: number): boolean {
  if (index < 1 || index >= swings.length - 1) return false;
  
  const swing = swings[index];
  const prevSwing = swings[index - 1];
  
  // Check if the swing represents a meaningful move (>2% from previous swing)
  const percentMove = Math.abs(swing.price - prevSwing.price) / prevSwing.price * 100;
  return percentMove > 2;
}

function calculateMSSConfidence(
  candles: Candle[],
  brokenIndex: number,
  currentSwingIndex: number,
  type: 'bullish' | 'bearish'
): number {
  let confidence = 50;
  
  const brokenCandle = candles[brokenIndex];
  const currentCandle = candles[currentSwingIndex];
  
  // Higher confidence if the break is decisive (strong candle)
  const breakRange = currentCandle.h - currentCandle.l;
  const avgRange = getAverageRange(candles, currentSwingIndex, 10);
  
  if (breakRange > avgRange * 1.3) confidence += 15;
  if (breakRange > avgRange * 1.5) confidence += 25;
  
  // Higher confidence if close is near extreme
  if (type === 'bullish') {
    const closePosition = (currentCandle.c - currentCandle.l) / breakRange;
    if (closePosition > 0.7) confidence += 10;
  } else {
    const closePosition = (currentCandle.h - currentCandle.c) / breakRange;
    if (closePosition > 0.7) confidence += 10;
  }
  
  // Higher confidence if multiple swings were broken
  const candlesBetween = candles.slice(brokenIndex, currentSwingIndex + 1);
  const priceMove = type === 'bullish' 
    ? currentCandle.h - brokenCandle.l 
    : brokenCandle.h - currentCandle.l;
  
  const percentMove = priceMove / brokenCandle.c * 100;
  if (percentMove > 5) confidence += 10;
  if (percentMove > 8) confidence += 15;
  
  return Math.min(confidence, 100);
}

function calculateMomentum(candles: Candle[], startIndex: number, endIndex: number): number {
  const startCandle = candles[startIndex];
  const endCandle = candles[endIndex];
  
  const priceChange = endCandle.c - startCandle.c;
  const percentChange = (priceChange / startCandle.c) * 100;
  const numCandles = endIndex - startIndex + 1;
  
  // Momentum as percentage change per candle
  const momentum = Math.abs(percentChange) / numCandles;
  
  return Math.min(momentum * 10, 100); // Normalize to 0-100
}

function getAverageRange(candles: Candle[], currentIndex: number, lookback: number): number {
  const start = Math.max(0, currentIndex - lookback);
  const slice = candles.slice(start, currentIndex + 1);
  
  const totalRange = slice.reduce((sum, c) => sum + (c.h - c.l), 0);
  return totalRange / slice.length;
}
