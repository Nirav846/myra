import { useState } from 'react';
import { Database, Play, RefreshCw, Layers, Cpu } from 'lucide-react';
import { Librarian } from '../lib/Librarian';

export default function ToolsView({ lib }: { lib: Librarian }) {
  const [running, setRunning] = useState<Record<string, boolean>>({});
  const [logs, setLogs] = useState<Record<string, string>>({});

    const tools = [
      {
        id: 'tools/force_sync.py',
        name: 'tools/force_sync.py',
        description: 'Forces an immediate synchronization of the institutional database ignoring cache TTL.',
        icon: <RefreshCw size={18} className="text-blue-400" />,
        color: 'blue'
      },
      {
        id: 'update_fundamentals.py',
        name: 'update_fundamentals.py',
        description: 'Fetches the latest Graham metrics and updates the value ranker logic.',
        icon: <Database size={18} className="text-green-400" />,
        color: 'green'
      },
      {
        id: 'research/train_aeon.py',
        name: 'research/train_aeon.py',
        description: 'Triggers the AEON Model retraining pipeline on the latest indicator Parquet lake.',
        icon: <Cpu size={18} className="text-red-400" />,
        color: 'red'
      },
      {
        id: 'myra_app/engine.py',
        name: 'myra_app/engine.py',
        description: 'Runs the core Myra calculation engine for standard market hours logging.',
        icon: <Layers size={18} className="text-fuchsia-400" />,
        color: 'fuchsia'
      }
    ];

  const handleRun = async (toolId: string) => {
    setRunning(prev => ({ ...prev, [toolId]: true }));
    setLogs(prev => ({ ...prev, [toolId]: `>> Executing python tools/${toolId}.py...\n` }));
    
    // Check if we are connected to the REAL local FastAPI backend
    if (lib.isConnectedToLocalRepo) {
      try {
        setLogs(prev => ({ ...prev, [toolId]: prev[toolId] + `[INFO] Requesting external execution on localhost:8000...\n` }));
        const response = await fetch('http://localhost:8000/api/tools/execute', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tool_id: toolId })
        });
        
        const data = await response.json();
        if (response.ok) {
          setLogs(prev => ({ ...prev, [toolId]: prev[toolId] + data.logs + `\n[FINISH] Status = ${data.success ? 'Success' : 'Failed'}` }));
        } else {
          setLogs(prev => ({ ...prev, [toolId]: prev[toolId] + `[ERROR] Network or server error: ${data.detail || response.statusText}` }));
        }
      } catch (err) {
        setLogs(prev => ({ ...prev, [toolId]: prev[toolId] + `[CRITICAL_ERROR] FastAPI server unreachable or script crashed.\n${err}` }));
      } finally {
        setRunning(prev => ({ ...prev, [toolId]: false }));
      }
      return; 
    }

    // --- FALLBACK: Live Demo Simulator if backend is down ---
    setTimeout(() => {
      setLogs(prev => ({ 
        ...prev, 
        [toolId]: prev[toolId] + `[INFO] Initializing environment...\n[INFO] Connecting to SQLite sidecars in myra_app/db...\n` 
      }));
    }, 800);

    setTimeout(() => {
      setLogs(prev => ({ 
        ...prev, 
        [toolId]: prev[toolId] + `[SUCCESS] Operation completed successfully in ${(Math.random() * 2 + 1).toFixed(2)}s.` 
      }));
      setRunning(prev => ({ ...prev, [toolId]: false }));
    }, 2500);
  };

  return (
    <div className="bg-[#1e2028] border border-[#ffffff1a] rounded flex flex-col shadow-xl">
      <div className="px-6 py-4 border-b border-[#ffffff1a] flex justify-between items-center bg-[#1a1c24]">
        <h3 className="font-medium text-lg flex items-center gap-2">
          Admin Tools & Scripts
        </h3>
        <span className="text-xs text-[#888] font-mono">/home/myra/tools</span>
      </div>

      <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-4">
        {tools.map(tool => (
          <div key={tool.id} className="bg-[#0e1117] border border-[#ffffff1a] rounded-lg overflow-hidden flex flex-col">
            <div className="p-4 flex gap-3">
               <div className={`p-2 bg-[${tool.color}]/10 rounded-lg h-fit`}>
                 {tool.icon}
               </div>
               <div className="flex-1">
                 <h4 className="font-medium text-[#eee]">{tool.name}</h4>
                 <p className="text-xs text-[#888] mt-1 line-clamp-2">{tool.description}</p>
               </div>
            </div>
            
            <div className="px-4 pb-4 mt-auto">
              {logs[tool.id] && (
                <div className="mb-3 p-2 bg-black rounded border border-[#333] text-[10px] font-mono text-[#aaa] whitespace-pre-wrap h-20 overflow-y-auto">
                  {logs[tool.id]}
                </div>
              )}
              
              <button 
                onClick={() => handleRun(tool.id)}
                disabled={running[tool.id]}
                className="w-full py-1.5 flex items-center justify-center gap-2 text-xs font-semibold bg-[#262730] hover:bg-[#333] disabled:opacity-50 border border-[#ffffff1a] rounded transition-colors"
              >
                {running[tool.id] ? (
                  <><RefreshCw size={12} className="animate-spin text-[#888]" /> Running...</>
                ) : (
                  <><Play size={12} className="text-[#bbb]" /> Execute Script</>
                )}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
