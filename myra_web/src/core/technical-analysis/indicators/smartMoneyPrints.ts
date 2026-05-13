import { IndicatorModule } from '../types';

export interface SmartMoneyPrintsResult {
    x: string[];
    y: number[];
    text: string[];
}

export const smartMoneyPrintsIndicator: IndicatorModule<any, SmartMoneyPrintsResult> = {
    id: 'smartMoneyPrints',
    
    calculate: (data, config, context) => { // we use data directly
        const dates: string[] = [];
        const ys: number[] = [];
        const text: string[] = [];
        
        // simple SMA over 20 for volumes
        const avgVol20: number[] = [];
        for (let i = 0; i < data.length; i++) {
            if (i < 19) {
                // For the first 19 bars, we can compute a shorter SMA or just use 0 and not trigger it.
                // It's safer to not trigger smart money prints until we have at least 20 periods
                avgVol20.push(0);
                continue;
            }
            let sum = 0;
            for (let j = 0; j < 20; j++) sum += (data[i-j].volume_final != null ? Number(data[i-j].volume_final) : Number(data[i-j].volume) || 0);
            avgVol20.push(sum / 20);
        }

        for (let i = 0; i < data.length; i++) {
            const d = data[i];
            const vol = d.volume_final != null ? Number(d.volume_final) : Number(d.volume) || 1;
            const delPct = d.delivery_pct != null ? d.delivery_pct : (d.delivery_final ? (Number(d.delivery_final) / vol * 100) : 0) || 0;
            const avgV = avgVol20[i];
            
             // Criteria: High relative volume (>1.5x 20MA) AND high delivery % (>60%)
             // Require we have at least 20 periods (avgV > 0)
             if (avgV > 0 && vol > avgV * 1.5 && delPct > 60) {
                 dates.push(d.date);
                 const isBullish = d.close >= d.open;
                 ys.push(isBullish ? d.low * 0.99 : d.high * 1.01);
                 text.push(`Smart Money<br>Rel Vol: ${(vol/avgV).toFixed(1)}x<br>Del: ${(delPct).toFixed(1)}%`);
             }
        }
        
        return {
            x: dates,
            y: ys,
            text
        };
    }
};
