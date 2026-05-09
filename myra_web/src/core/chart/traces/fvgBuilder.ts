import { TraceBuilder, TraceBuilderContext } from './types';
import { FVG, FVGConfig } from '../../technical-analysis/indicators/fvg';

export const fvgTraceBuilder: TraceBuilder<FVG[], FVGConfig> = {
  id: 'fvg',
  buildTraces: () => [], // FVGs don't produce typical traces
  buildShapes: (result: FVG[], context: TraceBuilderContext, config?: FVGConfig) => {
    const shapes: any[] = [];
    
    result.forEach(fvg => {
      const isUnmitigated = !fvg.mitigated;
      
      if (isUnmitigated) {
        shapes.push({
            type: 'rect',
            layer: 'below',
            xref: 'x', yref: 'y',
            x0: fvg.startDate,
            x1: fvg.endDate,
            y0: fvg.bottom,
            y1: fvg.top,
            fillcolor: fvg.type === 'bullish' 
                ? 'rgba(34, 197, 94, 0.15)'
                : 'rgba(239, 68, 68, 0.15)',
            line: {
                color: fvg.type === 'bullish' ? 'rgba(34, 197, 94, 0.4)' : 'rgba(239, 68, 68, 0.4)',
                width: 1,
                dash: 'solid'
            }
        });
      } else if (config?.showMitigated && fvg.mitigated) {
        shapes.push({
            type: 'rect', layer: 'below', xref: 'x', yref: 'y',
            x0: fvg.startDate, x1: fvg.endDate, y0: fvg.bottom, y1: fvg.top,
            fillcolor: fvg.type === 'bullish' ? 'rgba(34, 197, 94, 0.05)' : 'rgba(239, 68, 68, 0.05)',
            line: { width: 0 }
        });
      }
    });

    return shapes;
  }
};
