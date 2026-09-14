import type { Candle } from '../types';

export interface DeliveryCluster {
  startDate: string;
  endDate: string;
  startPrice: number;
  endPrice: number;
  avgDeliveryPct: number;
  clusterStrength: 'weak' | 'moderate' | 'strong';
  type: 'support' | 'resistance';
}

/**
 * Detects delivery clusters — consecutive days of high delivery activity
 * that may act as institutional support or resistance zones.
 *
 * @param data - Array of candle data
 * @param config - Optional configuration with minConsecutiveDays and threshold
 * @returns Array of detected delivery clusters
 */
export function detectDeliveryClusters(
  data: Candle[],
  config?: { minConsecutiveDays?: number; threshold?: number }
): DeliveryCluster[] {
  const minConsecutiveDays = config?.minConsecutiveDays ?? 3;
  const threshold = config?.threshold ?? 60;

  if (!data || data.length < minConsecutiveDays) {
    return [];
  }

  const getDeliveryPct = (c: Candle): number => {
    if (typeof c.delivery_pct === 'number' && !isNaN(c.delivery_pct)) return c.delivery_pct;
    if (typeof c.deliveryPercentage === 'number' && !isNaN(c.deliveryPercentage)) return c.deliveryPercentage;
    const vol = Number(c.volume_final ?? c.volume ?? 0);
    const del = Number(c.delivery_final ?? (c as any).delivery ?? 0);
    return vol > 0 ? (del / vol) * 100 : 0;
  };

  const clusters: DeliveryCluster[] = [];
  let currentCluster: Partial<DeliveryCluster> & { count: number; prices: number[] } = {
    count: 0,
    prices: [],
    avgDeliveryPct: 0,
  };

  for (let i = 0; i < data.length; i++) {
    const delivPct = getDeliveryPct(data[i]);

    if (delivPct >= threshold) {
      if (currentCluster.count === 0) {
        currentCluster = {
          startDate: data[i].date,
          startPrice: data[i].close,
          count: 1,
          prices: [data[i].close],
          avgDeliveryPct: delivPct,
        };
      } else {
        currentCluster.count++;
        currentCluster.prices.push(data[i].close);
        currentCluster.avgDeliveryPct =
          (currentCluster.avgDeliveryPct! * (currentCluster.count - 1) + delivPct) / currentCluster.count;
      }
    } else {
      if (currentCluster.count >= minConsecutiveDays) {
        const endPrice = currentCluster.prices![currentCluster.prices!.length - 1];
        const priceChange = endPrice - (currentCluster.startPrice || 0);

        clusters.push({
          startDate: currentCluster.startDate!,
          endDate: data[i - 1].date,
          startPrice: currentCluster.startPrice!,
          endPrice,
          avgDeliveryPct: currentCluster.avgDeliveryPct!,
          clusterStrength:
            currentCluster.avgDeliveryPct! >= 75
              ? 'strong'
              : currentCluster.avgDeliveryPct! >= 65
                ? 'moderate'
                : 'weak',
          type: priceChange >= 0 ? 'support' : 'resistance',
        });
      }
      currentCluster = { count: 0, prices: [], avgDeliveryPct: 0 };
    }
  }

  if (currentCluster.count >= minConsecutiveDays) {
    const endPrice = currentCluster.prices![currentCluster.prices!.length - 1];
    const priceChange = endPrice - (currentCluster.startPrice || 0);

    clusters.push({
      startDate: currentCluster.startDate!,
      endDate: data[data.length - 1].date,
      startPrice: currentCluster.startPrice!,
      endPrice,
      avgDeliveryPct: currentCluster.avgDeliveryPct!,
      clusterStrength:
        currentCluster.avgDeliveryPct! >= 75
          ? 'strong'
          : currentCluster.avgDeliveryPct! >= 65
            ? 'moderate'
            : 'weak',
      type: priceChange >= 0 ? 'support' : 'resistance',
    });
  }

  return clusters;
}
