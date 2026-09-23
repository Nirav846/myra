import { LayoutBuilder } from './types';
import { LiqVoidsResult } from '../../technical-analysis/indicators/liqVoids';

export interface EnhancedLiqVoidsResult extends LiqVoidsResult {
    voids: Array<{
        start: string;
        end: string;
        type?: 'bullish' | 'bearish';
        size?: number;
    }>;
}

export const liqVoidsLayoutBuilder: LayoutBuilder<EnhancedLiqVoidsResult> = {
    id: 'liqVoids',

    buildShapes: (context, result) => {
        const shapes: any[] = [];
        const annotations: any[] = [];
        
        if (!result || !result.voids) return shapes;
        
        const { dateToIndex, data } = context;

        for (let i = 0; i < result.voids.length; i++) {
            const v = result.voids[i];
            const x0 = dateToIndex.get(v.start);
            const x1 = dateToIndex.get(v.end);
            
            if (x0 === undefined || x1 === undefined) continue;

            // Determine void type based on price action
            const startIndex = dateToIndex.get(v.start);
            const endIndex = dateToIndex.get(v.end);
            let voidType: 'bullish' | 'bearish' = 'bullish';
            let voidSize = 0;
            
            if (startIndex !== undefined && endIndex !== undefined && data) {
                const startCandle = data[startIndex];
                const endCandle = data[endIndex];
                
                if (startCandle && endCandle) {
                    voidSize = Math.abs(endCandle.close - startCandle.close);
                    voidType = endCandle.close > startCandle.close ? 'bullish' : 'bearish';
                }
            }

            // Color scheme based on void type
            const isBullish = voidType === 'bullish';
            const baseColor = isBullish 
                ? 'rgba(34, 197, 94, 0.2)'
                : 'rgba(239, 68, 68, 0.2)';
            const borderColor = isBullish
                ? 'rgba(34, 197, 94, 0.5)'
                : 'rgba(239, 68, 68, 0.5)';
            const labelColor = isBullish ? '#22c55e' : '#ef4444';

            // Main void zone with enhanced opacity
            shapes.push({
                type: 'rect',
                xref: 'x',
                x0: x0 - 0.5,
                x1: x1 + 0.5,
                yref: 'paper',
                y0: 0,
                y1: 1,
                fillcolor: baseColor,
                line: {
                    color: borderColor,
                    width: 2,
                    dash: 'dot'
                },
                layer: 'below'
            });

            // Add subtle inner gradient effect (lighter center)
            shapes.push({
                type: 'rect',
                xref: 'x',
                x0: x0 - 0.3,
                x1: x1 + 0.3,
                yref: 'paper',
                y0: 0.1,
                y1: 0.9,
                fillcolor: isBullish
                    ? 'rgba(34, 197, 94, 0.08)'
                    : 'rgba(239, 68, 68, 0.08)',
                line: { width: 0 },
                layer: 'below'
            });

            // Add label showing void type and size
            const midX = (x0 + x1) / 2;
            const sizeLabel = voidSize > 0 ? ` (${voidSize.toFixed(2)})` : '';
            
            annotations.push({
                x: midX,
                y: 0.05,
                xref: 'x',
                yref: 'paper',
                text: `${isBullish ? '📈' : '📉'} Void${sizeLabel}`,
                showarrow: false,
                font: {
                    size: 10,
                    color: labelColor,
                    family: 'Inter, sans-serif',
                    weight: 'bold'
                },
                bgcolor: 'rgba(0, 0, 0, 0.6)',
                bordercolor: labelColor,
                borderwidth: 1,
                borderpad: 4,
                opacity: 0.9
            });

            // Add boundary markers at start and end
            shapes.push({
                type: 'circle',
                xref: 'x',
                x0: x0 - 0.2,
                x1: x0 + 0.2,
                yref: 'paper',
                y0: 0.95,
                y1: 1.0,
                fillcolor: labelColor,
                line: { width: 0 },
                opacity: 0.8
            });

            shapes.push({
                type: 'circle',
                xref: 'x',
                x0: x1 - 0.2,
                x1: x1 + 0.2,
                yref: 'paper',
                y0: 0.95,
                y1: 1.0,
                fillcolor: labelColor,
                line: { width: 0 },
                opacity: 0.8
            });
        }

        (shapes as any).annotations = annotations;

        return shapes;
    }
};
