export type AggregateMessage = {
  type: 'AGGREGATE';
  data: any[];
  timeframe: '1W' | '1w' | '1M' | '1m';
  symbol: string;
};

export type AggregateResponseMessage = {
  type: 'AGGREGATED';
  candles: any[];
  symbol: string;
};

self.onmessage = (e: MessageEvent<AggregateMessage>) => {
  if (e.data.type === 'AGGREGATE') {
    const { data, timeframe, symbol } = e.data;
    if (data.length === 0) {
      self.postMessage({ type: 'AGGREGATED', candles: [], symbol });
      return;
    }

    const aggregated = [];
    let currentCandle: any = null;

    const getGroupKey = (dateStr: string, tf: string) => {
      const d = new Date(dateStr);
      if (tf === '1W' || tf === '1w') {
        const dObj = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
        const dayNum = dObj.getUTCDay() || 7;
        dObj.setUTCDate(dObj.getUTCDate() + 4 - dayNum);
        const yearStart = new Date(Date.UTC(dObj.getUTCFullYear(),0,1));
        const weekNo = Math.ceil((((dObj.getTime() - yearStart.getTime()) / 86400000) + 1)/7);
        return `${dObj.getUTCFullYear()}-W${weekNo.toString().padStart(2, '0')}`;
      } else {
         return `${d.getFullYear()}-${(d.getMonth() + 1).toString().padStart(2, '0')}`;
      }
    };

    for (let i = 0; i < data.length; i++) {
        const row = data[i];
        const groupKey = getGroupKey(row.date, timeframe);
        
        if (!currentCandle) {
            currentCandle = { ...row, groupKey, date: row.date, volume: row.volume || 0, delivery_final: row.delivery_final || 0 };
        } else if (currentCandle.groupKey === groupKey) {
            currentCandle.high = Math.max(currentCandle.high, row.high);
            currentCandle.low = Math.min(currentCandle.low, row.low);
            currentCandle.close = row.close;
            currentCandle.volume += (row.volume || 0);
            currentCandle.delivery_final += (row.delivery_final || 0);
        } else {
            aggregated.push(currentCandle);
            currentCandle = { ...row, groupKey, date: row.date, volume: row.volume || 0, delivery_final: row.delivery_final || 0 };
        }
    }
    if (currentCandle) {
        aggregated.push(currentCandle);
    }

    self.postMessage({ type: 'AGGREGATED', candles: aggregated, symbol });
  }
};
