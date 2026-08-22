import { useState, useEffect, useMemo, useCallback } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import { ArrowLeft, ExternalLink, TrendingUp, TrendingDown, Minus, Loader2, AlertCircle, RefreshCw, Filter, ArrowUpDown } from 'lucide-react';
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

type SortKey = 'score' | 'pct_vs_sma' | 'fund_count' | 'adds' | 'reduces' | 'symbol';

const SORT_LABELS: Record<SortKey, string> = {
  score: 'Traction Score',
  pct_vs_sma: 'vs SMA %',
  fund_count: 'Fund Count',
  adds: 'Adds',
  reduces: 'Reduces',
  symbol: 'Symbol',
};

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
  // Green for >5%, yellow/amber for 0-5%, red for <0
  let color: string;
  if (pct > 5) {
    color = 'text-green-400';
  } else if (pct >= 0) {
    color = 'text-yellow-400';
  } else {
    color = 'text-red-400';
  }
  const icon = pct > 0 ? <TrendingUp size={12} /> : pct < 0 ? <TrendingDown size={12} /> : <Minus size={12} />;
  return (
    <span className={`flex items-center gap-1 justify-end ${color}`}>
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
  const [minScore, setMinScore] = useState<number>(0);
  const [sortKey, setSortKey] = useState<SortKey>('score');
  const [sortAsc, setSortAsc] = useState(false);

  const symbols = useMemo(() => {
    const raw = searchParams.get('symbols') || '';
    return raw.split(',').map(s => s.trim().toUpperCase()).filter(Boolean);
  }, [searchParams]);

  const fetchData = useCallback(() => {
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

  useEffect(() => { fetchData(); }, [fetchData]);

  // Filter by minimum score
  const filteredData = useMemo(() => {
    if (!data) return [];
    return symbols
      .map(sym => data.symbols[sym])
      .filter((d): d is TractionData => d != null && (d.traction_score ?? 0) >= minScore);
  }, [data, symbols, minScore]);

  // Sort
  const sortedData = useMemo(() => {
    return [...filteredData].sort((a, b) => {
      let va: number, vb: number;
      switch (sortKey) {
        case 'score':
          va = a.traction_score ?? -Infinity;
          vb = b.traction_score ?? -Infinity;
          break;
        case 'pct_vs_sma':
          va = a.pct_vs_sma ?? -Infinity;
          vb = b.pct_vs_sma ?? -Infinity;
          break;
        case 'fund_count':
          va = a.fund_count ?? 0;
          vb = b.fund_count ?? 0;
          break;
        case 'adds':
          va = a.adds_new ?? 0;
          vb = b.adds_new ?? 0;
          break;
        case 'reduces':
          va = a.reduces_closes ?? 0;
          vb = b.reduces_closes ?? 0;
          break;
        case 'symbol':
          return sortAsc
            ? a.symbol.localeCompare(b.symbol)
            : b.symbol.localeCompare(a.symbol);
        default:
          va = 0; vb = 0;
      }
      return sortAsc ? va - vb : vb - va;
    });
  }, [filteredData, sortKey, sortAsc]);

  // Symbols with no data
  const noDataSymbols = useMemo(() => {
    if (!data) return [];
    return symbols.filter(sym => data.symbols[sym] == null);
  }, [data, symbols]);

  // Summary stats
  const summary = useMemo(() => {
    if (filteredData.length === 0) return null;
    const scores = filteredData.map(d => d.traction_score ?? 0);
    const pctVs = filteredData.map(d => d.pct_vs_sma ?? 0);
    const funds = filteredData.map(d => d.fund_count ?? 0);
    const adds = filteredData.map(d => d.adds_new ?? 0);
    const reduces = filteredData.map(d => d.reduces_closes ?? 0);
    return {
      count: filteredData.length,
      avgScore: scores.reduce((a, b) => a + b, 0) / scores.length,
      avgPct: pctVs.reduce((a, b) => a + b, 0) / pctVs.length,
      totalFunds: funds.reduce((a, b) => a + b, 0),
      totalAdds: adds.reduce((a, b) => a + b, 0),
      totalReduces: reduces.reduce((a, b) => a + b, 0),
      greenCount: filteredData.filter(d => (d.pct_vs_sma ?? 0) > 5).length,
      yellowCount: filteredData.filter(d => { const p = d.pct_vs_sma ?? 0; return p >= 0 && p <= 5; }).length,
      redCount: filteredData.filter(d => (d.pct_vs_sma ?? 0) < 0).length,
    };
  }, [filteredData]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortAsc(!sortAsc);
    } else {
      setSortKey(key);
      setSortAsc(false);
    }
  };

  const sortIndicator = (key: SortKey) => {
    if (sortKey !== key) return <ArrowUpDown size={10} className="text-[#555]" />;
    return <ArrowUpDown size={10} className={sortAsc ? 'text-green-400' : 'text-indigo-400'} />;
  };

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
      <div className="flex items-center justify-between mb-3 shrink-0">
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
        <button
          onClick={fetchData}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-[#ccc] transition-colors disabled:opacity-40"
        >
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {/* Filters + Sort Bar */}
      <div className="flex items-center gap-4 mb-3 shrink-0 flex-wrap">
        {/* Min Score Filter */}
        <div className="flex items-center gap-2">
          <Filter size={12} className="text-[#888]" />
          <label className="text-xs text-[#888]">Min Score:</label>
          <input
            type="number"
            value={minScore}
            onChange={e => setMinScore(Number(e.target.value) || 0)}
            className="w-20 px-2 py-1 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-xs text-white text-right focus:outline-none focus:ring-1 focus:ring-indigo-500/50"
            min={0}
            step={10}
          />
        </div>

        {/* Sort Dropdown */}
        <div className="flex items-center gap-2">
          <ArrowUpDown size={12} className="text-[#888]" />
          <label className="text-xs text-[#888]">Sort by:</label>
          <select
            value={sortKey}
            onChange={e => { setSortKey(e.target.value as SortKey); setSortAsc(false); }}
            className="px-2 py-1 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-xs text-white focus:outline-none focus:ring-1 focus:ring-indigo-500/50"
          >
            {Object.entries(SORT_LABELS).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
          <button
            onClick={() => setSortAsc(!sortAsc)}
            className="px-2 py-1 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-[#ccc] transition-colors"
            title={sortAsc ? 'Ascending' : 'Descending'}
          >
            {sortAsc ? '↑ Asc' : '↓ Desc'}
          </button>
        </div>

        {/* Quick filter buttons */}
        <div className="flex items-center gap-1">
          <span className="text-xs text-[#888] mr-1">Quick:</span>
          {[0, 50, 100, 200, 500].map(threshold => (
            <button
              key={threshold}
              onClick={() => setMinScore(threshold)}
              className={`px-2 py-0.5 rounded text-[10px] transition-colors ${
                minScore === threshold
                  ? 'bg-indigo-600 text-white'
                  : 'bg-[#ffffff0a] text-[#888] hover:bg-[#ffffff15] hover:text-white'
              }`}
            >
              {threshold}+
            </button>
          ))}
        </div>
      </div>

      {/* Summary Bar */}
      {summary && (
        <div className="flex items-center gap-4 mb-3 px-3 py-2 bg-[#ffffff05] border border-[#ffffff0a] rounded text-xs shrink-0 flex-wrap">
          <span className="text-[#888]">
            Showing <span className="text-white font-semibold">{summary.count}</span>
            {noDataSymbols.length > 0 && (
              <> · <span className="text-[#555]">{noDataSymbols.length} without data</span></>
            )}
          </span>
          <span className="text-[#333]">|</span>
          <span className="text-indigo-400">
            Avg Score: <span className="font-semibold">{summary.avgScore.toFixed(1)}</span>
          </span>
          <span className="text-[#333]">|</span>
          <span className="text-cyan-400">
            Total Funds: <span className="font-semibold">{summary.totalFunds.toLocaleString()}</span>
          </span>
          <span className="text-[#333]">|</span>
          <span className="text-green-400">
            Adds: <span className="font-semibold">{summary.totalAdds.toLocaleString()}</span>
          </span>
          <span className="text-[#333]">|</span>
          <span className="text-red-400">
            Reduces: <span className="font-semibold">{summary.totalReduces.toLocaleString()}</span>
          </span>
          <span className="text-[#333]">|</span>
          {/* SMA color breakdown */}
          <span className="flex items-center gap-2">
            <span className="text-green-400">▲ {summary.greenCount}</span>
            <span className="text-yellow-400">● {summary.yellowCount}</span>
            <span className="text-red-400">▼ {summary.redCount}</span>
          </span>
        </div>
      )}

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
                <th className="text-left px-3 py-2 cursor-pointer hover:text-white transition-colors" onClick={() => toggleSort('symbol')}>
                  <span className="flex items-center gap-1">Symbol {sortIndicator('symbol')}</span>
                </th>
                <th className="text-right px-3 py-2 cursor-pointer hover:text-white transition-colors" onClick={() => toggleSort('score')}>
                  <span className="flex items-center gap-1 justify-end">Score {sortIndicator('score')}</span>
                </th>
                <th className="text-right px-3 py-2 cursor-pointer hover:text-white transition-colors" onClick={() => toggleSort('pct_vs_sma')}>
                  <span className="flex items-center gap-1 justify-end">vs SMA {sortIndicator('pct_vs_sma')}</span>
                </th>
                <th className="text-right px-3 py-2 cursor-pointer hover:text-white transition-colors" onClick={() => toggleSort('fund_count')}>
                  <span className="flex items-center gap-1 justify-end">Funds {sortIndicator('fund_count')}</span>
                </th>
                <th className="text-right px-3 py-2 cursor-pointer hover:text-white transition-colors" onClick={() => toggleSort('adds')}>
                  <span className="flex items-center gap-1 justify-end">Adds {sortIndicator('adds')}</span>
                </th>
                <th className="text-right px-3 py-2 cursor-pointer hover:text-white transition-colors" onClick={() => toggleSort('reduces')}>
                  <span className="flex items-center gap-1 justify-end">Reduces {sortIndicator('reduces')}</span>
                </th>
                <th className="text-right px-3 py-2">Close</th>
                <th className="text-right px-3 py-2">SMA 30</th>
                <th className="text-center px-3 py-2">Month</th>
                <th className="text-center px-3 py-2">Chart</th>
              </tr>
            </thead>
            <tbody>
              {sortedData.map(d => (
                <tr key={d.symbol} className="border-b border-[#ffffff0a] hover:bg-[#ffffff05]">
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
                        href={`/#/chart?symbol=${d.symbol}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-indigo-400 hover:text-indigo-300 transition-colors"
                      title={`Open chart for ${d.symbol}`}
                    >
                      <ExternalLink size={12} />
                    </a>
                  </td>
                </tr>
              ))}
              {/* No-data rows */}
              {noDataSymbols.map(sym => (
                <tr key={sym} className="border-b border-[#ffffff0a] hover:bg-[#ffffff05] opacity-50">
                  <td className="px-3 py-2 font-bold text-[#888]">{sym}</td>
                  <td colSpan={9} className="px-3 py-2 text-[#555] italic">No traction data</td>
                </tr>
              ))}
            </tbody>
            {/* Summary footer */}
            {summary && sortedData.length > 0 && (
              <tfoot className="border-t border-[#ffffff1a] bg-[#ffffff05]">
                <tr className="text-[#888]">
                  <td className="px-3 py-2 font-bold text-white">
                    TOTAL ({summary.count})
                  </td>
                  <td className="px-3 py-2 text-right text-indigo-400 font-semibold">
                    {summary.avgScore.toFixed(1)} avg
                  </td>
                  <td className="px-3 py-2 text-right">
                    <span className={summary.avgPct > 5 ? 'text-green-400' : summary.avgPct >= 0 ? 'text-yellow-400' : 'text-red-400'}>
                      {summary.avgPct >= 0 ? '+' : ''}{summary.avgPct.toFixed(2)}% avg
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right text-cyan-400 font-semibold">
                    {summary.totalFunds.toLocaleString()}
                  </td>
                  <td className="px-3 py-2 text-right text-green-400 font-semibold">
                    {summary.totalAdds.toLocaleString()}
                  </td>
                  <td className="px-3 py-2 text-right text-red-400 font-semibold">
                    {summary.totalReduces.toLocaleString()}
                  </td>
                  <td colSpan={4}></td>
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      )}

      {/* Empty state after load */}
      {!loading && !error && data && sortedData.length === 0 && (
        <div className="flex items-center justify-center h-64 text-[#555] flex-col gap-2">
          <AlertCircle size={24} />
          <span>No symbols match the current filter (min score: {minScore})</span>
          <button
            onClick={() => setMinScore(0)}
            className="text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
          >
            Clear filter
          </button>
        </div>
      )}
    </div>
  );
}
