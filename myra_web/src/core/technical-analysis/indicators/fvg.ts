import { Candle, IndicatorModule } from '../types';

export interface FVGConfig {
  showMitigated: boolean;
}

export interface FVG {
  startIndex: number;
  startDate: string;
  endIndex: number;
  endDate: string;
  top: number;
  bottom: number;
  type: 'bullish' | 'bearish';
  mitigated: boolean;
}

export const calculateFVG = (data: Candle[], config: FVGConfig): FVG[] => {
  const fvgs: FVG[] = [];
  
  for (let i = 2; i < data.length; i++) {
    const c1 = data[i-2];
    const c3 = data[i];
    
    const c3Low = typeof c3.low === 'number' ? c3.low : -1;
    const c3High = typeof c3.high === 'number' ? c3.high : -1;
    const c1Low = typeof c1.low === 'number' ? c1.low : -1;
    const c1High = typeof c1.high === 'number' ? c1.high : -1;
    
    if (c3Low === -1 || c3High === -1 || c1Low === -1 || c1High === -1) continue;
    
    // Bullish FVG
    if (c1High < c3Low) {
      fvgs.push({
        startIndex: i-2,
        startDate: c1.date,
        endIndex: -1,
        endDate: '',
        top: c3Low,
        bottom: c1High,
        type: 'bullish',
        mitigated: false
      });
    }
    // Bearish FVG
    else if (c1Low > c3High) {
      fvgs.push({
        startIndex: i-2,
        startDate: c1.date,
        endIndex: -1,
        endDate: '',
        top: c1Low,
        bottom: c3High,
        type: 'bearish',
        mitigated: false
      });
    }
  }

  // Mitigation pass
  for (let i = 0; i < fvgs.length; i++) {
    const fvg = fvgs[i];
    for (let j = fvg.startIndex + 2; j < data.length; j++) {
      const candle = data[j];
      if (fvg.type === 'bullish' && candle.low <= fvg.bottom) {
        fvg.mitigated = true;
        fvg.endIndex = j;
        fvg.endDate = candle.date;
        break;
      } else if (fvg.type === 'bearish' && candle.high >= fvg.top) {
        fvg.mitigated = true;
        fvg.endIndex = j;
        fvg.endDate = candle.date;
        break;
      }
    }
    if (!fvg.mitigated) {
        fvg.endIndex = data.length - 1;
        fvg.endDate = data[data.length - 1].date;
    }
  }

  return fvgs.filter(f => config.showMitigated || !f.mitigated);
};

export const fvgIndicator: IndicatorModule<FVG[], FVGConfig> = {
  id: 'fvg',
  defaults: {
    showMitigated: false
  },
  calculate: calculateFVG
};
