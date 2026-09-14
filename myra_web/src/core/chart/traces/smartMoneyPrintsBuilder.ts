import { TraceBuilder } from './types';
import { SmartMoneyPrintsResult } from '../../technical-analysis/indicators/smartMoneyPrints';

export const smartMoneyPrintsTraceBuilder: TraceBuilder<SmartMoneyPrintsResult, any> = {
    id: 'smartMoneyPrints',

    buildTraces: (result, context) => {
        if (result.x.length === 0) return [];
        const { dateToIndex } = context;
        const x = result.x.map(d => dateToIndex.get(d) ?? 0);
        
        const traces: any[] = [];
        
        // Add subtle glow effect behind each SMP marker
        traces.push({
            type: 'scattergl',
            mode: 'markers',
            x,
            y: result.y,
            name: '',
            marker: { 
                size: result.sizes.map(s => s + 8), 
                color: result.fillColors.map(c => c.replace(')', ', 0.15)').replace('rgb', 'rgba')), 
                symbol: 'circle', 
                line: { width: 0 }
            },
            yaxis: 'y',
            hoverinfo: 'skip',
            showlegend: false
        });
        
        // Main SMP markers with enhanced visibility
        traces.push({
            type: 'scattergl',
            mode: 'markers+text',
            x, y: result.y,
            hovertext: result.text,
            text: result.x.map(() => 'SM'),
            textposition: 'top center',
            textfont: {
                size: 12, 
                color: result.colors, 
                weight: 'bold',
                family: 'Inter, sans-serif'
            },
            marker: { 
                size: result.sizes.map(s => s + 2), 
                color: result.fillColors, 
                symbol: 'circle', 
                line: { color: result.colors, width: 2.5 }
            },
            name: 'Smart Money',
            yaxis: 'y',
            hovertemplate: '<b>Smart Money Print</b><br>%{hovertext}<br>Price: %{y:.2f}<extra></extra>'
        });
        
        return traces;
    }
};
