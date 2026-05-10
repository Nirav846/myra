import { Candle, IndicatorModule } from "../types";

export interface SMAConfig {
  period: number;
  source:
    | "close"
    | "open"
    | "high"
    | "low"
    | "volume"
    | "delivery"
    | "delivery_final"
    | "volume_final";
  color: string;
  width: number;
  name?: string;
  yaxis?: string;
}

export const calculateSMA = (
  data: Candle[],
  periodOrConfig: number | SMAConfig,
): number[] => {
  const config =
    typeof periodOrConfig === "number"
      ? { period: periodOrConfig, source: "close" as const }
      : periodOrConfig;

  const result: number[] = [];
  let sum = 0;
  for (let i = 0; i < data.length; i++) {
    const val = Number(data[i][config.source]) || 0;
    sum += val;
    if (i >= config.period) {
      const prevVal = Number(data[i - config.period][config.source]) || 0;
      sum -= prevVal;
      result.push(sum / config.period);
    } else {
      result.push(sum / (i + 1));
    }
  }
  return result;
};

export const smaIndicator: IndicatorModule<number[], SMAConfig> = {
  id: "sma",
  defaults: {
    period: 20,
    source: "close",
    color: "#eab308",
    width: 1,
    yaxis: "y",
  },
  calculate: calculateSMA,
};
