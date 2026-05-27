import { TraceBuilder } from './types';
import { DelIntensityCoreResult } from '../../technical-analysis/indicators/delIntensityCore';

export const delIntensityCoreTraceBuilder: TraceBuilder<DelIntensityCoreResult, any> = {
    id: 'delIntensityCore',

    buildTraces: (result, context) => {
        const { dateToIndex } = context;
        const traces: any = [];
        if (result.instX.length > 0) {
            traces.push({
                type: 'scattergl',
                mode: 'lines',
                x: result.instX.map(d => dateToIndex.get(d) ?? 0),
                y: result.instY,
                line: { color: '#00f2ff', width: 3 },
                name: 'Inst. Accumulation Core',
                yaxis: 'y',
                hoverinfo: 'skip'
            });
        }
        if (result.divX.length > 0) {
            traces.push({
                 type: 'scattergl',
                 mode: 'lines',
                 x: result.divX.map(d => dateToIndex.get(d) ?? 0),
                 y: result.divY,
                 line: { color: '#FF9800', width: 3 },
                 name: 'Divergence Core',
                 yaxis: 'y',
                 hoverinfo: 'skip'
            });
        }
        if (result.retX.length > 0) {
            traces.push({
                 type: 'scattergl',
                 mode: 'lines',
                 x: result.retX.map(d => dateToIndex.get(d) ?? 0),
                 y: result.retY,
                 line: { color: '#9CA3AF', width: 1.5, dash: 'dot' },
                 name: 'Retail Core',
                 yaxis: 'y',
                 hoverinfo: 'skip'
            });
        }
        return traces;
    }
};
