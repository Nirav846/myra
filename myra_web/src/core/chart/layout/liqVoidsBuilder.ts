import { LayoutBuilder } from './types';
import { LiqVoidsResult } from '../../technical-analysis/indicators/liqVoids';

export const liqVoidsLayoutBuilder: LayoutBuilder<LiqVoidsResult> = {
    id: 'liqVoids',

    buildShapes: (context, result) => {
        const shapes: any[] = [];
        if (!result || !result.voids) return shapes;
        
        for (const v of result.voids) {
            shapes.push({
                type: 'rect',
                xref: 'x', x0: v.start, x1: v.end,
                yref: 'paper', y0: 0, y1: 1,
                fillcolor: 'rgba(236, 72, 153, 0.25)', // Soft pink vertical band with better visibility
                line: { width: 0 },
                layer: 'below'
            });
        }
        
        return shapes;
    }
};
