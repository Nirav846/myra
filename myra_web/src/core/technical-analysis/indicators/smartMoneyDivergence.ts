import type { Candle } from '../types';

export interface SmartMoneyDivergencePoint {
  index: number;
  date: string;
  type: 'bullish' | 'bearish';
  priceValue: number;
  indicatorValue: number;
  confidence: number;
}

/**
 * Detects Smart Money Divergences between price action and delivery flow.
 *
 * Bearish divergence: price makes a higher high while delivery % makes a lower high,
 * suggesting institutions are distributing into strength.
 *
 * Bullish divergence: price makes a lower low while delivery % makes a higher low,
 * suggesting institutions are accumulating into weakness.
 *
 * @param data - Array of candle data with deliveryPercentage
 * @param config - Optional configuration with lookback period
 * @returns Array of divergence points
 */
export function detectSmartMoneyDivergence(
  data: Candle[],
  config?: { lookback?: number }
): SmartMoneyDivergencePoint[] {
  const lookback = config?.lookback ?? 10;

  if (!data || data.length < lookback * 2) {
    return [];
  }

  const divergences: SmartMoneyDivergencePoint[] = [];

  for (let i = lookback; i < data.length - lookback; i++) {
    const leftSlice = data.slice(i - lookback, i);
    const rightSlice = data.slice(i, i + lookback);

    const leftPriceLow = Math.min(...leftSlice.map(c => c.low));
    const leftPriceHigh = Math.max(...leftSlice.map(c => c.high));
    const rightPriceLow = Math.min(...rightSlice.map(c => c.low));
    const rightPriceHigh = Math.max(...rightSlice.map(c => c.high));

    const getDeliveryPct = (c: Candle): number => {
      if (typeof c.delivery_pct === 'number' && !isNaN(c.delivery_pct)) return c.delivery_pct;
      if (typeof c.deliveryPercentage === 'number' && !isNaN(c.deliveryPercentage)) return c.deliveryPercentage;
      const vol = Number(c.volume_final ?? c.volume ?? 0);
      const del = Number(c.delivery_final ?? (c as any).delivery ?? 0);
      return vol > 0 ? (del / vol) * 100 : 50;
    };

    const leftDelivAvg = leftSlice.reduce((sum, c) => sum + getDeliveryPct(c), 0) / lookback;
    const rightDelivAvg = rightSlice.reduce((sum, c) => sum + getDeliveryPct(c), 0) / lookback;

    const isBullish = rightPriceLow < leftPriceLow && rightDelivAvg > leftDelivAvg;
    const isBearish = rightPriceHigh > leftPriceHigh && rightDelivAvg < leftDelivAvg;

    if (isBullish || isBearish) {
      const confidence = Math.min(Math.abs(rightDelivAvg - leftDelivAvg) / 50, 1);

      divergences.push({
        index: i,
        date: data[i].date,
        type: isBullish ? 'bullish' : 'bearish',
        priceValue: isBullish ? rightPriceLow : rightPriceHigh,
        indicatorValue: isBullish ? rightDelivAvg : rightDelivAvg,
        confidence,
      });
    }
  }

  return divergences;
}
