import { useState, useEffect, useMemo, useCallback } from 'react';
import { Librarian } from '../lib/Librarian';
import { GitCompare, RefreshCw, AlertTriangle, ChevronDown, ChevronUp, ArrowUpDown } from 'lucide-react';
import { useSettings } from '../lib/SettingsContext';
import { resolveBucket } from '../lib/bucketUtils';
import { useHealthStatus } from '../hooks/useHealthStatus';
import PresetChip from '../components/PresetChip';
import { DivergenceConfig } from '../lib/scannerPresets';

interface ScannerData {
    symbol: string;
    sector: string;
    bucket: string;
    priceChangePct: number;
    deliveryChangePct: number;
    relativeVolume: number;
    score: number;
}

interface RawData {
    ticker: string;
    latest_close: number;
    latest_vwap: number;
    latest_typical: number;
    past_close: number;
    past_vwap: number;
    past_typical: number;
    latest_delivery_qty: number;
    past_delivery_qty: number;
    latest_delivery_pct: number;
    past_delivery_pct: number;
    avg_volume: number;
    latest_volume: number;
}

export default function PriceDeliveryDivergenceScannerView({ lib, onNavigate }: { lib: Librarian, onNavigate?: (tab: string, symbol?: string) => void }) {
    const { settings } = useSettings();
    const { isConnected } = useHealthStatus();

    const [isLoading, setIsLoading] = useState(false);
    const [isDemo, setIsDemo] = useState(false);
    const [errorMsg, setErrorMsg] = useState<string | null>(null);

    const [rawData, setRawData] = useState<RawData[]>([]);
    const [data, setData] = useState<ScannerData[]>([]);
    const [metadataMap, setMetadataMap] = useState<Map<string, { sector: string, bucket: string }>>(new Map());
    const [metadataLoaded, setMetadataLoaded] = useState(false);

    // Fetch controls
    const [lookbackBars, setLookbackBars] = useState(10);
    
    // UI Controls for filtering and scoring
    const [priceMetric, setPriceMetric] = useState<'Close' | 'VWAP' | 'Typical'>('Close');
    const [deliveryMetric, setDeliveryMetric] = useState<'Pct' | 'Qty'>('Pct');
    const [priceDirection, setPriceDirection] = useState<'Falling' | 'Rising'>('Falling');
    
    // Sliders
    const [minPriceChange, setMinPriceChange] = useState(-2);
    const [minDeliveryChange, setMinDeliveryChange] = useState(5);
    const [minRelativeVolume, setMinRelativeVolume] = useState(0);
    const [minScore, setMinScore] = useState(50);
    const [scoreWeighting, setScoreWeighting] = useState<'Balanced' | 'Price' | 'Delivery'>('Balanced');

    // Filtering controls
    const [filterSector, setFilterSector] = useState('All');
    const [filterMcap, setFilterMcap] = useState('All');

    const [settingsOpen, setSettingsOpen] = useState(true);
    const [sortConfig, setSortConfig] = useState<{ key: keyof ScannerData, direction: 'asc' | 'desc' } | null>({ key: 'score', direction: 'desc' });

    // Fetch Metadata Once
    useEffect(() => {
        let active = true;
        const fetchMeta = async () => {
            try {
                if (!lib.isConnectedToLocalRepo || settings.mockDataMode) {
                    if (active) setMetadataLoaded(true);
                    return;
                }
                const symbolsResult = await lib.executeQuery('_meta_conn', 'SELECT symbol as ticker, sector, in_nifty500 FROM symbols_master LIMIT 10000', {}, 12000);
                const indexResult = await lib.executeQuery('_meta_conn', 'SELECT symbol, index_name FROM index_constituents LIMIT 5000', {}, 12000);
                
                const indicesMap = new Map<string, string[]>();
                if (indexResult && Array.isArray(indexResult)) {
                    indexResult.forEach((row: any) => {
                        if (indicesMap.has(row.symbol)) {
                            indicesMap.get(row.symbol)!.push(row.index_name);
                        } else {
                            indicesMap.set(row.symbol, [row.index_name]);
                        }
                    });
                }
                const metaMap = new Map<string, { sector: string, bucket: string }>();
                if (symbolsResult) {
                    for (const m of symbolsResult) {
                        const indices = indicesMap.get(m.ticker) || [];
                        const bucket = resolveBucket(indices, m.in_nifty500);
                        metaMap.set(m.ticker, {
                            sector: m.sector || 'Unknown',
                            bucket: bucket
                        });
                    }
                }
                if (active) {
                    setMetadataMap(metaMap);
                    setMetadataLoaded(true);
                }
            } catch (e) {
                console.error(e);
                if (active) setMetadataLoaded(true);
            }
        };
        fetchMeta();
        return () => { active = false; };
    }, [lib, settings.mockDataMode]);

    const fetchData = useCallback(async () => {
        if (!metadataLoaded) return;
        setIsLoading(true);
        setErrorMsg(null);

        const mockMode = !lib.isConnectedToLocalRepo || settings.mockDataMode;
        
        try {
            if (mockMode) {
                setIsDemo(true);
                generateMockData();
                setIsLoading(false);
                return;
            }
            setIsDemo(false);

            const safeBars = Math.max(3, Math.min(252, Math.floor(Number(lookbackBars) || 10)));

            /* Note: Computing AVG(volume) inside the windowed subquery can be heavy for large datasets. Consider optimizing with a separate CTE if performance degrades. */
            const query = `
                SELECT symbol as ticker,
                       MAX(CASE WHEN rn_desc = 1 THEN close END) as latest_close,
                       MAX(CASE WHEN rn_desc = 1 THEN vwap END) as latest_vwap,
                       MAX(CASE WHEN rn_desc = 1 THEN (high + low + close)/3 END) as latest_typical,
                       MAX(CASE WHEN rn_desc = ? THEN close END) as past_close,
                       MAX(CASE WHEN rn_desc = ? THEN vwap END) as past_vwap,
                       MAX(CASE WHEN rn_desc = ? THEN (high + low + close)/3 END) as past_typical,
                       
                       MAX(CASE WHEN rn_desc = 1 THEN delivery END) as latest_delivery_qty,
                       MAX(CASE WHEN rn_desc = ? THEN delivery END) as past_delivery_qty,
                       
                       MAX(CASE WHEN rn_desc = 1 THEN (delivery * 100.0 / NULLIF(volume, 0)) END) as latest_delivery_pct,
                       MAX(CASE WHEN rn_desc = ? THEN (delivery * 100.0 / NULLIF(volume, 0)) END) as past_delivery_pct,
                       
                       AVG(volume) as avg_volume,
                       MAX(CASE WHEN rn_desc = 1 THEN volume END) as latest_volume
                FROM (
                    SELECT symbol, close, vwap, high, low, volume, delivery,
                           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) as rn_desc
                    FROM technical_data
                )
                WHERE rn_desc <= ?
                GROUP BY symbol
            `;
            
            const results = await lib.executeQuery('_tech_conn', query, [safeBars, safeBars, safeBars, safeBars, safeBars, safeBars], 15000);
            
            if (results && results.length > 0) {
                setIsDemo(false);
                setRawData(results.map((r: any) => ({
                    ticker: r.ticker,
                    latest_close: Number(r.latest_close) || 0,
                    latest_vwap: Number(r.latest_vwap) || 0,
                    latest_typical: Number(r.latest_typical) || 0,
                    past_close: Number(r.past_close) || 0,
                    past_vwap: Number(r.past_vwap) || 0,
                    past_typical: Number(r.past_typical) || 0,
                    latest_delivery_qty: Number(r.latest_delivery_qty) || 0,
                    past_delivery_qty: Number(r.past_delivery_qty) || 0,
                    latest_delivery_pct: Number(r.latest_delivery_pct) || 0,
                    past_delivery_pct: Number(r.past_delivery_pct) || 0,
                    avg_volume: Number(r.avg_volume) || 0,
                    latest_volume: Number(r.latest_volume) || 0
                })));
            } else {
                setRawData([]);
            }
        } catch (e: any) {
            console.error(e);
            setErrorMsg(e.message || 'Database unavailable - generating mock data.');
            setIsDemo(true);
            generateMockData();
        } finally {
            setIsLoading(false);
        }
    // Suppressed because generateMockData operates primarily on constants and the linter falsely requires it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [lookbackBars, metadataLoaded, settings.mockDataMode, lib]);

    const generateMockData = () => {
        const mock: RawData[] = [];
        const tickers = ['RELIANCE', 'TCS', 'HDFCBANK', 'ICICIBANK', 'INFY', 'ITC', 'SBIN', 'LARSEN', 'BAJFINANCE', 'BHARTIARTL', 'MARUTI', 'ASIANPAINT', 'TITAN', 'M&M', 'SUNPHARMA', 'TATASTEEL', 'KOTAKBANK', 'HUL', 'WIPRO', 'ONGC'];
        tickers.forEach(t => {
            const basePrice = Math.random() * 3000 + 100;
            const priceChange = (Math.random() * 0.2) - 0.1; // -10% to +10%
            const pastPrice = basePrice * (1 - priceChange);
            
            const baseDel = Math.random() * 40 + 20; // 20% to 60%
            const delChange = (Math.random() * 20) - 5; // -5% to +15%
            const pastDel = Math.max(0, baseDel - delChange);

            mock.push({
                ticker: t,
                latest_close: basePrice,
                latest_vwap: basePrice * 1.01,
                latest_typical: basePrice * 0.99,
                past_close: pastPrice,
                past_vwap: pastPrice * 1.01,
                past_typical: pastPrice * 0.99,
                latest_delivery_qty: baseDel * 10000,
                past_delivery_qty: pastDel * 10000,
                latest_delivery_pct: baseDel,
                past_delivery_pct: pastDel,
                avg_volume: 1000000,
                latest_volume: 1000000 * (1 + (Math.random() * 3))
            });
        });
        setRawData(mock);
    };

    useEffect(() => {
        if (metadataLoaded) {
            fetchData();
        }
    }, [fetchData, metadataLoaded]);

    const uniqueSectors = useMemo(() => {
        const s = new Set<string>();
        for (const meta of metadataMap.values()) {
            if (meta.sector) s.add(meta.sector);
        }
        return Array.from(s).sort();
    }, [metadataMap]);

    // Computing scores and filtering based on UI controls
    const processedData = useMemo(() => {
        const results: ScannerData[] = [];

        rawData.forEach(d => {
            let pChange = 0;
            switch(priceMetric) {
                case 'Close': pChange = d.past_close ? ((d.latest_close - d.past_close) / d.past_close) * 100 : 0; break;
                case 'VWAP': pChange = d.past_vwap ? ((d.latest_vwap - d.past_vwap) / d.past_vwap) * 100 : 0; break;
                case 'Typical': pChange = d.past_typical ? ((d.latest_typical - d.past_typical) / d.past_typical) * 100 : 0; break;
            }

            let dChange = 0;
            if (deliveryMetric === 'Pct') {
                dChange = d.latest_delivery_pct - d.past_delivery_pct;
            } else {
                dChange = d.past_delivery_qty ? ((d.latest_delivery_qty - d.past_delivery_qty) / d.past_delivery_qty) * 100 : 0;
            }

            const rVol = d.avg_volume > 0 ? d.latest_volume / d.avg_volume : 0;

            // Apply basic direction requirement
            let dirMatch = false;
            if (priceDirection === 'Falling' && pChange <= minPriceChange) dirMatch = true;
            if (priceDirection === 'Rising' && pChange >= minPriceChange) dirMatch = true;

            if (!dirMatch) return;
            if (dChange < minDeliveryChange) return;
            if (rVol < minRelativeVolume) return;

            // Score logic
            const score_p = Math.max(0, Math.min(100, Math.abs(pChange) / 10 * 100));
            const score_d = deliveryMetric === 'Pct'
              ? Math.max(0, Math.min(100, dChange / 30 * 100))
              : Math.max(0, Math.min(100, dChange / 200 * 100));
            const score_v = Math.max(0, Math.min(100, rVol / 2 * 100));

            let wP = 0.4, wD = 0.4, wV = 0.2;
            if (scoreWeighting === 'Price') { wP = 0.5; wD = 0.3; wV = 0.2; }
            if (scoreWeighting === 'Delivery') { wP = 0.3; wD = 0.5; wV = 0.2; }

            const score = Math.round(wP * score_p + wD * score_d + wV * score_v);

            if (score < minScore) return;

            const meta = metadataMap.get(d.ticker) || { sector: 'Unknown', bucket: 'Deep Frontier' };
            
            if (filterSector !== 'All' && meta.sector !== filterSector) return;
            if (filterMcap !== 'All' && meta.bucket !== filterMcap) return;

            results.push({
                symbol: d.ticker,
                sector: meta.sector,
                bucket: meta.bucket,
                priceChangePct: pChange,
                deliveryChangePct: dChange,
                relativeVolume: rVol,
                score: score
            });
        });

        return results;
    }, [rawData, priceMetric, deliveryMetric, priceDirection, minPriceChange, minDeliveryChange, minRelativeVolume, minScore, scoreWeighting, filterSector, filterMcap, metadataMap]);

    const sortedData = useMemo(() => {
        if (!sortConfig) return processedData;
        return [...processedData].sort((a, b) => {
            const aVal = a[sortConfig.key];
            const bVal = b[sortConfig.key];
            if (aVal < bVal) return sortConfig.direction === 'asc' ? -1 : 1;
            if (aVal > bVal) return sortConfig.direction === 'asc' ? 1 : -1;
            return 0;
        });
    }, [processedData, sortConfig]);

    const handleSort = (key: keyof ScannerData) => {
        setSortConfig(prev => {
            if (!prev) return { key, direction: 'desc' };
            return {
                key,
                direction: prev.key === key && prev.direction === 'desc' ? 'asc' : 'desc'
            };
        });
    };

    const SortIcon = ({ column }: { column: keyof ScannerData }) => {
        if (sortConfig?.key !== column) return <ArrowUpDown size={10} className="inline ml-1 opacity-30" />;
        return sortConfig.direction === 'asc' 
            ? <ChevronUp size={10} className="inline ml-1 text-orange-400" /> 
            : <ChevronDown size={10} className="inline ml-1 text-orange-400" />;
    };

    const summaries = useMemo(() => {
        if (processedData.length === 0) return { avgScore: 0, avgDel: 0 };
        const sumScore = processedData.reduce((acc, v) => acc + v.score, 0);
        const sumDel = processedData.reduce((acc, v) => acc + v.deliveryChangePct, 0);
        return {
            avgScore: Math.round(sumScore / processedData.length),
            avgDel: sumDel / processedData.length
        };
    }, [processedData]);

    return (
        <div className="bg-[#1e2028] border border-[#ffffff1a] rounded flex flex-col shadow-xl overflow-hidden min-h-[600px]">
            {/* Header */}
            <div className="px-6 py-4 border-b border-[#ffffff1a] flex justify-between items-center bg-[#1a1c24]">
                <div className="flex items-center gap-3">
                    <GitCompare size={20} className="text-orange-400" />
                    <h3 className="font-semibold text-[#fafafa] flex items-center gap-2">
                        Price-Delivery Divergence
                    </h3>
                    <div className="flex gap-2 items-center">
                        {errorMsg && (
                            <span className="text-[10px] bg-red-500/20 text-red-500 px-2 py-1 rounded font-mono border border-red-500/30 flex items-center gap-1">
                                <AlertTriangle size={10} /> {errorMsg}
                            </span>
                        )}
                        {isDemo && !isConnected && (
                            <span className="text-[10px] bg-yellow-500/20 text-yellow-500 px-2 py-1 rounded font-mono border border-yellow-500/30">
                                ⚠️ SIMULATED PIPELINE
                            </span>
                        )}
                    </div>
                </div>
                <div className="flex items-center gap-3">
                    <span className="text-[10px] text-[#666] font-mono">Dynamic Accumulation Logic</span>
                    <button 
                        onClick={fetchData} 
                        className="bg-[#2a2c34] hover:bg-[#3a3c44] text-[#aaa] hover:text-white px-2 py-1 rounded border border-[#ffffff1a] transition-all flex items-center gap-1"
                        disabled={isLoading}
                    >
                        <RefreshCw size={12} className={isLoading ? "animate-spin" : ""} />
                        <span className="text-xs">Sync DB</span>
                    </button>
                    <button onClick={() => setSettingsOpen(!settingsOpen)} className="p-1 border border-[#ffffff1a] rounded hover:bg-[#ffffff1a] text-[#888]">
                        {settingsOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                    </button>
                </div>
            </div>

            {/* Settings Panel */}
            {settingsOpen && (
                <div className="bg-[#15171d] border-b border-[#ffffff1a] p-4 flex flex-col gap-4">
                    <PresetChip
                        module="PriceDeliveryDivergence"
                        currentConfig={{
                            lookbackBars, priceMetric, deliveryMetric, priceDirection,
                            minPriceChange, minDeliveryChange, minRelativeVolume, minScore,
                            scoreWeighting, filterSector, filterMcap
                        }}
                        onLoad={(config) => {
                            const c = config as DivergenceConfig;
                            setLookbackBars(c.lookbackBars);
                            setPriceMetric(c.priceMetric);
                            setDeliveryMetric(c.deliveryMetric);
                            setPriceDirection(c.priceDirection);
                            setMinPriceChange(c.minPriceChange);
                            setMinDeliveryChange(c.minDeliveryChange);
                            setMinRelativeVolume(c.minRelativeVolume);
                            setMinScore(c.minScore);
                            setScoreWeighting(c.scoreWeighting);
                            setFilterSector(c.filterSector);
                            setFilterMcap(c.filterMcap);
                        }}
                    />
                    <div className="grid grid-cols-1 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-9 gap-4 items-end">
                        <div className="flex flex-col">
                           <label className="text-[10px] text-[#888] font-mono mb-1 text-nowrap">Lookback Period</label>
                           <select value={lookbackBars} onChange={(e) => setLookbackBars(Number(e.target.value))} className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full">
                               <option value={5}>1 Week</option>
                               <option value={10}>2 Weeks</option>
                               <option value={21}>1 Month</option>
                               <option value={63}>1 Quarter</option>
                               <option value={126}>6 Months</option>
                               <option value={252}>1 Year</option>
                           </select>
                        </div>
                        <div className="flex flex-col">
                           <label className="text-[10px] text-[#888] font-mono mb-1">Price Metric</label>
                           <select value={priceMetric} onChange={(e) => setPriceMetric(e.target.value as 'Close' | 'VWAP' | 'Typical')} className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full">
                               <option value="Close">Close</option>
                               <option value="VWAP">VWAP</option>
                               <option value="Typical">Typical Price</option>
                           </select>
                        </div>
                        <div className="flex flex-col">
                           <label className="text-[10px] text-[#888] font-mono mb-1 text-nowrap">Delivery Metric</label>
                           <select value={deliveryMetric} onChange={(e) => setDeliveryMetric(e.target.value as 'Pct' | 'Qty')} className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full">
                               <option value="Pct">Delivery %</option>
                               <option value="Qty">Delivery Qty</option>
                           </select>
                        </div>
                        <div className="flex flex-col">
                           <label className="text-[10px] text-[#888] font-mono mb-1 text-nowrap">Price Action</label>
                           <select value={priceDirection} onChange={(e) => {
                               const dir = e.target.value as 'Falling' | 'Rising';
                               setPriceDirection(dir);
                               if (dir === 'Rising' && minPriceChange < 0) setMinPriceChange(Math.abs(minPriceChange));
                               if (dir === 'Falling' && minPriceChange > 0) setMinPriceChange(-Math.abs(minPriceChange));
                           }} className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full">
                               <option value="Falling">Falling</option>
                               <option value="Rising">Rising</option>
                           </select>
                        </div>
                        
                        <div className="flex flex-col col-span-1 md:col-span-2 lg:col-span-1">
                           <div className="flex justify-between text-[10px] text-[#888] font-mono mb-0.5">
                               <label>Min Price {priceDirection === 'Rising' ? 'Increase' : 'Decline'} %</label>
                               <span className="text-orange-400">{minPriceChange}%</span>
                           </div>
                           <input type="range" min="-30" max="30" value={minPriceChange} onChange={(e) => setMinPriceChange(Number(e.target.value))} className="w-full accent-orange-500" />
                        </div>
                        
                        <div className="flex flex-col col-span-1 md:col-span-2 lg:col-span-1">
                           <div className="flex justify-between text-[10px] text-[#888] font-mono mb-0.5">
                               <label>Min Del Change {deliveryMetric === 'Pct' ? '%' : 'Pct'}</label>
                               <span className="text-orange-400">{minDeliveryChange}%</span>
                           </div>
                           <input type="range" min="-10" max="50" value={minDeliveryChange} onChange={(e) => setMinDeliveryChange(Number(e.target.value))} className="w-full accent-orange-500" />
                        </div>

                        <div className="flex flex-col col-span-1 md:col-span-2 lg:col-span-1">
                           <div className="flex justify-between text-[10px] text-[#888] font-mono mb-0.5">
                               <label>Min Rel Volume</label>
                               <span className="text-orange-400">{minRelativeVolume}x</span>
                           </div>
                           <input type="range" min="0" max="5" step="0.1" value={minRelativeVolume} onChange={(e) => setMinRelativeVolume(Number(e.target.value))} className="w-full accent-orange-500" />
                        </div>

                        <div className="flex flex-col">
                           <label className="text-[10px] text-[#888] font-mono mb-1 text-nowrap">Score Weighting</label>
                           <select value={scoreWeighting} onChange={(e) => setScoreWeighting(e.target.value as 'Balanced' | 'Price' | 'Delivery')} className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full">
                               <option value="Balanced">Balanced</option>
                               <option value="Price">Price-heavy</option>
                               <option value="Delivery">Delivery-heavy</option>
                           </select>
                        </div>
                        <div className="flex flex-col">
                           <div className="flex justify-between text-[10px] text-[#888] font-mono mb-0.5">
                               <label>Min Score</label>
                               <span className="text-orange-400">{minScore}</span>
                           </div>
                           <input type="range" min="0" max="100" value={minScore} onChange={(e) => setMinScore(Number(e.target.value))} className="w-full accent-orange-500" />
                        </div>
                    </div>
                </div>
            )}

            {/* Summaries & Filters Row */}
            <div className="grid grid-cols-1 md:grid-cols-[1fr_min-content] gap-4 p-4 border-b border-[#ffffff1a] bg-[#1a1c24]">
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                    <div className="bg-[#2a2c34] border border-[#ffffff1a] rounded p-3 flex flex-col justify-center">
                        <span className="text-xs text-[#888] font-mono mb-1">Divergence Signals</span>
                        <span className="text-2xl text-orange-400 font-semibold">{processedData.length}</span>
                    </div>
                    <div className="bg-[#2a2c34] border border-[#ffffff1a] rounded p-3 flex flex-col justify-center">
                        <span className="text-xs text-[#888] font-mono mb-1">Average Score</span>
                        <span className="text-2xl text-[#fafafa] font-semibold">{summaries.avgScore}</span>
                    </div>
                    <div className="bg-[#2a2c34] border border-[#ffffff1a] rounded p-3 flex flex-col justify-center">
                        <span className="text-xs text-[#888] font-mono mb-1">Avg Delivery Change</span>
                        <span className="text-2xl text-[#fafafa] font-semibold">{summaries.avgDel > 0 ? '+' : ''}{summaries.avgDel.toFixed(1)}%</span>
                    </div>
                </div>
                <div className="grid grid-cols-2 lg:grid-cols-1 gap-2 min-w-[200px] h-full items-center">
                      <div className="flex flex-col">
                         <label className="text-[10px] text-[#888] font-mono mb-1">Sector Filter</label>
                         <select value={filterSector} onChange={(e) => setFilterSector(e.target.value)} className="bg-[#2a2c34] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full">
                             <option value="All">All Sectors</option>
                             {uniqueSectors.map(s => <option key={s} value={s}>{s}</option>)}
                         </select>
                      </div>
                      <div className="flex flex-col">
                         <label className="text-[10px] text-[#888] font-mono mb-1">Mcap Filter</label>
                         <select value={filterMcap} onChange={(e) => setFilterMcap(e.target.value)} className="bg-[#2a2c34] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full">
                             <option value="All">All Caps</option>
                             <option value="Large Cap (N50)">Large Cap (N50)</option>
                             <option value="Large Cap (N100)">Large Cap (N100)</option>
                             <option value="Nifty Small Cap 250">Small Cap (N250)</option>
                             <option value="Broader Market (N500)">Broader Market (N500)</option>
                             <option value="Deep Frontier">Deep Frontier</option>
                         </select>
                      </div>
                </div>
            </div>

            {/* Table */}
            <div className="flex-1 overflow-auto">
                {isLoading ? (
                    <div className="p-8 text-center text-[#888] font-mono text-xs flex flex-col items-center justify-center h-64 gap-4">
                        <RefreshCw className="animate-spin text-orange-500/50" size={24} />
                        Syncing prices & delivery...
                    </div>
                ) : (
                    <table className="w-full text-left border-collapse">
                        <thead className="sticky top-0 bg-[#1a1c24] z-10 shadow-sm border-b border-[#ffffff1a]">
                            <tr>
                                <th className={`p-3 text-[10px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap ${sortConfig?.key === 'symbol' ? 'text-white' : ''}`} onClick={() => handleSort('symbol')}>
                                    Symbol <SortIcon column="symbol" />
                                </th>
                                <th className={`p-3 text-[10px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap ${sortConfig?.key === 'sector' ? 'text-white' : ''}`} onClick={() => handleSort('sector')}>
                                    Sector <SortIcon column="sector" />
                                </th>
                                <th className={`p-3 text-[10px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap ${sortConfig?.key === 'bucket' ? 'text-white' : ''}`} onClick={() => handleSort('bucket')}>
                                    Bucket <SortIcon column="bucket" />
                                </th>
                                <th className={`p-3 text-[10px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right ${sortConfig?.key === 'priceChangePct' ? 'text-white' : ''}`} onClick={() => handleSort('priceChangePct')}>
                                    Price Change % <SortIcon column="priceChangePct" />
                                </th>
                                <th className={`p-3 text-[10px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right ${sortConfig?.key === 'deliveryChangePct' ? 'text-white' : ''}`} onClick={() => handleSort('deliveryChangePct')}>
                                    Del Change <SortIcon column="deliveryChangePct" />
                                </th>
                                <th className={`p-3 text-[10px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right ${sortConfig?.key === 'relativeVolume' ? 'text-white' : ''}`} onClick={() => handleSort('relativeVolume')}>
                                    Rel Volume <SortIcon column="relativeVolume" />
                                </th>
                                <th className={`p-3 text-[10px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap ${sortConfig?.key === 'score' ? 'text-white' : ''}`} onClick={() => handleSort('score')}>
                                    Score <SortIcon column="score" />
                                </th>
                            </tr>
                        </thead>
                        <tbody>
                            {sortedData.length === 0 ? (
                                <tr>
                                    <td colSpan={7} className="p-8 text-center text-[#666] font-mono text-xs">
                                        No divergence signals match your strict criteria.
                                    </td>
                                </tr>
                            ) : (
                                sortedData.map(d => (
                                    <tr key={d.symbol} className="border-b border-[#ffffff0a] hover:bg-[#ffffff05] transition-colors group">
                                        <td className="p-3 whitespace-nowrap">
                                            <span 
                                                onClick={() => onNavigate?.('Technical Chart', d.symbol)}
                                                className="font-bold text-[#fafafa] cursor-pointer hover:text-orange-400 hover:underline inline-flex items-center gap-1 transition-colors"
                                            >
                                                {d.symbol}
                                            </span>
                                        </td>
                                        <td className="p-3 text-[#ccc] text-sm whitespace-nowrap">{d.sector}</td>
                                        <td className="p-3 text-[#888] text-xs font-mono whitespace-nowrap">{d.bucket}</td>
                                        <td className="p-3 text-sm font-mono whitespace-nowrap text-right"><span className={d.priceChangePct >= 0 ? "text-green-400" : "text-red-400"}>{d.priceChangePct > 0 ? "+" : ""}{d.priceChangePct.toFixed(2)}%</span></td>
                                        <td className="p-3 text-sm font-mono whitespace-nowrap text-right"><span className={d.deliveryChangePct >= 0 ? "text-green-400" : "text-red-400"}>{d.deliveryChangePct > 0 ? "+" : ""}{d.deliveryChangePct.toFixed(1)}%</span></td>
                                        <td className="p-3 text-sm font-mono whitespace-nowrap text-right"><span className={d.relativeVolume > 1.5 ? "text-orange-400" : "text-[#aaa]"}>{d.relativeVolume.toFixed(2)}x</span></td>
                                        <td className="p-3 w-48">
                                            <div className="flex items-center gap-2">
                                                <span className={`text-sm font-mono w-8 text-right font-semibold ${d.score >= 80 ? 'text-orange-400' : d.score >= 50 ? 'text-[#fafafa]' : 'text-[#666]'}`}>
                                                    {d.score}
                                                </span>
                                                <div className="flex-1 h-1.5 bg-[#ffffff1a] rounded overflow-hidden">
                                                    <div className={`h-full bg-orange-500 rounded ${d.score >= 80 ? 'shadow-[0_0_8px_rgba(249,115,22,0.5)]' : ''}`} style={{ width: `${Math.max(0, Math.min(100, d.score))}%` }} />
                                                </div>
                                            </div>
                                        </td>
                                    </tr>
                                ))
                            )}
                        </tbody>
                    </table>
                )}
            </div>
        </div>
    );
}
