import { IndicatorModule } from '../types';

export interface LiqVoidsResult {
    voids: { start: string, end: string }[];
}

export const liqVoidsIndicator: IndicatorModule<any, LiqVoidsResult> = {
    id: 'liqVoids',
    
    calculate: (data, config, context) => {
        const voids: { start: string, end: string }[] = [];
        if (data.length === 0) return { voids };
        
        const tr: number[] = [0];
        for (let i = 1; i < data.length; i++) {
            const h = data[i].high;
            const l = data[i].low;
            const pc = data[i-1].close;
            tr.push(Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc)));
        }

        const avgRange14: number[] = [];
        let trSum = 0;
        for (let i = 0; i < data.length; i++) {
            trSum += tr[i];
            if (i >= 14) trSum -= tr[i - 14];
            
            if (i < 13) {
                avgRange14.push(0);
            } else {
                avgRange14.push(trSum / 14);
            }
        }

        const avgVol20: number[] = [];
        let volSum = 0;
        for (let i = 0; i < data.length; i++) {
            const currentVol = data[i].volume_final != null ? Number(data[i].volume_final) : Number(data[i].volume) || 0;
            volSum += currentVol;
            
            if (i >= 20) {
                const oldVol = data[i - 20].volume_final != null ? Number(data[i - 20].volume_final) : Number(data[i - 20].volume) || 0;
                volSum -= oldVol;
            }
            
            if (i < 19) {
                avgVol20.push(0);
            } else {
                avgVol20.push(volSum / 20);
            }
        }

        for (let i = 1; i < data.length; i++) {
            const d = data[i];
            const bodySize = Math.abs(d.close - d.open);
            const avgBody = avgRange14[i] || 1;
            const avgV = avgVol20[i] || 1;
            const vol = d.volume_final != null ? Number(d.volume_final) : Number(d.volume) || 0;

            if (bodySize > avgBody * 1.2 && vol < avgV * 0.7) {
                voids.push({
                    start: data[i-1]?.date || d.date,
                    end: data[i+1]?.date || d.date
                });
            }
        }
        
        return { voids };
    }
};
