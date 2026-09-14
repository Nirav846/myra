import { TraceBuilder } from './types';

export const volumeTraceBuilder: TraceBuilder<number[], any> = {
    id: 'volume',
    buildTraces: (result, context) => {
        // Enhanced volume with gradient-like opacity based on volume intensity
        const maxVol = Math.max(...result.filter(v => v > 0));
        const avgVol = result.reduce((a, b) => a + b, 0) / result.length;
        
        const volumeColors = context.data.map((d, i) => {
            const isBullish = d.close >= d.open;
            const volRatio = result[i] / maxVol;
            const isHighVol = result[i] > avgVol * 1.5;
            
            // Base color
            const baseColor = isBullish ? [34, 197, 94] : [239, 68, 68];
            
            // Adjust opacity based on volume intensity
            const opacity = Math.min(0.4 + (volRatio * 0.6), 0.9);
            
            if (isHighVol) {
                // Highlight high volume bars with brighter color
                return `rgba(${baseColor[0]}, ${baseColor[1]}, ${baseColor[2]}, ${opacity})`;
            }
            return `rgba(${baseColor[0]}, ${baseColor[1]}, ${baseColor[2]}, ${opacity * 0.8})`;
        });
        
        return [{
            type: 'bar',
            x: context.candleIndexes,
            y: result,
            name: 'Vol',
            yaxis: 'y2',
            marker: { 
                color: volumeColors,
                line: { width: 0 }
            },
            hovertemplate: '<b>Volume</b><br>%{y:.2s}<extra></extra>'
        }];
    }
};
