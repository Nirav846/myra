import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { RefreshCw, Download, ChevronUp, ChevronDown, ArrowUpDown, Settings2, Info, Zap, Target, Building2 } from 'lucide-react';
import FundTractionButton from '../components/FundTractionButton';
import { fetchMarketCapMap } from '../lib/marketCapCache';
import { useWatchlist } from '../lib/WatchlistContext';
import { API_BASE } from '../config';
import ScrollableTable from '../components/ScrollableTable';
import MarketCapRangeFilter from '../components/MarketCapRangeFilter';

const TIER_COLORS: Record<string, string> = {
  HIGH: 'bg-green-500/20 text-green-400 border-green-500/30',
  MOD: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  LOW: 'bg-[#ffffff0a] text-[#888] border-[#ffffff1a]',
};

interface Preset {
  name: string;
  discount: number;
  traction: number;
  label: string;
}

const PRESETS: Preset[] = [
  { name: 'strict', discount: 15, traction: 30, label: 'Strict' },
  { name: 'moderate', discount: 12, traction: 20, label: 'Moderate' },
  { name: 'loose', discount: 10, traction: 10, label: 'Loose' },
  { name: 'dcb-only', discount: 15, traction: 0, label: 'DCB Only' },
  { name: 'traction-only', discount: 0, traction: 30, label: 'Traction Only' },
];

interface Candidate {
  symbol: string;
  sector?: string;
  close: number;
  dcb: number;
  discount_pct: number;
  traction_score: number;
  traction_aggregated?: number;
  traction_method?: string;
  traction_window?: number;
  traction_detail?: string;
  fund_count?: number;
  adds_new?: number;
  reduces_closes?: number;
  net_adds?: number;
  pct_vs_sma_traction?: number | null;
  pct_vs_sma?: number | null;
  del_abs: number;
  adtv_cr: number;
  score: number;
  combined_score: number;
  tier: string;
  tier_rank?: number;
}

interface ScanStatus {
  scan_status: string;
  last_scan: string | null;
  progress: number;
  message: string;
  candidates: Candidate[];
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

export default function SmartMoneyBargainView() {
  const [scanStatus, setScanStatus] = useState<ScanStatus | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [mcapRange, setMcapRange] = useState<{ min: number; max: number } | null>(null);
  const mcapMapRef = useRef<Map<string, number>>(new Map());

  const { isWatched } = useWatchlist();
  const [watchlistOnly, setWatchlistOnly] = useState(false);

  const [tierFilter, setTierFilter] = useState<string>('All');
  const [sectorFilter, setSectorFilter] = useState<string>('All');
  const [sortCol, setSortCol] = useState<string>('combined_score');
  const [sortAsc, setSortAsc] = useState(false);

  // Scan params
  const [minDiscountPct, setMinDiscountPct] = useState(15);
  const [minTractionScore, setMinTractionScore] = useState(30);
  const [maxPctVsSma, setMaxPctVsSma] = useState(10);
  const [filterPctVsSma, setFilterPctVsSma] = useState(true);
  const [tractionWindow, setTractionWindow] = useState(3);
  const [tractionAggregation, setTractionAggregation] = useState<string>('max');
  const [restrictToHoldings, setRestrictToHoldings] = useState(false);
  const [showParams, setShowParams] = useState(false);

  type RowData = Candidate & { _matchStatus?: 'match' | 'near-miss'; _nearMissReason?: string };

  // Presets and near-miss
  const [activePreset, setActivePreset] = useState<string>(() => {
    try { return localStorage.getItem('smart_money_preset') || 'strict'; } catch { return 'strict'; }
  });
  const [showNearMisses, setShowNearMisses] = useState<boolean>(() => {
    try { return localStorage.getItem('smart_money_show_near_misses') === 'true'; } catch { return false; }
  });

  const activePresetDef = useMemo(() => PRESETS.find(p => p.name === activePreset) ?? PRESETS[0], [activePreset]);
  const completedScan = scanStatus?.scan_status === 'completed';

  useEffect(() => { fetchMarketCapMap().then(m => mcapMapRef.current = m); }, []);

  const mountedRef = useRef(true);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const candidates = scanStatus?.candidates ?? [];

  const availableSectors = useMemo(() => {
    const sectors = new Set(candidates.map(c => c.sector ?? 'Unknown'));
    return ['All', ...Array.from(sectors).filter(s => s !== 'Unknown').sort(), 'Unknown'];
  }, [candidates]);

  const classifiedData = useMemo((): RowData[] => {
    if (!showNearMisses || !completedScan) return candidates.map(c => ({ ...c }));
    const preset = activePresetDef;
    const out: RowData[] = [];
    // Backend scanned with loose params (discount>=10, traction>=10); re-classify locally.
    for (const c of candidates) {
      const discount = c.discount_pct ?? 0;
      const traction = c.traction_aggregated ?? c.traction_score ?? 0;
      const passesDiscount = discount >= preset.discount;
      const passesTraction = traction >= preset.traction;
      let status: 'match' | 'near-miss' | undefined;
      let reason: string | undefined;
      if (passesDiscount && passesTraction) {
        status = 'match';
      } else if (passesDiscount && traction >= 10 && traction < preset.traction) {
        status = 'near-miss';
        reason = 'Traction Low';
      } else if (passesTraction && discount >= 10 && discount < preset.discount) {
        status = 'near-miss';
        reason = 'Discount Low';
      }
      if (!status) continue;
      out.push({ ...c, _matchStatus: status, _nearMissReason: reason });
    }
    return out;
  }, [candidates, showNearMisses, completedScan, activePresetDef]);

  const matchCount = useMemo(() => classifiedData.filter(r => r._matchStatus === 'match').length, [classifiedData]);
  const nearMissCount = useMemo(() => classifiedData.filter(r => r._matchStatus === 'near-miss').length, [classifiedData]);

  const displayData = useMemo(() => {
    const source: RowData[] = showNearMisses ? classifiedData : candidates;
    let data: RowData[] = [...source];
    if (mcapRange) {
      const map = mcapMapRef.current;
      data = data.filter(d => {
        const mcap = map.get(d.symbol);
        return mcap !== undefined && mcap >= mcapRange.min && mcap <= mcapRange.max;
      });
    }
    if (watchlistOnly) data = data.filter(d => isWatched(d.symbol));
    if (sectorFilter !== 'All') data = data.filter(d => d.sector === sectorFilter);
    if (tierFilter !== 'All') data = data.filter(d => d.tier === tierFilter);
    data.sort((a, b) => {
      const av = (a as any)[sortCol] ?? 0;
      const bv = (b as any)[sortCol] ?? 0;
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortAsc ? av - bv : bv - av;
      }
      return String(av).localeCompare(String(bv)) * (sortAsc ? 1 : -1);
    });
    return data;
  }, [candidates, classifiedData, showNearMisses, mcapRange, watchlistOnly, sectorFilter, tierFilter, isWatched, sortCol, sortAsc]);

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
      ? <ChevronUp size={10} className="inline ml-1 text-emerald-400" />
      : <ChevronDown size={10} className="inline ml-1 text-emerald-400" />;
  };

  const fetchScanStatus = useCallback(async () => {
    if (!mountedRef.current) return;
    try {
      const res = await fetch(`${API_BASE}/smart-money-bargain/status`);
      if (!mountedRef.current) return;
      if (res.ok) {
        const data: ScanStatus = await res.json();
        if (!mountedRef.current) return;
        setScanStatus(data);
        setError(null);
        if (data.scan_status === 'completed' || data.scan_status === 'error') {
          clearPolling();
          setIsScanning(false);
        }
      }
    } catch (e) {
      if (mountedRef.current) setError('Failed to fetch status');
    }
  }, [clearPolling]);

  const startScan = useCallback(async () => {
    setIsScanning(true);
    setError(null);
    try {
      const payload = {
        min_discount_pct: showNearMisses ? 10 : minDiscountPct,
        min_traction_score: showNearMisses ? 10 : minTractionScore,
        max_pct_vs_sma: maxPctVsSma,
        filter_pct_vs_sma: filterPctVsSma,
        traction_window: tractionWindow,
        traction_aggregation: tractionAggregation,
        restrict_to_holdings: restrictToHoldings,
      };
      await fetch(`${API_BASE}/smart-money-bargain/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      // Start polling
      pollTimerRef.current = setInterval(fetchScanStatus, 2000);
      fetchScanStatus();
    } catch {
      setIsScanning(false);
      setError('Failed to start scan');
    }
  }, [showNearMisses, minDiscountPct, minTractionScore, maxPctVsSma, filterPctVsSma, tractionWindow, tractionAggregation, restrictToHoldings, fetchScanStatus]);

  const applyPreset = useCallback((p: Preset) => {
    setActivePreset(p.name);
    setMinDiscountPct(p.discount);
    setMinTractionScore(p.traction);
    try { localStorage.setItem('smart_money_preset', p.name); } catch { /* ignore */ }
  }, []);

  const toggleNearMisses = useCallback(() => {
    setShowNearMisses(s => {
      const next = !s;
      try { localStorage.setItem('smart_money_show_near_misses', String(next)); } catch { /* ignore */ }
      return next;
    });
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    fetchScanStatus();
    return () => { mountedRef.current = false; clearPolling(); };
  }, [fetchScanStatus, clearPolling]);

  const exportCsv = () => {
    if (!displayData.length) return;
    const headers = ['Symbol', 'Sector', 'Close', 'DCB', 'Discount%', 'Traction Agg', 'Traction Latest', 'Method', 'Window', 'Funds', 'Adds', 'Reduces', 'Net Adds', '% vs SMA', 'Del Abs', 'ADTV Cr', 'Combined', 'Tier'];
    const rows = displayData.map(r => [
      r.symbol, r.sector ?? '', r.close, r.dcb, r.discount_pct,
      r.traction_aggregated ?? r.traction_score ?? '', r.traction_score,
      r.traction_method ?? '', r.traction_window ?? '',
      r.fund_count ?? '', r.adds_new ?? '', r.reduces_closes ?? '',
      r.net_adds ?? '', r.pct_vs_sma_traction ?? r.pct_vs_sma ?? '', r.del_abs, r.adtv_cr,
      r.combined_score, r.tier,
    ]);
    const csv = [headers, ...rows].map(r => r.join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'smart_money_bargain.csv'; a.click();
    URL.revokeObjectURL(url);
  };

  const stats = useMemo(() => {
    const n = displayData.length;
    const avgDisc = n ? (displayData.reduce((s, r) => s + r.discount_pct, 0) / n).toFixed(1) : '0';
    const avgTraction = n ? (displayData.reduce((s, r) => s + r.traction_score, 0) / n).toFixed(1) : '0';
    const totalAdds = displayData.reduce((s, r) => s + (r.adds_new ?? 0), 0);
    const totalReduces = displayData.reduce((s, r) => s + (r.reduces_closes ?? 0), 0);
    return { n, avgDisc, avgTraction, totalAdds, totalReduces };
  }, [displayData]);

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">Smart Money Bargain</h1>
          <p className="text-xs text-[#888] mt-1">DCB discount + Fund traction — backtest validated (Sharpe 3.41)</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-[#888]">Last: {relativeTime(scanStatus?.last_scan)}</span>
          <button
            onClick={() => setShowParams(s => !s)}
            className="p-1.5 rounded bg-[#ffffff0a] hover:bg-[#ffffff14] text-[#888]"
            title="Parameters"
          >
            <Settings2 size={14} />
          </button>
          <button
            onClick={startScan}
            disabled={isScanning}
            className="px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium disabled:opacity-50"
          >
            {isScanning ? <RefreshCw size={14} className="animate-spin inline mr-1" /> : null}
            {isScanning ? 'Scanning...' : 'Scan'}
          </button>
        </div>
      </div>

      {error && <div className="text-red-400 text-sm bg-red-500/10 p-2 rounded">{error}</div>}

      {isScanning && scanStatus && (
        <div className="bg-[#ffffff0a] rounded p-3">
          <div className="flex justify-between text-xs text-[#888] mb-1">
            <span>{scanStatus.message}</span>
            <span>{scanStatus.progress}%</span>
          </div>
          <div className="w-full bg-[#ffffff0a] rounded-full h-1.5">
            <div className="bg-emerald-500 h-1.5 rounded-full transition-all" style={{ width: `${scanStatus.progress}%` }} />
          </div>
        </div>
      )}

      {/* Preset buttons + near-miss toggle */}
      <div className="flex flex-wrap gap-2 items-center">
        <span className="text-[12px] text-[#888] font-mono mr-1">Presets:</span>
        {PRESETS.map(p => (
          <button
            key={p.name}
            onClick={() => applyPreset(p)}
            className={`px-3 py-1.5 rounded text-xs font-medium border transition-colors ${
              activePreset === p.name
                ? 'bg-emerald-500/20 border-emerald-500/40 text-emerald-400'
                : 'bg-[#ffffff0a] border-[#ffffff14] text-[#888] hover:text-white hover:border-[#ffffff30]'
            }`}
            aria-pressed={activePreset === p.name}
          >
            {p.label}
            <span className="ml-1 text-[10px] opacity-60">
              {p.discount > 0 ? `≥${p.discount}%` : ''}
              {p.discount > 0 && p.traction > 0 ? ' + ' : ''}
              {p.traction > 0 ? `≥${p.traction}` : ''}
            </span>
          </button>
        ))}
        <div className="ml-3 border-l border-[#ffffff14] pl-3 flex items-center gap-1.5">
          <label className="flex items-center gap-1.5 text-[12px] text-[#888] cursor-pointer select-none">
            <input type="checkbox" checked={showNearMisses} onChange={toggleNearMisses} className="rounded accent-emerald-500" />
            <Zap size={12} className={showNearMisses ? 'text-amber-400' : 'text-[#555]'} />
            Near Misses
          </label>
        </div>
      </div>

      {completedScan && !isScanning && (
        <div className="bg-[#ffffff06] border border-[#ffffff14] rounded p-3 text-xs font-mono text-[#888] flex items-start gap-2">
          <Info size={14} className="text-emerald-400 shrink-0 mt-0.5" />
          <div>
            <span className="text-white font-semibold">{activePresetDef.label}</span>
            {' '}filter: discount ≥{activePresetDef.discount}%, traction ≥{activePresetDef.traction}.
            {displayData.length === 0 ? (
              <span> <span className="text-red-400">0 candidates found.</span> Try a looser preset for more results.</span>
            ) : (
              <span> <span className="text-emerald-400">{matchCount} match{matchCount !== 1 ? 'es' : ''}</span></span>
            )}
            {showNearMisses && nearMissCount > 0 && (
              <span> + <span className="text-amber-400">{nearMissCount} near miss{nearMissCount !== 1 ? 'es' : ''}</span></span>
            )}
          </div>
        </div>
      )}

      {showParams && (
        <div className="bg-[#ffffff06] border border-[#ffffff14] rounded p-3 grid grid-cols-2 md:grid-cols-4 gap-3">
          <label className="text-xs text-[#888]">
            Min Discount %
            <input type="number" value={minDiscountPct} onChange={e => setMinDiscountPct(Number(e.target.value))}
              className="w-full mt-1 bg-[#ffffff0a] border border-[#ffffff14] rounded px-2 py-1 text-white text-sm" />
          </label>
          <label className="text-xs text-[#888]">
            Min Traction Score
            <input type="number" value={minTractionScore} onChange={e => setMinTractionScore(Number(e.target.value))}
              className="w-full mt-1 bg-[#ffffff0a] border border-[#ffffff14] rounded px-2 py-1 text-white text-sm" />
          </label>
          <label className="text-xs text-[#888]">
            Traction Window (months)
            <select value={tractionWindow} onChange={e => setTractionWindow(Number(e.target.value))}
              className="w-full mt-1 bg-[#ffffff0a] border border-[#ffffff14] rounded px-2 py-1 text-white text-sm"
              title="Number of months to look back for traction aggregation. Higher = smoother, lower = more responsive.">
              {[1, 2, 3, 6, 12].map(w => <option key={w} value={w}>{w}</option>)}
            </select>
          </label>
          <label className="text-xs text-[#888]">
            Aggregation
            <select value={tractionAggregation} onChange={e => setTractionAggregation(e.target.value)}
              className="w-full mt-1 bg-[#ffffff0a] border border-[#ffffff14] rounded px-2 py-1 text-white text-sm"
              title="How to combine multiple months: Max (best in window), Average (smoothed), Latest (most recent only), Momentum (change direction).">
              <option value="max">Max</option>
              <option value="average">Average</option>
              <option value="latest">Latest</option>
              <option value="momentum">Momentum</option>
            </select>
          </label>
          <label className="text-xs text-[#888]">
            Max % vs SMA
            <input type="number" value={maxPctVsSma} onChange={e => setMaxPctVsSma(Number(e.target.value))}
              className="w-full mt-1 bg-[#ffffff0a] border border-[#ffffff14] rounded px-2 py-1 text-white text-sm" />
          </label>
          <label className="text-xs text-[#888] flex items-end gap-2 pb-1">
            <input type="checkbox" checked={filterPctVsSma} onChange={e => setFilterPctVsSma(e.target.checked)} className="rounded" />
            Filter overbought (% vs SMA)
          </label>
        </div>
      )}

      {/* Summary bar */}
      {scanStatus?.scan_status !== 'scanning' && (
        <div className="flex flex-wrap gap-3 text-xs text-[#888]">
          <span className="bg-[#ffffff0a] px-2 py-1 rounded">{stats.n} candidates</span>
          <span className="bg-[#ffffff0a] px-2 py-1 rounded">Avg discount: {stats.avgDisc}%</span>
          <span className="bg-[#ffffff0a] px-2 py-1 rounded">Avg traction: {stats.avgTraction}</span>
          <span className="bg-[#ffffff0a] px-2 py-1 rounded" title={`Window: ${tractionWindow} months, Aggregation: ${tractionAggregation}`}>
            {tractionWindow}mo / {tractionAggregation}
          </span>
          <span className="bg-emerald-500/10 text-emerald-400 px-2 py-1 rounded">Adds: {stats.totalAdds}</span>
          <span className="bg-red-500/10 text-red-400 px-2 py-1 rounded">Reduces: {stats.totalReduces}</span>
        </div>
      )}

      {/* Filters */}
      {candidates.length > 0 && (
        <div className="flex flex-wrap gap-2 items-center text-xs">
          <MarketCapRangeFilter onChange={setMcapRange} />
          <select value={tierFilter} onChange={e => setTierFilter(e.target.value)}
            className="bg-[#ffffff0a] border border-[#ffffff14] rounded px-2 py-1 text-white">
            {['All', 'HIGH', 'MOD', 'LOW'].map(t => <option key={t} value={t}>{t}</option>)}
          </select>
          <select value={sectorFilter} onChange={e => setSectorFilter(e.target.value)}
            className="bg-[#ffffff0a] border border-[#ffffff14] rounded px-2 py-1 text-white">
            {availableSectors.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <label className="flex items-center gap-1 text-[#888]">
            <input type="checkbox" checked={watchlistOnly} onChange={e => setWatchlistOnly(e.target.checked)} className="rounded" />
            Watchlist
          </label>
          <label className="flex items-center gap-1 text-[#888]" title="Restrict to symbols held by at least one mutual fund (latest month)">
            <input type="checkbox" checked={restrictToHoldings} onChange={e => setRestrictToHoldings(e.target.checked)} className="rounded accent-emerald-500" />
            <Building2 size={11} aria-hidden="true" />
            MF-held
          </label>
          <button onClick={exportCsv} className="ml-auto px-2 py-1 rounded bg-[#ffffff0a] hover:bg-[#ffffff14] text-[#888]"
            title="Export CSV">
            <Download size={12} />
          </button>
        </div>
      )}

      {/* Results table */}
      {displayData.length > 0 ? (
        <ScrollableTable className="text-xs">
          <table className="w-full text-left">
            <thead>
              <tr className="text-[#888] border-b border-[#ffffff14]">
                {[
                  { key: 'symbol', label: 'Symbol' },
                  ...(showNearMisses ? [{ key: '_matchStatus', label: 'Status' }] : []),
                  { key: 'sector', label: 'Sector' },
                  { key: 'close', label: 'Close' },
                  { key: 'discount_pct', label: 'DCB Disc%' },
                  { key: 'combined_score', label: 'Score' },
                  { key: 'traction_aggregated', label: `Traction (${tractionAggregation})` },
                  { key: 'traction_score', label: 'Latest' },
                  { key: 'fund_count', label: 'Funds' },
                  { key: 'net_adds', label: 'Net Adds' },
                  { key: 'pct_vs_sma_traction', label: '% vs SMA' },
                  { key: 'del_abs', label: 'Del Abs' },
                  { key: 'adtv_cr', label: 'ADTV Cr' },
                  { key: 'tier', label: 'Tier' },
                ].map(col => (
                  <th key={col.key} className="px-2 py-1.5 cursor-pointer hover:text-white" onClick={() => handleSort(col.key)}>
                    {col.label}<SortIcon column={col.key} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {displayData.map((r, i) => (
                <tr key={r.symbol} className={`border-b border-[#ffffff08] hover:bg-[#ffffff08] ${showNearMisses && r._matchStatus === 'near-miss' ? 'opacity-60' : ''}`}>
                  <td className="px-2 py-1.5 font-medium text-white flex items-center gap-1">
                    {r.symbol}
                    {showNearMisses && r._matchStatus === 'near-miss' && (
                      <span className="px-1 py-0.5 rounded text-[9px] bg-amber-500/20 text-amber-400 border border-amber-500/30 whitespace-nowrap">
                        {r._nearMissReason}
                      </span>
                    )}
                    <FundTractionButton symbols={[r.symbol]} size="xs" />
                  </td>
                  {showNearMisses && (
                    <td className="px-2 py-1.5">
                      {r._matchStatus === 'match' ? (
                        <span className="flex items-center gap-1 text-green-400" title="Meets preset discount + traction thresholds">
                          <Target size={11} className="shrink-0" /> Match
                        </span>
                      ) : (
                        <span className="flex items-center gap-1 text-amber-400" title={r._nearMissReason}>
                          <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" />
                          {r._nearMissReason}
                        </span>
                      )}
                    </td>
                  )}
                  <td className="px-2 py-1.5 text-[#888]">{r.sector ?? '-'}</td>
                  <td className="px-2 py-1.5">{r.close?.toFixed(2)}</td>
                  <td className="px-2 py-1.5 text-red-400">{r.discount_pct?.toFixed(1)}%</td>
                  <td className="px-2 py-1.5 font-medium">{r.combined_score?.toFixed(1)}</td>
                  <td className="px-2 py-1.5">
                    <span className={r.traction_aggregated != null && r.traction_aggregated >= 60 ? 'text-green-400' : r.traction_aggregated != null && r.traction_aggregated >= 40 ? 'text-amber-400' : 'text-[#888]'}
                      title={r.traction_detail || ''}>
                      {r.traction_aggregated?.toFixed(1) ?? r.traction_score?.toFixed(1) ?? '-'}
                    </span>
                  </td>
                  <td className="px-2 py-1.5 text-[#888]">
                    {r.traction_score?.toFixed(1)}
                  </td>
                  <td className="px-2 py-1.5 text-[#888]">{r.fund_count ?? '-'}</td>
                  <td className="px-2 py-1.5">
                    {r.net_adds != null && (
                      <span className={r.net_adds > 0 ? 'text-green-400' : r.net_adds < 0 ? 'text-red-400' : 'text-[#888]'}>
                        {r.net_adds > 0 ? '+' : ''}{r.net_adds}
                      </span>
                    )}
                  </td>
                  <td className="px-2 py-1.5">
                    {r.pct_vs_sma_traction != null ? (
                      <span className={r.pct_vs_sma_traction < 0 ? 'text-green-400' : r.pct_vs_sma_traction > 10 ? 'text-red-400' : 'text-[#888]'}>
                        {r.pct_vs_sma_traction > 0 ? '+' : ''}{r.pct_vs_sma_traction.toFixed(1)}%
                      </span>
                    ) : '-'}
                  </td>
                  <td className="px-2 py-1.5 text-[#888]">{r.del_abs?.toFixed(2)}</td>
                  <td className="px-2 py-1.5 text-[#888]">{r.adtv_cr?.toFixed(1)}</td>
                  <td className="px-2 py-1.5">
                    <span className={`px-1.5 py-0.5 rounded text-[10px] border ${TIER_COLORS[r.tier] ?? TIER_COLORS.LOW}`}>
                      {r.tier}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollableTable>
      ) : scanStatus?.scan_status !== 'scanning' ? (
        <div className="text-center text-[#888] py-12">
          {scanStatus?.scan_status === 'idle' && !scanStatus?.last_scan
            ? 'Click Scan to find Smart Money Bargains'
            : 'No candidates match current filters'}
        </div>
      ) : null}
    </div>
  );
}
