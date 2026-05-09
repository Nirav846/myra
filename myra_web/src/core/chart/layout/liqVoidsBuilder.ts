import { LayoutBuilder } from './types';

export const liqVoidsLayoutBuilder: LayoutBuilder = {
    id: 'liqVoids',

    buildShapes: (context) => {
        const shapes: any[] = [];
        const { data } = context;
        if (data.length === 0) return shapes;
        
        // This is a specialized view, so we will do the calculation here for now
        // Normally we'd use indicators, but liquidity voids combine ATR and Volume SMA
        // We'll calculate a simple 14-period ATR and 20-period Volume SMA
        
        const tr: number[] = [0];
        for (let i = 1; i < data.length; i++) {
            const h = data[i].high;
            const l = data[i].low;
            const pc = data[i-1].close;
            tr.push(Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc)));
        }

        const avgRange14: number[] = [];
        for (let i = 0; i < data.length; i++) {
            if (i < 13) {
                avgRange14.push(0);
                continue;
            }
            let sum = 0;
            for (let j = 0; j < 14; j++) sum += tr[i-j];
            avgRange14.push(sum / 14);
        }

        const avgVol20: number[] = [];
        for (let i = 0; i < data.length; i++) {
            if (i < 19) {
                avgVol20.push(0);
                continue;
            }
            let sum = 0;
            for (let j = 0; j < 20; j++) {
                sum += (data[i-j].volume_final ? Number(data[i-j].volume_final) : Number(data[i-j].volume) || 0);
            }
            avgVol20.push(sum / 20);
        }

        for (let i = 1; i < data.length; i++) {
            const d = data[i];
            const bodySize = Math.abs(d.close - d.open);
            const avgBody = avgRange14[i] || 1;
            const avgV = avgVol20[i] || 1;
            const vol = d.volume_final ? Number(d.volume_final) : Number(d.volume) || 0;

            if (bodySize > avgBody * 1.2 && vol < avgV * 0.9) {
                shapes.push({
                    type: 'rect',
                    xref: 'x', x0: data[i-1]?.date || d.date, x1: data[i+1]?.date || d.date,
                    yref: 'paper', y0: 0, y1: 1,
                    fillcolor: 'rgba(236, 72, 153, 0.08)', // Soft pink vertical band
                    line: { width: 0 },
                    layer: 'below'
                });
            }
        }
        
        return shapes;
    }
};
