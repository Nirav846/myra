import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Librarian } from '../lib/Librarian';
import { GitCompare, RefreshCw, AlertTriangle, ChevronDown, ChevronUp, ArrowUpDown, ArrowUpRight } from 'lucide-react';
import MarketCapRangeFilter from '../components/MarketCapRangeFilter';
import { fetchMarketCapMap } from '../lib/marketCapCache';
import { useWatchlist } from '../lib/WatchlistContext';
import { API_BASE } from '../config';
import ScrollableTable from '../components/ScrollableTable';
import { HistoricalScanDatePicker } from '../components/HistoricalScanDatePicker';

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface Candidate {
  symbol: string;
  close: number;
  low_in_window: number;
  price_dist_pct: number;
  latest_del_pct: number;
  delivery_change: number;
  divergence_strength: number;
  price_lookback: number;
  delivery_period: number;
  delivery_threshold: number;
  adtv_cr: number | null;
  score: number;
  divergence_type: string;
  horizon: string;
}

interface ScanStatus {
  scan_status: string;
  last_scan: string | null;
  progress: number;
  message: string;
  candidates: Candidate[];
  scanned_date?: string | null;
}

/* ------------------------------------------------------------------ */
/*  Defaults                                                           */
/* ------------------------------------------------------------------ */

const DEFAULTS = {
  price_lookback: 20,
  delivery_period: 10,
  delivery_threshold: 1.0,
  min_mcap: 200,
  max_mcap: 50000,
  min_abs_delivery_pct: 0.0,
  min_adtv_cr: 0.0,
};

const HORIZON_PRESETS: Record<string, { price_lookback: number; delivery_period: number; delivery_threshold: number }> = {
  '60d':  { price_lookback: 20, delivery_period: 10, delivery_threshold: 1.0 },
  '120d': { price_lookback: 10, delivery_period: 5,  delivery_threshold: 0.0 },
  '180d': { price_lookback: 10, delivery_period: 5,  delivery_threshold: 1.0 },
};

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

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

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function PriceDeliveryDivergenceScannerView({ lib }: { lib: Librarian }) {
  const { isWatched } = useWatchlist();
  const [watchlistOnly, setWatchlistOnly] = useState(false);

  const [scanStatus, setScanStatus] = useState<ScanStatus | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Scanner parameters
  const [horizon, setHorizon] = useState<string | null>('60d');
  const [priceLookback, setPriceLookback] = useState(DEFAULTS.price_lookback);
  const [deliveryPeriod, setDeliveryPeriod] = useState(DEFAULTS.delivery_period);
  const [deliveryThreshold, setDeliveryThreshold] = useState(DEFAULTS.delivery_threshold);
  const [mcapRange, setMcapRange] = useState<{ min: number; max: number } | null>(null);
  const [minAbsDeliveryPct, setMinAbsDeliveryPct] = useState(DEFAULTS.min_abs_delivery_pct);
  const [minAdtvCr, setMinAdtvCr] = useState(DEFAULTS.min_adtv_cr);
  const [scanDate, setScanDate] = useState('');

  // Filtering / display
  const [sortCol, setSortCol] = useState<string>('score');
  const [sortAsc, setSortAsc] = useState(false);
  const [minScore, setMinScore] = useState(0);
  const [minDeliveryChange, setMinDeliveryChange] = useState(0);
  const [filtersVisible, setFiltersVisible] = useState(() => localStorage.getItem('pdd_filters_visible') !== 'false');

  const mountedRef = useRef(true);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startScanRef = useRef<(() => void) | null>(null);
  const mcapMapRef = useRef<Map<string, number>>(new Map());

  useEffect(() => { fetchMarketCapMap().then(m => mcapMapRef.current = m); }, []);

  const candidates = scanStatus?.candidates ?? [];

  // Apply horizon preset
  const applyHorizon = useCallback((h: string | null) => {
    setHorizon(h);
    if (h && HORIZON_PRESETS[h]) {
      const p = HORIZON_PRESETS[h];
      setPriceLookback(p.price_lookback);
      setDeliveryPeriod(p.delivery_period);
      setDeliveryThreshold(p.delivery_threshold);
    }
  }, []);

  // Sorting
  const handleSort = (col: string) => {
    if (sortCol === col) setSortAsc(s => !s);
    else { setSortCol(col); setSortAsc(false); }
  };

  const SortIcon = ({ column }: { column: string }) => {
    if (sortCol !== column) return <ArrowUpDown size={10} className="inline ml-1 opacity-30" />;
    return sortAsc
      ? <ChevronUp size={10} className="inline ml-1 text-orange-400" />
      : <ChevronDown size={10} className="inline ml-1 text-orange-400" />;
  };

  // Filter + sort
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
    if (minScore > 0) data = data.filter(d => d.score >= minScore);
    if (minDeliveryChange > 0) data = data.filter(d => d.delivery_change >= minDeliveryChange);

    data.sort((a, b) => {
      const av = (a as any)[sortCol] ?? 0;
      const bv = (b as any)[sortCol] ?? 0;
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortAsc ? av - bv : bv - av;
      }
      return String(av).localeCompare(String(bv)) * (sortAsc ? 1 : -1);
    });
    return data;
  }, [candidates, mcapRange, watchlistOnly, minScore, minDeliveryChange, isWatched, sortCol, sortAsc]);

  // Summaries
  const summaries = useMemo(() => {
    if (filteredData.length === 0) return { avgScore: 0, avgDelChange: 0, avgDist: 0 };
    const sumScore = filteredData.reduce((a, v) => a + v.score, 0);
    const sumDel = filteredData.reduce((a, v) => a + v.delivery_change, 0);
    const sumDist = filteredData.reduce((a, v) => a + v.price_dist_pct, 0);
    return {
      avgScore: Math.round(sumScore / filteredData.length),
      avgDelChange: sumDel / filteredData.length,
      avgDist: sumDist / filteredData.length,
    };
  }, [filteredData]);

  // Clear polling
  const clearPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  // Fetch scan status
  const fetchScanStatus = useCallback(async () => {
    if (!mountedRef.current) return;
    try {
      const res = await fetch(`${API_BASE}/delivery-divergence/status`);
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

  // Start scan
  const startScan = useCallback(async () => {
    if (!mountedRef.current) return;
    setIsScanning(true);
    setError(null);
    clearPolling();

    try {
      const body: Record<string, any> = {};
      if (horizon) body.horizon = horizon;
      else {
        body.price_lookback = priceLookback;
        body.delivery_period = deliveryPeriod;
        body.delivery_threshold = deliveryThreshold;
      }
      if (mcapRange) {
        body.min_mcap = mcapRange.min;
        body.max_mcap = mcapRange.max;
      }
      if (minAbsDeliveryPct > 0) body.min_abs_delivery_pct = minAbsDeliveryPct;
      if (minAdtvCr > 0) body.min_adtv_cr = minAdtvCr;
      if (scanDate.trim()) body.scan_date = scanDate;

      const res = await fetch(`${API_BASE}/delivery-divergence/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
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
        setError(e.message || 'Error starting scan');
        setIsScanning(false);
      }
    }
  }, [fetchScanStatus, clearPolling, fetchScanStatus, horizon, priceLookback, deliveryPeriod, deliveryThreshold, mcapRange, minAbsDeliveryPct, minAdtvCr, scanDate]);
  startScanRef.current = startScan;

  // Clear cache
  const clearCache = useCallback(async () => {
    try {
      await fetch(`${API_BASE}/cache/delivery-divergence`, { method: 'DELETE' });
      setScanStatus(null);
    } catch { /* ignore */ }
  }, []);

  // Export CSV
  const handleCSV = useCallback(() => {
    if (filteredData.length === 0) return;
    const headers = ['Symbol', 'Close', 'Low(window)', 'PriceDist%', 'Del%', 'DelChange', 'Strength', 'Score', 'Horizon', 'LB', 'DP', 'Thr'];
    const rows = filteredData.map(d => [
      d.symbol, d.close, d.low_in_window, d.price_dist_pct, d.latest_del_pct,
      d.delivery_change, d.divergence_strength, d.score, d.horizon,
      d.price_lookback, d.delivery_period, d.delivery_threshold,
    ]);
    const csv = [headers, ...rows].map(r => r.join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `divergence_scan_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [filteredData]);

  // Mount
  useEffect(() => {
    mountedRef.current = true;
    fetchScanStatus();
    return () => { mountedRef.current = false; clearPolling(); };
  }, [fetchScanStatus, clearPolling]);

  const isStale = scanStatus?.last_scan && (Date.now() - new Date(scanStatus.last_scan).getTime() > 30 * 60 * 1000);

  return (
    <main className="bg-[#1e2028] border border-[#ffffff1a] rounded flex flex-col shadow-xl overflow-hidden flex-1 min-h-0 min-h-[600px]" aria-label="Price-Delivery Divergence Scanner">
      {/* Header */}
      <header className="px-6 py-4 border-b border-[#ffffff1a] flex justify-between items-center bg-[#1a1c24]">
        <div className="flex items-center gap-3">
          <GitCompare size={20} className="text-orange-400" aria-hidden="true" />
          <h1 className="font-semibold text-[#fafafa] flex items-center gap-2 text-base">
            Price-Delivery Divergence
          </h1>
          <span className="text-[12px] text-[#888] font-mono">Backend Scanner</span>
          {error && (
            <span className="text-[12px] bg-red-500/20 text-red-500 px-2 py-1 rounded font-mono border border-red-500/30 flex items-center gap-1" role="alert">
              <AlertTriangle size={10} aria-hidden="true" /> {error}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <span className="text-[12px] text-[#888] font-mono">
            {scanStatus?.last_scan ? `Last: ${relativeTime(scanStatus.last_scan)}` : 'Never scanned'}
            {isStale && <span className="text-yellow-500 ml-1">(stale)</span>}
          </span>
          <HistoricalScanDatePicker value={scanDate} onChange={setScanDate} />
          <button
            onClick={clearCache}
            className="bg-[#2a2c34] hover:bg-[#3a3c44] text-[#aaa] hover:text-white px-2 py-1 rounded border border-[#ffffff1a] transition-all text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500/50"
          >
            Clear cache
          </button>
          <button
            onClick={handleCSV}
            disabled={filteredData.length === 0}
            className="bg-[#2a2c34] hover:bg-[#3a3c44] text-[#aaa] hover:text-white px-2 py-1 rounded border border-[#ffffff1a] transition-all flex items-center gap-1 disabled:opacity-40 disabled:cursor-not-allowed text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500/50"
          >
            <span>↓ CSV</span>
          </button>
          <button
            onClick={startScan}
            disabled={isScanning}
            className="bg-[#2a2c34] hover:bg-[#3a3c44] text-[#aaa] hover:text-white px-3 py-1.5 rounded border border-[#ffffff1a] transition-all flex items-center gap-1.5 disabled:opacity-50 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500/50"
          >
            <RefreshCw size={12} className={isScanning ? "animate-spin" : ""} aria-hidden="true" />
            {isScanning ? `Scanning... ${scanStatus?.progress ?? 0}%` : 'Scan'}
          </button>
          <button
            onClick={() => { const n = !filtersVisible; setFiltersVisible(n); localStorage.setItem('pdd_filters_visible', String(n)); }}
            className={`px-2.5 py-1 rounded text-[12px] font-mono border transition-all flex items-center gap-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-500/50 ${
              filtersVisible
                ? 'bg-[#2a2c34] border-[#ffffff3a] text-[#ccc]'
                : 'bg-[#2a2c34] border-[#ffffff1a] text-[#888]'
            }`}
            title="Toggle filter controls"
          >
            Filters <ChevronDown size={12} className={`transition-transform ${filtersVisible ? '' : '-rotate-90'}`} aria-hidden="true" />
          </button>
        </div>
      </header>

      {/* Filter Panel */}
      {filtersVisible && (
        <section className="bg-[#15171d] border-b border-[#ffffff1a] p-4 flex flex-col gap-4" aria-label="Scanner parameters">
          <div className="grid grid-cols-1 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-8 gap-4 items-end">
            {/* Horizon presets */}
            <div className="flex flex-col">
              <label className="text-[12px] text-[#888] font-mono mb-1">Horizon (backtested)</label>
              <div className="flex gap-1">
                {['60d', '120d', '180d'].map(h => (
                  <button key={h} onClick={() => applyHorizon(h)}
                    className={`px-2 py-1 text-[12px] rounded font-mono transition-colors ${
                      horizon === h ? 'bg-orange-600 text-white' : 'bg-[#ffffff0a] text-[#888] hover:text-white'
                    }`}>
                    {h}
                  </button>
                ))}
                <button onClick={() => applyHorizon(null)}
                  className={`px-2 py-1 text-[12px] rounded font-mono transition-colors ${
                    horizon === null ? 'bg-orange-600 text-white' : 'bg-[#ffffff0a] text-[#888] hover:text-white'
                  }`}>
                  Custom
                </button>
              </div>
            </div>

            {/* Price lookback */}
            <div className="flex flex-col">
              <label className="text-[12px] text-[#888] font-mono mb-1">Price Lookback (days)</label>
              <input type="number" min={3} max={60} value={priceLookback}
                disabled={horizon !== null}
                onChange={e => setPriceLookback(Number(e.target.value))}
                className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] font-mono focus:border-orange-500 outline-none w-24 disabled:opacity-40"
              />
            </div>

            {/* Delivery period */}
            <div className="flex flex-col">
              <label className="text-[12px] text-[#888] font-mono mb-1">Delivery Period (days)</label>
              <input type="number" min={2} max={30} value={deliveryPeriod}
                disabled={horizon !== null}
                onChange={e => setDeliveryPeriod(Number(e.target.value))}
                className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] font-mono focus:border-orange-500 outline-none w-24 disabled:opacity-40"
              />
            </div>

            {/* Delivery threshold */}
            <div className="flex flex-col">
              <label className="text-[12px] text-[#888] font-mono mb-1">Del Threshold (%)</label>
              <input type="number" min={0} max={20} step={0.5} value={deliveryThreshold}
                disabled={horizon !== null}
                onChange={e => setDeliveryThreshold(Number(e.target.value))}
                className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] font-mono focus:border-orange-500 outline-none w-24 disabled:opacity-40"
              />
            </div>

            {/* Market cap */}
            <div className="max-w-[220px] flex-shrink-0">
              <MarketCapRangeFilter onChange={setMcapRange} />
            </div>

            {/* Min abs delivery */}
            <div className="flex flex-col">
              <label className="text-[12px] text-[#888] font-mono mb-1">Min Del %</label>
              <input type="number" min={0} max={80} step={1} value={minAbsDeliveryPct}
                onChange={e => setMinAbsDeliveryPct(Number(e.target.value))}
                className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] font-mono focus:border-orange-500 outline-none w-24"
              />
            </div>

            {/* Min ADTV */}
            <div className="flex flex-col">
              <label className="text-[12px] text-[#888] font-mono mb-1">Min ADTV (₹ Cr)</label>
              <input type="number" min={0} max={100} step={0.5} value={minAdtvCr}
                onChange={e => setMinAdtvCr(Number(e.target.value))}
                className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] font-mono focus:border-orange-500 outline-none w-24"
              />
            </div>
          </div>
        </section>
      )}

      {/* Summaries + display filters */}
      <section className="grid grid-cols-1 md:grid-cols-[1fr_min-content] gap-4 p-4 border-b border-[#ffffff1a] bg-[#1a1c24]" role="status" aria-live="polite">
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
          <div className="bg-[#2a2c34] border border-[#ffffff1a] rounded p-3 flex flex-col justify-center">
            <span className="text-xs text-[#888] font-mono mb-1">Divergence Signals</span>
            <span className="text-2xl text-orange-400 font-semibold">{filteredData.length}</span>
          </div>
          <div className="bg-[#2a2c34] border border-[#ffffff1a] rounded p-3 flex flex-col justify-center">
            <span className="text-xs text-[#888] font-mono mb-1">Avg Score</span>
            <span className="text-2xl text-[#fafafa] font-semibold">{summaries.avgScore}</span>
          </div>
          <div className="bg-[#2a2c34] border border-[#ffffff1a] rounded p-3 flex flex-col justify-center">
            <span className="text-xs text-[#888] font-mono mb-1">Avg Del Change</span>
            <span className="text-2xl text-[#fafafa] font-semibold">{summaries.avgDelChange > 0 ? '+' : ''}{summaries.avgDelChange.toFixed(1)}pp</span>
          </div>
          <div className="bg-[#2a2c34] border border-[#ffffff1a] rounded p-3 flex flex-col justify-center">
            <span className="text-xs text-[#888] font-mono mb-1">Avg Price Dist</span>
            <span className="text-2xl text-[#fafafa] font-semibold">{summaries.avgDist.toFixed(1)}%</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-3 items-end">
          <div className="flex flex-col">
            <label className="text-[12px] text-[#888] font-mono mb-1">Min Score</label>
            <input type="range" min={0} max={50} value={minScore}
              onChange={e => setMinScore(Number(e.target.value))}
              className="w-24 accent-orange-500" />
            <span className="text-[12px] text-orange-400 font-mono text-center">{minScore || 'Off'}</span>
          </div>
          <div className="flex flex-col">
            <label className="text-[12px] text-[#888] font-mono mb-1">Min Del Change</label>
            <input type="range" min={0} max={20} value={minDeliveryChange}
              onChange={e => setMinDeliveryChange(Number(e.target.value))}
              className="w-24 accent-orange-500" />
            <span className="text-[12px] text-orange-400 font-mono text-center">{minDeliveryChange ? `≥${minDeliveryChange}pp` : 'Off'}</span>
          </div>
          <button
            onClick={() => setWatchlistOnly(f => !f)}
            className={`text-[12px] px-2 py-1 rounded border transition-colors font-mono ${
              watchlistOnly ? 'bg-orange-500/20 border-orange-500/50 text-orange-400' : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#888]'
            }`}
          >
            ★ Watchlist
          </button>
        </div>
      </section>

      {/* Table */}
      <div className="flex-1 min-h-0 overflow-hidden rounded">
        {isScanning && scanStatus?.progress !== undefined && scanStatus.progress < 100 ? (
          <div className="p-8 text-center text-[#888] font-mono text-xs flex flex-col items-center justify-center h-64 gap-4" role="status">
            <RefreshCw className="animate-spin text-orange-500/50" size={24} />
            <div>{scanStatus.message || 'Scanning...'}</div>
            <div className="w-48 h-2 bg-[#ffffff1a] rounded overflow-hidden">
              <div className="h-full bg-orange-500 rounded transition-all" style={{ width: `${scanStatus.progress}%` }} />
            </div>
          </div>
        ) : (
          <ScrollableTable>
            <table className="w-full min-w-max whitespace-nowrap text-left border-collapse">
              <thead className="sticky top-0 bg-[#1a1c24] z-10 shadow-sm border-b border-[#ffffff1a]">
                <tr>
                  {([
                    ['symbol', 'Symbol'],
                    ['close', 'Close'],
                    ['low_in_window', 'Low'],
                    ['price_dist_pct', 'Dist%'],
                    ['latest_del_pct', 'Del%'],
                    ['delivery_change', 'Del Chg'],
                    ['divergence_strength', 'Strength'],
                    ['score', 'Score'],
                    ['horizon', 'Horizon'],
                    ['price_lookback', 'LB'],
                    ['delivery_period', 'DP'],
                    ['delivery_threshold', 'Thr'],
                  ] as [string, string][]).map(([col, label]) => (
                    <th key={col}
                      className={`p-3 text-[12px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap${['close','low_in_window','price_dist_pct','latest_del_pct','delivery_change','divergence_strength','score'].includes(col) ? ' text-right' : ''}`}
                      onClick={() => handleSort(col)}
                      scope="col"
                    >
                      {label} <SortIcon column={col} />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredData.length === 0 ? (
                  <tr>
                    <td colSpan={12} className="p-8 text-center text-[#888] font-mono text-xs">
                      {scanStatus ? 'No divergence signals match your filters.' : 'Click Scan to find divergence signals.'}
                    </td>
                  </tr>
                ) : (
                  filteredData.slice(0, 500).map(d => (
                    <tr key={d.symbol} className="border-b border-[#ffffff0a] hover:bg-[#ffffff05] transition-colors group">
                      <td className="p-3 whitespace-nowrap" scope="row">
                        <span
                          onClick={() => window.open(`/#/chart?symbol=${encodeURIComponent(d.symbol)}`, '_blank')}
                          className="font-bold text-[#fafafa] cursor-pointer hover:text-orange-400 hover:underline inline-flex items-center gap-1 transition-colors"
                        >
                          {d.symbol}
                          <ArrowUpRight size={10} className="opacity-0 group-hover:opacity-100 transition-opacity" />
                        </span>
                      </td>
                      <td className="p-3 text-sm font-mono whitespace-nowrap text-right text-[#fafafa]">{d.close.toFixed(2)}</td>
                      <td className="p-3 text-sm font-mono whitespace-nowrap text-right text-[#888]">{d.low_in_window.toFixed(2)}</td>
                      <td className="p-3 text-sm font-mono whitespace-nowrap text-right">
                        <span className={d.price_dist_pct <= 1 ? 'text-green-400 font-bold' : d.price_dist_pct <= 3 ? 'text-yellow-400' : 'text-[#888]'}>
                          {d.price_dist_pct.toFixed(1)}%
                        </span>
                      </td>
                      <td className="p-3 text-sm font-mono whitespace-nowrap text-right text-[#fafafa]">{d.latest_del_pct.toFixed(1)}%</td>
                      <td className="p-3 text-sm font-mono whitespace-nowrap text-right">
                        <span className={d.delivery_change >= 0 ? 'text-green-400' : 'text-red-400'}>
                          {d.delivery_change > 0 ? '+' : ''}{d.delivery_change.toFixed(1)}
                        </span>
                      </td>
                      <td className="p-3 text-sm font-mono whitespace-nowrap text-right text-[#fafafa]">{d.divergence_strength.toFixed(2)}</td>
                      <td className="p-3 w-36">
                        <div className="flex items-center gap-2">
                          <span className={`text-sm font-mono w-8 text-right font-semibold ${d.score >= 15 ? 'text-orange-400' : d.score >= 8 ? 'text-[#fafafa]' : 'text-[#888]'}`}>
                            {d.score.toFixed(1)}
                          </span>
                          <div className="flex-1 h-1.5 bg-[#ffffff1a] rounded overflow-hidden">
                            <div className="h-full bg-orange-500 rounded" style={{ width: `${Math.min(100, d.score * 4)}%` }} />
                          </div>
                        </div>
                      </td>
                      <td className="p-3 text-xs font-mono whitespace-nowrap text-[#888]">{d.horizon}</td>
                      <td className="p-3 text-xs font-mono whitespace-nowrap text-right text-[#888]">{d.price_lookback}d</td>
                      <td className="p-3 text-xs font-mono whitespace-nowrap text-right text-[#888]">{d.delivery_period}d</td>
                      <td className="p-3 text-xs font-mono whitespace-nowrap text-right text-[#888]">{d.delivery_threshold}%</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </ScrollableTable>
        )}
      </div>
    </main>
  );
}
