import { useState, useEffect, useMemo, useCallback } from 'react';
import { RefreshCw, ExternalLink } from 'lucide-react';
import { API_BASE } from '../config';
import FundTractionButton from '../components/FundTractionButton';

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface ConfluenceSymbol {
  symbol: string;
  sector: string;
  scanner_count: number;
  scanners: string[];
  last_scan: string | null;
  best_grade: string | null;
}

interface ConfluenceResponse {
  generated_at: string;
  symbols: ConfluenceSymbol[];
}

type SortKey = 'scanner_count' | 'symbol' | 'sector' | 'best_grade';

/* ------------------------------------------------------------------ */
/*  Scanner display-name → route mapping                               */
/* ------------------------------------------------------------------ */

const SCANNER_ROUTES: Record<string, string> = {
  'The Trigger': '/trigger',
  'Bottom Hunter': '/bottom-hunter',
  'Invisible Hand': '/invisible-hand',
  'Wyckoff Automaton': '/wyckoff',
  'Liquidity Flip': '/liquidity-flip',
  'Operator Fingerprint': '/operator-fingerprint',
  'Float Exhaustion': '/float-exhaustion',
  'Seasonal Delivery': '/seasonal-delivery',
  'Darvas Box Pro': '/darvas-box-pro',
  'Multibagger Pro': '/multibagger-pro-scanner',
  'Climax Accumulation': '/climax-accumulation',
  'Launchpad Scanner': '/launchpad-scanner',
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

const GRADE_COLORS: Record<string, string> = {
  A: 'bg-green-500/20 text-green-400 border-green-500/30',
  'A+': 'bg-green-500/20 text-green-400 border-green-500/30',
  B: 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30',
  C: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  D: 'bg-red-500/20 text-red-400 border-red-500/30',
};

const SCANNER_COLORS: Record<string, string> = {
  'The Trigger': 'bg-indigo-500/20 text-indigo-300 border-indigo-500/30',
  'Bottom Hunter': 'bg-green-500/20 text-green-300 border-green-500/30',
  'Invisible Hand': 'bg-purple-500/20 text-purple-300 border-purple-500/30',
  'Wyckoff Automaton': 'bg-blue-500/20 text-blue-300 border-blue-500/30',
  'Liquidity Flip': 'bg-cyan-500/20 text-cyan-300 border-cyan-500/30',
  'Operator Fingerprint': 'bg-amber-500/20 text-amber-300 border-amber-500/30',
  'Float Exhaustion': 'bg-red-500/20 text-red-300 border-red-500/30',
  'Seasonal Delivery': 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30',
  'Darvas Box Pro': 'bg-orange-500/20 text-orange-300 border-orange-500/30',
  'Multibagger Pro': 'bg-fuchsia-500/20 text-fuchsia-300 border-fuchsia-500/30',
  'Climax Accumulation': 'bg-rose-500/20 text-rose-300 border-rose-500/30',
  'Launchpad Scanner': 'bg-teal-500/20 text-teal-300 border-teal-500/30',
};

const DEFAULT_SCANNER_COLOR = 'bg-[#ffffff0a] text-[#aaa] border-[#ffffff1a]';

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function ConfluenceView() {
  const [data, setData] = useState<ConfluenceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>('scanner_count');
  const [sortAsc, setSortAsc] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/confluence`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json: ConfluenceResponse = await res.json();
      setData(json);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const sorted = useMemo(() => {
    if (!data) return [];
    const rows = [...data.symbols];
    rows.sort((a, b) => {
      let cmp = 0;
      if (sortKey === 'scanner_count') cmp = a.scanner_count - b.scanner_count;
      else if (sortKey === 'best_grade') {
        // Sort A > B > C > D; nulls at bottom
        const rank = (g: string | null) => {
          if (!g) return -1;
          const u = g.toUpperCase();
          if (u === 'A' || u === 'A+') return 4;
          if (u === 'B') return 3;
          if (u === 'C') return 2;
          if (u === 'D') return 1;
          const n = parseFloat(g);
          return isNaN(n) ? 0 : n;
        };
        cmp = rank(a.best_grade) - rank(b.best_grade);
      } else {
        cmp = String(a[sortKey] ?? '').localeCompare(String(b[sortKey] ?? ''));
      }
      return sortAsc ? cmp : -cmp;
    });
    return rows;
  }, [data, sortKey, sortAsc]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(key === 'symbol'); }
  };

  const SortIndicator = ({ col }: { col: SortKey }) => (
    <span className="ml-1 text-[12px] opacity-50">
      {sortKey === col ? (sortAsc ? '▲' : '▼') : ''}
    </span>
  );

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between mb-4 shrink-0">
        <div>
          <h2 className="text-lg font-bold flex items-center gap-2">
            <span>Confluence</span>
            <span className="text-[#888] text-sm font-normal">Scanner Consensus</span>
          </h2>
          <p className="text-xs text-[#888] mt-0.5 font-mono">
            {data
              ? `${data.symbols.length} symbol${data.symbols.length !== 1 ? 's' : ''} flagged by 2+ scanners`
              : 'Loading...'}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {data && (
            <span className="text-[12px] text-[#888] font-mono">
              Updated {relativeTime(data.generated_at)}
            </span>
          )}
          <FundTractionButton symbols={sorted.map((s: any) => s.symbol)} />
          <button
            onClick={fetchData}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-[#ffffff0a] border border-[#ffffff1a] rounded font-mono text-[#888] hover:text-white transition-colors disabled:opacity-40"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-950/40 border border-red-500/50 p-3 rounded-lg mb-3 text-red-400 text-xs font-mono shrink-0">
          {error}
        </div>
      )}

      {/* Table */}
      <div className="flex-1 min-h-0 overflow-auto rounded-lg border border-[#ffffff1a]">
        {sorted.length === 0 && !loading && !error ? (
          <div className="flex flex-col items-center justify-center h-full py-16 text-center">
            <div className="text-3xl mb-3 opacity-30">🔗</div>
            <p className="text-sm text-[#888] font-mono">
              No confluence symbols found — run at least 2 scanners first.
            </p>
          </div>
        ) : (
          <table className="w-full text-xs font-mono" aria-label="Confluence scanner results">
            <thead className="sticky top-0 z-10 bg-[#1a1c24] border-b border-[#ffffff1a]">
              <tr>
                <th
                  className="text-left px-3 py-2 text-[12px] text-[#888] uppercase tracking-wider cursor-pointer hover:text-white select-none"
                  onClick={() => toggleSort('symbol')}
                >
                  Symbol <SortIndicator col="symbol" />
                </th>
                <th
                  className="text-left px-3 py-2 text-[12px] text-[#888] uppercase tracking-wider cursor-pointer hover:text-white select-none"
                  onClick={() => toggleSort('sector')}
                >
                  Sector <SortIndicator col="sector" />
                </th>
                <th
                  className="text-center px-3 py-2 text-[12px] text-[#888] uppercase tracking-wider cursor-pointer hover:text-white select-none"
                  onClick={() => toggleSort('scanner_count')}
                >
                  # Scanners <SortIndicator col="scanner_count" />
                </th>
                <th className="text-left px-3 py-2 text-[12px] text-[#888] uppercase tracking-wider">
                  Scanners
                </th>
                <th
                  className="text-center px-3 py-2 text-[12px] text-[#888] uppercase tracking-wider cursor-pointer hover:text-white select-none"
                  onClick={() => toggleSort('best_grade')}
                >
                  Best Grade <SortIndicator col="best_grade" />
                </th>
                <th className="text-left px-3 py-2 text-[12px] text-[#888] uppercase tracking-wider">
                  Links
                </th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row) => (
                <tr
                  key={row.symbol}
                  className="border-b border-[#ffffff0a] hover:bg-[#ffffff08] transition-colors"
                >
                  <td className="px-3 py-2 font-bold text-white">{row.symbol}</td>
                  <td className="px-3 py-2 text-[#aaa]">{row.sector || '—'}</td>
                  <td className="px-3 py-2 text-center">
                    <span
                      className={`inline-flex items-center justify-center min-w-[20px] px-1.5 py-0.5 rounded text-[12px] font-bold ${
                        row.scanner_count >= 4
                          ? 'bg-green-500/20 text-green-400 border border-green-500/30'
                          : row.scanner_count >= 3
                            ? 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/30'
                            : 'bg-[#ffffff0a] text-[#aaa] border border-[#ffffff1a]'
                      }`}
                    >
                      {row.scanner_count}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {row.scanners.map((name) => (
                        <span
                          key={name}
                          className={`inline-block px-1.5 py-0.5 rounded text-[12px] border ${
                            SCANNER_COLORS[name] || DEFAULT_SCANNER_COLOR
                          }`}
                        >
                          {name}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-center">
                    {row.best_grade ? (
                      <span
                        className={`inline-block px-2 py-0.5 rounded text-[12px] font-bold border ${
                          GRADE_COLORS[row.best_grade.toUpperCase()] ||
                          'bg-[#ffffff0a] text-[#aaa] border-[#ffffff1a]'
                        }`}
                      >
                        {row.best_grade}
                      </span>
                    ) : (
                      <span className="text-[#888]">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {row.scanners.map((name) => {
                        const route = SCANNER_ROUTES[name];
                        if (!route) return null;
                        return (
                          <a
                            key={name}
                            href={route}
                            title={`View ${name}`}
                            className="inline-flex items-center justify-center w-5 h-5 rounded bg-[#ffffff0a] border border-[#ffffff1a] text-[#888] hover:text-white hover:bg-[#ffffff1a] transition-colors"
                          >
                            <ExternalLink size={10} />
                          </a>
                        );
                      })}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
