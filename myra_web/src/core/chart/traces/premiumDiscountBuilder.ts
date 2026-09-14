import { TraceBuilder, TraceBuilderContext } from './types';
import { PremiumDiscountZone } from '../../technical-analysis/indicators/premiumDiscount';

export const premiumDiscountTraceBuilder: TraceBuilder<PremiumDiscountZone | null, any> = {
  id: 'premiumDiscount',
  buildTraces: () => [],
  buildShapes: (result: PremiumDiscountZone | null, context: TraceBuilderContext) => {
    const shapes: any[] = [];
    
    if (!result) return shapes;
    
    const { data } = context;
    const length = data.length;
    
    // Use the last 50 candles for display width
    const displayStart = Math.max(0, length - 50);
    const x0 = displayStart - 0.5;
    const x1 = length - 1 + 0.5;

    // Discount Zone (0%-50%): Green gradient
    shapes.push({
      type: 'rect',
      layer: 'below',
      xref: 'x',
      yref: 'y',
      x0,
      x1,
      y0: result.discountStart,
      y1: result.equilibrium,
      fillcolor: 'rgba(34, 197, 94, 0.12)',
      line: {
        color: 'rgba(34, 197, 94, 0.5)',
        width: 1,
        dash: 'dash',
      },
    });

    // Premium Zone (50%-100%): Red gradient
    shapes.push({
      type: 'rect',
      layer: 'below',
      xref: 'x',
      yref: 'y',
      x0,
      x1,
      y0: result.equilibrium,
      y1: result.premiumEnd,
      fillcolor: 'rgba(239, 68, 68, 0.12)',
      line: {
        color: 'rgba(239, 68, 68, 0.5)',
        width: 1,
        dash: 'dash',
      },
    });

    // Equilibrium line (50% level) - thicker
    shapes.push({
      type: 'line',
      layer: 'below',
      xref: 'x',
      yref: 'y',
      x0,
      x1,
      y0: result.equilibrium,
      y1: result.equilibrium,
      line: {
        color: 'rgba(251, 191, 36, 0.8)',
        width: 2,
        dash: 'solid',
      },
    });

    // Current price indicator
    const currentX = length - 1;
    shapes.push({
      type: 'circle',
      xref: 'x',
      yref: 'y',
      x0: currentX - 0.4,
      x1: currentX + 0.4,
      y0: result.currentPrice - 0.02,
      y1: result.currentPrice + 0.02,
      fillcolor: result.inPremium 
        ? 'rgba(239, 68, 68, 0.9)' 
        : result.inDiscount 
          ? 'rgba(34, 197, 94, 0.9)' 
          : 'rgba(251, 191, 36, 0.9)',
      line: { width: 2, color: '#fff' },
      opacity: 1,
    });

    // Zone labels
    const labelX = x0 + 1;
    
    // Premium label
    shapes.push({
      type: 'circle',
      xref: 'x',
      yref: 'y',
      x0: labelX - 0.5,
      x1: labelX + 0.5,
      y0: result.equilibrium * 1.005,
      y1: result.equilibrium * 1.015,
      fillcolor: 'rgba(239, 68, 68, 0.7)',
      line: { width: 0 },
      opacity: 0.9,
    });

    // Discount label
    shapes.push({
      type: 'circle',
      xref: 'x',
      yref: 'y',
      x0: labelX - 0.5,
      x1: labelX + 0.5,
      y0: result.equilibrium * 0.985,
      y1: result.equilibrium * 0.995,
      fillcolor: 'rgba(34, 197, 94, 0.7)',
      line: { width: 0 },
      opacity: 0.9,
    });

    return shapes;
  },
};
