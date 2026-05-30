import { useState } from 'react';
import { Calculator, AlertTriangle } from 'lucide-react';

interface PositionSizerProps {
    entryPrice: number;
    stopLossPrice: number;
    symbol: string;
}

export default function PositionSizer({ entryPrice, stopLossPrice, symbol }: PositionSizerProps) {
    const [accountSize, setAccountSize] = useState(500000);
    const [riskPct, setRiskPct] = useState(1);

    const riskPerTrade = accountSize * (riskPct / 100);
    const riskPerShare = entryPrice - stopLossPrice;
    const positionSize = riskPerShare > 0
        ? Math.floor(riskPerTrade / riskPerShare) : 0;
    const positionValue = positionSize * entryPrice;
    const positionPct = accountSize > 0
        ? (positionValue / accountSize * 100).toFixed(1) : '0';

    return (
        <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3 mb-4">
            <div className="text-[10px] text-[#888] font-mono mb-2 flex items-center gap-1">
                <Calculator size={10} />
                Position Sizing — {symbol}
            </div>
            <div className="grid grid-cols-2 gap-3 mb-3">
                <div>
                    <label className="text-[10px] text-[#666] font-mono">
                        Account Size (₹)
                    </label>
                    <input
                        type="number"
                        value={accountSize}
                        onChange={e => setAccountSize(Number(e.target.value))}
                        className="w-full bg-[#2a2c34] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none mt-0.5"
                        step="100000"
                        min="10000"
                    />
                </div>
                <div>
                    <label className="text-[10px] text-[#666] font-mono">
                        Risk per Trade %
                    </label>
                    <input
                        type="number"
                        value={riskPct}
                        onChange={e => setRiskPct(Number(e.target.value))}
                        className="w-full bg-[#2a2c34] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none mt-0.5"
                        step="0.5"
                        min="0.5"
                        max="5"
                    />
                </div>
            </div>
            <div className="grid grid-cols-4 gap-2 text-center">
                <div className="bg-[#2a2c34] rounded p-2">
                    <div className="text-[9px] text-[#666] font-mono">Shares</div>
                    <div className="text-sm text-orange-400 font-bold font-mono">
                        {positionSize.toLocaleString('en-IN')}
                    </div>
                </div>
                <div className="bg-[#2a2c34] rounded p-2">
                    <div className="text-[9px] text-[#666] font-mono">Capital</div>
                    <div className="text-sm text-[#fafafa] font-mono">
                        ₹{positionValue.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                    </div>
                </div>
                <div className="bg-[#2a2c34] rounded p-2">
                    <div className="text-[9px] text-[#666] font-mono">% of Account</div>
                    <div className={`text-sm font-mono ${
                        Number(positionPct) > 20 ? 'text-red-400' :
                        Number(positionPct) > 10 ? 'text-yellow-400' :
                        'text-green-400'
                    }`}>
                        {positionPct}%
                    </div>
                </div>
                <div className="bg-[#2a2c34] rounded p-2">
                    <div className="text-[9px] text-[#666] font-mono">Max Loss</div>
                    <div className="text-sm text-red-400 font-mono">
                        ₹{riskPerTrade.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                    </div>
                </div>
            </div>
            {Number(positionPct) > 20 && (
                <div className="mt-2 text-[10px] text-red-400 font-mono flex items-center gap-1">
                    <AlertTriangle size={10} />
                    Position exceeds 20% of account — consider reducing risk %
                </div>
            )}
        </div>
    );
}
