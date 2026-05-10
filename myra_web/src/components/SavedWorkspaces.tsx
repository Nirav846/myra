import { useState, useRef, useEffect } from "react";
import { useSettings, SettingsState } from "../lib/SettingsContext";
import { Save, FolderDown, Trash2, ChevronDown } from "lucide-react";

interface Workspace {
  id: string;
  name: string;
  settings: SettingsState;
}

export function SavedWorkspaces() {
  const { settings, updateSettings } = useSettings();
  const [workspaces, setWorkspaces] = useState<Workspace[]>(() => {
    try {
      const saved = localStorage.getItem("myra_workspaces");
      return saved ? JSON.parse(saved) : [];
    } catch {
      return [];
    }
  });

  const [isOpen, setIsOpen] = useState(false);
  const [newWorkspaceName, setNewWorkspaceName] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    localStorage.setItem("myra_workspaces", JSON.stringify(workspaces));
  }, [workspaces]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
        setIsSaving(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleSave = () => {
    if (!newWorkspaceName.trim()) return;

    const newWs: Workspace = {
      id: Math.random().toString(36).substr(2, 9),
      name: newWorkspaceName.trim(),
      settings: settings,
    };

    setWorkspaces([...workspaces, newWs]);
    setNewWorkspaceName("");
    setIsSaving(false);
  };

  const handleLoad = (ws: Workspace) => {
    updateSettings(ws.settings);
    setIsOpen(false);
  };

  const handleDelete = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setWorkspaces(workspaces.filter((w) => w.id !== id));
  };

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-1.5 px-2 py-1.5 bg-[#ffffff0a] border border-[#ffffff1a] rounded hover:bg-[#ffffff15] text-[#fafafa] transition-colors text-xs font-mono"
        title="Saved Workspaces"
      >
        <FolderDown size={14} />
        <span className="hidden sm:inline">Workspace</span>
        <ChevronDown
          size={14}
          className={`transition-transform ${isOpen ? "rotate-180" : ""}`}
        />
      </button>

      {isOpen && (
        <div className="absolute right-0 top-full mt-1 w-64 bg-[#1a1c24] border border-[#ffffff1a] rounded-lg shadow-xl z-50 overflow-hidden flex flex-col font-mono text-xs">
          <div className="p-2 border-b border-[#ffffff1a] bg-[#0e1117] flex justify-between items-center">
            <span className="text-[#888] font-semibold uppercase">
              Saved Layouts
            </span>
            <button
              onClick={() => setIsSaving(!isSaving)}
              className="text-indigo-400 hover:text-indigo-300 flex items-center gap-1"
            >
              <Save size={12} /> Save
            </button>
          </div>

          {isSaving && (
            <div className="p-2 bg-[#ffffff05] border-b border-[#ffffff1a] flex gap-2">
              <input
                type="text"
                value={newWorkspaceName}
                onChange={(e) => setNewWorkspaceName(e.target.value)}
                placeholder="Name..."
                className="flex-1 bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 h-7 focus:outline-none focus:border-indigo-500 text-[#fff]"
                autoFocus
              />
              <button
                onClick={handleSave}
                className="bg-indigo-600 hover:bg-indigo-500 text-white px-2 rounded font-semibold transition-colors disabled:opacity-50"
                disabled={!newWorkspaceName.trim()}
              >
                OK
              </button>
            </div>
          )}

          <div className="max-h-48 overflow-y-auto w-full flex flex-col">
            {workspaces.length === 0 ? (
              <span className="p-4 text-center text-[#555] italic">
                No saved workspaces
              </span>
            ) : (
              workspaces.map((ws) => (
                <button
                  key={ws.id}
                  className="flex justify-between items-center px-3 py-2 border-b border-[#ffffff0a] hover:bg-[#ffffff10] text-left transition-colors group"
                  onClick={() => handleLoad(ws)}
                >
                  <span className="truncate pr-2 text-[#ccc] group-hover:text-white">
                    {ws.name}
                  </span>
                  <div
                    onClick={(e) => handleDelete(ws.id, e)}
                    className="text-[#555] hover:text-red-400 p-1 opacity-0 group-hover:opacity-100 transition-opacity"
                  >
                    <Trash2 size={12} />
                  </div>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
