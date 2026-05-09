import { TraceBuilder } from './types';

export const niftyOutTraceBuilder: TraceBuilder<number[], any> = {
    id: 'niftyOut',
    buildTraces: (result, context) => {
        const dates = context.data.map(d => d.date);
        return [{
            type: 'scattergl',
            mode: 'lines',
            x: dates,
            y: result,
            name: 'Nifty Outperf.',
            line: { color: '#a855f7', width: 1.5 },
            yaxis: 'y4',
            opacity: 0.8,
            fill: 'tozeroy',
            fillcolor: 'rgba(168, 85, 247, 0.1)',
            hovertemplate: '%{y:.2f}%'
        }];
    }
};
