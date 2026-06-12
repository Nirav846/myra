let mcapMap: Map<string, number> | null = null;

function getApiUrl(): string {
    try {
        const saved = localStorage.getItem('myra_ui_settings');
        if (saved) {
            const settings = JSON.parse(saved);
            if (settings.apiEndpoint) {
                return settings.apiEndpoint.replace(/\/$/, '');
            }
        }
    } catch {}
    return 'http://localhost:8000/api';
}

export async function fetchMarketCapMap(): Promise<Map<string, number>> {
    if (mcapMap) return mcapMap;

    const apiUrl = getApiUrl();
    const res = await fetch(`${apiUrl}/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Myra-Auth': 'myra-local-dev-2026' },
        body: JSON.stringify({
            db: '_val_conn',
            query: 'SELECT symbol, market_cap AS market_cap FROM fundamentals WHERE market_cap IS NOT NULL AND market_cap > 0',
            params: []
        })
    });

    if (!res.ok) throw new Error(`Market cap fetch failed (Status ${res.status})`);

    const json = await res.json();
    const rows = Array.isArray(json) ? json : (json.data ?? json);

    const map = new Map<string, number>();
    for (const row of rows) {
        map.set(row.symbol, Number(row.market_cap));
    }

    mcapMap = map;
    return map;
}

export function clearMarketCapCache(): void {
    mcapMap = null;
}

export async function fetchFreeFloatMcapMap(): Promise<Map<string, number>> {
    try {
        const apiUrl = getApiUrl();
        const res = await fetch(`${apiUrl}/query`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Myra-Auth': 'myra-local-dev-2026' },
            body: JSON.stringify({
                db: '_val_conn',
                query: `
                    SELECT symbol,
                           COALESCE(marketCap, market_cap, 0)   AS mcap,
                           COALESCE(free_float_pct, 100.0)      AS ff_pct
                    FROM fundamentals
                    WHERE COALESCE(marketCap, market_cap, 0) > 0
                    LIMIT 10000
                `,
                params: []
            })
        });
        const json = await res.json();
        const rows = Array.isArray(json) ? json : (json.data ?? []);
        const map = new Map<string, number>();
        rows.forEach((r: any) => {
            const mcap  = Number(r.mcap) || 0;
            const ffPct = Math.min(100, Math.max(0, Number(r.ff_pct) || 100));
            const ffMcap = mcap * (ffPct / 100);
            if (ffMcap > 0) map.set(String(r.symbol), ffMcap);
        });
        return map;
    } catch {
        return new Map();
    }
}
