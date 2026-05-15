import { TraceBuilder, TraceBuilderContext } from './types';
import { FrvpResult, FrvpConfig } from '../../../lib/frvpUtils';

export const volumeProfileTraceBuilder: TraceBuilder<FrvpResult, Partial<FrvpConfig> & { showDeliveryProfile?: boolean; showDeliverySR?: boolean; showDelDelta?: boolean }> = {
    id: 'volumeProfile',

    buildTraces: (result, context, config) => {
        const traces: any[] = [];
        
        if (result.levels && result.levels.length > 0) {
            traces.push({
                type: 'bar', 
                orientation: 'h',
                xaxis: 'x2', 
                yaxis: 'y',
                x: result.levels.map(l => l.volume), 
                y: result.levels.map(l => l.priceMid), 
                marker: { color: 'rgba(168, 85, 247, 0.4)' },
                name: 'Volume Profile', 
                opacity: 0.5, 
                hoverinfo: 'none', 
                showlegend: false
            });
            
            traces.push({
                type: 'bar', 
                orientation: 'h',
                xaxis: 'x2', 
                yaxis: 'y',
                x: result.levels.map(l => l.deliveryVol), 
                y: result.levels.map(l => l.priceMid), 
                marker: { color: 'rgba(6, 182, 212, 0.4)' },
                name: 'Delivery Profile', 
                opacity: 0.8, 
                hoverinfo: 'none', 
                showlegend: false
            });

            if (config?.showDelDelta && result.maxDeltaAbs > 0) {
                // Determine scaling ratio relative to vpMaxVolume
                const vpMaxVolume = result.maxVolume || 1;
                const getWidth = (d: number) => (Math.abs(d) / result.maxDeltaAbs) * (vpMaxVolume * 0.15);
                traces.push({
                   type: 'bar',
                   orientation: 'h',
                   xaxis: 'x2',
                   yaxis: 'y',
                   x: result.levels.map(l => getWidth(l.deltaDelivery)),
                   y: result.levels.map(l => l.priceMid),
                   marker: { color: result.levels.map(l => l.deltaDelivery >= 0 ? 'rgba(34,197,94,0.5)' : 'rgba(239,68,68,0.5)') },
                   name: 'Delivery Delta',
                   opacity: 0.9,
                   hoverinfo: 'none',
                   showlegend: false
                });
            }
        }

        return traces;
    },

    buildShapes: (result, context, config) => {
        const shapes: any[] = [];
        
        if (config?.showDeliveryProfile === true && result.pocVolumePrice !== null) {
            shapes.push({
                type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: result.pocVolumePrice, y1: result.pocVolumePrice,
                line: { color: 'rgba(136, 136, 136, 0.7)', width: 1.5, dash: 'solid' },
                label: { text: 'Vol POC', font: { size: 10, color: '#888' }, textposition: 'start' }
            });
        }

        if (config?.showDeliveryProfile === true && result.pocDeliveryPrice !== null) {
            shapes.push({
                type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: result.pocDeliveryPrice, y1: result.pocDeliveryPrice,
                line: { color: '#06b6d4', width: 2, dash: 'solid' },
                label: { text: 'Del POC', font: { size: 10, color: '#06b6d4' }, textposition: 'start' }
            });
        }

        if (config?.showDeliveryProfile === true) {
            if (result.vahY !== null) {
                shapes.push({
                   type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: result.vahY, y1: result.vahY,
                   line: { color: 'rgba(34,197,94,0.5)', width: 1, dash: 'dash' },
                   label: { text: 'VAH', font: { size: 9, color: 'rgba(34,197,94,0.8)' }, textposition: 'end' }
                });
            }
            if (result.valY !== null) {
                shapes.push({
                   type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: result.valY, y1: result.valY,
                   line: { color: 'rgba(239,68,68,0.5)', width: 1, dash: 'dash' },
                   label: { text: 'VAL', font: { size: 9, color: 'rgba(239,68,68,0.8)' }, textposition: 'end' }
                });
            }
        }
        
        // Add Delivery SR lines
        if (config?.showDeliverySR === true && result.levels && result.levels.length > 0) {
            const levels = result.levels;
            const maxProf = result.pocDelivery;
            for (let i = 2; i < levels.length - 2; i++) {
                if (levels[i].deliveryVol > levels[i-1].deliveryVol && 
                    levels[i].deliveryVol > levels[i-2].deliveryVol &&
                    levels[i].deliveryVol > levels[i+1].deliveryVol && 
                    levels[i].deliveryVol > levels[i+2].deliveryVol && 
                    levels[i].deliveryVol > maxProf * 0.3) {
                    
                    const y = levels[i].priceMid;
                    shapes.push({
                        type: 'line',
                        xref: 'paper', x0: 0, x1: 1,
                        yref: 'y', y0: y, y1: y,
                        line: { color: 'rgba(234, 179, 8, 0.6)', width: 2, dash: 'dot' },
                        label: { text: `Del SR`, font: { size: 10, color: '#eab308' }, textposition: 'end' }
                    });
                }
            }
        }
        
        return shapes;
    }
};
