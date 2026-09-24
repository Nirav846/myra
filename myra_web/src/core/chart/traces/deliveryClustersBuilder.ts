import { Candle } from '../../technical-analysis/types';

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
 * Delivery Clusters
 * Identifies consecutive high delivery activity days as support/resistance zones
 * Heat-map visualization showing institutional accumulation/distribution areas
 */
export function buildDeliveryClusters(
  data: Candle[],
  minConsecutiveDays: number = 3,
  threshold: number = 60 // Delivery % threshold
): any[] {
  if (data.length < minConsecutiveDays) return [];

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
    avgDeliveryPct: 0
  };

  for (let i = 0; i < data.length; i++) {
    const delivPct = getDeliveryPct(data[i]);
    
    if (delivPct >= threshold) {
      // Continue or start cluster
      if (currentCluster.count === 0) {
        currentCluster = {
          startDate: data[i].date,
          startPrice: data[i].close,
          count: 1,
          prices: [data[i].close],
          avgDeliveryPct: delivPct
        };
      } else {
        currentCluster.count++;
        currentCluster.prices.push(data[i].close);
        currentCluster.avgDeliveryPct = 
          (currentCluster.avgDeliveryPct * (currentCluster.count - 1) + delivPct) / currentCluster.count;
      }
    } else {
      // End cluster if it meets minimum days
      if (currentCluster.count >= minConsecutiveDays) {
        const endPrice = currentCluster.prices[currentCluster.prices.length - 1];
        const priceChange = endPrice - (currentCluster.startPrice || 0);
        
        clusters.push({
          startDate: currentCluster.startDate!,
          endDate: data[i - 1].date,
          startPrice: currentCluster.startPrice!,
          endPrice,
          avgDeliveryPct: currentCluster.avgDeliveryPct,
          clusterStrength: currentCluster.avgDeliveryPct >= 75 ? 'strong' : 
                          currentCluster.avgDeliveryPct >= 65 ? 'moderate' : 'weak',
          type: priceChange >= 0 ? 'support' : 'resistance'
        });
      }
      currentCluster = { count: 0, prices: [], avgDeliveryPct: 0 };
    }
  }

  // Handle cluster at end of data
  if (currentCluster.count >= minConsecutiveDays) {
    const endPrice = currentCluster.prices[currentCluster.prices.length - 1];
    const priceChange = endPrice - (currentCluster.startPrice || 0);
    
    clusters.push({
      startDate: currentCluster.startDate!,
      endDate: data[data.length - 1].date,
      startPrice: currentCluster.startPrice!,
      endPrice,
      avgDeliveryPct: currentCluster.avgDeliveryPct,
      clusterStrength: currentCluster.avgDeliveryPct >= 75 ? 'strong' : 
                      currentCluster.avgDeliveryPct >= 65 ? 'moderate' : 'weak',
      type: priceChange >= 0 ? 'support' : 'resistance'
    });
  }

  if (clusters.length === 0) return [];

  // Create heat-map rectangles for clusters
  const shapes: any[] = clusters.map((cluster, idx) => {
    const colorIntensity = cluster.avgDeliveryPct / 100;
    const baseColor = cluster.type === 'support' ? '16, 185, 129' : '239, 68, 68'; // emerald or red
    
    return {
      type: 'rect',
      xref: 'paper',
      yref: 'y',
      x0: cluster.startDate,
      x1: cluster.endDate,
      y0: Math.min(cluster.startPrice, cluster.endPrice) * 0.98,
      y1: Math.max(cluster.startPrice, cluster.endPrice) * 1.02,
      fillcolor: `rgba(${baseColor}, ${colorIntensity * 0.3})`,
      line: {
        color: `rgba(${baseColor}, ${colorIntensity})`,
        width: 2,
        dash: cluster.clusterStrength === 'strong' ? 'solid' : 'dot'
      },
      opacity: 0.4,
      name: `Cluster ${idx + 1}`,
      hovertemplate:
        `<b>${cluster.type.toUpperCase()} Cluster</b><br>` +
        `Period: ${cluster.startDate} to ${cluster.endDate}<br>` +
        `Avg Delivery: ${cluster.avgDeliveryPct.toFixed(1)}%<br>` +
        `Strength: ${cluster.clusterStrength}<br>` +
        `Price Range: ${cluster.startPrice.toFixed(2)} - ${cluster.endPrice.toFixed(2)}<br>` +
        `<extra></extra>`
    } as any;
  });

  // Add label markers for strong clusters
  const strongClusters = clusters.filter(c => c.clusterStrength === 'strong');
  const labelTrace: any = {
    type: 'scatter',
    x: strongClusters.map(c => c.endDate),
    y: strongClusters.map(c => c.type === 'support' ? 
      Math.min(c.startPrice, c.endPrice) * 0.97 : 
      Math.max(c.startPrice, c.endPrice) * 1.03),
    name: 'Strong Clusters',
    mode: 'text',
    text: strongClusters.map(c => 
      `🔥 ${c.type === 'support' ? 'Support' : 'Resistance'}`
    ),
    textfont: {
      size: 11,
      weight: 'bold',
      color: strongClusters.map(c => c.type === 'support' ? '#10b981' : '#ef4444')
    },
    hoverinfo: 'skip'
  };

  return [...shapes, labelTrace];
}

export default buildDeliveryClusters;
