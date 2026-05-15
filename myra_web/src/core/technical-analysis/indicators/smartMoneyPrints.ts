import { IndicatorModule } from '../types';

export interface SmartMoneyPrintsResult {
    x: string[];
    y: number[];
    text: string[];
    colors: string[];
    fillColors: string[];
    sizes: number[];
}

export const smartMoneyPrintsIndicator: IndicatorModule<any, SmartMoneyPrintsResult> = {
    id: 'smartMoneyPrints',
    
    calculate: (data, config, context) => {
        const dates: string[] = [];
        const ys: number[] = [];
        const text: string[] = [];
        const colors: string[] = [];
        const fillColors: string[] = [];
        const sizes: number[] = [];
        
        const avgVol20: number[] = [];
        let windowSum = 0;

        for (let i = 0; i < data.length; i++) {
            const currentVol = data[i].volume_final != null ? Number(data[i].volume_final) : Number(data[i].volume) || 0;
            windowSum += currentVol;

            if (i >= 20) {
                const oldVol = data[i - 20].volume_final != null ? Number(data[i - 20].volume_final) : Number(data[i - 20].volume) || 0;
                windowSum -= oldVol;
            }

            if (i < 19) {
                avgVol20.push(0);
            } else {
                avgVol20.push(windowSum / 20);
            }
        }

        for (let i = 0; i < data.length; i++) {
            const d = data[i];
            const vol = d.volume_final != null ? Number(d.volume_final) : Number(d.volume) || 1;
            const delPct = d.delivery_pct != null ? d.delivery_pct : (d.delivery_final ? (Number(d.delivery_final) / vol * 100) : 0) || 0;
            const avgV = avgVol20[i];
            
            if (avgV > 0 && vol > avgV * 1.5 && delPct > 60) {
                dates.push(d.date);
                const isBullish = d.close >= d.open;
                ys.push(isBullish ? d.low * 0.99 : d.high * 1.01);
                
                const volMult = vol / avgV;
                text.push(`Smart Money<br>Rel Vol: ${volMult.toFixed(1)}x<br>Del: ${(delPct).toFixed(1)}%`);
                
                colors.push(isBullish ? '#22c55e' : '#ef4444');
                fillColors.push(isBullish ? 'rgba(34, 197, 94, 0.2)' : 'rgba(239, 68, 68, 0.2)');
                
                // Scale marker size based on relative volume multiplier. Base size = 10, max = 24.
                const scaledSize = Math.min(24, 10 + (volMult - 1.5) * 4);
                sizes.push(scaledSize);
            }
        }
        
        return {
            x: dates,
            y: ys,
            text,
            colors,
            fillColors,
            sizes
        };
    }
};

