import { TraceBuilder, TraceBuilderContext } from './types';
import { OrderBlockResult } from '../../technical-analysis/indicators/orderBlocks';

export const orderBlocksTraceBuilder: TraceBuilder<OrderBlockResult, any> = {
  id: 'orderBlocks',
  buildTraces: () => [],
  buildShapes: (result: OrderBlockResult, context: TraceBuilderContext) => {
    const shapes: any[] = [];
    const { dateToIndex } = context;

    // Process bullish order blocks
    result.bullishBlocks.forEach((block) => {
      const xIndex = block.index;
      const x0 = xIndex - 0.5;
      const x1 = xIndex + 1.5; // Show zone extending forward

      // Main order block zone with gradient effect
      const baseColor = 'rgba(34, 197, 94, 0.15)';
      const borderColor = 'rgba(34, 197, 94, 0.6)';
      const glowColor = 'rgba(34, 197, 94, 0.08)';

      // Bullish OB rectangle
      shapes.push({
        type: 'rect',
        layer: 'below',
        xref: 'x',
        yref: 'y',
        x0,
        x1,
        y0: block.low,
        y1: block.high,
        fillcolor: baseColor,
        line: {
          color: borderColor,
          width: 2,
          dash: block.mitigated ? 'dot' : 'solid',
        },
      });

      // Inner glow for unmitigated blocks
      if (!block.mitigated) {
        shapes.push({
          type: 'rect',
          layer: 'below',
          xref: 'x',
          yref: 'y',
          x0: x0 + 0.1,
          x1: x1 - 0.1,
          y0: block.low + (block.high - block.low) * 0.1,
          y1: block.high - (block.high - block.low) * 0.1,
          fillcolor: glowColor,
          line: { width: 0 },
        });

        // Label marker
        shapes.push({
          type: 'circle',
          xref: 'x',
          yref: 'y',
          x0: x0 + 0.2,
          x1: x0 + 0.6,
          y0: block.high + 0.01,
          y1: block.high + 0.03,
          fillcolor: '#22c55e',
          line: { width: 0 },
          opacity: 0.8,
        });
      } else {
        // Mitigated label
        shapes.push({
          type: 'circle',
          xref: 'x',
          yref: 'y',
          x0: x0 + 0.2,
          x1: x0 + 0.6,
          y0: block.low - 0.03,
          y1: block.low - 0.01,
          fillcolor: 'rgba(34, 197, 94, 0.4)',
          line: { width: 0 },
          opacity: 0.6,
        });
      }
    });

    // Process bearish order blocks
    result.bearishBlocks.forEach((block) => {
      const xIndex = block.index;
      const x0 = xIndex - 0.5;
      const x1 = xIndex + 1.5;

      const baseColor = 'rgba(239, 68, 68, 0.15)';
      const borderColor = 'rgba(239, 68, 68, 0.6)';
      const glowColor = 'rgba(239, 68, 68, 0.08)';

      // Bearish OB rectangle
      shapes.push({
        type: 'rect',
        layer: 'below',
        xref: 'x',
        yref: 'y',
        x0,
        x1,
        y0: block.low,
        y1: block.high,
        fillcolor: baseColor,
        line: {
          color: borderColor,
          width: 2,
          dash: block.mitigated ? 'dot' : 'solid',
        },
      });

      // Inner glow for unmitigated blocks
      if (!block.mitigated) {
        shapes.push({
          type: 'rect',
          layer: 'below',
          xref: 'x',
          yref: 'y',
          x0: x0 + 0.1,
          x1: x1 - 0.1,
          y0: block.low + (block.high - block.low) * 0.1,
          y1: block.high - (block.high - block.low) * 0.1,
          fillcolor: glowColor,
          line: { width: 0 },
        });

        // Label marker
        shapes.push({
          type: 'circle',
          xref: 'x',
          yref: 'y',
          x0: x0 + 0.2,
          x1: x0 + 0.6,
          y0: block.low - 0.03,
          y1: block.low - 0.01,
          fillcolor: '#ef4444',
          line: { width: 0 },
          opacity: 0.8,
        });
      } else {
        // Mitigated label
        shapes.push({
          type: 'circle',
          xref: 'x',
          yref: 'y',
          x0: x0 + 0.2,
          x1: x0 + 0.6,
          y0: block.high + 0.01,
          y1: block.high + 0.03,
          fillcolor: 'rgba(239, 68, 68, 0.4)',
          line: { width: 0 },
          opacity: 0.6,
        });
      }
    });

    return shapes;
  },
};
