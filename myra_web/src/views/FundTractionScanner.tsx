import { useState, useEffect, useCallback, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft, RefreshCw, Download, ArrowUpDown, Filter, TrendingUp, TrendingDown, Minus, Loader2, BarChart3 } from 'lucide-react';
import { API_BASE } from '../config';

interface Stock {
  symbol: string; month: string; traction_score: number | null;
  fund_count: number | null; adds_new: number | null; reduces_closes: number | null;
  sma_30: number | null; month_end_close: number | null; close_latest: number | null;
  pct_vs_sma: number | null; market_cap: number | null; sector: string | null;
  roe: number | null; net_margin: number | null; pe: number | null;
  promoter_holding_pct: number | null; free_float_pct: number | null;
  quality_score: number | null;
}
interface Summary {
  avg_score: number; avg_fund_count: number; total_adds: number; total_reduces: number;
  top_sectors: { sector: string; count: number }[];
  cap_distribution: { small: number; mid: number; large: number; unknown: number };
}
interface ScannerResponse { month: string | null; stocks: Stock[]; total: number; summary: Summary; }
type SortKey = 'traction_score' | 'pct_vs_sma' | 'fund_count' | 'adds_new' | 'reduces_closes' | 'market_cap' | 'quality_score' | 'roe' | 'sector' | 'symbol';

const SECTORS = ['Healthcare','Financial Services','IT','Consumer Goods','Industrials','Energy','Chemicals','Automobile','Metals','Real Estate','Telecom','Power','Infrastructure','Media','Textiles'];

function fmt(v: number | null, d = 1) { return v == null ? '\u2014' : v.toFixed(d); }
function fmtInt(v: number | null) { return v == null ? '\u2014' : v.toLocaleString(); }
function fmtMcap(v: number | null) {
  if (v == null) return '\u2014';
  const cr = v / 1e7;
  return cr >= 1e5 ? `${(cr/1e3).toFixed(0)}K Cr` : `${cr.toFixed(0)} Cr`;
}
function SmaBadge({ pct }: { pct: number | null }) {
  if (pct == null) return <span className="text-[#555]">\u2014</span>;
  const c = pct > 5 ? 'text-green-400' : pct >= 0 ? 'text-yellow-400' : 'text-red-400';
  const I = pct > 0 ? TrendingUp : pct < 0 ? TrendingDown : Minus;
  return <span className={`flex items-center gap-1 justify-end ${c}`}><I size={11} />{pct >= 0 ? '+' : ''}{pct.toFixed(2)}%</span>;
}
function ScoreBadge({ score }: { score: number | null }) {
  if (score == null) return <span className="text-[#555]">\u2014</span>;
  const c = score >= 200 ? 'text-green-400' : score >= 100 ? 'text-yellow-400' : 'text-[#ccc]';
  return <span className={`font-semibold ${c}`}>{fmt(score)}</span>;
}

export default function FundTractionScannerView() {
  const [data, setData] = useState<ScannerResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [months, setMonths] = useState<string[]>([]);
  const [month, setMonth] = useState('');
  const [limit, setLimit] = useState(100);
  const [minScore, setMinScore] = useState(0);
  const [minFunds, setMinFunds] = useState(0);
  const [minAdds, setMinAdds] = useState(0);
  const [selectedSectors, setSelectedSectors] = useState<string[]>([]);
  const [mcapMin, setMcapMin] = useState(0);
  const [mcapMax, setMcapMax] = useState(0);
  const [minRoe, setMinRoe] = useState(0);
  const [minMargin, setMinMargin] = useState(0);
  const [showQuality, setShowQuality] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>('traction_score');
  const [sortAsc, setSortAsc] = useState(false);

  const fetchData = useCallback(() => {
    setLoading(true); setError(null);
    const p = new URLSearchParams();
    if (month) p.set('month', month);
    p.set('limit', String(limit));
    if (minScore > 0) p.set('min_score', String(minScore));
    if (minFunds > 0) p.set('min_fund_count', String(minFunds));
    if (minAdds > 0) p.set('min_add_count', String(minAdds));
    if (selectedSectors.length > 0) p.set('sector', selectedSectors.join(','));
    if (mcapMin > 0) p.set('market_cap_min', String(mcapMin * 1e7));
    if (mcapMax > 0) p.set('market_cap_max', String(mcapMax * 1e7));
    if (minRoe > 0) p.set('min_roe', String(minRoe));
    if (minMargin > 0) p.set('min_net_margin', String(minMargin));
    fetch(`${API_BASE}/fund-traction/scanner?${p}`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(setData).catch(e => setError(e.message)).finally(() => setLoading(false));
  }, [month, limit, minScore, minFunds, minAdds, selectedSectors, mcapMin, mcapMax, minRoe, minMargin]);

  useEffect(() => {
    fetch(`${API_BASE}/fund-traction/months`).then(r => r.json())
      .then(d => { setMonths(d.months || []); if (d.months?.length) setMonth(d.months[0]); })
      .catch(() => {});
  }, []);
  useEffect(() => { if (month) fetchData(); }, [month, fetchData]);

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
      if (sortKey === 'sector') return sortAsc ? (a.sector||'').localeCompare(b.sector||'') : (b.sector||'').localeCompare(a.sector||'');
      const va = (a as any)[sortKey] ?? -Infinity, vb = (b as any)[sortKey] ?? -Infinity;
      return sortAsc ? va - vb : vb - va;
    });
  }, [data, sortKey, sortAsc]);

  const toggleSector = (s: string) =>
    setSelectedSectors(prev => prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s]);

  const handleCSV = () => {
    if (!sorted.length) return;
    const h = ['Symbol','Score','vs SMA%','Funds','Adds','Reduces','Close','SMA30','MCap Cr','Sector','ROE','Net Margin','PE','Quality'];
    const rows = sorted.map(s => [s.symbol, s.traction_score, s.pct_vs_sma, s.fund_count, s.adds_new,
      s.reduces_closes, s.close_latest, s.sma_30, s.market_cap ? (s.market_cap/1e7).toFixed(0) : '',
      s.sector||'', s.roe, s.net_margin, s.pe, s.quality_score]);
    const csv = [h, ...rows].map(r => r.join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = `fund_traction_${month||'latest'}.csv`; a.click();
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
              <BarChart3 size={18} className="text-indigo-400" /> Fund Traction Scanner
            </h1>
            <p className="text-xs text-[#888]">{data ? `${data.total} stocks \u00b7 Month: ${data.month}` : 'Loading...'}</p>
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

      {/* Controls */}
      <div className="flex items-center gap-3 mb-3 shrink-0 flex-wrap">
        <select value={month} onChange={e => setMonth(e.target.value)}
          className="px-2 py-1 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-xs text-white">
          {months.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <select value={limit} onChange={e => setLimit(Number(e.target.value))}
          className="px-2 py-1 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-xs text-white">
          {[25,50,100,200,500].map(n => <option key={n} value={n}>Top {n}</option>)}
        </select>
        <span className="text-[#333]">|</span>
        <div className="flex items-center gap-1">
          <label className="text-[10px] text-[#888]">Score&ge;</label>
          <input type="number" value={minScore||''} onChange={e => setMinScore(Number(e.target.value)||0)}
            className="w-16 px-1.5 py-0.5 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-xs text-white text-right focus:outline-none focus:ring-1 focus:ring-indigo-500/50" />
        </div>
        <div className="flex items-center gap-1">
          <label className="text-[10px] text-[#888]">Funds&ge;</label>
          <input type="number" value={minFunds||''} onChange={e => setMinFunds(Number(e.target.value)||0)}
            className="w-14 px-1.5 py-0.5 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-xs text-white text-right focus:outline-none focus:ring-1 focus:ring-indigo-500/50" />
        </div>
        <div className="flex items-center gap-1">
          <label className="text-[10px] text-[#888]">Adds&ge;</label>
          <input type="number" value={minAdds||''} onChange={e => setMinAdds(Number(e.target.value)||0)}
            className="w-14 px-1.5 py-0.5 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-xs text-white text-right focus:outline-none focus:ring-1 focus:ring-indigo-500/50" />
        </div>
        <span className="text-[#333]">|</span>
        <div className="flex items-center gap-1">
          <label className="text-[10px] text-[#888]">MCap Cr</label>
          <input type="number" value={mcapMin||''} onChange={e => setMcapMin(Number(e.target.value)||0)}
            placeholder="min" className="w-16 px-1.5 py-0.5 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-xs text-white text-right focus:outline-none focus:ring-1 focus:ring-indigo-500/50" />
          <span className="text-[#555]">&ndash;</span>
          <input type="number" value={mcapMax||''} onChange={e => setMcapMax(Number(e.target.value)||0)}
            placeholder="max" className="w-16 px-1.5 py-0.5 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-xs text-white text-right focus:outline-none focus:ring-1 focus:ring-indigo-500/50" />
        </div>
        <span className="text-[#333]">|</span>
        <div className="flex items-center gap-1">
          <label className="text-[10px] text-[#888]">ROE&ge;</label>
          <input type="number" value={minRoe||''} onChange={e => setMinRoe(Number(e.target.value)||0)}
            className="w-14 px-1.5 py-0.5 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-xs text-white text-right focus:outline-none focus:ring-1 focus:ring-indigo-500/50" />
        </div>
        <div className="flex items-center gap-1">
          <label className="text-[10px] text-[#888]">Margin&ge;</label>
          <input type="number" value={minMargin||''} onChange={e => setMinMargin(Number(e.target.value)||0)}
            className="w-14 px-1.5 py-0.5 bg-[#ffffff0a] border border-[#ffffff1a] rounded text-xs text-white text-right focus:outline-none focus:ring-1 focus:ring-indigo-500/50" />
        </div>
        <button onClick={() => setShowQuality(!showQuality)}
          className={`px-2 py-0.5 rounded text-[10px] transition-colors ${showQuality ? 'bg-indigo-600 text-white' : 'bg-[#ffffff0a] text-[#888] hover:bg-[#ffffff15]'}`}>
          Quality
        </button>
        <button onClick={fetchData} className="px-3 py-1 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-xs transition-colors">Apply</button>
      </div>

      {/* Sector chips */}
      <div className="flex items-center gap-1 mb-2 shrink-0 flex-wrap">
        <Filter size={10} className="text-[#888]" />
        {SECTORS.map(s => (
          <button key={s} onClick={() => toggleSector(s)}
            className={`px-1.5 py-0.5 rounded text-[10px] transition-colors ${selectedSectors.includes(s) ? 'bg-indigo-600 text-white' : 'bg-[#ffffff0a] text-[#888] hover:bg-[#ffffff15] hover:text-white'}`}>
            {s}
          </button>
        ))}
        {selectedSectors.length > 0 && <button onClick={() => setSelectedSectors([])} className="text-[10px] text-red-400 hover:text-red-300 ml-1">Clear</button>}
      </div>

      {/* Summary */}
      {data?.summary && (
        <div className="flex items-center gap-3 mb-2 px-3 py-2 bg-[#ffffff05] border border-[#ffffff0a] rounded text-xs shrink-0 flex-wrap">
          <span className="text-[#888]"><span className="text-white font-semibold">{data.total}</span> stocks</span>
          <span className="text-[#333]">|</span>
          <span className="text-indigo-400">Avg Score: <b>{data.summary.avg_score}</b></span>
          <span className="text-[#333]">|</span>
          <span className="text-cyan-400">Avg Funds: <b>{data.summary.avg_fund_count}</b></span>
          <span className="text-[#333]">|</span>
          <span className="text-green-400">Adds: <b>{data.summary.total_adds}</b></span>
          <span className="text-red-400 ml-1">Reduces: <b>{data.summary.total_reduces}</b></span>
          <span className="text-[#333]">|</span>
          <span className="text-[#888]">
            S:<span className="text-blue-400">{data.summary.cap_distribution.small}</span>{' '}
            M:<span className="text-yellow-400">{data.summary.cap_distribution.mid}</span>{' '}
            L:<span className="text-purple-400">{data.summary.cap_distribution.large}</span>
          </span>
          {data.summary.top_sectors.length > 0 && <>
            <span className="text-[#333]">|</span>
            <span className="text-[#888]">Top: {data.summary.top_sectors.slice(0,3).map(s => `${s.sector}(${s.count})`).join(', ')}</span>
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
                <Th k="traction_score" label="Score" align="text-right" />
                <Th k="pct_vs_sma" label="vs SMA" align="text-right" />
                <Th k="fund_count" label="Funds" align="text-right" />
                <Th k="adds_new" label="Adds" align="text-right" />
                <Th k="reduces_closes" label="Reduces" align="text-right" />
                <Th k={null} label="Close" align="text-right" />
                <Th k="market_cap" label="MCap" align="text-right" />
                <Th k="sector" label="Sector" align="text-left" />
                <Th k="roe" label="ROE" align="text-right" />
                <Th k={null} label="N.Mgn" align="text-right" />
                {showQuality && <Th k="quality_score" label="Quality" align="text-right" />}
              </tr>
            </thead>
            <tbody>
              {sorted.map(s => (
                <tr key={s.symbol} className="border-b border-[#ffffff0a] hover:bg-[#ffffff05]">
                  <td className="px-2 py-1.5 font-bold text-white">
                    <a href={`/chart?symbol=${s.symbol}`} target="_blank" rel="noopener noreferrer" className="hover:text-indigo-400 transition-colors">{s.symbol}</a>
                  </td>
                  <td className="px-2 py-1.5 text-right"><ScoreBadge score={s.traction_score} /></td>
                  <td className="px-2 py-1.5 text-right"><SmaBadge pct={s.pct_vs_sma} /></td>
                  <td className="px-2 py-1.5 text-right text-cyan-400">{fmtInt(s.fund_count)}</td>
                  <td className="px-2 py-1.5 text-right text-green-400">{fmtInt(s.adds_new)}</td>
                  <td className="px-2 py-1.5 text-right text-red-400">{fmtInt(s.reduces_closes)}</td>
                  <td className="px-2 py-1.5 text-right text-[#ccc]">{fmt(s.close_latest, 2)}</td>
                  <td className="px-2 py-1.5 text-right text-[#888]">{fmtMcap(s.market_cap)}</td>
                  <td className="px-2 py-1.5 text-[#ccc]">{s.sector || '\u2014'}</td>
                  <td className="px-2 py-1.5 text-right text-[#ccc]">{fmt(s.roe)}</td>
                  <td className="px-2 py-1.5 text-right text-[#ccc]">{fmt(s.net_margin)}</td>
                  {showQuality && <td className="px-2 py-1.5 text-right text-indigo-400 font-semibold">{fmt(s.quality_score)}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!loading && !error && data && sorted.length === 0 && (
        <div className="flex items-center justify-center h-48 text-[#555] flex-col gap-2">
          No stocks match the current filters.
          <button onClick={() => { setMinScore(0); setMinFunds(0); setMinAdds(0); setSelectedSectors([]); setMcapMin(0); setMcapMax(0); setMinRoe(0); setMinMargin(0); }}
            className="text-xs text-indigo-400 hover:text-indigo-300">Clear all filters</button>
        </div>
      )}
    </div>
  );
}
