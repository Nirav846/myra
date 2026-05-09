import { Candle } from './types';
import { NormalizedViewport } from '../../store/chartStore';

export interface VolumeProfileConfig {
  resolution: 'cumulative' | 'monthly' | 'weekly' | 'auto';
  bins?: number;
  showDeliveryProfile?: boolean;
  showDeliverySR?: boolean;
}

export interface VolumeProfileResult {
  volProfileX: number[];
  volProfileY: number[];
  volProfileColors: string[];
  deliveryProfileX: number[];
  deliveryProfileY: number[];
  deliveryProfileColors: string[];
  pocVolY: number | null;
  pocDelY: number | null;
}

export const calculateVolumeProfile = (
  data: Candle[], 
  viewport: NormalizedViewport | null,
  config: VolumeProfileConfig
): VolumeProfileResult | null => {
  let sliceStart = 0;
  let sliceEnd = data.length - 1;

  if (config.resolution === 'cumulative') {
      sliceStart = 0;
      sliceEnd = data.length - 1;
  } else if (viewport) {
      sliceStart = Math.max(0, Math.floor(viewport.startIndex));
      sliceEnd = Math.min(data.length - 1, Math.ceil(viewport.endIndex));
  }
  
  if (sliceStart > sliceEnd) return null;

  let visibleData = data.slice(sliceStart, sliceEnd + 1);

  if (config.resolution === 'weekly' || config.resolution === 'monthly') {
      const aggregatedMap = new Map();
      visibleData.forEach((d: any) => {
          const dateObj = new Date(d.date);
          let key = d.date;
          if (config.resolution === 'weekly') {
              const dayNum = dateObj.getDay() || 7;
              dateObj.setDate(dateObj.getDate() + 4 - dayNum);
              const yearStart = new Date(dateObj.getFullYear(), 0, 1);
              const weekNo = Math.ceil((((dateObj.getTime() - yearStart.getTime()) / 86400000) + 1) / 7);
              key = `${dateObj.getFullYear()}-W${weekNo.toString().padStart(2, '0')}`;
          } else if (config.resolution === 'monthly') {
              key = `${dateObj.getFullYear()}-${(dateObj.getMonth() + 1).toString().padStart(2, '0')}`;
          }

          if (!aggregatedMap.has(key)) {
              aggregatedMap.set(key, { ...d });
          } else {
              const agg = aggregatedMap.get(key);
              agg.high = Math.max(agg.high, d.high);
              agg.low = Math.min(agg.low, d.low);
              agg.close = d.close; 
              agg.volume = (agg.volume || 0) + (d.volume || 0);
              agg.delivery_final = (agg.delivery_final || 0) + (d.delivery_final || d.delivery || 0);
              agg.volume_final = (agg.volume_final || 0) + (d.volume_final || d.volume || 0);
              agg.delivery = (agg.delivery || 0) + (d.delivery || 0);
          }
      });
      visibleData = Array.from(aggregatedMap.values());
  }

  if (visibleData.length === 0) return null;

  const vMinL = Math.min(...visibleData.map(d => typeof d.low === 'number' ? d.low : Infinity));
  const vMaxH = Math.max(...visibleData.map(d => typeof d.high === 'number' ? d.high : -Infinity));
  
  if (vMaxH <= vMinL) return null;

  const bins = config.bins || 60; 
  const binSize = (vMaxH - vMinL) / bins;
  
  const profileVol = new Array(bins).fill(0);
  const profileDelVol = new Array(bins).fill(0);

  visibleData.forEach((d) => {
      let typicalPrice = typeof d.close === 'number' ? d.close : 0;
      if (typeof d.high === 'number' && typeof d.low === 'number' && typeof d.close === 'number') {
        typicalPrice = (d.high + d.low + d.close) / 3;
      }
      
      let binIdx = Math.floor((typicalPrice - vMinL) / binSize);
      if (binIdx >= bins) binIdx = bins - 1;
      if (binIdx < 0) binIdx = 0;
      
      const v = d.volume_final || d.volume || 0;
      const delv = d.delivery_final || d.delivery || 0;
      
      profileVol[binIdx] += v;
      profileDelVol[binIdx] += delv;
  });

  let maxVol = 0;
  let maxDel = 0;
  let pocVolBin = -1;
  let pocDelBin = -1;
  const volProfileX: number[] = [];
  const volProfileY: number[] = [];
  const volProfileColors: string[] = [];
  const deliveryProfileX: number[] = [];
  const deliveryProfileY: number[] = [];
  const deliveryProfileColors: string[] = [];

  for (let i = 0; i < bins; i++) {
      if (profileVol[i] > 0) {
          const y = vMinL + (i * binSize) + (binSize / 2);
          volProfileY.push(y);
          volProfileX.push(profileVol[i]);
          volProfileColors.push('rgba(168, 85, 247, 0.4)'); // Solid Purple
          
          deliveryProfileY.push(y);
          deliveryProfileX.push(profileDelVol[i]);
          deliveryProfileColors.push('rgba(6, 182, 212, 0.4)'); // Solid Cyan
          
          if (profileVol[i] > maxVol) {
              maxVol = profileVol[i];
              pocVolBin = i;
          }
          if (profileDelVol[i] > maxDel) {
              maxDel = profileDelVol[i];
              pocDelBin = i;
          }
      }
  }

  let pocVolY = pocVolBin !== -1 ? vMinL + (pocVolBin * binSize) + (binSize / 2) : null;
  let pocDelY = pocDelBin !== -1 ? vMinL + (pocDelBin * binSize) + (binSize / 2) : null;

  return {
    volProfileX,
    volProfileY,
    volProfileColors,
    deliveryProfileX,
    deliveryProfileY,
    deliveryProfileColors,
    pocVolY,
    pocDelY
  };
};
