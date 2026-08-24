import { useState, useEffect, useCallback, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft, RefreshCw, Download, ArrowUpDown, Loader2, Handshake } from 'lucide-react';
import { API_BASE } from '../config';

interface CrossBuyStock {
  symbol: string; month: string;
  total_funds: number | null; large_funds: number | null; mid_funds: number | null;
  small_funds: number | null; multi_funds: number | null; other_funds: number | null;
  cross_buy_ratio: number | null; signal_tag: string | null;
  stock_category: string | null; market_cap: number | null; sector: string | null;
}
interface ScannerResponse { month: string | null; stocks: CrossBuyStock[]; total: number; }
type SortKey = 'symbol' | 'sector' | 'stock_category' | 'total_funds' | 'large_funds' | 'mid_funds'
  | 'small_funds' | 'multi_funds' | 'other_funds' | 'cross_buy_ratio' | 'market_cap';
type SignalTag = 'STRONG_CROSS_BUY' | 'CROSS_BUY' | 'MIXED' | 'STYLE_CONCENTRATED';

const TAGS: (SignalTag | '')[] = ['', 'STRONG_CROSS_BUY', 'CROSS_BUY', 'MIXED', 'STYLE_CONCENTRATED'];
const CATEGORIES = ['', 'Large', 'Mid', 'Small'];

const TAG_BADGE: Record<SignalTag, string> = {
  STRONG_CROSS_BUY: 'text-green-400 bg-green-500/10 border-green-500/30',
  CROSS_BUY: 'text-cyan-400 bg-cyan-500/10 border-cyan-500/30',
  MIXED: 'text-amber-400 bg-amber-500/10 border-amber-500/30',
  STYLE_CONCENTRATED: 'text-[#888] bg-white/5 border-white/10',
};

const DASH = '\u2014';
function fmtInt(v: number | null | undefined) { return v == null ? DASH : Number(v).toLocaleString(); }
function fmtMcap(v: number | null | undefined) {
  if (v == null) return DASH;
  const cr = Number(v) / 1e7;
  return `\u20B9${cr >= 1e5 ? `${(cr / 1e3).toFixed(0)}K` : cr.toFixed(0)} Cr`;
}
function TagBadge({ tag }: { tag: string | null }) {
  if (!tag) return <span className="text-[#555]">{DASH}</span>;
  const cls = TAG_BADGE[tag as SignalTag] ?? 'text-[#888] bg-white/5 border-white/10';
  return <span className={`inline-block px-1.5 py-0.5 rounded border text-[10px] font-semibold whitespace-nowrap ${cls}`}>{tag}</span>;
}
function RatioCell({ v }: { v: number | null }) {
  if (v == null) return <span className="text-[#555]">{DASH}</span>;
  const pct = Number(v) * 100;
  return (
    <span className="flex items-center gap-1.5 justify-end">
      <span className="w-10 h-1 rounded-full bg-white/10 overflow-hidden shrink-0">
        <span className="block h-full bg-indigo-400/60 rounded-full" style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} />
      </span>
      <span className={pct >= 50 ? 'text-green-400 font-semibold' : pct > 0 ? 'text-yellow-400' : 'text-[#ccc]'}>{pct.toFixed(2)}%</span>
    </span>
  );
}

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

  const fetchData = useCallback(() => {
    setLoading(true); setError(null);
    const p = new URLSearchParams();
    if (month) p.set('month', month);
    p.set('limit', String(limit));
    if (minRatio > 0) p.set('min_cross_buy_ratio', String(minRatio));
    if (signalTag) p.set('signal_tag', signalTag);
    if (stockCategory) p.set('stock_category', stockCategory);
    if (minTotalFunds > 0) p.set('min_total_funds', String(minTotalFunds));
    fetch(`${API_BASE}/cross-buy/scanner?${p}`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(setData).catch(e => setError(e.message)).finally(() => setLoading(false));
  }, [month, limit, minRatio, signalTag, stockCategory, minTotalFunds]);

  // Months bootstrap: on success pick newest month (data fetch fires via month change);
  // on empty list OR fetch failure set monthsReady so the initial fetch still runs exactly once.
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
  const sortIcon = (k: SortKey) =>
    sortKey !== k ? <ArrowUpDown size={10} className="text-[#555]" /> :
      <ArrowUpDown size={10} className={sortAsc ? 'text-green-400' : 'text-indigo-400'} />;

  const sorted = useMemo(() => {
    if (!data) return [];
    return [...data.stocks].sort((a, b) => {
      if (sortKey === 'symbol') return sortAsc ? a.symbol.localeCompare(b.symbol) : b.symbol.localeCompare(a.symbol);
      if (sortKey === 'sector') return sortAsc ? (a.sector || '').localeCompare(b.sector || '') : (b.sector || '').localeCompare(a.sector || '');
      if (sortKey === 'stock_category') return sortAsc ? (a.stock_category || '').localeCompare(b.stock_category || '') : (b.stock_category || '').localeCompare(a.stock_category || '');
      const va = a[sortKey] ?? -Infinity, vb = b[sortKey] ?? -Infinity;
      return sortAsc ? va - vb : vb - va;
    });
  }, [data, sortKey, sortAsc]);

  const summary = useMemo(() => {
    const stocks = data?.stocks ?? [];
    const n = stocks.length;
    const avgRatio = n ? stocks.reduce((s, x) => s + (x.cross_buy_ratio ?? 0), 0) / n : 0;
    const tagCounts: Record<string, number> = {};
    for (const t of stocks.map(s => s.signal_tag || 'UNKNOWN')) tagCounts[t] = (tagCounts[t] || 0) + 1;
    return { n, avgRatio, tagCounts };
  }, [data]);

  const handleCSV = () => {
    if (!sorted.length) return;
    const h = ['Symbol', 'Sector', 'MCap Cr', 'Stock Category', 'Total Funds', 'Large', 'Mid', 'Small', 'Multi', 'Other', 'Cross-Buy Ratio', 'Signal Tag'];
    const rows = sorted.map(s => [s.symbol, s.sector || '', s.market_cap ? (s.market_cap / 1e7).toFixed(0) : '',
      s.stock_category || '', s.total_funds, s.large_funds, s.mid_funds, s.small_funds, s.multi_funds, s.other_funds,
      s.cross_buy_ratio != null ? (Number(s.cross_buy_ratio) * 100).toFixed(2) : '', s.signal_tag || '']);
    // RFC 4180: wrap every cell in double quotes; escape embedded quotes by doubling them.
    const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const csv = [h, ...rows].map(r => r.map(esc).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url;
    a.download = `cross_buy_${month || 'latest'}_${new Date().toISOString().slice(0, 10)}.csv`; a.click();
    URL.revokeObjectURL(url);
  };

  const Th = ({ k, label, align }: { k: SortKey | null; label: string; align: string }) => (
    <th className={`${align} px-2 py-1.5 ${k ? 'cursor-pointer hover:text-white' : ''} transition-colors`}
      onClick={() => k && toggleSort(k)}>
      <span className={`flex items-center gap-1 ${align === 'text-right' ? 'justify-end' : ''}`}>
        {label} {k && sortIcon(k)}
      </span>
    </th>
  );

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between mb-3 shrink-0">
        <div className="flex items-center gap-3">
          <Link to="/mission-control" className="text-[#888] hover:text-white transition-colors"><ArrowLeft size={18} /></Link>
          <div>
            <h1 className="text-lg font-bold text-white flex items-center gap-2">
              <Handshake size={18} className="text-indigo-400" /> Cross-Buy Scanner
            </h1>
            <p className="text-xs text-[#888]">Funds with different mandates accumulating the same stock &mdash; cross-style institutional conviction</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={handleCSV} disabled={!sorted.length}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-[#ccc] transition-colors disabled:opacity-40">
            <Download size={12} /> CSV
          </button>
          <button onClick={fetchData} disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-[#ccc] transition-colors disabled:opacity-40">
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>
        </div>
      </div>

      {/* Filters bar */}
      <div className="flex items-center gap-3 mb-3 shrink-0 flex-wrap">
        <select value={month} onChange={e => setMonth(e.target.value)}
          className="px-2 py-1 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-xs text-white">
          {months.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <select value={limit} onChange={e => setLimit(Number(e.target.value))}
          className="px-2 py-1 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-xs text-white">
          {[25, 50, 100, 200, 500].map(n => <option key={n} value={n}>Top {n}</option>)}
        </select>
        <span className="text-[#333]">|</span>
        <div className="flex items-center gap-1">
          <label className="text-[10px] text-[#888]">Ratio&ge;</label>
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
          <label className="text-[10px] text-[#888]">Funds&ge;</label>
          <input type="number" min={0} value={minTotalFunds || ''}
            onChange={e => setMinTotalFunds(Number(e.target.value) || 0)}
            className="w-14 px-1.5 py-0.5 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-xs text-white text-right focus:outline-none focus:ring-1 focus:ring-indigo-500/50" />
        </div>
        <button onClick={() => { setMinRatio(0); setSignalTag(''); setStockCategory(''); setMinTotalFunds(0); }}
          className="px-2 py-1 bg-[#ffffff0a] hover:bg-[#ffffff15] border border-[#ffffff1a] rounded text-xs text-red-400 transition-colors">Reset</button>
      </div>

      {/* Summary strip */}
      {!loading && !error && data && (
        <div className="flex items-center gap-3 mb-2 px-3 py-2 bg-[#ffffff05] border border-[#ffffff0a] rounded text-xs shrink-0 flex-wrap">
          <span className="text-[#888]"><span className="text-white font-semibold">{summary.n}</span> stocks</span>
          <span className="text-[#333]">|</span>
          <span className="text-indigo-400">Avg Ratio: <b>{(summary.avgRatio * 100).toFixed(2)}%</b></span>
          {(Object.keys(summary.tagCounts).length > 0) && <>
            <span className="text-[#333]">|</span>
            {'STRONG_CROSS_BUY MIXED CROSS_BUY STYLE_CONCENTRATED UNKNOWN'.split(' ').filter(t => summary.tagCounts[t]).map((t, i, arr) => (
              <span key={t}>
                <span className={
                  t === 'STRONG_CROSS_BUY' ? 'text-green-400' :
                  t === 'MIXED' ? 'text-amber-400' :
                  t === 'CROSS_BUY' ? 'text-cyan-400' : 'text-[#888]'
                }>{t.replace(/_/g, ' ')}: <b>{summary.tagCounts[t]}</b></span>
                {i < arr.length - 1 && <span className="text-[#333] ml-3">|</span>}
              </span>
            ))}
          </>}
        </div>
      )}

      {loading && <div className="flex items-center justify-center h-48 gap-2 text-[#888]"><Loader2 size={20} className="animate-spin" /> Loading...</div>}
      {error && <div className="bg-red-950/40 border border-red-500/50 rounded p-3 text-red-400 text-sm mb-2">{error}</div>}

      {/* Table */}
      {!loading && !error && sorted.length > 0 && (
        <div className="flex-1 overflow-auto">
          <table className="w-full text-xs font-mono">
            <thead className="sticky top-0 bg-[#0e1117] z-10">
              <tr className="border-b border-[#ffffff1a] text-[#888] uppercase tracking-wider">
                <Th k="symbol" label="Symbol" align="text-left" />
                <Th k="sector" label="Sector" align="text-left" />
                <Th k="market_cap" label="MCap" align="text-right" />
                <Th k="stock_category" label="Cat." align="text-left" />
                <Th k="total_funds" label="Funds" align="text-right" />
                <Th k="large_funds" label="Large" align="text-right" />
                <Th k="mid_funds" label="Mid" align="text-right" />
                <Th k="small_funds" label="Small" align="text-right" />
                <Th k="multi_funds" label="Multi" align="text-right" />
                <Th k="other_funds" label="Other" align="text-right" />
                <Th k="cross_buy_ratio" label="Cross-Buy" align="text-right" />
                <Th k={null} label="Signal Tag" align="text-left" />
              </tr>
            </thead>
            <tbody>
              {sorted.map(s => (
                <tr key={s.symbol} className="border-b border-[#ffffff0a] hover:bg-[#ffffff05]">
                  <td className="px-2 py-1.5 font-bold text-white">
                    <a href={`/#/chart?symbol=${s.symbol}`} target="_blank" rel="noopener noreferrer" className="hover:text-indigo-400 transition-colors">{s.symbol}</a>
                  </td>
                  <td className="px-2 py-1.5 text-[#ccc]">{s.sector || DASH}</td>
                  <td className="px-2 py-1.5 text-right text-[#888] whitespace-nowrap">{fmtMcap(s.market_cap)}</td>
                  <td className="px-2 py-1.5 text-[#ccc]">{s.stock_category || DASH}</td>
                  <td className="px-2 py-1.5 text-right font-semibold text-white">{fmtInt(s.total_funds)}</td>
                  <td className="px-2 py-1.5 text-right text-purple-400">{fmtInt(s.large_funds)}</td>
                  <td className="px-2 py-1.5 text-right text-blue-400">{fmtInt(s.mid_funds)}</td>
                  <td className="px-2 py-1.5 text-right text-yellow-400">{fmtInt(s.small_funds)}</td>
                  <td className="px-2 py-1.5 text-right text-cyan-400">{fmtInt(s.multi_funds)}</td>
                  <td className="px-2 py-1.5 text-right text-[#888]">{fmtInt(s.other_funds)}</td>
                  <td className="px-2 py-1.5 text-right"><RatioCell v={s.cross_buy_ratio} /></td>
                  <td className="px-2 py-1.5"><TagBadge tag={s.signal_tag} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!loading && !error && data && sorted.length === 0 && (
        <div className="flex items-center justify-center h-48 text-[#555] flex-col gap-2">
          No stocks match the current filters.
          <button onClick={() => { setMinRatio(0); setSignalTag(''); setStockCategory(''); setMinTotalFunds(0); }}
            className="text-xs text-indigo-400 hover:text-indigo-300">Clear all filters</button>
        </div>
      )}
    </div>
  );
}
