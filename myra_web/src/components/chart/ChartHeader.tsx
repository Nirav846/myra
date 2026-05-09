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
    trendAlignment
}) => {
    const lastIdx = dataIndex;
    const dClose = closes[lastIdx];
    const dOpen = opens[lastIdx];
    const trend = trendAlignment[lastIdx];

    return (
        <div className="px-2 py-1 border-b border-[#ffffff1a] font-mono font-bold text-sm text-white flex justify-between items-center bg-[#0e1117] z-10 relative">
            <div className="flex items-center gap-3">
                <span className="text-base leading-none">{symbol}</span>
                <span className="text-[10px] font-normal text-[#888] tracking-widest">{dates[lastIdx] || ''}</span>
            </div>
            <div className="flex gap-3 text-[10px] font-normal flex-wrap justify-end tracking-wider">
               <span className="text-[#888]">O:<span className="text-[#fafafa] ml-1">{opens[lastIdx]?.toFixed(1)}</span></span>
               <span className="text-[#888]">H:<span className="text-[#fafafa] ml-1">{highs[lastIdx]?.toFixed(1)}</span></span>
               <span className="text-[#888]">L:<span className="text-[#fafafa] ml-1">{lows[lastIdx]?.toFixed(1)}</span></span>
               <span className="text-[#888]">C:<span className="text-[#fafafa] ml-1">{closes[lastIdx]?.toFixed(1)}</span></span>
               
               <span className="text-[#888]">V:<span className="text-[#fafafa] ml-1">
                   {typeof volumes[lastIdx] === 'number' 
                       ? (volumes[lastIdx] >= 1000000 
                           ? (volumes[lastIdx] / 1000000).toFixed(1) + 'M' 
                           : (volumes[lastIdx] / 1000).toFixed(0) + 'k') 
                       : '-'}
               </span></span>
               
               {deliveryPct[lastIdx] != null && (
                   <span className="text-[#888]">D:<span className="text-[#fafafa] ml-1">{Number(deliveryPct[lastIdx]).toFixed(0)}%</span></span>
               )}
               
               {trend != null && !Number.isNaN(trend) && (
                   <span className="text-[#888] hidden sm:inline">Trend:
                       <span className={`font-bold ml-1 ${trend > 0 ? 'text-green-400' : (trend < 0 ? 'text-red-400' : 'text-[#fafafa]')}`}>
                           {trend}
                       </span>
                   </span>
               )}
            </div>
        </div>
    );
};
