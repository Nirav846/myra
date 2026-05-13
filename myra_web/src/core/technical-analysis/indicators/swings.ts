import { IndicatorModule } from '../types';

export interface SwingsResult {
    highs: { dates: string[], values: number[] };
    lows: { dates: string[], values: number[] };
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
