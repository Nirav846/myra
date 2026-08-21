import { useState, useEffect, useMemo } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import { ArrowLeft, ExternalLink, TrendingUp, TrendingDown, Minus, Loader2, AlertCircle } from 'lucide-react';
import { API_BASE } from '../config';

interface TractionData {
  symbol: string;
  month: string | null;
  traction_score: number | null;
  fund_count: number | null;
  adds_new: number | null;
  reduces_closes: number | null;
  sma_30: number | null;
  month_end_close: number | null;
  close_latest: number | null;
  pct_vs_sma: number | null;
}

interface BatchResponse {
  latest_month: string | null;
  symbols: Record<string, TractionData | null>;
}

function formatNum(val: number | null, decimals = 1): string {
  if (val == null) return '—';
  return val.toFixed(decimals);
}

function formatInt(val: number | null): string {
  if (val == null) return '—';
  return val.toLocaleString();
}

function SmaBadge({ pct }: { pct: number | null }) {
  if (pct == null) return <span className="text-[#555]">—</span>;
  const color = pct > 0 ? 'text-green-400' : pct < 0 ? 'text-red-400' : 'text-[#888]';
  const icon = pct > 0 ? <TrendingUp size={12} /> : pct < 0 ? <TrendingDown size={12} /> : <Minus size={12} />;
  return (
    <span className={`flex items-center gap-1 ${color}`}>
      {icon}
      {pct >= 0 ? '+' : ''}{pct.toFixed(2)}%
    </span>
  );
}

export default function FundTractionReportView() {
  const [searchParams] = useSearchParams();
  const [data, setData] = useState<BatchResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const symbols = useMemo(() => {
    const raw = searchParams.get('symbols') || '';
    return raw.split(',').map(s => s.trim().toUpperCase()).filter(Boolean);
  }, [searchParams]);

  useEffect(() => {
    if (symbols.length === 0) {
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);

    fetch(`${API_BASE}/fund-traction/batch?symbols=${symbols.join(',')}`)
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then(setData)
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }, [symbols]);

  // Sort by traction_score descending
  const sortedSymbols = useMemo(() => {
    if (!data) return symbols;
    return [...symbols].sort((a, b) => {
      const sa = data.symbols[a]?.traction_score ?? -Infinity;
      const sb = data.symbols[b]?.traction_score ?? -Infinity;
      return sb - sa;
    });
  }, [data, symbols]);

  if (symbols.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 text-[#888]">
        <AlertCircle size={48} className="text-amber-500/50" />
        <h2 className="text-lg font-semibold text-white">No symbols selected</h2>
        <p className="text-sm text-center max-w-md">
          Please run a scanner first, then click the <strong>"MF Report"</strong> button
          to view fund traction data for the scanner's candidates.
        </p>
        <Link
          to="/mission-control"
          className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-sm transition-colors"
        >
          <ArrowLeft size={14} /> Back to Dashboard
        </Link>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between mb-4 shrink-0">
        <div className="flex items-center gap-3">
          <Link to="/mission-control" className="text-[#888] hover:text-white transition-colors">
            <ArrowLeft size={18} />
          </Link>
          <div>
            <h1 className="text-lg font-bold text-white flex items-center gap-2">
              <TrendingUp size={18} className="text-indigo-400" />
              Fund Traction Report
            </h1>
            <p className="text-xs text-[#888]">
              {symbols.length} symbols
              {data?.latest_month && ` · Latest month: ${data.latest_month}`}
            </p>
          </div>
        </div>
      </div>

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center h-64 gap-2 text-[#888]">
          <Loader2 size={20} className="animate-spin" />
          Loading traction data...
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="bg-red-950/40 border border-red-500/50 rounded p-4 text-red-400 text-sm">
          {error}
        </div>
      )}

      {/* Table */}
      {!loading && !error && data && (
        <div className="flex-1 overflow-auto">
          <table className="w-full text-xs font-mono">
            <thead className="sticky top-0 bg-[#0e1117] z-10">
              <tr className="border-b border-[#ffffff1a] text-[#888] uppercase tracking-wider">
                <th className="text-left px-3 py-2">Symbol</th>
                <th className="text-right px-3 py-2">Score</th>
                <th className="text-right px-3 py-2">vs SMA</th>
                <th className="text-right px-3 py-2">Funds</th>
                <th className="text-right px-3 py-2">Adds</th>
                <th className="text-right px-3 py-2">Reduces</th>
                <th className="text-right px-3 py-2">Close</th>
                <th className="text-right px-3 py-2">SMA 30</th>
                <th className="text-center px-3 py-2">Month</th>
                <th className="text-center px-3 py-2">Chart</th>
              </tr>
            </thead>
            <tbody>
              {sortedSymbols.map(sym => {
                const d = data.symbols[sym];
                if (!d) {
                  return (
                    <tr key={sym} className="border-b border-[#ffffff0a] hover:bg-[#ffffff05]">
                      <td className="px-3 py-2 font-bold text-white">{sym}</td>
                      <td colSpan={9} className="px-3 py-2 text-[#555] italic">No traction data</td>
                    </tr>
                  );
                }
                return (
                  <tr key={sym} className="border-b border-[#ffffff0a] hover:bg-[#ffffff05]">
                    <td className="px-3 py-2 font-bold text-white">{d.symbol}</td>
                    <td className="px-3 py-2 text-right">
                      <span className="text-indigo-400 font-semibold">
                        {formatNum(d.traction_score)}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <SmaBadge pct={d.pct_vs_sma} />
                    </td>
                    <td className="px-3 py-2 text-right text-cyan-400">
                      {formatInt(d.fund_count)}
                    </td>
                    <td className="px-3 py-2 text-right text-green-400">
                      {formatInt(d.adds_new)}
                    </td>
                    <td className="px-3 py-2 text-right text-red-400">
                      {formatInt(d.reduces_closes)}
                    </td>
                    <td className="px-3 py-2 text-right text-[#ccc]">
                      {formatNum(d.close_latest, 2)}
                    </td>
                    <td className="px-3 py-2 text-right text-[#888]">
                      {formatNum(d.sma_30, 2)}
                    </td>
                    <td className="px-3 py-2 text-center text-[#888]">
                      {d.month || '—'}
                    </td>
                    <td className="px-3 py-2 text-center">
                      <a
                        href={`/chart?symbol=${d.symbol}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-indigo-400 hover:text-indigo-300 transition-colors"
                        title={`Open chart for ${d.symbol}`}
                      >
                        <ExternalLink size={12} />
                      </a>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Empty state after load */}
      {!loading && !error && data && sortedSymbols.length === 0 && (
        <div className="flex items-center justify-center h-64 text-[#555]">
          No data to display.
        </div>
      )}
    </div>
  );
}
