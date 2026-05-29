import { useState, useEffect, useRef, useCallback } from 'react';
import { fetchMarketCapMap } from '../lib/marketCapCache';

interface MarketCapRangeFilterProps {
    onChange: (range: { min: number; max: number } | null) => void;
    className?: string;
}

function roundUpNice(val: number): number {
    const cr = val / 1e7;
    if (cr <= 0) return 100 * 1e7;
    if (cr <= 100) return Math.ceil(cr / 10) * 10 * 1e7;
    if (cr <= 500) return Math.ceil(cr / 25) * 25 * 1e7;
    if (cr <= 1000) return Math.ceil(cr / 50) * 50 * 1e7;
    if (cr <= 10000) return Math.ceil(cr / 100) * 100 * 1e7;
    if (cr <= 50000) return Math.ceil(cr / 500) * 500 * 1e7;
    return Math.ceil(cr / 1000) * 1000 * 1e7;
}

function roundDownNice(val: number): number {
    if (val <= 0) return 0;
    const cr = val / 1e7;
    if (cr <= 100) return Math.floor(cr / 10) * 10 * 1e7;
    if (cr <= 500) return Math.floor(cr / 25) * 25 * 1e7;
    if (cr <= 1000) return Math.floor(cr / 50) * 50 * 1e7;
    if (cr <= 10000) return Math.floor(cr / 100) * 100 * 1e7;
    if (cr <= 50000) return Math.floor(cr / 500) * 500 * 1e7;
    return Math.floor(cr / 1000) * 1000 * 1e7;
}

export default function MarketCapRangeFilter({ onChange, className = '' }: MarketCapRangeFilterProps) {
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(false);
    const [minCr, setMinCr] = useState(0);
    const [maxCr, setMaxCr] = useState(100);
    const [absMinCr, setAbsMinCr] = useState(0);
    const [absMaxCr, setAbsMaxCr] = useState(100);
    const [draftMin, setDraftMin] = useState('0');
    const [draftMax, setDraftMax] = useState('100');
    const [focused, setFocused] = useState<'min' | 'max' | null>(null);

    const onChangeRef = useRef(onChange);
    onChangeRef.current = onChange;

    const commitMin = useCallback(() => {
        const raw = draftMin.trim();
        if (raw === '' || isNaN(Number(raw))) {
            setDraftMin(String(minCr));
            return;
        }
        let clamped = Math.max(absMinCr, Math.min(absMaxCr, Math.round(Number(raw))));
        clamped = Math.min(clamped, maxCr);
        if (clamped === minCr && String(clamped) === draftMin) return;
        setMinCr(clamped);
        setDraftMin(String(clamped));
        if (clamped === absMinCr && maxCr === absMaxCr) {
            onChangeRef.current(null);
        } else {
            onChangeRef.current({ min: clamped * 1e7, max: maxCr * 1e7 });
        }
    }, [draftMin, minCr, maxCr, absMinCr, absMaxCr]);

    const commitMax = useCallback(() => {
        const raw = draftMax.trim();
        if (raw === '' || isNaN(Number(raw))) {
            setDraftMax(String(maxCr));
            return;
        }
        let clamped = Math.max(absMinCr, Math.min(absMaxCr, Math.round(Number(raw))));
        clamped = Math.max(clamped, minCr);
        if (clamped === maxCr && String(clamped) === draftMax) return;
        setMaxCr(clamped);
        setDraftMax(String(clamped));
        if (minCr === absMinCr && clamped === absMaxCr) {
            onChangeRef.current(null);
        } else {
            onChangeRef.current({ min: minCr * 1e7, max: clamped * 1e7 });
        }
    }, [draftMax, minCr, maxCr, absMinCr, absMaxCr]);

    useEffect(() => {
        let active = true;
        fetchMarketCapMap()
            .then(map => {
                if (!active) return;
                let minVal = Infinity;
                let maxVal = -Infinity;
                for (const v of map.values()) {
                    if (v < minVal) minVal = v;
                    if (v > maxVal) maxVal = v;
                }
                const mn = roundDownNice(minVal);
                const mx = roundUpNice(maxVal);
                const minCrV = Math.round(mn / 1e7);
                const maxCrV = Math.round(mx / 1e7);
                setAbsMinCr(minCrV);
                setAbsMaxCr(maxCrV);
                setMinCr(minCrV);
                setMaxCr(maxCrV);
                setDraftMin(String(minCrV));
                setDraftMax(String(maxCrV));
                setLoading(false);
                onChangeRef.current(null);
            })
            .catch(() => {
                if (active) {
                    setError(true);
                    setLoading(false);
                }
            });
        return () => { active = false; };
    }, []);

    const handleMinSlider = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
        const val = Number(e.target.value);
        const newMin = Math.min(val, maxCr);
        setMinCr(newMin);
        setDraftMin(String(newMin));
        if (newMin === absMinCr && maxCr === absMaxCr) {
            onChangeRef.current(null);
        } else {
            onChangeRef.current({ min: newMin * 1e7, max: maxCr * 1e7 });
        }
    }, [maxCr, absMinCr, absMaxCr]);

    const handleMaxSlider = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
        const val = Number(e.target.value);
        const newMax = Math.max(val, minCr);
        setMaxCr(newMax);
        setDraftMax(String(newMax));
        if (minCr === absMinCr && newMax === absMaxCr) {
            onChangeRef.current(null);
        } else {
            onChangeRef.current({ min: minCr * 1e7, max: newMax * 1e7 });
        }
    }, [minCr, absMinCr, absMaxCr]);

    const commitMinOnBlur = useCallback(() => {
        setFocused(null);
        commitMin();
    }, [commitMin]);

    const commitMaxOnBlur = useCallback(() => {
        setFocused(null);
        commitMax();
    }, [commitMax]);

    const handleMinKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
        if (e.key === 'Enter') {
            (e.target as HTMLInputElement).blur();
        }
    }, []);

    const handleMaxKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
        if (e.key === 'Enter') {
            (e.target as HTMLInputElement).blur();
        }
    }, []);

    if (loading) {
        return (
            <div className={`flex flex-col flex-shrink-0 w-[160px] ${className}`}>
                <label className="text-[10px] text-[#888] font-mono mb-1">Market Cap</label>
                <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-[10px] text-[#555] font-mono flex items-center gap-1">
                    <span className="inline-block w-2 h-2 border border-[#555] border-t-transparent rounded-full animate-spin" />
                    Loading...
                </div>
            </div>
        );
    }

    if (error) {
        return (
            <div className={`flex flex-col flex-shrink-0 w-[160px] ${className}`}>
                <label className="text-[10px] text-[#888] font-mono mb-1">Market Cap</label>
                <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-[10px] text-[#666] font-mono">
                    Unavailable
                </div>
            </div>
        );
    }

    return (
        <div className={`flex flex-col flex-shrink-0 w-[230px] ${className}`}>
            <label className="text-[10px] text-[#888] font-mono mb-1">Market Cap</label>

            {/* Input row */}
            <div className="flex items-center gap-1.5 mb-1">
                <span className="text-[10px] text-[#888] font-mono">Min ₹</span>
                <input
                    type="number"
                    value={draftMin}
                    onChange={e => setDraftMin(e.target.value)}
                    onFocus={() => setFocused('min')}
                    onBlur={commitMinOnBlur}
                    onKeyDown={handleMinKeyDown}
                    className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-0.5 text-xs font-mono text-[#fafafa] w-20 text-right focus:border-cyan-500/50 outline-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                />
                <span className="text-[10px] text-[#888] font-mono">Cr</span>
                <span className="text-[10px] text-[#888] font-mono ml-1">Max ₹</span>
                <input
                    type="number"
                    value={draftMax}
                    onChange={e => setDraftMax(e.target.value)}
                    onFocus={() => setFocused('max')}
                    onBlur={commitMaxOnBlur}
                    onKeyDown={handleMaxKeyDown}
                    className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-0.5 text-xs font-mono text-[#fafafa] w-20 text-right focus:border-cyan-500/50 outline-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                />
                <span className="text-[10px] text-[#888] font-mono">Cr</span>
            </div>

            {/* Sliders */}
            <input
                type="range"
                min={absMinCr}
                max={absMaxCr}
                value={minCr}
                onChange={handleMinSlider}
                className="w-full accent-orange-500"
            />
            <input
                type="range"
                min={absMinCr}
                max={absMaxCr}
                value={maxCr}
                onChange={handleMaxSlider}
                className="w-full accent-orange-500"
            />
        </div>
    );
}
