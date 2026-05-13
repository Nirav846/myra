import { IndicatorModule } from '../types';

export interface DelVwapBandsResult {
    mid: number[];
    upper: number[];
    lower: number[];
}

export const delVwapBandsIndicator: IndicatorModule<any, DelVwapBandsResult> = {
    id: 'delVwapBands',
    
    calculate: (data, config, context) => { // we use data directly
        let vwapSum = 0;
        let volSum = 0;
        let squaredDevSum = 0;
        const mid: number[] = [];
        const upper: number[] = [];
        const lower: number[] = [];
        
        for (let i = 0; i < data.length; i++) {
            const d = data[i];
            const vol = d.volume_final != null ? Number(d.volume_final) : Number(d.volume) || 1;
            const delPct = d.delivery_pct != null ? d.delivery_pct : (d.delivery_final ? (Number(d.delivery_final) / vol * 100) : 0) || 0;
            const delVol = vol * (delPct / 100);
            
            const typPrice = (d.high + d.low + d.close) / 3;
            
            vwapSum += typPrice * delVol;
            volSum += delVol;
            const cvwap = volSum > 0 ? vwapSum / volSum : typPrice;
            mid.push(cvwap);
            
            squaredDevSum += delVol * Math.pow(typPrice - cvwap, 2);
            const stdDev = volSum > 0 ? Math.sqrt(squaredDevSum / volSum) : 0;
            
            upper.push(cvwap + (stdDev * 1.5));
            lower.push(cvwap - (stdDev * 1.5));
        }
        
        return {
            mid,
            upper,
            lower
        };
    }
};
