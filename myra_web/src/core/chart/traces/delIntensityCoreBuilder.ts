import { TraceBuilder } from './types';
import { DelIntensityCoreResult } from '../../technical-analysis/indicators/delIntensityCore';

export const delIntensityCoreTraceBuilder: TraceBuilder<DelIntensityCoreResult, any> = {
    id: 'delIntensityCore',

    buildTraces: (result) => {
        const traces: any = [];
        if (result.instX.length > 0) {
            traces.push({
                type: 'scattergl',
                mode: 'lines',
                x: result.instX,
                y: result.instY,
                line: { color: '#00f2ff', width: 3 }, // Bright Cyan
                name: 'Inst. Accumulation Core',
                yaxis: 'y',
                hoverinfo: 'skip'
            });
        }
        if (result.divX.length > 0) {
            traces.push({
                 type: 'scattergl',
                 mode: 'lines',
                 x: result.divX,
                 y: result.divY,
                 line: { color: '#FF9800', width: 3 }, // Bright Gold
                 name: 'Divergence Core',
                 yaxis: 'y',
                 hoverinfo: 'skip'
            });
        }
        if (result.retX.length > 0) {
            traces.push({
                 type: 'scattergl',
                 mode: 'lines',
                 x: result.retX,
                 y: result.retY,
                 line: { color: '#9CA3AF', width: 1.5, dash: 'dot' }, // Gray
                 name: 'Retail Core',
                 yaxis: 'y',
                 hoverinfo: 'skip'
            });
        }
        return traces;
    }
};
