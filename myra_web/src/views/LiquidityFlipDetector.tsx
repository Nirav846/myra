import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Librarian } from '../lib/Librarian';
import { Box, Filter, AlertTriangle, ArrowUpRight, RefreshCw, CheckCircle, Clock, XCircle, Download, ChevronUp, ChevronDown, ArrowUpDown, Star, Info } from 'lucide-react';
import FundTractionButton from '../components/FundTractionButton';
import MarketCapRangeFilter from '../components/MarketCapRangeFilter';
import { fetchMarketCapMap } from '../lib/marketCapCache';
import { useWatchlist } from '../lib/WatchlistContext';
import { StarButton } from '../components/StarButton';
import { API_BASE } from '../config';
import { Tooltip } from '../components/Tooltip';
import ScrollableTable from '../components/ScrollableTable';
import { HistoricalScanDatePicker } from '../components/HistoricalScanDatePicker';

const CONFIDENCE_COLORS: Record<string, string> = {
  High: 'bg-green-500/20 text-green-400 border-green-500/30',
  Moderate: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  Low: 'bg-[#ffffff0a] text-[#888] border-[#ffffff1a]',
};

interface Candidate {
  symbol: string;
  sector?: string;
  market_cap_cr: number;
  confidence?: string;
  prior_del_pct: number;
  current_del_pct: number;
  del_jump_pp: number;
  del50_days: number;
  flip_type: string;
  flip_score: number;
  grade: string;
  prior_vol_rank: number;
  close: number;
  wk52_pos: number;
  avg_del_value_cr?: number;
  flip_consistency?: number;
  sma_200?: number | null;
  sma_200_factor?: number;
}

interface ScanStatus {
  scan_status: string;
  last_scan: string | null;
  progress: number;
  message: string;
  candidates: Candidate[];
  bear_market?: boolean;
  scanned_date?: string | null;
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

const FLIP_TYPE_COLORS: Record<string, string> = {
  'STRONG FLIP': 'bg-green-500/20 text-green-400 border-green-500/30',
  'MODERATE FLIP': 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30',
  'EARLY FLIP': 'bg-amber-500/20 text-amber-400 border-amber-500/30',
};

const STATUS_FILTERS = ['All', 'STRONG FLIP', 'MODERATE FLIP', 'EARLY FLIP'];

const PRESETS: Record<string, { prior_window: number; recent_window: number; lookback_days: number }> = {
  'Swing / Momentum': { prior_window: 75, recent_window: 5, lookback_days: 80 },
  'Structural Accumulation': { prior_window: 120, recent_window: 21, lookback_days: 141 },
  'Value / Deep Accumulation': { prior_window: 180, recent_window: 42, lookback_days: 222 },
};

export default function LiquidityFlipDetectorView({ lib }: { lib: Librarian }) {
  const [scanStatus, setScanStatus] = useState<ScanStatus | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [staleBannerOpen, setStaleBannerOpen] = useState(true);

  const [mcapRange, setMcapRange] = useState<{ min: number; max: number } | null>(null);
  const mcapMapRef = useRef<Map<string, number>>(new Map());

  const { isWatched } = useWatchlist();
  const [watchlistOnly, setWatchlistOnly] = useState(false);

  const [sectorFilter, setSectorFilter] = useState<string>('All');
  const [statusFilter, setStatusFilter] = useState<string>('All');
  const [minJumpFilter, setMinJumpFilter] = useState(0);
  const [minDel50Days, setMinDel50Days] = useState(0);

  const [scanDate, setScanDate] = useState('');

  const [activePreset, setActivePreset] = useState('Structural Accumulation');

  const [sortCol, setSortCol] = useState<string>('flip_score');
  const [sortAsc, setSortAsc] = useState(false);

  useEffect(() => { fetchMarketCapMap().then(m => mcapMapRef.current = m); }, []);

  const mountedRef = useRef(true);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const presetScanTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startScanRef = useRef<(() => void) | null>(null);

  const candidates = scanStatus?.candidates ?? [];

  const availableSectors = useMemo(() => {
    const sectors = new Set(candidates.map(c => c.sector ?? 'Unknown'));
    return ['All', ...Array.from(sectors).filter(s => s !== 'Unknown').sort(), 'Unknown'];
  }, [candidates]);

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
    if (sectorFilter !== 'All') data = data.filter(d => d.sector === sectorFilter);
    if (statusFilter !== 'All') data = data.filter(d => d.flip_type === statusFilter);
    if (minJumpFilter > 0) data = data.filter(d => d.del_jump_pp >= minJumpFilter);
    if (minDel50Days > 0) data = data.filter(d => d.del50_days >= minDel50Days);
    data.sort((a, b) => {
      const av = (a as any)[sortCol] ?? 0;
      const bv = (b as any)[sortCol] ?? 0;
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortAsc ? av - bv : bv - av;
      }
      return String(av).localeCompare(String(bv)) * (sortAsc ? 1 : -1);
    });
    return data;
  }, [candidates, mcapRange, watchlistOnly, sectorFilter, statusFilter, minJumpFilter, minDel50Days, isWatched, sortCol, sortAsc]);

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
      ? <ChevronUp size={10} className="inline ml-1 text-purple-400" />
      : <ChevronDown size={10} className="inline ml-1 text-purple-400" />;
  };

  const fetchScanStatus = useCallback(async () => {
    if (!mountedRef.current) return;
    try {
      const res = await fetch(`${API_BASE}/liquidity-flip/status`);
      if (!mountedRef.current) return;
      if (res.ok) {
        const data: ScanStatus = await res.json();
        if (!mountedRef.current) return;
        setScanStatus(data);
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
      const preset = PRESETS[activePreset];
      const res = await fetch(`${API_BASE}/liquidity-flip/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...preset,
          min_mcap: mcapRange?.min ?? 200,
          max_mcap: mcapRange?.max ?? 50000,
          ...(scanDate.trim() && { scan_date: scanDate }),
        }),
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
  }, [fetchScanStatus, clearPolling, mcapRange, scanDate, activePreset]);
  startScanRef.current = startScan;

  const handlePresetChange = (name: string) => {
    if (presetScanTimerRef.current) clearTimeout(presetScanTimerRef.current);
    setActivePreset(name);
    fetch(`${API_BASE}/cache/liquidity-flip`, { method: 'DELETE' }).catch(() => {});
    presetScanTimerRef.current = setTimeout(() => startScanRef.current(), 300);
  };

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
    const headers = [
      'Symbol', 'Sector', 'Market Cap Cr', 'Confidence', 'Prior Del%', 'Current Del%', 'Jump(pp)',
      'Del50 Days', 'Del Val (₹ Cr)', 'Consistency%', 'SMA-200', 'Flip Type', 'Vol Rank', 'Close',
      '52W Pos%', 'Score', 'Grade',
    ];
    const rows = filteredData.map(r => [
      r.symbol, r.sector ?? '', r.market_cap_cr, r.confidence ?? 'Low', r.prior_del_pct, r.current_del_pct,
      r.del_jump_pp, r.del50_days,
      r.avg_del_value_cr?.toFixed(1) ?? '', r.flip_consistency ?? '',
      r.sma_200 != null ? ((r.close ?? 0) >= r.sma_200 ? 'Above' : 'Below') : '',
      r.flip_type, r.prior_vol_rank, r.close,
      r.wk52_pos, r.flip_score, r.grade,
    ].join(','));
    const csv = [headers.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `liquidity_flip_${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const progressPct = scanStatus?.progress ?? 0;
  const isIdle = scanStatus?.scan_status === 'idle' || !scanStatus;

  return (
    <main className="flex flex-col flex-1 min-h-0 relative gap-4 p-4" aria-label="Liquidity Flip Detector">
      {isStale && staleBannerOpen && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded px-4 py-2 flex items-center gap-2 text-xs font-mono" role="alert">
          <AlertTriangle size={14} className="text-amber-400 shrink-0" aria-hidden="true" />
          <span className="text-amber-300/90">Data may be stale — re-scan recommended (last scan &gt; 30 min ago).</span>
          <button onClick={() => setStaleBannerOpen(false)} className="ml-auto text-amber-500/50 hover:text-amber-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/50 rounded" aria-label="Dismiss stale warning">
            <XCircle size={14} aria-hidden="true" />
          </button>
        </div>
      )}

      <header className="flex justify-between items-center bg-[#1a1c24] border border-[#ffffff1a] rounded p-4">
        <div className="flex items-center gap-3">
          <div className="bg-cyan-500/20 p-2 rounded" aria-hidden="true">
            <Box className="text-cyan-400" size={24} />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-[#fafafa]">Liquidity Flip Detector</h1>
            <p className="text-xs font-mono text-[#888]">Churn → Conviction Flip Signal</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <HistoricalScanDatePicker selectedDate={scanDate} onSelect={setScanDate} />
          <div className="flex items-center gap-1">
            {Object.entries(PRESETS).map(([name, _config]) => (
              <button
                key={name}
                onClick={() => handlePresetChange(name)}
                className={`px-2 py-1 text-[12px] rounded font-mono transition-colors ${
                  activePreset === name
                    ? 'bg-blue-600 text-white'
                    : 'bg-[#ffffff0a] text-[#888] hover:text-white'
                }`}
              >
                {name.split(' / ')[0]}
              </button>
            ))}
          </div>
          <button
            onClick={startScan}
            disabled={isScanning}
            className="px-4 py-2 bg-cyan-600 hover:bg-cyan-700 disabled:opacity-50 text-white rounded text-xs font-semibold flex items-center gap-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/50"
            aria-label={isScanning ? 'Scanning, please wait' : 'Start scan'}
          >
            {isScanning ? (
              <><RefreshCw size={14} className="animate-spin" aria-hidden="true" /> Scanning...</>
            ) : (
              <><Box size={14} fill="currentColor" aria-hidden="true" /> Scan</>
            )}
          </button>
          <button
            onClick={() => {
              fetch(`${API_BASE}/cache/liquidity-flip`, { method: 'DELETE' }).then(() => fetchScanStatus()).catch(() => {});
            }}
            className="text-[12px] text-[#888] hover:text-red-400 transition-colors"
            title="Clear cached scan results"
          >
            Clear cache
          </button>
        </div>
      </header>

      {isScanning && (
        <div className="bg-cyan-500/10 border border-cyan-500/30 rounded p-3" role="progressbar" aria-valuenow={progressPct} aria-valuemin={0} aria-valuemax={100} aria-label="Scan progress">
          <div className="flex items-center gap-2 text-xs font-mono text-cyan-300 mb-2">
            <RefreshCw size={14} className="animate-spin" aria-hidden="true" />
            <span role="status" aria-live="polite">{scanStatus?.message || 'Scanning...'}</span>
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
        }`} role="status" aria-live="polite">
          {scanStatus.scan_status === 'completed' ? <CheckCircle size={14} className="text-green-400" aria-hidden="true" /> :
           scanStatus.scan_status === 'error' ? <XCircle size={14} className="text-red-400" aria-hidden="true" /> :
           <Clock size={14} aria-hidden="true" />}
          <span>
            {scanStatus.scan_status === 'completed' ? `Completed (${relativeTime(scanStatus.last_scan)})` :
             scanStatus.scan_status === 'error' ? 'Scan failed' :
             scanStatus.message}
          </span>
          <span className="ml-auto text-[#888]">{scanStatus.message}</span>
        </div>
      )}

      {scanDate && scanStatus?.scan_status === 'completed' && scanStatus.scanned_date && scanStatus.scanned_date !== scanDate && (
        <div className="flex items-center gap-2 px-3 py-1.5 rounded text-[12px] font-mono text-cyan-400 bg-cyan-500/5 border border-cyan-500/20">
          <Info size={12} aria-hidden="true" />
          <span>Selected date is a holiday or weekend — adjusted to {scanStatus.scanned_date} (previous trading day)</span>
        </div>
      )}

      {error && !isScanning && (
        <div className="bg-red-500/10 border border-red-500/30 rounded px-4 py-2 flex items-center gap-2 text-xs font-mono text-red-300" role="alert">
          <AlertTriangle size={14} className="shrink-0" aria-hidden="true" />
          <span>Error: {error}</span>
        </div>
      )}

      <section className="bg-[#0e1117] border border-[#ffffff1a] rounded p-4 flex flex-wrap gap-4 items-end" aria-label="Filters">
        <div className="flex items-center gap-2 mb-1 text-xs text-[#888] w-full">
          <Filter size={14} aria-hidden="true" /> <span className="font-mono uppercase font-semibold">Filters</span>
        </div>
        <div className="max-w-[220px] flex-shrink-0">
          <MarketCapRangeFilter onChange={setMcapRange} />
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] text-[#888] font-mono">Watchlist</div>
          <button
            onClick={() => setWatchlistOnly(o => !o)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded border text-[12px] font-mono transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-yellow-500/50 ${
              watchlistOnly
                ? 'bg-yellow-500/20 border-yellow-500/40 text-yellow-400'
                : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#888] hover:text-yellow-400'
            }`}
            aria-label={watchlistOnly ? 'Show all symbols' : 'Filter to starred watchlist only'}
            aria-pressed={watchlistOnly}
          >
            <Star size={11} fill={watchlistOnly ? 'currentColor' : 'none'} aria-hidden="true" />
            Only Starred
          </button>
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] text-[#888] font-mono">Flip Type</div>
          <select
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value)}
            className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-cyan-500 outline-none font-mono focus-visible:ring-2 focus-visible:ring-cyan-500/50"
          >
            {STATUS_FILTERS.map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1 w-28">
          <div className="flex justify-between text-[12px] text-[#888] font-mono items-center">
            <Tooltip content="Minimum Jump (pp) filter. Jump = Current Del% - Prior Del%.">
              <span>Min Jump</span>
            </Tooltip>
            <span className="text-cyan-400">{minJumpFilter}</span>
          </div>
          <input
            type="range"
            min={0}
            max={30}
            step={1}
            value={minJumpFilter}
            onChange={e => setMinJumpFilter(Number(e.target.value))}
            className="w-full accent-cyan-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/50"
            aria-label="Minimum jump percentage points"
          />
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] text-[#888] font-mono">Min Del50 Days</div>
          <div className="flex gap-1">
            {[0, 3, 4, 5].map(n => (
              <button
                key={n}
                onClick={() => setMinDel50Days(n)}
                className={`px-2 py-1 rounded border text-[12px] font-mono transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/50 ${
                  minDel50Days === n
                    ? 'bg-cyan-500/20 border-cyan-500/40 text-cyan-400'
                    : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#888] hover:text-[#aaa]'
                }`}
                aria-pressed={minDel50Days === n}
              >
                {n === 0 ? 'Any' : `${n}+`}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[12px] text-[#888] font-mono" id="sector-filter-label">Sector</div>
          <select
            value={sectorFilter}
            onChange={e => setSectorFilter(e.target.value)}
            className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-cyan-500 outline-none font-mono focus-visible:ring-2 focus-visible:ring-cyan-500/50"
            aria-labelledby="sector-filter-label"
          >
            {availableSectors.map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
      </section>

      {(scanStatus?.scan_status === 'completed' || (isIdle && candidates.length > 0)) && !isScanning && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Candidates</div>
              <div className="text-2xl font-bold text-[#fafafa]">{filteredData.length}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Strong Flips</div>
              <div className="text-2xl font-bold text-green-400">{filteredData.filter(d => d.flip_type === 'STRONG FLIP').length}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Avg Jump</div>
              <div className="text-2xl font-bold text-cyan-400">
                {filteredData.length > 0
                  ? (filteredData.reduce((s, d) => s + d.del_jump_pp, 0) / filteredData.length).toFixed(1) + 'pp'
                  : '—'}
              </div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[12px] text-[#888] font-mono uppercase tracking-wider">Avg Score</div>
              <div className="text-2xl font-bold text-purple-400">
                {filteredData.length > 0
                  ? (filteredData.reduce((s, d) => s + d.flip_score, 0) / filteredData.length).toFixed(0)
                  : '—'}
              </div>
            </div>
          </div>

          {filteredData.filter(d => d.grade === 'A').length > 0 && (
            <div className="bg-green-500/5 border border-green-500/20 rounded p-3">
              <div className="text-[12px] text-green-400 font-mono uppercase tracking-wider mb-2 flex items-center gap-2">
                <span>Grade A Flips</span>
                <span className="text-[#888]">— highest conviction liquidity flips</span>
              </div>
              <div className="flex flex-wrap gap-2">
                {filteredData
                  .filter(d => d.grade === 'A')
                  .slice(0, 12)
                  .map(d => (
                    <div key={d.symbol}
                      className="flex items-center gap-1.5 px-2 py-1 rounded border text-[12px] font-mono border-green-500/20 bg-[#1a1c24]"
                    >
                      <StarButton symbol={d.symbol} size={10} />
                      <span className="text-white font-bold">{d.symbol}</span>
                      <span className="text-[#888]">{d.sector ?? ''}</span>
                      <span className={d.flip_type === 'STRONG FLIP' ? 'text-green-400' : 'text-cyan-400'}>
                        {d.flip_type === 'STRONG FLIP' ? 'STRONG' : 'MODERATE'}
                      </span>
                      <span className="text-yellow-400">+{d.del_jump_pp.toFixed(1)}pp</span>
                    </div>
                  ))
                }
              </div>
            </div>
          )}

          <div className="flex-1 bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden">
            <ScrollableTable>
              <table
                className="w-full min-w-max text-left text-xs font-mono whitespace-nowrap"
                role="grid"
                aria-label="Liquidity Flip Detector results"
                aria-rowcount={filteredData.length}
                aria-colcount={17}
              >
                <thead className="sticky top-0 z-20 text-[#888]">
                  <tr style={{ boxShadow: '0 1px 0 0 rgba(255,255,255,0.08), 0 2px 4px 0 rgba(0,0,0,0.4)' }}>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-500/50" onClick={() => handleSort('symbol')} scope="col" aria-sort={sortCol === 'symbol' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      Symbol <SortIcon column="symbol" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-500/50" onClick={() => handleSort('sector')} scope="col" aria-sort={sortCol === 'sector' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      Sector <SortIcon column="sector" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-500/50" onClick={() => handleSort('market_cap_cr')} scope="col" aria-sort={sortCol === 'market_cap_cr' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      MCap (₹ Cr) <SortIcon column="market_cap_cr" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white" onClick={() => handleSort('confidence')} scope="col">
                      <Tooltip content="Confidence based on flip type, SMA-200 trend, and 52-week position." good="High: strong flip, above SMA-200, 30-90% range" bad="Low: weak setup">Conf <SortIcon column="confidence" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('prior_del_pct')} scope="col">
                      <Tooltip content="Average delivery% over the prior 75 days (baseline churn level). Lower = more noise." good="<35: churning (good setup)" bad="≥45: not a flip">Prior Del% <SortIcon column="prior_del_pct" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('current_del_pct')} scope="col">
                      <Tooltip content="Average delivery% over the last 5 sessions. >55% = genuine conviction." good=">55: strong" bad="<45: weak">Curr Del% <SortIcon column="current_del_pct" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('del_jump_pp')} scope="col">
                      <Tooltip content="Current Del% minus Prior Del%. Higher = more dramatic flip from noise to conviction." good=">20: dramatic flip" bad="<10: marginal">Jump(pp) <SortIcon column="del_jump_pp" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('del50_days')} scope="col">
                      <Tooltip content="Number of days (out of last 5) where delivery% > 50%. 5/5 = maximum conviction." good="5: perfect" bad="<3: inconsistent">Del50 Days <SortIcon column="del50_days" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('avg_del_value_cr')} scope="col">
                      <Tooltip content="Average daily delivery value in ₹ Cr. Higher = stronger institutional participation." good=">15: institutional" bad="<15: retail-driven">Del Val (₹ Cr) <SortIcon column="avg_del_value_cr" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('flip_consistency')} scope="col">
                      <Tooltip content="% of recent window days with delivery > 50%. Higher = more persistent conviction.">Consistency% <SortIcon column="flip_consistency" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white" onClick={() => handleSort('sma_200')} scope="col">
                      <Tooltip content="Price vs SMA-200. Above = uptrend, Below = downtrend (penalised).">SMA-200 <SortIcon column="sma_200" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white" onClick={() => handleSort('flip_type')} scope="col">
                      Flip Type <SortIcon column="flip_type" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('prior_vol_rank')} scope="col">
                      <Tooltip content="Volume rank vs universe median. >1.5 = high volume churner — more meaningful when flip occurs.">Vol Rank <SortIcon column="prior_vol_rank" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('close')} scope="col">
                      Close <SortIcon column="close" />
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white" onClick={() => handleSort('wk52_pos')} scope="col">
                      <Tooltip content="Position within 52-week range. <90% means the stock hasn't already run its full course." good="<80: room to run" bad="≥90: already exhausted">52W Pos% <SortIcon column="wk52_pos" /></Tooltip>
                    </th>
                    <th role="columnheader" className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-500/50" onClick={() => handleSort('flip_score')} scope="col" aria-sort={sortCol === 'flip_score' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      Score <SortIcon column="flip_score" />
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#ffffff0a]">
                  {filteredData.length === 0 ? (
                    <tr>
                      <td colSpan={17} className="px-4 py-8 text-center text-[#888]">No liquidity flips match current filters.</td>
                    </tr>
                  ) : (
                    filteredData.map((row, index) => (
                      <tr key={row.symbol} role="row" aria-rowindex={index + 1} className="hover:bg-[#ffffff05] transition-colors">
                        <td className="px-3 py-3 font-bold" role="rowheader">
                          <div className="flex items-center gap-1.5">
                            <StarButton symbol={row.symbol} size={11} />
                            <button
                              onClick={() => window.open(`/#/chart?symbol=${encodeURIComponent(row.symbol)}`, '_blank')}
                              className="text-[#fafafa] hover:text-cyan-400 inline-flex items-center gap-1 transition-colors group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-500/50"
                              aria-label={`Open chart for ${row.symbol}`}
                            >
                              {row.symbol}
                              <ArrowUpRight size={12} className="opacity-0 group-hover:opacity-100" aria-hidden="true" />
                            </button>
                          </div>
                        </td>
                        <td className="px-3 py-3 text-[#888] text-[12px] max-w-[120px] truncate" title={row.sector ?? ''}>
                          {row.sector ?? '—'}
                        </td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.market_cap_cr.toFixed(0)}</td>
                        <td className="px-3 py-3 text-center">
                          <span className={`px-2 py-0.5 rounded text-[12px] font-bold border ${CONFIDENCE_COLORS[row.confidence ?? 'Low']}`}>
                            {row.confidence ?? 'Low'}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.prior_del_pct.toFixed(1)}%</td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.current_del_pct.toFixed(1)}%</td>
                        <td className="px-3 py-3 text-right">
                          <span className={
                            row.del_jump_pp > 20 ? 'text-green-400' :
                            row.del_jump_pp > 10 ? 'text-yellow-400' :
                            'text-[#888]'
                          }>
                            +{row.del_jump_pp.toFixed(1)}pp
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right">
                          <span className="text-[#ccc]">
                            {row.del50_days}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right text-[#ccc]">
                          {row.avg_del_value_cr?.toFixed(1) ?? '—'}
                        </td>
                        <td className="px-3 py-3 text-right">
                          <span className={row.flip_consistency != null && row.flip_consistency >= 80 ? 'text-green-400' : row.flip_consistency != null && row.flip_consistency >= 50 ? 'text-yellow-400' : 'text-[#888]'}>
                            {row.flip_consistency != null ? `${row.flip_consistency}%` : '—'}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-center">
                          <span className={row.sma_200 != null && (row.close ?? 0) >= row.sma_200 ? 'text-green-400' : row.sma_200 != null ? 'text-red-400' : 'text-[#888]'}>
                            {row.sma_200 != null ? ((row.close ?? 0) >= row.sma_200 ? 'Above' : 'Below') : '—'}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-center">
                          <span className={`px-2 py-0.5 rounded text-[12px] font-bold border ${
                            FLIP_TYPE_COLORS[row.flip_type] || 'bg-[#ffffff1a] text-[#aaa]'
                          }`}>
                            {row.flip_type === 'STRONG FLIP' ? 'STRONG' : row.flip_type === 'MODERATE FLIP' ? 'MOD' : row.flip_type === 'EARLY FLIP' ? 'EARLY' : row.flip_type}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.prior_vol_rank.toFixed(2)}</td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.close.toFixed(2)}</td>
                        <td className="px-3 py-3 text-right">
                          <span className={row.wk52_pos < 80 ? 'text-green-400' : row.wk52_pos < 90 ? 'text-yellow-400' : 'text-[#888]'}>
                            {row.wk52_pos.toFixed(1)}%
                          </span>
                        </td>
                        <td className="px-3 py-3 text-center">
                          <span className={`px-2 py-0.5 rounded text-[12px] font-bold border ${GRADE_COLORS[row.grade] || 'bg-[#ffffff1a] text-[#aaa]'}`}>
                            {row.flip_score.toFixed(0)} · {row.grade}
                          </span>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </ScrollableTable>
          </div>
          <div className="flex justify-end gap-2">
            <FundTractionButton symbols={filteredData.map(c => c.symbol)} disabled={filteredData.length === 0} />
            <button
              onClick={handleCSV}
              disabled={filteredData.length === 0}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-[#ccc] transition-colors disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/50"
              aria-label="Export table as CSV"
            >
              <Download size={12} aria-hidden="true" />
              CSV
            </button>
          </div>
        </>
      )}

      {isIdle && candidates.length === 0 && !isScanning && !error && (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center text-[#888] font-mono flex flex-col items-center gap-2">
            <Box size={32} className="opacity-30" aria-hidden="true" />
            <p>Click Scan to detect liquidity flips from churn to conviction.</p>
            <p className="text-[12px]">Stocks transitioning from high-volume/low-delivery to high-volume/high-delivery.</p>
          </div>
        </div>
      )}
    </main>
  );
}
