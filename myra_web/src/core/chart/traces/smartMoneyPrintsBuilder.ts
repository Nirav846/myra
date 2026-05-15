import { TraceBuilder } from './types';
import { SmartMoneyPrintsResult } from '../../technical-analysis/indicators/smartMoneyPrints';

export const smartMoneyPrintsTraceBuilder: TraceBuilder<SmartMoneyPrintsResult, any> = {
    id: 'smartMoneyPrints',

    buildTraces: (result) => {
        if (result.x.length === 0) return [];
        return [
            {
                type: 'scattergl',
                mode: 'markers+text',
                x: result.x, y: result.y,
                hovertext: result.text,
                text: result.x.map(() => 'SM'),
                textposition: 'top center',
                textfont: {size: 10, color: result.colors, weight: 'bold'},
                marker: { size: result.sizes, color: result.fillColors, symbol: 'circle', line: {color: result.colors, width: 1.5} },
                name: 'Smart Money',
                yaxis: 'y',
                hoverinfo: 'text'
            }
        ];
    }
};
