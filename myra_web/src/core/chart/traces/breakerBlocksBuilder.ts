import { TraceBuilder, TraceBuilderContext } from './types';
import { BreakerBlockResult } from '../../technical-analysis/indicators/breakerBlocks';

export const breakerBlocksTraceBuilder: TraceBuilder<BreakerBlockResult, any> = {
  id: 'breakerBlocks',
  buildTraces: () => [],
  buildShapes: (result: BreakerBlockResult, context: TraceBuilderContext) => {
    const shapes: any[] = [];
    const { dateToIndex } = context;

    // Process breaker blocks
    result.breakerBlocks.forEach((block) => {
      const x0 = block.startIndex - 0.5;
      const x1 = block.endIndex + 0.5;

      if (block.type === 'bullish') {
        // Bullish breaker block zone
        const baseColor = 'rgba(59, 130, 246, 0.2)'; // Blue with opacity
        const borderColor = 'rgba(59, 130, 246, 0.7)';
        const glowColor = 'rgba(59, 130, 246, 0.1)';

        // Main breaker block rectangle
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
            dash: block.isMitigated ? 'dot' : 'solid',
          },
        });

        // Inner glow for unmitigated blocks
        if (!block.isMitigated) {
          shapes.push({
            type: 'rect',
            layer: 'below',
            xref: 'x',
            yref: 'y',
            x0: x0 + 0.2,
            x1: x1 - 0.2,
            y0: block.low + (block.high - block.low) * 0.1,
            y1: block.high - (block.high - block.low) * 0.1,
            fillcolor: glowColor,
            line: { width: 0 },
          });
        }

        // Liquidity sweep marker
        if (block.sweptLiquidity) {
          shapes.push({
            type: 'path',
            layer: 'above',
            xref: 'x',
            yref: 'y',
            path: `M ${block.endIndex} ${block.high} L ${block.endIndex - 0.3} ${block.high + 0.02 * (block.high - block.low)} L ${block.endIndex + 0.3} ${block.high + 0.02 * (block.high - block.low)} Z`,
            fillcolor: borderColor,
            line: { color: borderColor, width: 1 },
          });
        }

        // Confidence label
        if (block.confidence >= 75) {
          shapes.push({
            type: 'circle',
            layer: 'above',
            xref: 'x',
            yref: 'y',
            x0: block.endIndex + 0.3,
            y0: block.high,
            x1: block.endIndex + 0.5,
            y1: block.high + (block.high - block.low) * 0.05,
            fillcolor: 'rgba(34, 197, 94, 0.8)',
            line: { width: 0 },
          });
        }
      } else {
        // Bearish breaker block zone
        const baseColor = 'rgba(244, 63, 94, 0.2)'; // Red with opacity
        const borderColor = 'rgba(244, 63, 94, 0.7)';
        const glowColor = 'rgba(244, 63, 94, 0.1)';

        // Main breaker block rectangle
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
            dash: block.isMitigated ? 'dot' : 'solid',
          },
        });

        // Inner glow for unmitigated blocks
        if (!block.isMitigated) {
          shapes.push({
            type: 'rect',
            layer: 'below',
            xref: 'x',
            yref: 'y',
            x0: x0 + 0.2,
            x1: x1 - 0.2,
            y0: block.low + (block.high - block.low) * 0.1,
            y1: block.high - (block.high - block.low) * 0.1,
            fillcolor: glowColor,
            line: { width: 0 },
          });
        }

        // Liquidity sweep marker
        if (block.sweptLiquidity) {
          shapes.push({
            type: 'path',
            layer: 'above',
            xref: 'x',
            yref: 'y',
            path: `M ${block.endIndex} ${block.low} L ${block.endIndex - 0.3} ${block.low - 0.02 * (block.high - block.low)} L ${block.endIndex + 0.3} ${block.low - 0.02 * (block.high - block.low)} Z`,
            fillcolor: borderColor,
            line: { color: borderColor, width: 1 },
          });
        }

        // Confidence label
        if (block.confidence >= 75) {
          shapes.push({
            type: 'circle',
            layer: 'above',
            xref: 'x',
            yref: 'y',
            x0: block.endIndex + 0.3,
            y0: block.low,
            x1: block.endIndex + 0.5,
            y1: block.low - (block.high - block.low) * 0.05,
            fillcolor: 'rgba(244, 63, 94, 0.8)',
            line: { width: 0 },
          });
        }
      }
    });

    return shapes;
  },
};
