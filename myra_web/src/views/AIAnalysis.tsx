import { useState } from 'react';
import { Librarian } from '../lib/Librarian';
import { generateAnalysis } from '../lib/ai';
import { Copy, Check } from 'lucide-react';

export default function AIAnalysisView({ lib }: { lib: Librarian }) {
  const [target, setTarget] = useState('inst_flow');
  const [depth, setDepth] = useState('32');
  const [model, setModel] = useState('myra_quant_v2.2');
  const [dbRoute, setDbRoute] = useState('_meta_conn');
  const [latencyOpt, setLatencyOpt] = useState('lean');

  const [result, setResult] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  const displayCommand = `GENERATE_PROMPT --target=${target} --depth=${depth} --model=${model} --inject_params={db_route: '${dbRoute}', latency_opt: '${latencyOpt}'}`;

  const runAnalysis = async () => {
    setLoading(true);
    setResult(null);
    try {
      const prompt = `Execute MYRA Option 35 Prompt: Run a high-level quantitative analysis based on the latest macro conditions. \nApply the following engine constraints:\n- Target: ${target}\n- Depth: ${depth}\n- Model: ${model}\n- Database Route: ${dbRoute}\n- Latency Opt: ${latencyOpt}\n\nPlease generate a quantitative interpretation report answering the prompt.`;
      const res = await generateAnalysis(prompt);
      setResult(res || 'No analysis generated.');
    } catch (e: any) {
      console.error(e);
      setResult('Error generating analysis: ' + e.message);
    }
    setLoading(false);
  };

  const handleCopy = () => {
    if (result) {
      navigator.clipboard.writeText(result);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className="bg-[#262730] rounded-lg border border-[#ffffff1a] flex flex-col overflow-hidden">
      <div className="p-3 border-b border-[#ffffff1a] bg-[#ffffff05] flex justify-between items-center">
        <span className="text-xs font-semibold uppercase tracking-wider text-[#fafafa]">AI Analysis: Option 35</span>
        <span className="text-[10px] font-mono text-[#666]">SQL: {dbRoute}.execute()</span>
      </div>

      <div className="p-4 space-y-4">
        
        {/* Filter Controls Grid */}
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 bg-[#ffffff05] p-3 rounded border border-[#ffffff0a]">
          <div className="flex flex-col gap-1" title="Select the metric to focus the AI analysis on (e.g. Institutional Flow, Technical Scan).">
            <label className="text-[10px] text-[#888] uppercase font-semibold">Target</label>
            <select value={target} onChange={e => setTarget(e.target.value)} className="bg-[#0e1117] border border-[#ffffff1a] text-[#ccc] text-xs p-1.5 rounded font-mono focus:outline-none focus:border-blue-500 transition-colors">
              <option className="bg-[#1a1c24] text-[#fafafa]" value="inst_flow">inst_flow</option>
              <option className="bg-[#1a1c24] text-[#fafafa]" value="tech_scan">tech_scan</option>
              <option className="bg-[#1a1c24] text-[#fafafa]" value="macro_aggr">macro_aggr</option>
            </select>
          </div>
          <div className="flex flex-col gap-1" title="Set the recursion depth for historical data traversal.">
            <label className="text-[10px] text-[#888] uppercase font-semibold">Depth</label>
            <input type="number" value={depth} onChange={e => setDepth(e.target.value)} className="bg-[#0e1117] border border-[#ffffff1a] text-[#ccc] text-xs p-1.5 rounded font-mono focus:outline-none focus:border-blue-500 transition-colors" />
          </div>
          <div className="flex flex-col gap-1" title="Choose the quantitative model architecture to execute.">
            <label className="text-[10px] text-[#888] uppercase font-semibold">Model</label>
            <select value={model} onChange={e => setModel(e.target.value)} className="bg-[#0e1117] border border-[#ffffff1a] text-[#ccc] text-xs p-1.5 rounded font-mono focus:outline-none focus:border-blue-500 transition-colors">
              <option className="bg-[#1a1c24] text-[#fafafa]" value="myra_quant_v2.2">myra_quant_v2.2</option>
              <option className="bg-[#1a1c24] text-[#fafafa]" value="myra_macro_v3.0">myra_macro_v3.0</option>
              <option className="bg-[#1a1c24] text-[#fafafa]" value="gemini_pro_v1.5">gemini_pro_v1.5</option>
            </select>
          </div>
          <div className="flex flex-col gap-1" title="Specify which internal SQLite database sidecar to query data from.">
            <label className="text-[10px] text-[#888] uppercase font-semibold">DB Route</label>
            <select value={dbRoute} onChange={e => setDbRoute(e.target.value)} className="bg-[#0e1117] border border-[#ffffff1a] text-[#ccc] text-xs p-1.5 rounded font-mono focus:outline-none focus:border-blue-500 transition-colors">
              <option className="bg-[#1a1c24] text-[#fafafa]" value="_meta_conn">_meta_conn</option>
              <option className="bg-[#1a1c24] text-[#fafafa]" value="_inst_conn">_inst_conn</option>
              <option className="bg-[#1a1c24] text-[#fafafa]" value="_tech_conn">_tech_conn</option>
            </select>
          </div>
          <div className="flex flex-col gap-1" title="Tune for calculation speed vs processing precision.">
            <label className="text-[10px] text-[#888] uppercase font-semibold">Latency Opt</label>
            <select value={latencyOpt} onChange={e => setLatencyOpt(e.target.value)} className="bg-[#0e1117] border border-[#ffffff1a] text-[#ccc] text-xs p-1.5 rounded font-mono focus:outline-none focus:border-blue-500 transition-colors">
              <option className="bg-[#1a1c24] text-[#fafafa]" value="lean">lean</option>
              <option className="bg-[#1a1c24] text-[#fafafa]" value="deep">deep</option>
              <option className="bg-[#1a1c24] text-[#fafafa]" value="balanced">balanced</option>
            </select>
          </div>
        </div>
        
        <div className="bg-[#0e1117] p-3 rounded font-mono text-[11px] text-[#88d] border border-[#ffffff0a] italic">
          {displayCommand}
        </div>

        <div className="flex justify-end">
          <button 
            onClick={runAnalysis} 
            disabled={loading} 
            className="font-mono text-xs px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-white transition-colors disabled:opacity-50 flex items-center"
          >
            {loading ? (
              <>
                <div className="animate-spin h-3 w-3 border-2 border-[#fafafa] border-t-transparent rounded-full mr-2"></div>
                Executing...
              </>
            ) : (
              'Execute Option 35'
            )}
          </button>
        </div>

        {result && (
          <div className="relative group">
            <button 
              onClick={handleCopy}
              className="absolute top-0 right-0 p-1.5 bg-[#1a1c24] border border-[#ffffff1a] rounded text-[#888] hover:text-[#fff] hover:bg-[#ffffff1a] transition-colors z-10"
              title="Copy Output"
            >
              {copied ? <Check size={14} className="text-green-500" /> : <Copy size={14} />}
            </button>
            <div className="p-4 bg-[#0e1117] border border-[#ffffff1a] rounded text-[#ccc] whitespace-pre-wrap font-mono text-xs h-64 overflow-y-auto w-full">
              {result}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
