import { TraceBuilder, TraceBuilderContext } from './types';
import { RSIConfig, rsiIndicator } from '../../technical-analysis/indicators/rsi';

export const rsiTraceBuilder: TraceBuilder<number[], RSIConfig> = {
  id: 'rsi',
  buildTraces: (result: number[], context: TraceBuilderContext, config?: RSIConfig) => {
    const cfg = { ...rsiIndicator.defaults, ...config };
    const dates = context.data.map(d => d.date);
    
    return [
      {
        type: 'scattergl' as const,
        mode: 'lines' as const,
        x: dates,
        y: result,
        name: `RSI(${cfg.period})`,
        line: { color: cfg.color, width: cfg.width },
        yaxis: cfg.yaxis,
        hovertemplate: '%{y:.1f}' as const
      }
    ];
  }
};
