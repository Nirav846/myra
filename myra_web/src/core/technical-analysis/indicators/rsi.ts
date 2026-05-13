import { Candle, IndicatorModule } from '../types';

export interface RSIConfig {
  period: number;
  color: string;
  width: number;
  yaxis: string;
}

export const calculateRSI = (data: Candle[], periodOrConfig: number | RSIConfig): number[] => {
  const config = typeof periodOrConfig === 'number'
    ? { period: periodOrConfig }
    : periodOrConfig;
  
  const result: number[] = [];
  let gains = 0, losses = 0;
  for (let i = 0; i < data.length; i++) {
    if (i === 0) {
      result.push(NaN);
      continue;
    }
    const diff = data[i].close - data[i - 1].close;
    if (i < config.period) {
      if (diff >= 0) gains += diff;
      else losses -= diff;
      result.push(NaN);
    } else if (i === config.period) {
      if (diff >= 0) gains += diff;
      else losses -= diff;
      const rs = (gains/config.period) / (losses/config.period === 0 ? 1 : losses/config.period);
      result.push(100 - (100 / (1 + rs)));
    } else {
      const gain = diff >= 0 ? diff : 0;
      const loss = diff < 0 ? -diff : 0;
      gains = (gains * (config.period - 1) + gain) / config.period;
      losses = (losses * (config.period - 1) + loss) / config.period;
      const rs = gains / (losses === 0 ? 1 : losses);
      result.push(100 - (100 / (1 + rs)));
    }
  }
  return result;
};

export const rsiIndicator: IndicatorModule<number[], RSIConfig> = {
  id: 'rsi',
  defaults: {
    period: 14,
    color: '#8b5cf6',
    width: 1.5,
    yaxis: 'y3'
  },
  calculate: calculateRSI
};
