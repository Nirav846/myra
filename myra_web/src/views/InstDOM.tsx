import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Librarian } from '../lib/Librarian';
import { AlignRight, Activity, TrendingUp, Loader2, Star } from 'lucide-react';
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine, Legend } from 'recharts';
import { SymbolSearch } from '../components/SymbolSearch';
import { useWatchlist } from '../lib/WatchlistContext';
import { StarButton } from '../components/StarButton';

export default function InstDOMView({ lib }: { lib: Librarian }) {
  const { isWatched } = useWatchlist();
  const [watchlistOnly, setWatchlistOnly] = useState(false);
  const [data, setData] = useState<any[]>([]);
  const [ticker, setTicker] = useState('RELIANCE');
  const [lookbackDays, setLookbackDays] = useState(30);
  const [isLoading, setIsLoading] = useState(false);
  const [isDemo, setIsDemo] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [totals, setTotals] = useState<{ delivery: number; intraday: number; delPct: number } | null>(null);
  const [latestClose, setLatestClose] = useState<number | null>(null);
  const fetchRef = useRef<{ active: boolean } | null>(null);

  const fetchProfile = useCallback(async () => {
    if (fetchRef.current) fetchRef.current.active = false;
    const run = { active: true };
    fetchRef.current = run;
    setIsLoading(true);
    setErrorMsg(null);
    setIsDemo(false);
    try {
      const closeResult = await lib.executeQuery('_tech_conn',
        'SELECT close FROM technical_data WHERE symbol = ? ORDER BY date DESC LIMIT 1',
        [ticker], 5000
      );
      if (!run.active) return;
      const refClose = closeResult && closeResult.length > 0 ? Number(closeResult[0].close) : null;
      setLatestClose(refClose);

      let dp: number;
      if (refClose && refClose >= 5000) dp = -2;
      else if (refClose && refClose >= 100) dp = -1;
      else dp = 0;

      const query = `
        SELECT 
          ROUND(COALESCE(vwap, (high + low + close) / 3), ?) as price_level, 
          SUM(delivery) as delivery,
          (SUM(volume) - SUM(delivery)) as intraday
        FROM technical_data
        WHERE symbol = ? AND date >= date('now', ?)
        GROUP BY price_level 
        ORDER BY price_level DESC
      `;
      const result = await lib.executeQuery('_tech_conn', query, [dp, ticker, `-${lookbackDays} days`], 30000);
      if (!run.active) return;

      if (result && result.length > 0) {
        const mapped = result.map((r: any) => ({
          price: r.price_level,
          delivery: Number(r.delivery || 0),
          intraday: Math.max(0, Number(r.intraday || 0))
        }));
        setData(mapped);
        const totDel = mapped.reduce((s: number, r: any) => s + r.delivery, 0);
        const totInt = mapped.reduce((s: number, r: any) => s + r.intraday, 0);
        setTotals({ delivery: totDel, intraday: totInt, delPct: totDel + totInt > 0 ? (totDel / (totDel + totInt)) * 100 : 0 });
      } else {
        setData([]);
        setTotals(null);
        if (!lib.isConnectedToLocalRepo) generateMockProfile();
        if (lib.isConnectedToLocalRepo) setErrorMsg(`No data found for ${ticker} in selected range.`);
      }
    } catch (e: any) {
      if (!run.active) return;
      console.error(e);
      setErrorMsg(e.message || "Query failed. Local sidecar may be offline.");
      if (!lib.isConnectedToLocalRepo) generateMockProfile();
      else setData([]);
    } finally {
      if (run.active) setIsLoading(false);
    }
  }, [ticker, lookbackDays, lib]);

  useEffect(() => {
    fetchProfile();
  }, [fetchProfile]);

  const generateMockProfile = () => {
    setIsDemo(true);
    const basePrice = Math.random() > 0.5 ? Math.floor(Math.random() * 400 + 50) : Math.floor(Math.random() * 4500 + 500);
    const step = basePrice >= 5000 ? 100 : basePrice >= 100 ? 10 : 1;
    const mock = [];
    for (let i = 15; i >= -15; i--) {
      const price = Math.round(basePrice / step) * step + (i * step);
      const weight = Math.exp(-(i * i) / 100);
      const totalVol = Math.floor(Math.random() * 200000 + (1000000 * weight));
      const delivery_pct = (i >= -2 && i <= 2) ? 0.65 : (Math.random() * 0.2 + 0.1);
      mock.push({ price, delivery: Math.floor(totalVol * delivery_pct), intraday: totalVol - Math.floor(totalVol * delivery_pct) });
    }
    setData(mock);
    const totDel = mock.reduce((s, r) => s + r.delivery, 0);
    const totInt = mock.reduce((s, r) => s + r.intraday, 0);
    setTotals({ delivery: totDel, intraday: totInt, delPct: totDel + totInt > 0 ? (totDel / (totDel + totInt)) * 100 : 0 });
  };

  const poc = useMemo(() => {
    if (data.length === 0) return null;
    let maxVol = 0;
    let pocPrice = 0;
    for (const d of data) {
      const total = d.delivery + d.intraday;
      if (total > maxVol) { maxVol = total; pocPrice = d.price; }
    }
    return pocPrice;
  }, [data]);

  const cmpPrice = useMemo(() => {
    if (latestClose === null) return null;
    const step = data.length > 1 ? Math.abs(data[0].price - data[1].price) : 1;
    return Math.round(latestClose / step) * step;
  }, [latestClose, data]);

  return (
    <div className="bg-[#1e2028] border border-[#ffffff1a] rounded flex flex-col shadow-xl min-h-[600px]">
      <div className="px-6 py-4 border-b border-[#ffffff1a] flex justify-between items-center bg-[#1a1c24]">
        <h3 className="font-medium text-lg flex items-center gap-2">
          <AlignRight size={20} className="text-orange-400" />
          Delivery Volume Profile
        </h3>
        <span className="text-xs text-[#888] font-mono">Module: _tech_conn.profile</span>
      </div>

      <div className="p-6 flex flex-col gap-6">
        {/* Controls */}
        <div className="flex flex-wrap gap-4 items-center bg-[#0e1117] p-3 rounded-lg border border-[#ffffff0a]">
          <div className="flex items-center gap-3">
            <label className="text-xs font-mono text-[#888] uppercase">Target Ticker</label>
            <div className="w-40">
              <SymbolSearch
                lib={lib}
                initialValue={ticker}
                onSymbolSelect={(sym) => setTicker(sym)}
                placeholder="Ticker..."
              />
            </div>
          </div>
          
          <div className="flex items-center gap-3 border-l border-[#ffffff1a] pl-4">
            <label className="text-xs font-mono text-[#888] uppercase">Lookback Range</label>
            <select 
              value={lookbackDays} 
              onChange={e => setLookbackDays(Number(e.target.value))}
              className="w-32 bg-[#1a1c24] border border-[#ffffff1a] rounded px-3 py-1.5 text-sm focus:outline-none focus:border-blue-500 font-mono text-[#fafafa]"
            >
              <option className="bg-[#1a1c24] text-[#fafafa]" value={7}>Last 7 Days</option>
              <option className="bg-[#1a1c24] text-[#fafafa]" value={30}>Last 30 Days</option>
              <option className="bg-[#1a1c24] text-[#fafafa]" value={90}>Last 90 Days</option>
              <option className="bg-[#1a1c24] text-[#fafafa]" value={180}>Last 180 Days</option>
              <option className="bg-[#1a1c24] text-[#fafafa]" value={365}>1 Year / YTD</option>
            </select>
          </div>
          
           <div className="ml-auto flex items-center gap-3">
              {totals && !isLoading && (
                <span className="text-[10px] font-mono text-[#888] hidden md:flex items-center gap-2 border-r border-[#ffffff1a] pr-3">
                  <TrendingUp size={11} className="text-green-400" />
                  Del: {(totals.delPct).toFixed(1)}%
                </span>
              )}
              {errorMsg && <span className="text-[10px] text-red-400 font-mono px-2 py-1 bg-red-500/10 rounded border border-red-500/20">{errorMsg}</span>}
              {isDemo && <span className="text-[10px] text-yellow-500 font-mono px-2 py-1 bg-yellow-500/10 rounded border border-yellow-500/20">⚠️ DEMO DATA</span>}
             {isLoading && <span className="text-[10px] text-blue-400 font-mono animate-pulse">Calculating DOM...</span>}
            <button
              onClick={() => setWatchlistOnly(o => !o)}
              className={`flex items-center gap-1.5 px-2 py-1.5 rounded border text-[11px] font-mono transition-colors ${
                watchlistOnly
                  ? 'bg-yellow-500/20 border-yellow-500/40 text-yellow-400'
                  : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#888] hover:text-yellow-400'
              }`}
            >
              <Star size={11} fill={watchlistOnly ? 'currentColor' : 'none'} />
              Watchlist
            </button>
             <span className="text-[10px] text-fuchsia-400 font-mono hidden md:flex items-center gap-2" title="Shows price levels where institutional delivery volume is concentrated relative to total volume traded. High delivery % at a price level indicates strong-hand accumulation.">
              <Activity size={12}/> Delivery Profile (?)
            </span>
          </div>
        </div>

        {/* DOM Profile Chart */}
        <div className="flex-1 bg-[#0e1117] rounded-lg border border-[#ffffff0a] p-4 h-[500px]">
          {isLoading && (
            <div className="h-full flex flex-col items-center justify-center gap-3">
              <Loader2 size={24} className="animate-spin text-orange-500/50" />
              <span className="text-xs font-mono text-[#666] animate-pulse">Loading volume profile...</span>
            </div>
          )}
          {!isLoading && data.length === 0 && (
            <div className="h-full flex items-center justify-center text-sm font-mono text-[#666]">
              {errorMsg || `No delivery data for ${ticker} in selected range.`}
            </div>
          )}
          {!isLoading && data.length > 0 && (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data} layout="vertical" margin={{ top: 5, right: 40, left: 20, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#222" horizontal={false} />
                <XAxis type="number" stroke="#666" tick={{ fill: '#666', fontSize: 10 }} tickFormatter={(v: number) => v >= 1_000_000 ? (v / 1_000_000).toFixed(1) + 'M' : v >= 1000 ? (v / 1000).toFixed(0) + 'k' : String(v)} />
                <YAxis
                  type="category"
                  dataKey="price"
                  stroke="#ccc"
                  tick={{ fill: '#ccc', fontSize: 11, fontWeight: 'bold' }}
                  width={80}
                />
                <Tooltip
                  cursor={{ fill: '#ffffff0a' }}
                  contentStyle={{ backgroundColor: '#1a1c24', border: '1px solid #333', borderRadius: '4px', fontSize: '12px' }}
                  formatter={(value: number, name: string, props: any) => {
                    if (!props.payload) return [value.toLocaleString(), name];
                    const total = props.payload.delivery + props.payload.intraday;
                    const delPct = total > 0 ? ((props.payload.delivery / total) * 100).toFixed(1) : '0.0';
                    if (name === 'Institutional Delivery') {
                      return [`${value.toLocaleString()} (${delPct}%)`, 'Institutional Delivery'];
                    }
                    return [value.toLocaleString(), 'Intraday / Speculation'];
                  }}
                />
                {poc !== null && <ReferenceLine y={poc} stroke="#8884d8" strokeDasharray="6 4" label={{ value: 'POC', position: 'right', fill: '#8884d8', fontSize: 10 }} />}
                {cmpPrice !== null && <ReferenceLine y={cmpPrice} stroke="#f97316" strokeDasharray="6 4" label={{ value: 'CMP', position: 'right', fill: '#f97316', fontSize: 10 }} />}
                <Legend wrapperStyle={{ fontSize: '11px', color: '#888' }} />
                <Bar dataKey="delivery" stackId="a" fill="#10b981" name="Institutional Delivery" />
                <Bar dataKey="intraday" stackId="a" fill="#333333" name="Intraday / Speculation" />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    </div>
  );
}
