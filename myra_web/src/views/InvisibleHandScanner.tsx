import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Librarian } from '../lib/Librarian';
import { Box, Filter, AlertTriangle, ArrowUpRight, RefreshCw, CheckCircle, Clock, XCircle, Download, ChevronUp, ChevronDown, ArrowUpDown, Star, Eye, Zap } from 'lucide-react';
import MarketCapRangeFilter from '../components/MarketCapRangeFilter';
import { fetchMarketCapMap } from '../lib/marketCapCache';
import { useWatchlist } from '../lib/WatchlistContext';
import { StarButton } from '../components/StarButton';
import { API_BASE } from '../config';
import { Tooltip } from '../components/Tooltip';
import ScrollableTable from '../components/ScrollableTable';

interface Candidate {
  symbol: string;
  sector?: string;
  market_cap_cr: number;
  der_ratio: number;
  der_score: number;
  ddas: number;
  ddas_score: number;
  mean_del_pct: number;
  dcs_score: number;
  qcd: number;
  qcd_score: number;
  ih_score: number;
  grade: string;
  down_day_count: number;
  base_duration: number;
  close: number;
  wk52_pos: number;
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

const STATUS_FILTERS = ['All', 'A', 'B', 'C', 'D'];

export default function InvisibleHandScannerView({ lib }: { lib: Librarian }) {
  const [scanStatus, setScanStatus] = useState<ScanStatus | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [staleBannerOpen, setStaleBannerOpen] = useState(true);

  const [mcapRange, setMcapRange] = useState<{ min: number; max: number } | null>(null);
  const mcapMapRef = useRef<Map<string, number>>(new Map());

  const { isWatched } = useWatchlist();
  const [watchlistOnly, setWatchlistOnly] = useState(false);

  const [sectorFilter, setSectorFilter] = useState<string>('All');
  const [minIhScoreFilter, setMinIhScoreFilter] = useState(0);
  const [minQcdFilter, setMinQcdFilter] = useState(0);
  const [gradeFilter, setGradeFilter] = useState<string>('All');

  const [sortCol, setSortCol] = useState<string>('ih_score');
  const [sortAsc, setSortAsc] = useState(false);

  useEffect(() => { fetchMarketCapMap().then(m => mcapMapRef.current = m); }, []);

  const mountedRef = useRef(true);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

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
    if (minIhScoreFilter > 0) data = data.filter(d => d.ih_score >= minIhScoreFilter);
    if (minQcdFilter > 0) data = data.filter(d => d.qcd >= minQcdFilter);
    if (gradeFilter !== 'All') data = data.filter(d => d.grade === gradeFilter);
    data.sort((a, b) => {
      const av = (a as any)[sortCol] ?? 0;
      const bv = (b as any)[sortCol] ?? 0;
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortAsc ? av - bv : bv - av;
      }
      return String(av).localeCompare(String(bv)) * (sortAsc ? 1 : -1);
    });
    return data;
  }, [candidates, mcapRange, watchlistOnly, sectorFilter, minIhScoreFilter, minQcdFilter, gradeFilter, isWatched, sortCol, sortAsc]);

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
      ? <ChevronUp size={10} className="inline ml-1 text-violet-400" />
      : <ChevronDown size={10} className="inline ml-1 text-violet-400" />;
  };

  const fetchScanStatus = useCallback(async () => {
    if (!mountedRef.current) return;
    try {
      const res = await fetch(`${API_BASE}/api/invisible-hand/status`);
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
      const res = await fetch(`${API_BASE}/api/invisible-hand/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          min_mcap: mcapRange?.min ?? 200,
          max_mcap: mcapRange?.max ?? 50000,
          window: 20,
          hist_window: 60,
          min_ih_score: 35,
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
  }, [fetchScanStatus, clearPolling, mcapRange]);

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
      'Symbol', 'Sector', 'Market Cap Cr', 'DER Ratio', 'DDAS%', 'Mean Del%', 'DCS', 'QCD', 'IH Score', 'Base Days', 'Close', '52W Pos%',
    ];
    const rows = filteredData.map(r => [
      r.symbol, r.sector ?? '', r.market_cap_cr, r.der_ratio, r.ddas,
      r.mean_del_pct, r.dcs_score, r.qcd, r.ih_score, r.base_duration,
      r.close, r.wk52_pos,
    ].join(','));
    const csv = [headers.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `invisible_hand_${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const progressPct = scanStatus?.progress ?? 0;
  const isIdle = scanStatus?.scan_status === 'idle' || !scanStatus;

  return (
    <main className="flex flex-col flex-1 min-h-0 relative gap-4 p-4" aria-label="Invisible Hand Scanner">
      {isStale && staleBannerOpen && (
        <div className="bg-violet-500/10 border border-violet-500/30 rounded px-4 py-2 flex items-center gap-2 text-xs font-mono" role="alert">
          <AlertTriangle size={14} className="text-violet-400 shrink-0" aria-hidden="true" />
          <span className="text-violet-300/90">Data may be stale — re-scan recommended (last scan &gt; 30 min ago).</span>
          <button onClick={() => setStaleBannerOpen(false)} className="ml-auto text-violet-500/50 hover:text-violet-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/50 rounded" aria-label="Dismiss stale warning">
            <XCircle size={14} aria-hidden="true" />
          </button>
        </div>
      )}

      <header className="flex justify-between items-center bg-[#1a1c24] border border-[#ffffff1a] rounded p-4">
        <div className="flex items-center gap-3">
          <div className="bg-violet-500/20 p-2 rounded" aria-hidden="true">
            <Eye className="text-violet-400" size={24} />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-[#fafafa]">Invisible Hand Scanner</h1>
            <p className="text-xs font-mono text-[#888]">Systematic accumulation when nobody is watching</p>
          </div>
        </div>
        <button
          onClick={startScan}
          disabled={isScanning}
          className="px-4 py-2 bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white rounded text-xs font-semibold flex items-center gap-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/50"
          aria-label={isScanning ? 'Scanning, please wait' : 'Start scan'}
        >
          {isScanning ? (
            <><RefreshCw size={14} className="animate-spin" aria-hidden="true" /> Scanning...</>
          ) : (
            <><Eye size={14} fill="currentColor" aria-hidden="true" /> Scan</>
          )}
        </button>
      </header>

      {isScanning && (
        <div className="bg-violet-500/10 border border-violet-500/30 rounded p-3" role="progressbar" aria-valuenow={progressPct} aria-valuemin={0} aria-valuemax={100} aria-label="Scan progress">
          <div className="flex items-center gap-2 text-xs font-mono text-violet-300 mb-2">
            <RefreshCw size={14} className="animate-spin" aria-hidden="true" />
            <span>{scanStatus?.message || 'Scanning...'}</span>
            <span className="ml-auto">{progressPct}%</span>
          </div>
          <div className="w-full h-1.5 bg-[#ffffff1a] rounded-full overflow-hidden">
            <div className="h-full bg-violet-500 rounded-full transition-all duration-500" style={{ width: `${Math.max(progressPct, 5)}%` }} />
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
          <span className="ml-auto text-[#666]">{scanStatus.message}</span>
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
          <div className="text-[10px] text-[#888] font-mono">Watchlist</div>
          <button
            onClick={() => setWatchlistOnly(o => !o)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded border text-[11px] font-mono transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-yellow-500/50 ${
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
          <div className="text-[10px] text-[#888] font-mono">Sector</div>
          <select
            value={sectorFilter}
            onChange={e => setSectorFilter(e.target.value)}
            className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-violet-500 outline-none font-mono focus-visible:ring-2 focus-visible:ring-violet-500/50"
            aria-label="Sector filter"
          >
            {availableSectors.map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1 w-32">
          <div className="text-[10px] text-[#888] font-mono" id="ih-score-filter-label">Min IH Score</div>
          <select
            value={minIhScoreFilter}
            onChange={e => setMinIhScoreFilter(Number(e.target.value))}
            className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-violet-500 outline-none font-mono focus-visible:ring-2 focus-visible:ring-violet-500/50"
            aria-labelledby="ih-score-filter-label"
          >
            {[0, 35, 55, 75, 85].map(v => (
              <option key={v} value={v}>{v === 0 ? 'Any' : `${v}+`}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1 w-28">
          <div className="text-[10px] text-[#888] font-mono">Min QCD</div>
          <div className="flex gap-1">
            {[0, 4, 6, 8].map(n => (
              <button
                key={n}
                onClick={() => setMinQcdFilter(n)}
                className={`px-2 py-1 rounded border text-[10px] font-mono transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/50 ${
                  minQcdFilter === n
                    ? 'bg-violet-500/20 border-violet-500/40 text-violet-400'
                    : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#666] hover:text-[#aaa]'
                }`}
                aria-pressed={minQcdFilter === n}
              >
                {n === 0 ? 'Any' : `${n}+`}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[10px] text-[#888] font-mono">Grade</div>
          <div className="flex gap-1">
            {['All', 'A', 'B', 'C', 'D'].map(g => (
              <button
                key={g}
                onClick={() => setGradeFilter(g)}
                className={`px-2 py-1 rounded border text-[10px] font-mono transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/50 ${
                  gradeFilter === g
                    ? 'bg-violet-500/20 border-violet-500/40 text-violet-400'
                    : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#666] hover:text-[#aaa]'
                }`}
                aria-pressed={gradeFilter === g}
              >
                {g}
              </button>
            ))}
          </div>
        </div>
      </section>

      {(scanStatus?.scan_status === 'completed' || (isIdle && candidates.length > 0)) && !isScanning && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider">Candidates</div>
              <div className="text-2xl font-bold text-[#fafafa]">{filteredData.length}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider">Grade A</div>
              <div className="text-2xl font-bold text-violet-400">{filteredData.filter(d => d.grade === 'A').length}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider">Avg DER Ratio</div>
              <div className="text-2xl font-bold text-cyan-400">
                {filteredData.length > 0
                  ? (filteredData.reduce((s, d) => s + d.der_ratio, 0) / filteredData.length).toFixed(1) + ''
                  : '—'}
              </div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider">Avg DDAS%</div>
              <div className="text-2xl font-bold text-amber-400">
                {filteredData.length > 0
                  ? (filteredData.reduce((s, d) => s + d.ddas, 0) / filteredData.length).toFixed(1) + '%'
                  : '—'}
              </div>
            </div>
          </div>

          {filteredData.filter(d => d.grade === 'A').length > 0 && (
            <div className="bg-violet-500/5 border border-violet-500/20 rounded p-3">
              <div className="text-[10px] text-violet-400 font-mono uppercase tracking-wider mb-2 flex items-center gap-2">
                <span>Grade A Flips</span>
                <span className="text-[#666]">— highest conviction invisible hand setups</span>
              </div>
              <div className="flex flex-wrap gap-2">
                {filteredData
                  .filter(d => d.grade === 'A')
                  .slice(0, 12)
                  .map(d => (
                    <div key={d.symbol}
                      className="flex items-center gap-1.5 px-2 py-1 rounded border text-[11px] font-mono border-violet-500/20 bg-[#1a1c24]"
                    >
                      <StarButton symbol={d.symbol} size={10} />
                      <span className="text-white font-bold">{d.symbol}</span>
                      <span className="text-[#888]">{d.sector ?? ''}</span>
                      <span className="text-violet-400">DER {d.der_ratio.toFixed(1)}</span>
                      <span className="text-amber-400">DDAS {d.ddas.toFixed(1)}%</span>
                    </div>
                  ))}
              </div>
            </div>
          )}

          <div className="flex-1 bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden">
            <ScrollableTable>
              <table
                className="w-full min-w-max text-left text-xs font-mono whitespace-nowrap"
                role="grid"
                aria-label="Invisible Hand Scanner results"
                aria-rowcount={filteredData.length}
                aria-colcount={14}
              >
                <thead className="sticky top-0 z-20 text-[#888]">
                  <tr style={{ boxShadow: '0 1px 0 0 rgba(255,255,255,0.08), 0 2px 4px 0 rgba(0,0,0,0.4)' }}>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-violet-500/50" onClick={() => handleSort('symbol')} scope="col" aria-sort={sortCol === 'symbol' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      Symbol <SortIcon column="symbol" />
                    </th>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-violet-500/50" onClick={() => handleSort('sector')} scope="col" aria-sort={sortCol === 'sector' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      Sector <SortIcon column="sector" />
                    </th>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-violet-500/50" onClick={() => handleSort('market_cap_cr')} scope="col" aria-sort={sortCol === 'market_cap_cr' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      MCap (₹ Cr) <SortIcon column="market_cap_cr" />
                    </th>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-violet-500/50" onClick={() => handleSort('der_ratio')} scope="col" aria-sort={sortCol === 'der_ratio' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      DER Ratio <SortIcon column="der_ratio" />
                    </th>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-violet-500/50" onClick={() => handleSort('ddas')} scope="col" aria-sort={sortCol === 'ddas' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      DDAS% <SortIcon column="ddas" />
                    </th>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-violet-500/50" onClick={() => handleSort('mean_del_pct')} scope="col" aria-sort={sortCol === 'mean_del_pct' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      Mean Del% <SortIcon column="mean_del_pct" />
                    </th>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-violet-500/50" onClick={() => handleSort('dcs_score')} scope="col" aria-sort={sortCol === 'dcs_score' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      DCS <SortIcon column="dcs_score" />
                    </th>
                    <th className="px-3 py-3 bg-[#0e1c1] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-violet-500/50" onClick={() => handleSort('qcd')} scope="col" aria-sort={sortCol === 'qcd' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      QCD <SortIcon column="qcd" />
                    </th>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-violet-500/50" onClick={() => handleSort('ih_score')} scope="col" aria-sort={sortCol === 'ih_score' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      IH Score <SortIcon column="ih_score" />
                    </th>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-violet-500/50" onClick={() => handleSort('base_duration')} scope="col" aria-sort={sortCol === 'base_duration' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      Base Days <SortIcon column="base_duration" />
                    </th>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-violet-500/50" onClick={() => handleSort('close')} scope="col" aria-sort={sortCol === 'close' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      Close <SortIcon column="close" />
                    </th>
                    <th className="px-3 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-violet-500/50" onClick={() => handleSort('wk52_pos')} scope="col" aria-sort={sortCol === 'wk52_pos' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      52W Pos% <SortIcon column="wk52_pos" />
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#ffffff0a]">
                  {filteredData.length === 0 ? (
                    <tr>
                      <td colSpan={14} className="px-4 py-8 text-center text-[#666]">No invisible hand setups match current filters.</td>
                    </tr>
                  ) : (
                    filteredData.map((row, index) => (
                      <tr key={row.symbol} role="row" aria-rowindex={index + 1} className="hover:bg-[#ffffff05] transition-colors">
                        <td className="px-3 py-3 font-bold" scope="row">
                          <div className="flex items-center gap-1.5">
                            <StarButton symbol={row.symbol} size={11} />
                            <button
                              onClick={() => window.open(`/#/chart?symbol=${encodeURIComponent(row.symbol)}`, '_blank')}
                              className="text-[#fafafa] hover:text-violet-400 inline-flex items-center gap-1 transition-colors group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-violet-500/50"
                              aria-label={`Open chart for ${row.symbol}`}
                            >
                              {row.symbol}
                              <ArrowUpRight size={12} className="opacity-0 group-hover:opacity-100" aria-hidden="true" />
                            </button>
                          </div>
                        </td>
                        <td className="px-3 py-3 text-[#888] text-[11px] max-w-[120px] truncate" title={row.sector ?? ''}>{row.sector ?? '—'}</td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.market_cap_cr.toFixed(0)}</td>
                        <td className="px-3 py-3 text-right">
                          <span className={row.der_ratio > 2.0 ? 'text-violet-400' : row.der_ratio > 1.5 ? 'text-cyan-400' : 'text-[#888]'}>${row.der_ratio.toFixed(2)}</span>
                        </td>
                        <td className="px-3 py-3 text-right">
                          <span className={row.ddas > 60 ? 'text-green-400' : row.ddas > 48 ? 'text-amber-400' : 'text-[#888]'}>{row.ddas.toFixed(1)}%</span>
                        </td>
                        <td className="px-3 py-3 text-right">
                          <span className={row.mean_del_pct > 55 ? 'text-green-400' : row.mean_del_pct > 45 ? 'text-amber-400' : 'text-[#888]'}>{row.mean_del_pct.toFixed(1)}%</span>
                        </td>
                        <td className="px-3 py-3 text-right">
                          <span className={row.dcs_score > 70 ? 'text-green-400' : row.dcs_score > 50 ? 'text-amber-400' : 'text-[#888]'}>{row.dcs_score.toFixed(1)}</span>
                        </td>
                        <td className="px-3 py-3 text-right">
                          <span className={row.qcd >= 8 ? 'text-green-400' : row.qcd >= 5 ? 'text-amber-400' : 'text-[#888]'}>{row.qcd}</span>
                        </td>
                        <td className="px-3 py-3 text-right font-mono">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold border ${GRADE_COLORS[row.grade] || 'bg-[#ffffff1a] text-[#aaa]'}`}>
                            {row.ih_score.toFixed(0)} · {row.grade}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.base_duration}</td>
                        <td className="px-3 py-3 text-right text-[#ccc]">{row.close.toFixed(2)}</td>
                        <td className="px-3 py-3 text-right">
                          <span className={row.wk52_pos < 75 ? 'text-green-400' : row.wk52_pos < 88 ? 'text-amber-400' : 'text-[#888]'}>{row.wk52_pos.toFixed(1)}%</span>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </ScrollableTable>
          </div>
          <div className="flex justify-end">
            <button
              onClick={handleCSV}
              disabled={filteredData.length === 0}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-[#ccc] transition-colors disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/50"
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
          <div className="text-center text-[#666] font-mono flex flex-col items-center gap-2">
            <Eye size={32} className="opacity-30" aria-hidden="true" />
            <p>Click Scan to detect invisible hand accumulation.</p>
            <p className="text-[10px]">Systematic buyers loading up when price is flat or falling with high delivery.</p>
          </div>
        </div>
      )}
    </main>
  );
}