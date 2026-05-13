export const calculateSMA = (data: any[], period: number) => {
  const result: number[] = [];
  let sum = 0;
  for (let i = 0; i < data.length; i++) {
    sum += data[i].close;
    if (i >= period) {
      sum -= data[i - period].close;
      result.push(sum / period);
    } else {
      result.push(sum / (i + 1));
    }
  }
  return result;
};

export const calculateRSI = (data: any[], period: number) => {
  const result: number[] = [];
  let gains = 0, losses = 0;
  for (let i = 0; i < data.length; i++) {
    if (i === 0) {
      result.push(NaN);
      continue;
    }
    const diff = data[i].close - data[i - 1].close;
    if (i < period) {
      if (diff >= 0) gains += diff;
      else losses -= diff;
      result.push(NaN);
    } else if (i === period) {
      if (diff >= 0) gains += diff;
      else losses -= diff;
      const rs = (gains/period) / (losses/period === 0 ? 1 : losses/period);
      result.push(100 - (100 / (1 + rs)));
    } else {
      const gain = diff >= 0 ? diff : 0;
      const loss = diff < 0 ? -diff : 0;
      gains = (gains * (period - 1) + gain) / period;
      losses = (losses * (period - 1) + loss) / period;
      const rs = gains / (losses === 0 ? 1 : losses);
      result.push(100 - (100 / (1 + rs)));
    }
  }
  return result;
};

export const calculateATR = (data: any[], period: number) => {
  const result: number[] = [];
  let trSum = 0;
  for (let i = 0; i < data.length; i++) {
    if (i === 0) {
      result.push(NaN);
      continue;
    }
    const high = data[i].high;
    const low = data[i].low;
    const prevClose = data[i-1].close;
    const tr = Math.max(high - low, Math.abs(high - prevClose), Math.abs(low - prevClose));
    
    if (i < period) {
      trSum += tr;
      result.push(NaN);
    } else if (i === period) {
      trSum += tr;
      result.push(trSum / period);
    } else {
      const prevAtr = result[i - 1];
      result.push((prevAtr * (period - 1) + tr) / period);
    }
  }
  return result;
};
