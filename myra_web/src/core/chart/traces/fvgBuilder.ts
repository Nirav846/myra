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
        // Enhanced unmitigated FVG with gradient-like effect using multiple layers
        const baseColor = fvg.type === 'bullish' 
          ? 'rgba(34, 197, 94, 0.2)'
          : 'rgba(239, 68, 68, 0.2)';
        const borderColor = fvg.type === 'bullish' 
          ? 'rgba(34, 197, 94, 0.6)'
          : 'rgba(239, 68, 68, 0.6)';
        
        // Main FVG zone with enhanced opacity
        shapes.push({
            type: 'rect',
            layer: 'below',
            xref: 'x', yref: 'y',
            x0, x1,
            y0: fvg.bottom,
            y1: fvg.top,
            fillcolor: baseColor,
            line: {
                color: borderColor,
                width: 2,
                dash: 'solid'
            }
        });
        
        // Add subtle inner glow effect
        shapes.push({
            type: 'rect',
            layer: 'below',
            xref: 'x', yref: 'y',
            x0: x0 + 0.1,
            x1: x1 - 0.1,
            y0: fvg.bottom + (fvg.top - fvg.bottom) * 0.1,
            y1: fvg.top - (fvg.top - fvg.bottom) * 0.1,
            fillcolor: fvg.type === 'bullish' 
                ? 'rgba(34, 197, 94, 0.08)'
                : 'rgba(239, 68, 68, 0.08)',
            line: { width: 0 }
        });
        
        // Add label for unmitigated FVG
        const midX = (x0 + x1) / 2;
        const midY = (fvg.bottom + fvg.top) / 2;
        shapes.push({
            type: 'circle',
            xref: 'x', yref: 'y',
            x0: midX - 0.3, x1: midX + 0.3,
            y0: midY - 0.02, y1: midY + 0.02,
            fillcolor: fvg.type === 'bullish' ? '#22c55e' : '#ef4444',
            line: { width: 0 },
            opacity: 0.8
        });
      } else if (config?.showMitigated && fvg.mitigated) {
        // Enhanced mitigated FVG with dashed border
        shapes.push({
            type: 'rect', 
            layer: 'below', 
            xref: 'x', yref: 'y',
            x0, x1, 
            y0: fvg.bottom, 
            y1: fvg.top,
            fillcolor: fvg.type === 'bullish' 
                ? 'rgba(34, 197, 94, 0.08)' 
                : 'rgba(239, 68, 68, 0.08)',
            line: { 
                color: fvg.type === 'bullish' 
                    ? 'rgba(34, 197, 94, 0.3)' 
                    : 'rgba(239, 68, 68, 0.3)',
                width: 1,
                dash: 'dot'
            }
        });
      }
    });

    return shapes;
  }
};
