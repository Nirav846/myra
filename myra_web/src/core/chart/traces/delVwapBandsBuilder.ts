import { TraceBuilder } from './types';
import { DelVwapBandsResult } from '../../technical-analysis/indicators/delVwapBands';

export const delVwapBandsTraceBuilder: TraceBuilder<DelVwapBandsResult, any> = {
    id: 'delVwapBands',

    buildTraces: (result, context) => {
        const traces: any[] = [];
        
        // Add gradient fill between upper and lower bands
        traces.push({
            type: 'scattergl',
            mode: 'lines',
            x: [...context.candleIndexes, ...context.candleIndexes.slice().reverse()],
            y: [...result.upper, ...result.lower.slice().reverse()],
            name: '',
            fill: 'toself',
            fillcolor: 'rgba(251, 146, 60, 0.15)',
            line: { width: 0 },
            yaxis: 'y',
            hoverinfo: 'skip',
            showlegend: false
        });
        
        // Upper band with enhanced visibility
        traces.push({
            type: 'scattergl',
            mode: 'lines',
            x: context.candleIndexes,
            y: result.upper,
            name: 'DWAP High',
            line: { 
                color: 'rgba(251, 146, 60, 0.9)',
                width: 2,
                dash: 'dash'
            },
            yaxis: 'y',
            hovertemplate: '<b>DWAP High</b><br>%{y:.2f}<extra></extra>'
        });
        
        // Lower band with enhanced visibility
        traces.push({
            type: 'scattergl',
            mode: 'lines',
            x: context.candleIndexes,
            y: result.lower,
            name: 'DWAP Low',
            line: { 
                color: 'rgba(251, 146, 60, 0.9)',
                width: 2,
                dash: 'dash'
            },
            yaxis: 'y',
            hovertemplate: '<b>DWAP Low</b><br>%{y:.2f}<extra></extra>'
        });
        
        // Middle VWAP line with glow effect
        // Add subtle glow behind main line
        traces.push({
            type: 'scattergl',
            mode: 'lines',
            x: context.candleIndexes,
            y: result.mid,
            name: '',
            line: { 
                color: 'rgba(251, 191, 36, 0.3)',
                width: 6,
                dash: 'solid'
            },
            yaxis: 'y',
            hoverinfo: 'skip',
            showlegend: false
        });
        
        // Main VWAP line
        traces.push({
            type: 'scattergl',
            mode: 'lines',
            x: context.candleIndexes,
            y: result.mid,
            name: 'DWAP',
            line: { 
                color: '#fbbf24',
                width: 2.5,
                dash: 'solid'
            },
            yaxis: 'y',
            hovertemplate: '<b>DWAP</b><br>%{y:.2f}<extra></extra>'
        });
        
        return traces;
    }
};
