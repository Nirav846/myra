import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { Librarian } from '../lib/Librarian';
import { useSettings } from '../lib/SettingsContext';
import { resolveBucket } from '../lib/bucketUtils';
import { fetchMarketCapMap } from '../lib/marketCapCache';

export interface ScannerData {
    symbol: string;
    date: string;
    close: number;
    anomaly_close: number;
    return_since: number;
    delivery_pct: number;
    delivery_value_cr: number;
    volume_to_mcap_pct: number;
    delivery_divergence_score: number;
    volatility_compression_score: number;
    relative_volume_score: number;
    nifty_outperformance_score: number;
    composite_score: number;
    composite_badge: { text: string; className: string };
    strength: number | null;
    volume: number;
    sector: string;
    bucket: string;
}

export interface SummaryData {
    symbol: string;
    sector: string;
    bucket: string;
    persistence: number;
    latestDate: string;
    highestComposite: number;
    highestBadge: { text: string; className: string };
    avgDelivery: number;
    avgStrength: number | null;
    returnSinceEarliest: number;
    close: number;
    volume: number;
}

interface RawRow {
    symbol: string;
    date: string;
    anomaly_close: number;
    high: number;
    low: number;
    delivery: number;
    delivery_pct: number;
    delivery_divergence_score: number;
    volatility_compression_score: number;
    relative_volume_score: number;
    nifty_outperformance_score: number;
    volume: number;
}

interface SortConfig {
    key: keyof ScannerData;
    direction: 'asc' | 'desc';
}

export interface ScannerStats {
    count: number;
    avgDeliveryPct: string;
    avgReturnSince: string;
    topSector: string;
    topSectorCount: number;
}

const COLUMN_VISIBILITY_KEY = 'das_columns';

function loadColumnVisibility(): Record<string, boolean> {
    try {
        const saved = localStorage.getItem(COLUMN_VISIBILITY_KEY);
        if (saved) {
            const parsed = JSON.parse(saved);
            return {
                composite_score: parsed.composite_score ?? true,
                return_since: parsed.return_since ?? true,
                volatility_compression_score: parsed.volatility_compression_score ?? true,
                nifty_outperformance_score: parsed.nifty_outperformance_score ?? true,
                strength: parsed.strength ?? true,
            };
        }
    } catch {}
    return {
        composite_score: true,
        return_since: true,
        volatility_compression_score: true,
        nifty_outperformance_score: true,
        strength: true,
    };
}

export function useDeliveryScanner(lib: Librarian, mcapRange?: { min: number; max: number } | null) {
    const { settings } = useSettings();

    const [rawData, setRawData] = useState<RawRow[]>([]);
    const [closeMap, setCloseMap] = useState<Map<string, number>>(new Map());
    const [metadataMap, setMetadataMap] = useState<Map<string, { sector: string, bucket: string }>>(new Map());
    const [metadataLoaded, setMetadataLoaded] = useState(false);

    const [isLoading, setIsLoading] = useState(false);
    const [errorMsg, setErrorMsg] = useState<string | null>(null);
    const [hasRun, setHasRun] = useState(false);
    const [lastScanned, setLastScanned] = useState<Date | null>(null);

    const [minDeliveryPct, setMinDeliveryPct] = useState(0);
    const [maxDeliveryPct, setMaxDeliveryPct] = useState(100);
    const [minRelVolScore, setMinRelVolScore] = useState(0);
    const [minDeliveryValueCr, setMinDeliveryValueCr] = useState(0);
    const [minVolumeToMcap, setMinVolumeToMcap] = useState(0);
    const [filterSector, setFilterSector] = useState('All');
    const [lookbackDays, setLookbackDays] = useState(30);
    const [symbolSearch, setSymbolSearch] = useState('');
    const [filterBucket, setFilterBucket] = useState('All Caps');

    const [sortConfig, setSortConfig] = useState<SortConfig | null>({ key: 'delivery_divergence_score', direction: 'desc' });

    const [columnVisibility, setColumnVisibility] = useState<Record<string, boolean>>(loadColumnVisibility);

    useEffect(() => {
        localStorage.setItem(COLUMN_VISIBILITY_KEY, JSON.stringify(columnVisibility));
    }, [columnVisibility]);

    const [triggerMode, setTriggerMode] = useState(false);
    const [triggerMaxDays, setTriggerMaxDays] = useState(7);
    const [triggerMinStrength, setTriggerMinStrength] = useState(0.5);
    const [triggerMinComposite, setTriggerMinComposite] = useState(8);
    const [triggerMinReturn, setTriggerMinReturn] = useState(-5);
    const [triggerRequirePersistence, setTriggerRequirePersistence] = useState(false);

    const fetchIdRef = useRef(0);
    const mcapMapRef = useRef<Map<string, number> | null>(null);

    useEffect(() => {
        fetchMarketCapMap().then(map => {
            mcapMapRef.current = map;
        }).catch(() => {});
    }, []);

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
                            bucket
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

        const fetchId = ++fetchIdRef.current;

        setIsLoading(true);
        setErrorMsg(null);
        setHasRun(true);

        const safeDays = Math.max(1, Math.min(90, Math.floor(Number(lookbackDays) || 30)));

        const anomalyQuery = `
            SELECT symbol, date, close as anomaly_close, high, low, delivery, delivery_pct,
                   delivery_divergence_score, volatility_compression_score,
                   relative_volume_score, nifty_outperformance_score, volume
            FROM technical_data
            WHERE date >= date('now', '-${safeDays} days')
              AND delivery_pct IS NOT NULL
              AND delivery_divergence_score IS NOT NULL
            ORDER BY delivery_divergence_score DESC
            LIMIT 500
        `;

        try {
            const anomalyRows = await lib.executeQuery('_tech_conn', anomalyQuery, [], 15000);

            if (fetchId !== fetchIdRef.current) return;

            if (anomalyRows && anomalyRows.length > 0) {
                const symbols = anomalyRows.map((r: any) => r.symbol);
                const placeholders = symbols.map(() => '?').join(', ');
                const latestQuery = `
                    SELECT symbol, close as latest_close
                    FROM technical_data
                    WHERE (symbol, date) IN (
                        SELECT symbol, MAX(date)
                        FROM technical_data
                        WHERE symbol IN (${placeholders})
                        GROUP BY symbol
                    )
                `;
                const latestRows = await lib.executeQuery('_tech_conn', latestQuery, symbols, 10000);

                if (fetchId !== fetchIdRef.current) return;

                const map = new Map<string, number>();
                if (latestRows && Array.isArray(latestRows)) {
                    for (const r of latestRows) {
                        map.set(r.symbol, Number(r.latest_close));
                    }
                }

                setCloseMap(map);
                setRawData(anomalyRows.map((r: any) => ({
                    symbol: r.symbol,
                    date: r.date,
                    anomaly_close: Number(r.anomaly_close) || 0,
                    high: Number(r.high) || 0,
                    low: Number(r.low) || 0,
                    delivery: Number(r.delivery) || 0,
                    delivery_pct: Number(r.delivery_pct) || 0,
                    delivery_divergence_score: Number(r.delivery_divergence_score) || 0,
                    volatility_compression_score: Number(r.volatility_compression_score) || 0,
                    relative_volume_score: Number(r.relative_volume_score) || 0,
                    nifty_outperformance_score: Number(r.nifty_outperformance_score) || 0,
                    volume: Number(r.volume) || 0
                })));
                setLastScanned(new Date());
            } else {
                setRawData([]);
                setCloseMap(new Map());
                setLastScanned(new Date());
            }
        } catch (e: any) {
            if (fetchId !== fetchIdRef.current) return;
            console.error(e);
            setErrorMsg(e.message || 'Query failed');
            setRawData([]);
            setCloseMap(new Map());
        } finally {
            if (fetchId === fetchIdRef.current) {
                setIsLoading(false);
            }
        }
    }, [lookbackDays, metadataLoaded, lib, minDeliveryPct, maxDeliveryPct, minRelVolScore, filterSector, filterBucket, mcapRange]);

    const uniqueSectors = useMemo(() => {
        const s = new Set<string>();
        for (const meta of metadataMap.values()) {
            if (meta.sector) s.add(meta.sector);
        }
        return Array.from(s).sort();
    }, [metadataMap]);

    const uniqueBuckets = useMemo(() => {
        const s = new Set<string>();
        for (const meta of metadataMap.values()) {
            if (meta.bucket) s.add(meta.bucket);
        }
        return Array.from(s).filter(Boolean).sort();
    }, [metadataMap]);

    const processedData = useMemo(() => {
        const results: ScannerData[] = [];

        rawData.forEach(d => {
            if (d.delivery_pct < minDeliveryPct || d.delivery_pct > maxDeliveryPct) return;
            if (d.relative_volume_score < minRelVolScore) return;

            const meta = metadataMap.get(d.symbol) || { sector: 'Unknown', bucket: 'Deep Frontier' };
            if (filterSector !== 'All' && meta.sector !== filterSector) return;
            if (filterBucket !== 'All Caps' && meta.bucket !== filterBucket) return;

            if (mcapRange) {
                const mcap = mcapMapRef.current?.get(d.symbol);
                if (mcap == null || mcap < mcapRange.min || mcap > mcapRange.max) return;
            }

            const search = symbolSearch.trim().toUpperCase();
            if (search) {
                const sym = d.symbol.toUpperCase();
                if (!sym.startsWith(search) && !sym.includes(search)) return;
            }

            const close = closeMap.get(d.symbol) ?? null;
            const anomalyClose = d.anomaly_close;
            const returnSince = close != null && anomalyClose > 0
                ? ((close - anomalyClose) / anomalyClose) * 100
                : 0;

            const deliveryValueCr = (d.delivery * d.anomaly_close) / 1e7;
            const mcap = mcapMapRef.current?.get(d.symbol);
            const volumeToMcap = mcap && mcap > 0 ? (d.volume / mcap) * 100 : 0;

            if (minDeliveryValueCr > 0 && deliveryValueCr < minDeliveryValueCr) return;
            if (minVolumeToMcap > 0 && volumeToMcap < minVolumeToMcap) return;

            const compositeScore =
                d.delivery_divergence_score * 0.40 +
                d.volatility_compression_score * 0.20 +
                d.relative_volume_score * 0.25 +
                d.nifty_outperformance_score * 0.15;

            // Size-aware modifier: boost large delivery values, penalise tiny ones
            let sizeModifier = 0;
            if (deliveryValueCr >= 100) sizeModifier = 10;       // ₹100 Cr+ delivery = strong institutional
            else if (deliveryValueCr >= 10) sizeModifier = 5;     // ₹10 Cr+ = meaningful
            else if (deliveryValueCr < 0.1 && deliveryValueCr > 0) sizeModifier = -5;  // <₹10 L = likely noise

            // Volume/Mcap bonus: extreme turnover relative to size
            if (volumeToMcap > 10) sizeModifier += 5;             // >10% of mcap traded = extraordinary
            else if (volumeToMcap > 5) sizeModifier += 2;

            const adjustedScore = compositeScore + sizeModifier;
            const roundedScore = Math.round(adjustedScore * 10) / 10;
            const badge = roundedScore >= 15
                ? { text: 'STRONG', className: 'bg-green-500/20 text-green-400' }
                : roundedScore >= 8
                ? { text: 'SETUP', className: 'bg-yellow-500/20 text-yellow-400' }
                : { text: 'WATCH', className: 'bg-[#ffffff0a] text-[#888]' };

            const strength = d.high > d.low && d.anomaly_close > 0
                ? Math.round(((d.anomaly_close - d.low) / (d.high - d.low)) * 1000) / 1000
                : null;

            results.push({
                symbol: d.symbol,
                date: d.date,
                close: close ?? 0,
                anomaly_close: anomalyClose,
                return_since: returnSince,
                delivery_pct: d.delivery_pct,
                delivery_value_cr: deliveryValueCr,
                volume_to_mcap_pct: volumeToMcap,
                delivery_divergence_score: d.delivery_divergence_score,
                volatility_compression_score: d.volatility_compression_score,
                relative_volume_score: d.relative_volume_score,
                nifty_outperformance_score: d.nifty_outperformance_score,
                composite_score: roundedScore,
                composite_badge: badge,
                strength,
                volume: d.volume,
                sector: meta.sector,
                bucket: meta.bucket
            });
        });

        return results;
    }, [rawData, minDeliveryPct, maxDeliveryPct, minRelVolScore, minDeliveryValueCr, minVolumeToMcap, filterSector, filterBucket, symbolSearch, metadataMap, closeMap, mcapRange]);

    const maxRelVolObserved = useMemo(() => {
        let max = 0;
        for (const d of processedData) {
            if (d.relative_volume_score > max) max = d.relative_volume_score;
        }
        return max;
    }, [processedData]);

    const summaryData = useMemo((): SummaryData[] => {
        const map = new Map<string, ScannerData[]>();
        for (const d of processedData) {
            const arr = map.get(d.symbol);
            if (arr) arr.push(d);
            else map.set(d.symbol, [d]);
        }

        const result: SummaryData[] = [];
        for (const [symbol, rows] of map) {
            const sorted = rows.sort((a, b) => a.date.localeCompare(b.date));
            const latest = sorted[sorted.length - 1];
            const earliest = sorted[0];

            let sumStrength = 0;
            let strengthCount = 0;
            let highestComposite = -Infinity;
            let sumDelivery = 0;

            for (const r of rows) {
                sumDelivery += r.delivery_pct;
                if (r.strength !== null) { sumStrength += r.strength; strengthCount++; }
                if (r.composite_score > highestComposite) highestComposite = r.composite_score;
            }

            const badge = highestComposite >= 15
                ? { text: 'STRONG', className: 'bg-green-500/20 text-green-400' }
                : highestComposite >= 8
                ? { text: 'SETUP', className: 'bg-yellow-500/20 text-yellow-400' }
                : { text: 'WATCH', className: 'bg-[#ffffff0a] text-[#888]' };

            const returnSinceEarliest = earliest.anomaly_close > 0 && latest.close > 0
                ? ((latest.close - earliest.anomaly_close) / earliest.anomaly_close) * 100
                : 0;

            result.push({
                symbol,
                sector: latest.sector,
                bucket: latest.bucket,
                persistence: rows.length,
                latestDate: latest.date,
                highestComposite: Math.round(highestComposite * 10) / 10,
                highestBadge: badge,
                avgDelivery: Math.round((sumDelivery / rows.length) * 10) / 10,
                avgStrength: strengthCount > 0 ? Math.round((sumStrength / strengthCount) * 1000) / 1000 : null,
                returnSinceEarliest: Math.round(returnSinceEarliest * 10) / 10,
                close: latest.close,
                volume: rows.reduce((acc, r) => acc + r.volume, 0),
            });
        }

        return result;
    }, [processedData]);

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

    const stats = useMemo((): ScannerStats => {
        const count = processedData.length;
        if (count === 0) {
            return { count: 0, avgDeliveryPct: '0.0', avgReturnSince: '0.0', topSector: '—', topSectorCount: 0 };
        }

        let totalDelivery = 0;
        let totalReturn = 0;
        const sectorCounts = new Map<string, number>();

        for (const d of processedData) {
            totalDelivery += d.delivery_pct;
            totalReturn += d.return_since;
            sectorCounts.set(d.sector, (sectorCounts.get(d.sector) || 0) + 1);
        }

        let topSector = '—';
        let topSectorCount = 0;
        for (const [sector, count] of sectorCounts) {
            if (count > topSectorCount) {
                topSector = sector;
                topSectorCount = count;
            }
        }

        return {
            count,
            avgDeliveryPct: (totalDelivery / count).toFixed(1),
            avgReturnSince: (totalReturn / count).toFixed(1),
            topSector,
            topSectorCount
        };
    }, [processedData]);

    const latestDataDate = useMemo(() => {
        let latest: string | null = null;
        for (const d of processedData) {
            if (!latest || d.date > latest) latest = d.date;
        }
        return latest;
    }, [processedData]);

    const triggerFilteredData = useMemo(() => {
        if (!triggerMode) return [];
        if (!latestDataDate) return [];
        const latestD = new Date(latestDataDate + 'T00:00:00');
        latestD.setDate(latestD.getDate() - triggerMaxDays);
        const cutoffStr = latestD.toISOString().split('T')[0];

        const persistenceCount = new Map<string, number>();
        if (triggerRequirePersistence) {
            for (const d of processedData) {
                persistenceCount.set(d.symbol, (persistenceCount.get(d.symbol) || 0) + 1);
            }
        }

        return processedData.filter(d => {
            if (d.date < cutoffStr) return false;
            if (d.strength === null || d.strength < triggerMinStrength) return false;
            if (d.composite_score < triggerMinComposite) return false;
            if (d.return_since < triggerMinReturn) return false;
            if (triggerRequirePersistence && (persistenceCount.get(d.symbol) || 0) < 2) return false;
            return true;
        });
    }, [processedData, triggerMode, triggerMaxDays, triggerMinStrength, triggerMinComposite, triggerMinReturn, triggerRequirePersistence, latestDataDate]);

    const triggerSortedData = useMemo(() => {
        if (!sortConfig) return triggerFilteredData;
        return [...triggerFilteredData].sort((a, b) => {
            const aVal = a[sortConfig.key];
            const bVal = b[sortConfig.key];
            if (aVal < bVal) return sortConfig.direction === 'asc' ? -1 : 1;
            if (aVal > bVal) return sortConfig.direction === 'asc' ? 1 : -1;
            return 0;
        });
    }, [triggerFilteredData, sortConfig]);

    const triggerSummaryData = useMemo((): SummaryData[] => {
        const map = new Map<string, ScannerData[]>();
        for (const d of triggerFilteredData) {
            const arr = map.get(d.symbol);
            if (arr) arr.push(d);
            else map.set(d.symbol, [d]);
        }

        const result: SummaryData[] = [];
        for (const [symbol, rows] of map) {
            const sorted = rows.sort((a, b) => a.date.localeCompare(b.date));
            const latest = sorted[sorted.length - 1];
            const earliest = sorted[0];

            let sumStrength = 0;
            let strengthCount = 0;
            let highestComposite = -Infinity;
            let sumDelivery = 0;

            for (const r of rows) {
                sumDelivery += r.delivery_pct;
                if (r.strength !== null) { sumStrength += r.strength; strengthCount++; }
                if (r.composite_score > highestComposite) highestComposite = r.composite_score;
            }

            const badge = highestComposite >= 15
                ? { text: 'STRONG', className: 'bg-green-500/20 text-green-400' }
                : highestComposite >= 8
                ? { text: 'SETUP', className: 'bg-yellow-500/20 text-yellow-400' }
                : { text: 'WATCH', className: 'bg-[#ffffff0a] text-[#888]' };

            const returnSinceEarliest = earliest.anomaly_close > 0 && latest.close > 0
                ? ((latest.close - earliest.anomaly_close) / earliest.anomaly_close) * 100
                : 0;

            result.push({
                symbol,
                sector: latest.sector,
                bucket: latest.bucket,
                persistence: rows.length,
                latestDate: latest.date,
                highestComposite: Math.round(highestComposite * 10) / 10,
                highestBadge: badge,
                avgDelivery: Math.round((sumDelivery / rows.length) * 10) / 10,
                avgStrength: strengthCount > 0 ? Math.round((sumStrength / strengthCount) * 1000) / 1000 : null,
                returnSinceEarliest: Math.round(returnSinceEarliest * 10) / 10,
                close: latest.close,
                volume: rows.reduce((acc, r) => acc + r.volume, 0),
            });
        }

        return result;
    }, [triggerFilteredData]);

    return {
        sortedData,
        processedData,
        summaryData,
        isLoading,
        errorMsg,
        hasRun,
        lastScanned,
        fetchData,
        minDeliveryPct, setMinDeliveryPct,
        maxDeliveryPct, setMaxDeliveryPct,
        minRelVolScore, setMinRelVolScore,
        minDeliveryValueCr, setMinDeliveryValueCr,
        minVolumeToMcap, setMinVolumeToMcap,
        filterSector, setFilterSector,
        lookbackDays, setLookbackDays,
        symbolSearch, setSymbolSearch,
        filterBucket, setFilterBucket,
        sortConfig, setSortConfig,
        uniqueSectors,
        uniqueBuckets,
        metadataMap,
        stats,
        columnVisibility, setColumnVisibility,
        maxRelVolObserved,
        triggerMode, setTriggerMode,
        triggerMaxDays, setTriggerMaxDays,
        triggerMinStrength, setTriggerMinStrength,
        triggerMinComposite, setTriggerMinComposite,
        triggerMinReturn, setTriggerMinReturn,
        triggerRequirePersistence, setTriggerRequirePersistence,
        latestDataDate,
        triggerSortedData,
        triggerSummaryData,
    };
}
