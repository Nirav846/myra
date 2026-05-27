import { TraceBuilder, TraceBuilderContext } from './types';
import { SMAConfig, smaIndicator } from '../../technical-analysis/indicators/sma';

export const smaTraceBuilder: TraceBuilder<number[], SMAConfig> = {
  id: 'sma',
  buildTraces: (result: number[], context: TraceBuilderContext, config?: SMAConfig) => {
    const cfg = { ...smaIndicator.defaults, ...config };
    
    return [
      {
        type: 'scattergl' as const,
        mode: 'lines' as const,
        x: context.candleIndexes,
        y: result,
        name: cfg.name || `SMA${cfg.period}`,
        line: { color: cfg.color, width: cfg.width },
        yaxis: cfg.yaxis,
        hovertemplate: '%{y:.2f}' as const
      }
    ];
  }
};
