import { TraceBuilder, TraceBuilderContext } from './types';
import { EqualHighLowResult } from '../../technical-analysis/indicators/equalHighsLows';

export const equalHighsLowsTraceBuilder: TraceBuilder<EqualHighLowResult, any> = {
  id: 'equalHighsLows',
  buildTraces: () => [],
  buildShapes: (result: EqualHighLowResult, context: TraceBuilderContext) => {
    const shapes: any[] = [];
    const { data, dateToIndex } = context;

    // Process Equal Highs (EQH)
    result.equalHighs.forEach((eqh) => {
      const priceLevel = eqh.priceLevel;
      
      // Find the x-axis range to draw the line across
      const firstIndex = Math.min(...eqh.indices);
      const lastIndex = Math.max(...eqh.indices);
      
      // Extend the line a bit on both sides for better visibility
      const x0 = Math.max(0, firstIndex - 2) - 0.5;
      const x1 = Math.min(data.length - 1, lastIndex + 2) + 0.5;

      // Main EQH line with gradient effect
      const baseColor = 'rgba(239, 68, 68, 0.2)';
      const lineColor = 'rgba(239, 68, 68, 0.7)';

      // Horizontal line at EQH level
      shapes.push({
        type: 'line',
        layer: 'below',
        xref: 'x',
        yref: 'y',
        x0,
        x1,
        y0: priceLevel,
        y1: priceLevel,
        line: {
          color: lineColor,
          width: 2,
          dash: 'dash',
        },
      });

      // Filled zone around EQH level
      shapes.push({
        type: 'rect',
        layer: 'below',
        xref: 'x',
        yref: 'y',
        x0,
        x1,
        y0: priceLevel * 0.998,
        y1: priceLevel * 1.002,
        fillcolor: baseColor,
        line: { width: 0 },
      });

      // Markers at each touch point
      eqh.indices.forEach((index) => {
        if (index >= 0 && index < data.length) {
          const candle = data[index];
          shapes.push({
            type: 'circle',
            xref: 'x',
            yref: 'y',
            x0: index - 0.25,
            x1: index + 0.25,
            y0: candle.high - 0.02,
            y1: candle.high + 0.02,
            fillcolor: '#ef4444',
            line: { width: 1, color: '#fff' },
            opacity: 0.9,
          });
        }
      });

      // Label showing number of touches
      const labelX = x1 - 1;
      shapes.push({
        type: 'circle',
        xref: 'x',
        yref: 'y',
        x0: labelX - 0.4,
        x1: labelX + 0.4,
        y0: priceLevel * 1.003,
        y1: priceLevel * 1.008,
        fillcolor: 'rgba(239, 68, 68, 0.8)',
        line: { width: 0 },
        opacity: 0.9,
      });
    });

    // Process Equal Lows (EQL)
    result.equalLows.forEach((eql) => {
      const priceLevel = eql.priceLevel;
      
      // Find the x-axis range to draw the line across
      const firstIndex = Math.min(...eql.indices);
      const lastIndex = Math.max(...eql.indices);
      
      // Extend the line a bit on both sides
      const x0 = Math.max(0, firstIndex - 2) - 0.5;
      const x1 = Math.min(data.length - 1, lastIndex + 2) + 0.5;

      // Main EQL line with gradient effect
      const baseColor = 'rgba(34, 197, 94, 0.2)';
      const lineColor = 'rgba(34, 197, 94, 0.7)';

      // Horizontal line at EQL level
      shapes.push({
        type: 'line',
        layer: 'below',
        xref: 'x',
        yref: 'y',
        x0,
        x1,
        y0: priceLevel,
        y1: priceLevel,
        line: {
          color: lineColor,
          width: 2,
          dash: 'dash',
        },
      });

      // Filled zone around EQL level
      shapes.push({
        type: 'rect',
        layer: 'below',
        xref: 'x',
        yref: 'y',
        x0,
        x1,
        y0: priceLevel * 0.998,
        y1: priceLevel * 1.002,
        fillcolor: baseColor,
        line: { width: 0 },
      });

      // Markers at each touch point
      eql.indices.forEach((index) => {
        if (index >= 0 && index < data.length) {
          const candle = data[index];
          shapes.push({
            type: 'circle',
            xref: 'x',
            yref: 'y',
            x0: index - 0.25,
            x1: index + 0.25,
            y0: candle.low - 0.02,
            y1: candle.low + 0.02,
            fillcolor: '#22c55e',
            line: { width: 1, color: '#fff' },
            opacity: 0.9,
          });
        }
      });

      // Label showing number of touches
      const labelX = x1 - 1;
      shapes.push({
        type: 'circle',
        xref: 'x',
        yref: 'y',
        x0: labelX - 0.4,
        x1: labelX + 0.4,
        y0: priceLevel * 0.992,
        y1: priceLevel * 0.997,
        fillcolor: 'rgba(34, 197, 94, 0.8)',
        line: { width: 0 },
        opacity: 0.9,
      });
    });

    return shapes;
  },
};
