import React from "react";
import { PanelLeftClose, Settings2, Info } from "lucide-react";

interface SidebarToggle {
  id: string;
  label: string;
  state: boolean;
  set: (val: boolean) => void;
  desc?: string;
  color?: string;
}

interface ChartSidebarProps {
  sidebarOpen: boolean;
  setSidebarOpen: (val: boolean) => void;
  toggles: SidebarToggle[];
  profileResolution: string;
  setProfileResolution: (val: string) => void;
}

export const ChartSidebar: React.FC<ChartSidebarProps> = ({
  sidebarOpen,
  setSidebarOpen,
  toggles,
  profileResolution,
  setProfileResolution,
}) => {
  return (
    <div
      className={`flex flex-col border-r border-[#ffffff1a] transition-all duration-300 ${sidebarOpen ? "w-64" : "w-0 overflow-hidden"}`}
    >
      <div className="h-14 flex items-center justify-between px-4 border-b border-[#ffffff1a] shrink-0">
        <div className="flex items-center gap-2">
          <Settings2 size={16} className="text-gray-400" />
          <span className="text-sm font-semibold text-white tracking-wide uppercase">
            Indicators
          </span>
        </div>
        <button
          onClick={() => setSidebarOpen(false)}
          className="p-1.5 text-gray-400 hover:text-white hover:bg-[#ffffff10] rounded-md transition-colors"
        >
          <PanelLeftClose size={18} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-6 custom-scrollbar bg-[#16181d]">
        <div className="space-y-3">
          <h3 className="text-[10px] font-bold text-gray-500 uppercase tracking-widest mb-4 border-b border-white/5 pb-2">
            Analysis Objects
          </h3>
          <div className="space-y-1.5 px-1">
            {toggles.map((toggle) => (
              <div
                key={toggle.id}
                className="group relative flex items-center gap-2"
              >
                <label className="flex items-center gap-2 cursor-pointer flex-1 group-hover:text-white">
                  <input
                    type="checkbox"
                    checked={toggle.state}
                    onChange={(e) => toggle.set(e.target.checked)}
                    className="accent-cyan-500 w-3.5 h-3.5 rounded border-[#ffffff33] bg-transparent focus:ring-offset-0 focus:ring-0"
                  />
                  <span
                    className="text-[11px] font-mono transition-colors group-hover:brightness-150"
                    style={{ color: toggle.color || "#ccc" }}
                  >
                    {toggle.label}
                  </span>
                </label>

                {toggle.id === "del_profile" && toggle.state && (
                  <select
                    value={profileResolution}
                    onChange={(e) => setProfileResolution(e.target.value)}
                    className="ml-2 bg-[#1a1c24] border border-[#ffffff1a] rounded px-1.5 py-0.5 text-[10px] text-[#ccc] font-mono focus:outline-none focus:border-cyan-500 shadow-sm transition-all"
                  >
                    <option value="auto">Visible</option>
                    <option value="weekly">Weekly</option>
                    <option value="monthly">Monthly</option>
                    <option value="cumulative">All</option>
                  </select>
                )}

                {toggle.desc && (
                  <div className="group/hint relative">
                    <Info
                      size={12}
                      className="text-gray-600 hover:text-cyan-500 cursor-help transition-colors"
                    />
                    <div className="absolute left-full ml-2 top-1/2 -translate-y-1/2 w-48 p-2 bg-[#1a1c24] border border-[#ffffff1a] rounded-lg text-[10px] text-gray-400 shadow-2xl opacity-0 invisible group-hover/hint:opacity-100 group-hover/hint:visible transition-all z-50 pointer-events-none">
                      {toggle.desc}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        <div className="pt-4 border-t border-[#ffffff0a]">
          <div className="p-3 bg-cyan-500/5 rounded-lg border border-cyan-500/10">
            <div className="flex items-center gap-2 mb-2">
              <div className="w-1.5 h-1.5 rounded-full bg-cyan-500 animate-pulse" />
              <span className="text-[10px] font-bold text-cyan-400 uppercase tracking-wider">
                Strategy Insight
              </span>
            </div>
            <p className="text-[10px] text-gray-500 leading-relaxed italic">
              Combine "Divergence Core" with "Inst. Blocks" to identify
              institutional absorption zones near SR levels.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};
