export interface NakedPoc {
  id: string;
  type: 'volume' | 'delivery';
  price: number;
  dateStr: string;
  retested: boolean;
  retestDateStr?: string;
  color: string;
}

export function computePocTolerance(price: number, avgVolatility: number): number {
  return price * avgVolatility * 0.1;
}

export function computeNakedPocs(data: any[], avgVolatility: number): NakedPoc[] {
  const pocs: NakedPoc[] = [];
  
  for (let i = 0; i < data.length; i++) {
    const bar = data[i];
    const price = bar.vwap ?? ((bar.high + bar.low + bar.close) / 3);
    const vol = bar.volume || 0;
    
    if (vol <= 0) continue;
    
    pocs.push({
      id: `vol-${bar.date}`,
      type: 'volume',
      price: price,
      dateStr: bar.date,
      retested: false,
      color: 'rgba(136,136,136,0.8)'
    });
    
    pocs.push({
      id: `del-${bar.date}`,
      type: 'delivery',
      price: price,
      dateStr: bar.date,
      retested: false,
      color: 'rgba(6,182,212,0.8)'
    });
  }
  
  for (let i = 0; i < pocs.length; i++) {
    const poc = pocs[i];
    const startDateIdx = data.findIndex(d => d.date === poc.dateStr);
    if (startDateIdx === -1) continue;
    
    const tol = computePocTolerance(poc.price, avgVolatility);
    
    for (let j = startDateIdx + 1; j < data.length; j++) {
      const bar = data[j];
      const low = bar.low || bar.close;
      const high = bar.high || bar.close;
      if (poc.price >= low - tol && poc.price <= high + tol) {
        poc.retested = true;
        poc.retestDateStr = bar.date;
        break;
      }
    }
  }
  
  return pocs;
}

export function nakedPocsToShapes(pocs: NakedPoc[], latestDate: string): any[] {
  const shapes: any[] = [];
  pocs.forEach(poc => {
    if (!poc.retested) {
      shapes.push({
        type: 'line',
        x0: poc.dateStr,
        x1: latestDate,
        y0: poc.price,
        y1: poc.price,
        line: { color: poc.color, width: 1, dash: 'dot' },
        layer: 'below'
      });
    }
  });
  return shapes;
}
