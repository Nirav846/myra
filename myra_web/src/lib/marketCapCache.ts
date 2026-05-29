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
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            db: '_val_conn',
            query: 'SELECT symbol, market_cap FROM fundamentals WHERE market_cap IS NOT NULL',
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
