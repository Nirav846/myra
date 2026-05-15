import { LiqVoidSettings, VOID_THRESHOLDS, getVolatilityRegime, applyVolatilityScalar } from './indicatorConfig';

export type VoidFillStatus = 'unfilled' | 'partial' | 'filled';

export interface LiquidityVoid {
  id: string;
  startDate: string;
  endDate: string | null;
  direction: 'bullish' | 'bearish';
  topPrice: number;
  bottomPrice: number;
  status: VoidFillStatus;
  strength: number;
}

export function computeLiquidityVoids(data: any[], bucket: string | null, settings: LiqVoidSettings): LiquidityVoid[] {
  const voids: LiquidityVoid[] = [];
  if (data.length < 20) return voids;

  const bkt = bucket || 'Broader Market (N500)';
  const defaults = VOID_THRESHOLDS[bkt] || VOID_THRESHOLDS['Broader Market (N500)'];

  const atrPeriod = 14;
  const volPeriod = 20;

  for (let i = Math.max(atrPeriod, volPeriod); i < data.length; i++) {
    const bar = data[i];
    const prev = data[i - 1];
    
    // Calculate simple rolling ATR proxy
    let trSum = 0;
    for (let j = i - atrPeriod; j < i; j++) {
      const h = data[j].high;
      const l = data[j].low;
      const pc = data[j - 1] ? data[j - 1].close : l;
      const tr = Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc));
      trSum += tr;
    }
    const currentAtr = trSum / atrPeriod;

    // Historical ATR
    let histTrSum = 0;
    const histStart = Math.max(1, i - atrPeriod * 3);
    for (let j = histStart; j < i - atrPeriod; j++) {
        const h = data[j].high;
        const l = data[j].low;
        const pc = data[j - 1] ? data[j - 1].close : l;
        const tr = Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc));
        histTrSum += tr;
    }
    const historicalPeriod = i - atrPeriod - histStart;
    const historicalAtr = historicalPeriod > 0 ? histTrSum / historicalPeriod : currentAtr;

    let volSum = 0;
    for (let j = i - volPeriod; j < i; j++) {
      volSum += (data[j].volume || 0);
    }
    const avgVol = volSum / volPeriod;
    
    const regime = getVolatilityRegime(currentAtr, historicalAtr);

    let atrMult = applyVolatilityScalar(settings.minAtrMultiplier !== undefined ? settings.minAtrMultiplier : defaults.minAtrMultiplier, regime, settings.volatilityScaling);
    let maxVolMult = applyVolatilityScalar(settings.maxVolumeMultiplier !== undefined ? settings.maxVolumeMultiplier : defaults.maxVolumeMultiplier, regime, settings.volatilityScaling);

    // Breakaway gap check
    const isBullGap = bar.low > prev.high;
    const isBearGap = bar.high < prev.low;
    
    const bodySize = Math.abs(bar.close - bar.open);
    const requiredBody = currentAtr * atrMult;
    const isLowVolume = (bar.volume || 0) < (avgVol * maxVolMult);

    if ((isBullGap || isBearGap) || (bodySize > requiredBody && isLowVolume)) {
       let topPrice = 0;
       let bottomPrice = 0;
       let direction: 'bullish' | 'bearish' = 'bullish';

       if (isBullGap) {
           topPrice = bar.low;
           bottomPrice = prev.high;
           direction = 'bullish';
       } else if (isBearGap) {
           topPrice = prev.low;
           bottomPrice = bar.high;
           direction = 'bearish';
       } else if (bar.close > bar.open) { // solid bullish candle
           topPrice = Math.min(bar.close, bar.high - (bar.high - bar.low)*0.1);
           bottomPrice = Math.max(bar.open, bar.low + (bar.high - bar.low)*0.1);
           direction = 'bullish';
       } else {
           topPrice = Math.max(bar.open, bar.low + (bar.high - bar.low)*0.1); // using high/low approximations
           bottomPrice = Math.min(bar.close, bar.high - (bar.high - bar.low)*0.1);
           direction = 'bearish';
           if (topPrice < bottomPrice) {
               const tmp = topPrice;
               topPrice = bottomPrice;
               bottomPrice = tmp;
           }
       }

       if (topPrice > bottomPrice) {
           const bodyContrib = Math.min(100, (bodySize / currentAtr) * 50);
           const volContrib = Math.min(100, (avgVol / Math.max(1, bar.volume)) * 50);
           const strength = Math.min(100, bodyContrib + volContrib);

           if (strength >= settings.minStrengthScore) {
             voids.push({
                 id: direction + '-' + bar.date,
                 startDate: bar.date,
                 endDate: null,
                 direction,
                 topPrice,
                 bottomPrice,
                 status: 'unfilled',
                 strength
             });
           }
       }
    }
  }

  // Fill tracking
  for (let i = 0; i < voids.length; i++) {
     const vd = voids[i];
     const startIdx = data.findIndex(d => d.date === vd.startDate);
     if (startIdx === -1) continue;

     let currentTop = vd.topPrice;
     let currentBottom = vd.bottomPrice;

     for (let j = startIdx + 1; j < data.length; j++) {
         const bar = data[j];
         if (vd.direction === 'bullish') {
             if (bar.low < currentTop) {
                 vd.status = 'partial';
                 currentTop = bar.low;
             }
             if (currentTop <= currentBottom) {
                 vd.status = 'filled';
                 vd.endDate = bar.date;
                 break;
             }
         } else {
             if (bar.high > currentBottom) {
                 vd.status = 'partial';
                 currentBottom = bar.high;
             }
             if (currentBottom >= currentTop) {
                 vd.status = 'filled';
                 vd.endDate = bar.date;
                 break;
             }
         }
     }
  }

  if (settings.hideFilledVoids) {
     return voids.filter(v => v.status !== 'filled').sort((a, b) => b.strength - a.strength);
  }

  return voids.sort((a, b) => {
      const s1 = a.status === 'unfilled' ? 2 : a.status === 'partial' ? 1 : 0;
      const s2 = b.status === 'unfilled' ? 2 : b.status === 'partial' ? 1 : 0;
      if (s1 !== s2) return s2 - s1;
      return b.strength - a.strength;
  });
}

export function liqVoidsToShapes(voids: LiquidityVoid[], dates: string[], settings: LiqVoidSettings): any[] {
   const shapes: any[] = [];
   if (dates.length === 0) return shapes;
   const latestDate = dates[dates.length - 1];

   voids.forEach(vd => {
       const opacities = {
           'unfilled': 0.8,
           'partial': 0.4,
           'filled': 0.1
       };
       const opacity = opacities[vd.status];
       const color = vd.direction === 'bullish' ? `rgba(34,197,94,${opacity})` : `rgba(239,68,68,${opacity})`;
       const lineThick = vd.strength > 80 ? 2 : vd.strength > 50 ? 1 : 0;

       shapes.push({
           type: 'rect',
           xref: 'x', yref: 'y',
           x0: vd.startDate, x1: vd.endDate || latestDate,
           y0: vd.bottomPrice, y1: vd.topPrice,
           fillcolor: color,
           line: { width: lineThick, color: color },
           layer: 'below'
       });
   });
   return shapes;
}
