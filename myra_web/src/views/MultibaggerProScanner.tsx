import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Librarian } from '../lib/Librarian';
import { Rocket, Filter, AlertTriangle, ArrowUpRight, RefreshCw, CheckCircle, Clock, XCircle, Download, ChevronUp, ChevronDown, ArrowUpDown, Star } from 'lucide-react';
import MarketCapRangeFilter from '../components/MarketCapRangeFilter';
import { fetchMarketCapMap } from '../lib/marketCapCache';
import { useWatchlist } from '../lib/WatchlistContext';
import { StarButton } from '../components/StarButton';
import { API_BASE } from '../config';
import { Tooltip } from '../components/Tooltip';
import ScrollableTable from '../components/ScrollableTable';

interface Candidate {
  symbol: string;
  market_cap_cr: number;
  base_days: number;
  dar_median: number;
  base_range_pct: number;
  volume_ratio: number;
  vol_dry_up: number;
  delivery_slope: number;
  rs_score: number;
  composite_score: number;
  grade: string;
  entry_type: string;
  entry: number;
  cheat_entry: number;
  retest_entry: number;
  sl: number;
  sl_pct: number;
  buffer_to_sl_pct?: number;
  t1: number;
  t2: number;
  t3: number | null;
  status: string;
  close: number;
  wk52_pos?: number;
  risk_reward?: number;
  max_upside_pct?: number;
  dist_to_bo_pct?: number;
  sector?: string;
  liq_grab: boolean;
  equal_lows: boolean;
  equal_lows_level?: number | null;
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

const ENTRY_TYPE_COLORS: Record<string, string> = {
  'LiqGrab':  'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
  'Cheat':    'bg-purple-500/20 text-purple-400 border-purple-500/30',
  'Breakout': 'bg-blue-500/20 text-blue-400 border-blue-500/30',
};

const ENTRY_TYPE_LABELS: Record<string, string> = {
  'LiqGrab':  '⚡ Liq Grab',
  'Cheat':    '🎯 Cheat',
  'Breakout': '🚀 Breakout',
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

  const [entryTypeFilter, setEntryTypeFilter] = useState<string>('All');
  const [sectorFilter, setSectorFilter] = useState<string>('All');
  const [minScoreFilter, setMinScoreFilter] = useState(55);

  const [sortCol, setSortCol] = useState<string>('composite_score');
  const [sortAsc, setSortAsc] = useState(false);
  const ALL_COLUMNS = [
    { key: 'sector',           label: 'Sector',           group: 'core' },
    { key: 'entry_type',       label: 'Entry Type',       group: 'core' },
    { key: 'composite_score',  label: 'Score',            group: 'core' },
    { key: 'grade',            label: 'Grade',            group: 'core' },
    { key: 'entry',            label: 'Entry',            group: 'core' },
    { key: 'sl',               label: 'SL',               group: 'core' },
    { key: 'max_upside_pct',   label: 'Upside %',         group: 'core' },
    { key: 'status',           label: 'Status',           group: 'core' },
    { key: 'dar_median',       label: 'DAR % (Absorption)', group: 'signals' },
    { key: 'volume_ratio',     label: 'Vol Ratio',        group: 'signals' },
    { key: 'vol_dry_up',       label: 'Dry-Up',           group: 'signals' },
    { key: 'rs_score',         label: 'RS vs Nifty',      group: 'signals' },
    { key: 'wk52_pos',         label: '52W Position',     group: 'signals' },
    { key: 'market_cap_cr',    label: 'MCap Cr',          group: 'signals' },
    { key: 'close',            label: 'Close (CMP)',      group: 'trade' },
    { key: 'cheat_entry',      label: 'Cheat / Retest',   group: 'trade' },
    { key: 'sl_pct',           label: 'SL %',             group: 'trade' },
    { key: 'buffer_to_sl_pct', label: 'Buffer to SL',     group: 'trade' },
    { key: 't1',               label: 'T1 (Conservative)', group: 'trade' },
    { key: 't2',               label: 'T2 (Primary)',     group: 'trade' },
    { key: 't3',               label: 'T3 (Multibagger)', group: 'trade' },
    { key: 'dist_to_bo_pct',   label: '→ Breakout',       group: 'trade' },
  ] as const;

  type ColKey = typeof ALL_COLUMNS[number]['key'];

  const DEFAULT_VISIBLE = new Set<ColKey>([
    'sector', 'entry_type', 'composite_score', 'grade',
    'entry', 'sl', 'max_upside_pct', 'status',
    'dar_median', 'vol_dry_up', 'sl_pct', 'buffer_to_sl_pct',
  ]);

  const [visibleCols, setVisibleCols] = useState<Set<ColKey>>(() => {
    try {
      const saved = localStorage.getItem('mb_visible_cols');
      if (saved) return new Set(JSON.parse(saved)) as Set<ColKey>;
    } catch {}
    return DEFAULT_VISIBLE;
  });
  const [colPanelOpen, setColPanelOpen] = useState(false);
  const colPanelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (colPanelRef.current && !colPanelRef.current.contains(e.target as Node)) {
        setColPanelOpen(false);
      }
    };
    if (colPanelOpen) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [colPanelOpen]);

  const toggleCol = (key: ColKey) => {
    setVisibleCols(prev => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      localStorage.setItem('mb_visible_cols', JSON.stringify(Array.from(next)));
      return next;
    });
  };

  const isVisible = (key: ColKey) => visibleCols.has(key);

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
    if (entryTypeFilter !== 'All') data = data.filter(d => d.entry_type === entryTypeFilter);
    if (sectorFilter !== 'All') data = data.filter(d => d.sector === sectorFilter);
    if (minScoreFilter > 0) data = data.filter(d => d.composite_score >= minScoreFilter);
    data.sort((a, b) => {
      const av = (a as any)[sortCol] ?? 0;
      const bv = (b as any)[sortCol] ?? 0;
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortAsc ? av - bv : bv - av;
      }
      return String(av).localeCompare(String(bv)) * (sortAsc ? 1 : -1);
    });
    return data;
  }, [candidates, mcapRange, watchlistOnly, entryTypeFilter, sectorFilter, minScoreFilter, isWatched, sortCol, sortAsc]);

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
    <main className="flex flex-col flex-1 min-h-0 relative gap-4 p-4" aria-label="Multibagger Pro Scanner">
      {/* Staleness Warning */}
      {isStale && staleBannerOpen && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded px-4 py-2 flex items-center gap-2 text-xs font-mono" role="alert">
          <AlertTriangle size={14} className="text-amber-400 shrink-0" aria-hidden="true" />
          <span className="text-amber-300/90">Data may be stale — re-scan recommended (last scan &gt; 30 min ago).</span>
          <button onClick={() => setStaleBannerOpen(false)} className="ml-auto text-amber-500/50 hover:text-amber-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/50 rounded" aria-label="Dismiss stale warning">
            <XCircle size={14} aria-hidden="true" />
          </button>
        </div>
      )}

      {/* Header */}
      <header className="flex justify-between items-center bg-[#1a1c24] border border-[#ffffff1a] rounded p-4">
        <div className="flex items-center gap-3">
          <div className="bg-purple-500/20 p-2 rounded" aria-hidden="true">
            <Rocket className="text-purple-400" size={24} />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-[#fafafa]">Multibagger Pro Scanner</h1>
            <div className="flex items-center gap-2">
              <p className="text-xs font-mono text-[#888]">Accumulation Base Breakout Detection</p>
              {bearMarket && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold border border-orange-500/40 bg-orange-500/15 text-orange-400" title="Minimum thresholds raised: base_days≥30, min_dar≥0.4%" aria-label="Risk-off mode — thresholds raised">
                  ⚠ Risk-Off
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 ml-auto mr-3">
          {/* Lookback presets */}
          <div className="flex items-center gap-1" role="group" aria-label="Lookback presets">
            <span className="text-[10px] text-[#555] font-mono" aria-hidden="true">Lookback:</span>
            {[
              { label: 'Quick 21d', days: 21 },
              { label: 'Quality 42d', days: 42 },
              { label: 'Strong 63d', days: 63 },
            ].map(p => (
              <button
                key={p.days}
                onClick={() => setBaseDays(p.days)}
                className={`px-2 py-1 rounded border text-[10px] font-mono transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-500/50 ${
                  baseDays === p.days
                    ? 'bg-purple-500/20 border-purple-500/40 text-purple-400'
                    : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#666] hover:text-[#aaa]'
                }`}
                aria-pressed={baseDays === p.days}
                aria-label={`Set lookback to ${p.days} days`}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
        <button
          onClick={startScan}
          disabled={isScanning}
          className="px-4 py-2 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white rounded text-xs font-semibold flex items-center gap-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-400/50"
          aria-label={isScanning ? 'Scanning, please wait' : 'Start scan'}
        >
          {isScanning ? (
            <><RefreshCw size={14} className="animate-spin" aria-hidden="true" /> Scanning...</>
          ) : (
            <><Rocket size={14} fill="currentColor" aria-hidden="true" /> Scan</>
          )}
        </button>
      </header>

      {/* Progress / Status Bar */}
      {isScanning && (
        <div className="bg-cyan-500/10 border border-cyan-500/30 rounded p-3" role="progressbar" aria-valuenow={progressPct} aria-valuemin={0} aria-valuemax={100} aria-label="Scan progress">
          <div className="flex items-center gap-2 text-xs font-mono text-cyan-300 mb-2">
            <RefreshCw size={14} className="animate-spin" aria-hidden="true" />
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

      {/* Filters (always visible) */}
      <section className="bg-[#0e1117] border border-[#ffffff1a] rounded p-4 flex flex-wrap gap-4 items-end" aria-label="Filters">
        <div className="flex items-center gap-2 mb-1 text-xs text-[#888] w-full">
          <Filter size={14} aria-hidden="true" /> <span className="font-mono uppercase font-semibold">Filters</span>
        </div>
        <div className="flex flex-col gap-1 w-24">
          <label className="text-[10px] text-[#888] font-mono" id="lookback-label">Lookback Days</label>
          <input
            type="number"
            min={7}
            max={90}
            value={baseDays}
            onChange={e => setBaseDays(Number(e.target.value))}
            className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-purple-500 outline-none w-full font-mono focus-visible:ring-2 focus-visible:ring-purple-500/50"
            aria-labelledby="lookback-label"
          />
        </div>
        <div className="flex flex-col gap-1 w-24">
          <label className="text-[10px] text-[#888] font-mono" id="min-dar-label">Min DAR %</label>
          <input
            type="number"
            min={0}
            max={10}
            step={0.1}
            value={minDar}
            onChange={e => setMinDar(Number(e.target.value))}
            className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-purple-500 outline-none w-full font-mono focus-visible:ring-2 focus-visible:ring-purple-500/50"
            aria-labelledby="min-dar-label"
          />
        </div>
        <div className="flex flex-col gap-1 w-24">
          <label className="text-[10px] text-[#888] font-mono" id="target-dar-label">Target DAR %</label>
          {targetDar !== null ? (
            <div className="flex items-center gap-1">
              <input
                type="number"
                min={0.1}
                max={2.0}
                step={0.1}
                value={targetDar}
                onChange={e => setTargetDar(Number(e.target.value))}
                className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-purple-500 outline-none w-full font-mono focus-visible:ring-2 focus-visible:ring-purple-500/50"
                aria-labelledby="target-dar-label"
              />
              <button
                onClick={() => setTargetDar(null)}
                className="text-[9px] text-purple-400 hover:text-purple-300 font-mono shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-500/50 rounded"
                aria-label="Reset target DAR to auto"
              >
                Reset
              </button>
            </div>
          ) : (
            <div
              onClick={() => setTargetDar(0.5)}
              className="bg-[#1a1c24] border border-purple-500/30 rounded px-2 py-1.5 text-xs text-purple-400 font-mono cursor-pointer text-center"
              role="button"
              aria-label="Set target DAR to auto (0.5%)"
            >
              Auto
            </div>
          )}
        </div>
        <div className="flex flex-col gap-1 w-28">
          <label className="text-[10px] text-[#888] font-mono" id="tightness-full-label">Tightness Full %</label>
          <input
            type="range"
            min={2}
            max={20}
            step={0.5}
            value={tightnessFull ?? 2}
            onChange={e => setTightnessFull(Number(e.target.value))}
            disabled={tightnessFull === null}
            className="w-full accent-purple-500 disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-500/50"
            aria-labelledby="tightness-full-label"
          />
          <div className="flex items-center justify-between">
            {tightnessFull !== null ? (
              <>
                <span className="text-[10px] text-[#ccc] font-mono">{tightnessFull.toFixed(1)}</span>
                <button onClick={() => setTightnessFull(null)} className="text-[9px] text-purple-400 hover:text-purple-300 font-mono focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-500/50 rounded" aria-label="Reset tightness full to auto">Reset</button>
              </>
            ) : (
              <span className="text-[10px] text-purple-400 font-mono">Auto</span>
            )}
          </div>
        </div>
        <div className="flex flex-col gap-1 w-28">
          <label className="text-[10px] text-[#888] font-mono" id="tightness-zero-label">Tightness Zero %</label>
          <input
            type="range"
            min={10}
            max={50}
            step={0.5}
            value={tightnessZero ?? 10}
            onChange={e => setTightnessZero(Number(e.target.value))}
            disabled={tightnessZero === null}
            className="w-full accent-purple-500 disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-500/50"
            aria-labelledby="tightness-zero-label"
          />
          <div className="flex items-center justify-between">
            {tightnessZero !== null ? (
              <>
                <span className="text-[10px] text-[#ccc] font-mono">{tightnessZero.toFixed(1)}</span>
                <button onClick={() => setTightnessZero(null)} className="text-[9px] text-purple-400 hover:text-purple-300 font-mono focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-500/50 rounded" aria-label="Reset tightness zero to auto">Reset</button>
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
          <div className="text-[10px] text-[#888] font-mono">Entry Type</div>
          <div className="flex gap-1">
            {['All', 'LiqGrab', 'Cheat', 'Breakout'].map(t => (
              <button
                key={t}
                onClick={() => setEntryTypeFilter(t)}
                className={`px-2 py-1.5 rounded border text-[10px] font-mono transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-500/50 ${
                  entryTypeFilter === t
                    ? (t === 'LiqGrab' ? 'bg-emerald-500/20 border-emerald-500/40 text-emerald-400' :
                       t === 'Cheat'   ? 'bg-purple-500/20 border-purple-500/40 text-purple-400' :
                       t === 'Breakout'? 'bg-blue-500/20 border-blue-500/40 text-blue-400' :
                                         'bg-white/10 border-white/20 text-white')
                    : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#888] hover:text-white'
                }`}
                aria-pressed={entryTypeFilter === t}
                aria-label={`Filter by ${t === 'All' ? 'all entry types' : t}`}
              >
                {t === 'All' ? 'All' : ENTRY_TYPE_LABELS[t]}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-1">
          <div className="flex justify-between text-[10px] text-[#888] font-mono items-center">
            <Tooltip
              content="Minimum Accumulation Score to show. Recommended: 55+ for quality setups. Below 55 is noise for most users."
              good="55–70: good quality filter. 70+: only high-conviction setups."
            >
              <span>Min Score</span>
            </Tooltip>
            <span className="text-purple-400">{minScoreFilter}</span>
          </div>
          <input
            type="range"
            min={0}
            max={100}
            step={5}
            value={minScoreFilter}
            onChange={e => setMinScoreFilter(Number(e.target.value))}
            className="w-full accent-purple-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-500/50"
            aria-label="Minimum accumulation score"
          />
        </div>
        <div className="flex flex-col gap-1">
          <div className="text-[10px] text-[#888] font-mono" id="sector-filter-label">Sector</div>
          <select
            value={sectorFilter}
            onChange={e => setSectorFilter(e.target.value)}
            className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-purple-500 outline-none font-mono focus-visible:ring-2 focus-visible:ring-purple-500/50"
            aria-labelledby="sector-filter-label"
          >
            {availableSectors.map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
      </section>

      {/* Results */}
      {(scanStatus?.scan_status === 'completed' || (isIdle && candidates.length > 0)) && !isScanning && (
        <>
          {/* Stats Summary */}
          <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider">Candidates</div>
              <div className="text-2xl font-bold text-[#fafafa]">{filteredData.length}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider">Grade A</div>
              <div className="text-2xl font-bold text-green-400">{filteredData.filter(d => d.grade === 'A').length}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[10px] text-emerald-400 font-mono uppercase tracking-wider">⚡ Liq Grabs</div>
              <div className="text-2xl font-bold text-emerald-400">{filteredData.filter(d => d.liq_grab).length}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-3">
              <div className="text-[10px] text-purple-400 font-mono uppercase tracking-wider">🎯 Cheat</div>
              <div className="text-2xl font-bold text-purple-400">{filteredData.filter(d => d.entry_type === 'Cheat').length}</div>
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
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider">Breakouts</div>
              <div className="flex items-baseline gap-1.5">
                <span className="text-2xl font-bold text-green-400">
                  {filteredData.filter(d => d.status === 'Triggered').length}
                </span>
                <span className="text-xs text-yellow-400 font-mono">
                  +{filteredData.filter(d => d.status === 'Breakout Pending').length} pending
                </span>
              </div>
            </div>
          </div>

          {/* Grade A Spotlight */}
          {filteredData.filter(d => d.grade === 'A').length > 0 && (
            <div className="bg-green-500/5 border border-green-500/20 rounded p-3">
              <div className="text-[10px] text-green-400 font-mono uppercase tracking-wider mb-2 flex items-center gap-2">
                <span>Grade A Candidates</span>
                <span className="text-[#666]">— highest conviction setups</span>
              </div>
              <div className="flex flex-wrap gap-2">
                {filteredData
                  .filter(d => d.grade === 'A')
                  .map(d => (
                    <div key={d.symbol}
                      className={`flex items-center gap-1.5 px-2 py-1 rounded border text-[11px] font-mono ${
                        d.liq_grab ? 'border-emerald-500/40 bg-emerald-500/10' :
                        d.entry_type === 'Cheat' ? 'border-purple-500/30 bg-purple-500/10' :
                        'border-green-500/20 bg-[#1a1c24]'
                      }`}
                    >
                      <StarButton symbol={d.symbol} size={10} />
                      <span className="text-white font-bold">{d.symbol}</span>
                      <span className="text-[#888]">{d.sector ?? ''}</span>
                      <span className={`text-[10px] px-1 rounded ${ENTRY_TYPE_COLORS[d.entry_type]}`}>
                        {ENTRY_TYPE_LABELS[d.entry_type]}
                      </span>
                      <span className="text-green-400">{d.entry.toFixed(2)}</span>
                      <span className="text-red-400 text-[10px]">SL {d.sl.toFixed(2)}</span>
                      {d.equal_lows && (
                        <span
                          className="text-orange-400"
                          title={`Equal lows at ₹${d.equal_lows_level?.toFixed(2) ?? '?'} — expect liquidity sweep`}
                        >
                          ⚠ {d.equal_lows_level != null ? `₹${d.equal_lows_level.toFixed(0)}` : ''}
                        </span>
                      )}
                    </div>
                  ))
                }
              </div>
            </div>
          )}

          {/* Column visibility toggles */}
          <div className="flex items-center gap-2 px-1 relative" ref={colPanelRef}>
            <button
              onClick={() => setColPanelOpen(o => !o)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded border text-[11px] font-mono transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-500/50 ${
                colPanelOpen
                  ? 'bg-purple-500/20 border-purple-500/40 text-purple-400'
                  : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#888] hover:text-[#ccc]'
              }`}
              aria-expanded={colPanelOpen}
              aria-label={`Toggle column visibility panel, ${visibleCols.size} columns visible`}
            >
              <Filter size={11} aria-hidden="true" />
              Columns ({visibleCols.size})
            </button>

            {/* Sort indicator when sorting by hidden column */}
            {!isVisible(sortCol as ColKey) && (
              <span className="text-[10px] text-yellow-400 font-mono flex items-center gap-1" role="status">
                <ArrowUpDown size={10} aria-hidden="true" />
                Sorted by hidden column: {ALL_COLUMNS.find(c => c.key === sortCol)?.label ?? sortCol}
              </span>
            )}

            <span className="ml-auto text-[10px] text-[#555] font-mono" role="status" aria-live="polite">
              {filteredData.length} results
            </span>

            {colPanelOpen && (
              <div className="absolute top-full left-0 mt-1 z-50 bg-[#0e1117] border border-[#ffffff20] rounded-lg shadow-2xl p-4 w-80">
                <div className="flex items-center justify-between mb-3">
                  <span className="text-[11px] text-[#ccc] font-mono font-bold uppercase tracking-wider">Visible Columns</span>
                  <button
                    onClick={() => {
                      setVisibleCols(DEFAULT_VISIBLE);
                      localStorage.setItem('mb_visible_cols', JSON.stringify(Array.from(DEFAULT_VISIBLE)));
                    }}
                    className="text-[10px] text-purple-400 hover:text-purple-300 font-mono focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-500/50 rounded"
                    aria-label="Reset columns to default"
                  >
                    Reset to default
                  </button>
                </div>
                {(['core', 'signals', 'trade'] as const).map(group => (
                  <div key={group} className="mb-3">
                    <div className={`text-[9px] font-mono uppercase tracking-widest mb-1.5 ${
                      group === 'core' ? 'text-[#666]' :
                      group === 'signals' ? 'text-cyan-500/60' : 'text-purple-500/60'
                    }`}>
                      {group === 'core' ? 'Core' : group === 'signals' ? 'Signal Analysis' : 'Trade Levels'}
                    </div>
                    <div className="grid grid-cols-2 gap-1">
                      {ALL_COLUMNS.filter(c => c.group === group).map(col => (
                        <label key={col.key} className="flex items-center gap-1.5 cursor-pointer group">
                          <input
                            type="checkbox"
                            checked={isVisible(col.key)}
                            onChange={() => toggleCol(col.key)}
                            className="accent-purple-500 cursor-pointer focus-visible:ring-2 focus-visible:ring-purple-500/50"
                          />
                          <span className="text-[10px] text-[#aaa] group-hover:text-white transition-colors font-mono">
                            {col.label}
                          </span>
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Table */}
          <div className="flex-1 bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden">
            <ScrollableTable>
              <table
                className="w-full min-w-max text-left text-xs font-mono whitespace-nowrap"
                role="grid"
                aria-label="Multibagger Pro Scanner results"
                aria-rowcount={filteredData.length}
                aria-colcount={1 + ALL_COLUMNS.filter(c => isVisible(c.key)).length}
              >
                <thead className="sticky top-0 z-20 text-[#888]">
                  <tr style={{ boxShadow: '0 1px 0 0 rgba(255,255,255,0.08), 0 2px 4px 0 rgba(0,0,0,0.4)' }}>
                    {/* ── CORE (always visible) ── */}
                    <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('symbol')} scope="col" aria-sort={sortCol === 'symbol' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip content="Stock ticker symbol. ⚠ means equal lows detected — a liquidity trap likely exists below the base." showIcon={false}>
                        Symbol <SortIcon column="symbol" />
                      </Tooltip>
                    </th>
                    {isVisible('sector') && <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider" scope="col">
                      <Tooltip content="Business sector the company operates in. Useful for avoiding concentration — don't put all picks in one sector." showIcon={false}>Sector</Tooltip>
                    </th>}
                    {isVisible('entry_type') && <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center" scope="col">
                      <Tooltip
                        content="How to enter this stock. Each type has different risk-reward."
                        good="⚡ Liq Grab = best entry, stock swept stops then recovered. 🎯 Cheat = enter inside the base at lower end. 🚀 Breakout = enter above resistance."
                        bad="Breakout entries have worst R:R and highest fakeout risk."
                      >Entry Type</Tooltip>
                    </th>}
                    {isVisible('composite_score') && <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('composite_score')} scope="col" aria-sort={sortCol === 'composite_score' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip
                        content="Accumulation Score (0–100). Combines delivery absorption, base tightness, volume character, and delivery trend into one number."
                        good="Above 70: strong accumulation evidence. Above 85: exceptional setup."
                        bad="Below 55: marginal setup, skip unless other signals are outstanding."
                        example="Score 80 = stock scoring well on all 4 dimensions simultaneously."
                      >Score <SortIcon column="composite_score" /></Tooltip>
                    </th>}
                    {isVisible('grade') && <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('grade')} scope="col" aria-sort={sortCol === 'grade' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip
                        content="Overall grade based on score. A=80+, B=60–79, C=40–59, D=below 40."
                        good="Focus on A and B grade only. C grade requires strong conviction from fundamentals."
                        bad="D grade: avoid."
                      >Grade <SortIcon column="grade" /></Tooltip>
                    </th>}
                    {isVisible('entry') && <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right" scope="col">
                      <Tooltip
                        content="Recommended buy price. For Breakout: just above the base ceiling. For Cheat: current price (enter now). For Liq Grab: close of the sweep candle."
                        good="The earlier you enter (Cheat/LiqGrab), the better your risk-reward."
                        example="Entry 150 with SL 140 means you risk ₹10 per share."
                      >Entry</Tooltip>
                    </th>}
                    {isVisible('sl') && <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right" scope="col">
                      <Tooltip
                        content="Stop Loss — the price where you exit if the setup fails. Placed below the base structure with buffer for minor stop hunts."
                        bad="If price closes below SL on meaningful volume, the accumulation thesis is broken — exit without hesitation."
                        example="SL 140 means if stock drops below ₹140, sell and protect capital."
                      >SL</Tooltip>
                    </th>}
                    {isVisible('max_upside_pct') && <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('max_upside_pct')} scope="col" aria-sort={sortCol === 'max_upside_pct' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip
                        content="Maximum potential upside % from entry to the final target (T3 for Grade A/B, T2 otherwise)."
                        good="Above 40%: meaningful multibagger potential. Above 100%: true multibagger territory."
                        bad="Below 20%: risk-reward not worth it for a 1+ month holding."
                      >Upside% <SortIcon column="max_upside_pct" /></Tooltip>
                    </th>}
                    {isVisible('status') && <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-center" scope="col">
                      <Tooltip
                        content="Current setup status. 'In Base' = still consolidating, wait. 'Breakout Pending' = near resistance. 'Triggered' = breakout confirmed with volume."
                        good="'In Base' with Cheat/LiqGrab entry type = best time to enter."
                        bad="'Triggered' with Breakout entry = you may be late, wait for a retest."
                      >Status</Tooltip>
                    </th>}

                    {/* ── SIGNALS (toggleable) ── */}
                    {isVisible('dar_median') && <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white bg-cyan-500/5 border-l border-cyan-500/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-500/50" onClick={() => handleSort('dar_median')} scope="col" aria-sort={sortCol === 'dar_median' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip
                        content="Delivery Absorption Rate — what % of the company's free-float market cap is being bought and held each day. Normalised so small companies and large companies are comparable."
                        good="Above target threshold (auto-set by mcap): genuine accumulation happening."
                        bad="Below 0.2%: not enough buying to signal institutional interest."
                        example="DAR 0.5% means 0.5% of the tradeable float changes hands as delivery daily — unusually high."
                      >DAR% (Absorption) <SortIcon column="dar_median" /></Tooltip>
                    </th>}
                    {isVisible('volume_ratio') && <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white bg-cyan-500/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-500/50" onClick={() => handleSort('volume_ratio')} scope="col" aria-sort={sortCol === 'volume_ratio' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip
                        content="Volume Character — median volume on up-days divided by median volume on down-days within the base. Above 1.0 means more shares traded on green days than red days."
                        good="Above 1.5: strong accumulation signature. Buyers are more active than sellers."
                        bad="Below 0.8: sellers are more active — potential distribution, not accumulation."
                      >Vol Ratio (Character) <SortIcon column="volume_ratio" /></Tooltip>
                    </th>}
                    {isVisible('vol_dry_up') && <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white bg-cyan-500/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-500/50" onClick={() => handleSort('vol_dry_up')} scope="col" aria-sort={sortCol === 'vol_dry_up' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip
                        content="Volume Dry-Up — last 5 days volume vs full base average. Below 1.0 means volume is shrinking, which happens when supply is exhausted and the float is locked up."
                        good="Below 0.7: volume compression (green). Float is locked — small buying pressure will move price."
                        bad="Above 1.2: volume expanding inside base (orange) — could be distribution."
                      >Dry-Up (Vol Compress) <SortIcon column="vol_dry_up" /></Tooltip>
                    </th>}
                    {isVisible('rs_score') && <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white bg-cyan-500/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-500/50" onClick={() => handleSort('rs_score')} scope="col" aria-sort={sortCol === 'rs_score' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip
                        content="Relative Strength — how the stock performed vs Nifty 50 during the base period. Positive = stock held up or outperformed while market consolidated."
                        good="Above 0.3: stock is stronger than the market — institutional support likely."
                        bad="Below -0.3: underperforming even when it should be recovering — weak setup."
                      >RS (Vs Nifty) <SortIcon column="rs_score" /></Tooltip>
                    </th>}
                    {isVisible('wk52_pos') && <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white bg-cyan-500/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-500/50" onClick={() => handleSort('wk52_pos')} scope="col" aria-sort={sortCol === 'wk52_pos' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip
                        content="Where the stock sits within its 52-week high-low range. 0% = at 52W low, 100% = at 52W high."
                        good="25–60%: corrected from highs but not broken — ideal accumulation zone."
                        bad="Above 75%: near 52W high — limited room before facing resistance. Below 15%: may be a falling knife."
                      >52W Position <SortIcon column="wk52_pos" /></Tooltip>
                    </th>}
                    {isVisible('market_cap_cr') && <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white bg-cyan-500/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-500/50" onClick={() => handleSort('market_cap_cr')} scope="col" aria-sort={sortCol === 'market_cap_cr' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      MCap Cr <SortIcon column="market_cap_cr" />
                    </th>}

                    {/* ── TRADE LEVELS (toggleable) ── */}
                    {isVisible('close') && <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right bg-purple-500/5 border-l border-purple-500/20" scope="col">
                      <Tooltip content="Current market price of the stock. Compare with Entry to see how far you are from the recommended entry point.">Close (CMP)</Tooltip>
                    </th>}
                    {isVisible('cheat_entry') && <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right bg-purple-500/5" scope="col">
                      <Tooltip
                        content="Alternative entry for Breakout-type setups. For Breakout stocks: enter at the 38.2% level inside the base for better risk-reward. For Cheat/LiqGrab: not applicable."
                        good="Cheat/Retest entry gives 2-3x better risk-reward than waiting for the breakout."
                      >Cheat/Retest <SortIcon column="cheat_entry" /></Tooltip>
                    </th>}
                    {isVisible('sl_pct') && <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white bg-purple-500/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('sl_pct')} scope="col" aria-sort={sortCol === 'sl_pct' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip
                        content="Stop Loss as a % of entry price. Lower is better — it means you risk less capital to participate in the setup."
                        good="Below 5%: tight stop, excellent risk control."
                        bad="Above 12%: wide stop — size your position smaller to keep total risk manageable."
                      >SL% (Risk) <SortIcon column="sl_pct" /></Tooltip>
                    </th>}
                    {isVisible('buffer_to_sl_pct') && <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white bg-purple-500/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('buffer_to_sl_pct')} scope="col" aria-sort={sortCol === 'buffer_to_sl_pct' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip
                        content="Buffer to SL — how much space exists between current entry price and the stop loss. Higher = safer, less chance of being stopped on normal volatility."
                        good="Above 8%: enough room for normal price swings."
                        bad="Below 4% (red): dangerously close to SL — one bad day stops you out. Avoid entering. Wait for price to move up or for a confirmed liquidity grab."
                      >Buffer to SL <SortIcon column="buffer_to_sl_pct" /></Tooltip>
                    </th>}
                    {isVisible('t1') && <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right bg-purple-500/5" scope="col">
                      <Tooltip content="Target 1 — conservative exit. Take partial profits here (suggest 30% of position). Equivalent to 1× your risk amount above entry.">T1 (Conservative)</Tooltip>
                    </th>}
                    {isVisible('t2') && <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right bg-purple-500/5" scope="col">
                      <Tooltip content="Target 2 — primary exit. Take bulk of position here (suggest 50%). Equivalent to 2.5× your risk amount above entry.">T2 (Primary)</Tooltip>
                    </th>}
                    {isVisible('t3') && <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right bg-purple-500/5" scope="col">
                      <Tooltip
                        content="Target 3 — multibagger exit. Only for Grade A and B setups. Let 20% of position ride here. Equivalent to 5× your risk amount above entry."
                        good="Only available for Grade A/B. This is the 'let it run' target for genuine multibaggers."
                      >T3 (Multibagger)</Tooltip>
                    </th>}
                    {isVisible('dist_to_bo_pct') && <th className="px-4 py-3 bg-[#0e1117] font-semibold uppercase tracking-wider text-right cursor-pointer hover:text-white bg-purple-500/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50" onClick={() => handleSort('dist_to_bo_pct')} scope="col" aria-sort={sortCol === 'dist_to_bo_pct' ? (sortAsc ? 'ascending' : 'descending') : 'none'}>
                      <Tooltip
                        content="Distance to Breakout — how far the current price is from the base ceiling (breakout level). Lower = closer to triggering."
                        good="Below 3%: breakout imminent — watch closely."
                        bad="Above 15%: stock still deep in base — you have time to monitor."
                      >→ Breakout <SortIcon column="dist_to_bo_pct" /></Tooltip>
                    </th>}
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#ffffff0a]">
                  {filteredData.length === 0 ? (
                    <tr>
                      <td colSpan={1 + ALL_COLUMNS.filter(c => isVisible(c.key)).length} className="px-4 py-8 text-center text-[#666]">No candidates match current filters.</td>
                    </tr>
                  ) : (
                    filteredData.map((row, index) => (
                      <tr key={row.symbol} role="row" aria-rowindex={index + 1} className={`hover:bg-[#ffffff05] transition-colors ${row.liq_grab ? 'border-l-2 border-emerald-500/50' : (row.buffer_to_sl_pct ?? 99) < 4 ? 'border-l-2 border-red-500/40 opacity-60' : ''}`}>
                        {/* ── CORE ── */}
                        <td className="px-4 py-3 font-bold" scope="row">
                          <div className="flex items-center gap-1.5">
                            <StarButton symbol={row.symbol} size={11} />
                            <button
                              onClick={() => window.open(`/#/chart?symbol=${encodeURIComponent(row.symbol)}`, '_blank')}
                              className="text-[#fafafa] hover:text-purple-400 inline-flex items-center gap-1 transition-colors group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-purple-500/50"
                              aria-label={`Open chart for ${row.symbol}`}
                            >
                              {row.symbol}
                              {row.equal_lows && (
                                <span
                                  title={`Equal lows at ₹${row.equal_lows_level?.toFixed(2) ?? '?'} — expect liquidity sweep`}
                                  className="text-orange-400"
                                >
                                  ⚠
                                </span>
                              )}
                              <ArrowUpRight size={12} className="opacity-0 group-hover:opacity-100" aria-hidden="true" />
                            </button>
                          </div>
                        </td>
                        {isVisible('sector') && <td className="px-4 py-3 text-[#888] text-[11px] max-w-[120px] truncate" title={row.sector}>
                          {row.sector ?? '—'}
                        </td>}
                        {isVisible('entry_type') && <td className="px-4 py-3 text-center">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold border ${ENTRY_TYPE_COLORS[row.entry_type] || 'bg-[#ffffff1a] text-[#aaa]'}`}>
                            {ENTRY_TYPE_LABELS[row.entry_type] || row.entry_type}
                          </span>
                        </td>}
                        {isVisible('composite_score') && <td className="px-4 py-3 text-right font-bold">
                          <span className={row.composite_score >= 80 ? 'text-green-400' : row.composite_score >= 60 ? 'text-blue-400' : row.composite_score >= 40 ? 'text-yellow-400' : 'text-red-400'}>
                            {row.composite_score.toFixed(1)}
                          </span>
                        </td>}
                        {isVisible('grade') && <td className="px-4 py-3 text-center">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold border ${GRADE_COLORS[row.grade] || 'bg-[#ffffff1a] text-[#aaa]'}`}>
                            {row.grade}
                          </span>
                        </td>}
                        {isVisible('entry') && <td className="px-4 py-3 text-right text-green-400 font-semibold">{row.entry.toFixed(2)}</td>}
                        {isVisible('sl') && <td className="px-4 py-3 text-right text-red-400">{row.sl.toFixed(2)}</td>}
                        {isVisible('max_upside_pct') && <td className="px-4 py-3 text-right">
                          <span className={(row.max_upside_pct ?? 0) >= 40 ? 'text-green-400' : (row.max_upside_pct ?? 0) >= 20 ? 'text-cyan-400' : 'text-[#888]'}>
                            {row.max_upside_pct !== undefined ? `+${row.max_upside_pct.toFixed(1)}%` : '—'}
                          </span>
                        </td>}
                        {isVisible('status') && <td className="px-4 py-3 text-center">
                          <span className={`text-[10px] font-semibold ${STATUS_COLORS[row.status] || 'text-[#aaa]'}`}>
                            {row.status}
                          </span>
                        </td>}

                        {/* ── SIGNALS ── */}
                        {isVisible('dar_median') && <td className="px-4 py-3 text-right text-[#ccc] bg-cyan-500/[0.02] border-l border-cyan-500/10">{row.dar_median.toFixed(3)}</td>}
                        {isVisible('volume_ratio') && <td className="px-4 py-3 text-right bg-cyan-500/[0.02]">
                          <span className={row.volume_ratio >= 1.5 ? 'text-green-400' : row.volume_ratio <= 0.8 ? 'text-red-400' : 'text-[#ccc]'}>
                            {row.volume_ratio.toFixed(2)}
                          </span>
                        </td>}
                        {isVisible('vol_dry_up') && <td className="px-4 py-3 text-right bg-cyan-500/[0.02]">
                          <span className={row.vol_dry_up <= 0.6 ? 'text-green-400' : row.vol_dry_up >= 1.2 ? 'text-orange-400' : 'text-[#ccc]'}>
                            {row.vol_dry_up !== undefined ? row.vol_dry_up.toFixed(2) : '—'}
                          </span>
                        </td>}
                        {isVisible('rs_score') && <td className="px-4 py-3 text-right bg-cyan-500/[0.02]">
                          <span className={row.rs_score > 0.3 ? 'text-green-400' : row.rs_score < -0.3 ? 'text-red-400' : 'text-[#ccc]'}>
                            {row.rs_score !== undefined ? row.rs_score.toFixed(2) : '—'}
                          </span>
                        </td>}
                        {isVisible('wk52_pos') && <td className="px-4 py-3 text-right bg-cyan-500/[0.02]">
                          <span className={(row.wk52_pos ?? 50) > 75 ? 'text-orange-400' : (row.wk52_pos ?? 50) < 20 ? 'text-red-400' : 'text-[#ccc]'}>
                            {row.wk52_pos !== undefined ? `${row.wk52_pos.toFixed(0)}%` : '—'}
                          </span>
                        </td>}
                        {isVisible('market_cap_cr') && <td className="px-4 py-3 text-right text-[#ccc] bg-cyan-500/[0.02]">{row.market_cap_cr?.toFixed(1) ?? '—'}</td>}

                        {/* ── TRADE LEVELS ── */}
                        {isVisible('close') && <td className="px-4 py-3 text-right text-[#ccc] bg-purple-500/[0.02] border-l border-purple-500/10">{row.close.toFixed(2)}</td>}
                        {isVisible('cheat_entry') && <td className="px-4 py-3 text-right bg-purple-500/[0.02]">
                          {row.entry_type === 'Breakout'
                            ? <span className="text-purple-400">{row.cheat_entry?.toFixed(2) ?? '—'}</span>
                            : <span className="text-[#333]">—</span>}
                        </td>}
                        {isVisible('sl_pct') && <td className="px-4 py-3 text-right bg-purple-500/[0.02]">
                          <span className={row.sl_pct <= 5 ? 'text-green-400' : row.sl_pct <= 10 ? 'text-yellow-400' : 'text-red-400'}>
                            {row.sl_pct?.toFixed(1)}%
                          </span>
                        </td>}
                        {isVisible('buffer_to_sl_pct') && (
                          <td className="px-4 py-3 text-right bg-purple-500/[0.02]">
                            <Tooltip
                              content={
                                (row.buffer_to_sl_pct ?? 99) < 4
                                  ? "⚠ Dangerously close to SL. Do not enter. Wait for price to move up from this level or for a confirmed liquidity sweep and recovery."
                                  : (row.buffer_to_sl_pct ?? 99) < 8
                                  ? "Moderate buffer. Be cautious — size smaller than usual."
                                  : "Healthy buffer. Normal entry sizing appropriate."
                              }
                              showIcon={false}
                            >
                              <span className={
                                (row.buffer_to_sl_pct ?? 99) < 4 ? 'text-red-400 font-bold' :
                                (row.buffer_to_sl_pct ?? 99) < 8 ? 'text-yellow-400' : 'text-green-400'
                              }>
                                {row.buffer_to_sl_pct !== undefined
                                  ? `${row.buffer_to_sl_pct.toFixed(1)}%`
                                  : '—'}
                              </span>
                            </Tooltip>
                          </td>
                        )}
                        {isVisible('t1') && <td className="px-4 py-3 text-right text-[#777] bg-purple-500/[0.02]">{row.t1.toFixed(2)}</td>}
                        {isVisible('t2') && <td className="px-4 py-3 text-right text-[#ccc] bg-purple-500/[0.02]">{row.t2.toFixed(2)}</td>}
                        {isVisible('t3') && <td className="px-4 py-3 text-right text-[#888] bg-purple-500/[0.02]">{row.t3 !== null ? row.t3.toFixed(2) : '—'}</td>}
                        {isVisible('dist_to_bo_pct') && <td className="px-4 py-3 text-right bg-purple-500/[0.02]">
                          <span className={(row.dist_to_bo_pct ?? 99) <= 3 ? 'text-yellow-400' : 'text-[#888]'}>
                            {row.dist_to_bo_pct !== undefined ? `${row.dist_to_bo_pct.toFixed(1)}%` : '—'}
                          </span>
                        </td>}
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
              className="flex items-center gap-1.5 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-[#ccc] transition-colors disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-500/50"
              aria-label="Export table as CSV"
            >
              <Download size={12} aria-hidden="true" />
              CSV
            </button>
          </div>
        </>
      )}

      {/* Empty state */}
      {isIdle && candidates.length === 0 && !isScanning && !error && (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center text-[#666] font-mono flex flex-col items-center gap-2">
            <Rocket size={32} className="opacity-30" aria-hidden="true" />
            <p>Click Scan to detect multibagger candidates.</p>
            <p className="text-[10px]">Scans for accumulation bases with delivery absorption, volume character, and tightness analysis.</p>
          </div>
        </div>
      )}
    </main>
  );
}
