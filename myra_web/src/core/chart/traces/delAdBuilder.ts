import { TraceBuilder } from './types';

export const delAdTraceBuilder: TraceBuilder<number[], any> = {
    id: 'delAd',

    buildTraces: (result, context) => {
        return [
            { type: 'scattergl', mode: 'lines', x: context.candleIndexes, y: result, name: 'Del. A/D', line: { color: '#ec4899', width: 1.5 }, yaxis: 'y6', hovertemplate: 'Val: %{y:.2s}', fill: 'tozeroy', fillcolor: 'rgba(236,72,153,0.1)' }
        ];
    }
};
