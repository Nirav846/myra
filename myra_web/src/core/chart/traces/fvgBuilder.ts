import { TraceBuilder, TraceBuilderContext } from './types';
import { FVG, FVGConfig } from '../../technical-analysis/indicators/fvg';

export const fvgTraceBuilder: TraceBuilder<FVG[], FVGConfig> = {
  id: 'fvg',
  buildTraces: () => [],
  buildShapes: (result: FVG[], context: TraceBuilderContext, config?: FVGConfig) => {
    const shapes: any[] = [];
    const { dateToIndex } = context;
    
    result.forEach(fvg => {
      const isUnmitigated = !fvg.mitigated;
      const x0i = dateToIndex.get(fvg.startDate);
      const x1i = dateToIndex.get(fvg.endDate);
      if (x0i === undefined || x1i === undefined) return;
      
      const x0 = x0i - 0.5;
      const x1 = x1i + 0.5;
      
      if (isUnmitigated) {
        shapes.push({
            type: 'rect',
            layer: 'below',
            xref: 'x', yref: 'y',
            x0, x1,
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
            x0, x1, y0: fvg.bottom, y1: fvg.top,
            fillcolor: fvg.type === 'bullish' ? 'rgba(34, 197, 94, 0.05)' : 'rgba(239, 68, 68, 0.05)',
            line: { width: 0 }
        });
      }
    });

    return shapes;
  }
};
