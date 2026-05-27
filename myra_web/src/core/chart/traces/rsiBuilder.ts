import { TraceBuilder, TraceBuilderContext } from './types';
import { RSIConfig, rsiIndicator } from '../../technical-analysis/indicators/rsi';

export const rsiTraceBuilder: TraceBuilder<number[], RSIConfig> = {
  id: 'rsi',
  buildTraces: (result: number[], context: TraceBuilderContext, config?: RSIConfig) => {
    const cfg = { ...rsiIndicator.defaults, ...config };
    
    return [
      {
        type: 'scattergl' as const,
        mode: 'lines' as const,
        x: context.candleIndexes,
        y: result,
        name: `RSI(${cfg.period})`,
        line: { color: cfg.color, width: cfg.width },
        yaxis: cfg.yaxis,
        hovertemplate: '%{y:.1f}' as const
      }
    ];
  }
};
