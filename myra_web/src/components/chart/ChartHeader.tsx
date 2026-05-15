import React from 'react';

interface ChartHeaderProps {
    symbol: string;
    dataIndex: number;
    dates: string[];
    opens: number[];
    highs: number[];
    lows: number[];
    closes: number[];
    volumes: number[];
    deliveryPct: number[];
    relVol: number[];
    volComp: number[];
    divScores: number[];
    trendAlignment: number[];
}

export const ChartHeader: React.FC<ChartHeaderProps> = ({
    symbol,
    dataIndex,
    dates,
    opens,
    highs,
    lows,
    closes,
    volumes,
    deliveryPct,
    relVol,
    volComp,
    divScores,
    trendAlignment
}) => {
    const lastIdx = dataIndex;
    const dClose = closes[lastIdx];
    const dOpen = opens[lastIdx];
    const trend = trendAlignment[lastIdx];

    const formatVol = (v: number) => {
        if (typeof v !== 'number' || isNaN(v)) return '-';
        if (v >= 10000000) return (v / 10000000).toFixed(2) + ' Cr';
        if (v >= 100000) return (v / 100000).toFixed(2) + ' L';
        if (v >= 1000) return (v / 1000).toFixed(1) + ' k';
        return v.toString();
    };

    const isBullish = dClose >= dOpen;
    const priceColor = isBullish ? 'text-green-400' : 'text-red-400';

    return (
        <div className="absolute top-2 left-10 right-10 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[10px] z-10 pointer-events-none select-none items-center">
            <span className="text-[11px] font-bold text-cyan-400">{symbol}</span>
            <span className="text-[#888]">{dates[lastIdx] || ''}</span>
            
            <div className="flex gap-1.5 ml-1">
                <span className="text-[#888]">O<span className={`${priceColor} ml-1`}>{opens[lastIdx]?.toFixed(2)}</span></span>
                <span className="text-[#888]">H<span className={`${priceColor} ml-1`}>{highs[lastIdx]?.toFixed(2)}</span></span>
                <span className="text-[#888]">L<span className={`${priceColor} ml-1`}>{lows[lastIdx]?.toFixed(2)}</span></span>
                <span className="text-[#888]">C<span className={`${priceColor} ml-1`}>{closes[lastIdx]?.toFixed(2)}</span></span>
            </div>

            <div className="flex gap-2 ml-1">
               <span className="text-[#888]">V<span className="text-[#fafafa] ml-1">{formatVol(volumes[lastIdx])}</span></span>
               
               {deliveryPct[lastIdx] != null && !isNaN(deliveryPct[lastIdx]) && (
                   <span className="text-[#888]">D%<span className="text-cyan-400 ml-1">{Number(deliveryPct[lastIdx]).toFixed(1)}%</span></span>
               )}

               {relVol[lastIdx] != null && !isNaN(relVol[lastIdx]) && (
                   <span className="text-[#888]">RV<span className={`${relVol[lastIdx] > 1.5 ? 'text-orange-400' : 'text-[#fafafa]'} ml-1`}>{Number(relVol[lastIdx]).toFixed(2)}</span></span>
               )}

               {volComp[lastIdx] != null && !isNaN(volComp[lastIdx]) && (
                   <span className="text-[#888]">VC<span className={`${volComp[lastIdx] < 3 ? 'text-purple-400' : 'text-[#fafafa]'} ml-1`}>{Number(volComp[lastIdx]).toFixed(2)}</span></span>
               )}

               {divScores[lastIdx] != null && !isNaN(divScores[lastIdx]) && (
                   <span className="text-[#888]">DIV<span className={`${Math.abs(divScores[lastIdx]) > 2 ? 'text-yellow-400' : 'text-[#fafafa]'} ml-1`}>{Number(divScores[lastIdx]).toFixed(2)}</span></span>
               )}
               
               {trend != null && !Number.isNaN(trend) && (
                   <span className="text-[#888] hidden lg:inline">TREND<span className={`font-bold ml-1 ${trend > 0 ? 'text-green-400' : (trend < 0 ? 'text-red-400' : 'text-[#fafafa]')}`}>
                       {trend}
                   </span></span>
               )}
            </div>
        </div>
    );
};
