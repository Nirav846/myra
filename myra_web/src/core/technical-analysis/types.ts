import { TraceBuilderContext } from "../chart/traces/types";

export interface Candle {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
  volume_final?: number;
  delivery?: number;
  delivery_final?: number;
  delivery_pct?: number;
  [key: string]: any;
}

export interface IndicatorModule<TResult, TConfig> {
  id: string;
  calculate: (
    data: Candle[],
    config?: TConfig,
    context?: TraceBuilderContext,
  ) => TResult;
  defaults?: TConfig;
}
