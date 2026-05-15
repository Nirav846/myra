import { SmpSettings, SMP_THRESHOLDS, getVolatilityRegime, applyVolatilityScalar } from './indicatorConfig';

export type SmpType = 'accumulation' | 'distribution' | 'absorption' | 'exhaustion' | 'indecision';

export interface SmartMoneyPrint {
    date: string;
    type: SmpType;
    score: number;
    price: number;
    metrics: {
        volumeSpike: number;
        deliveryPct: number;
        closeLoc: number;
        zScore: number;
    }
}

export function closeLocation(open: number, high: number, low: number, close: number): number {
    const range = high - low;
    if (range === 0) return 0.5;
    return (close - low) / range;
}

export function computeSmartMoneyPrints(data: any[], bucket: string | null, settings: SmpSettings): SmartMoneyPrint[] {
    const prints: SmartMoneyPrint[] = [];
    if (data.length < 20) return prints;

    const bkt = bucket || 'Broader Market (N500)';
    const defaults = SMP_THRESHOLDS[bkt] || SMP_THRESHOLDS['Broader Market (N500)'];
    
    const volPeriod = 20;

    for (let i = volPeriod; i < data.length; i++) {
        const bar = data[i];
        
        let volSum = 0;
        let delSum = 0;
        for (let j = i - volPeriod; j < i; j++) {
           volSum += (data[j].volume || 0);
           delSum += (data[j].delivery || 0);
        }
        const avgVol = volSum / volPeriod;
        const currentVol = bar.volume || 0;
        const currentDel = bar.delivery || 0;
        const currentDelPct = currentVol > 0 ? (currentDel / currentVol) * 100 : 0;

        const volSpike = currentVol / (avgVol || 1);
        
        const currentRange = bar.high - bar.low;
        let rangeSum = 0;
        for (let j = i - volPeriod; j < i; j++) {
            rangeSum += (data[j].high - data[j].low);
        }
        const avgRange = rangeSum / volPeriod;
        const regime = getVolatilityRegime(currentRange, avgRange);

        const targetVolSpike = applyVolatilityScalar(settings.minVolumeSpike || defaults.minVolumeSpike, regime, settings.volatilityScaling);
        const targetDelPct = settings.minDeliveryPct || defaults.minDeliveryPct;

        if (volSpike >= targetVolSpike && currentDelPct >= targetDelPct) {
            const cLoc = closeLocation(bar.open, bar.high, bar.low, bar.close);
            let type: SmpType = 'indecision';
            
            const isGreen = bar.close > bar.open;

            if (cLoc >= settings.accumulationCloseZone) {
                if (isGreen) type = 'accumulation';
                else type = 'absorption'; 
            } else if (cLoc <= settings.distributionCloseZone) {
                if (!isGreen) type = 'distribution';
                else type = 'exhaustion'; 
            }

            const prev = data[i-1];
            const breakoutBonus = (type === 'accumulation' && bar.close > prev.high) || (type === 'distribution' && bar.close < prev.low) ? 20 : 0;

            const score = Math.min(100, (volSpike * 10) + (currentDelPct * 0.5) + breakoutBonus);

            if (
                (type === 'accumulation' && !settings.showAccumulation) ||
                (type === 'distribution' && !settings.showDistribution) ||
                (type === 'indecision' && settings.hideIndecision)
            ) {
                continue;
            }

            prints.push({
                date: bar.date,
                type,
                score,
                price: type === 'accumulation' || type === 'absorption' ? bar.low : bar.high,
                metrics: {
                    volumeSpike: volSpike,
                    deliveryPct: currentDelPct,
                    closeLoc: cLoc,
                    zScore: score
                }
            });
        }
    }

    return prints;
}

export function smpToTraces(smps: SmartMoneyPrint[], dates: string[]): any[] {
    const tracesMap: Record<SmpType, any> = {
        'accumulation': { x: [], y: [], text: [], marker: { symbol: 'triangle-up', size: [], color: 'rgba(34,197,94,1)', opacity: [], line: { width: 0 } }, name: 'Accumulation' },
        'distribution': { x: [], y: [], text: [], marker: { symbol: 'triangle-down', size: [], color: 'rgba(239,68,68,1)', opacity: [], line: { width: 0 } }, name: 'Distribution' },
        'absorption': { x: [], y: [], text: [], marker: { symbol: 'diamond', size: [], color: 'rgba(56,189,248,1)', opacity: [], line: { width: 0 } }, name: 'Absorption' },
        'exhaustion': { x: [], y: [], text: [], marker: { symbol: 'diamond', size: [], color: 'rgba(249,115,22,1)', opacity: [], line: { width: 0 } }, name: 'Exhaustion' },
        'indecision': { x: [], y: [], text: [], marker: { symbol: 'circle', size: [], color: 'rgba(156,163,175,1)', opacity: [], line: { width: 0 } }, name: 'Indecision' },
    };

    smps.forEach(smp => {
        const tr = tracesMap[smp.type];
        tr.x.push(smp.date);
        
        let yOffset = 0;
        if (smp.type === 'accumulation' || smp.type === 'absorption') {
             yOffset = smp.price * 0.99; 
        } else {
             yOffset = smp.price * 1.01; 
        }

        tr.y.push(yOffset);
        
        const size = Math.max(8, Math.min(20, smp.score / 5));
        const opacity = Math.max(0.6, Math.min(1.0, smp.score / 100));

        tr.marker.size.push(size);
        tr.marker.opacity.push(opacity);

        tr.text.push(`Type: ${smp.type.toUpperCase()}<br>Vol Spike: ${smp.metrics.volumeSpike.toFixed(2)}x<br>Delivery: ${smp.metrics.deliveryPct.toFixed(1)}%<br>Score: ${smp.score.toFixed(1)}`);
    });

    const outTraces: any[] = [];
    Object.keys(tracesMap).forEach(k => {
        const mapped = tracesMap[k as SmpType];
        if (mapped.x.length > 0) {
            outTraces.push({
                type: 'scatter',
                mode: 'markers',
                x: mapped.x,
                y: mapped.y,
                text: mapped.text,
                hoverinfo: 'text',
                marker: mapped.marker,
                name: mapped.name,
                showlegend: true
            });
        }
    });

    return outTraces;
}
