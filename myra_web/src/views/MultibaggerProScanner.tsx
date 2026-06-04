import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Librarian } from '../lib/Librarian';
import { Rocket, Filter, AlertTriangle, ArrowUpRight, RefreshCw, CheckCircle, Clock, XCircle, Download, ChevronUp, ChevronDown, ArrowUpDown } from 'lucide-react';
import MarketCapRangeFilter from '../components/MarketCapRangeFilter';
import { fetchMarketCapMap } from '../lib/marketCapCache';
import { useWatchlist } from '../lib/WatchlistContext';
import { StarButton } from '../components/StarButton';
import { API_BASE } from '../config';

interface Candidate {
  symbol: string;
  market_cap_cr: number;
  base_days: number;
  dar_median: number;
  base_range_pct: number;
  volume_ratio: number;
  delivery_slope: number;
  composite_score: number;
  grade: string;
  entry: number;
  sl: number;
  t1: number;
  t2: number;
  t3: number | null;
  status: string;
  close: number;
}

interface ScanStatus {
  scan_status: string;
  last_scan: string | null;
  progress: number;
  message: string;
  candidates: Candidate[];
  bear_market?: boolean;
}

function relativeTime(dateStr: string | null | undefined): string {
  if (!dateStr) return 'Never';
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return 'Never';
    const diffMs = Date.now() - d.getTime();
    if (diffMs < 0) return 'Just now';
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return 'Just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  } catch {
    return dateStr || 'Never';
  }
}

const GRADE_COLORS: Record<string, string> = {
  A: 'bg-green-500/20 text-green-400 border-green-500/30',
  B: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  C: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  D: 'bg-red-500/20 text-red-400 border-red-500/30',
};

const STATUS_COLORS: Record<string, string> = {
  'In Base': 'text-[#aaa]',
  'Breakout Pending': 'text-yellow-400',
  'Triggered': 'text-green-400',
  'Invalidated': 'text-red-400',
};

export default function MultibaggerProScannerView({ lib }: { lib: Librarian }) {
  const [scanStatus, setScanStatus] = useState<ScanStatus | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [staleBannerOpen, setStaleBannerOpen] = useState(true);
  const [bearMarket, setBearMarket] = useState(false);

  const [baseDays, setBaseDays] = useState(21);
  const [minDar, setMinDar] = useState(0.2);
  const [targetDar, setTargetDar] = useState<number | null>(null);
  const [tightnessFull, setTightnessFull] = useState<number | null>(null);
  const [tightnessZero, setTightnessZero] = useState<number | null>(null);
  const [mcapRange, setMcapRange] = useState<{ min: number; max: number } | null>(null);
  const mcapMapRef = useRef<Map<string, number>>(new Map());

  const { isWatched } = useWatchlist();
  const [watchlistOnly, setWatchlistOnly] = useState(false);

  const [sortCol, setSortCol] = useState<string>('composite_score');
  const [sortAsc, setSortAsc] = useState(false);

  useEffect(() => { fetchMarketCapMap().then(m => mcapMapRef.current = m); }, []);

  const mountedRef = useRef(true);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const candidates = scanStatus?.candidates ?? [];

  const filteredData = useMemo(() => {
    let data = [...candidates];
    if (mcapRange) {
      const map = mcapMapRef.current;
      data = data.filter(d => {
        const mcap = map.get(d.symbol);
        return mcap !== undefined && mcap >= mcapRange.min && mcap <= mcapRange.max;
      });
    }
    if (watchlistOnly) data = data.filter(d => isWatched(d.symbol));
    data.sort((a, b) => {
      const av = (a as any)[sortCol] ?? 0;
      const bv = (b as any)[sortCol] ?? 0;
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortAsc ? av - bv : bv - av;
      }
      return String(av).localeCompare(String(bv)) * (sortAsc ? 1 : -1);
    });
    return data;
  }, [candidates, mcapRange, watchlistOnly, isWatched, sortCol, sortAsc]);

  const clearPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const handleSort = (col: string) => {
    if (sortCol === col) setSortAsc(s => !s);
    else { setSortCol(col); setSortAsc(false); }
  };

  const SortIcon = ({ column }: { column: string }) => {
    if (sortCol !== column) return <ArrowUpDown size={10} className="inline ml-1 opacity-30" />;
    return sortAsc
      ? <ChevronUp size={10} className="inline ml-1 text-cyan-400" />
      : <ChevronDown size={10} className="inline ml-1 text-cyan-400" />;
  };

  const fetchScanStatus = useCallback(async () => {
    if (!mountedRef.current) return;
    try {
      const res = await fetch(`${API_BASE}/multibagger/status`);
      if (!mountedRef.current) return;
      if (res.ok) {
        const data: ScanStatus = await res.json();
        if (!mountedRef.current) return;
        setScanStatus(data);
        setBearMarket(data.bear_market ?? false);
        setError(null);

        if (data.scan_status === 'completed' || data.scan_status === 'error') {
          clearPolling();
          setIsScanning(false);
        } else if (data.scan_status === 'scanning' && !pollTimerRef.current) {
          pollTimerRef.current = setInterval(fetchScanStatus, 2000);
          setIsScanning(true);
        }
      }
    } catch (e: any) {
      if (mountedRef.current) {
        setError(e.message || 'Error connecting to backend');
      }
    }
  }, [clearPolling]);

  const startScan = useCallback(async () => {
    if (!mountedRef.current) return;
    setIsScanning(true);
    setError(null);
    clearPolling();

    try {
      const res = await fetch(`${API_BASE}/multibagger/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base_days: baseDays, min_dar: minDar, target_dar: targetDar, tightness_full_score_pct: tightnessFull, tightness_zero_score_pct: tightnessZero }),
      });
      if (!mountedRef.current) return;
      if (res.ok) {
        await fetchScanStatus();
        pollTimerRef.current = setInterval(fetchScanStatus, 2000);
      } else {
        const err = await res.json().catch(() => ({ detail: 'Failed to start scan' }));
        setError(err.detail || 'Failed to start scan');
        setIsScanning(false);
      }
    } catch (e: any) {
      if (mountedRef.current) {
        setError(e.message || 'Error connecting to backend');
        setIsScanning(false);
      }
    }
  }, [fetchScanStatus, clearPolling, baseDays, minDar, targetDar, tightnessFull, tightnessZero]);

  useEffect(() => {
    mountedRef.current = true;
    fetchScanStatus();
    return () => {
      mountedRef.current = false;
      clearPolling();
    };
  }, [fetchScanStatus, clearPolling]);

  const isStale = scanStatus?.last_scan && (Date.now() - new Date(scanStatus.last_scan).getTime() > 30 * 60 * 1000);

  const handleCSV = () => {
    if (filteredData.length === 0) return;
    const headers = ['Symbol', 'Market Cap Cr', 'Base Days', 'DAR Median', 'Base Range %', 'Volume Ratio', 'Delivery Slope', 'Composite Score', 'Grade', 'Entry', 'SL', 'T1', 'T2', 'T3', 'Status'];
    const rows = filteredData.map(r => [
      r.symbol, r.market_cap_cr, r.base_days, r.dar_median, r.base_range_pct,
      r.volume_ratio, r.delivery_slope, r.composite_score, r.grade,
      r.entry, r.sl, r.t1, r.t2, r.t3 ?? '', r.status,
    ].join(','));
    const csv = [headers.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `multibagger_pro_${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const progressPct = scanStatus?.progress ?? 0;
  const isIdle = scanStatus?.scan_status === 'idle' || !scanStatus;

  return (
    <div className="flex flex-col h-full relative space-y-4 p-4">
      {/* Staleness Warning */}
      {isStale && staleBannerOpen && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded px-4 py-2 flex items-center gap-2 text-xs font-mono">
          <AlertTriangle size={14} className="text-amber-400 shrink-0" />
          <span className="text-amber-300/90">Data may be stale — re-scan recommended (last scan &gt; 30 min ago).</span>
          <button onClick={() => setStaleBannerOpen(false)} className="ml-auto text-amber-500/50 hover:text-amber-300">
            <XCircle size={14} />
          </button>
        </div>
      )}

      {/* Header */}
      <div className="flex justify-between items-center bg-[#1a1c24] border border-[#ffffff1a] rounded p-4">
        <div className="flex items-center gap-3">
          <div className="bg-purple-500/20 p-2 rounded">
            <Rocket className="text-purple-400" size={24} />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-[#fafafa]">Multibagger Pro Scanner</h1>
            <div className="flex items-center gap-2">
              <p className="text-xs font-mono text-[#888]">Accumulation Base Breakout Detection</p>
              {bearMarket && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold border border-orange-500/40 bg-orange-500/15 text-orange-400" title="Minimum thresholds raised: base_days≥30, min_dar≥0.4%">
                  ⚠ Risk-Off
                </span>
              )}
            </div>
          </div>
        </div>
        <button
          onClick={startScan}
          disabled={isScanning}
          className="px-4 py-2 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white rounded text-xs font-semibold flex items-center gap-2 transition-colors"
        >
          {isScanning ? (
            <><RefreshCw size={14} className="animate-spin" /> Scanning...</>
          ) : (
            <><Rocket size={14} fill="currentColor" /> Scan</>
          )}
        </button>
      </div>

      {/* Progress / Status Bar */}
      {isScanning && (
        <div className="bg-cyan-500/10 border border-cyan-500/30 rounded p-3">
          <div className="flex items-center gap-2 text-xs font-mono text-cyan-300 mb-2">
            <RefreshCw size={14} className="animate-spin" />
            <span>{scanStatus?.message || 'Scanning...'}</span>
            <span className="ml-auto">{progressPct}%</span>
          </div>
          <div className="w-full h-1.5 bg-[#ffffff1a] rounded-full overflow-hidden">
            <div className="h-full bg-cyan-500 rounded-full transition-all duration-500" style={{ width: `${Math.max(progressPct, 5)}%` }} />
          </div>
        </div>
      )}

      {!isScanning && scanStatus && scanStatus.scan_status !== 'idle' && (
        <div className={`flex items-center gap-2 px-3 py-2 rounded text-xs font-mono border ${
          scanStatus.scan_status === 'completed' ? 'bg-green-500/10 border-green-500/30 text-green-300' :
          scanStatus.scan_status === 'error' ? 'bg-red-500/10 border-red-500/30 text-red-300' :
          'bg-[#ffffff0a] border-[#ffffff1a] text-[#888]'
        }`}>
          {scanStatus.scan_status === 'completed' ? <CheckCircle size={14} className="text-green-400" /> :
           scanStatus.scan_status === 'error' ? <XCircle size={14} className="text-red-400" /> :
           <Clock size={14} />}
          <span>
            {scanStatus.scan_status === 'completed' ? `Completed (${relativeTime(scanStatus.last_scan)})` :
             scanStatus.scan_status === 'error' ? 'Scan failed' :
             scanStatus.message}
          </span>
          <span className="ml-auto text-[#666]">{scanStatus.message}</span>
        </div>
      )}

      {error && !isScanning && (
        <div className="bg-red-500/10 border border-red-500/30 rounded px-4 py-2 flex items-center gap-2 text-xs font-mono text-red-300">
          <AlertTriangle size={14} className="shrink-0" />
          <span>Error: {error}</span>
        </div>
      )}

      {/* Results */}
      {(scanStatus?.scan_status === 'completed' || (isIdle && candidates.length > 0)) && !isScanning && (
        <>
          {/* Filters */}
          <div className="bg-[#0e1117] border border-[#ffffff1a] rounded p-4 flex flex-wrap gap-4 items-end">
            <div className="flex items-center gap-2 mb-1 text-xs text-[#888] w-full">
              <Filter size={14} /> <span className="font-mono uppercase font-semibold">Filters</span>
            </div>
            <div className="flex flex-col gap-1 w-24">
              <label className="text-[10px] text-[#888] font-mono">Lookback Days</label>
              <input
                type="number"
                min={7}
                max={90}
                value={baseDays}
                onChange={e => setBaseDays(Number(e.target.value))}
                className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-purple-500 outline-none w-full font-mono"
              />
            </div>
            <div className="flex flex-col gap-1 w-24">
              <label className="text-[10px] text-[#888] font-mono">Min DAR %</label>
              <input
                type="number"
                min={0}
                max={10}
                step={0.1}
                value={minDar}
                onChange={e => setMinDar(Number(e.target.value))}
                className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-purple-500 outline-none w-full font-mono"
              />
            </div>
            <div className="flex flex-col gap-1 w-24">
              <label className="text-[10px] text-[#888] font-mono">Target DAR %</label>
              {targetDar !== null ? (
                <div className="flex items-center gap-1">
                  <input
                    type="number"
                    min={0.1}
                    max={2.0}
                    step={0.1}
                    value={targetDar}
                    onChange={e => setTargetDar(Number(e.target.value))}
                    className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-purple-500 outline-none w-full font-mono"
                  />
                  <button
                    onClick={() => setTargetDar(null)}
                    className="text-[9px] text-purple-400 hover:text-purple-300 font-mono shrink-0"
                  >
                    Reset
                  </button>
                </div>
              ) : (
                <div
                  onClick={() => setTargetDar(0.5)}
                  className="bg-[#1a1c24] border border-purple-500/30 rounded px-2 py-1.5 text-xs text-purple-400 font-mono cursor-pointer text-center"
                >
                  Auto
                </div>
              )}
            </div>
            <div className="flex flex-col gap-1 w-28">
              <label className="text-[10px] text-[#888] font-mono">Tightness Full %</label>
              <input
                type="range"
                min={2}
                max={20}
                step={0.5}
                value={tightnessFull ?? 2}
                onChange={e => setTightnessFull(Number(e.target.value))}
                className="w-full accent-purple-500"
              />
              <div className="flex items-center justify-between">
                {tightnessFull !== null ? (
                  <>
                    <span className="text-[10px] text-[#ccc] font-mono">{tightnessFull.toFixed(1)}</span>
                    <button onClick={() => setTightnessFull(null)} className="text-[9px] text-purple-400 hover:text-purple-300 font-mono">Reset</button>
                  </>
                ) : (
                  <span className="text-[10px] text-purple-400 font-mono">Auto</span>
                )}
              </div>
            </div>
            <div className="flex flex-col gap-1 w-28">
              <label className="text-[10px] text-[#888] font-mono">Tightness Zero %</label>
              <input
                type="range"
                min={10}
                max={50}
                step={0.5}
                value={tightnessZero ?? 10}
                onChange={e => setTightnessZero(Number(e.target.value))}
                className="w-full accent-purple-500"
              />
              <div className="flex items-center justify-between">
                {tightnessZero !== null ? (
                  <>
                    <span className="text-[10px] text-[#ccc] font-mono">{tightnessZero.toFixed(1)}</span>
                    <button onClick={() => setTightnessZero(null)} className="text-[9px] text-purple-400 hover:text-purple-300 font-mono">Reset</button>
                  </>
                ) : (
                  <span className="text-[10px] text-purple-400 font-mono">Auto</span>
                )}
              </div>
            </div>
            <div className="max-w-[220px] flex-shrink-0">
              <MarketCapRangeFilter onChange={setMcapRange} />
            </div>
            <div className="flex flex-col gap-1">
              <div className="text-[10px] text-[#888] font-mono">Watchlist</div>
              <button
                onClick={() => setWatchlistOnly(o => !o)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded border text-[11px] font-mono transition-colors ${
                  watchlistOnly
                    ? 'bg-yellow-500/20 border-yellow-500/40 text-yellow-400'
                    : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#888] hover:text-yellow-400'
                }`}
              >
                <Rocket size={11} fill={watchlistOnly ? 'currentColor' : 'none'} />
                Only Starred
              </button>
            </div>
            <button
              onClick={handleCSV}
              disabled={filteredData.length === 0}
              className="ml-auto flex items-center gap-1.5 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-[#ccc] transition-colors disabled:opacity-40"
            >
              <Download size={12} />
              CSV
            </button>
          </div>

          {/* Stats Summary */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider">Candidates</div>
              <div className="text-2xl font-bold text-[#fafafa]">{filteredData.length}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider">Grade A</div>
              <div className="text-2xl font-bold text-green-400">{filteredData.filter(d => d.grade === 'A').length}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider">Avg Score</div>
              <div className="text-2xl font-bold text-cyan-400">
                {filteredData.length > 0
                  ? (filteredData.reduce((s, d) => s + d.composite_score, 0) / filteredData.length).toFixed(1)
                  : '—'}
              </div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider">Triggered</div>
              <div className="text-2xl font-bold text-yellow-400">{filteredData.filter(d => d.status === 'Triggered' || d.status === 'Breakout Pending').length}</div>
            </div>
          </div>

          {/* Table */}
          <div className="flex-1 bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden flex flex-col">
            <div className="overflow-x-auto flex-1">
              <table className="w-full text-left text-xs font-mono whitespace-nowrap">
                <thead className="bg-[#0e1117] text-[#888] sticky top-0">
                  <tr>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider cursor-pointer hover:text-white" onClick={() => handleSort('symbol')}>Symbol <SortIcon column="symbol" /></th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('market_cap_cr')}>Mkt Cap <SortIcon column="market_cap_cr" /></th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('base_days')}>Base Days <SortIcon column="base_days" /></th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('dar_median')}>DAR Med <SortIcon column="dar_median" /></th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('base_range_pct')}>Range % <SortIcon column="base_range_pct" /></th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('volume_ratio')}>Vol Ratio <SortIcon column="volume_ratio" /></th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('delivery_slope')}>Del Slope <SortIcon column="delivery_slope" /></th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('composite_score')}>Score <SortIcon column="composite_score" /></th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white" onClick={() => handleSort('grade')}>Grade <SortIcon column="grade" /></th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">Entry</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">SL</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">T1</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">T2</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">T3</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-center">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#ffffff0a]">
                  {filteredData.length === 0 ? (
                    <tr>
                      <td colSpan={15} className="px-4 py-8 text-center text-[#666]">No candidates match current filters.</td>
                    </tr>
                  ) : (
                    filteredData.map((row, i) => (
                      <tr key={row.symbol} className="hover:bg-[#ffffff05] transition-colors">
                        <td className="px-4 py-3 text-[#fafafa] font-bold">
                          <div className="flex items-center gap-1.5">
                            <StarButton symbol={row.symbol} size={11} />
                            <button
                              onClick={() => window.open(`/#/chart?symbol=${encodeURIComponent(row.symbol)}`, '_blank')}
                              className="hover:text-purple-400 inline-flex items-center gap-1 transition-colors group"
                            >
                              {row.symbol} <ArrowUpRight size={12} className="opacity-0 group-hover:opacity-100" />
                            </button>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-right text-[#ccc]">{row.market_cap_cr.toFixed(1)}</td>
                        <td className="px-4 py-3 text-right text-[#ccc]">{row.base_days}</td>
                        <td className="px-4 py-3 text-right text-[#ccc]">{row.dar_median.toFixed(3)}</td>
                        <td className="px-4 py-3 text-right text-[#ccc]">{row.base_range_pct.toFixed(2)}%</td>
                        <td className="px-4 py-3 text-right text-[#ccc]">{row.volume_ratio.toFixed(2)}</td>
                        <td className="px-4 py-3 text-right">
                          <span className={row.delivery_slope > 0 ? 'text-green-400' : 'text-red-400'}>
                            {row.delivery_slope.toFixed(4)}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-right font-bold">
                          <span className={row.composite_score >= 80 ? 'text-green-400' : row.composite_score >= 60 ? 'text-blue-400' : row.composite_score >= 40 ? 'text-yellow-400' : 'text-red-400'}>
                            {row.composite_score.toFixed(1)}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-center">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold border ${GRADE_COLORS[row.grade] || 'bg-[#ffffff1a] text-[#aaa]'}`}>
                            {row.grade}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-right text-green-400">{row.entry.toFixed(2)}</td>
                        <td className="px-4 py-3 text-right text-red-400">{row.sl.toFixed(2)}</td>
                        <td className="px-4 py-3 text-right text-[#ccc]">{row.t1.toFixed(2)}</td>
                        <td className="px-4 py-3 text-right text-[#ccc]">{row.t2.toFixed(2)}</td>
                        <td className="px-4 py-3 text-right text-[#ccc]">{row.t3 !== null ? row.t3.toFixed(2) : '—'}</td>
                        <td className="px-4 py-3 text-center">
                          <span className={`text-[10px] font-semibold ${STATUS_COLORS[row.status] || 'text-[#aaa]'}`}>
                            {row.status}
                          </span>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {/* Empty state */}
      {isIdle && candidates.length === 0 && !isScanning && !error && (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center text-[#666] font-mono flex flex-col items-center gap-2">
            <Rocket size={32} className="opacity-30" />
            <p>Click Scan to detect multibagger candidates.</p>
            <p className="text-[10px]">Scans for accumulation bases with delivery absorption, volume character, and tightness analysis.</p>
          </div>
        </div>
      )}
    </div>
  );
}
