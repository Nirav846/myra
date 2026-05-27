import { TraceBuilder } from './types';
import { SwingsResult } from '../../technical-analysis/indicators/swings';

export const swingsTraceBuilder: TraceBuilder<SwingsResult, any> = {
    id: 'swings',

    buildTraces: (result, context) => {
        const { dateToIndex } = context;
        const traces = [];
        if (result.highs.dates.length > 0) {
            const x = result.highs.dates.map(d => dateToIndex.get(d) ?? 0);
            traces.push({
                type: 'scattergl', 
                mode: 'markers+text', 
                x, 
                y: result.highs.values, 
                name: 'Swing High', 
                marker: { symbol: 'triangle-down', size: 8, color: '#ef4444' }, 
                text: result.highs.values.map(v => v?.toFixed(1)), 
                textposition: 'top center', 
                textfont: {size: 9, color: '#ef4444'}, 
                yaxis: 'y'
            });
        }
        if (result.lows.dates.length > 0) {
            const x = result.lows.dates.map(d => dateToIndex.get(d) ?? 0);
            traces.push({
                type: 'scattergl', 
                mode: 'markers+text', 
                x, 
                y: result.lows.values, 
                name: 'Swing Low', 
                marker: { symbol: 'triangle-up', size: 8, color: '#22c55e' }, 
                text: result.lows.values.map(v => v?.toFixed(1)), 
                textposition: 'bottom center', 
                textfont: {size: 9, color: '#22c55e'}, 
                yaxis: 'y'
            });
        }
        return traces;
    }
};
