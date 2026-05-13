export function aggregateData(data: any[], timeframe: '1D' | '1W' | '1M'): any[] {
    if (timeframe === '1D' || !data || data.length === 0) return data;

    // Helper to get ISO week string "YYYY-Www"
    const getISOWeek = (dateString: string) => {
        const d = new Date(dateString);
        d.setHours(0, 0, 0, 0);
        d.setDate(d.getDate() + 3 - (d.getDay() + 6) % 7);
        const week1 = new Date(d.getFullYear(), 0, 4);
        const week = 1 + Math.round(((d.getTime() - week1.getTime()) / 86400000 - 3 + (week1.getDay() + 6) % 7) / 7);
        return `${d.getFullYear()}-W${week.toString().padStart(2, '0')}`;
    };

    const getMonth = (dateString: string) => {
        return dateString.substring(0, 7); // YYYY-MM
    };

    const aggregated = [];
    let currentGroupKey = '';
    let currentCandle: any = null;

    for (const row of data) {
        const groupKey = timeframe === '1W' ? getISOWeek(row.date) : getMonth(row.date);

        if (groupKey !== currentGroupKey) {
            if (currentCandle) {
                if (currentCandle.volume > 0) {
                    currentCandle.delivery_pct = (currentCandle.delivery / currentCandle.volume) * 100;
                } else {
                    currentCandle.delivery_pct = 0;
                }
                if (currentCandle._vwap_vol > 0) {
                    currentCandle.vwap = currentCandle._vwap_sum / currentCandle._vwap_vol;
                }
                aggregated.push({ ...currentCandle });
            }
            currentGroupKey = groupKey;
            currentCandle = {
                ...row,
                high: row.high,
                low: row.low,
                close: row.close,
                volume: row.volume || 0,
                delivery: row.delivery || 0,
                volume_final: row.volume_final || 0,
                delivery_final: row.delivery_final || 0,
                _vwap_sum: (row.vwap && row.volume) ? row.vwap * row.volume : 0,
                _vwap_vol: row.volume || 0
            };
        } else {
            currentCandle.close = row.close;
            currentCandle.high = Math.max(currentCandle.high, row.high);
            currentCandle.low = Math.min(currentCandle.low, row.low);
            currentCandle.volume += (row.volume || 0);
            currentCandle.delivery += (row.delivery || 0);
            if (row.volume_final) currentCandle.volume_final += row.volume_final;
            if (row.delivery_final) currentCandle.delivery_final += row.delivery_final;
            
            currentCandle._vwap_sum += (row.vwap && row.volume) ? row.vwap * row.volume : 0;
            currentCandle._vwap_vol += (row.volume || 0);
            
            // Set date to the last trading day of the period
            currentCandle.date = row.date; 
        }
    }

    if (currentCandle) {
        if (currentCandle.volume > 0) {
            currentCandle.delivery_pct = (currentCandle.delivery / currentCandle.volume) * 100;
        } else {
            currentCandle.delivery_pct = 0;
        }
        if (currentCandle._vwap_vol > 0) {
            currentCandle.vwap = currentCandle._vwap_sum / currentCandle._vwap_vol;
        }
        aggregated.push({ ...currentCandle });
    }

    return aggregated;
}
