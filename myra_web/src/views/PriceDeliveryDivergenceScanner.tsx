import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { Librarian } from '../lib/Librarian';
import { GitCompare, RefreshCw, AlertTriangle, ChevronDown, ChevronUp, ArrowUpDown, BarChart2 } from 'lucide-react';
import { useSettings } from '../lib/SettingsContext';
import { resolveBucket } from '../lib/bucketUtils';
import { useHealthStatus } from '../hooks/useHealthStatus';
import PresetChip from '../components/PresetChip';
import { DivergenceConfig } from '../lib/scannerPresets';
import MarketCapRangeFilter from '../components/MarketCapRangeFilter';
import { fetchMarketCapMap, fetchFreeFloatMcapMap } from '../lib/marketCapCache';
import { useDebouncedCallback } from 'use-debounce';
import BacktestPanel from './BacktestPanel';

interface ScannerData {
    symbol: string;
    sector: string;
    bucket: string;
    priceChangePct: number;
    deliveryChangePct: number;
    relativeVolume: number;
    relativeStrength: number;
    position52W: number;
    score: number;
    consecutiveHighDeliveryDays: number;
    latestDeliveryPct: number;
    signalBadge: string;
    detectedBaseLength: number;
    triggerPrice: number;
    stopLossPrice: number;
    targetPrice: number;
    riskReward: number;
    latestClose: number;
    baseTightness: number;
    dar: number;
    alreadyTriggered: boolean;
    nearEarnings: boolean;
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
    consecutiveHighDeliveryDays: number;
    detected_base_length: number;
    base_high_5: number;
    base_low_5: number;
    base_high_10: number;
    base_low_10: number;
    base_high_21: number;
    base_low_21: number;
    base_high_45: number;
    base_low_45: number;
    atr_14: number;
    high_52w: number;
    low_52w: number;
}

export default function PriceDeliveryDivergenceScannerView({ lib }: { lib: Librarian }) {
    const { settings } = useSettings();
    const { isConnected } = useHealthStatus();

    const [isLoading, setIsLoading] = useState(false);
    const [isDemo, setIsDemo] = useState(false);
    const [errorMsg, setErrorMsg] = useState<string | null>(null);

    const [rawData, setRawData] = useState<RawData[]>([]);
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

    // Debounced display states for smooth slider UX
    const [minPriceChangeDisplay, setMinPriceChangeDisplay] = useState(minPriceChange);
    const setMinPriceChangeDebounced = useDebouncedCallback(setMinPriceChange, 100);
    const [minDeliveryChangeDisplay, setMinDeliveryChangeDisplay] = useState(minDeliveryChange);
    const setMinDeliveryChangeDebounced = useDebouncedCallback(setMinDeliveryChange, 100);
    const [minRelativeVolumeDisplay, setMinRelativeVolumeDisplay] = useState(minRelativeVolume);
    const setMinRelativeVolumeDebounced = useDebouncedCallback(setMinRelativeVolume, 100);
    const [minScoreDisplay, setMinScoreDisplay] = useState(minScore);
    const setMinScoreDebounced = useDebouncedCallback(setMinScore, 100);

    // Filtering controls
    const [minAbsDeliveryPct, setMinAbsDeliveryPct] = useState(0);
    const [minConsecutiveDays, setMinConsecutiveDays] = useState(0);
    const [minRR, setMinRR] = useState(0);
    const [minDAR, setMinDAR] = useState(0);
    const [filterSector, setFilterSector] = useState('All');
    const [filterMcap, setFilterMcap] = useState('All');
    const [mcapRange, setMcapRange] = useState<{ min: number; max: number } | null>(null);
    const mcapMapRef = useRef<Map<string, number>>(new Map());
    const ffMcapMapRef = useRef<Map<string, number>>(new Map());
    useEffect(() => {
        fetchMarketCapMap().then(m => mcapMapRef.current = m);
        fetchFreeFloatMcapMap().then(m => { ffMcapMapRef.current = m;  });
    }, []);

    const [earningsProximitySet, setEarningsProximitySet] = useState<Set<string>>(new Set());
    const [hideNearEarnings, setHideNearEarnings] = useState(false);

    const [watchlist, setWatchlist] = useState<Set<string>>(() => {
        try {
            const stored = localStorage.getItem('divergence_watchlist');
            return stored ? new Set(JSON.parse(stored)) : new Set();
        } catch { return new Set(); }
    });
    const [showWatchlistOnly, setShowWatchlistOnly] = useState(false);
    const [showNewOnly, setShowNewOnly] = useState(false);
    const [previousSymbols, setPreviousSymbols] = useState<Set<string>>(new Set());
    const [newSymbols, setNewSymbols] = useState<Set<string>>(new Set());
    const toggleWatchlist = useCallback((symbol: string) => {
        setWatchlist(prev => {
            const next = new Set(prev);
            next.has(symbol) ? next.delete(symbol) : next.add(symbol);
            localStorage.setItem('divergence_watchlist', JSON.stringify(Array.from(next)));
            return next;
        });
    }, []);

    const [niftyChangePct, setNiftyChangePct] = useState(0);
    const [filterRSNegative, setFilterRSNegative] = useState(false);
    const [maxPosition52W, setMaxPosition52W] = useState(100);
    const [filtersVisible, setFiltersVisible] = useState(() => localStorage.getItem('pdd_filters_visible') !== 'false');
    const [backtestSymbol, setBacktestSymbol] = useState<string | null>(null);
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
                    setEarningsProximitySet(new Set());
                    setMetadataMap(metaMap);
                    setMetadataLoaded(true);
                }
            } catch (e) {
                console.error(e);
                if (active) {
                    setEarningsProximitySet(new Set());
                    setMetadataLoaded(true);
                }
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
    WITH     baseline AS (
        SELECT symbol,
               AVG(volume) AS avg_volume_20d
        FROM (
            SELECT symbol, volume,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
            FROM technical_data
            WHERE date >= date('now', '-60 days')
        )
        WHERE rn BETWEEN 2 AND 21
        GROUP BY symbol
    ),
    windowed AS (
        SELECT symbol, close, vwap, high, low, volume, delivery,
               ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn_desc
        FROM technical_data
        WHERE date >= date('now', '-400 days')
    ),
    streaks AS (
        SELECT symbol,
               CASE WHEN MAX(CASE WHEN rn_desc = 1 THEN (delivery * 100.0 / NULLIF(volume, 0)) END) >= 40
               THEN 1 ELSE 0 END
               +
               CASE WHEN MAX(CASE WHEN rn_desc = 1 THEN (delivery * 100.0 / NULLIF(volume, 0)) END) >= 40
                AND  MAX(CASE WHEN rn_desc = 2 THEN (delivery * 100.0 / NULLIF(volume, 0)) END) >= 40
               THEN 1 ELSE 0 END
               +
               CASE WHEN MAX(CASE WHEN rn_desc = 1 THEN (delivery * 100.0 / NULLIF(volume, 0)) END) >= 40
                AND  MAX(CASE WHEN rn_desc = 2 THEN (delivery * 100.0 / NULLIF(volume, 0)) END) >= 40
                AND  MAX(CASE WHEN rn_desc = 3 THEN (delivery * 100.0 / NULLIF(volume, 0)) END) >= 40
               THEN 1 ELSE 0 END
               +
               CASE WHEN MAX(CASE WHEN rn_desc = 1 THEN (delivery * 100.0 / NULLIF(volume, 0)) END) >= 40
                AND  MAX(CASE WHEN rn_desc = 2 THEN (delivery * 100.0 / NULLIF(volume, 0)) END) >= 40
                AND  MAX(CASE WHEN rn_desc = 3 THEN (delivery * 100.0 / NULLIF(volume, 0)) END) >= 40
                AND  MAX(CASE WHEN rn_desc = 4 THEN (delivery * 100.0 / NULLIF(volume, 0)) END) >= 40
               THEN 1 ELSE 0 END
               +
               CASE WHEN MAX(CASE WHEN rn_desc = 1 THEN (delivery * 100.0 / NULLIF(volume, 0)) END) >= 40
                AND  MAX(CASE WHEN rn_desc = 2 THEN (delivery * 100.0 / NULLIF(volume, 0)) END) >= 40
                AND  MAX(CASE WHEN rn_desc = 3 THEN (delivery * 100.0 / NULLIF(volume, 0)) END) >= 40
                AND  MAX(CASE WHEN rn_desc = 4 THEN (delivery * 100.0 / NULLIF(volume, 0)) END) >= 40
                AND  MAX(CASE WHEN rn_desc = 5 THEN (delivery * 100.0 / NULLIF(volume, 0)) END) >= 40
               THEN 1 ELSE 0 END
               AS consecutive_streak
        FROM windowed
        WHERE rn_desc <= 5
        GROUP BY symbol
    ),
    multiframe AS (
        SELECT symbol,
               MAX(CASE WHEN rn_desc = 1  THEN close END) AS c1,
               MAX(CASE WHEN rn_desc = 5  THEN close END) AS c5,
               MAX(CASE WHEN rn_desc = 10 THEN close END) AS c10,
               MAX(CASE WHEN rn_desc = 21 THEN close END) AS c21,
               MAX(CASE WHEN rn_desc = 45 THEN close END) AS c45,
               MAX(CASE WHEN rn_desc = 1  THEN (delivery * 100.0 / NULLIF(volume, 0)) END) AS d1,
               MAX(CASE WHEN rn_desc = 5  THEN (delivery * 100.0 / NULLIF(volume, 0)) END) AS d5,
               MAX(CASE WHEN rn_desc = 10 THEN (delivery * 100.0 / NULLIF(volume, 0)) END) AS d10,
               MAX(CASE WHEN rn_desc = 21 THEN (delivery * 100.0 / NULLIF(volume, 0)) END) AS d21,
               MAX(CASE WHEN rn_desc = 45 THEN (delivery * 100.0 / NULLIF(volume, 0)) END) AS d45
        FROM windowed
        WHERE rn_desc <= 45
        GROUP BY symbol
    ),
    atr_data AS (
        SELECT symbol,
               AVG(high - low) AS atr_14
        FROM (
            SELECT symbol, high, low,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
            FROM technical_data
            WHERE date >= date('now', '-30 days')
        )
        WHERE rn BETWEEN 1 AND 14
        GROUP BY symbol
    )
    SELECT
        w.symbol AS ticker,
        MAX(CASE WHEN w.rn_desc = 1 THEN w.close END)                              AS latest_close,
        MAX(CASE WHEN w.rn_desc = 1 THEN w.vwap END)                               AS latest_vwap,
        MAX(CASE WHEN w.rn_desc = 1 THEN (w.high + w.low + w.close) / 3 END)       AS latest_typical,
        MAX(CASE WHEN w.rn_desc = ? THEN w.close END)                              AS past_close,
        MAX(CASE WHEN w.rn_desc = ? THEN w.vwap END)                               AS past_vwap,
        MAX(CASE WHEN w.rn_desc = ? THEN (w.high + w.low + w.close) / 3 END)       AS past_typical,
        MAX(CASE WHEN w.rn_desc = 1 THEN w.delivery END)                           AS latest_delivery_qty,
        MAX(CASE WHEN w.rn_desc = ? THEN w.delivery END)                           AS past_delivery_qty,
        MAX(CASE WHEN w.rn_desc = 1 THEN (w.delivery * 100.0 / NULLIF(w.volume, 0)) END)  AS latest_delivery_pct,
        MAX(CASE WHEN w.rn_desc = ? THEN (w.delivery * 100.0 / NULLIF(w.volume, 0)) END)  AS past_delivery_pct,
        COALESCE(b.avg_volume_20d, AVG(w.volume)) AS avg_volume,
        MAX(CASE WHEN w.rn_desc = 1 THEN w.volume END)                             AS latest_volume,
        MAX(CASE WHEN w.rn_desc <= 5  THEN w.high END) AS base_high_5,
        MIN(CASE WHEN w.rn_desc <= 5  THEN w.low  END) AS base_low_5,
        MAX(CASE WHEN w.rn_desc <= 10 THEN w.high END) AS base_high_10,
        MIN(CASE WHEN w.rn_desc <= 10 THEN w.low  END) AS base_low_10,
        MAX(CASE WHEN w.rn_desc <= 21 THEN w.high END) AS base_high_21,
        MIN(CASE WHEN w.rn_desc <= 21 THEN w.low  END) AS base_low_21,
        MAX(CASE WHEN w.rn_desc <= 45 THEN w.high END) AS base_high_45_w,
        MIN(CASE WHEN w.rn_desc <= 45 THEN w.low  END) AS base_low_45_w,
        COALESCE(a.atr_14, (MAX(w.high) - MIN(w.low)) / 10.0) AS atr_14,
        MAX(CASE WHEN w.rn_desc <= 252 THEN w.high END) AS high_52w,
        MIN(CASE WHEN w.rn_desc <= 252 THEN w.low  END) AS low_52w,
        COALESCE(s.consecutive_streak, 0) AS consecutive_high_delivery_days,
        ROUND(
            CASE 
                WHEN (mf.d1 - mf.d5)  / (ABS((mf.c1 - mf.c5)  / NULLIF(mf.c5,  0) * 100) + 0.5) >
                     (mf.d1 - mf.d10) / (ABS((mf.c1 - mf.c10) / NULLIF(mf.c10, 0) * 100) + 0.5)
                 AND (mf.d1 - mf.d5)  / (ABS((mf.c1 - mf.c5)  / NULLIF(mf.c5,  0) * 100) + 0.5) >
                     (mf.d1 - mf.d21) / (ABS((mf.c1 - mf.c21) / NULLIF(mf.c21, 0) * 100) + 0.5)
                 AND (mf.d1 - mf.d5)  / (ABS((mf.c1 - mf.c5)  / NULLIF(mf.c5,  0) * 100) + 0.5) >
                     (mf.d1 - mf.d45) / (ABS((mf.c1 - mf.c45) / NULLIF(mf.c45, 0) * 100) + 0.5)
                THEN 5
                WHEN (mf.d1 - mf.d10) / (ABS((mf.c1 - mf.c10) / NULLIF(mf.c10, 0) * 100) + 0.5) >
                     (mf.d1 - mf.d21) / (ABS((mf.c1 - mf.c21) / NULLIF(mf.c21, 0) * 100) + 0.5)
                 AND (mf.d1 - mf.d10) / (ABS((mf.c1 - mf.c10) / NULLIF(mf.c10, 0) * 100) + 0.5) >
                     (mf.d1 - mf.d45) / (ABS((mf.c1 - mf.c45) / NULLIF(mf.c45, 0) * 100) + 0.5)
                THEN 10
                WHEN (mf.d1 - mf.d21) / (ABS((mf.c1 - mf.c21) / NULLIF(mf.c21, 0) * 100) + 0.5)  >
                     (mf.d1 - mf.d45) / (ABS((mf.c1 - mf.c45) / NULLIF(mf.c45, 0) * 100) + 0.5)
                THEN 21
                ELSE 45
            END
        , 0) AS detected_base_length
    FROM windowed w
    LEFT JOIN baseline b ON w.symbol = b.symbol
    LEFT JOIN streaks s ON w.symbol = s.symbol
    LEFT JOIN multiframe mf ON w.symbol = mf.symbol
    LEFT JOIN atr_data a ON w.symbol = a.symbol
    WHERE w.rn_desc <= 252
    GROUP BY w.symbol
`;
            
            const results = await lib.executeQuery('_tech_conn', query, [safeBars, safeBars, safeBars, safeBars, safeBars], 60000);

            const niftyQuery = `
                SELECT
                    MAX(CASE WHEN rn_desc = 1 THEN close END) AS nifty_latest,
                    MAX(CASE WHEN rn_desc = ? THEN close END) AS nifty_past
                FROM (
                    SELECT close,
                           ROW_NUMBER() OVER (ORDER BY date DESC) AS rn_desc
                    FROM benchmarks
                    WHERE symbol = '^NSEI'
                )
                WHERE rn_desc <= ?
            `;
            const niftyResult = await lib.executeQuery(
                '_meta_conn', niftyQuery, [safeBars, safeBars], 5000
            ).catch(() => null);
            const niftyChange = niftyResult?.[0]
                ? ((niftyResult[0].nifty_latest - niftyResult[0].nifty_past)
                   / niftyResult[0].nifty_past) * 100
                : 0;
            setNiftyChangePct(parseFloat(niftyChange.toFixed(2)));
            
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
                    latest_volume: Number(r.latest_volume) || 0,
                    base_high_5: Number(r.base_high_5) || 0,
                    base_low_5: Number(r.base_low_5) || 0,
                    base_high_10: Number(r.base_high_10) || 0,
                    base_low_10: Number(r.base_low_10) || 0,
                    base_high_21: Number(r.base_high_21) || 0,
                    base_low_21: Number(r.base_low_21) || 0,
                    base_high_45: Number(r.base_high_45_w) || 0,
                    base_low_45: Number(r.base_low_45_w) || 0,
                    atr_14: Number(r.atr_14) || 0,
                    high_52w: Number(r.high_52w) || 0,
                    low_52w: Number(r.low_52w) || 0,
                    consecutiveHighDeliveryDays: Number(r.consecutive_high_delivery_days) || 0,
                    detected_base_length: Number(r.detected_base_length) || 0
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
                latest_volume: 1000000 * (1 + (Math.random() * 3)),
                base_high_5: basePrice * 1.03,
                base_low_5: basePrice * 0.97,
                base_high_10: basePrice * 1.05,
                base_low_10: basePrice * 0.95,
                base_high_21: basePrice * 1.07,
                base_low_21: basePrice * 0.93,
                base_high_45: basePrice * 1.10,
                base_low_45: basePrice * 0.88,
                atr_14: basePrice * 0.015,
                high_52w: basePrice * 1.25,
                low_52w: basePrice * 0.75,
                consecutiveHighDeliveryDays: Math.floor(Math.random() * 6),
                detected_base_length: [5, 10, 21, 45][Math.floor(Math.random() * 4)]
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

    // Enrich raw data with computed metrics (no filtering — expensive, depends only on metric choices)
    const enrichedData = useMemo(() => {
        const results: ScannerData[] = [];

        rawData.forEach(d => {
            let pChange = 0;
            switch(priceMetric) {
                case 'Close': pChange = d.past_close ? ((d.latest_close - d.past_close) / d.past_close) * 100 : 0; break;
                case 'VWAP':
                    const vwapLatest = d.latest_vwap || d.latest_close;
                    const vwapPast   = d.past_vwap || d.past_close;
                    pChange = vwapPast ? ((vwapLatest - vwapPast) / vwapPast) * 100 : 0;
                    break;
                case 'Typical': pChange = d.past_typical ? ((d.latest_typical - d.past_typical) / d.past_typical) * 100 : 0; break;
            }

            let dChange = 0;
            if (deliveryMetric === 'Pct') {
                dChange = d.latest_delivery_pct - d.past_delivery_pct;
            } else {
                dChange = d.past_delivery_qty ? ((d.latest_delivery_qty - d.past_delivery_qty) / d.past_delivery_qty) * 100 : 0;
            }

            const rVol = d.avg_volume > 0 ? d.latest_volume / d.avg_volume : 0;

            const deliveryValueCr = (d.latest_delivery_qty * (d.latest_vwap || d.latest_close)) / 1e7;
            const ffMcapCr = (ffMcapMapRef.current.get(d.ticker) ?? mcapMapRef.current.get(d.ticker) ?? 0) / 1e7;
            const dar = ffMcapCr > 0 ? parseFloat(((deliveryValueCr / ffMcapCr) * 100).toFixed(4)) : 0;

            const relativeStrength = parseFloat((pChange - niftyChangePct).toFixed(2));
            const high52w = d.high_52w || d.latest_close;
            const low52w = d.low_52w || d.latest_close;
            const position52W = high52w > low52w
                ? parseFloat(((d.latest_close - low52w) / (high52w - low52w) * 100).toFixed(1))
                : 50;
            const nearEarnings = earningsProximitySet.has(d.ticker);

            const meta = metadataMap.get(d.ticker) || { sector: 'Unknown', bucket: 'Deep Frontier' };

            // Trigger / SL / Target / R:R
            const latestClose = d.latest_close;
            const baseLen = d.detected_base_length;
            const baseHigh = baseLen === 5  ? d.base_high_5  :
                             baseLen === 10 ? d.base_high_10 :
                             baseLen === 21 ? d.base_high_21 :
                                               d.base_high_45;
            const baseLow  = baseLen === 5  ? d.base_low_5   :
                             baseLen === 10 ? d.base_low_10  :
                             baseLen === 21 ? d.base_low_21  :
                                               d.base_low_45;

            const baseMidpoint = (baseHigh + baseLow) / 2;
            const baseRangePct = baseMidpoint > 0
                ? ((baseHigh - baseLow) / baseMidpoint) * 100
                : 0;
            const baseTightness = Math.round(Math.max(0, Math.min(100,
                100 - (baseRangePct / 20 * 100)
            )));

            const atrBuffer = d.atr_14 > 0 ? d.atr_14 : latestClose * 0.005;

            const triggerPrice  = parseFloat((baseHigh + atrBuffer).toFixed(2));
            const stopLossPrice = parseFloat((baseLow  - atrBuffer).toFixed(2));
            const risk  = triggerPrice - stopLossPrice;
            let targetPrice: number, riskReward: number;
            if (priceDirection === 'Falling') {
                targetPrice = parseFloat((triggerPrice + risk * 2).toFixed(2));
                riskReward = (latestClose > stopLossPrice && risk > 0)
                    ? parseFloat(((targetPrice - latestClose) / (latestClose - stopLossPrice)).toFixed(2))
                    : 0;
            } else {
                targetPrice = parseFloat((triggerPrice - risk * 2).toFixed(2));
                riskReward = (latestClose < triggerPrice && risk > 0)
                    ? parseFloat(((latestClose - targetPrice) / (triggerPrice - latestClose)).toFixed(2))
                    : 0;
            }

            const alreadyTriggered = priceDirection === 'Falling'
                ? latestClose >= triggerPrice
                : latestClose <= triggerPrice;

            // Score components
            const score_p = Math.max(0, Math.min(100, Math.log1p(Math.abs(pChange)) / Math.log1p(15) * 100));
            const score_d = deliveryMetric === 'Pct'
              ? Math.max(0, Math.min(100, dChange / 30 * 100))
              : Math.max(0, Math.min(100, dChange / 200 * 100));
            const score_v = Math.max(0, Math.min(100, rVol / 2 * 100));

            let wP = 0.35, wD = 0.35, wV = 0.15, wT = 0.15;
            if (scoreWeighting === 'Price')    { wP = 0.45; wD = 0.25; wV = 0.15; wT = 0.15; }
            if (scoreWeighting === 'Delivery') { wP = 0.25; wD = 0.45; wV = 0.15; wT = 0.15; }

            const score_dar_bonus = dar > 0 ? Math.round(Math.min(10, (dar / 2) * 10)) : 0;
            const score = Math.round(wP * score_p + wD * score_d + wV * score_v + wT * baseTightness) + score_dar_bonus;

            let signalBadge: string;
            if (score >= 80 && dar >= 1) signalBadge = 'A';
            else if (score >= 60) signalBadge = 'B';
            else if (score >= 40) signalBadge = 'C';
            else signalBadge = 'D';

            results.push({
                symbol: d.ticker,
                sector: meta.sector,
                bucket: meta.bucket,
                priceChangePct: pChange,
                deliveryChangePct: dChange,
                relativeVolume: rVol,
                relativeStrength,
                position52W,
                score,
                consecutiveHighDeliveryDays: d.consecutiveHighDeliveryDays,
                detectedBaseLength: d.detected_base_length,
                triggerPrice,
                stopLossPrice,
                targetPrice,
                riskReward,
                latestClose,
                baseTightness,
                dar,
                alreadyTriggered,
                nearEarnings,
                latestDeliveryPct: d.latest_delivery_pct,
                signalBadge
            });
        });

        return results;
    }, [rawData, priceMetric, deliveryMetric, priceDirection, niftyChangePct, earningsProximitySet, metadataMap, scoreWeighting]);

    // Filter enriched data by slider/button controls (cheap — runs on every slider change)
    const filteredData = useMemo(() => {
        return enrichedData.filter(d => {
            if (priceDirection === 'Falling' && d.priceChangePct > minPriceChange) return false;
            if (priceDirection === 'Rising' && d.priceChangePct < minPriceChange) return false;
            if (priceDirection === 'Falling' && d.deliveryChangePct < minDeliveryChange) return false;
            if (priceDirection === 'Rising' && d.deliveryChangePct > minDeliveryChange) return false;
            if (d.latestDeliveryPct < minAbsDeliveryPct) return false;
            if (minConsecutiveDays > 0 && d.consecutiveHighDeliveryDays < minConsecutiveDays) return false;
            if (d.relativeVolume < minRelativeVolume) return false;
            if (minDAR > 0 && d.dar < minDAR) return false;
            if (d.score < minScore) return false;
            if (d.riskReward < minRR) return false;
            if (filterSector !== 'All' && d.sector !== filterSector) return false;
            if (filterMcap !== 'All' && d.bucket !== filterMcap) return false;
            if (mcapRange) {
                const mcap = mcapMapRef.current.get(d.symbol);
                if (mcap === undefined || mcap < mcapRange.min || mcap > mcapRange.max) return false;
            }
            if (filterRSNegative && d.relativeStrength >= 0) return false;
            if (d.position52W > maxPosition52W) return false;
            if (showWatchlistOnly && !watchlist.has(d.symbol)) return false;
            if (hideNearEarnings && d.nearEarnings) return false;
            return true;
        });
    }, [enrichedData, priceDirection, minPriceChange, minDeliveryChange, minAbsDeliveryPct, minConsecutiveDays, minRelativeVolume, minDAR, minScore, minRR, filterSector, filterMcap, mcapRange, filterRSNegative, maxPosition52W, showWatchlistOnly, watchlist, hideNearEarnings]);

    useEffect(() => {
        if (filteredData.length === 0) return;
        const currentSymbols = new Set(filteredData.map(d => d.symbol));
        const stored = sessionStorage.getItem('prev_divergence_scan');
        const prevSymbols: Set<string> = stored
            ? new Set(JSON.parse(stored))
            : new Set();
        const newOnes = new Set<string>();
        currentSymbols.forEach(s => {
            if (!prevSymbols.has(s)) newOnes.add(s);
        });
        setNewSymbols(newOnes);
        setPreviousSymbols(prevSymbols);
        sessionStorage.setItem('prev_divergence_scan', JSON.stringify(Array.from(currentSymbols)));
    }, [filteredData]);

    const sortedData = useMemo(() => {
        let data = showNewOnly
            ? filteredData.filter(d => newSymbols.has(d.symbol))
            : filteredData;
        if (!sortConfig) return data;
        return [...data].sort((a, b) => {
            const aVal = a[sortConfig.key];
            const bVal = b[sortConfig.key];
            if (aVal < bVal) return sortConfig.direction === 'asc' ? -1 : 1;
            if (aVal > bVal) return sortConfig.direction === 'asc' ? 1 : -1;
            return 0;
        });
    }, [filteredData, sortConfig, showNewOnly, newSymbols]);

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

    const exportCSV = useCallback(() => {
        if (sortedData.length === 0) return;
        const headers = ['Symbol', 'Sector', 'Bucket', 'Price Change%', 'RS vs N50', 'Del Change', 'DAR %', 'Consec Days', 'Base', 'Tightness', '52W Pos', 'Rel Vol', 'Score', 'Signal', 'Trigger', 'SL', 'R:R'];
        const rows = sortedData.map(d => [
            d.symbol,
            d.sector,
            d.bucket,
            d.priceChangePct.toFixed(2),
            d.relativeStrength.toFixed(2),
            d.deliveryChangePct.toFixed(1),
            d.dar.toFixed(4),
            d.consecutiveHighDeliveryDays,
            `${d.detectedBaseLength}d`,
            d.baseTightness,
            d.position52W.toFixed(1),
            d.relativeVolume.toFixed(2),
            d.score,
            d.signalBadge,
            d.triggerPrice.toFixed(2),
            d.stopLossPrice.toFixed(2),
            d.riskReward.toFixed(2)
        ]);
        const csv = [headers, ...rows].map(r => r.join(',')).join('\n');
        const blob = new Blob([csv], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `divergence_scan_${new Date().toISOString().slice(0, 10)}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    }, [sortedData]);

    const summaries = useMemo(() => {
        if (filteredData.length === 0) return { avgScore: 0, avgDel: 0, avgRR: 0, avgDAR: 0 };
        const sumScore = filteredData.reduce((acc, v) => acc + v.score, 0);
        const sumDel = filteredData.reduce((acc, v) => acc + v.deliveryChangePct, 0);
        const sumRR = filteredData.reduce((acc, v) => acc + v.riskReward, 0);
        const validDAR = filteredData.filter(v => v.dar > 0);
        const avgDAR = validDAR.length > 0
            ? parseFloat((validDAR.reduce((a, v) => a + v.dar, 0) / validDAR.length).toFixed(3))
            : 0;
        return {
            avgScore: Math.round(sumScore / filteredData.length),
            avgDel: sumDel / filteredData.length,
            avgRR: sumRR / filteredData.length,
            avgDAR
        };
    }, [filteredData]);

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
                        onClick={exportCSV}
                        disabled={sortedData.length === 0}
                        className="bg-[#2a2c34] hover:bg-[#3a3c44] text-[#aaa] hover:text-white px-2 py-1 rounded border border-[#ffffff1a] transition-all flex items-center gap-1 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                        <span className="text-xs">↓ CSV</span>
                    </button>
                    <button 
                        onClick={fetchData} 
                        className="bg-[#2a2c34] hover:bg-[#3a3c44] text-[#aaa] hover:text-white px-2 py-1 rounded border border-[#ffffff1a] transition-all flex items-center gap-1"
                        disabled={isLoading}
                    >
                        <RefreshCw size={12} className={isLoading ? "animate-spin" : ""} />
                        <span className="text-xs">Sync DB</span>
                    </button>
                    <button
                        onClick={() => { const n = !filtersVisible; setFiltersVisible(n); localStorage.setItem('pdd_filters_visible', String(n)); }}
                        className={`px-2.5 py-1 rounded text-[10px] font-mono border transition-all flex items-center gap-1 ${
                            filtersVisible
                                ? 'bg-[#2a2c34] border-[#ffffff3a] text-[#ccc]'
                                : 'bg-[#2a2c34] border-[#ffffff1a] text-[#888]'
                        }`}
                        title="Toggle filter controls"
                    >
                        Filters <ChevronDown size={12} className={`transition-transform ${filtersVisible ? '' : '-rotate-90'}`} />
                    </button>
                </div>
            </div>

            {/* Settings Panel */}
            {filtersVisible && (
                <div className="bg-[#15171d] border-b border-[#ffffff1a] p-4 flex flex-col gap-4">
                    <PresetChip
                        module="PriceDeliveryDivergence"
                        currentConfig={{
                            lookbackBars, priceMetric, deliveryMetric, priceDirection,
                            minPriceChange, minDeliveryChange, minRelativeVolume, minScore,
                            scoreWeighting, filterSector, filterMcap, minAbsDeliveryPct,
                            minConsecutiveDays, minRR, minDAR, maxPosition52W, filterRSNegative
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
                            setMinAbsDeliveryPct(c.minAbsDeliveryPct ?? 0);
                            setMinConsecutiveDays(c.minConsecutiveDays ?? 0);
                            setMinRR(c.minRR ?? 0);
                            setMinDAR(c.minDAR ?? 0);
                            setMaxPosition52W(c.maxPosition52W ?? 100);
                            setFilterRSNegative(c.filterRSNegative ?? false);
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
                                <span className="text-orange-400">{minPriceChangeDisplay}%</span>
                            </div>
                             <input type="range" min={priceDirection === 'Rising' ? 0 : -30} max={priceDirection === 'Rising' ? 30 : 0} value={minPriceChangeDisplay} onChange={(e) => { const v = Number(e.target.value); setMinPriceChangeDisplay(v); setMinPriceChangeDebounced(v); }} className="w-full accent-orange-500" />
                        </div>
                        
                        <div className="flex flex-col col-span-1 md:col-span-2 lg:col-span-1">
                           <div className="flex justify-between text-[10px] text-[#888] font-mono mb-0.5">
                                <label>Min Del Change {deliveryMetric === 'Pct' ? 'pp' : '%'}</label>
                                <span className="text-orange-400">{minDeliveryChangeDisplay}%</span>
                            </div>
                            <input type="range" min="-10" max="50" value={minDeliveryChangeDisplay} onChange={(e) => { const v = Number(e.target.value); setMinDeliveryChangeDisplay(v); setMinDeliveryChangeDebounced(v); }} className="w-full accent-orange-500" />
                        </div>

                        <div className="flex flex-col col-span-1 md:col-span-2 lg:col-span-1">
                            <div className="flex justify-between text-[10px] text-[#888] font-mono mb-0.5">
                                <label>Min Abs Delivery %</label>
                                <span className="text-orange-400">{minAbsDeliveryPct}%</span>
                            </div>
                            <input type="range" min="0" max="80" step="1" value={minAbsDeliveryPct} onChange={(e) => setMinAbsDeliveryPct(Number(e.target.value))} className="w-full accent-orange-500" />
                        </div>

                        <div className="flex flex-col col-span-1 md:col-span-2 lg:col-span-1">
                            <div className="flex justify-between text-[10px] text-[#888] font-mono mb-0.5">
                                <label>Min Consec. Sessions ≥40% Del</label>
                                <span className="text-orange-400">{minConsecutiveDays === 0 ? 'Off' : `${minConsecutiveDays}+`}</span>
                            </div>
                            <input type="range" min="0" max="5" step="1" value={minConsecutiveDays} onChange={(e) => setMinConsecutiveDays(Number(e.target.value))} className="w-full accent-orange-500" />
                        </div>

                        <div className="flex flex-col col-span-1 md:col-span-2 lg:col-span-1">
                            <div className="flex justify-between text-[10px] text-[#888] font-mono mb-0.5">
                                <label>Min R:R Ratio</label>
                                <span className="text-orange-400">{minRR === 0 ? 'Off' : minRR.toFixed(1)}</span>
                            </div>
                            <input type="range" min="0" max="5" step="0.5" value={minRR} onChange={(e) => setMinRR(Number(e.target.value))} className="w-full accent-orange-500" />
                        </div>

                        <div className="flex flex-col col-span-1 md:col-span-2 lg:col-span-1">
                            <div className="flex justify-between text-[10px] text-[#888] font-mono mb-0.5">
                                <label>Max 52W Position %</label>
                                <span className="text-orange-400">{maxPosition52W === 100 ? 'All' : `<${maxPosition52W}%`}</span>
                            </div>
                            <input type="range" min="10" max="100" step="5" value={maxPosition52W} onChange={(e) => setMaxPosition52W(Number(e.target.value))} className="w-full accent-orange-500" />
                        </div>

                        <div className="flex flex-col col-span-1 md:col-span-2 lg:col-span-1">
                            <div className="flex justify-between text-[10px] text-[#888] font-mono mb-0.5">
                                <label>RS vs N50</label>
                            </div>
                            <button
                                onClick={() => setFilterRSNegative(prev => !prev)}
                                className={`text-[11px] px-2 py-1 rounded border transition-colors font-mono ${
                                    filterRSNegative
                                        ? 'bg-orange-500/20 border-orange-500/50 text-orange-400'
                                        : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#888]'
                                }`}
                            >
                                {filterRSNegative ? 'Negative Only' : 'All Stocks'}
                            </button>
                        </div>

                        <div className="flex flex-col col-span-1 md:col-span-2 lg:col-span-1">
                            <div className="flex justify-between text-[10px] text-[#888] font-mono mb-0.5">
                                <label>Results Zone</label>
                            </div>
                            <button
                                onClick={() => setHideNearEarnings(f => !f)}
                                className={`text-[11px] px-2 py-1 rounded border transition-colors font-mono ${
                                    hideNearEarnings
                                        ? 'bg-yellow-500/20 border-yellow-500/50 text-yellow-400'
                                        : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#888]'
                                }`}
                            >
                                {hideNearEarnings ? 'Hide Results-Zone' : 'Show All'}
                            </button>
                        </div>

                        <div className="flex flex-col col-span-1 md:col-span-2 lg:col-span-1">
                            <div className="flex justify-between text-[10px] text-[#888] font-mono mb-0.5">
                                <label>Watchlist</label>
                            </div>
                            <button
                                onClick={() => setShowWatchlistOnly(f => !f)}
                                className={`text-[11px] px-2 py-1 rounded border transition-colors font-mono ${
                                    showWatchlistOnly
                                        ? 'bg-orange-500/20 border-orange-500/50 text-orange-400'
                                        : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#888]'
                                }`}
                            >
                                ★ {watchlist.size}
                            </button>
                        </div>

                        <div className="flex flex-col col-span-1 md:col-span-2 lg:col-span-1">
                            <div className="flex justify-between text-[10px] text-[#888] font-mono mb-0.5">
                                <label>New Signals</label>
                            </div>
                            <button
                                onClick={() => setShowNewOnly(f => !f)}
                                className={`text-[11px] px-2 py-1 rounded border transition-colors font-mono flex items-center gap-1 ${
                                    showNewOnly
                                        ? 'bg-orange-500/20 border-orange-500/50 text-orange-400'
                                        : 'bg-[#ffffff0a] border-[#ffffff1a] text-[#888]'
                                }`}
                            >
                                <span className={showNewOnly ? 'animate-pulse' : ''}>●</span>
                                New ({newSymbols.size})
                            </button>
                        </div>

                        <div className="flex flex-col col-span-1 md:col-span-2 lg:col-span-1">
                            <div className="flex justify-between text-[10px] text-[#888] font-mono mb-0.5">
                                <label>Min Rel Volume</label>
                                <span className="text-orange-400">{minRelativeVolumeDisplay}x</span>
                            </div>
                            <input type="range" min="0" max="5" step="0.1" value={minRelativeVolumeDisplay} onChange={(e) => { const v = Number(e.target.value); setMinRelativeVolumeDisplay(v); setMinRelativeVolumeDebounced(v); }} className="w-full accent-orange-500" />
                        </div>

                        <div className="flex flex-col col-span-1 md:col-span-2 lg:col-span-1">
                            <div className="flex justify-between text-[10px] text-[#888] font-mono mb-0.5">
                                <label>Min DAR %</label>
                                <span className="text-orange-400">
                                    {minDAR === 0 ? 'Off' : `≥${minDAR}%`}
                                </span>
                            </div>
                            <input
                                type="range" min="0" max="5" step="0.1"
                                value={minDAR}
                                onChange={(e) => setMinDAR(Number(e.target.value))}
                                className="w-full accent-orange-500"
                            />
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
                                <span className="text-orange-400">{minScoreDisplay}</span>
                            </div>
                            <input type="range" min="0" max="100" value={minScoreDisplay} onChange={(e) => { const v = Number(e.target.value); setMinScoreDisplay(v); setMinScoreDebounced(v); }} className="w-full accent-orange-500" />
                        </div>
                    </div>
                </div>
            )}

            {/* Summaries & Filters Row */}
            <div className="grid grid-cols-1 md:grid-cols-[1fr_min-content] gap-4 p-4 border-b border-[#ffffff1a] bg-[#1a1c24]">
                <div className="grid grid-cols-1 sm:grid-cols-5 gap-4">
                    <div className="bg-[#2a2c34] border border-[#ffffff1a] rounded p-3 flex flex-col justify-center">
                        <span className="text-xs text-[#888] font-mono mb-1">Divergence Signals</span>
                        <div className="flex items-end gap-2">
                            <span className="text-2xl text-orange-400 font-semibold">{filteredData.length}</span>
                            {newSymbols.size > 0 && (
                                <span className="text-sm text-orange-400/70 font-mono mb-0.5">+{newSymbols.size} new</span>
                            )}
                        </div>
                    </div>
                    <div className="bg-[#2a2c34] border border-[#ffffff1a] rounded p-3 flex flex-col justify-center">
                        <span className="text-xs text-[#888] font-mono mb-1">Average Score</span>
                        <span className="text-2xl text-[#fafafa] font-semibold">{summaries.avgScore}</span>
                    </div>
                    <div className="bg-[#2a2c34] border border-[#ffffff1a] rounded p-3 flex flex-col justify-center">
                        <span className="text-xs text-[#888] font-mono mb-1">Avg Delivery Change</span>
                        <span className="text-2xl text-[#fafafa] font-semibold">{summaries.avgDel > 0 ? '+' : ''}{summaries.avgDel.toFixed(1)}%</span>
                    </div>
                    <div className="bg-[#2a2c34] border border-[#ffffff1a] rounded p-3 flex flex-col justify-center">
                        <span className="text-xs text-[#888] font-mono mb-1">Avg DAR</span>
                        <span className={`text-2xl font-semibold ${
                            summaries.avgDAR >= 2   ? 'text-orange-400' :
                            summaries.avgDAR >= 0.5 ? 'text-green-400' : 'text-[#fafafa]'
                        }`}>
                            {summaries.avgDAR > 0 ? `${summaries.avgDAR.toFixed(2)}%` : '—'}
                        </span>
                    </div>
                    <div className="bg-[#2a2c34] border border-[#ffffff1a] rounded p-3 flex flex-col justify-center">
                        <span className="text-xs text-[#888] font-mono mb-1">Avg R:R Ratio</span>
                        <span className={`text-2xl font-semibold ${summaries.avgRR >= 2 ? 'text-green-400' : summaries.avgRR >= 1 ? 'text-yellow-400' : 'text-[#666]'}`}>{summaries.avgRR.toFixed(2)}</span>
                    </div>
                </div>
                <div className="flex flex-wrap gap-3 items-end">
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
                      <div className="max-w-[280px] flex-shrink-0">
                          <MarketCapRangeFilter onChange={setMcapRange} />
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
                            <tr className="bg-[#1a1c24] border-b border-[#ffffff1a]">
                                <th colSpan={17} className="p-1 text-[10px] text-[#888] font-mono text-left">
                                    <span className="text-[#555]">Nifty50</span>{' '}
                                    <span className={niftyChangePct >= 0 ? 'text-green-400' : 'text-red-400'}>
                                        {niftyChangePct >= 0 ? '+' : ''}{niftyChangePct.toFixed(2)}%
                                    </span>
                                </th>
                            </tr>
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
                                <th className={`p-3 text-[10px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right ${sortConfig?.key === 'relativeStrength' ? 'text-white' : ''}`} onClick={() => handleSort('relativeStrength')}>
                                    RS vs N50 <SortIcon column="relativeStrength" />
                                </th>
                                <th className={`p-3 text-[10px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right ${sortConfig?.key === 'deliveryChangePct' ? 'text-white' : ''}`} onClick={() => handleSort('deliveryChangePct')}>
                                    Del Change <SortIcon column="deliveryChangePct" />
                                </th>
                                <th className={`p-3 text-[10px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right ${sortConfig?.key === 'dar' ? 'text-white' : ''}`} onClick={() => handleSort('dar')}>
                                    DAR % <SortIcon column="dar" />
                                </th>
                                <th className={`p-3 text-[10px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right ${sortConfig?.key === 'consecutiveHighDeliveryDays' ? 'text-white' : ''}`} onClick={() => handleSort('consecutiveHighDeliveryDays')}>
                                    Consec. Days <SortIcon column="consecutiveHighDeliveryDays" />
                                </th>
                                <th className={`p-3 text-[10px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right ${sortConfig?.key === 'detectedBaseLength' ? 'text-white' : ''}`} onClick={() => handleSort('detectedBaseLength')}>
                                    Base <SortIcon column="detectedBaseLength" />
                                </th>
                                <th className={`p-3 text-[10px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right ${sortConfig?.key === 'baseTightness' ? 'text-white' : ''}`} onClick={() => handleSort('baseTightness')}>
                                    Tightness <SortIcon column="baseTightness" />
                                </th>
                                <th className={`p-3 text-[10px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right ${sortConfig?.key === 'position52W' ? 'text-white' : ''}`} onClick={() => handleSort('position52W')}>
                                    52W Pos <SortIcon column="position52W" />
                                </th>
                                <th className={`p-3 text-[10px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right ${sortConfig?.key === 'relativeVolume' ? 'text-white' : ''}`} onClick={() => handleSort('relativeVolume')}>
                                    Rel Volume <SortIcon column="relativeVolume" />
                                </th>
                                <th className={`p-3 text-[10px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap ${sortConfig?.key === 'score' ? 'text-white' : ''}`} onClick={() => handleSort('score')}>
                                    Score <SortIcon column="score" />
                                </th>
                                <th className={`p-3 text-[10px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap ${sortConfig?.key === 'signalBadge' ? 'text-white' : ''}`} onClick={() => handleSort('signalBadge')}>
                                    Signal <SortIcon column="signalBadge" />
                                </th>
                                <th className={`p-3 text-[10px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right ${sortConfig?.key === 'triggerPrice' ? 'text-white' : ''}`} onClick={() => handleSort('triggerPrice')}>
                                    Trigger <SortIcon column="triggerPrice" />
                                </th>
                                <th className={`p-3 text-[10px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right ${sortConfig?.key === 'stopLossPrice' ? 'text-white' : ''}`} onClick={() => handleSort('stopLossPrice')}>
                                    SL <SortIcon column="stopLossPrice" />
                                </th>
                                <th className={`p-3 text-[10px] font-medium uppercase text-[#888] font-mono cursor-pointer hover:text-white transition-colors whitespace-nowrap text-right ${sortConfig?.key === 'riskReward' ? 'text-white' : ''}`} onClick={() => handleSort('riskReward')}>
                                    R:R <SortIcon column="riskReward" />
                                </th>
                            </tr>
                        </thead>
                        <tbody>
                            {sortedData.length === 0 ? (
                                <tr>
                                        <td colSpan={17} className="p-8 text-center text-[#666] font-mono text-xs">
                                        No divergence signals match your strict criteria.
                                    </td>
                                </tr>
                            ) : (
                                (() => {
                                    const MAX_ROWS = 500;
                                    const displayData = sortedData.slice(0, MAX_ROWS);
                                    const overflow = sortedData.length > MAX_ROWS;
                                    return (
                                        <>
                                            {overflow && (
                                                <tr>
                                                    <td colSpan={17} className="px-3 py-1.5 text-[10px] text-center text-yellow-500 font-mono bg-yellow-500/5 border-b border-yellow-500/20">
                                                        Showing top {MAX_ROWS} of {sortedData.length} signals — tighten filters to see all
                                                    </td>
                                                </tr>
                                            )}
                                            {displayData.map(d => (
                                                <tr key={d.symbol} className="border-b border-[#ffffff0a] hover:bg-[#ffffff05] transition-colors group">
                                                    <td className="p-3 whitespace-nowrap">
                                                        <button
                                                            onClick={(e) => { e.stopPropagation(); toggleWatchlist(d.symbol); }}
                                                            className={`transition-colors mr-1 ${
                                                                watchlist.has(d.symbol)
                                                                    ? 'text-orange-400' : 'text-[#333] hover:text-[#888]'
                                                            }`}
                                                            title={watchlist.has(d.symbol) ? 'Remove from watchlist' : 'Add to watchlist'}
                                                        >
                                                            ★
                                                        </button>
                                                        <span 
                                                            onClick={() => window.open(`/#/chart?symbol=${encodeURIComponent(d.symbol)}`, '_blank')}
                                                            className="font-bold text-[#fafafa] cursor-pointer hover:text-orange-400 hover:underline inline-flex items-center gap-1 transition-colors"
                                                        >
                                                            {d.symbol}
                                                        </span>
                                                        {d.alreadyTriggered && priceDirection === 'Falling' && (
                                                            <span className="text-[9px] bg-green-500/20 text-green-400 px-1 rounded border border-green-500/30 font-mono ml-1">
                                                                TRIGGERED
                                                            </span>
                                                        )}
                                                        {d.alreadyTriggered && priceDirection === 'Rising' && (
                                                            <span className="text-[9px] bg-yellow-500/20 text-yellow-400 px-1 rounded border border-yellow-500/30 font-mono ml-1">
                                                                STILL RUNNING
                                                            </span>
                                                        )}
                                                        {d.nearEarnings && (
                                                            <span className="text-[9px] bg-yellow-500/20 text-yellow-400 px-1 rounded border border-yellow-500/30 font-mono ml-1" title="Quarterly results due within 10 days — delivery signal may be noise">
                                                                ⚠ RESULTS
                                                            </span>
                                                        )}
                                                        {newSymbols.has(d.symbol) && (
                                                            <span className="text-[9px] bg-orange-500/20 text-orange-400 px-1 rounded border border-orange-500/30 font-mono ml-1 animate-pulse">
                                                                NEW
                                                            </span>
                                                        )}
                                                        <button
                                                            onClick={(e) => { e.stopPropagation(); setBacktestSymbol(d.symbol); }}
                                                            className="opacity-0 group-hover:opacity-100 transition-opacity text-[#666] hover:text-orange-400 ml-1"
                                                            title="Backtest this symbol"
                                                        >
                                                            <BarChart2 size={12} />
                                                        </button>
                                                    </td>
                                                    <td className="p-3 text-[#ccc] text-sm whitespace-nowrap">{d.sector}</td>
                                                    <td className="p-3 text-[#888] text-xs font-mono whitespace-nowrap">{d.bucket}</td>
                                                    <td className="p-3 text-sm font-mono whitespace-nowrap text-right"><span className={d.priceChangePct >= 0 ? "text-green-400" : "text-red-400"}>{d.priceChangePct > 0 ? "+" : ""}{d.priceChangePct.toFixed(2)}%</span></td>
                                                    <td className={`p-3 text-sm font-mono whitespace-nowrap text-right ${d.relativeStrength >= 0 ? 'text-[#22c55e]' : 'text-[#ef4444]'}`}>{d.relativeStrength >= 0 ? '+' : ''}{d.relativeStrength.toFixed(2)}%</td>
                                                    <td className="p-3 text-sm font-mono whitespace-nowrap text-right"><span className={d.deliveryChangePct >= 0 ? "text-green-400" : "text-red-400"}>{d.deliveryChangePct > 0 ? "+" : ""}{d.deliveryChangePct.toFixed(1)}%</span></td>
                                                    <td className="p-3 text-xs font-mono whitespace-nowrap text-right">
                                                        <span className={
                                                            d.dar >= 2   ? 'text-orange-400 font-bold' :
                                                            d.dar >= 0.5 ? 'text-green-400' :
                                                            d.dar >= 0.1 ? 'text-[#fafafa]' :
                                                            d.dar > 0    ? 'text-[#555]' : 'text-[#333]'
                                                        }>
                                                            {d.dar > 0 ? `${d.dar.toFixed(2)}%` : '—'}
                                                        </span>
                                                    </td>
                                                    <td className="p-3 text-sm font-mono whitespace-nowrap text-right">
                                                        <span className={
                                                            d.consecutiveHighDeliveryDays >= 3 ? 'text-orange-400 font-bold' :
                                                            d.consecutiveHighDeliveryDays >= 1 ? 'text-[#fafafa]' : 'text-[#555]'
                                                        }>
                                                            {d.consecutiveHighDeliveryDays > 0 ? `${d.consecutiveHighDeliveryDays}d` : '—'}
                                                        </span>
                                                    </td>
                                                    <td className="p-3 text-xs font-mono whitespace-nowrap text-right">
                                                        <span className={`
                                                            ${d.detectedBaseLength === 45 ? 'text-orange-400 font-bold' : 
                                                              d.detectedBaseLength === 21 ? 'text-yellow-400' : 'text-[#888]'}
                                                        `}>
                                                            {d.detectedBaseLength}d
                                                        </span>
                                                    </td>
                                                    <td className="p-3 text-xs font-mono whitespace-nowrap text-right">
                                                        <span className={
                                                            d.baseTightness >= 75 ? 'text-green-400 font-bold' :
                                                            d.baseTightness >= 50 ? 'text-[#fafafa]' : 'text-[#555]'
                                                        }>
                                                            {d.baseTightness}
                                                        </span>
                                                    </td>
                                                    <td className="p-3 text-xs font-mono whitespace-nowrap text-right text-[#888]">{d.position52W.toFixed(1)}%</td>
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
                                                    <td className="p-3 whitespace-nowrap">
                                                        <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded font-mono ${
                                                            d.signalBadge === 'A' ? 'bg-green-500/20 text-green-400 border border-green-500/30' :
                                                            d.signalBadge === 'B' ? 'bg-blue-500/20 text-blue-400 border border-blue-500/30' :
                                                            d.signalBadge === 'C' ? 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/30' :
                                                            'bg-red-500/20 text-red-400 border border-red-500/30'
                                                        }`}>
                                                            {d.signalBadge}
                                                        </span>
                                                    </td>
                                                    <td className="p-3 text-sm font-mono whitespace-nowrap text-right text-[#fafafa]">{d.triggerPrice.toFixed(2)}</td>
                                                    <td className="p-3 text-sm font-mono whitespace-nowrap text-right text-red-400">{d.stopLossPrice.toFixed(2)}</td>
                                                    <td className="p-3 text-sm font-mono whitespace-nowrap text-right">
                                                        <span className={d.riskReward >= 2 ? 'text-green-400 font-bold' : d.riskReward >= 1 ? 'text-yellow-400' : 'text-[#555]'}>
                                                            {d.riskReward.toFixed(2)}
                                                        </span>
                                                    </td>
                                                </tr>
                                            ))}
                                        </>
                                    );
                                })()
                            )}
                        </tbody>
                    </table>
                )}
            </div>
            {backtestSymbol && (() => {
                const row = sortedData.find(d => d.symbol === backtestSymbol);
                if (!row) return null;
                return (
                    <BacktestPanel
                        lib={lib}
                        symbol={row.symbol}
                        entryPrice={row.triggerPrice}
                        stopLossPrice={row.stopLossPrice}
                        detectedBaseLength={row.detectedBaseLength}
                        minDeliveryChange={minDeliveryChange}
                        deliveryMetric={deliveryMetric}
                        onClose={() => setBacktestSymbol(null)}
                    />
                );
            })()}
        </div>
    );
}
