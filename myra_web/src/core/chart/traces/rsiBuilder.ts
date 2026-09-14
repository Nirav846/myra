import { TraceBuilder, TraceBuilderContext } from './types';
import { RSIConfig, rsiIndicator } from '../../technical-analysis/indicators/rsi';

export const rsiTraceBuilder: TraceBuilder<number[], RSIConfig> = {
  id: 'rsi',
  buildTraces: (result: number[], context: TraceBuilderContext, config?: RSIConfig) => {
    const cfg = { ...rsiIndicator.defaults, ...config };
    
    const traces: any[] = [];
    
    // Add overbought zone background (70-100)
    traces.push({
      type: 'scattergl' as const,
      mode: 'lines' as const,
      x: [...context.candleIndexes, ...context.candleIndexes.slice().reverse()],
      y: [...Array(context.candleIndexes.length).fill(70), ...Array(context.candleIndexes.length).fill(100)],
      name: '',
      fill: 'toself' as const,
      fillcolor: 'rgba(239, 68, 68, 0.1)',
      line: { width: 0 },
      yaxis: cfg.yaxis,
      hoverinfo: 'skip' as const,
      showlegend: false
    });
    
    // Add oversold zone background (0-30)
    traces.push({
      type: 'scattergl' as const,
      mode: 'lines' as const,
      x: [...context.candleIndexes, ...context.candleIndexes.slice().reverse()],
      y: [...Array(context.candleIndexes.length).fill(0), ...Array(context.candleIndexes.length).fill(30)],
      name: '',
      fill: 'toself' as const,
      fillcolor: 'rgba(34, 197, 94, 0.1)',
      line: { width: 0 },
      yaxis: cfg.yaxis,
      hoverinfo: 'skip' as const,
      showlegend: false
    });
    
    // Add overbought line (70)
    traces.push({
      type: 'scattergl' as const,
      mode: 'lines' as const,
      x: context.candleIndexes,
      y: Array(context.candleIndexes.length).fill(70),
      name: '',
      line: { 
        color: 'rgba(239, 68, 68, 0.4)',
        width: 1,
        dash: 'dash' as const
      },
      yaxis: cfg.yaxis,
      hoverinfo: 'skip' as const,
      showlegend: false
    });
    
    // Add oversold line (30)
    traces.push({
      type: 'scattergl' as const,
      mode: 'lines' as const,
      x: context.candleIndexes,
      y: Array(context.candleIndexes.length).fill(30),
      name: '',
      line: { 
        color: 'rgba(34, 197, 94, 0.4)',
        width: 1,
        dash: 'dash' as const
      },
      yaxis: cfg.yaxis,
      hoverinfo: 'skip' as const,
      showlegend: false
    });
    
    // Add centerline (50)
    traces.push({
      type: 'scattergl' as const,
      mode: 'lines' as const,
      x: context.candleIndexes,
      y: Array(context.candleIndexes.length).fill(50),
      name: '',
      line: { 
        color: 'rgba(156, 163, 175, 0.4)',
        width: 1,
        dash: 'dot' as const
      },
      yaxis: cfg.yaxis,
      hoverinfo: 'skip' as const,
      showlegend: false
    });
    
    // Add color-changing RSI line based on overbought/oversold status
    const rsiValues = result.map(v => v ?? 50);
    const colors = rsiValues.map(v => {
      if (v >= 70) return 'rgba(239, 68, 68, 0.9)'; // Overbought - red
      if (v <= 30) return 'rgba(34, 197, 94, 0.9)'; // Oversold - green
      return cfg.color + 'CC'; // Neutral - configured color with slight transparency
    });
    
    // Main RSI line with enhanced visibility
    traces.push({
      type: 'scattergl' as const,
      mode: 'lines' as const,
      x: context.candleIndexes,
      y: rsiValues,
      name: `RSI(${cfg.period})`,
      line: { 
        color: cfg.color,
        width: cfg.width + 1,
        shape: 'spline' as const
      },
      yaxis: cfg.yaxis,
      hovertemplate: '<b>RSI(%{fullData.name})</b><br>Value: %{y:.1f}<extra></extra>' as const
    });
    
    return traces;
  }
};
