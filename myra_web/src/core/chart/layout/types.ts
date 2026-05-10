import { TraceBuilderContext } from "../traces/types";

export interface LayoutBuilder<TConfig = any> {
  id: string;
  buildShapes?: (context: TraceBuilderContext, config?: TConfig) => any[];
  buildAnnotations?: (context: TraceBuilderContext, config?: TConfig) => any[];
}
