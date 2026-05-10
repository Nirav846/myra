import { useState, useEffect, useCallback } from "react";
import { RefreshCw, Play, CheckCircle, AlertTriangle } from "lucide-react";
import { Librarian } from "../lib/Librarian";

const API_BASE = "http://localhost:8000";

interface PipelineStatus {
  fundamentals: string;
  etf: string;
  index: string;
  ingest: string;
  db_doctor: string;
}

const mockStatus: PipelineStatus = {
  fundamentals: "2026-05-07 18:30:00",
  etf: "2026-05-07 18:40:00",
  index: "2026-05-07 18:45:00",
  ingest: "2026-05-07 16:15:00",
  db_doctor: "2026-05-07 19:00:00",
};

const ENDPOINTS = {
  fundamentals: "/api/tools/sync/fundamentals",
  etf: "/api/tools/sync/etf",
  index: "/api/tools/sync/index",
  ingest: "/api/tools/ingest",
  db_doctor: "/api/tools/db-doctor",
};

const TASK_NAMES = {
  fundamentals: "Fundamentals Sync",
  etf: "ETF Sync",
  index: "Index Sync",
  ingest: "Daily Ingest",
  db_doctor: "DB Doctor",
};

export default function ToolsView({ lib }: { lib: Librarian }) {
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [isOffline, setIsOffline] = useState(false);
  const [loadingTasks, setLoadingTasks] = useState<Record<string, boolean>>({});
  const [messages, setMessages] = useState<
    Record<string, { type: "success" | "error"; text: string }>
  >({});

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/tools/status`);
      if (!res.ok) throw new Error("Network response was not ok");
      const data: PipelineStatus = await res.json();
      setStatus(data);
      setIsOffline(false);
    } catch (e) {
      console.warn("Backend not reachable. Falling back to mock status.", e);
      setIsOffline(true);
      setStatus(mockStatus);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    const intervalId = setInterval(fetchStatus, 30000);
    return () => clearInterval(intervalId);
  }, [fetchStatus]);

  const handleRunTask = async (taskKey: keyof typeof ENDPOINTS) => {
    if (isOffline) return;

    setLoadingTasks((prev) => ({ ...prev, [taskKey]: true }));
    setMessages((prev) => {
      const next = { ...prev };
      delete next[taskKey];
      return next;
    });

    try {
      const res = await fetch(`${API_BASE}${ENDPOINTS[taskKey]}`, {
        method: "POST",
      });
      if (!res.ok) throw new Error("Failed to start task");

      const data = await res.json();
      setMessages((prev) => ({
        ...prev,
        [taskKey]: { type: "success", text: data.message || "Task completed" },
      }));
      fetchStatus();
    } catch (e: any) {
      setMessages((prev) => ({
        ...prev,
        [taskKey]: { type: "error", text: e.message || "Error executing task" },
      }));
    } finally {
      setLoadingTasks((prev) => ({ ...prev, [taskKey]: false }));
      // Clear message after 5 seconds
      setTimeout(() => {
        setMessages((prev) => {
          const next = { ...prev };
          delete next[taskKey];
          return next;
        });
      }, 5000);
    }
  };

  return (
    <div className="bg-[#1e2028] border border-[#ffffff1a] rounded flex flex-col shadow-xl">
      <div className="px-6 py-4 border-b border-[#ffffff1a] flex justify-between items-center bg-[#1a1c24]">
        <h3 className="font-medium text-lg flex items-center gap-2">
          Pipeline Status
        </h3>
        <span className="text-xs text-[#888] font-mono">/api/tools</span>
      </div>

      {isOffline && (
        <div className="bg-yellow-500/10 border-b border-yellow-500/20 px-6 py-3 flex items-center gap-3">
          <AlertTriangle size={16} className="text-yellow-500" />
          <span className="text-sm font-medium text-yellow-500">
            Backend not reachable – using mock status
          </span>
        </div>
      )}

      <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-4">
        {(Object.keys(TASK_NAMES) as Array<keyof typeof TASK_NAMES>).map(
          (taskKey) => (
            <div
              key={taskKey}
              className="bg-[#0e1117] border border-[#ffffff1a] rounded-lg p-5 flex flex-col"
            >
              <div className="flex justify-between items-start mb-4">
                <div>
                  <h4 className="font-medium text-[#eee]">
                    {TASK_NAMES[taskKey]}
                  </h4>
                  <div className="text-xs text-[#888] mt-1 font-mono">
                    Last Run: {status ? status[taskKey] : "Loading..."}
                  </div>
                </div>
              </div>

              <div className="mt-auto pt-2">
                {messages[taskKey] && (
                  <div
                    className={`mb-3 flex items-center gap-2 text-xs ${messages[taskKey].type === "success" ? "text-green-400" : "text-red-400"}`}
                  >
                    {messages[taskKey].type === "success" ? (
                      <CheckCircle size={14} />
                    ) : (
                      <AlertTriangle size={14} />
                    )}
                    {messages[taskKey].text}
                  </div>
                )}

                <button
                  onClick={() => handleRunTask(taskKey)}
                  disabled={isOffline || loadingTasks[taskKey]}
                  className="w-full py-2 flex items-center justify-center gap-2 text-sm font-semibold bg-[#262730] hover:bg-[#333] disabled:opacity-50 border border-[#ffffff1a] rounded transition-colors"
                >
                  {loadingTasks[taskKey] ? (
                    <>
                      <RefreshCw
                        size={14}
                        className="animate-spin text-[#888]"
                      />{" "}
                      Running...
                    </>
                  ) : (
                    <>
                      <Play size={14} className="text-[#bbb]" /> Run Now
                    </>
                  )}
                </button>
              </div>
            </div>
          ),
        )}
      </div>
    </div>
  );
}
