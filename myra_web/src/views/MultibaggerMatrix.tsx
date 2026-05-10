import { useState, useEffect, useMemo } from "react";
import { Librarian } from "../lib/Librarian";
import { Rocket, ShieldAlert, Zap, ArrowUpDown } from "lucide-react";
import {
  ResponsiveContainer,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  ZAxis,
  Tooltip,
  CartesianGrid,
  Cell,
  ReferenceLine,
} from "recharts";

export default function MultibaggerMatrixView({ lib }: { lib: Librarian }) {
  const [data, setData] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isDemo, setIsDemo] = useState(false);

  // Dynamic States for Toggles
  const [excludeCyclical, setExcludeCyclical] = useState(true);
  const [requireAccumulation, setRequireAccumulation] = useState(true);
  const [minRoe, setMinRoe] = useState(10);
  const [minEps, setMinEps] = useState(10);
  const [days, setDays] = useState(180);
  const [filterMcap, setFilterMcap] = useState<string>("All");

  // Sorting State
  const [sortConfig, setSortConfig] = useState<{
    key: string;
    direction: "asc" | "desc";
  }>({ key: "multibagger_index", direction: "desc" });

  useEffect(() => {
    fetchMultibaggerCandidates();
  }, [excludeCyclical, requireAccumulation, minRoe, minEps, days, filterMcap]);

  const fetchMultibaggerCandidates = async () => {
    setIsLoading(true);
    setIsDemo(!lib.isConnectedToLocalRepo);

    try {
      // 1. Fetch symbols & indices for bucket mapping
      const symbolsQuery = `SELECT symbol as ticker, sector, in_nifty500 FROM symbols_master`;
      const symbolsResult = await lib.executeQuery(
        "_meta_conn",
        symbolsQuery,
        {},
        12000,
      );

      const indexResult = await lib.executeQuery(
        "_meta_conn",
        "SELECT symbol, index_name FROM index_constituents LIMIT 5000",
      );
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

      const metaMap = new Map();
      if (symbolsResult) {
        for (const m of symbolsResult) {
          const indices = indicesMap.get(m.ticker) || [];
          let bucket = "Deep Frontier";
          if (
            indices.some(
              (i: string) => i.includes("NIFTY 50") && !i.includes("NEXT"),
            )
          ) {
            bucket = "Large Cap (N50)";
          } else if (indices.some((i: string) => i.includes("NIFTY NEXT 50"))) {
            bucket = "Large Cap (N100)";
          } else if (
            m.in_nifty500 === 1 ||
            indices.some((i: string) => i.includes("NIFTY 500"))
          ) {
            bucket = "Broader Market (N500)";
          }
          metaMap.set(m.ticker, {
            sector: m.sector,
            bucket: bucket,
          });
        }
      }

      // 2. Fetch fundamentals
      let fundQuery = `
        SELECT symbol as ticker, sector, returnOnEquity, earningsPerShare, peRatio
        FROM fundamentals
        WHERE returnOnEquity > ${minRoe} AND earningsPerShare > ${minEps}
      `;
      if (excludeCyclical) {
        fundQuery += ` AND sector NOT IN ('Metals', 'Chemicals', 'Energy', 'Mining', 'Materials')`;
      }
      const fundResult = await lib.executeQuery(
        "_val_conn",
        fundQuery,
        {},
        12000,
      );

      if (fundResult && fundResult.length > 0) {
        const candidateTickers = fundResult
          .filter((r: any) => {
            const bucket = metaMap.get(r.ticker)?.bucket || "Deep Frontier";
            return filterMcap === "All" || bucket === filterMcap;
          })
          .map((r: any) => `'${r.ticker}'`);

        if (candidateTickers.length === 0) {
          setData([]);
          setIsLoading(false);
          return;
        }

        const tickersStr = candidateTickers.join(",");

        // 3. Fetch Accumulation
        const techQuery = `
          SELECT symbol as ticker, AVG((delivery * 100.0) / NULLIF(volume, 0)) as accumulation
          FROM technical_data
          WHERE date >= date('now', '-${days} days') AND symbol IN (${tickersStr})
          GROUP BY symbol
        `;
        const techResult = await lib.executeQuery(
          "_tech_conn",
          techQuery,
          {},
          12000,
        );

        const techMap = new Map();
        if (techResult && techResult.length > 0) {
          for (const t of techResult) {
            techMap.set(t.ticker, t.accumulation || 0);
          }
        }

        // 4. Fetch price to calculate exact earnings yield from technicals, or fallback to PE
        const priceQuery = `
          SELECT symbol as ticker, close FROM technical_data 
          WHERE date = (SELECT MAX(date) FROM technical_data) AND symbol IN (${tickersStr})
        `;
        const priceResult = await lib.executeQuery(
          "_tech_conn",
          priceQuery,
          {},
          12000,
        );
        const priceMap = new Map();
        if (priceResult) {
          for (const p of priceResult) {
            priceMap.set(p.ticker, p.close);
          }
        }

        let mapped = fundResult
          .filter((d: any) => candidateTickers.includes(`'${d.ticker}'`))
          .map((d: any) => {
            const accumulation = techMap.get(d.ticker) || 0;
            const closePrice =
              priceMap.get(d.ticker) ||
              Number(d.peRatio) * Number(d.earningsPerShare) ||
              1;
            const earningsYield =
              closePrice > 0 ? Number(d.earningsPerShare) / closePrice : 0;
            const earningsYieldPct = earningsYield * 100;

            return {
              ...d,
              accumulation,
              earningsYield: earningsYieldPct,
              multibagger_index: Math.round(
                Number(d.returnOnEquity) * 0.4 +
                  earningsYieldPct * 0.3 +
                  accumulation * 0.3,
              ),
              moat_score:
                Number(d.returnOnEquity) > 25
                  ? "Monopoly"
                  : Number(d.returnOnEquity) > 15
                    ? "High"
                    : "Medium",
            };
          });

        if (requireAccumulation) {
          mapped = mapped.filter((d: any) => d.accumulation >= 50);
        }

        mapped.sort(
          (a: any, b: any) => b.multibagger_index - a.multibagger_index,
        );

        setData(mapped.slice(0, 25));
      } else {
        setIsDemo(true);
        generateMockCandidates();
      }
    } catch (e: any) {
      console.error(e);
      setIsDemo(true);
      generateMockCandidates();
    } finally {
      setIsLoading(false);
    }
  };

  const generateMockCandidates = () => {
    const rawMock = [
      {
        ticker: "DIXON",
        sector: "Manufacturing",
        returnOnEquity: 28.4,
        earningsPerShare: 45.2,
        peRatio: 85,
        accumulation: 68.5,
        moat_score: "High",
        bucket: "Broader Market (N500)",
      },
      {
        ticker: "KPITTECH",
        sector: "IT Services",
        returnOnEquity: 22.1,
        earningsPerShare: 38.6,
        peRatio: 65,
        accumulation: 72.1,
        moat_score: "High",
        bucket: "Broader Market (N500)",
      },
      {
        ticker: "TRENT",
        sector: "Retail",
        returnOnEquity: 19.5,
        earningsPerShare: 55.4,
        peRatio: 120,
        accumulation: 61.2,
        moat_score: "Monopoly",
        bucket: "Large Cap (N100)",
      },
      {
        ticker: "POLYCAB",
        sector: "Industrials",
        returnOnEquity: 25.8,
        earningsPerShare: 28.9,
        peRatio: 45,
        accumulation: 58.4,
        moat_score: "High",
        bucket: "Large Cap (N100)",
      },
      {
        ticker: "HAL",
        sector: "Defense",
        returnOnEquity: 32.4,
        earningsPerShare: 22.1,
        peRatio: 35,
        accumulation: 75.8,
        moat_score: "Monopoly",
        bucket: "Large Cap (N50)",
      },
      {
        ticker: "ZOMATO",
        sector: "Consumer Tech",
        returnOnEquity: 12.5,
        earningsPerShare: 6.4,
        peRatio: 90,
        accumulation: 54.2,
        moat_score: "High",
        bucket: "Large Cap (N100)",
      },
      {
        ticker: "CDSL",
        sector: "Financials",
        returnOnEquity: 42.1,
        earningsPerShare: 28.5,
        peRatio: 48,
        accumulation: 66.7,
        moat_score: "Monopoly",
        bucket: "Broader Market (N500)",
      },
      {
        ticker: "KAYNES",
        sector: "Manufacturing",
        returnOnEquity: 18.2,
        earningsPerShare: 52.1,
        peRatio: 105,
        accumulation: 69.4,
        moat_score: "Medium",
        bucket: "Broader Market (N500)",
      },
      {
        ticker: "TATASTEEL",
        sector: "Metals",
        returnOnEquity: 14.2,
        earningsPerShare: 18.5,
        peRatio: 12,
        accumulation: 42.1,
        moat_score: "Low",
        bucket: "Large Cap (N50)",
      },
      {
        ticker: "RELIANCE",
        sector: "Energy",
        returnOnEquity: 11.5,
        earningsPerShare: 14.2,
        peRatio: 28,
        accumulation: 50.1,
        moat_score: "High",
        bucket: "Large Cap (N50)",
      },
    ];

    let filtered = rawMock;
    if (filterMcap !== "All") {
      filtered = filtered.filter((f) => f.bucket === filterMcap);
    }
    if (excludeCyclical) {
      const cyclicals = [
        "Metals",
        "Chemicals",
        "Energy",
        "Mining",
        "Materials",
      ];
      filtered = filtered.filter((f) => !cyclicals.includes(f.sector));
    }

    filtered = filtered.filter(
      (f) => f.returnOnEquity >= minRoe && f.earningsPerShare >= minEps,
    );
    if (requireAccumulation) {
      filtered = filtered.filter((f) => f.accumulation >= 50);
    }

    const calculated = filtered.map((d) => {
      const earningsYieldPct = d.peRatio > 0 ? (1 / d.peRatio) * 100 : 0;
      return {
        ...d,
        earningsYield: earningsYieldPct,
        multibagger_index: Math.round(
          d.returnOnEquity * 0.4 +
            earningsYieldPct * 0.3 +
            d.accumulation * 0.3,
        ),
        moat_score:
          d.returnOnEquity > 25
            ? "Monopoly"
            : d.returnOnEquity > 15
              ? "High"
              : "Medium",
      };
    });

    setData(
      calculated.sort((a, b) => b.multibagger_index - a.multibagger_index),
    );
  };

  const handleSort = (key: string) => {
    setSortConfig((prev) => ({
      key,
      direction: prev.key === key && prev.direction === "desc" ? "asc" : "desc",
    }));
  };

  const sortedData = useMemo(() => {
    let sortableItems = [...data];
    if (sortConfig !== null) {
      sortableItems.sort((a, b) => {
        if (a[sortConfig.key] < b[sortConfig.key]) {
          return sortConfig.direction === "asc" ? -1 : 1;
        }
        if (a[sortConfig.key] > b[sortConfig.key]) {
          return sortConfig.direction === "asc" ? 1 : -1;
        }
        return 0;
      });
    }
    return sortableItems;
  }, [data, sortConfig]);

  return (
    <div className="bg-[#1e2028] border border-[#ffffff1a] rounded flex flex-col shadow-xl overflow-hidden min-h-[600px]">
      <div className="px-6 py-4 border-b border-[#ffffff1a] flex justify-between items-center bg-[#1a1c24]">
        <h3 className="font-medium text-lg flex items-center gap-2">
          <Rocket size={20} className="text-orange-500" />
          Multibagger Discovery Matrix
        </h3>
        <div className="flex gap-2 items-center">
          {isDemo && (
            <span className="text-[10px] bg-yellow-500/20 text-yellow-500 px-2 py-1 rounded font-mono border border-yellow-500/30">
              ⚠️ SIMULATED PIPELINE
            </span>
          )}
          <span className="text-xs text-[#888] font-mono">
            Module: alpha.secular_growth
          </span>
        </div>
      </div>

      <div className="p-6 grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Left Side: Analytical Concepts & Metrics */}
        <div className="lg:col-span-1 flex flex-col gap-4">
          <div className="bg-orange-500/10 border border-orange-500/30 p-4 rounded-lg">
            <h4 className="text-orange-400 font-semibold text-sm flex items-center gap-2 mb-2">
              <Zap size={16} /> Earnings Yield & ROE
            </h4>
            <p className="text-xs text-[#ccc] leading-relaxed">
              This matrix plots <strong>Return on Equity (ROE)</strong> against{" "}
              <strong>Earnings Yield</strong> to identify systemic compounders
              trading at reasonable valuations. Targets in the top-right
              quadrant represent superior structural alphas.
            </p>
          </div>

          <div className="bg-[#0e1117] border border-[#ffffff0a] p-4 rounded-lg flex-1 border-t-4 border-t-orange-500/50">
            <h4 className="font-semibold text-[#fafafa] text-sm mb-3">
              Risk Toggles / Logic State
            </h4>
            <div className="space-y-3">
              <label className="flex items-center gap-3 p-2 bg-[#1a1c24] border border-[#ffffff1a] rounded cursor-pointer hover:bg-[#ffffff0a] transition-colors">
                <input
                  type="checkbox"
                  checked={excludeCyclical}
                  onChange={(e) => setExcludeCyclical(e.target.checked)}
                  className="accent-orange-500"
                />
                <span className="text-xs text-[#eee]">
                  Exclude Cyclicals (Metals, Energy)
                </span>
              </label>
              <label className="flex items-center gap-3 p-2 bg-[#1a1c24] border border-[#ffffff1a] rounded cursor-pointer hover:bg-[#ffffff0a] transition-colors">
                <input
                  type="checkbox"
                  checked={requireAccumulation}
                  onChange={(e) => setRequireAccumulation(e.target.checked)}
                  className="accent-orange-500"
                />
                <span className="text-xs text-[#eee]">
                  Require High Accumulation
                </span>
              </label>

              {/* Market Cap Filter */}
              <div className="flex flex-col pt-2 border-t border-[#ffffff1a]">
                <label className="text-[10px] text-[#888] font-mono mb-1">
                  Market Cap Category
                </label>
                <select
                  value={filterMcap}
                  onChange={(e) => setFilterMcap(e.target.value)}
                  className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1.5 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full"
                >
                  <option className="bg-[#1a1c24] text-[#fafafa]" value="All">
                    All
                  </option>
                  <option
                    className="bg-[#1a1c24] text-[#fafafa]"
                    value="Large Cap (N50)"
                  >
                    Large Cap (N50)
                  </option>
                  <option
                    className="bg-[#1a1c24] text-[#fafafa]"
                    value="Large Cap (N100)"
                  >
                    Large Cap (N100)
                  </option>
                  <option
                    className="bg-[#1a1c24] text-[#fafafa]"
                    value="Broader Market (N500)"
                  >
                    Broader Market (N500)
                  </option>
                  <option
                    className="bg-[#1a1c24] text-[#fafafa]"
                    value="Deep Frontier"
                  >
                    Deep Frontier
                  </option>
                </select>
              </div>

              {/* Dynamic Threshold Inputs */}
              <div className="grid grid-cols-2 gap-2 pt-2 border-t border-[#ffffff1a]">
                <div className="flex flex-col">
                  <label className="text-[10px] text-[#888] font-mono mb-1">
                    Min ROE (%)
                  </label>
                  <input
                    type="number"
                    value={minRoe}
                    onChange={(e) => setMinRoe(Number(e.target.value))}
                    className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full"
                  />
                </div>
                <div className="flex flex-col">
                  <label className="text-[10px] text-[#888] font-mono mb-1">
                    Min EPS (₹)
                  </label>
                  <input
                    type="number"
                    value={minEps}
                    onChange={(e) => setMinEps(Number(e.target.value))}
                    className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full"
                  />
                </div>
                <div className="flex flex-col col-span-2">
                  <label className="text-[10px] text-[#888] font-mono mb-1">
                    Lookback Period (Days)
                  </label>
                  <select
                    value={days}
                    onChange={(e) => setDays(Number(e.target.value))}
                    className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-xs text-[#fafafa] focus:border-orange-500 outline-none w-full"
                  >
                    <option className="bg-[#1a1c24] text-[#fafafa]" value={30}>
                      30 Days
                    </option>
                    <option className="bg-[#1a1c24] text-[#fafafa]" value={90}>
                      90 Days
                    </option>
                    <option className="bg-[#1a1c24] text-[#fafafa]" value={180}>
                      180 Days
                    </option>
                    <option className="bg-[#1a1c24] text-[#fafafa]" value={365}>
                      1 Year
                    </option>
                  </select>
                </div>
              </div>
            </div>
            {isLoading && (
              <div className="text-xs text-orange-400 font-mono mt-4 animate-pulse flex items-center justify-center">
                Recalculating Tensor Matrix...
              </div>
            )}
          </div>
        </div>

        {/* Right Side: The Matrix */}
        <div className="lg:col-span-3 flex flex-col gap-6">
          <div className="h-72 bg-[#0e1117] border border-[#ffffff0a] rounded-lg p-4 relative">
            <h4 className="text-xs font-mono text-[#888] uppercase mb-2 absolute top-4 left-4 z-10">
              ROE vs Earnings Yield (%)
            </h4>
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart
                margin={{ top: 20, right: 30, bottom: 20, left: -20 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="#222" />
                <XAxis
                  type="number"
                  dataKey="earningsYield"
                  name="Earnings Yield"
                  unit="%"
                  stroke="#666"
                  tick={{ fill: "#666", fontSize: 10 }}
                  tickFormatter={(v) => v.toFixed(1)}
                />
                <YAxis
                  type="number"
                  dataKey="returnOnEquity"
                  name="ROE"
                  unit="%"
                  stroke="#666"
                  tick={{ fill: "#666", fontSize: 10 }}
                />
                <ZAxis
                  type="number"
                  dataKey="multibagger_index"
                  range={[100, 500]}
                  name="Score"
                />
                <Tooltip
                  cursor={{ strokeDasharray: "3 3" }}
                  contentStyle={{
                    backgroundColor: "#1a1c24",
                    border: "1px solid #333",
                    fontSize: "12px",
                    color: "#fff",
                    borderRadius: "4px",
                  }}
                  formatter={(value: number, name: string) => {
                    if (name === "Earnings Yield" || name === "ROE")
                      return `${value.toFixed(2)}%`;
                    return value;
                  }}
                />
                {/* Quadrant Lines matching ideal 'compounder' thresholds */}
                <ReferenceLine
                  x={4}
                  stroke="#ffffff22"
                  strokeDasharray="5 5"
                  label={{
                    position: "insideTopRight",
                    value: "High Yield",
                    fill: "#ffffff44",
                    fontSize: 10,
                  }}
                />
                <ReferenceLine
                  y={20}
                  stroke="#ffffff22"
                  strokeDasharray="5 5"
                  label={{
                    position: "insideTopLeft",
                    value: "High ROE",
                    fill: "#ffffff44",
                    fontSize: 10,
                  }}
                />

                <Scatter name="Candidates" data={sortedData} fill="#f97316">
                  {sortedData.map((entry, index) => (
                    <Cell
                      key={`cell-${index}`}
                      fill={
                        entry.moat_score === "Monopoly"
                          ? "#a855f7"
                          : entry.accumulation >= 65
                            ? "#22c55e"
                            : "#f97316"
                      }
                    />
                  ))}
                </Scatter>
              </ScatterChart>
            </ResponsiveContainer>
          </div>

          <div className="bg-[#0e1117] border border-[#ffffff0a] rounded-lg overflow-hidden flex-1">
            <div className="overflow-x-auto max-h-64">
              <table className="w-full text-left font-mono text-xs relative">
                <thead className="bg-[#1a1c24] border-b border-[#ffffff1a] sticky top-0 z-10">
                  <tr className="text-[#888]">
                    <th
                      className="py-3 px-4 font-medium uppercase cursor-pointer hover:text-white"
                      onClick={() => handleSort("ticker")}
                    >
                      Ticker{" "}
                      <ArrowUpDown
                        size={10}
                        className="inline ml-1 opacity-50"
                      />
                    </th>
                    <th
                      className="py-3 px-4 font-medium uppercase cursor-pointer hover:text-white"
                      onClick={() => handleSort("sector")}
                    >
                      Sector{" "}
                      <ArrowUpDown
                        size={10}
                        className="inline ml-1 opacity-50"
                      />
                    </th>
                    <th
                      className="py-3 px-4 font-medium uppercase text-right cursor-pointer hover:text-white"
                      onClick={() => handleSort("returnOnEquity")}
                    >
                      ROE{" "}
                      <ArrowUpDown
                        size={10}
                        className="inline ml-1 opacity-50"
                      />
                    </th>
                    <th
                      className="py-3 px-4 font-medium uppercase text-right cursor-pointer hover:text-white"
                      onClick={() => handleSort("earningsYield")}
                    >
                      Earn Yield{" "}
                      <ArrowUpDown
                        size={10}
                        className="inline ml-1 opacity-50"
                      />
                    </th>
                    <th
                      className="py-3 px-4 font-medium uppercase text-right text-green-400 cursor-pointer hover:text-green-300"
                      onClick={() => handleSort("accumulation")}
                    >
                      Accumulation{" "}
                      <ArrowUpDown
                        size={10}
                        className="inline ml-1 opacity-50"
                      />
                    </th>
                    <th
                      className="py-3 px-4 font-medium uppercase text-center cursor-pointer hover:text-white"
                      onClick={() => handleSort("moat_score")}
                    >
                      Moat{" "}
                      <ArrowUpDown
                        size={10}
                        className="inline ml-1 opacity-50"
                      />
                    </th>
                    <th
                      className="py-3 px-4 font-medium uppercase text-right text-orange-400 cursor-pointer hover:text-orange-300"
                      onClick={() => handleSort("multibagger_index")}
                    >
                      MB Index{" "}
                      <ArrowUpDown
                        size={10}
                        className="inline ml-1 opacity-50"
                      />
                    </th>
                  </tr>
                </thead>
                <tbody className="text-[#ccc]">
                  {sortedData.map((row, idx) => (
                    <tr
                      key={idx}
                      className="border-b border-[#ffffff0a] hover:bg-[#ffffff05] transition-colors"
                    >
                      <td className="py-2.5 px-4 font-bold text-white flex items-center gap-2">
                        {row.ticker}
                        {row.moat_score === "Monopoly" && (
                          <ShieldAlert size={12} className="text-purple-400" />
                        )}
                      </td>
                      <td className="py-2.5 px-4 text-[#888]">{row.sector}</td>
                      <td className="py-2.5 px-4 text-right text-cyan-400">
                        {(row.returnOnEquity || 0).toFixed(1)}%
                      </td>
                      <td className="py-2.5 px-4 text-right">
                        {(row.earningsYield || 0).toFixed(2)}%
                      </td>
                      <td className="py-2.5 px-4 text-right text-green-400">
                        {(row.accumulation || 0).toFixed(1)}%
                      </td>
                      <td className="py-2.5 px-4 text-center">
                        <span
                          className={`px-2 py-0.5 rounded text-[10px] ${row.moat_score === "Monopoly" ? "bg-purple-500/20 text-purple-400" : "bg-[#ffffff1a] text-[#aaa]"}`}
                        >
                          {row.moat_score}
                        </span>
                      </td>
                      <td className="py-2.5 px-4 text-right font-bold text-orange-400">
                        {row.multibagger_index}
                      </td>
                    </tr>
                  ))}
                  {sortedData.length === 0 && (
                    <tr>
                      <td colSpan={7} className="py-8 text-center text-[#666]">
                        No candidates match the structural criteria.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
