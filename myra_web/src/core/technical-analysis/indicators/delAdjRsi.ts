import type { Candle } from '../types';

/**
 * Calculates Delivery-Adjusted RSI.
 *
 * Enhances standard RSI by weighting price changes with delivery percentage,
 * giving more weight to moves backed by institutional delivery flow.
 *
 * @param data - Array of candle data
 * @param config - Optional configuration with period and deliveryWeight
 * @returns Array of delivery-adjusted RSI values (0–100)
 */
export function calculateDeliveryAdjustedRSI(
  data: Candle[],
  config?: { period?: number; deliveryWeight?: number }
): number[] {
  const period = config?.period ?? 14;
  const deliveryWeight = config?.deliveryWeight ?? 0.5;

  if (!data || data.length < period + 1) {
    return [];
  }

  const rsiValues: number[] = [];

  for (let i = period; i < data.length; i++) {
    const slice = data.slice(i - period, i + 1);

    let delivGains = 0;
    let delivLosses = 0;

    for (let j = 1; j < slice.length; j++) {
      const change = slice[j].close - slice[j - 1].close;

      const delivPct =
        typeof slice[j].delivery_pct === 'number'
          ? slice[j].delivery_pct
          : typeof slice[j].deliveryPercentage === 'number'
            ? slice[j].deliveryPercentage
            : 50;

      const weight = 0.5 + (delivPct / 100) * deliveryWeight;

      if (change > 0) {
        delivGains += change * weight;
      } else {
        delivLosses += Math.abs(change) * weight;
      }
    }

    const avgGain = delivGains / period;
    const avgLoss = delivLosses / period;
    const rs = avgGain / (avgLoss || 1);
    const rsi = 100 - 100 / (1 + rs);

    rsiValues.push(rsi);
  }

  return rsiValues;
}
