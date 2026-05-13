export type ConnectionStatus = {
  connected: boolean;
  path: string;
  error?: string;
};

export class Librarian {
  public health: Record<string, ConnectionStatus> = {};
  public apiUrl: string = 'http://localhost:8000/api'; // Target for your local MYRA repo
  public isConnectedToLocalRepo: boolean = false;
  
  // LRU / TTL In-Memory Cache for rapid UI tab switching 
  private queryCache: Map<string, { timestamp: number, data: any }> = new Map();
  private readonly CACHE_TTL_MS = 60000; // 60 seconds

  constructor() {
    console.log("Librarian engine initialized. Attemping connection to MYRA backend...");
    this.refreshSettings();
    this.checkBackendHealth();
  }

  // Reload URL from local storage injected by settings context
  public refreshSettings() {
    try {
      const saved = localStorage.getItem('myra_ui_settings');
      if (saved) {
        const settings = JSON.parse(saved);
        if (settings.apiEndpoint) {
          this.apiUrl = settings.apiEndpoint.replace(/\/$/, "");
        }
      }
    } catch(e) {
      // Ignored
    }
  }

  // Attempt to ping your local Python server
  private async checkBackendHealth() {
    try {
      const baseUrl = this.apiUrl.endsWith('/api') ? this.apiUrl.slice(0, -4) : this.apiUrl;
      const res = await fetch(`${baseUrl}/api/health`);
      if (res.ok) {
        const data = await res.json();
        this.isConnectedToLocalRepo = true;
        this.health = data.health || this.getFallbackHealth();
      } else {
        this.handleFallback();
      }
    } catch (error) {
      console.warn("Could not connect to local MYRA backend. Is your FastAPI server running? Falling back to demo mode.");
      this.handleFallback();
    }
  }

  private handleFallback() {
    this.isConnectedToLocalRepo = false;
    this.health = this.getFallbackHealth();
  }

  private getFallbackHealth() {
    return {
      _tech: { connected: true, path: './db/tech_sidecar.db' },
      _meta: { connected: true, path: './db/meta_sidecar.db' },
      _inst: { connected: true, path: './db/inst_sidecar.db' },
      _gov:  { 
        connected: true, 
        path: './db/gov_sidecar.db'
      }
    };
  }

  // Generic query executor that routes to your Python backend
  public async executeQuery(database: string, query: string, args: any = {}, timeoutMs: number = 8000): Promise<any> {
    const isDebug = localStorage.getItem('myra_ui_settings') ? JSON.parse(localStorage.getItem('myra_ui_settings') as string).debugMode : false;
    const startTime = performance.now();

    // DAILY-ADJUSTED: Log a warning if a query attempts to access intraday data
    if (query.toLowerCase().includes('minute_data') || query.toLowerCase().includes('tick_data')) {
      console.warn("WARNING: Backend provides ONLY daily OHLCV data. Request for minute_data or tick_data detected:", query);
    }

    if (isDebug) {
      console.log(`[Librarian SQL OUT] DB: ${database} | Query: ${query.length > 100 ? query.substring(0, 100) + '...' : query}`);
      // Dispatch custom event for our Debug Panel
      window.dispatchEvent(new CustomEvent('librarian-debug', {
          detail: {
              type: 'request',
              database,
              query,
              timestamp: Date.now()
          }
      }));
    }

    if (!this.isConnectedToLocalRepo) {
      // Return simulated data in demo mode based on query context to keep UI interactive
      return new Promise((resolve) => {
        setTimeout(() => {
          const q = query.toLowerCase();
          
          if (q.includes('distinct symbol')) {
             const term = q.match(/like '([^%]+)%'/i)?.[1] || '';
             const demoSymbols = ['RELIANCE', 'TCS', 'HDFCBANK', 'ICICIBANK', 'INFY', 'ITC', 'SBIN', 'BHARTIARTL', 'BAJFINANCE', 'KOTAKBANK', 'NIFTY', 'BANKNIFTY'];
             const matched = demoSymbols.filter(s => s.toLowerCase().startsWith(term.toLowerCase()));
             resolve(matched.map(s => ({ symbol: s })));
             return;
          }
          
          if (q.includes('technical_data') || q.includes('open, high, low')) {
             // Generate dummy OHLC data for charts
             const data = [];
             let currentPrice = 2500;
             const volumeBase = 5000000;
             
             for (let i = 0; i < 90; i++) {
               const date = new Date(Date.now() - (90 - i) * 24 * 60 * 60 * 1000);
               const volatility = currentPrice * 0.02; // 2% volatility
               
               const open = currentPrice + (Math.random() - 0.5) * volatility;
               const high = open + Math.random() * volatility;
               const low = open - Math.random() * volatility;
               const close = (open + high + low) / 3 + (Math.random() - 0.5) * volatility;
               
               currentPrice = close;
               
               const volume = Math.floor(volumeBase * (0.5 + Math.random()));
               const delivery_pct = 40 + Math.random() * 40; // 40-80% delivery
               const delivery_qty = Math.floor(volume * (delivery_pct / 100));
               
               data.push({
                 date: date.toISOString().split('T')[0],
                 open, high, low, close,
                 volume, volume_final: volume, trades: Math.floor(volume/100),
                 delivery_qty, delivery: delivery_qty, delivery_final: delivery_qty,
                 delivery_pct,
                 fvg_top: Math.random() > 0.8 ? high + volatility : null,
                 fvg_bottom: Math.random() > 0.8 ? low - volatility : null,
                 bullish_fvg: Math.random() > 0.9 ? 1 : 0,
                 bearish_fvg: Math.random() > 0.9 ? 1 : 0,
                 fvg_freshness: Math.random()
               });
             }
             resolve(data);
             return;
          }
          
          if (q.includes('sector_flow') || q.includes('sector')) {
             const sectors = ['IT', 'BANKS', 'AUTO', 'PHARMA', 'FMCG', 'REALTY', 'METAL', 'ENERGY'];
             const data = sectors.map(sec => ({
                 sector: sec,
                 net_delivery: (Math.random() - 0.3) * 10000000,
                 avg_accumulation: 45 + Math.random() * 30
             })).sort((a, b) => b.net_delivery - a.net_delivery);
             resolve(data);
             return;
          }
          
          // Default fallback mock data
          resolve([]);
        }, 600);
      });
    }

    // Generate unique stable cache key from query context
    const cacheKey = `${database}:${query.trim()}:${JSON.stringify(args || {})}`;
    const cachedHit = this.queryCache.get(cacheKey);
    
    // Serve from RAM if requested within TTL boundary 
    if (cachedHit && (Date.now() - cachedHit.timestamp < this.CACHE_TTL_MS)) {
      console.log(`[Librarian] Serving Cache-Hit for: ${database}`);
      return cachedHit.data;
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

    let targetDb = database;

    try {
      const baseUrl = this.apiUrl.endsWith('/api') ? this.apiUrl.slice(0, -4) : this.apiUrl;
      const res = await fetch(`${baseUrl}/api/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          db: targetDb, 
          query: query, 
          params: Array.isArray(args) ? args : [] 
        }),
        signal: controller.signal
      });
      clearTimeout(timeoutId);
      
      if (!res.ok) throw new Error(`Database query failed (Status ${res.status})`);
      
      const json = await res.json();
      // Unwrap {data: [...]} envelope from FastAPI
      const payload = Array.isArray(json) ? json : (json.data ?? json);
      
      // Store successful payload into RAM
      this.queryCache.set(cacheKey, { timestamp: Date.now(), data: payload });
      if (isDebug) {
          const duration = performance.now() - startTime;
          console.log(`[Librarian SQL IN] Took ${(duration).toFixed(1)}ms. Rows: ${payload.length}`);
          window.dispatchEvent(new CustomEvent('librarian-debug', {
              detail: {
                  type: 'response',
                  database,
                  query,
                  duration,
                  rows: payload.length,
                  timestamp: Date.now()
              }
          }));
      }
      return payload;
    } catch (e: any) {
      clearTimeout(timeoutId);
      if (isDebug) {
          const duration = performance.now() - startTime;
          console.error(`[Librarian SQL ERROR] Took ${(duration).toFixed(1)}ms. Error: `, e);
          window.dispatchEvent(new CustomEvent('librarian-debug', {
              detail: {
                  type: 'error',
                  database,
                  query,
                  duration,
                  error: e.message || String(e),
                  timestamp: Date.now()
              }
          }));
      }
      if (e.name === 'AbortError') {
         console.warn(`[Librarian] Query timed out after ${timeoutMs}ms:`, query.substring(0, 50) + '...');
         throw new Error("Local backend timeout. Operation aborted.");
      }
      console.error("[Librarian] Execution Exception:", e);
      throw e;
    }
  }
}

let librarianInstance: Librarian | null = null;

export const getLibrarian = () => {
  if (!librarianInstance) {
    librarianInstance = new Librarian();
  }
  return librarianInstance;
};
