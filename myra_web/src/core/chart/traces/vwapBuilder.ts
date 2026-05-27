import { TraceBuilder } from './types';

export const vwapTraceBuilder: TraceBuilder<number[], any> = {
    id: 'vwap',

    buildTraces: (result, context) => {
        return [{
            type: 'scattergl', 
            mode: 'lines', 
            x: context.candleIndexes, 
            y: result, 
            name: 'AVWAP (Anchored)', 
            line: { color: '#888', width: 1.5, dash: 'dot' }, 
            yaxis: 'y', 
            hovertemplate: '%{y:.2f}'
        }];
    }
};
