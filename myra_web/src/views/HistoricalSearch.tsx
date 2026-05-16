import { useState, useMemo, useEffect } from 'react';
import { Librarian } from '../lib/Librarian';
import { Search, Calendar, Activity, BarChart2, Table as TableIcon, Package, TrendingUp, BarChart, Download, AlertTriangle, Info } from 'lucide-react';
import { ResponsiveContainer, ComposedChart, Line, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from 'recharts';
import { SymbolSearch } from '../components/SymbolSearch';
import { motion, AnimatePresence } from 'motion/react';
import { useSettings } from '../lib/SettingsContext';

interface HistoricalDataRow {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  delivery_qty: number;
  delivery_pct: number;
  non_delivery: number;
}

export default function HistoricalSearchView({ lib }: { lib: Librarian }) {
  const { settings } = useSettings();
  const [ticker, setTicker] = useState('RELIANCE');
  const [startDate, setStartDate] = useState(new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString().split('T')[0]); // 30 days ago
  const [endDate, setEndDate] = useState(new Date().toISOString().split('T')[0]);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<HistoricalDataRow[] | null>(null);
  const [viewMode, setViewMode] = useState<'chart' | 'table'>('chart');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isDemo, setIsDemo] = useState(false);
  const [fundaData, setFundaData] = useState<any>(null);
  const [showFunda, setShowFunda] = useState(false);
  const [fundaLoading, setFundaLoading] = useState(false);
  const [sentimentData, setSentimentData] = useState<any>(null);

  const [pledgeData, setPledgeData] = useState<any>(null);

  const fetchFunda = async (symbol: string) => {
    setFundaLoading(true);
    try {
      const [fundaRes, sentRes, pledgeRes] = await Promise.all([
        fetch(`http://localhost:8000/api/fundamentals/live/${symbol}`),
        fetch(`http://localhost:8000/api/finstack/social-sentiment/${symbol}`).catch(() => null),
        fetch(`http://localhost:8000/api/finstack/pledge-alert/${symbol}`).catch(() => null)
      ]);
      
      if (fundaRes.ok) {
        const json = await fundaRes.json();
        setFundaData(json);
      }
      
      if (sentRes && sentRes.ok) {
        const sentJson = await sentRes.json();
        setSentimentData(sentJson);
      }

      if (pledgeRes && pledgeRes.ok) {
        const pledgeJson = await pledgeRes.json();
        setPledgeData(pledgeJson);
      }
    } catch (e) {
      console.warn('Fundamental fetch failed', e);
    } finally {
      setFundaLoading(false);
    }
  };

  const handleSearch = async () => {
    if (!ticker) return;
    setLoading(true);
    setErrorMsg(null);
    setFundaData(null);
    setIsDemo(!lib.isConnectedToLocalRepo);
    
    try {
      if (!lib.isConnectedToLocalRepo || settings.mockDataMode) {
        if (!lib.isConnectedToLocalRepo) setErrorMsg('Database unavailable. Using mock data.');
        setIsDemo(true);
        generateMockData();
        return;
      }

      setIsDemo(false);
      // SECURE SQL: Using positional placeholders (?) and spreading parameters into args array
      const query = `
        SELECT date, open, high, low, close, volume, delivery, (delivery * 100.0 / NULLIF(volume, 0)) as delivery_pct
        FROM technical_data
        WHERE symbol = ? AND date >= ? AND date <= ?
        ORDER BY date ASC
      `;
      const result = await lib.executeQuery('_tech_conn', query, [ticker, startDate, endDate]);

      if (result && result.length > 0) {
        const mapped: HistoricalDataRow[] = result.map((r: any) => ({
            date: r.date || r.Date || '',
            open: Number(r.open || r.Open || 0),
            high: Number(r.high || r.High || 0),
            low: Number(r.low || r.Low || 0),
            close: Number(r.close || r.Close || 0),
            volume: Number(r.volume || r.Volume || 0),
            delivery_qty: Number(r.delivery || 0), 
            delivery_pct: Number(r.delivery_pct || 0),
            // Historical rows may have delivery > volume due to reporting artifacts; clamp to zero for visual correctness.
            non_delivery: Math.max(0, Number(r.volume || 0) - Number(r.delivery || 0))
        }));
        setData(mapped);
      } else {
        setData([]);
      }
    } catch (e: any) {
      console.error(e);
      setErrorMsg(e.message || 'Database unavailable');
      setIsDemo(true);
      generateMockData();
    } finally {
      setLoading(false);
    }
  };

  const generateMockData = () => {
    let currentPrice = 2400;
    const mock: HistoricalDataRow[] = [];
    const start = new Date(startDate);
    const end = new Date(endDate);
    
    for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
      if (d.getDay() === 0 || d.getDay() === 6) continue; // Skip weekends
      
      const volatility = currentPrice * 0.02;
      const open = currentPrice + (Math.random() - 0.5) * volatility;
      const close = open + (Math.random() - 0.5) * volatility;
      const high = Math.max(open, close) + Math.random() * volatility * 0.5;
      const low = Math.min(open, close) - Math.random() * volatility * 0.5;
      
      const volume = Math.floor(Math.random() * 5000000) + 1000000;
      const delivery_pct = Number((Math.random() * 60 + 15).toFixed(2)); 
      const delivery_qty = Math.floor(volume * (delivery_pct / 100));
      const non_delivery = volume - delivery_qty;
      
      mock.push({
        date: d.toISOString().split('T')[0],
        close: Number(close.toFixed(2)),
        open: Number(open.toFixed(2)),
        high: Number(high.toFixed(2)),
        low: Number(low.toFixed(2)),
        volume,
        delivery_qty,
        delivery_pct,
        non_delivery
      });
      currentPrice = close;
    }
    setData(mock);
  };

  const exportCSV = () => {
    if (!data) return;
    const headers = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume', 'DeliveryQty', 'DeliveryPct'];
    const rows = data.map(r => [r.date, r.open, r.high, r.low, r.close, r.volume, r.delivery_qty, r.delivery_pct]);
    const csvContent = [headers, ...rows].map(e => e.join(",")).join("\n");
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", `historical_${ticker}_${new Date().toISOString().split('T')[0]}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const summaryStats = useMemo(() => {
    if (!data || data.length === 0) return null;
    const totalVol = data.reduce((acc, curr) => acc + curr.volume, 0);
    const totalDel = data.reduce((acc, curr) => acc + curr.delivery_qty, 0);
    const avgDelPct = totalVol > 0 ? ((totalDel / totalVol) * 100).toFixed(2) : '0.00';
    const highestDel = [...data].sort((a,b) => b.delivery_pct - a.delivery_pct)[0];
    const avgDailyVol = Math.floor(totalVol / data.length);
    
    return { totalVol, totalDel, avgDelPct, highestDel, avgDailyVol };
  }, [data]);

  return (
    <div className="bg-[#1e2028] border border-[#ffffff1a] rounded flex flex-col shadow-xl overflow-hidden min-h-[600px]">
      {/* Dynamic Demo Banner */}
      <AnimatePresence>
        {isDemo && (
          <motion.div 
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="bg-yellow-500/10 border-b border-yellow-500/20 px-6 py-2 flex items-center justify-between overflow-hidden"
          >
            <div className="flex items-center gap-2 text-yellow-500 text-xs font-medium italic">
              <AlertTriangle size={14} />
              <span>ENVIRONMENT: SIMULATED / DEMO MODE ACTIVE</span>
            </div>
            <span className="text-[10px] text-yellow-500/60 uppercase tracking-widest font-mono">Mock Data Provided by Librarian Fallback</span>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="px-6 py-4 border-b border-[#ffffff1a] flex justify-between items-center bg-[#1a1c24]">
        <h3 className="font-medium text-lg flex items-center gap-2">
          <Activity size={20} className="text-blue-400" />
          Historical Engine & Delivery Scrutiny
        </h3>
        <div className="flex items-center gap-3">
          {errorMsg && <span className="text-xs text-red-400 font-mono px-2 py-1 bg-red-400/10 rounded">{errorMsg}</span>}
          <span className="text-xs text-[#888] font-mono">Module: query_layer.delivery</span>
        </div>
      </div>

      <div className="p-6 flex flex-col gap-6">
        {/* Search Controls */}
        <div className="flex flex-wrap gap-4 items-end bg-[#0e1117] p-4 rounded-lg border border-[#ffffff0a]">
          <div className="flex-1 min-w-[200px]">
            <label className="block text-xs font-mono text-[#888] uppercase mb-1">Ticker Symbol</label>
            <SymbolSearch 
              lib={lib}
              initialValue={ticker}
              onSymbolSelect={setTicker}
              placeholder="e.g. RELIANCE"
            />
          </div>
          
          <div className="w-40">
            <label className="block text-xs font-mono text-[#888] uppercase mb-1">Start Date</label>
            <div className="relative">
              <Calendar size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#555]" />
              <input 
                type="date" 
                value={startDate}
                onChange={e => setStartDate(e.target.value)}
                className="w-full bg-[#1a1c24] border border-[#ffffff1a] rounded pl-9 pr-3 py-2 text-sm focus:outline-none focus:border-blue-500 font-mono text-[#fafafa] [color-scheme:dark]"
              />
            </div>
          </div>

          <div className="w-40">
            <label className="block text-xs font-mono text-[#888] uppercase mb-1">End Date</label>
            <div className="relative">
              <Calendar size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#555]" />
              <input 
                type="date" 
                value={endDate}
                onChange={e => setEndDate(e.target.value)}
                className="w-full bg-[#1a1c24] border border-[#ffffff1a] rounded pl-9 pr-3 py-2 text-sm focus:outline-none focus:border-blue-500 font-mono text-[#fafafa] [color-scheme:dark]"
              />
            </div>
          </div>

          <button 
            onClick={handleSearch}
            disabled={loading}
            className="px-6 py-2 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded text-sm transition-colors disabled:opacity-50 h-[38px] flex items-center justify-center min-w-[120px]"
          >
            {loading ? <Search size={16} className="animate-pulse" /> : 'Fetch Series'}
          </button>
        </div>

        {/* Loading Skeletons */}
        {loading && (
          <div className="space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {[1, 2, 3].map(i => (
                <div key={i} className="h-24 bg-[#ffffff05] border border-[#ffffff0a] rounded animate-pulse border-dashed" />
              ))}
            </div>
            <div className="h-[400px] bg-[#ffffff05] border border-[#ffffff0a] rounded animate-pulse border-dashed flex items-center justify-center">
              <div className="text-[#333] font-mono text-xs uppercase tracking-tighter">Initializing Scrutiny Layer...</div>
            </div>
          </div>
        )}

        {/* Dashboard Highlight Cards */}
        {!loading && data && summaryStats && (
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="bg-[#1a1c24] border border-green-500/30 rounded p-4 flex flex-col justify-center relative overflow-hidden group">
                <div className="absolute -right-4 -top-4 opacity-5 group-hover:opacity-10 transition-opacity"><Package size={100} /></div>
                <span className="text-[#888] text-xs font-mono mb-1 flex items-center gap-1.5"><Package size={12}/> AGGREGATE DELIVERY %</span>
                <span className="text-2xl font-bold text-green-400">{summaryStats.avgDelPct}%</span>
                <span className="text-[10px] text-[#666] mt-1 font-mono">Total Qty: {summaryStats.totalDel.toLocaleString()}</span>
              </div>
              <div className="bg-[#1a1c24] border border-fuchsia-500/30 rounded p-4 flex flex-col justify-center relative overflow-hidden group">
                <div className="absolute -right-4 -top-4 opacity-5 group-hover:opacity-10 transition-opacity"><TrendingUp size={100} /></div>
                <span className="text-[#888] text-xs font-mono mb-1 flex items-center gap-1.5"><TrendingUp size={12}/> HIGH DELIVERY SPIKE</span>
                <span className="text-2xl font-bold text-fuchsia-400">{summaryStats.highestDel.delivery_pct}%</span>
                <span className="text-[10px] text-[#666] mt-1 font-mono">Recorded on: {summaryStats.highestDel.date}</span>
              </div>
              <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-4 flex flex-col justify-center relative overflow-hidden group">
                <div className="absolute -right-4 -top-4 opacity-5 group-hover:opacity-10 transition-opacity"><BarChart size={100} /></div>
                <span className="text-[#888] text-xs font-mono mb-1 flex items-center gap-1.5"><Activity size={12}/> AVERAGE DAILY VOLUME</span>
                <span className="text-2xl font-bold text-[#fafafa]">{summaryStats.avgDailyVol.toLocaleString()}</span>
                <span className="text-[10px] text-[#666] mt-1 font-mono">Across {data.length} trading days</span>
              </div>
            </div>

            {data && data.length > 0 && (
              <div className="border border-[#ffffff0a] rounded-lg bg-[#0e1117]">
                <button
                  onClick={() => {
                    const willOpen = !showFunda;
                    setShowFunda(willOpen);
                    if (willOpen) fetchFunda(ticker);
                  }}
                  className="w-full px-4 py-3 flex items-center justify-between text-sm font-mono text-[#ccc] hover:text-white"
                >
                  <span>📊 Fundamental Snapshot</span>
                  <span className="text-[10px] text-[#666]">{showFunda ? '▲' : '▼'}</span>
                </button>
                {showFunda && (
                  <div className="px-4 pb-4">
                    {fundaLoading ? (
                      <div className="text-xs text-[#666] font-mono animate-pulse py-4 text-center">
                        Fetching live fundamentals...
                      </div>
                    ) : fundaData ? (
                      <div className="space-y-4">
                        {/* Source badge */}
                        <div className="flex justify-end">
                          <span className={`text-[9px] px-2 py-0.5 rounded-full font-mono ${
                            fundaData.source === 'live'
                              ? 'bg-green-500/10 text-green-400 border border-green-500/20'
                              : 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/20'
                          }`}>
                            {fundaData.source === 'live' ? '● Live' : '◌ DB'}
                          </span>
                        </div>

                        {/* Valuation Row */}
                        <div>
                          <h4 className="text-[10px] font-mono text-[#666] uppercase tracking-wider mb-2">Valuation</h4>
                          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                            <MetricCard label="PE Ratio" value={fundaData.fundamentals?.pe || fundaData.key_metrics?.pe} />
                            <MetricCard label="PB Ratio" value={fundaData.fundamentals?.pb} />
                            <MetricCard label="Market Cap" value={fundaData.key_metrics?.market_cap || fundaData.fundamentals?.market_cap} isString />
                            <MetricCard label="Face Value" value={fundaData.key_metrics?.face_value} isString />
                          </div>
                        </div>

                        {/* Profitability Row */}
                        <div>
                          <h4 className="text-[10px] font-mono text-[#666] uppercase tracking-wider mb-2">Profitability</h4>
                          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                            <MetricCard label="ROE" value={fundaData.fundamentals?.roe || fundaData.key_metrics?.roe} suffix="%" />
                            <MetricCard label="ROCE" value={fundaData.key_metrics?.roce} suffix="%" />
                            <MetricCard label="Net Margin" value={fundaData.fundamentals?.net_margin ? fundaData.fundamentals.net_margin * 100 : null} suffix="%" />
                            <MetricCard label="Op Margin" value={fundaData.fundamentals?.operating_margin ? fundaData.fundamentals.operating_margin * 100 : null} suffix="%" />
                          </div>
                        </div>

                        {/* Shareholding Row */}
                        {fundaData.shareholding && (
                          <div>
                            <h4 className="text-[10px] font-mono text-[#666] uppercase tracking-wider mb-2">
                              Shareholding
                              {fundaData.shareholding.period_end && (
                                <span className="ml-2 text-[#555]">as of {fundaData.shareholding.period_end}</span>
                              )}
                            </h4>
                            <div className="grid grid-cols-2 md:grid-cols-6 gap-2">
                              <MetricCard label="Promoter" value={fundaData.shareholding.promoter_pct} suffix="%" />
                              <MetricCard label="FII" value={fundaData.shareholding.fii_pct} suffix="%" />
                              <MetricCard label="DII" value={fundaData.shareholding.dii_pct} suffix="%" />
                              <MetricCard label="Public" value={fundaData.shareholding.public_pct} suffix="%" />
                              <MetricCard label="Govt" value={fundaData.shareholding.government_pct} suffix="%" />
                              <MetricCard label="Pledged %" value={pledgeData?.pledge_pct} suffix="%" tooltip="Promoter shares pledged as collateral. Rising pledge is a corporate governance red flag." color />
                            </div>
                          </div>
                        )}

                        {/* Financial Health Row */}
                        <div>
                          <h4 className="text-[10px] font-mono text-[#666] uppercase tracking-wider mb-2">Financial Health</h4>
                          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                            <MetricCard label="Debt/Equity" value={fundaData.fundamentals?.debt_equity} />
                            <MetricCard label="Current Ratio" value={fundaData.fundamentals?.current_ratio} />
                            <MetricCard label="Quick Ratio" value={fundaData.fundamentals?.quick_ratio} />
                            <MetricCard label="FCF Yield" value={fundaData.fundamentals?.free_cash_flow_yield ? fundaData.fundamentals.free_cash_flow_yield * 100 : null} suffix="%" />
                          </div>
                        </div>

                        {/* Growth Row */}
                        <div>
                          <h4 className="text-[10px] font-mono text-[#666] uppercase tracking-wider mb-2">Growth</h4>
                          <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                            <MetricCard label="Revenue Growth" value={fundaData.fundamentals?.revenue_growth ? fundaData.fundamentals.revenue_growth * 100 : null} suffix="%" color />
                            <MetricCard label="Earnings Growth" value={fundaData.fundamentals?.earnings_growth ? fundaData.fundamentals.earnings_growth * 100 : null} suffix="%" color />
                            <MetricCard label="Div Yield" value={fundaData.fundamentals?.dividend_yield} suffix="%" />
                          </div>
                        </div>

                        {/* Social Sentiment Row */}
                        {sentimentData && (
                          <div>
                            <h4 className="text-[10px] font-mono text-[#666] uppercase tracking-wider mb-2">Social Sentiment</h4>
                            <div className="bg-[#1a1c24] border border-[#ffffff0a] p-3 rounded">
                              <div className="flex items-center gap-4">
                                <div className="text-[10px] text-[#888] font-mono uppercase w-32 shrink-0">Score: {sentimentData.score}</div>
                                <div className="flex-1 bg-[#ffffff0a] h-1.5 rounded relative overflow-hidden flex items-center">
                                  <div className="absolute left-1/2 top-0 bottom-0 w-px bg-[#444] z-10"></div>
                                  {sentimentData.score > 0 ? (
                                    <div className="absolute left-1/2 top-0 bottom-0 bg-green-500/80" style={{ width: `${Math.min(sentimentData.score / 2, 50)}%` }}></div>
                                  ) : (
                                    <div className="absolute right-1/2 top-0 bottom-0 bg-red-500/80" style={{ width: `${Math.min(Math.abs(sentimentData.score) / 2, 50)}%` }}></div>
                                  )}
                                </div>
                                <div className={`text-xs font-bold font-mono w-24 text-right ${sentimentData.overall_sentiment === 'BULLISH' ? 'text-green-400' : sentimentData.overall_sentiment === 'BEARISH' ? 'text-red-400' : 'text-gray-400'}`}>
                                  {sentimentData.overall_sentiment}
                                </div>
                              </div>
                              {sentimentData.sources && (
                                <div className="mt-3 pt-2 border-t border-[#ffffff0a] text-[9px] font-mono text-[#666] flex flex-wrap gap-3">
                                  {Object.entries(sentimentData.sources).map(([src, details]: [string, any]) => (
                                    <span key={src}>
                                      {src.replace('_', ' ').toUpperCase()}: {details.mentions || 0} mentions
                                    </span>
                                  ))}
                                </div>
                              )}
                            </div>
                          </div>
                        )}

                        {/* Pros/Cons */}
                        {fundaData.pros_cons && (fundaData.pros_cons.pros.length > 0 || fundaData.pros_cons.cons.length > 0) && (
                          <div>
                            <h4 className="text-[10px] font-mono text-[#666] uppercase tracking-wider mb-2">screener.in Insights</h4>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                              <div className="bg-[#0a1a0a] border border-green-500/20 rounded p-2">
                                <p className="text-[10px] text-green-400 font-mono mb-1">PROS</p>
                                {fundaData.pros_cons.pros.length > 0
                                  ? fundaData.pros_cons.pros.map((p: string, i: number) => (
                                      <p key={i} className="text-[11px] text-green-300/80 leading-relaxed">• {p}</p>
                                    ))
                                  : <p className="text-[11px] text-[#666]">None listed</p>
                                }
                              </div>
                              <div className="bg-[#1a0a0a] border border-red-500/20 rounded p-2">
                                <p className="text-[10px] text-red-400 font-mono mb-1">CONS</p>
                                {fundaData.pros_cons.cons.length > 0
                                  ? fundaData.pros_cons.cons.map((c: string, i: number) => (
                                      <p key={i} className="text-[11px] text-red-300/80 leading-relaxed">• {c}</p>
                                    ))
                                  : <p className="text-[11px] text-[#666]">None listed</p>
                                }
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="text-xs text-[#666] font-mono py-4 text-center">
                        No fundamental data available.
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* View Toggle & Content */}
        {!loading && data && (
          <div className="flex-1 flex flex-col border border-[#ffffff0a] rounded-lg overflow-hidden bg-[#0e1117]">
            <div className="flex justify-between items-center bg-[#1a1c24] border-b border-[#ffffff0a] p-2 pr-4">
              <div className="flex gap-2">
                <button 
                  onClick={() => setViewMode('chart')}
                  className={`flex items-center gap-2 px-3 py-1.5 rounded text-xs font-medium transition-colors ${viewMode === 'chart' ? 'bg-[#ffffff1a] text-white' : 'text-[#888] hover:text-[#ccc]'}`}
                >
                  <BarChart2 size={14} /> OHLCV + Delivery Tracking
                </button>
                <button 
                  onClick={() => setViewMode('table')}
                  className={`flex items-center gap-2 px-3 py-1.5 rounded text-xs font-medium transition-colors ${viewMode === 'table' ? 'bg-[#ffffff1a] text-white' : 'text-[#888] hover:text-[#ccc]'}`}
                >
                  <TableIcon size={14} /> Raw Vector Grid
                </button>
              </div>
              <button 
                onClick={exportCSV}
                className="flex items-center gap-2 px-3 py-1.5 rounded text-xs font-bold bg-[#ffffff0a] hover:bg-[#ffffff15] text-[#ccc] transition-all active:scale-95"
              >
                <Download size={14} /> Export CSV
              </button>
            </div>

            <div className="p-4 flex-1 min-h-[400px]">
              {viewMode === 'chart' ? (
                <div className="h-full w-full relative">
                  <ResponsiveContainer width="100%" height={400}>
                    <ComposedChart data={data} margin={{ top: 10, right: 30, left: -20, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#222" vertical={false} />
                      <XAxis dataKey="date" stroke="#666" tick={{ fill: '#666', fontSize: 10 }} tickMargin={10} minTickGap={30} />
                      
                      {/* Left axis for price */}
                      <YAxis yAxisId="price" domain={['auto', 'auto']} stroke="#666" tick={{ fill: '#666', fontSize: 10 }} />
                      
                      {/* Internal axis for volume scale */}
                      <YAxis yAxisId="volume" orientation="right" hide={true} />
                      
                      {/* Visible right axis for delivery percentage */}
                      <YAxis 
                        yAxisId="pct" 
                        orientation="right" 
                        domain={[0, 100]} 
                        stroke="#f43f5e" 
                        tick={{ fill: '#f43f5e', fontSize: 10 }} 
                        label={{ value: 'Delivery %', angle: 90, position: 'insideRight', style: { fill: '#f43f5e', fontSize: 10, fontWeight: 'bold' } }}
                      />
                      
                      <Tooltip 
                        contentStyle={{ backgroundColor: '#1a1c24', border: '1px solid #333', borderRadius: '4px', fontSize: '12px' }}
                        itemStyle={{ color: '#ccc' }}
                        formatter={(value: any, name: string) => {
                          if (name === "Delivery %") return [`${value}%`, name];
                          if (name === "Delivery Qty" || name === "Intraday Qty") return [value.toLocaleString(), name];
                          return [value, name];
                        }}
                      />
                      <Legend wrapperStyle={{ fontSize: '11px', color: '#888' }} />
                      
                      {/* Stacked Bars for Volume Decomposition */}
                      <Bar yAxisId="volume" dataKey="delivery_qty" stackId="vol" fill="#10b981" opacity={0.6} name="Delivery Qty" />
                      <Bar yAxisId="volume" dataKey="non_delivery" stackId="vol" fill="#333" opacity={0.4} name="Intraday Qty" />
                      
                      {/* Line for Price */}
                      <Line yAxisId="price" type="monotone" dataKey="close" stroke="#3b82f6" strokeWidth={2} dot={false} name="Closing Price (₹)" />
                      
                      {/* Delivery Percentage Tracking Line (Visible Axis) */}
                      <Line yAxisId="pct" type="monotone" dataKey="delivery_pct" stroke="#f43f5e" strokeWidth={1} dot={false} name="Delivery %" />
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <div className="overflow-auto max-h-[400px] relative">
                  <table className="w-full text-left font-mono text-xs">
                    <thead className="sticky top-0 bg-[#0e1117] z-10">
                      <tr className="text-[#888] border-b border-[#ffffff1a]">
                        <th className="pb-2 px-2 font-medium uppercase">Date</th>
                        <th className="pb-2 px-2 font-medium uppercase text-right">Close</th>
                        <th className="pb-2 px-2 font-medium uppercase text-right">Volume</th>
                        <th className="pb-2 px-2 font-medium uppercase text-right text-green-400">Delivery QTY</th>
                        <th className="pb-2 px-4 font-medium uppercase text-right text-fuchsia-400">Del (%)</th>
                      </tr>
                    </thead>
                    <tbody className="text-[#ccc]">
                      {data.map((row, idx) => (
                        <tr key={idx} className="border-b border-[#ffffff0a] hover:bg-[#ffffff05]">
                          <td className="py-2 px-2 text-[#888]">{row.date}</td>
                          <td className="py-2 px-2 text-right">{row.close.toFixed(2)}</td>
                          <td className="py-2 px-2 text-right text-[#666]">{row.volume.toLocaleString()}</td>
                          <td className="py-2 px-2 text-right text-green-400 font-medium">{row.delivery_qty.toLocaleString()}</td>
                          <td className="py-2 px-4 text-right">
                             <span className={`px-2 py-0.5 rounded ${row.delivery_pct > 50 ? 'bg-fuchsia-500/20 text-fuchsia-400 font-bold' : row.delivery_pct < 30 ? 'bg-red-500/10 text-red-500' : 'text-[#888]'}`}>
                                {row.delivery_pct}%
                             </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}

        {!data && !loading && (
          <div className="flex-1 border border-[#ffffff0a] rounded flex items-center justify-center flex-col text-[#666] bg-[#0e1117] min-h-[300px]">
            <Search size={32} className="mb-3 opacity-20" />
            <p className="text-sm font-mono">Enter a ticker and date range to query the technical sidecar.</p>
          </div>
        )}
      </div>
    </div>
  );
}

function MetricCard({ label, value, suffix = '', isString = false, color = false, tooltip }: {
  label: string;
  value: number | string | null | undefined;
  suffix?: string;
  isString?: boolean;
  color?: boolean;
  tooltip?: string;
}) {
  if (value === null || value === undefined || value === '') {
    return (
      <div className="bg-[#1a1c24] border border-[#ffffff0a] p-3 rounded">
        <div className="text-[10px] text-[#888] font-mono uppercase flex items-center gap-1">
          {label}
          {tooltip && (
            <span title={tooltip}>
              <Info size={10} className="text-[#888] cursor-help" />
            </span>
          )}
        </div>
        <div className="text-sm font-bold text-[#555]">—</div>
      </div>
    );
  }

  const num = typeof value === 'string' ? parseFloat(value) : value;
  const display = isString ? value : (typeof num === 'number' ? num.toFixed(2) : value);
  const colorClass = color
    ? (Number(num) > 0 ? 'text-green-400' : Number(num) < 0 ? 'text-red-400' : 'text-[#fafafa]')
    : 'text-[#fafafa]';

  return (
    <div className="bg-[#1a1c24] border border-[#ffffff0a] p-3 rounded">
      <div className="text-[10px] text-[#888] font-mono uppercase flex items-center gap-1">
        {label}
        {tooltip && (
          <span title={tooltip}>
            <Info size={10} className="text-[#888] cursor-help" />
          </span>
        )}
      </div>
      <div className={`text-sm font-bold ${colorClass}`}>
        {display}{suffix}
      </div>
    </div>
  );
}
