import { useState } from 'react';
import { Librarian } from '../lib/Librarian';
import { SymbolSearch } from '../components/SymbolSearch';
import { ChevronDown, ChevronRight } from 'lucide-react';

interface FinStackAgent {
  name: string;
  signal: string;
  reasoning: string[];
}

interface FinStackResponse {
  consensus: string;
  score: number;
  agents: FinStackAgent[];
  summary: string;
}

export default function AIAnalysisView({ lib }: { lib: Librarian }) {
  const [ticker, setTicker] = useState('');
  const [result, setResult] = useState<FinStackResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedAgents, setExpandedAgents] = useState<{ [key: string]: boolean }>({});

  const runAnalysis = async () => {
    if (!ticker) return;
    setLoading(true);
    setResult(null);
    setError(null);
    try {
      const res = await fetch(`http://localhost:8000/api/finstack/stock-brief/${ticker.toUpperCase()}`);
      if (!res.ok) {
        throw new Error('Analysis request failed');
      }
      const data = await res.json();
      setResult(data);
    } catch (e: any) {
      console.error(e);
      setError(e.message || 'Error generating analysis');
    }
    setLoading(false);
  };

  const toggleAgent = (name: string) => {
    setExpandedAgents(prev => ({ ...prev, [name]: !prev[name] }));
  };

  return (
    <div className="bg-[#262730] rounded-lg border border-[#ffffff1a] flex flex-col overflow-hidden">
      <div className="p-3 border-b border-[#ffffff1a] bg-[#ffffff05] flex justify-between items-center">
        <span className="text-xs font-semibold uppercase tracking-wider text-[#fafafa]">FinStack AI Analysis</span>
      </div>

      <div className="p-4 space-y-4">
        <div className="flex items-end gap-3">
          <div className="w-64 relative z-10 flex flex-col gap-1">
             <label className="text-[10px] text-[#888] uppercase mb-1 font-semibold font-mono tracking-wider ml-1">
               Target Ticker
             </label>
             <SymbolSearch 
               lib={lib} 
               onSymbolSelect={setTicker} 
               placeholder="Search NSE ticker..." 
               className="h-9"
             />
          </div>
          <button 
            onClick={runAnalysis} 
            disabled={loading || !ticker} 
            className="font-mono text-xs px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-white transition-colors disabled:opacity-50 flex items-center h-9"
          >
            {loading ? (
              <>
                <div className="animate-spin h-3 w-3 border-2 border-[#fafafa] border-t-transparent rounded-full mr-2"></div>
                Analyzing...
              </>
            ) : (
              'Generate Analysis'
            )}
          </button>
        </div>

        {error && (
          <div className="text-xs text-red-400 font-mono py-4">
            No analysis available. ({error})
          </div>
        )}

        {result && (
          <div className="space-y-4">
            {/* Header info */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 bg-[#ffffff05] p-3 rounded border border-[#ffffff0a]">
              <div className="bg-[#1a1c24] border border-[#ffffff0a] p-3 rounded">
                <div className="text-[10px] text-[#888] font-mono uppercase">Consensus</div>
                <div className={`text-lg font-bold ${
                  result.consensus === 'BUY' || result.consensus === 'BULLISH' ? 'text-green-400' :
                  result.consensus === 'SELL' || result.consensus === 'BEARISH' ? 'text-red-400' : 'text-yellow-400'
                }`}>
                  {result.consensus}
                </div>
              </div>
              <div className="bg-[#1a1c24] border border-[#ffffff0a] p-3 rounded">
                <div className="text-[10px] text-[#888] font-mono uppercase">Confidence Score</div>
                <div className="text-lg font-bold text-[#fafafa]">{result.score}%</div>
              </div>
            </div>

            {/* Summary */}
            {result.summary && (
              <div className="bg-[#0e1117] p-4 rounded border border-[#ffffff0a] font-mono text-xs text-[#ccc] leading-relaxed">
                <div className="text-[10px] text-[#888] mb-2 font-semibold">SUMMARY</div>
                {result.summary}
              </div>
            )}

            {/* Agents */}
            <div className="space-y-2">
              <div className="text-[10px] text-[#888] font-mono uppercase font-semibold mt-4 mb-2">Agent Reasoning</div>
              {result.agents?.map((agent, i) => (
                <div key={agent.name || i} className="border border-[#ffffff0a] rounded bg-[#0e1117] overflow-hidden">
                  <button
                    onClick={() => toggleAgent(agent.name)}
                    className="w-full px-4 py-3 flex items-center justify-between text-xs font-mono text-[#ccc] hover:bg-[#ffffff05]"
                  >
                    <div className="flex items-center gap-2">
                       {expandedAgents[agent.name] ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                       <span className="font-semibold text-white">{agent.name}</span>
                    </div>
                    <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                       agent.signal === 'BULLISH' || agent.signal === 'BUY' ? 'bg-green-500/10 text-green-400 border border-green-500/20' :
                       agent.signal === 'BEARISH' || agent.signal === 'SELL' ? 'bg-red-500/10 text-red-400 border border-red-500/20' :
                       'bg-gray-500/10 text-gray-400 border border-gray-500/20'
                    }`}>
                      {agent.signal}
                    </span>
                  </button>
                  {expandedAgents[agent.name] && (
                    <div className="px-4 pb-4 pt-1 border-t border-[#ffffff0a]">
                      <ul className="space-y-2">
                        {agent.reasoning?.map((reason, idx) => (
                           <li key={idx} className="text-[11px] font-mono text-[#aaa] leading-relaxed flex gap-2">
                             <span className="text-[#666] shrink-0">•</span>
                             <span>{reason}</span>
                           </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
