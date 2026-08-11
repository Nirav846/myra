import { useState, useMemo, useRef, useEffect, useCallback } from 'react';
import { Librarian } from '../lib/Librarian';
import { Box, RefreshCw, AlertTriangle, ChevronDown, ChevronUp, ArrowUpDown, Download, X, List, LayoutGrid, Star } from 'lucide-react';
import { useDeliveryScanner, ScannerData, SummaryData } from '../hooks/useDeliveryScanner';
import MarketCapRangeFilter from '../components/MarketCapRangeFilter';
import { useWatchlist } from '../lib/WatchlistContext';
import { StarButton } from '../components/StarButton';
import ScrollableTable from '../components/ScrollableTable';

interface Preset { name: string; minDelivery: number; maxDelivery: number; minRelVol: number; lookbackDays?: number; isTrigger?: boolean }
const PRESETS: Preset[] = [
    { name: 'Accumulation', minDelivery: 60, maxDelivery: 100, minRelVol: 30 },
    { name: 'Breakout Setup', minDelivery: 40, maxDelivery: 100, minRelVol: 50 },
    { name: 'Quiet Buying', minDelivery: 55, maxDelivery: 75, minRelVol: 0 },
    { name: 'Active Setup', minDelivery: 40, maxDelivery: 95, minRelVol: 20, lookbackDays: 30, isTrigger: true },
];

function relativeTime(date: Date): string {
    const diff = Date.now() - date.getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins} min ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ${mins % 60}m ago`;
    return `${Math.floor(hours / 24)}d ago`;
}

function SortIcon({ column, sortConfig }: { column: string; sortConfig: { key: string; direction: 'asc' | 'desc' } | null }) {
    if (sortConfig?.key !== column) return <ArrowUpDown size={10} className="inline ml-1 opacity-30" />;
    return sortConfig.direction === 'asc'
        ? <ChevronUp size={10} className="inline ml-1 text-orange-400" />
        : <ChevronDown size={10} className="inline ml-1 text-orange-400" />;
}

function SummarySortIcon({ column, sortCol, sortAsc }: { column: string; sortCol: string; sortAsc: boolean }) {
    if (sortCol !== column) return <ArrowUpDown size={10} className="inline ml-1 opacity-30" />;
    return sortAsc
        ? <ChevronUp size={10} className="inline ml-1 text-orange-400" />
        : <ChevronDown size={10} className="inline ml-1 text-orange-400" />;
}

function ColTip({ text }: { text: string }) {
    return (
        <span title={text} className="inline-flex items-center justify-center w-3 h-3 rounded-full border border-[#777] text-[#888] hover:text-white hover:border-white cursor-help text-[12px] leading-none font-bold ml-0.5 transition-colors">?</span>
    );
}

export default function DeliveryAnomalyScanner({ lib, onNavigate }: { lib: Librarian, onNavigate?: (tab: string, symbol?: string) => void }) {
    const [mcapRange, setMcapRange] = useState<{ min: number; max: number } | null>(null);
    const [activePreset, setActivePreset] = useState<string | null>(null);
    const [columnsOpen, setColumnsOpen] = useState(false);
    const [filtersVisible, setFiltersVisible] = useState(() => localStorage.getItem('das_filters_visible') !== 'false');
    const { isWatched } = useWatchlist();
    const [watchlistOnly, setWatchlistOnly] = useState(false);
    const [viewMode, setViewMode] = useState<'detail' | 'summary'>('detail');
    const [summarySortCol, setSummarySortCol] = useState<keyof SummaryData>('persistence');
    const [summarySortAsc, setSummarySortAsc] = useState(false);

    const {
        sortedData,
        processedData,
        summaryData,
        isLoading,
        errorMsg,
        hasRun,
        lastScanned,
        fetchData,
        minDeliveryPct, setMinDeliveryPct,
        maxDeliveryPct, setMaxDeliveryPct,
        minRelVolScore, setMinRelVolScore,
        minDeliveryValueCr, setMinDeliveryValueCr,
        minVolumeToMcap, setMinVolumeToMcap,
        filterSector, setFilterSector,
        lookbackDays, setLookbackDays,
        symbolSearch, setSymbolSearch,
        filterBucket, setFilterBucket,
        sortConfig, setSortConfig,
        uniqueSectors,
        uniqueBuckets,
        stats,
        columnVisibility, setColumnVisibility,
        maxRelVolObserved,
        triggerMode, setTriggerMode,
        triggerMaxDays, setTriggerMaxDays,
        triggerMinStrength, setTriggerMinStrength,
        triggerMinComposite, setTriggerMinComposite,
        triggerMinReturn, setTriggerMinReturn,
        triggerRequirePersistence, setTriggerRequirePersistence,
        latestDataDate,
        triggerSortedData,
        triggerSummaryData,
    } = useDeliveryScanner(lib, mcapRange);

    const handleSort = (key: keyof ScannerData) => {
        setSortConfig(prev => {
            if (!prev) return { key, direction: 'desc' };
            return {
                key,
                direction: prev.key === key && prev.direction === 'desc' ? 'asc' : 'desc'
            };
        });
    };

    const handleSummarySort = (col: keyof SummaryData) => {
        if (summarySortCol === col) setSummarySortAsc(prev => !prev);
        else { setSummarySortCol(col); setSummarySortAsc(false); }
    };

    const sortedSummary = useMemo(() => {
        return [...summaryData].sort((a, b) => {
            const av = a[summarySortCol];
            const bv = b[summarySortCol];
            if (av == null) return 1;
            if (bv == null) return -1;
            if (typeof av === 'number' && typeof bv === 'number') {
                return summarySortAsc ? av - bv : bv - av;
            }
            if (typeof av === 'string' && typeof bv === 'string') {
                return summarySortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
            }
            return 0;
        });
    }, [summaryData, summarySortCol, summarySortAsc]);

    const triggerSortedSummary = useMemo(() => {
        return [...triggerSummaryData].sort((a, b) => {
            const av = a[summarySortCol];
            const bv = b[summarySortCol];
            if (av == null) return 1;
            if (bv == null) return -1;
            if (typeof av === 'number' && typeof bv === 'number') {
                return summarySortAsc ? av - bv : bv - av;
            }
            if (typeof av === 'string' && typeof bv === 'string') {
                return summarySortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
            }
            return 0;
        });
    }, [triggerSummaryData, summarySortCol, summarySortAsc]);

    const handleCSV = useCallback(() => {
        if (viewMode === 'summary') {
            const data = triggerMode ? triggerSortedSummary : sortedSummary;
            if (data.length === 0) return;
            const headers = ['Symbol', 'Sector', 'Bucket', 'Persistence', 'Latest Date', 'Highest Composite', 'Avg Delivery %', 'Avg Strength', 'Return Since Earliest %', 'Close', 'Volume'];
            const rows = data.map(r => [
                r.symbol, r.sector, r.bucket, r.persistence, r.latestDate,
                r.highestComposite.toFixed(1), r.avgDelivery.toFixed(1),
                r.avgStrength !== null ? r.avgStrength.toFixed(3) : '',
                r.returnSinceEarliest.toFixed(1), r.close.toFixed(2), r.volume
            ].join(','));
            const csv = [headers.join(','), ...rows].join('\n');
            const blob = new Blob([csv], { type: 'text/csv' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `delivery_anomaly_summary_${new Date().toISOString().split('T')[0]}.csv`;
            a.click();
            URL.revokeObjectURL(url);
        } else {
            const data = triggerMode ? triggerSortedData : sortedData;
            if (data.length === 0) return;
            const headers = ['Symbol', 'Date', 'Close', 'Return Since %', 'Delivery%', 'Divergence Score', 'Volatility Compression', 'Rel Volume Score', 'Nifty Outperformance', 'Composite Score', 'Volume', 'Sector', 'Del Value (₹ Cr)', 'Vol/Mcap %'];
            const rows = data.map(r => [
                r.symbol, r.date,
                r.close.toFixed(2), r.return_since.toFixed(2),
                r.delivery_pct.toFixed(2), r.delivery_divergence_score.toFixed(2),
                r.volatility_compression_score.toFixed(2), r.relative_volume_score.toFixed(2),
                r.nifty_outperformance_score.toFixed(2), r.composite_score.toFixed(2),
                r.volume, r.sector,
                r.delivery_value_cr.toFixed(2), r.volume_to_mcap_pct.toFixed(2)
            ].join(','));
            const csv = [headers.join(','), ...rows].join('\n');
            const blob = new Blob([csv], { type: 'text/csv' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `delivery_anomaly_${new Date().toISOString().split('T')[0]}.csv`;
            a.click();
            URL.revokeObjectURL(url);
        }
    }, [viewMode, triggerMode, triggerSortedSummary, sortedSummary, triggerSortedData, sortedData]);

    const applyPreset = (p: Preset) => {
        setMinDeliveryPct(p.minDelivery);
        setMaxDeliveryPct(p.maxDelivery);
        setMinRelVolScore(p.minRelVol);
        setFilterSector('All');
        setFilterBucket('All Caps');
        setSymbolSearch('');
        if (p.lookbackDays) setLookbackDays(p.lookbackDays);
        setActivePreset(p.name);
        if (p.isTrigger) {
            setTriggerMode(true);
            setTriggerMaxDays(5);
            setTriggerMinStrength(0.6);
            setTriggerMinComposite(8);
            setTriggerMinReturn(-5);
            setTriggerRequirePersistence(false);
        } else {
            setTriggerMode(false);
        }
    };

    const formatVolume = (v: number) => {
        if (v >= 1e7) return (v / 1e7).toFixed(1) + 'Cr';
        if (v >= 1e5) return (v / 1e5).toFixed(1) + 'L';
        return v.toLocaleString('en-IN');
    };

    const formatPrice = (v: number) => `\u20B9 ${Math.round(v).toLocaleString('en-IN')}`;

    const getMetricColor = (value: number, column: string): string => {
        switch (column) {
            case 'delivery_pct':
                if (value > 50) return 'text-green-400';
                if (value < 20) return 'text-red-400';
                return 'text-[#fafafa]';
            case 'nifty_outperformance_score':
                if (value > 0) return 'text-green-400';
                if (value < 0) return 'text-red-400';
                return 'text-[#aaa]';
            default:
                if (value > 0) return 'text-green-400';
                if (value < 0) return 'text-red-400';
                return 'text-[#aaa]';
        }
    };

    const ALWAYS_VISIBLE = ['symbol','date','close','delivery_pct','delivery_divergence_score','relative_volume_score','volume','delivery_value_cr','volume_to_mcap_pct'] as const;
    const OPTIONAL = ['return_since','composite_score','volatility_compression_score','nifty_outperformance_score','strength'] as const;
    const visibleColCount = ALWAYS_VISIBLE.length + OPTIONAL.filter(k => columnVisibility[k]).length;

    const columnsRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!columnsOpen) return;
        const handler = (e: MouseEvent) => {
            if (columnsRef.current && !columnsRef.current.contains(e.target as Node)) {
                setColumnsOpen(false);
            }
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, [columnsOpen]);

    const [, setTick] = useState(0);
    useEffect(() => {
        if (!lastScanned) return;
        const id = setInterval(() => setTick(t => t + 1), 60_000);
        return () => clearInterval(id);
    }, [lastScanned]);
    const isStale = lastScanned && (Date.now() - lastScanned.getTime() > 30 * 60 * 1000);

    return (
        <section className="bg-[#1e2028] border border-[#ffffff1a] rounded flex flex-col shadow-xl overflow-hidden flex-1 min-h-0 min-h-[600px]" aria-label="Delivery Anomaly Scanner">
            {/* Header */}
            <header className="px-6 py-4 border-b border-[#ffffff1a] flex justify-between items-center bg-[#1a1c24]">
                <div className="flex items-center gap-3">
                    <Box size={20} className="text-orange-400" aria-hidden="true" />
                    <h1 className="font-semibold text-[#fafafa] flex items-center gap-2 text-base">
                        Delivery Anomaly Scanner
                    </h1>
                    <div className="flex gap-2 items-center">
                        {errorMsg && (
                            <span className="text-[12px] bg-red-500/20 text-red-500 px-2 py-1 rounded font-mono border border-red-500/30 flex items-center gap-1" role="alert">
                                <AlertTriangle size={10} aria-hidden="true" /> {errorMsg}
                            </span>
                        )}
                    </div>
                </div>
                <div className="flex items-center gap-3">
                    {lastScanned && (
                        <span className="text-[12px] text-[#888] font-mono whitespace-nowrap">
                            Last scanned: {lastScanned ? relativeTime(lastScanned) : ''}
                        </span>
                    )}
                    {hasRun && (
                        <span className="text-[12px] text-[#888] font-mono whitespace-nowrap">
                            Results: <span className="text-orange-400 font-bold">{triggerMode ? triggerSortedData.length : processedData.length}</span>
                        </span>
                    )}
                    <button
                        onClick={() => setViewMode(v => v === 'detail' ? 'summary' : 'detail')}
                        className="bg-[#2a2c34] hover:bg-[#3a3c44] text-[#aaa] hover:text-white px-2 py-1 rounded border border-[#ffffff1a] transition-all flex items-center gap-1 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500/50"
                        aria-label={`Switch to ${viewMode === 'detail' ? 'summary' : 'detail'} view`}
                    >
                        {viewMode === 'detail' ? <LayoutGrid size={12} aria-hidden="true" /> : <List size={12} aria-hidden="true" />}
                        {viewMode === 'detail' ? 'Summary' : 'Detail'}
                    </button>
                    {viewMode === 'detail' && (
                    <div className="relative" ref={columnsRef}>
                        <button
                            onClick={() => setColumnsOpen(o => !o)}
                            className="bg-[#2a2c34] hover:bg-[#3a3c44] text-[#aaa] hover:text-white px-2 py-1 rounded border border-[#ffffff1a] transition-all flex items-center gap-1 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500/50"
                            aria-label="Toggle column visibility"
                            aria-expanded={columnsOpen}
                        >
                            Columns <ChevronDown size={10} aria-hidden="true" />
                        </button>
                        {columnsOpen && (
                            <div className="absolute right-0 top-full mt-1 z-50 bg-[#1a1c24] border border-[#ffffff1a] rounded shadow-xl p-2 min-w-[160px]">
                                    {[
                                        { key: 'composite_score', label: 'Composite' },
                                        { key: 'return_since', label: 'Return Since' },
                                        { key: 'volatility_compression_score', label: 'Vol Compression' },
                                        { key: 'nifty_outperformance_score', label: 'Nifty Outperform' },
                                        { key: 'strength', label: 'Strength' },
                                    ].map(col => (
                                        <label key={col.key} className="flex items-center gap-2 px-2 py-1.5 text-[12px] text-[#ccc] font-mono cursor-pointer hover:bg-[#ffffff0a] rounded transition-colors whitespace-nowrap">
                                            <input
                                                type="checkbox"
                                                checked={columnVisibility[col.key] ?? true}
                                                onChange={(e) => setColumnVisibility({ ...columnVisibility, [col.key]: e.target.checked })}
                                                className="accent-orange-500"
                                            />
                                            {col.label}
                                        </label>
                                    ))}
                                </div>
                    )}
                    </div>
                    )}
                    <button
                        onClick={handleCSV}
                        disabled={viewMode === 'detail' ? sortedData.length === 0 : sortedSummary.length === 0}
                        className="bg-[#2a2c34] hover:bg-[#3a3c44] text-[#aaa] hover:text-white px-2 py-1 rounded border border-[#ffffff1a] transition-all flex items-center gap-1 text-xs disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500/50"
                        aria-label="Export data as CSV"
                    >
                        <Download size={12} aria-hidden="true" />
                        CSV Export
                    </button>
                    <button
                        onClick={() => { const n = !filtersVisible; setFiltersVisible(n); localStorage.setItem('das_filters_visible', String(n)); }}
                        className={`px-2.5 py-1 rounded text-[12px] font-mono border transition-all flex items-center gap-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500/50 ${
                            filtersVisible
                                ? 'bg-[#2a2c34] border-[#ffffff3a] text-[#ccc]'
                                : 'bg-[#2a2c34] border-[#ffffff1a] text-[#888]'
                        }`}
                        title="Toggle filter controls"
                        aria-label="Toggle filter controls"
                        aria-expanded={filtersVisible}
                    >
                        Filters <ChevronDown size={10} className={`transition-transform ${filtersVisible ? '' : '-rotate-90'}`} aria-hidden="true" />
                    </button>
                    <button
                        onClick={fetchData}
                        disabled={isLoading}
                        className="flex items-center gap-2 px-4 h-[34px] bg-indigo-500/10 hover:bg-indigo-500/20 border border-indigo-500/30 rounded text-xs text-indigo-300 font-bold transition-all disabled:opacity-50 font-mono shadow-[0_0_15px_rgba(99,102,241,0.1)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/50"
                        aria-label={isLoading ? 'Scanning for delivery anomalies' : 'Scan for delivery anomalies'}
                    >
                        <RefreshCw size={12} className={isLoading ? "animate-spin" : ""} aria-hidden="true" />
                        {isLoading ? "Scanning..." : "Scan"}
                    </button>
                </div>
            </header>

            {/* Filter Presets */}
            <div className="bg-[#111318] border-b border-[#ffffff1a] px-4 py-2 flex items-center gap-2" role="radiogroup" aria-label="Filter presets">
                <span className="text-[12px] text-[#888] font-mono mr-1" aria-hidden="true">Presets:</span>
                {PRESETS.map(p => {
                    const active = activePreset === p.name;
                    return (
                        <button
                            key={p.name}
                            role="radio"
                            aria-checked={active}
                            aria-label={`${p.name} preset`}
                            onClick={() => applyPreset(p)}
                            className={`px-3 py-1 rounded-full text-[12px] font-mono whitespace-nowrap transition-all border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/50 ${
                                active
                                    ? 'border-cyan-500 bg-cyan-500/10 text-cyan-400'
                                    : 'border-[#ffffff1a] bg-[#1a1c24] text-[#aaa] hover:border-[#ffffff3a]'
                            }`}
                        >
                            {p.name}
                        </button>
                    );
                })}
                {activePreset && (
                    <button
                        onClick={() => { setActivePreset(null); setTriggerMode(false); }}
                        className="ml-1 text-[#888] hover:text-white transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/50 rounded"
                        title="Clear preset"
                        aria-label="Clear active preset"
                    >
                        <X size={12} aria-hidden="true" />
                    </button>
                )}
            </div>

            {/* Filter Row */}
            {filtersVisible && (
            <div className="bg-[#15171d] border-b border-[#ffffff1a] p-4">
                <div className="flex flex-wrap gap-4 items-end">
                    <div className="flex flex-col flex-shrink-0 w-[100px]">
                        <label className="text-[12px] text-[#888] font-mono mb-0.5" id="lookback-label">Lookback Days</label>
                        <input
                            type="number"
                            min={1}
                            max={90}
                            value={lookbackDays}
                            onChange={(e) => setLookbackDays(Number(e.target.value))}
                            className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full focus-visible:ring-2 focus-visible:ring-orange-500/50"
                            aria-labelledby="lookback-label"
                        />
                    </div>
                    <div className="flex flex-col flex-shrink-0 w-[130px]">
                        <label className="text-[12px] text-[#888] font-mono mb-0.5" id="search-symbol-label">Search Symbol</label>
                        <input
                            type="text"
                            value={symbolSearch}
                            onChange={(e) => setSymbolSearch(e.target.value)}
                            placeholder="e.g. RELIANCE"
                            className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full placeholder-[#888] focus-visible:ring-2 focus-visible:ring-orange-500/50"
                            aria-labelledby="search-symbol-label"
                        />
                    </div>
                    <div className="flex flex-col flex-shrink-0 w-[130px]">
                        <div className="flex justify-between text-[12px] text-[#888] font-mono mb-0.5">
                            <label id="min-del-label">Min Delivery %</label>
                            <span className="text-orange-400">{minDeliveryPct}%</span>
                        </div>
                        <input type="range" min="0" max="100" value={minDeliveryPct} onChange={(e) => setMinDeliveryPct(Number(e.target.value))} className="w-full accent-orange-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500/50" aria-labelledby="min-del-label" />
                    </div>
                    <div className="flex flex-col flex-shrink-0 w-[130px]">
                        <div className="flex justify-between text-[12px] text-[#888] font-mono mb-0.5">
                            <label id="max-del-label">Max Delivery %</label>
                            <span className="text-orange-400">{maxDeliveryPct}%</span>
                        </div>
                        <input type="range" min="0" max="100" value={maxDeliveryPct} onChange={(e) => setMaxDeliveryPct(Number(e.target.value))} className="w-full accent-orange-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500/50" aria-labelledby="max-del-label" />
                    </div>
                    <div className="flex flex-col flex-shrink-0 w-[130px]">
                        <div className="flex justify-between text-[12px] text-[#888] font-mono mb-0.5">
                            <label id="min-vol-label">Min Rel Vol Score</label>
                            <span className="text-orange-400">{minRelVolScore}</span>
                        </div>
                        <input
                            type="number"
                            min={0}
                            max={Math.ceil(maxRelVolObserved) || 1}
                            value={minRelVolScore}
                            onChange={(e) => setMinRelVolScore(Number(e.target.value))}
                            disabled={maxRelVolObserved === 0}
                            className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full disabled:opacity-40 focus-visible:ring-2 focus-visible:ring-orange-500/50"
                            aria-labelledby="min-vol-label"
                        />
                    </div>
                    <div className="flex flex-col flex-shrink-0 w-[130px]">
                        <div className="flex justify-between text-[12px] text-[#888] font-mono mb-0.5">
                            <label>Min Del Value (₹ Cr)</label>
                            <span className="text-orange-400">{minDeliveryValueCr > 0 ? `≥${minDeliveryValueCr}` : 'Off'}</span>
                        </div>
                        <input type="range" min="0" max="100" step="1" value={minDeliveryValueCr}
                            onChange={(e) => setMinDeliveryValueCr(Number(e.target.value))}
                            className="w-full accent-orange-500" />
                    </div>
                    <div className="flex flex-col flex-shrink-0 w-[130px]">
                        <div className="flex justify-between text-[12px] text-[#888] font-mono mb-0.5">
                            <label>Min Vol/Mcap %</label>
                            <span className="text-orange-400">{minVolumeToMcap > 0 ? `≥${minVolumeToMcap}%` : 'Off'}</span>
                        </div>
                        <input type="range" min="0" max="20" step="0.5" value={minVolumeToMcap}
                            onChange={(e) => setMinVolumeToMcap(Number(e.target.value))}
                            className="w-full accent-orange-500" />
                    </div>
                    <div className="flex flex-col flex-shrink-0 w-[130px]">
                        <label className="text-[12px] text-[#888] font-mono mb-1" id="sector-filter-label">Sector Filter</label>
                        <select value={filterSector} onChange={(e) => setFilterSector(e.target.value)} className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full focus-visible:ring-2 focus-visible:ring-orange-500/50" aria-labelledby="sector-filter-label">
                            <option value="All">All Sectors</option>
                            {uniqueSectors.map(s => <option key={s} value={s}>{s}</option>)}
                        </select>
                    </div>
                    <div className="flex flex-col flex-shrink-0 w-[140px]">
                        <label className="text-[12px] text-[#888] font-mono mb-1" id="bucket-filter-label">Bucket</label>
                        <select value={filterBucket} onChange={(e) => setFilterBucket(e.target.value)} className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full focus-visible:ring-2 focus-visible:ring-orange-500/50" aria-labelledby="bucket-filter-label">
                            <option value="All Caps">All Caps</option>
                            {uniqueBuckets.map(b => <option key={b} value={b}>{b}</option>)}
                        </select>
                    </div>
                    <div className="max-w-[280px] flex-shrink-0">
                        <MarketCapRangeFilter onChange={setMcapRange} />
                    </div>
                      <div className="flex flex-col self-end">
                         <button
                           onClick={() => setWatchlistOnly(o => !o)}
                           className={`flex items-center gap-1.5 px-3 py-1.5 rounded border text-[12px] font-mono transition-colors h-[32px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-yellow-500/50 ${
                             watchlistOnly
                               ? 'bg-yellow-500/20 border-yellow-500/40 text-yellow-400'
                               : 'bg-[#1a1c24] border-[#ffffff1a] text-[#888] hover:text-yellow-400'
                           }`}
                           aria-label={watchlistOnly ? 'Show all symbols' : 'Show watchlist only'}
                           aria-pressed={watchlistOnly}
                         >
                           <Star size={11} fill={watchlistOnly ? 'currentColor' : 'none'} aria-hidden="true" />
                           Watchlist
                         </button>
                       </div>
                     <div className="flex items-end gap-2 ml-auto">
                        {latestDataDate && (
                            <button
                                onClick={() => setTriggerMode(t => !t)}
                                disabled={!latestDataDate}
                                className={`px-3 py-1 rounded text-[12px] font-mono whitespace-nowrap border transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-500/50 ${
                                    triggerMode
                                        ? 'bg-green-500/10 border-green-500/50 text-green-400'
                                        : 'bg-[#2a2c34] border-[#ffffff1a] text-[#aaa] hover:border-[#ffffff3a]'
                                }`}
                                aria-label={`Toggle trigger mode: currently ${triggerMode ? 'ON' : 'OFF'}`}
                                aria-pressed={triggerMode}
                            >
                                Trigger Mode {triggerMode ? 'ON' : 'OFF'}
                            </button>
                        )}
                        {triggerMode && (
                            <>
                                <div className="flex flex-col flex-shrink-0 w-[100px]">
                                    <label className="text-[12px] text-[#888] font-mono mb-0.5" id="trigger-max-days-label">Max Days Since</label>
                                    <input
                                        type="number"
                                        min={1}
                                        max={90}
                                        value={triggerMaxDays}
                                        onChange={(e) => setTriggerMaxDays(Number(e.target.value))}
                                        className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full focus-visible:ring-2 focus-visible:ring-orange-500/50"
                                        aria-labelledby="trigger-max-days-label"
                                    />
                                </div>
                                <div className="flex flex-col flex-shrink-0 w-[120px]">
                                    <div className="flex justify-between text-[12px] text-[#888] font-mono mb-0.5">
                                        <label id="trigger-min-strength-label">Min Strength</label>
                                        <span className="text-orange-400">{triggerMinStrength.toFixed(2)}</span>
                                    </div>
                                    <input type="range" min="0" max="1" step="0.05" value={triggerMinStrength} onChange={(e) => setTriggerMinStrength(Number(e.target.value))} className="w-full accent-orange-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500/50" aria-labelledby="trigger-min-strength-label" />
                                </div>
                                <div className="flex flex-col flex-shrink-0 w-[100px]">
                                    <label className="text-[12px] text-[#888] font-mono mb-0.5" id="trigger-min-comp-label">Min Composite</label>
                                    <input
                                        type="number"
                                        min={0}
                                        max={100}
                                        step={1}
                                        value={triggerMinComposite}
                                        onChange={(e) => setTriggerMinComposite(Number(e.target.value))}
                                        className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full focus-visible:ring-2 focus-visible:ring-orange-500/50"
                                        aria-labelledby="trigger-min-comp-label"
                                    />
                                </div>
                                <div className="flex flex-col flex-shrink-0 w-[100px]">
                                    <label className="text-[12px] text-[#888] font-mono mb-0.5" id="trigger-min-return-label">Min Return %</label>
                                    <input
                                        type="number"
                                        step={1}
                                        value={triggerMinReturn}
                                        onChange={(e) => setTriggerMinReturn(Number(e.target.value))}
                                        className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full focus-visible:ring-2 focus-visible:ring-orange-500/50"
                                        aria-labelledby="trigger-min-return-label"
                                    />
                                </div>
                                <div className="flex flex-col flex-shrink-0">
                                    <label className="text-[12px] text-[#888] font-mono mb-0.5">Persistence</label>
                                    <label className="flex items-center gap-2 text-[12px] text-[#ccc] cursor-pointer h-[28px] hover:text-white transition-colors">
                                        <input
                                            type="checkbox"
                                            checked={triggerRequirePersistence}
                                            onChange={(e) => setTriggerRequirePersistence(e.target.checked)}
                                            className="accent-orange-500 focus-visible:ring-2 focus-visible:ring-orange-500/50"
                                        />
                                        Min 2 visits
                                    </label>
                                </div>
                            </>
                        )}
                    </div>
                </div>
            </div>
            )}

            {errorMsg && (
                <div className="bg-red-950/40 border-b border-red-500/50 px-4 py-3 flex items-start gap-3" role="alert">
                    <AlertTriangle className="text-red-400 flex-shrink-0 mt-0.5" size={16} aria-hidden="true" />
                    <div>
                        <h4 className="text-red-400 text-xs font-semibold mb-0.5">Query Error</h4>
                        <p className="text-[#ccc] text-[12px] font-mono">{errorMsg}</p>
                    </div>
                </div>
            )}

            {/* Stale Warning Banner */}
            {isStale && (
                <div className="bg-yellow-950/40 border-b border-yellow-500/50 px-4 py-2 flex items-center gap-2" role="status">
                    <AlertTriangle className="text-yellow-400 flex-shrink-0" size={14} aria-hidden="true" />
                    <span className="text-yellow-400 text-xs font-mono">Data may be stale — re-scan recommended</span>
                </div>
            )}

            {/* Summary Stats Bar */}
            {hasRun && !isLoading && processedData.length > 0 && (
                <div className="bg-[#15171d] border-b border-[#ffffff1a] px-4 py-1.5 flex items-center gap-4 text-xs text-[#ccc]" role="status" aria-live="polite">
                    <span className="flex items-center gap-1">
                        <span className="text-orange-400">{triggerMode ? triggerSortedData.length : stats.count}</span> {triggerMode ? 'active setups' : 'signals'}
                    </span>
                    <span className="text-[#ffffff1a]">|</span>
                    <span>Avg Delivery: <span className="text-[#ccc]">{stats.avgDeliveryPct}%</span></span>
                    <span className="text-[#ffffff1a]">|</span>
                    <span>Avg Return Since: <span className="text-[#ccc]">{stats.avgReturnSince}%</span></span>
                    <span className="text-[#ffffff1a]">|</span>
                    <span>Top Sector: <span className="text-[#ccc]">{stats.topSector}</span> <span className="text-[#888]">({stats.topSectorCount})</span></span>
                </div>
            )}

            {/* Table */}
            <div className="flex-1 min-h-0 overflow-hidden rounded">
                {isLoading ? (
                    <div className="p-8 text-center text-[#888] font-mono text-xs flex flex-col items-center justify-center h-64 gap-4" role="status" aria-live="polite">
                        <RefreshCw className="animate-spin text-orange-500/50" size={24} aria-hidden="true" />
                        Scanning for delivery anomalies...
                    </div>
                ) : (
                <ScrollableTable>
                    {viewMode === 'detail' ? (
                    <table className="w-full min-w-max whitespace-nowrap text-left border-collapse">
                        <thead className="sticky top-0 bg-[#1a1c24] z-10 shadow-sm border-b border-[#ffffff1a]">
                            <tr>
                                <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${sortConfig?.key === 'symbol' ? 'text-white' : ''}`} onClick={() => handleSort('symbol')} scope="col" aria-sort={sortConfig?.key === 'symbol' ? (sortConfig.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                                    Symbol <ColTip text="NSE symbol." /> <SortIcon column="symbol" sortConfig={sortConfig} />
                                </th>
                                <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${sortConfig?.key === 'date' ? 'text-white' : ''}`} onClick={() => handleSort('date')} scope="col" aria-sort={sortConfig?.key === 'date' ? (sortConfig.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                                    Date <ColTip text="Date of the anomaly." /> <SortIcon column="date" sortConfig={sortConfig} />
                                </th>
                                <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${sortConfig?.key === 'close' ? 'text-white' : ''}`} onClick={() => handleSort('close')} scope="col" aria-sort={sortConfig?.key === 'close' ? (sortConfig.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                                    Close <ColTip text="Most recent closing price for this symbol." /> <SortIcon column="close" sortConfig={sortConfig} />
                                </th>
                                {columnVisibility.return_since && (
                                    <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${sortConfig?.key === 'return_since' ? 'text-white' : ''}`} onClick={() => handleSort('return_since')} scope="col" aria-sort={sortConfig?.key === 'return_since' ? (sortConfig.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                                        Return Since <ColTip text="% change from anomaly date close to latest close. Forward validation of the anomaly signal." /> <SortIcon column="return_since" sortConfig={sortConfig} />
                                    </th>
                                )}
                                {columnVisibility.strength && (
                                    <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${sortConfig?.key === 'strength' ? 'text-white' : ''}`} onClick={() => handleSort('strength')} scope="col" aria-sort={sortConfig?.key === 'strength' ? (sortConfig.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                                        Strength <ColTip text="Where the stock closed within the day's range. High = accumulation, Low = distribution." /> <SortIcon column="strength" sortConfig={sortConfig} />
                                    </th>
                                )}
                                {columnVisibility.composite_score && (
                                    <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${sortConfig?.key === 'composite_score' ? 'text-white' : ''}`} onClick={() => handleSort('composite_score')} scope="col" aria-sort={sortConfig?.key === 'composite_score' ? (sortConfig.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                                        Composite <ColTip text="Composite anomaly score (weighted z-scores). Higher = stronger delivery/volume anomaly." /> <SortIcon column="composite_score" sortConfig={sortConfig} />
                                    </th>
                                )}
                                <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${sortConfig?.key === 'delivery_pct' ? 'text-white' : ''}`} onClick={() => handleSort('delivery_pct')} scope="col" aria-sort={sortConfig?.key === 'delivery_pct' ? (sortConfig.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                                    Delivery % <ColTip text="Percentage of traded volume that was delivered (not intraday). High values indicate strong hands accumulating." /> <SortIcon column="delivery_pct" sortConfig={sortConfig} />
                                </th>
                                <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${sortConfig?.key === 'delivery_divergence_score' ? 'text-white' : ''}`} onClick={() => handleSort('delivery_divergence_score')} scope="col" aria-sort={sortConfig?.key === 'delivery_divergence_score' ? (sortConfig.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                                    Divergence <ColTip text="Z-score of delivery divergence. Positive values mean delivery is unusually high relative to the stock's own history." /> <SortIcon column="delivery_divergence_score" sortConfig={sortConfig} />
                                </th>
                                {columnVisibility.volatility_compression_score && (
                                    <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${sortConfig?.key === 'volatility_compression_score' ? 'text-white' : ''}`} onClick={() => handleSort('volatility_compression_score')} scope="col" aria-sort={sortConfig?.key === 'volatility_compression_score' ? (sortConfig.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                                        Vol Compression <ColTip text="Z-score of volatility compression. High values mean the price range is tighter than normal — potential setup for expansion." /> <SortIcon column="volatility_compression_score" sortConfig={sortConfig} />
                                    </th>
                                )}
                                <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${sortConfig?.key === 'relative_volume_score' ? 'text-white' : ''}`} onClick={() => handleSort('relative_volume_score')} scope="col" aria-sort={sortConfig?.key === 'relative_volume_score' ? (sortConfig.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                                    Rel Vol Score <ColTip text="Z-score of relative volume. Positive values mean volume is above its rolling average." /> <SortIcon column="relative_volume_score" sortConfig={sortConfig} />
                                </th>
                                {columnVisibility.nifty_outperformance_score && (
                                    <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${sortConfig?.key === 'nifty_outperformance_score' ? 'text-white' : ''}`} onClick={() => handleSort('nifty_outperformance_score')} scope="col" aria-sort={sortConfig?.key === 'nifty_outperformance_score' ? (sortConfig.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                                        Nifty Outperform <ColTip text="Stock return minus Nifty return on that day. Positive = outperformed Nifty." /> <SortIcon column="nifty_outperformance_score" sortConfig={sortConfig} />
                                    </th>
                                )}
                                <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${sortConfig?.key === 'volume' ? 'text-white' : ''}`} onClick={() => handleSort('volume')} scope="col" aria-sort={sortConfig?.key === 'volume' ? (sortConfig.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                                    Volume <ColTip text="Total traded volume (shares)." /> <SortIcon column="volume" sortConfig={sortConfig} />
                                </th>
                                <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${sortConfig?.key === 'delivery_value_cr' ? 'text-white' : ''}`} onClick={() => handleSort('delivery_value_cr')} scope="col" aria-sort={sortConfig?.key === 'delivery_value_cr' ? (sortConfig.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                                    Del Value (₹ Cr) <ColTip text="Delivery quantity × close price, in Crores. Distinguishes genuine institutional flow from microcap noise." /> <SortIcon column="delivery_value_cr" sortConfig={sortConfig} />
                                </th>
                                <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${sortConfig?.key === 'volume_to_mcap_pct' ? 'text-white' : ''}`} onClick={() => handleSort('volume_to_mcap_pct')} scope="col" aria-sort={sortConfig?.key === 'volume_to_mcap_pct' ? (sortConfig.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                                    Vol/Mcap % <ColTip text="Volume as a % of market cap. Above 5% flags extreme turnover events. Above 1% is meaningful." /> <SortIcon column="volume_to_mcap_pct" sortConfig={sortConfig} />
                                </th>
                            </tr>
                        </thead>
                        <tbody>
                            {(triggerMode ? triggerSortedData : sortedData).length === 0 && hasRun ? (
                                <tr>
                                    <td colSpan={visibleColCount} className="p-8 text-center text-[#888] font-mono text-xs">
                                        {triggerMode
                                            ? 'No active setups match the current trigger filters. Try increasing Max Days Since Anomaly or lowering Min Strength.'
                                            : 'No delivery anomalies match your criteria.'}
                                    </td>
                                </tr>
                            ) : (triggerMode ? triggerSortedData : sortedData).length === 0 && !hasRun ? (
                                <tr>
                                    <td colSpan={visibleColCount} className="p-8 text-center text-[#888] font-mono text-xs">
                                        Click Scan to detect delivery anomalies.
                                    </td>
                                </tr>
                            ) : (triggerMode ? triggerSortedData : sortedData).filter(d => !watchlistOnly || isWatched(d.symbol)).map(d => (
                                <tr key={d.symbol + d.date} className="border-b border-[#ffffff0a] hover:bg-[#ffffff05] transition-colors group">
                                    <td className={`p-3 whitespace-nowrap${triggerMode ? ' border-l-2 border-l-green-500' : ''}`} scope="row">
                                        <div className="flex items-center gap-1.5">
                                          <StarButton symbol={d.symbol} size={11} />
                                          <span
                                              onClick={() => window.open(`/#/chart?symbol=${encodeURIComponent(d.symbol)}`, '_blank')}
                                              className="font-bold text-[#fafafa] cursor-pointer hover:text-orange-400 hover:underline inline-flex items-center gap-1 transition-colors"
                                          >
                                              {d.symbol}
                                          </span>
                                        </div>
                                    </td>
                                    <td className="p-3 text-[#ccc] text-sm font-mono whitespace-nowrap">{d.date}</td>
                                    <td className="p-3 text-sm font-mono whitespace-nowrap text-right text-[#fafafa]">{d.close > 0 ? formatPrice(d.close) : "\u2014"}</td>
                                    {columnVisibility.return_since && (
                                        <td className="p-3 text-sm font-mono whitespace-nowrap text-right">
                                            {d.anomaly_close > 0 && d.close > 0 ? (
                                                <span className={getMetricColor(d.return_since, 'return_since')}>
                                                    {d.return_since >= 0 ? '+' : ''}{d.return_since.toFixed(1)}%
                                                </span>
                                            ) : "\u2014"}
                                        </td>
                                    )}
                                    {columnVisibility.strength && (
                                        <td className="p-3 text-sm font-mono whitespace-nowrap text-right">
                                            {d.strength !== null ? (
                                                <span className={d.strength >= 0.7 ? 'text-green-400' : d.strength <= 0.3 ? 'text-red-400' : 'text-[#ccc]'}>
                                                    {d.strength.toFixed(3)}
                                                </span>
                                            ) : "\u2014"}
                                        </td>
                                    )}
                                    {columnVisibility.composite_score && (
                                        <td className="p-3 text-sm font-mono whitespace-nowrap text-right">
                                            <span className="inline-flex items-center gap-1">
                                                <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[12px] font-bold ${d.composite_badge.className}`}>
                                                    {d.composite_badge.text}
                                                </span>
                                                {triggerMode && (
                                                    <span className="px-1.5 py-0.5 rounded text-[12px] font-bold bg-green-500/20 text-green-400 border border-green-500/30">
                                                        ACTIVE
                                                    </span>
                                                )}
                                            </span>
                                        </td>
                                    )}
                                    <td className="p-3 text-sm font-mono whitespace-nowrap text-right">
                                        <span className={getMetricColor(d.delivery_pct, 'delivery_pct')}>{d.delivery_pct.toFixed(1)}%</span>
                                    </td>
                                    <td className="p-3 text-sm font-mono whitespace-nowrap text-right">
                                        <span className={getMetricColor(d.delivery_divergence_score, 'delivery_divergence_score')}>{d.delivery_divergence_score.toFixed(1)}</span>
                                    </td>
                                    {columnVisibility.volatility_compression_score && (
                                        <td className="p-3 text-sm font-mono whitespace-nowrap text-right">
                                            <span className={getMetricColor(d.volatility_compression_score, 'volatility_compression_score')}>{d.volatility_compression_score.toFixed(1)}</span>
                                        </td>
                                    )}
                                    <td className="p-3 text-sm font-mono whitespace-nowrap text-right">
                                        <span className={getMetricColor(d.relative_volume_score, 'relative_volume_score')}>{d.relative_volume_score.toFixed(1)}</span>
                                    </td>
                                    {columnVisibility.nifty_outperformance_score && (
                                        <td className="p-3 text-sm font-mono whitespace-nowrap text-right">
                                            <span className={getMetricColor(d.nifty_outperformance_score, 'nifty_outperformance_score')}>{d.nifty_outperformance_score >= 0 ? '+' : ''}{d.nifty_outperformance_score.toFixed(1)}</span>
                                        </td>
                                    )}
                                    <td className="p-3 text-sm font-mono whitespace-nowrap text-right text-[#ccc]">
                                        {formatVolume(d.volume)}
                                    </td>
                                    <td className="p-3 text-sm font-mono whitespace-nowrap text-right text-[#ccc]">
                                        {d.delivery_value_cr >= 10
                                            ? `₹${d.delivery_value_cr.toFixed(1)} Cr`
                                            : d.delivery_value_cr >= 0.01
                                                ? `₹${d.delivery_value_cr.toFixed(2)} Cr`
                                                : '—'}
                                    </td>
                                    <td className="p-3 text-sm font-mono whitespace-nowrap text-right">
                                        <span className={d.volume_to_mcap_pct > 5 ? 'text-orange-400' : d.volume_to_mcap_pct > 1 ? 'text-[#fafafa]' : 'text-[#888]'}>
                                            {d.volume_to_mcap_pct > 0 ? `${d.volume_to_mcap_pct.toFixed(2)}%` : '—'}
                                        </span>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                ) : (
                    <table className="w-full min-w-max whitespace-nowrap text-left border-collapse">
                        <thead className="sticky top-0 bg-[#1a1c24] z-10 shadow-sm border-b border-[#ffffff1a]">
                            <tr>
                                <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${summarySortCol === 'symbol' ? 'text-white' : ''}`} onClick={() => handleSummarySort('symbol')} scope="col" aria-sort={summarySortCol === 'symbol' ? (summarySortAsc ? 'ascending' : 'descending') : 'none'}>
                                    Symbol <SummarySortIcon column="symbol" sortCol={summarySortCol} sortAsc={summarySortAsc} />
                                </th>
                                <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${summarySortCol === 'sector' ? 'text-white' : ''}`} onClick={() => handleSummarySort('sector')} scope="col" aria-sort={summarySortCol === 'sector' ? (summarySortAsc ? 'ascending' : 'descending') : 'none'}>
                                    Sector <SummarySortIcon column="sector" sortCol={summarySortCol} sortAsc={summarySortAsc} />
                                </th>
                                <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${summarySortCol === 'bucket' ? 'text-white' : ''}`} onClick={() => handleSummarySort('bucket')} scope="col" aria-sort={summarySortCol === 'bucket' ? (summarySortAsc ? 'ascending' : 'descending') : 'none'}>
                                    Bucket <SummarySortIcon column="bucket" sortCol={summarySortCol} sortAsc={summarySortAsc} />
                                </th>
                                <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${summarySortCol === 'persistence' ? 'text-white' : ''}`} onClick={() => handleSummarySort('persistence')} scope="col" aria-sort={summarySortCol === 'persistence' ? (summarySortAsc ? 'ascending' : 'descending') : 'none'}>
                                    Persistence <ColTip text="Number of anomaly days for this symbol." /> <SummarySortIcon column="persistence" sortCol={summarySortCol} sortAsc={summarySortAsc} />
                                </th>
                                <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${summarySortCol === 'latestDate' ? 'text-white' : ''}`} onClick={() => handleSummarySort('latestDate')} scope="col" aria-sort={summarySortCol === 'latestDate' ? (summarySortAsc ? 'ascending' : 'descending') : 'none'}>
                                    Latest Date <SummarySortIcon column="latestDate" sortCol={summarySortCol} sortAsc={summarySortAsc} />
                                </th>
                                <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${summarySortCol === 'highestComposite' ? 'text-white' : ''}`} onClick={() => handleSummarySort('highestComposite')} scope="col" aria-sort={summarySortCol === 'highestComposite' ? (summarySortAsc ? 'ascending' : 'descending') : 'none'}>
                                    Highest Composite <SummarySortIcon column="highestComposite" sortCol={summarySortCol} sortAsc={summarySortAsc} />
                                </th>
                                <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${summarySortCol === 'avgDelivery' ? 'text-white' : ''}`} onClick={() => handleSummarySort('avgDelivery')} scope="col" aria-sort={summarySortCol === 'avgDelivery' ? (summarySortAsc ? 'ascending' : 'descending') : 'none'}>
                                    Avg Delivery % <SummarySortIcon column="avgDelivery" sortCol={summarySortCol} sortAsc={summarySortAsc} />
                                </th>
                                <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${summarySortCol === 'avgStrength' ? 'text-white' : ''}`} onClick={() => handleSummarySort('avgStrength')} scope="col" aria-sort={summarySortCol === 'avgStrength' ? (summarySortAsc ? 'ascending' : 'descending') : 'none'}>
                                    Avg Strength <ColTip text="Average anomaly-day strength across all anomaly rows." /> <SummarySortIcon column="avgStrength" sortCol={summarySortCol} sortAsc={summarySortAsc} />
                                </th>
                                <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${summarySortCol === 'returnSinceEarliest' ? 'text-white' : ''}`} onClick={() => handleSummarySort('returnSinceEarliest')} scope="col" aria-sort={summarySortCol === 'returnSinceEarliest' ? (summarySortAsc ? 'ascending' : 'descending') : 'none'}>
                                    Return Since Earliest <SummarySortIcon column="returnSinceEarliest" sortCol={summarySortCol} sortAsc={summarySortAsc} />
                                </th>
                                <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${summarySortCol === 'close' ? 'text-white' : ''}`} onClick={() => handleSummarySort('close')} scope="col" aria-sort={summarySortCol === 'close' ? (summarySortAsc ? 'ascending' : 'descending') : 'none'}>
                                    Close <SummarySortIcon column="close" sortCol={summarySortCol} sortAsc={summarySortAsc} />
                                </th>
                                <th className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-orange-500/50 ${summarySortCol === 'volume' ? 'text-white' : ''}`} onClick={() => handleSummarySort('volume')} scope="col" aria-sort={summarySortCol === 'volume' ? (summarySortAsc ? 'ascending' : 'descending') : 'none'}>
                                    Volume <SummarySortIcon column="volume" sortCol={summarySortCol} sortAsc={summarySortAsc} />
                                </th>
                            </tr>
                        </thead>
                        <tbody>
                            {(triggerMode ? triggerSortedSummary : sortedSummary).length === 0 ? (
                                <tr>
                                    <td colSpan={11} className="p-8 text-center text-[#888] font-mono text-xs">
                                        {triggerMode
                                            ? 'No active setups match the current trigger filters. Try increasing Max Days Since Anomaly or lowering Min Strength.'
                                            : 'No symbols match your criteria.'}
                                    </td>
                                </tr>
                            ) : (
                                (triggerMode ? triggerSortedSummary : sortedSummary).filter(d => !watchlistOnly || isWatched(d.symbol)).map(d => (
                                    <tr key={d.symbol} className="border-b border-[#ffffff0a] hover:bg-[#ffffff05] transition-colors group">
                                        <td className={`p-3 whitespace-nowrap${triggerMode ? ' border-l-2 border-l-green-500' : ''}`} scope="row">
                                            <div className="flex items-center gap-1.5">
                                              <StarButton symbol={d.symbol} size={11} />
                                              <span
                                                  onClick={() => window.open(`/#/chart?symbol=${encodeURIComponent(d.symbol)}`, '_blank')}
                                                  className="font-bold text-[#fafafa] cursor-pointer hover:text-orange-400 hover:underline inline-flex items-center gap-1 transition-colors"
                                              >
                                                  {d.symbol}
                                              </span>
                                            </div>
                                        </td>
                                        <td className="p-3 text-[#ccc] text-sm whitespace-nowrap">{d.sector}</td>
                                        <td className="p-3 text-[#888] text-xs font-mono whitespace-nowrap">{d.bucket}</td>
                                        <td className="p-3 text-sm font-mono whitespace-nowrap text-right">
                                            <span className={d.persistence > 1 ? 'text-orange-400 font-bold' : 'text-[#ccc]'}>{d.persistence}</span>
                                        </td>
                                        <td className="p-3 text-[#ccc] text-sm font-mono whitespace-nowrap text-right">{d.latestDate}</td>
                                        <td className="p-3 text-sm font-mono whitespace-nowrap text-right">
                                            <span className="inline-flex items-center gap-1">
                                                <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[12px] font-bold ${d.highestBadge.className}`}>
                                                    {d.highestBadge.text}
                                                </span>
                                                {triggerMode && (
                                                    <span className="px-1.5 py-0.5 rounded text-[12px] font-bold bg-green-500/20 text-green-400 border border-green-500/30">
                                                        ACTIVE
                                                    </span>
                                                )}
                                            </span>
                                        </td>
                                        <td className="p-3 text-sm font-mono whitespace-nowrap text-right">
                                            <span className={d.avgDelivery > 50 ? 'text-green-400' : d.avgDelivery < 20 ? 'text-red-400' : 'text-[#ccc]'}>{d.avgDelivery.toFixed(1)}%</span>
                                        </td>
                                        <td className="p-3 text-sm font-mono whitespace-nowrap text-right">
                                            {d.avgStrength !== null ? (
                                                <span className={d.avgStrength >= 0.7 ? 'text-green-400' : d.avgStrength <= 0.3 ? 'text-red-400' : 'text-[#ccc]'}>
                                                    {d.avgStrength.toFixed(3)}
                                                </span>
                                            ) : "\u2014"}
                                        </td>
                                        <td className="p-3 text-sm font-mono whitespace-nowrap text-right">
                                            <span className={d.returnSinceEarliest > 0 ? 'text-green-400' : d.returnSinceEarliest < 0 ? 'text-red-400' : 'text-[#aaa]'}>
                                                {d.returnSinceEarliest >= 0 ? '+' : ''}{d.returnSinceEarliest.toFixed(1)}%
                                            </span>
                                        </td>
                                        <td className="p-3 text-sm font-mono whitespace-nowrap text-right text-[#fafafa]">{d.close > 0 ? formatPrice(d.close) : "\u2014"}</td>
                                        <td className="p-3 text-sm font-mono whitespace-nowrap text-right text-[#ccc]">{formatVolume(d.volume)}</td>
                                    </tr>
                                ))
                            )}
                        </tbody>
                    </table>
                    )}
                </ScrollableTable>
                )}
            </div>
        </section>
    );
}
