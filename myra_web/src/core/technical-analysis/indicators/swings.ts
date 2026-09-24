import { IndicatorModule, Candle } from '../types';

export interface SwingPoint {
    type: 'high' | 'low';
    index: number;
    price: number;
}

export interface SwingsResult {
    highs: { dates: string[], values: number[] };
    lows: { dates: string[], values: number[] };
}

/**
 * Normalizes swing data from either the worker format
 * ({ swingHighs, swingLows } as candle index arrays) or the registry
 * format ({ highs/lows: { dates, values } }) into a unified SwingPoint[].
 */
export function toSwingPoints(
    swings: SwingsResult | { swingHighs: number[]; swingLows: number[] } | null | undefined,
    data: Candle[]
): SwingPoint[] {
    const points: SwingPoint[] = [];
    if (!swings || !data || data.length === 0) return points;

    const push = (type: 'high' | 'low', index: number, price: number) => {
        if (index >= 0 && index < data.length && Number.isFinite(price)) {
            points.push({ type, index, price });
        }
    };

    const worker = swings as { swingHighs?: number[]; swingLows?: number[] };
    if (Array.isArray(worker.swingHighs) && Array.isArray(worker.swingLows)) {
        worker.swingHighs.forEach(idx => push('high', idx, data[idx]?.high));
        worker.swingLows.forEach(idx => push('low', idx, data[idx]?.low));
    } else {
        const dateSwings = swings as SwingsResult;
        if (dateSwings?.highs && dateSwings?.lows) {
            const indexByDate = new Map(data.map((d, i) => [d.date, i]));
            dateSwings.highs.dates.forEach((date, k) => {
                const idx = indexByDate.get(date);
                if (idx !== undefined) push('high', idx, dateSwings.highs.values[k]);
            });
            dateSwings.lows.dates.forEach((date, k) => {
                const idx = indexByDate.get(date);
                if (idx !== undefined) push('low', idx, dateSwings.lows.values[k]);
            });
        }
    }

    return points.sort((a, b) => a.index - b.index);
}

export const swingsIndicator: IndicatorModule<any, SwingsResult> = {
    id: 'swings',
    
    calculate: (data, config, context) => { // we use data directly
        const swingHighsDates: string[] = [];
        const swingHighsValues: number[] = [];
        const swingLowsDates: string[] = [];
        const swingLowsValues: number[] = [];
        
        if (data.length > 4) {
           const n = 2; // bars to left and right
           for (let i = n; i < data.length - n; i++) {
               let isHigh = true;
               let isLow = true;
               for (let j = 1; j <= n; j++) {
                   if (data[i].high <= data[i-j].high || data[i].high <= data[i+j].high) isHigh = false;
                   if (data[i].low >= data[i-j].low || data[i].low >= data[i+j].low) isLow = false;
               }
               if (isHigh) {
                  swingHighsDates.push(data[i].date);
                  swingHighsValues.push(data[i].high);
               }
               if (isLow) {
                  swingLowsDates.push(data[i].date);
                  swingLowsValues.push(data[i].low);
               }
           }
        }
        
        return {
            highs: { dates: swingHighsDates, values: swingHighsValues },
            lows: { dates: swingLowsDates, values: swingLowsValues },
        };
    }
};
