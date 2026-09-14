import { CandleData } from '../../types';

export interface PremiumDiscountZone {
  premiumStart: number;
  premiumEnd: number;
  discountStart: number;
  discountEnd: number;
  fairValue: number;
  equilibrium: number; // 50% level
  currentPrice: number;
  inPremium: boolean;
  inDiscount: boolean;
}

/**
 * Calculates Premium/Discount Zones based on Fibonacci levels
 * Uses the highest high and lowest low over a lookback period
 * 
 * Zones:
 * - Discount (0%-50%): Buy zone, price below fair value
 * - Fair Value (50%): Equilibrium
 * - Premium (50%-100%): Sell zone, price above fair value
 */
export function calculatePremiumDiscount(
  data: CandleData[],
  lookback: number = 50
): PremiumDiscountZone | null {
  if (data.length < lookback) {
    return null;
  }

  // Get the most recent 'lookback' candles
  const slice = data.slice(-lookback);
  
  // Find highest high and lowest low in the lookback period
  let highestHigh = -Infinity;
  let lowestLow = Infinity;

  for (const candle of slice) {
    if (candle.high > highestHigh) highestHigh = candle.high;
    if (candle.low < lowestLow) lowestLow = candle.low;
  }

  const range = highestHigh - lowestLow;
  const currentPrice = data[data.length - 1].close;

  // Calculate Fibonacci levels
  const fairValue = lowestLow; // 0% level
  const equilibrium = lowestLow + (range * 0.5); // 50% level
  const premiumStart = equilibrium;
  const premiumEnd = highestHigh; // 100% level
  const discountStart = lowestLow;
  const discountEnd = equilibrium;

  return {
    premiumStart,
    premiumEnd,
    discountStart,
    discountEnd,
    fairValue,
    equilibrium,
    currentPrice,
    inPremium: currentPrice > equilibrium,
    inDiscount: currentPrice < equilibrium,
  };
}

/**
 * Gets the current zone label for display
 */
export function getZoneLabel(zone: PremiumDiscountZone): string {
  if (zone.inPremium) {
    const premiumPercent = ((zone.currentPrice - zone.equilibrium) / (zone.premiumEnd - zone.equilibrium)) * 100;
    if (premiumPercent > 75) return 'Extreme Premium';
    if (premiumPercent > 50) return 'Strong Premium';
    return 'Premium';
  } else if (zone.inDiscount) {
    const discountPercent = ((zone.equilibrium - zone.currentPrice) / (zone.equilibrium - zone.discountStart)) * 100;
    if (discountPercent > 75) return 'Extreme Discount';
    if (discountPercent > 50) return 'Strong Discount';
    return 'Discount';
  }
  return 'Equilibrium';
}
