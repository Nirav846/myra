// Worker for heavy indicator calculations
// Offloads ATR, OBV, Delivery OBV, and Swing calculations from main thread

export type IndicatorDataType = 'atr' | 'obv' | 'deliveryObv' | 'swings';

export interface IndicatorWorker {
  calculateIndicators: (requestId: number, dataType: IndicatorDataType, data: any[], params?: any) => Promise<any>;
}

const calculateIndicator = (dataType: IndicatorDataType, data: any[], params?: any): any => {
  switch (dataType) {
    case 'atr': {
      // 14-period ATR using Wilder's smoothing
      if (!data || data.length < 2) {
        return data ? data.map(() => 0) : [];
      }
      const tr: number[] = [data[0].high - data[0].low];
      for (let i = 1; i < data.length; i++) {
        const hl = data[i].high - data[i].low;
        const hc = Math.abs(data[i].high - data[i - 1].close);
        const lc = Math.abs(data[i].low - data[i - 1].close);
        tr.push(Math.max(hl, hc, lc));
      }
      const arr: number[] = [];
      let atrVal = tr.slice(0, 14).reduce((a, b) => a + b, 0) / 14;
      for (let i = 0; i < 14 && i < data.length; i++) arr.push(atrVal);
      for (let i = 14; i < data.length; i++) {
        atrVal = (atrVal * 13 + tr[i]) / 14;
        arr.push(atrVal);
      }
      return arr;
    }
    
    case 'obv': {
      // Standard OBV calculation
      if (!data || data.length === 0) {
        return [];
      }
      const arr: number[] = [];
      let cum = 0;
      for (const d of data) {
        const vol = Number(d.volume_final ?? d.volume ?? 0);
        if (d.close > d.open) cum += vol;
        else if (d.close < d.open) cum -= vol;
        arr.push(cum);
      }
      return arr;
    }
    
    case 'deliveryObv': {
      // Delivery-Weighted OBV
      if (!data || data.length === 0) {
        return [];
      }
      const arr: number[] = [];
      let cum = 0;
      for (const d of data) {
        const del = Number(d.delivery_final ?? d.delivery ?? 0);
        if (d.close > d.open) cum += del;
        else if (d.close < d.open) cum -= del;
        arr.push(cum);
      }
      return arr;
    }
    
    case 'swings': {
      // Swing high/low detection
      if (!data || data.length === 0) {
        return null;
      }
      const swingHighs: number[] = [];
      const swingLows: number[] = [];
      const lookback = params?.lookback || 5;
      
      for (let i = lookback; i < data.length - lookback; i++) {
        let isSwingHigh = true;
        let isSwingLow = true;
        
        for (let j = 1; j <= lookback; j++) {
          if (data[i].high <= data[i - j].high || data[i].high <= data[i + j].high) {
            isSwingHigh = false;
          }
          if (data[i].low >= data[i - j].low || data[i].low >= data[i + j].low) {
            isSwingLow = false;
          }
        }
        
        if (isSwingHigh) swingHighs.push(i);
        if (isSwingLow) swingLows.push(i);
      }
      
      return { swingHighs, swingLows };
    }
    
    default:
      console.error(`Unknown indicator type:`);
      return null;
  }
};

// Expose the function to Comlink
const workerFunctions = {
  calculateIndicators: async (requestId: number, dataType: IndicatorDataType, data: any[], params?: any) => {
    try {
      const result = calculateIndicator(dataType, data, params);
      return result;
    } catch (error) {
      console.error('Error in indicator worker:', error);
      throw error;
    }
  }
};

export default workerFunctions;
