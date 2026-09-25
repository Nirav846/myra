import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft, RefreshCw, Download, Handshake } from 'lucide-react';
import { API_BASE } from '../config';
import { 
  VirtualizedTable, 
  SignalBadge, 
  MiniBarCell, 
  FormatCurrency,
  type TableColumn,
  type SortState
} from '../components/ui/VirtualizedTable';
import { formatScannerCsv } from '../lib/scannerCsv';

interface CrossBuyStock {
  symbol: string; month: string;
  total_funds: number | null; large_funds: number | null; mid_funds: number | null;
  small_funds: number | null; multi_funds: number | null; other_funds: number | null;
  cross_buy_ratio: number | null; signal_tag: string | null;
  stock_category: string | null; market_cap: number | null; sector: string | null;
}
interface ScannerResponse { month: string | null; stocks: CrossBuyStock[]; total: number; }
type SortKey = keyof CrossBuyStock;
type SignalTag = 'STRONG_CROSS_BUY' | 'CROSS_BUY' | 'MIXED' | 'STYLE_CONCENTRATED';

const TAGS: (SignalTag | '')[] = ['', 'STRONG_CROSS_BUY', 'CROSS_BUY', 'MIXED', 'STYLE_CONCENTRATED'];
const CATEGORIES = ['', 'Large', 'Mid', 'Small'];

const TAG_BADGE: Record<SignalTag, string> = {
  STRONG_CROSS_BUY: 'text-success bg-success-bg border-success-border',
  CROSS_BUY: 'text-cyan-400 bg-cyan-500/10 border-cyan-500/30',
  MIXED: 'text-warning bg-warning-bg border-warning-border',
  STYLE_CONCENTRATED: 'text-text-tertiary bg-white/5 border-white/10',
};

export default function CrossBuyScannerView() {
  const [data, setData] = useState<ScannerResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [months, setMonths] = useState<string[]>([]);
  const [monthsReady, setMonthsReady] = useState(false);
  const [month, setMonth] = useState('');
  const [limit, setLimit] = useState(500);
  const [minRatio, setMinRatio] = useState(0);
  const [signalTag, setSignalTag] = useState('');
  const [stockCategory, setStockCategory] = useState('');
  const [minTotalFunds, setMinTotalFunds] = useState(0);
  const [sortKey, setSortKey] = useState<SortKey>('cross_buy_ratio');
  const [sortAsc, setSortAsc] = useState(false);
  const queryParamsRef = useRef<Record<string, unknown> | null>(null);

  const buildQueryParams = useCallback(() => {
    const p = new URLSearchParams();
    if (month) p.set('month', month);
    p.set('limit', String(limit));
    if (minRatio > 0) p.set('min_cross_buy_ratio', String(minRatio));
    if (signalTag) p.set('signal_tag', signalTag);
    if (stockCategory) p.set('stock_category', stockCategory);
    if (minTotalFunds > 0) p.set('min_total_funds', String(minTotalFunds));
    return p;
  }, [month, limit, minRatio, signalTag, stockCategory, minTotalFunds]);

  const fetchData = useCallback(() => {
    setLoading(true); setError(null);
    const p = buildQueryParams();
    const queryParams = Object.fromEntries(p.entries());
    fetch(`${API_BASE}/cross-buy/scanner?${p}`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        queryParamsRef.current = { ...queryParams };
        return r.json();
      })
      .then(setData).catch(e => setError(e.message)).finally(() => setLoading(false));
  }, [buildQueryParams]);

  useEffect(() => {
    fetch(`${API_BASE}/cross-buy/months`).then(r => r.json())
      .then(d => {
        const ms = d.months || [];
        setMonths(ms);
        if (ms.length) setMonth(ms[0]); else setMonthsReady(true);
      })
      .catch(() => setMonthsReady(true));
  }, []);
  useEffect(() => { if (month || monthsReady) fetchData(); }, [month, monthsReady, fetchData]);

  const toggleSort = (k: SortKey) => {
    if (sortKey === k) setSortAsc(!sortAsc); else { setSortKey(k); setSortAsc(false); }
  };

  const columns: TableColumn<CrossBuyStock>[] = [
    { 
      key: 'symbol', 
      label: 'Symbol', 
      align: 'left',
      render: (item) => (
        <a 
          href={`/#/chart?symbol=${item.symbol}`} 
          target="_blank" 
          rel="noopener noreferrer" 
          className="font-bold text-white hover:text-accent-indigo transition-colors"
        >
          {item.symbol}
        </a>
      )
    },
    { key: 'sector', label: 'Sector', align: 'left' },
    { 
      key: 'market_cap', 
      label: 'MCap', 
      align: 'right',
      render: (item) => <FormatCurrency value={item.market_cap} />
    },
    { key: 'stock_category', label: 'Cat.', align: 'left' },
    { 
      key: 'total_funds', 
      label: 'Funds', 
      align: 'right',
      cellClassName: 'font-semibold text-text-primary'
    },
    { 
      key: 'large_funds', 
      label: 'Large', 
      align: 'right',
      cellClassName: 'text-purple-400'
    },
    { 
      key: 'mid_funds', 
      label: 'Mid', 
      align: 'right',
      cellClassName: 'text-blue-400'
    },
    { 
      key: 'small_funds', 
      label: 'Small', 
      align: 'right',
      cellClassName: 'text-yellow-400'
    },
    { 
      key: 'multi_funds', 
      label: 'Multi', 
      align: 'right',
      cellClassName: 'text-cyan-400'
    },
    { 
      key: 'other_funds', 
      label: 'Other', 
      align: 'right',
      cellClassName: 'text-text-tertiary'
    },
    { 
      key: 'cross_buy_ratio', 
      label: 'Cross-Buy', 
      align: 'right',
      render: (item) => <MiniBarCell value={item.cross_buy_ratio} colorClass="bg-accent-indigo/60" />
    },
    { 
      key: 'signal_tag', 
      label: 'Signal Tag', 
      align: 'left',
      render: (item) => (
        <SignalBadge 
          signal={item.signal_tag} 
          mapping={TAG_BADGE} 
        />
      )
    },
  ];

  const sortState: SortState<CrossBuyStock> = { sortKey, sortAsc };

  const summary = useMemo(() => {
    const stocks = data?.stocks ?? [];
    const n = stocks.length;
    const avgRatio = n ? stocks.reduce((s, x) => s + (x.cross_buy_ratio ?? 0), 0) / n : 0;
    const tagCounts: Record<string, number> = {};
    for (const t of stocks.map(s => s.signal_tag || 'UNKNOWN')) tagCounts[t] = (tagCounts[t] || 0) + 1;
    return { n, avgRatio, tagCounts };
  }, [data]);

  const handleCSV = () => {
    if (!data?.stocks.length) return;
    const h = ['Symbol', 'Sector', 'MCap Cr', 'Stock Category', 'Total Funds', 'Large', 'Mid', 'Small', 'Multi', 'Other', 'Cross-Buy Ratio', 'Signal Tag'];
    const rows = data.stocks.map(s => [s.symbol, s.sector || '', s.market_cap ? (s.market_cap / 1e7).toFixed(0) : '',
      s.stock_category || '', s.total_funds, s.large_funds, s.mid_funds, s.small_funds, s.multi_funds, s.other_funds,
      s.cross_buy_ratio != null ? (Number(s.cross_buy_ratio) * 100).toFixed(2) : '', s.signal_tag || '']);
    const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const exportedAt = new Date().toISOString();
    const params = queryParamsRef.current ?? Object.fromEntries(buildQueryParams().entries());
    const csv = formatScannerCsv([h, ...rows].map(r => r.map(esc).join(',')).join('\n'), {
      scanner: 'cross-buy',
      scanned_date: '',
      params,
      exported_at: exportedAt,
    });
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url;
    a.download = `cross_buy_${month || 'latest'}_${exportedAt.slice(0, 10)}.csv`; a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between mb-4 shrink-0">
        <div className="flex items-center gap-3">
          <Link to="/mission-control" className="text-text-secondary hover:text-white transition-colors"><ArrowLeft size={18} /></Link>
          <div>
            <h1 className="text-lg font-bold text-white flex items-center gap-2">
              <Handshake size={18} className="text-accent-indigo" /> Cross-Buy Scanner
            </h1>
            <p className="text-xs text-text-secondary">Funds with different mandates accumulating the same stock — cross-style institutional conviction</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={handleCSV} disabled={!data?.stocks.length}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-text-secondary transition-colors disabled:opacity-40">
            <Download size={12} /> CSV
          </button>
          <button onClick={fetchData} disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-text-secondary transition-colors disabled:opacity-40">
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>
        </div>
      </div>

      {/* Filters bar */}
      <div className="flex items-center gap-3 mb-4 shrink-0 flex-wrap">
        <select value={month} onChange={e => setMonth(e.target.value)}
          className="px-2 py-1 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-xs text-white">
          {months.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <select value={limit} onChange={e => setLimit(Number(e.target.value))}
          className="px-2 py-1 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-xs text-white">
          {[25, 50, 100, 200, 500].map(n => <option key={n} value={n}>Top {n}</option>)}
        </select>
        <span className="text-border-default">|</span>
        <div className="flex items-center gap-1">
          <label className="text-[10px] text-text-secondary">Ratio≥</label>
          <input type="number" min={0} max={1} step={0.05} value={minRatio || ''}
            onChange={e => setMinRatio(Number(e.target.value) || 0)}
            className="w-16 px-1.5 py-0.5 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-xs text-white text-right focus:outline-none focus:ring-1 focus:ring-indigo-500/50" />
        </div>
        <select value={signalTag} onChange={e => setSignalTag(e.target.value)}
          className="px-2 py-1 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-xs text-white">
          <option value="">All Tags</option>
          {TAGS.filter(t => t).map(t => <option key={t} value={t}>{t}</option>)}
        </select>
        <select value={stockCategory} onChange={e => setStockCategory(e.target.value)}
          className="px-2 py-1 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-xs text-white">
          <option value="">All Caps</option>
          {CATEGORIES.filter(c => c).map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <div className="flex items-center gap-1">
          <label className="text-[10px] text-text-secondary">Funds≥</label>
          <input type="number" min={0} value={minTotalFunds || ''}
            onChange={e => setMinTotalFunds(Number(e.target.value) || 0)}
            className="w-14 px-1.5 py-0.5 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-xs text-white text-right focus:outline-none focus:ring-1 focus:ring-indigo-500/50" />
        </div>
        <button onClick={() => { setMinRatio(0); setSignalTag(''); setStockCategory(''); setMinTotalFunds(0); }}
          className="px-2 py-1 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-error transition-colors">Reset</button>
      </div>

      {/* Summary strip */}
      {!loading && !error && data && (
        <div className="flex items-center gap-3 mb-3 px-3 py-2 bg-[#ffffff05] border border-[#ffffff0a] rounded text-xs shrink-0 flex-wrap">
          <span className="text-text-secondary"><span className="text-white font-semibold">{summary.n}</span> stocks</span>
          <span className="text-border-default">|</span>
          <span className="text-accent-indigo">Avg Ratio: <b>{(summary.avgRatio * 100).toFixed(2)}%</b></span>
          {(Object.keys(summary.tagCounts).length > 0) && <>
            <span className="text-border-default">|</span>
            {'STRONG_CROSS_BUY MIXED CROSS_BUY STYLE_CONCENTRATED UNKNOWN'.split(' ').filter(t => summary.tagCounts[t]).map((t, i, arr) => (
              <span key={t}>
                <span className={
                  t === 'STRONG_CROSS_BUY' ? 'text-success' :
                  t === 'MIXED' ? 'text-warning' :
                  t === 'CROSS_BUY' ? 'text-cyan-400' : 'text-text-tertiary'
                }>{t.replace(/_/g, ' ')}: <b>{summary.tagCounts[t]}</b></span>
                {i < arr.length - 1 && <span className="text-border-default ml-3">|</span>}
              </span>
            ))}
          </>}
        </div>
      )}

      {error && <div className="bg-red-950/40 border border-red-500/50 rounded p-3 text-error text-sm mb-3">{error}</div>}

      {/* Table */}
      {data && (
        <VirtualizedTable<CrossBuyStock>
          data={data.stocks}
          columns={columns}
          sortState={sortState}
          onSort={(k) => k && toggleSort(k)}
          loading={loading}
          emptyMessage="No stocks match the current filters."
          rowKey={(item) => item.symbol}
          enableHover={true}
          enableStripes={true}
          containerHeight={500}
          estimatedRowHeight={40}
          overscan={5}
        />
      )}

      {!loading && !error && data && data.stocks.length === 0 && (
        <div className="flex items-center justify-center h-48 text-text-tertiary flex-col gap-2">
          No stocks match the current filters.
          <button onClick={() => { setMinRatio(0); setSignalTag(''); setStockCategory(''); setMinTotalFunds(0); }}
            className="text-xs text-accent-indigo hover:text-accent-indigo/80">Clear all filters</button>
        </div>
      )}
    </div>
  );
}
