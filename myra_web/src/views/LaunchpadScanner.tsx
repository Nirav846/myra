import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Librarian } from '../lib/Librarian';
import { Rocket, Filter, AlertCircle, ArrowUpRight, RefreshCw, CheckCircle, Clock, AlertTriangle, XCircle, Star } from 'lucide-react';
import MarketCapRangeFilter from '../components/MarketCapRangeFilter';
import { fetchMarketCapMap } from '../lib/marketCapCache';
import { useWatchlist } from '../lib/WatchlistContext';
import { StarButton } from '../components/StarButton';
import ScrollableTable from '../components/ScrollableTable';
import { API_BASE } from '../config';

interface ScanPrediction {
  symbol: string;
  trigger_date: string;
  predicted_return_pct: number;
  predicted_days_to_breakout: number;
  current_digestion_days: number;
  sector?: string | null;
  market_cap?: number | null;
  breakout_probability?: number;
}

interface LaunchpadScanStatus {
  scan_status: string;
  last_scan: string | null;
  progress: number;
  message: string;
  predictions: ScanPrediction[];
}

interface PipelineTaskStatus {
  last_run: string | null;
  last_status: string;
  error_message: string | null;
  progress_pct: number;
}

interface PipelineStatus {
  overall: { status: string };
  tasks: Record<string, PipelineTaskStatus>;
}

function formatMarketCap(mcap: number | null | undefined): string {
  if (mcap == null) return '\u2014';
  const cr = Math.round(mcap / 10000000);
  return `\u20B9${cr.toLocaleString('en-IN')}Cr`;
}

function getConfidence(ret: number): string {
  if (ret >= 8) return 'High';
  if (ret >= 4) return 'Medium';
  return 'Low';
}

function isStale(dateStr: string | null | undefined): boolean {
  if (!dateStr) return true;
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return true;
  return Date.now() - d.getTime() > 24 * 60 * 60 * 1000;
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

export default function LaunchpadScannerView({ lib, onNavigate }: { lib: Librarian; onNavigate: (tab: string, symbol: string) => void }) {
  const [scanStatus, setScanStatus] = useState<LaunchpadScanStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pipelineStatus, setPipelineStatus] = useState<PipelineStatus | null>(null);
  const [staleBannerOpen, setStaleBannerOpen] = useState(true);

  const { isWatched } = useWatchlist();
  const [watchlistOnly, setWatchlistOnly] = useState(false);

  const [minReturn, setMinReturn] = useState<number>(0);
  const [mcapRange, setMcapRange] = useState<{ min: number; max: number } | null>(null);
  const mcapMapRef = useRef<Map<string, number>>(new Map());

  useEffect(() => { fetchMarketCapMap().then(m => mcapMapRef.current = m); }, []);

  const mountedRef = useRef(true);
  const scanAbortRef = useRef<AbortController | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const predictions = scanStatus?.predictions ?? [];

  const filteredData = useMemo(() => {
    let result = predictions.filter(d => d.predicted_return_pct >= minReturn);
    if (mcapRange) {
      const map = mcapMapRef.current;
      result = result.filter(d => {
        const mcap = map.get(d.symbol);
        return mcap !== undefined && mcap >= mcapRange.min && mcap <= mcapRange.max;
      });
    }
    if (watchlistOnly) result = result.filter(d => isWatched(d.symbol));
    return result.sort((a, b) => b.predicted_return_pct - a.predicted_return_pct);
  }, [predictions, minReturn, mcapRange, watchlistOnly, isWatched]);

  const avgExpectedReturn = useMemo(() => {
    if (filteredData.length === 0) return 0;
    const total = filteredData.reduce((acc, curr) => acc + curr.predicted_return_pct, 0);
    return total / filteredData.length;
  }, [filteredData]);

  const highestConfidence = useMemo(() => {
    if (filteredData.length === 0) return null;
    return filteredData[0];
  }, [filteredData]);

  const fetchPipelineStatus = useCallback(async () => {
    if (!mountedRef.current) return;
    try {
      const res = await fetch(`${API_BASE}/pipeline/status`);
      if (res.ok && mountedRef.current) {
        setPipelineStatus(await res.json());
      }
    } catch { /* ignore */ }
  }, []);

  const clearPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const fetchScanStatus = useCallback(async () => {
    if (!mountedRef.current) return;
    const ac = new AbortController();
    scanAbortRef.current = ac;
    try {
      const res = await fetch(`${API_BASE}/launchpad/status`, { signal: ac.signal });
      if (!mountedRef.current) return;
      if (res.ok) {
        const data: LaunchpadScanStatus = await res.json();
        if (!mountedRef.current) return;
        setScanStatus(data);
        setError(null);

        if (data.scan_status === 'completed' || data.scan_status === 'error' || data.scan_status === 'no_model' || data.scan_status === 'no_events') {
          clearPolling();
          setLoading(false);
        } else if (data.scan_status === 'scanning' && !pollTimerRef.current) {
          pollTimerRef.current = setInterval(fetchScanStatus, 2000);
          setLoading(true);
        }
      }
    } catch (e: any) {
      if (e.name !== 'AbortError' && mountedRef.current) {
        setError(e.message || 'Error connecting to backend');
      }
    }
  }, [clearPolling]);

  const startScan = useCallback(async () => {
    if (!mountedRef.current) return;
    scanAbortRef.current?.abort();
    setLoading(true);
    setError(null);
    clearPolling();

    try {
      const res = await fetch(`${API_BASE}/launchpad/scan`, { method: 'POST' });
      if (!mountedRef.current) return;
      if (res.ok) {
        await fetchScanStatus();
        pollTimerRef.current = setInterval(fetchScanStatus, 2000);
      } else {
        const err = await res.json().catch(() => ({ detail: 'Failed to start scan' }));
        setError(err.detail || 'Failed to start scan');
        setLoading(false);
      }
    } catch (e: any) {
      if (mountedRef.current) {
        setError(e.message || 'Error connecting to backend');
        setLoading(false);
      }
    }
  }, [fetchScanStatus, clearPolling]);

  useEffect(() => {
    mountedRef.current = true;
    fetchPipelineStatus();
    fetchScanStatus();
    return () => {
      mountedRef.current = false;
      clearPolling();
      scanAbortRef.current?.abort();
    };
  }, [fetchPipelineStatus, fetchScanStatus, clearPolling]);

  const tasks = pipelineStatus?.tasks ?? {};
  const enrichmentStale = isStale(tasks.enrichment?.last_run);
  const fundamentalsStale = isStale(tasks.fundamentals_sync?.last_run);
  const showStaleWarning = staleBannerOpen && (enrichmentStale || fundamentalsStale);

  const isScanning = scanStatus?.scan_status === 'scanning';
  const progressPct = scanStatus?.progress ?? 0;

  const statusIcon = () => {
    switch (scanStatus?.scan_status) {
      case 'scanning': return <RefreshCw size={16} className="text-cyan-400 animate-spin" />;
      case 'completed': return <CheckCircle size={16} className="text-green-400" />;
      case 'no_model':
      case 'no_events': return <AlertCircle size={16} className="text-orange-400" />;
      case 'error': return <XCircle size={16} className="text-red-400" />;
      default: return <Clock size={16} className="text-[#666]" />;
    }
  };

  const statusColor = () => {
    switch (scanStatus?.scan_status) {
      case 'scanning': return 'text-cyan-400';
      case 'completed': return 'text-green-400';
      case 'no_model':
      case 'no_events': return 'text-orange-400';
      case 'error': return 'text-red-400';
      default: return 'text-[#888]';
    }
  };

  const statusLabel = () => {
    switch (scanStatus?.scan_status) {
      case 'scanning': return 'Scanning...';
      case 'completed': return `Completed (${relativeTime(scanStatus?.last_scan)})`;
      case 'no_model': return 'Model not trained';
      case 'no_events': return 'No stocks in digestion';
      case 'error': return 'Scan failed';
      default: return 'Idle \u2014 click Scan to start';
    }
  };

  return (
    <div className="flex flex-col flex-1 min-h-0 relative gap-4 p-4">
      {showStaleWarning && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded px-4 py-2 flex items-center gap-2 text-xs font-mono">
          <AlertTriangle size={14} className="text-amber-400 shrink-0" />
          <span className="text-amber-300/90">
            Data may be stale.{' '}
            <button onClick={() => onNavigate('Data Sync', '')} className="underline hover:text-amber-200">
              Run Feature Enrichment and Fundamentals Sync from Data Sync
            </button>
          </span>
          <button onClick={() => setStaleBannerOpen(false)} className="ml-auto text-amber-500/50 hover:text-amber-300">
            <XCircle size={14} />
          </button>
        </div>
      )}

      <div className="flex justify-between items-center bg-[#1a1c24] border border-[#ffffff1a] rounded p-4">
        <div className="flex items-center gap-3">
          <div className="bg-red-500/20 p-2 rounded">
            <Rocket className="text-red-400" size={24} />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-[#fafafa]">Launchpad Scanner</h1>
            <p className="text-xs font-mono text-[#888]">Quantifying Breakout Mechanics</p>
          </div>
        </div>
        <button
          onClick={startScan}
          disabled={isScanning}
          className="px-4 py-2 bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white rounded text-xs font-semibold flex items-center gap-2 transition-colors"
        >
          {isScanning ? (
            <><RefreshCw size={14} className="animate-spin" /> Scanning...</>
          ) : (
            <><Rocket size={14} fill="currentColor" /> Scan</>
          )}
        </button>
      </div>

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

      {!isScanning && scanStatus && (
        <div className={`flex items-center gap-2 px-3 py-2 rounded text-xs font-mono border ${
          scanStatus.scan_status === 'completed' ? 'bg-green-500/10 border-green-500/30 text-green-300' :
          scanStatus.scan_status === 'no_model' || scanStatus.scan_status === 'no_events' ? 'bg-orange-500/10 border-orange-500/30 text-orange-300' :
          scanStatus.scan_status === 'error' ? 'bg-red-500/10 border-red-500/30 text-red-300' :
          'bg-[#ffffff0a] border-[#ffffff1a] text-[#888]'
        }`}>
          {statusIcon()}
          <span className={statusColor()}>{statusLabel()}</span>
          {scanStatus.scan_status === 'completed' && scanStatus.last_scan && (
            <span className="text-[#888] ml-1">
              ({new Date(scanStatus.last_scan).toLocaleTimeString()})
            </span>
          )}
          <span className="ml-auto text-[#666]">{scanStatus.message}</span>
        </div>
      )}

      {error && !isScanning && (
        <div className="bg-red-500/10 border border-red-500/30 rounded px-4 py-2 flex items-center gap-2 text-xs font-mono text-red-300">
          <AlertCircle size={14} className="shrink-0" />
          <span>Error: {error}</span>
        </div>
      )}

      {scanStatus?.scan_status === 'no_model' && !isScanning && (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center text-[#666] font-mono flex flex-col items-center gap-2">
            <AlertCircle size={32} className="opacity-50" />
            <p>No launchpad model trained yet.</p>
            <button onClick={() => onNavigate('ML Lab', '')} className="mt-2 px-4 py-2 bg-[#ffffff1a] hover:bg-[#ffffff2a] rounded text-white text-xs transition-colors">
              Go to ML Lab to train
            </button>
          </div>
        </div>
      )}

      {scanStatus?.scan_status === 'no_events' && !isScanning && (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center text-[#666] font-mono flex flex-col items-center gap-2">
            <AlertCircle size={32} className="opacity-50" />
            <p>No stocks currently in digestion phase.</p>
            <p className="text-[10px]">Check back later or run event labelling from ML Lab.</p>
          </div>
        </div>
      )}

      {scanStatus?.scan_status === 'error' && !isScanning && (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center text-[#666] font-mono flex flex-col items-center gap-2">
            <XCircle size={32} className="opacity-50 text-red-400" />
            <p className="text-red-400">Scan failed: {scanStatus.message}</p>
          </div>
        </div>
      )}

      {scanStatus?.scan_status === 'completed' && predictions.length === 0 && !isScanning && (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center text-[#666] font-mono flex flex-col items-center gap-2">
            <AlertCircle size={32} className="opacity-50" />
            <p>No stocks in digestion phase.</p>
            <p className="text-[10px]">Check back later or run event labelling from ML Lab.</p>
          </div>
        </div>
      )}

      {isScanning && (
        <div className="flex-1 flex justify-center items-center font-mono text-[#888] animate-pulse">
          Running Inferences...
        </div>
      )}

      {(scanStatus?.scan_status === 'completed' || scanStatus?.scan_status === 'idle') && predictions.length > 0 && !isScanning && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4">
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider mb-1">Stocks in Launch Window</div>
              <div className="text-2xl font-bold text-[#fafafa]">{filteredData.length}</div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4">
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider mb-1">Avg Expected Return</div>
              <div className={`text-2xl font-bold ${avgExpectedReturn > 0 ? 'text-green-400' : 'text-red-400'}`}>
                {avgExpectedReturn.toFixed(2)}%
              </div>
            </div>
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4">
              <div className="text-[10px] text-[#888] font-mono uppercase tracking-wider mb-1">Highest Confidence Setup</div>
              <div className="text-xl font-bold text-[#fafafa] flex items-center gap-2">
                {highestConfidence ? (
                  <>
                    <span
                      className="cursor-pointer hover:text-cyan-400 decoration-cyan-400/50 underline underline-offset-4"
                      onClick={() => window.open(`/#/chart?symbol=${encodeURIComponent(highestConfidence.symbol)}`, '_blank')}
                    >
                      {highestConfidence.symbol}
                    </span>
                    <span className="text-xs px-2 py-0.5 rounded bg-green-500/10 text-green-400 font-mono">
                      {highestConfidence.predicted_return_pct.toFixed(2)}% Return
                    </span>
                  </>
                ) : (
                  <span className="text-[#666]">&mdash;</span>
                )}
              </div>
            </div>
          </div>

          <div className="bg-[#0e1117] border border-[#ffffff1a] rounded p-4 flex flex-wrap gap-4 items-end">
            <div className="flex items-center gap-2 mb-1 text-xs text-[#888] w-full">
              <Filter size={14} /> <span className="font-mono uppercase font-semibold">Filters</span>
            </div>
            <div className="flex flex-col gap-1 w-64">
              <div className="flex justify-between items-center text-[10px] text-[#888] font-mono">
                <span>Min Exp Return</span>
                <span>{minReturn}%</span>
              </div>
              <input
                type="range"
                min="-10"
                max="50"
                step="1"
                value={minReturn}
                onChange={e => setMinReturn(parseInt(e.target.value))}
                className="w-full accent-cyan-500"
              />
            </div>
            <div className="max-w-[280px] flex-shrink-0">
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
                <Star size={11} fill={watchlistOnly ? 'currentColor' : 'none'} />
                Only Starred
              </button>
            </div>
          </div>

          <div className="flex-1 bg-[#1a1c24] border border-[#ffffff1a] rounded overflow-hidden">
            <ScrollableTable>
              <table className="w-full min-w-max text-left text-xs font-mono whitespace-nowrap">
                <thead className="sticky top-0 z-10 bg-[#1a1c24] text-[#888] shadow-sm">
                  <tr>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider">Symbol</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">Trigger Date</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">Age (Days)</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">Mkt Cap</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">Predicted Days to Breakout</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-right">Exp. Return</th>
                    <th className="px-4 py-3 font-semibold uppercase tracking-wider text-center">Confidence</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#ffffff0a]">
                  {filteredData.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="px-4 py-8 text-center text-[#666]">No setups match current filters.</td>
                    </tr>
                  ) : (
                    filteredData.map((row, i) => {
                      const conf = getConfidence(row.predicted_return_pct);
                      return (
                        <tr key={row.symbol + '-' + row.trigger_date} className="hover:bg-[#ffffff05] transition-colors">
                          <td className="px-4 py-3 text-[#fafafa] font-bold">
                            <div className="flex items-center gap-1.5">
                              <StarButton symbol={row.symbol} size={11} />
                              <button
                                onClick={() => window.open(`/#/chart?symbol=${encodeURIComponent(row.symbol)}`, '_blank')}
                                className="hover:text-cyan-400 inline-flex items-center gap-1 transition-colors group"
                              >
                                {row.symbol} <ArrowUpRight size={12} className="opacity-0 group-hover:opacity-100" />
                              </button>
                            </div>
                          </td>
                          <td className="px-4 py-3 text-[#aaa] text-right">{row.trigger_date}</td>
                          <td className="px-4 py-3 text-[#ccc] text-right font-bold">{row.current_digestion_days}</td>
                          <td className="px-4 py-3 text-[#ccc] text-right">{formatMarketCap(row.market_cap)}</td>
                          <td className="px-4 py-3 text-[#ccc] text-right">{Number.isFinite(row.predicted_days_to_breakout) ? row.predicted_days_to_breakout.toFixed(1) : '-'}</td>
                          <td className="px-4 py-3 text-right">
                            <span className={row.predicted_return_pct > 0 ? 'text-cyan-400 font-bold' : 'text-red-400'}>
                              {row.predicted_return_pct.toFixed(2)}%
                            </span>
                          </td>
                          <td className="px-4 py-3 text-center">
                            <span className={`px-2 py-0.5 rounded text-[10px] uppercase font-bold
                              ${conf === 'High' ? 'bg-green-500/10 text-green-400 border border-green-500/20' :
                                conf === 'Medium' ? 'bg-yellow-500/10 text-yellow-400 border border-yellow-500/20' :
                                'bg-red-500/10 text-red-400 border border-red-500/20'}`}
                            >
                              {conf}
                            </span>
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </ScrollableTable>
          </div>
        </>
      )}

      {(!scanStatus || (scanStatus.scan_status === 'idle' && predictions.length === 0)) && !isScanning && !error && (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center text-[#666] font-mono flex flex-col items-center gap-2">
            <Rocket size={32} className="opacity-30" />
            <p>Click Scan to detect breakout setups.</p>
          </div>
        </div>
      )}
    </div>
  );
}
