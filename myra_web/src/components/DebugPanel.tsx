import { useState, useEffect } from 'react';
import { useSettings } from '../lib/SettingsContext';
import { Terminal, X, AlertTriangle, Play, Pause, Trash2 } from 'lucide-react';

interface DebugEvent {
  id: string;
  type: 'request' | 'response' | 'error';
  database: string;
  query: string;
  duration?: number;
  rows?: number;
  error?: string;
  timestamp: number;
}

export function DebugPanel() {
  const { settings } = useSettings();
  const [events, setEvents] = useState<DebugEvent[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const [isPaused, setIsPaused] = useState(false);

  useEffect(() => {
    if (!settings.debugMode) {
      if (isOpen) setIsOpen(false);
      return;
    }

    const handleDebug = (e: any) => {
      if (isPaused) return;
      
      const detail = e.detail;
      setEvents(prev => {
        const newEvents = [{...detail, id: Math.random().toString(36).substr(2, 9)}, ...prev];
        return newEvents.slice(0, 50); // keep last 50
      });
    };

    window.addEventListener('librarian-debug', handleDebug);
    return () => window.removeEventListener('librarian-debug', handleDebug);
  }, [settings.debugMode, isPaused]);

  if (!settings.debugMode) return null;

  if (!isOpen) {
    return (
      <button 
        onClick={() => setIsOpen(true)}
        className="fixed bottom-12 right-4 z-50 bg-indigo-600 hover:bg-indigo-500 text-white p-2 rounded-full shadow-lg border border-indigo-400/50 flex items-center justify-center transition-all"
        title="Open Debug Panel"
      >
        <Terminal size={18} />
      </button>
    );
  }

  return (
    <div className="fixed bottom-12 right-4 z-50 w-[450px] max-w-[calc(100vw-2rem)] h-[400px] bg-[#0e1117]/95 backdrop-blur-md border border-[#ffffff2a] rounded-lg shadow-2xl flex flex-col font-mono text-xs overflow-hidden">
      <div className="flex justify-between items-center bg-[#1a1c24] border-b border-[#ffffff1a] px-3 py-2 cursor-move select-none">
        <div className="flex items-center gap-2 text-indigo-400 font-bold">
          <Terminal size={14} />
          DEBUG SQL TERMINAL
        </div>
        <div className="flex items-center gap-2">
          <button 
            onClick={() => setEvents([])} 
            className="text-[#888] hover:text-[#fff] p-1 rounded hover:bg-[#ffffff10]"
            title="Clear Logs"
          >
            <Trash2 size={12} />
          </button>
          <button 
            onClick={() => setIsPaused(!isPaused)} 
            className={`${isPaused ? 'text-yellow-400 hover:text-yellow-300' : 'text-[#888] hover:text-[#fff]'} p-1 rounded hover:bg-[#ffffff10]`}
            title={isPaused ? "Resume Logging" : "Pause Logging"}
          >
            {isPaused ? <Play size={12} /> : <Pause size={12} />}
          </button>
          <button 
            onClick={() => setIsOpen(false)} 
            className="text-[#888] hover:text-red-400 p-1 rounded hover:bg-[#ffffff10] ml-1"
          >
            <X size={14} />
          </button>
        </div>
      </div>
      
      <div className="flex-1 overflow-y-auto p-2 space-y-2">
        {events.length === 0 ? (
          <div className="text-[#555] text-center mt-10">Waiting for queries...</div>
        ) : (
          events.map((ev) => (
            <div key={ev.id} className="bg-[#1a1c24] border border-[#ffffff1a] rounded p-2">
              <div className="flex justify-between items-start mb-1">
                <div className="flex items-center gap-2">
                  <span className={`px-1.5 py-0.5 rounded text-[10px] uppercase font-bold
                    ${ev.type === 'request' ? 'bg-blue-500/20 text-blue-400' : 
                      ev.type === 'response' ? 'bg-green-500/20 text-green-400' : 
                      'bg-red-500/20 text-red-400'}
                  `}>
                    {ev.type}
                  </span>
                  <span className="text-[#888]">{ev.database}</span>
                </div>
                <span className="text-[#555]">{new Date(ev.timestamp).toLocaleTimeString()}</span>
              </div>
              
              <div className="text-[#ccc] mt-1 break-all bg-[#0e1117] p-1.5 rounded text-[10px] border border-[#ffffff0a]">
                {ev.query}
              </div>
              
              {(ev.duration !== undefined || ev.error) && (
                <div className="flex justify-between items-center mt-1.5 text-[10px]">
                  {ev.duration !== undefined && (
                    <span className="text-[#888]">Took {(ev.duration).toFixed(1)}ms</span>
                  )}
                  {ev.rows !== undefined && (
                    <span className="text-green-400 text-right w-full">Returned {ev.rows} rows</span>
                  )}
                  {ev.error && (
                    <span className="text-red-400 flex items-center gap-1 justify-end w-full">
                      <AlertTriangle size={10} />
                      {ev.error}
                    </span>
                  )}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
