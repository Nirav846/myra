import { TraceBuilder } from './types';
import { SwingsResult } from '../../technical-analysis/indicators/swings';

export const swingsTraceBuilder: TraceBuilder<SwingsResult, any> = {
    id: 'swings',

    buildTraces: (result, context) => {
        const { dateToIndex } = context;
        const traces = [];
        
        // Enhanced Swing Highs with better markers, glow effects, and labels
        if (result.highs.dates.length > 0) {
            const x = result.highs.dates.map(d => dateToIndex.get(d) ?? 0);
            traces.push({
                type: 'scattergl', 
                mode: 'markers+text', 
                x, 
                y: result.highs.values, 
                name: 'Swing High', 
                marker: { 
                    symbol: 'triangle-down', 
                    size: 12, 
                    color: '#ef4444',
                    line: { width: 2, color: '#ffffff' },
                    opacity: 0.9
                }, 
                text: result.highs.values.map(v => v?.toFixed(2)), 
                textposition: 'top center', 
                textfont: {
                    size: 11, 
                    color: '#ef4444',
                    family: 'Inter, sans-serif',
                    weight: 'bold'
                }, 
                yaxis: 'y',
                hovertemplate: '<b>Swing High</b><br>Price: %{y:.2f}<br>Date: %{x}<extra></extra>'
            });
            
            // Add subtle glow effect behind swing highs
            traces.push({
                type: 'scattergl',
                mode: 'markers',
                x,
                y: result.highs.values,
                name: '',
                marker: {
                    symbol: 'triangle-down',
                    size: 18,
                    color: 'rgba(239, 68, 68, 0.15)',
                    line: { width: 0 }
                },
                yaxis: 'y',
                hoverinfo: 'skip'
            });
        }
        
        // Enhanced Swing Lows with better markers, glow effects, and labels
        if (result.lows.dates.length > 0) {
            const x = result.lows.dates.map(d => dateToIndex.get(d) ?? 0);
            traces.push({
                type: 'scattergl', 
                mode: 'markers+text', 
                x, 
                y: result.lows.values, 
                name: 'Swing Low', 
                marker: { 
                    symbol: 'triangle-up', 
                    size: 12, 
                    color: '#22c55e',
                    line: { width: 2, color: '#ffffff' },
                    opacity: 0.9
                }, 
                text: result.lows.values.map(v => v?.toFixed(2)), 
                textposition: 'bottom center', 
                textfont: {
                    size: 11, 
                    color: '#22c55e',
                    family: 'Inter, sans-serif',
                    weight: 'bold'
                }, 
                yaxis: 'y',
                hovertemplate: '<b>Swing Low</b><br>Price: %{y:.2f}<br>Date: %{x}<extra></extra>'
            });
            
            // Add subtle glow effect behind swing lows
            traces.push({
                type: 'scattergl',
                mode: 'markers',
                x,
                y: result.lows.values,
                name: '',
                marker: {
                    symbol: 'triangle-up',
                    size: 18,
                    color: 'rgba(34, 197, 94, 0.15)',
                    line: { width: 0 }
                },
                yaxis: 'y',
                hoverinfo: 'skip'
            });
        }
        
        return traces;
    }
};
