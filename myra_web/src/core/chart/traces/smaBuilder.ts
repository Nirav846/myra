import { TraceBuilder, TraceBuilderContext } from './types';
import { SMAConfig, smaIndicator } from '../../technical-analysis/indicators/sma';

export const smaTraceBuilder: TraceBuilder<number[], SMAConfig> = {
  id: 'sma',
  buildTraces: (result: number[], context: TraceBuilderContext, config?: SMAConfig) => {
    const cfg = { ...smaIndicator.defaults, ...config };
    
    // Create gradient effect by adding a subtle glow line behind the main line
    const traces: any[] = [];
    
    // Add subtle glow/shadow effect (wider, semi-transparent line)
    traces.push({
      type: 'scattergl' as const,
      mode: 'lines' as const,
      x: context.candleIndexes,
      y: result,
      name: '',
      line: { 
        color: cfg.color + '40', // Add transparency (hex alpha)
        width: cfg.width + 4,
        opacity: 0.3
      },
      yaxis: cfg.yaxis,
      hoverinfo: 'skip' as const,
      showlegend: false
    });
    
    // Main SMA line with enhanced visibility
    traces.push({
      type: 'scattergl' as const,
      mode: 'lines' as const,
      x: context.candleIndexes,
      y: result,
      name: cfg.name || `SMA${cfg.period}`,
      line: { 
        color: cfg.color, 
        width: cfg.width,
        shape: 'spline' as const, // Smooth curves
        opacity: 0.9
      },
      yaxis: cfg.yaxis,
      hovertemplate: '<b>%{fullData.name}</b><br>Price: %{y:.2f}<extra></extra>' as const
    });
    
    return traces;
  }
};
