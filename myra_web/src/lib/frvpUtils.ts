export type McapBucket = 'Large Cap (N100)' | 'Mid Cap (N101-250)' | 'Small Cap (N251-500)' | 'Micro Cap (N501-1000)' | 'Deep Frontier (N1001+)';

export interface FrvpConfig {
  bucket: McapBucket | null;
  resolution: string | number;
  visibleBars: number;
  priceRange: number;
  avgVolatility: number;
}

export interface FrvpLevel {
  priceBottom: number;
  priceTop: number;
  priceMid: number;
  volume: number;
  deliveryVol: number;
  deltaDelivery: number;
}

export interface FrvpResult {
  levels: FrvpLevel[];
  pocVolume: number;
  pocDelivery: number;
  pocVolumePrice: number | null;
  pocDeliveryPrice: number | null;
  vahY: number | null;
  valY: number | null;
  maxVolume: number;
  maxDeltaAbs: number;
}

export function snapToCleanLevel(val: number, step: number): number {
  return Math.round(val / step) * step;
}

export function computeBinSize(config: FrvpConfig): number {
  if (typeof config.resolution === 'number') {
    return config.resolution;
  }
  
  let targetBins = 100;
  if (config.resolution === 'high') targetBins = 200;
  if (config.resolution === 'low') targetBins = 50;
  
  if (config.bucket) {
    switch (config.bucket) {
      case 'Large Cap (N100)':
        targetBins *= 1.2;
        break;
      case 'Deep Frontier (N1001+)':
        targetBins *= 0.5;
        break;
    }
  }

  targetBins = Math.round(targetBins * Math.min(2, Math.max(0.5, config.visibleBars / 100)));

  const rawStep = config.priceRange / Math.max(10, targetBins);
  
  const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep)));
  if (magnitude === 0) return Math.max(0.05, rawStep); // Prevent NaN if priceRange is 0
  const normalized = rawStep / magnitude;
  
  let step = 1;
  if (normalized > 5) step = 10;
  else if (normalized > 2) step = 5;
  else if (normalized > 1) step = 2;
  else step = 1;

  return Math.max(0.05, step * magnitude);
}

export function computeFrvp(data: any[], config: FrvpConfig): FrvpResult {
  const binSize = computeBinSize(config);
  const bins = new Map<number, FrvpLevel>();
  let totalVolume = 0;
  
  data.forEach(d => {
    const vol = Number(d.volume || 0);
    const del = Number(d.delivery || d.delivery_final || d.delivery_qty || 0);
    if (vol <= 0) return;
    
    const priceHigh = d.high || d.close;
    const priceLow = d.low || d.close;
    const startBin = Math.floor(priceLow / binSize) * binSize;
    const endBin = Math.floor(priceHigh / binSize) * binSize;
    
    const binCount = Math.max(1, Math.round((endBin - startBin) / binSize) + 1);
    const volPerBin = vol / binCount;
    const delPerBin = del / binCount;
    
    for (let b = startBin; b <= endBin + 0.001; b += binSize) {
      const snapB = snapToCleanLevel(b, binSize);
      let level = bins.get(snapB);
      if (!level) {
        level = {
          priceBottom: snapB,
          priceTop: snapB + binSize,
          priceMid: snapB + (binSize / 2),
          volume: 0,
          deliveryVol: 0,
          deltaDelivery: 0
        };
        bins.set(snapB, level);
      }
      level.volume += volPerBin;
      level.deliveryVol += delPerBin;
      
      const isUp = d.close >= (d.open || d.close);
      if (isUp) level.deltaDelivery += delPerBin;
      else level.deltaDelivery -= delPerBin;
      
      totalVolume += volPerBin;
    }
  });

  const levels = Array.from(bins.values()).sort((a, b) => a.priceMid - b.priceMid);
  
  let pocVolume = 0;
  let pocDelivery = 0;
  let pocVolumePrice: number | null = null;
  let pocDeliveryPrice: number | null = null;
  let maxVolume = 0;
  let maxDeltaAbs = 0;
  
  levels.forEach(l => {
    if (l.volume > pocVolume) {
      pocVolume = l.volume;
      pocVolumePrice = l.priceMid;
    }
    if (l.deliveryVol > pocDelivery) {
      pocDelivery = l.deliveryVol;
      pocDeliveryPrice = l.priceMid;
    }
    if (l.volume > maxVolume) maxVolume = l.volume;
    const absDelta = Math.abs(l.deltaDelivery);
    if (absDelta > maxDeltaAbs) maxDeltaAbs = absDelta;
  });
  
  const valueAreaVolume = totalVolume * 0.70;
  let currentVolume = pocVolume;
  
  let vahY = pocVolumePrice;
  let valY = pocVolumePrice;
  
  if (levels.length > 0 && pocVolumePrice !== null) {
      let pocIdx = levels.findIndex(l => l.priceMid === pocVolumePrice);
      if (pocIdx === -1) pocIdx = 0;
      let upperIdx = pocIdx + 1;
      let lowerIdx = pocIdx - 1;
      
      while (currentVolume < valueAreaVolume && (upperIdx < levels.length || lowerIdx >= 0)) {
          let upperVol = upperIdx < levels.length ? levels[upperIdx].volume : -1;
          let lowerVol = lowerIdx >= 0 ? levels[lowerIdx].volume : -1;
          
          if (upperVol >= lowerVol && upperVol !== -1) {
              currentVolume += upperVol;
              vahY = levels[upperIdx].priceMid;
              upperIdx++;
          } else if (lowerVol > upperVol && lowerVol !== -1) {
              currentVolume += lowerVol;
              valY = levels[lowerIdx].priceMid;
              lowerIdx--;
          } else {
              break;
          }
      }
      
      if (vahY !== null && valY !== null && vahY < valY) {
          const t = vahY;
          vahY = valY;
          valY = t;
      }
  }

  return { levels, pocVolume, pocDelivery, pocVolumePrice, pocDeliveryPrice, vahY, valY, maxVolume, maxDeltaAbs };
}
