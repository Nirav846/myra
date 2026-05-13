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
        <div className="px-2 py-0.5 border-b border-[#ffffff1a] font-mono text-[10px] text-white flex justify-between items-center bg-[#0e1117]/90 z-10 relative backdrop-blur-sm">
            <div className="flex items-center gap-2">
                <span className="text-xs font-bold text-cyan-400">{symbol}</span>
                <span className="text-[#888] font-normal">{dates[lastIdx] || ''}</span>
                <div className="flex gap-2 ml-2">
                    <span className="text-[#888]">O:<span className={`${priceColor} ml-0.5`}>{opens[lastIdx]?.toFixed(2)}</span></span>
                    <span className="text-[#888]">H:<span className={`${priceColor} ml-0.5`}>{highs[lastIdx]?.toFixed(2)}</span></span>
                    <span className="text-[#888]">L:<span className={`${priceColor} ml-0.5`}>{lows[lastIdx]?.toFixed(2)}</span></span>
                    <span className="text-[#888]">C:<span className={`${priceColor} ml-0.5`}>{closes[lastIdx]?.toFixed(2)}</span></span>
                </div>
            </div>
            <div className="flex gap-2.5 font-normal flex-wrap justify-end">
               <span className="text-[#888]">V:<span className="text-[#fafafa] ml-0.5">{formatVol(volumes[lastIdx])}</span></span>
               
               {deliveryPct[lastIdx] != null && !isNaN(deliveryPct[lastIdx]) && (
                   <span className="text-[#888]">D%:<span className="text-cyan-400 ml-0.5">{Number(deliveryPct[lastIdx]).toFixed(1)}%</span></span>
               )}

               {relVol[lastIdx] != null && !isNaN(relVol[lastIdx]) && (
                   <span className="text-[#888]">RV:<span className={`${relVol[lastIdx] > 1.5 ? 'text-orange-400' : 'text-[#fafafa]'} ml-0.5`}>{Number(relVol[lastIdx]).toFixed(2)}</span></span>
               )}

               {volComp[lastIdx] != null && !isNaN(volComp[lastIdx]) && (
                   <span className="text-[#888]">VC:<span className={`${volComp[lastIdx] < 3 ? 'text-purple-400' : 'text-[#fafafa]'} ml-0.5`}>{Number(volComp[lastIdx]).toFixed(2)}</span></span>
               )}

               {divScores[lastIdx] != null && !isNaN(divScores[lastIdx]) && (
                   <span className="text-[#888]">DIV:<span className={`${Math.abs(divScores[lastIdx]) > 2 ? 'text-yellow-400' : 'text-[#fafafa]'} ml-0.5`}>{Number(divScores[lastIdx]).toFixed(2)}</span></span>
               )}
               
               {trend != null && !Number.isNaN(trend) && (
                   <span className="text-[#888] hidden lg:inline">TREND:
                       <span className={`font-bold ml-0.5 ${trend > 0 ? 'text-green-400' : (trend < 0 ? 'text-red-400' : 'text-[#fafafa]')}`}>
                           {trend}
                       </span>
                   </span>
               )}
            </div>
        </div>
    );
};
