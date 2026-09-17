/**
 * Delivery Profile — EXPERIMENTAL / UNVALIDATED
 *
 * Like Volume Profile, but bucketed by delivery quantity instead of traded volume.
 * Shows price levels where genuine historical accumulation occurred (shares actually
 * taken for delivery, not just traded).
 *
 * NOTE: This indicator is experimental and has NOT been backtested or calibrated.
 * Thresholds are preliminary guesses pending empirical validation.
 */

import type { Candle } from '../../../core/technical-analysis/types';

export interface DeliveryProfileBucket {
  priceLow: number;
  priceHigh: number;
  deliveryTotal: number;
  deliveryPctTotal: number;
  candleCount: number;
  isPOC: boolean; // Point of Control — bucket with highest delivery
}

export interface DeliveryProfileResult {
  buckets: DeliveryProfileBucket[];
  pocPrice: number;
  pocDelivery: number;
  priceRange: { min: number; max: number };
}

export interface DeliveryProfileConfig {
  numBuckets: number;
  lookbackPeriod: number;
  pocHighlight: boolean;
  showPOCLine: boolean;
  weightingMode: 'delivery_pct' | 'delivery_raw';
  isExperimental: boolean; // Always true — UI label flag
}

const DEFAULT_CONFIG: DeliveryProfileConfig = {
  numBuckets: 40,
  lookbackPeriod: 120,
  pocHighlight: true,
  showPOCLine: true,
  weightingMode: 'delivery_pct',
  isExperimental: true,
};

/**
 * Calculate delivery profile from candle data
 */
export function calculateDeliveryProfile(
  candles: Candle[],
  config: Partial<DeliveryProfileConfig> = {}
): DeliveryProfileResult {
  const fullConfig = { ...DEFAULT_CONFIG, ...config };

  if (candles.length === 0) {
    return { buckets: [], pocPrice: 0, pocDelivery: 0, priceRange: { min: 0, max: 0 } };
  }

  const lookback = Math.min(fullConfig.lookbackPeriod, candles.length);
  const data = candles.slice(-lookback);

  // Determine price range
  let priceMin = Infinity;
  let priceMax = -Infinity;
  for (const c of data) {
    if (c.low < priceMin) priceMin = c.low;
    if (c.high > priceMax) priceMax = c.high;
  }

  // Add small padding
  const pricePad = (priceMax - priceMin) * 0.02;
  priceMin -= pricePad;
  priceMax += pricePad;

  const bucketSize = (priceMax - priceMin) / fullConfig.numBuckets;

  // Initialize buckets
  const buckets: DeliveryProfileBucket[] = [];
  for (let i = 0; i < fullConfig.numBuckets; i++) {
    buckets.push({
      priceLow: priceMin + i * bucketSize,
      priceHigh: priceMin + (i + 1) * bucketSize,
      deliveryTotal: 0,
      deliveryPctTotal: 0,
      candleCount: 0,
      isPOC: false,
    });
  }

  // Accumulate delivery into buckets
  for (const c of data) {
    const typicalPrice = (c.high + c.low + c.close) / 3;
    const bucketIdx = Math.min(
      fullConfig.numBuckets - 1,
      Math.max(0, Math.floor((typicalPrice - priceMin) / bucketSize))
    );

    const bucket = buckets[bucketIdx];
    const weight = fullConfig.weightingMode === 'delivery_pct'
      ? (c.delivery_pct || 0)
      : (c.delivery || 0);

    bucket.deliveryTotal += weight;
    bucket.deliveryPctTotal += (c.delivery_pct || 0);
    bucket.candleCount += 1;
  }

  // Find POC (Point of Control)
  let maxDelivery = 0;
  let pocIdx = 0;
  for (let i = 0; i < buckets.length; i++) {
    if (buckets[i].deliveryTotal > maxDelivery) {
      maxDelivery = buckets[i].deliveryTotal;
      pocIdx = i;
    }
  }

  buckets[pocIdx].isPOC = true;

  return {
    buckets,
    pocPrice: (buckets[pocIdx].priceLow + buckets[pocIdx].priceHigh) / 2,
    pocDelivery: maxDelivery,
    priceRange: { min: priceMin, max: priceMax },
  };
}

/**
 * Build Plotly horizontal bar traces for delivery profile
 */
export function buildDeliveryProfileTraces(
  candles: Candle[],
  config: Partial<DeliveryProfileConfig> = {}
): any[] {
  const profile = calculateDeliveryProfile(candles, config);
  if (profile.buckets.length === 0) return [];

  const fullConfig = { ...DEFAULT_CONFIG, ...config };
  const traces: any[] = [];

  const yLabels = profile.buckets.map(b =>
    `${b.priceLow.toFixed(0)}-${b.priceHigh.toFixed(0)}`
  );
  const xValues = profile.buckets.map(b =>
    fullConfig.weightingMode === 'delivery_pct' ? b.deliveryPctTotal : b.deliveryTotal
  );

  // Normalize for display (0-100 scale)
  const maxVal = Math.max(...xValues.filter(v => v > 0), 1);
  const normalized = xValues.map(v => (v / maxVal) * 100);

  // Main bars
  traces.push({
    type: 'bar',
    orientation: 'h',
    y: yLabels,
    x: normalized,
    name: fullConfig.weightingMode === 'delivery_pct' ? 'Delivery Profile (%)' : 'Delivery Profile (Qty)',
    marker: {
      color: profile.buckets.map(b =>
        b.isPOC && fullConfig.pocHighlight
          ? 'rgba(255, 215, 0, 0.8)' // Gold for POC
          : 'rgba(34, 197, 94, 0.5)' // Green for others
      ),
      line: {
        width: profile.buckets.map(b => b.isPOC ? 2 : 0),
        color: profile.buckets.map(b => b.isPOC ? '#ffd700' : 'transparent'),
      },
    },
    hovertemplate: '%{y}<br>Delivery: %{x:.1f}<extra></extra>',
    xaxis: 'x2', // Secondary x-axis for profile
    yaxis: 'y',
    showlegend: false,
  });

  // POC line
  if (fullConfig.showPOCLine) {
    traces.push({
      type: 'scatter',
      mode: 'lines',
      x: [0, 100],
      y: [
        `${profile.pocPrice.toFixed(0)}-${(profile.pocPrice + (profile.priceRange.max - profile.priceRange.min) / fullConfig.numBuckets).toFixed(0)}`,
        `${profile.pocPrice.toFixed(0)}-${(profile.pocPrice + (profile.priceRange.max - profile.priceRange.min) / fullConfig.numBuckets).toFixed(0)}`,
      ],
      name: 'POC',
      line: { color: '#ffd700', width: 2, dash: 'dash' },
      hoverinfo: 'skip',
      showlegend: false,
      xaxis: 'x2',
      yaxis: 'y',
    });
  }

  return traces;
}

/**
 * Get profile summary for crosshair tooltip
 */
export function getDeliveryProfileAtPrice(
  candles: Candle[],
  price: number,
  config: Partial<DeliveryProfileConfig> = {}
): DeliveryProfileBucket | null {
  const profile = calculateDeliveryProfile(candles, config);
  for (const b of profile.buckets) {
    if (price >= b.priceLow && price <= b.priceHigh) {
      return b;
    }
  }
  return null;
}

export { DEFAULT_CONFIG };