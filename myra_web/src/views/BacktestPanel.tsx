import { useState, useEffect } from 'react';
import { Librarian } from '../lib/Librarian';
import { X } from 'lucide-react';
import PositionSizer from './PositionSizer';

interface BacktestPanelProps {
    lib: Librarian;
    symbol: string;
    entryPrice: number;
    stopLossPrice: number;
    detectedBaseLength: number;
    minDeliveryChange: number;
    deliveryMetric: 'Pct' | 'Qty';
    onClose: () => void;
}

interface BacktestInstance {
    date: string;
    entry_price: number;
    del_change: number;
    ret_5d: number | null;
    ret_10d: number | null;
    ret_21d: number | null;
}

export default function BacktestPanel({ lib, symbol, entryPrice, stopLossPrice, detectedBaseLength, minDeliveryChange, deliveryMetric, onClose }: BacktestPanelProps) {
    const [instances, setInstances] = useState<BacktestInstance[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let active = true;
        const fetch = async () => {
            setIsLoading(true);
            setError(null);
            try {
                const query = `
    WITH ranked AS (
        SELECT
            date, close, high, low, volume, delivery,
            ROW_NUMBER() OVER (ORDER BY date ASC) AS rn
        FROM technical_data
        WHERE symbol = ?
        ORDER BY date DESC
        LIMIT 504
    ),
    with_lag AS (
        SELECT
            r.rn,
            r.date,
            r.close                                              AS entry_close,
            (r.delivery * 100.0 / NULLIF(r.volume, 0))          AS del_pct_now,
            (p.delivery * 100.0 / NULLIF(p.volume, 0))          AS del_pct_past,
            (r.delivery * 100.0 / NULLIF(r.volume, 0)) -
            (p.delivery * 100.0 / NULLIF(p.volume, 0))          AS del_change
        FROM ranked r
        INNER JOIN ranked p ON p.rn = r.rn - ?
    ),
    with_forward AS (
        SELECT
            w.date,
            w.entry_close,
            w.del_change,
            f5.close  AS fwd_5_close,
            f10.close AS fwd_10_close,
            f21.close AS fwd_21_close
        FROM with_lag w
        LEFT JOIN ranked f5  ON f5.rn  = w.rn + 5
        LEFT JOIN ranked f10 ON f10.rn = w.rn + 10
        LEFT JOIN ranked f21 ON f21.rn = w.rn + 21
    )
    SELECT
        date,
        entry_close AS entry_price,
        ROUND(del_change, 2)                                               AS del_change,
        ROUND((fwd_5_close  - entry_close) / entry_close * 100, 2)        AS ret_5d,
        ROUND((fwd_10_close - entry_close) / entry_close * 100, 2)        AS ret_10d,
        ROUND((fwd_21_close - entry_close) / entry_close * 100, 2)        AS ret_21d
    FROM with_forward
    WHERE del_change >= ?
      AND fwd_21_close IS NOT NULL
    ORDER BY date DESC
`;
                const params = [symbol, detectedBaseLength, minDeliveryChange];
                const results = await lib.executeQuery('_tech_conn', query, params, 15000);
                if (active) {
                    setInstances(results && Array.isArray(results) ? results as BacktestInstance[] : []);
                }
            } catch (e: any) {
                if (active) setError(e.message || 'Query failed');
            } finally {
                if (active) setIsLoading(false);
            }
        };
        fetch();
        return () => { active = false; };
    }, [lib, symbol, detectedBaseLength, minDeliveryChange]);

    const computeMedian = (arr: number[]): number => {
        if (arr.length === 0) return 0;
        const sorted = [...arr].sort((a, b) => a - b);
        const mid = Math.floor(sorted.length / 2);
        return sorted.length % 2 !== 0
            ? sorted[mid]
            : parseFloat(((sorted[mid - 1] + sorted[mid]) / 2).toFixed(2));
    };

    const stats = (() => {
        if (instances.length === 0) return null;
        const returns21 = instances.map(i => i.ret_21d).filter((r): r is number => r !== null);
        const returns10 = instances.map(i => i.ret_10d).filter((r): r is number => r !== null);
        const returns5  = instances.map(i => i.ret_5d).filter((r): r is number => r !== null);
        if (returns21.length === 0) return null;
        const winCount = returns21.filter(r => r > 0).length;
        const mean21 = parseFloat((returns21.reduce((a, b) => a + b, 0) / returns21.length || 0).toFixed(2));
        const median21 = computeMedian(returns21);
        const sorted21 = [...returns21].sort((a, b) => a - b);
        return {
            count: returns21.length,
            winRate: (winCount / returns21.length) * 100,
            median: median21,
            mean: mean21,
            median10: computeMedian(returns10),
            median5: computeMedian(returns5),
            best: sorted21[sorted21.length - 1],
            worst: sorted21[0],
            skew: median21 > mean21 ? 'positive' : mean21 > median21 ? 'negative' : 'none',
        };
    })();

    const insufficient = stats !== null && stats.count < 5;

    return (
        <div className="border-t border-[#ffffff1a] bg-[#15171d]">
            <div className="px-4 py-3 flex items-center justify-between border-b border-[#ffffff1a]">
                <div className="flex items-center gap-2">
                    <span className="text-xs font-mono text-[#888]">Backtest</span>
                    <span className="text-sm font-bold text-[#fafafa]">{symbol}</span>
                    <span className="text-[12px] font-mono text-[#888]">
                        del ≥{minDeliveryChange}{deliveryMetric === 'Pct' ? 'pp' : '%'} over {detectedBaseLength}b base
                    </span>
                </div>
                <button onClick={onClose} className="text-[#888] hover:text-white transition-colors" title="Close panel">
                    <X size={14} />
                </button>
            </div>
            <div className="p-4">
                {isLoading ? (
                    <div className="text-center py-6 text-[#888] font-mono text-xs">Loading historical instances...</div>
                ) : error ? (
                    <div className="text-center py-6 text-red-400 font-mono text-xs">{error}</div>
                ) : instances.length === 0 ? (
                    <div className="text-center py-6 text-[#888] font-mono text-xs">No historical instances found for these parameters.</div>
                ) : (
                    <>
                        <PositionSizer
                            entryPrice={entryPrice}
                            stopLossPrice={stopLossPrice}
                            symbol={symbol}
                        />
                        {stats && (
                            <div className="grid grid-cols-6 gap-3 mb-2">
                                <div className="bg-[#2a2c34] border border-[#ffffff1a] rounded p-2.5 text-center">
                                    <div className="text-[12px] text-[#888] font-mono">Sample Size</div>
                                    <div className="text-lg font-semibold text-[#fafafa]">{stats.count}</div>
                                </div>
                                <div className="bg-[#2a2c34] border border-[#ffffff1a] rounded p-2.5 text-center">
                                    <div className="text-[12px] text-[#888] font-mono">Win Rate (21d)</div>
                                    <div className={`text-lg font-semibold ${stats.winRate >= 60 ? 'text-green-400' : stats.winRate >= 40 ? 'text-yellow-400' : 'text-red-400'}`}>
                                        {stats.winRate.toFixed(0)}%
                                    </div>
                                </div>
                                <div className="bg-[#2a2c34] border border-[#ffffff1a] rounded p-2.5 text-center">
                                    <div className="text-[12px] text-[#888] font-mono">Median 21d</div>
                                    <div className={`text-lg font-semibold ${stats.median >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                                        {stats.median > 0 ? '+' : ''}{stats.median.toFixed(1)}%
                                    </div>
                                </div>
                                <div className="bg-[#2a2c34] border border-[#ffffff1a] rounded p-2.5 text-center">
                                    <div className="text-[12px] text-[#888] font-mono">Mean 21d</div>
                                    <div className={`text-lg font-semibold ${stats.mean >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                                        {stats.mean > 0 ? '+' : ''}{stats.mean.toFixed(1)}%
                                    </div>
                                </div>
                                <div className="bg-[#2a2c34] border border-[#ffffff1a] rounded p-2.5 text-center">
                                    <div className="text-[12px] text-[#888] font-mono">Best 21d</div>
                                    <div className="text-lg font-semibold text-green-400">+{stats.best.toFixed(1)}%</div>
                                </div>
                                <div className="bg-[#2a2c34] border border-[#ffffff1a] rounded p-2.5 text-center">
                                    <div className="text-[12px] text-[#888] font-mono">Worst 21d</div>
                                    <div className="text-lg font-semibold text-red-400">{stats.worst.toFixed(1)}%</div>
                                </div>
                            </div>
                        )}

                        {stats && stats.skew !== 'none' && (
                            <div className="mb-3 px-3 py-1.5 bg-[#ffffff08] border border-[#ffffff1a] rounded text-[12px] text-[#aaa] font-mono text-center">
                                {stats.skew === 'positive'
                                    ? 'Distribution skewed positive (few large losses drag mean below median)'
                                    : 'Distribution skewed negative (few large wins push mean above median)'}
                            </div>
                        )}

                        {insufficient && (
                            <div className="mb-3 px-3 py-2 bg-yellow-500/10 border border-yellow-500/30 rounded text-[12px] text-yellow-400 font-mono text-center">
                                Insufficient historical instances (n={stats!.count}) — results not statistically meaningful
                            </div>
                        )}

                        <div className="overflow-x-auto max-h-[300px] overflow-y-auto">
                            <table className="w-full text-left border-collapse">
                                <thead className="sticky top-0 bg-[#1a1c24] z-10">
                                    <tr className="border-b border-[#ffffff1a]">
                                        <th className="px-3 py-2 text-[12px] font-medium uppercase text-[#888] font-mono whitespace-nowrap">Date</th>
                                        <th className="px-3 py-2 text-[12px] font-medium uppercase text-[#888] font-mono whitespace-nowrap text-right">Entry</th>
                                        <th className="px-3 py-2 text-[12px] font-medium uppercase text-[#888] font-mono whitespace-nowrap text-right">Del Change</th>
                                        <th className="px-3 py-2 text-[12px] font-medium uppercase text-[#888] font-mono whitespace-nowrap text-right">+5d</th>
                                        <th className="px-3 py-2 text-[12px] font-medium uppercase text-[#888] font-mono whitespace-nowrap text-right">+10d</th>
                                        <th className="px-3 py-2 text-[12px] font-medium uppercase text-[#888] font-mono whitespace-nowrap text-right">+21d</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {instances.map((inst, i) => (
                                        <tr key={i} className="border-b border-[#ffffff0a] hover:bg-[#ffffff05] transition-colors">
                                            <td className="px-3 py-2 text-xs font-mono text-[#aaa] whitespace-nowrap">{inst.date?.slice(0, 10)}</td>
                                            <td className="px-3 py-2 text-xs font-mono text-[#fafafa] whitespace-nowrap text-right">{Number(inst.entry_price).toFixed(2)}</td>
                                            <td className="px-3 py-2 text-xs font-mono whitespace-nowrap text-right">
                                                <span className={inst.del_change >= 0 ? 'text-green-400' : 'text-red-400'}>
                                                    {inst.del_change > 0 ? '+' : ''}{Number(inst.del_change).toFixed(1)}
                                                </span>
                                            </td>
                                            {[inst.ret_5d, inst.ret_10d, inst.ret_21d].map((ret, ci) => (
                                                <td key={ci} className={`px-3 py-2 text-xs font-mono whitespace-nowrap text-right ${ret === null ? 'text-[#888]' : ret >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                                                    {ret === null ? '\u2014' : `${ret > 0 ? '+' : ''}${ret.toFixed(1)}%`}
                                                </td>
                                            ))}
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}
