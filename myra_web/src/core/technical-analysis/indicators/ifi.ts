import type { Candle } from '../types';

/**
 * Calculates the Institutional Flow Index (IFI).
 *
 * Combines delivery percentage, relative volume, and price momentum into a
 * single score ranging from −100 to +100.
 *   > 50  → strong accumulation
 *   < −50 → strong distribution
 *
 * @param data - Array of candle data
 * @param config - Optional configuration with period
 * @returns Array of IFI values (−100 to +100)
 */
export function calculateIFI(
  data: Candle[],
  config?: { period?: number }
): number[] {
  const period = config?.period ?? 20;

  if (!data || data.length < period) {
    return [];
  }

  const ifiValues: number[] = [];

  for (let i = period - 1; i < data.length; i++) {
    const slice = data.slice(i - period + 1, i + 1);

    // Delivery Component (−100 to +100)
    const avgDeliveryPct =
      slice.reduce((sum, c) => {
        const dp =
          typeof c.delivery_pct === 'number'
            ? c.delivery_pct
            : typeof c.deliveryPercentage === 'number'
              ? c.deliveryPercentage
              : 50;
        return sum + dp;
      }, 0) / period;
    const deliveryScore = (avgDeliveryPct - 50) * 2;

    // Volume Component (−100 to +100)
    const avgVolume = slice.reduce((sum, c) => sum + (c.volume ?? 0), 0) / period;
    const currentVolume = data[i].volume ?? 0;
    const volumeRatio = avgVolume > 0 ? currentVolume / avgVolume : 1;
    const volumeScore = Math.min(Math.max((volumeRatio - 1) * 100, -100), 100);

    // Momentum Component (−100 to +100)
    const startClose = data[i - period + 1].close;
    const endClose = data[i].close;
    const priceChange = startClose > 0 ? ((endClose - startClose) / startClose) * 100 : 0;
    const momentumScore = Math.min(Math.max(priceChange * 10, -100), 100);

    // Combined IFI (weighted average)
    const ifiValue = deliveryScore * 0.4 + volumeScore * 0.3 + momentumScore * 0.3;
    ifiValues.push(Math.min(Math.max(ifiValue, -100), 100));
  }

  return ifiValues;
}
