import type { Candle } from '../types';

export interface DeliveryTrendResult {
  deliveryPercentages: number[];
  ema20: (number | null)[];
  signalLine: (number | null)[]; // EMA of EMA20
  currentDeliveryPercent: number;
  currentEma20: number | null;
  currentSignal: number | null;
  trend: 'bullish' | 'bearish' | 'neutral';
  crossover?: 'bullish' | 'bearish' | null;
}

/**
 * Calculates Delivery Percentage Trend
 * Shows institutional accumulation/distribution over time
 * Uses 20-day EMA of delivery percentage with signal line
 */
export function calculateDeliveryTrend(data: Candle[]): DeliveryTrendResult {
  const length = data.length;
  const deliveryPercentages: number[] = [];
  const ema20: (number | null)[] = [];
  const signalLine: (number | null)[] = [];

  // Calculate delivery percentage for each candle
  for (let i = 0; i < length; i++) {
    const candle = data[i];
    
    // Delivery percentage = (delivery quantity / total quantity) * 100
    // Assuming deliveryQty and totalQty are available in CandleData
    // If not, we'll use a proxy based on price action
    let deliveryPercent = 0;
    
    if ('deliveryQty' in candle && 'totalQty' in candle && candle.totalQty > 0) {
      deliveryPercent = (candle.deliveryQty / candle.totalQty) * 100;
    } else {
      // Proxy: Use volume-weighted price position
      const range = candle.high - candle.low;
      if (range > 0) {
        const position = (candle.close - candle.low) / range;
        // Higher delivery on strong closes with high volume
        deliveryPercent = 50 + (position - 0.5) * 40; // Range: 30-70%
        
        // Adjust for volume spike (proxy for institutional activity)
        if (i > 0) {
          const avgVolume = data.slice(Math.max(0, i - 20), i).reduce((sum, c) => sum + (c.volume ?? 0), 0) / Math.min(i, 20);
          if ((candle.volume ?? 0) > avgVolume * 1.5) {
            deliveryPercent += position > 0.5 ? 10 : -10;
          }
        }
      }
      
      // Clamp to realistic range
      deliveryPercent = Math.max(30, Math.min(70, deliveryPercent));
    }
    
    deliveryPercentages.push(deliveryPercent);
  }

  // Calculate 20-day EMA of delivery percentage
  const emaPeriod = 20;
  const multiplier = 2 / (emaPeriod + 1);

  for (let i = 0; i < length; i++) {
    if (i < emaPeriod - 1) {
      ema20.push(null);
      continue;
    }

    if (i === emaPeriod - 1) {
      // First EMA is SMA of first 'period' values
      const sum = deliveryPercentages.slice(0, emaPeriod).reduce((a, b) => a + b, 0);
      ema20.push(sum / emaPeriod);
    } else {
      // EMA = (Close - Previous EMA) * Multiplier + Previous EMA
      const prevEma = ema20[i - 1]!;
      const currentEma = (deliveryPercentages[i] - prevEma) * multiplier + prevEma;
      ema20.push(currentEma);
    }
  }

  // Calculate Signal Line (9-day EMA of EMA20)
  const signalPeriod = 9;
  const signalMultiplier = 2 / (signalPeriod + 1);

  for (let i = 0; i < length; i++) {
    if (i < emaPeriod + signalPeriod - 2 || ema20[i] === null) {
      signalLine.push(null);
      continue;
    }

    // Find the index in ema20 array (accounting for initial nulls)
    const validEmaIndex = i - (emaPeriod - 1);
    
    if (validEmaIndex === signalPeriod - 1) {
      // First signal is SMA of first 'period' EMA values
      const emaSlices = ema20.slice(emaPeriod - 1, i + 1).filter((v): v is number => v !== null);
      if (emaSlices.length === signalPeriod) {
        const sum = emaSlices.reduce((a, b) => a + b, 0);
        signalLine.push(sum / signalPeriod);
      } else {
        signalLine.push(null);
      }
    } else {
      const prevSignal = signalLine[i - 1];
      if (prevSignal !== null && prevSignal !== undefined) {
        const currentSignal = (ema20[i]! - prevSignal) * signalMultiplier + prevSignal;
        signalLine.push(currentSignal);
      } else {
        signalLine.push(null);
      }
    }
  }

  // Determine current trend and crossovers
  const currentDeliveryPercent = deliveryPercentages[length - 1] || 0;
  const currentEma20Value = ema20[length - 1];
  const currentSignalValue = signalLine[length - 1];

  let trend: 'bullish' | 'bearish' | 'neutral' = 'neutral';
  let crossover: 'bullish' | 'bearish' | null = null;

  if (currentEma20Value !== null && currentSignalValue !== null) {
    if (currentEma20Value > currentSignalValue) {
      trend = 'bullish';
    } else if (currentEma20Value < currentSignalValue) {
      trend = 'bearish';
    }

    // Check for crossover
    const prevEma = ema20[length - 2];
    const prevSignal = signalLine[length - 2];

    if (prevEma !== null && prevSignal !== null) {
      if (prevEma <= prevSignal && currentEma20Value > currentSignalValue) {
        crossover = 'bullish'; // Golden cross
      } else if (prevEma >= prevSignal && currentEma20Value < currentSignalValue) {
        crossover = 'bearish'; // Death cross
      }
    }
  }

  return {
    deliveryPercentages,
    ema20,
    signalLine,
    currentDeliveryPercent,
    currentEma20: currentEma20Value,
    currentSignal: currentSignalValue,
    trend,
    crossover,
  };
}
