import { Candle, IndicatorModule } from "../types";

export interface ATRConfig {
  period: number;
}

export const calculateATR = (
  data: Candle[],
  periodOrConfig: number | ATRConfig,
): number[] => {
  const config =
    typeof periodOrConfig === "number"
      ? { period: periodOrConfig }
      : periodOrConfig;

  const result: number[] = [];
  let trSum = 0;
  for (let i = 0; i < data.length; i++) {
    if (i === 0) {
      result.push(NaN);
      continue;
    }
    const high = data[i].high;
    const low = data[i].low;
    const prevClose = data[i - 1].close;
    const tr = Math.max(
      high - low,
      Math.abs(high - prevClose),
      Math.abs(low - prevClose),
    );

    if (i < config.period) {
      trSum += tr;
      result.push(NaN);
    } else if (i === config.period) {
      trSum += tr;
      result.push(trSum / config.period);
    } else {
      const prevAtr = result[i - 1];
      result.push((prevAtr * (config.period - 1) + tr) / config.period);
    }
  }
  return result;
};

export const atrIndicator: IndicatorModule<number[], ATRConfig> = {
  id: "atr",
  defaults: {
    period: 14,
  },
  calculate: calculateATR,
};
