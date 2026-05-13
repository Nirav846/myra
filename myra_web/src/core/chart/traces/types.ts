import { Candle } from '../../technical-analysis/types';
import { NormalizedViewport } from '../../../store/chartStore';

export interface TraceBuilderContext {
  data: Candle[];
  viewport: NormalizedViewport | null;
}

export interface TraceBuilder<TResult, TConfig> {
  id: string;
  buildTraces: (result: TResult, context: TraceBuilderContext, config?: TConfig) => any[];
  buildShapes?: (result: TResult, context: TraceBuilderContext, config?: TConfig) => any[];
}
