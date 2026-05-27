import { LayoutBuilder } from './types';
import { LiqVoidsResult } from '../../technical-analysis/indicators/liqVoids';

export const liqVoidsLayoutBuilder: LayoutBuilder<LiqVoidsResult> = {
    id: 'liqVoids',

    buildShapes: (context, result) => {
        const shapes: any[] = [];
        if (!result || !result.voids) return shapes;
        const { dateToIndex } = context;
        
        for (const v of result.voids) {
            const x0 = dateToIndex.get(v.start);
            const x1 = dateToIndex.get(v.end);
            if (x0 === undefined || x1 === undefined) continue;
            shapes.push({
                type: 'rect',
                xref: 'x', x0: x0 - 0.5, x1: x1 + 0.5,
                yref: 'paper', y0: 0, y1: 1,
                fillcolor: 'rgba(236, 72, 153, 0.25)',
                line: { width: 0 },
                layer: 'below'
            });
        }
        
        return shapes;
    }
};
