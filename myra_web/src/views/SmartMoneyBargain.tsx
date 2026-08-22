import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Filter, RefreshCw, Download, ChevronUp, ChevronDown, ArrowUpDown, Settings2 } from 'lucide-react';
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

interface Candidate {
  symbol: string;
  sector?: string;
  close: number;
  dcb: number;
  discount_pct: number;
  traction_score: number;
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
  const [showParams, setShowParams] = useState(false);

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
  }, [candidates, mcapRange, watchlistOnly, sectorFilter, tierFilter, isWatched, sortCol, sortAsc]);

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
        min_discount_pct: minDiscountPct,
        min_traction_score: minTractionScore,
        max_pct_vs_sma: maxPctVsSma,
        filter_pct_vs_sma: filterPctVsSma,
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
  }, [minDiscountPct, minTractionScore, maxPctVsSma, filterPctVsSma, fetchScanStatus]);

  useEffect(() => {
    mountedRef.current = true;
    fetchScanStatus();
    return () => { mountedRef.current = false; clearPolling(); };
  }, [fetchScanStatus, clearPolling]);

  const exportCsv = () => {
    if (!filteredData.length) return;
    const headers = ['Symbol', 'Sector', 'Close', 'DCB', 'Discount%', 'Traction', 'Funds', 'Adds', 'Reduces', 'Net Adds', '% vs SMA', 'Del Abs', 'ADTV Cr', 'Combined', 'Tier'];
    const rows = filteredData.map(r => [
      r.symbol, r.sector ?? '', r.close, r.dcb, r.discount_pct,
      r.traction_score, r.fund_count ?? '', r.adds_new ?? '', r.reduces_closes ?? '',
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
    const n = filteredData.length;
    const avgDisc = n ? (filteredData.reduce((s, r) => s + r.discount_pct, 0) / n).toFixed(1) : '0';
    const avgTraction = n ? (filteredData.reduce((s, r) => s + r.traction_score, 0) / n).toFixed(1) : '0';
    const totalAdds = filteredData.reduce((s, r) => s + (r.adds_new ?? 0), 0);
    const totalReduces = filteredData.reduce((s, r) => s + (r.reduces_closes ?? 0), 0);
    return { n, avgDisc, avgTraction, totalAdds, totalReduces };
  }, [filteredData]);

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
          <button onClick={exportCsv} className="ml-auto px-2 py-1 rounded bg-[#ffffff0a] hover:bg-[#ffffff14] text-[#888]"
            title="Export CSV">
            <Download size={12} />
          </button>
        </div>
      )}

      {/* Results table */}
      {filteredData.length > 0 ? (
        <ScrollableTable className="text-xs">
          <table className="w-full text-left">
            <thead>
              <tr className="text-[#888] border-b border-[#ffffff14]">
                {[
                  { key: 'symbol', label: 'Symbol' },
                  { key: 'sector', label: 'Sector' },
                  { key: 'close', label: 'Close' },
                  { key: 'discount_pct', label: 'DCB Disc%' },
                  { key: 'combined_score', label: 'Score' },
                  { key: 'traction_score', label: 'Traction' },
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
              {filteredData.map((r, i) => (
                <tr key={r.symbol} className="border-b border-[#ffffff08] hover:bg-[#ffffff08]">
                  <td className="px-2 py-1.5 font-medium text-white flex items-center gap-1">
                    {r.symbol}
                    <FundTractionButton symbols={[r.symbol]} size="xs" />
                  </td>
                  <td className="px-2 py-1.5 text-[#888]">{r.sector ?? '-'}</td>
                  <td className="px-2 py-1.5">{r.close?.toFixed(2)}</td>
                  <td className="px-2 py-1.5 text-red-400">{r.discount_pct?.toFixed(1)}%</td>
                  <td className="px-2 py-1.5 font-medium">{r.combined_score?.toFixed(1)}</td>
                  <td className="px-2 py-1.5">
                    <span className={r.traction_score >= 60 ? 'text-green-400' : r.traction_score >= 40 ? 'text-amber-400' : 'text-[#888]'}>
                      {r.traction_score?.toFixed(1)}
                    </span>
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
