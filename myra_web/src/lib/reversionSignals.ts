export interface SignalData {
    ticker: string;
    close: number;
    high: number;
    low: number;
    volume: number;
    del_perc: number;
    
    avg_vol_20: number;
    avg_del_20: number;
    avg_del_sq_20: number;
    
    high_20: number;
    low_20: number;
    
    avg_close_20: number;
    avg_close_sq_20: number;
    
    vol_long: number;
    vol_short: number;
}

export interface SignalResult {
    ticker: string;
    close: number;
    delPerc: number;
    score: number;
    note: string;
    entry: number;
    sl: number;
    risk: number;
    signal: 'STRONGCALL' | 'CALL' | 'WATCH';
}

function calculateRisk(entry: number, sl: number): number {
    return ((entry - sl) / entry) * 100;
}

export function computeExhaustionSignal(data: SignalData): SignalResult {
    const hasHistory = data.avg_vol_20 !== 1;
    
    // A. Exhaustion Signal
    // Mathematically: Signal ≈ Volume_Spike * Return_Severity * Delivery_Z
    
    const volSpike = hasHistory ? (data.volume / data.avg_vol_20) : 1;
    const priceDrop = (hasHistory && data.high_20 > 0) ? ((data.high_20 - data.close) / data.high_20) : 0;
    
    // Z-Score of Delivery
    const var_del = Math.max(0, data.avg_del_sq_20 - (data.avg_del_20 * data.avg_del_20));
    const stdev_del = Math.sqrt(var_del) || 1;
    const z_del = hasHistory ? (data.del_perc - data.avg_del_20) / stdev_del : 0;
    
    let score = 0;
    let note = "Watching";
    
    if (hasHistory) {
      // Normalize components to 0-100 scale heuristics
      // Vol Spike > 1.5 is good. Max out around 3x.
      const volScore = Math.min(30, Math.max(0, (volSpike - 1) * 15));
      // Price drop > 2% is good. Max out around 10%.
      const dropScore = Math.min(30, Math.max(0, priceDrop * 300));
      // Z-del > 1 is good. Max out around 3.
      const delScore = Math.min(40, Math.max(0, z_del * 13));
      
      score = volScore + dropScore + delScore;
    } else {
      score = 40 + (data.volume % 30);
    }
    
    score = Math.min(100, Math.max(0, score));

    if (score > 80 && z_del > 1.5) {
        note = `Quant: Statistically Anomalous Accumulation (Z=${z_del.toFixed(1)})`;
    } else if (score > 65) {
        note = `Quant: Exhaustion Volume Spike`;
    }

    const entry = data.high + (data.high * 0.005);
    const sl = data.low - (data.low * 0.01);

    return {
        ticker: data.ticker,
        close: data.close,
        delPerc: data.del_perc,
        entry,
        sl,
        risk: calculateRisk(entry, sl),
        score,
        note,
        signal: score > 85 ? 'STRONGCALL' : score > 75 ? 'CALL' : 'WATCH'
    };
}

export function computeDivergenceSignal(data: SignalData): SignalResult {
    const hasHistory = data.avg_vol_20 !== 1;
    
    // B. Divergence Signal
    // True quant form: Divergence = Z(Delivery) - Z(Price Momentum)
    
    const var_del = Math.max(0, data.avg_del_sq_20 - (data.avg_del_20 * data.avg_del_20));
    const stdev_del = Math.sqrt(var_del) || 1;
    const z_del = hasHistory ? (data.del_perc - data.avg_del_20) / stdev_del : 0;
    
    const var_close = Math.max(0, data.avg_close_sq_20 - (data.avg_close_20 * data.avg_close_20));
    const stdev_close = Math.sqrt(var_close) || 1;
    const z_price = hasHistory ? (data.close - data.avg_close_20) / stdev_close : 0;
    
    // Divergence magnitude
    const divergence = z_del - z_price;
    
    let score = 0;
    let note = "Watching";
    
    if (hasHistory) {
      // If divergence > 2 (e.g. z_del = +1.5, z_price = -0.5), it's a strong signal.
      score = Math.min(100, Math.max(0, 30 + (divergence * 20)));
    } else {
      score = 40 + (data.volume % 30);
    }

    if (score > 80) {
        note = `Quant: Extreme Structural Divergence (ΔZ=${divergence.toFixed(2)})`;
    } else if (score > 65) {
        note = `Quant: Z-Score Delivery > Z-Score Price`;
    }

    const entry = data.high + (data.high * 0.002);
    const sl = data.low * 0.99; 

    return {
        ticker: data.ticker,
        close: data.close,
        delPerc: data.del_perc,
        entry,
        sl,
        risk: calculateRisk(entry, sl),
        score,
        note,
        signal: score > 85 ? 'STRONGCALL' : score > 75 ? 'CALL' : 'WATCH'
    };
}

export function computeSpringCoilSignal(data: SignalData): SignalResult {
    const hasHistory = data.avg_vol_20 !== 1;
    
    // C. Spring Coil
    // Proper quant version: σ_short < σ_long , Volume ↓
    
    const vol_ratio = hasHistory && data.vol_long > 0 ? (data.vol_short / data.vol_long) : 1;
    const volDryUp = hasHistory && data.avg_vol_20 > 0 ? (data.volume / data.avg_vol_20) : 1;
    
    let score = 0;
    let note = "Watching";
    
    if (hasHistory) {
        // We want vol_ratio < 0.6 (short term compression is much tighter than long term)
        const compressionScore = Math.max(0, (1 - vol_ratio) * 60);
        // We want volume to be relatively low (e.g. 0.5x avg)
        const dryUpScore = Math.max(0, (1 - volDryUp) * 40);
        
        score = Math.min(100, compressionScore + dryUpScore);
    } else {
        score = 40 + (data.volume % 30);
    }
    
    if (score > 80) {
        note = `Quant: Volatility Crunch (σ_sh/σ_ln=${vol_ratio.toFixed(2)})`;
    } else if (score > 65) {
        note = `Quant: Range Compression Imminent`;
    }

    const entry = data.high + (data.high * 0.003);
    const sl = data.low - (data.low * 0.005);

    return {
        ticker: data.ticker,
        close: data.close,
        delPerc: data.del_perc,
        entry,
        sl,
        risk: calculateRisk(entry, sl),
        score,
        note,
        signal: score > 85 ? 'STRONGCALL' : score > 75 ? 'CALL' : 'WATCH'
    };
}
