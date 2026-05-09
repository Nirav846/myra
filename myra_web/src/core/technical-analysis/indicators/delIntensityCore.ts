import { IndicatorModule } from '../types';

export interface DelIntensityCoreResult {
    instX: string[];
    instY: number[];
    divX: string[];
    divY: number[];
    retX: string[];
    retY: number[];
}

export const delIntensityCoreIndicator: IndicatorModule<any, DelIntensityCoreResult> = {
    id: 'delIntensityCore',
    
    calculate: (data, config, context) => { // we use data directly
        const instX: string[] = [];
        const instY: number[] = [];
        const divX: string[] = [];
        const divY: number[] = [];
        const retX: string[] = [];
        const retY: number[] = [];
        
        const avgDel60: number[] = [];
        for (let i = 0; i < data.length; i++) {
            if (i < 59) {
                avgDel60.push(0);
                continue;
            }
            let sum = 0;
            for (let j = 0; j < 60; j++) sum += (data[i-j].delivery_final ? Number(data[i-j].delivery_final) : Number(data[i-j].delivery) || 0);
            avgDel60.push(sum / 60);
        }

        for (let i = 0; i < data.length; i++) {
            const d = data[i];
            const delQty = d.delivery_final ? Number(d.delivery_final) : Number(d.delivery) || 0;
            const vol = d.volume_final != null ? Number(d.volume_final) : Number(d.volume) || 1;
            const delPct = d.delivery_pct != null ? d.delivery_pct : (delQty ? (delQty / vol * 100) : 0) || 0;
            
            const avgD = avgDel60[i] || 1;
            const divScore = d.divergence_score || 0;
            const score = d.delivery_intensity_score || 0;
             
            const isInstitutional = delQty > avgD * 1.5 && score > 70;
            
            const bodyHeight = Math.abs(d.close - d.open);
            const bBot = Math.min(d.open, d.close);
            
            const delPctNormalized = Math.min((delPct || 0) / 100, 1);
            const coreHeight = bodyHeight * delPctNormalized;
            const coreTop = bBot + coreHeight;
            
            if (isInstitutional) {
                instX.push(d.date, d.date, null as any);
                instY.push(bBot, coreTop, null as any);
            } else if (divScore > 0) {
                divX.push(d.date, d.date, null as any);
                divY.push(bBot, coreTop, null as any);
            } else {
                retX.push(d.date, d.date, null as any);
                retY.push(bBot, coreTop, null as any);
            }
        }
        
        return {
            instX, instY,
            divX, divY,
            retX, retY
        };
    }
};
