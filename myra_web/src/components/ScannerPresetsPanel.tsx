import { useState, useEffect } from 'react';
import { X, Trash2, Download, Upload, SlidersHorizontal, Database } from 'lucide-react';
import { 
    ScannerModule, 
    ScannerPreset, 
    getAllPresets, 
    deleteUserPreset,
    exportPresetsToJSON,
    importPresetsFromJSON
} from '../lib/scannerPresets';

interface ScannerPresetsPanelProps {
    onClose: () => void;
    onLoad: (preset: ScannerPreset) => void;
}

export default function ScannerPresetsPanel({ onClose, onLoad }: ScannerPresetsPanelProps) {
    const [tab, setTab] = useState<'All' | ScannerModule>('All');
    const [allPresets, setAllPresets] = useState<ScannerPreset[]>([]);
    const [renderTick, setRenderTick] = useState(0);

    const forceUpdate = (fn: (n: number) => number) => {
        setRenderTick(fn);
    };

    useEffect(() => {
        setAllPresets(getAllPresets());
    }, [renderTick]);

    const filteredPresets = allPresets.filter(p => tab === 'All' || p.module === tab);

    return (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
            <div
                className="bg-[#1a1c24] border border-[#ffffff1a] rounded-lg shadow-2xl w-full max-w-2xl overflow-hidden flex flex-col max-h-[85vh]"
                role="dialog"
                aria-modal="true"
                aria-labelledby="scanner-presets-title"
            >
                <div className="px-5 py-4 border-b border-[#ffffff1a] flex justify-between items-center bg-[#1e2028]">
                    <h3 id="scanner-presets-title" className="font-semibold text-white flex items-center gap-2">
                        <SlidersHorizontal size={18} className="text-orange-400" aria-hidden="true" />
                        Scanner Presets
                    </h3>
                    <button onClick={onClose} className="p-1 hover:bg-[#ffffff1a] rounded text-[#888] hover:text-white transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/50" aria-label="Close presets panel">
                        <X size={18} aria-hidden="true" />
                    </button>
                </div>

                <div className="p-5 flex-1 overflow-y-auto">
                    <div className="flex flex-wrap gap-2 mb-6 border-b border-[#ffffff1a] pb-2" role="tablist" aria-label="Preset categories">
                        <button role="tab" aria-selected={tab === 'All'} onClick={() => setTab('All')} className={`text-xs font-mono px-3 py-1.5 rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/50 ${tab === 'All' ? 'bg-[#ffffff1a] text-white' : 'text-[#888] hover:text-white'}`}>All</button>
                        <button role="tab" aria-selected={tab === 'ReversionEngine'} onClick={() => setTab('ReversionEngine')} className={`text-xs font-mono px-3 py-1.5 rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/50 ${tab === 'ReversionEngine' ? 'bg-[#ffffff1a] text-cyan-400' : 'text-[#888] hover:text-cyan-400'}`}>Reversion Engine</button>
                        <button role="tab" aria-selected={tab === 'MultibaggerMatrix'} onClick={() => setTab('MultibaggerMatrix')} className={`text-xs font-mono px-3 py-1.5 rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/50 ${tab === 'MultibaggerMatrix' ? 'bg-[#ffffff1a] text-orange-400' : 'text-[#888] hover:text-orange-400'}`}>Multibagger Matrix</button>
                        <button role="tab" aria-selected={tab === 'PriceDeliveryDivergence'} onClick={() => setTab('PriceDeliveryDivergence')} className={`text-xs font-mono px-3 py-1.5 rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/50 ${tab === 'PriceDeliveryDivergence' ? 'bg-[#ffffff1a] text-yellow-400' : 'text-[#888] hover:text-yellow-400'}`}>Divergence Scanner</button>
                        <button role="tab" aria-selected={tab === 'ValueRanker'} onClick={() => setTab('ValueRanker')} className={`text-xs font-mono px-3 py-1.5 rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/50 ${tab === 'ValueRanker' ? 'bg-[#ffffff1a] text-green-400' : 'text-[#888] hover:text-green-400'}`}>Value Ranker</button>
                        <button role="tab" aria-selected={tab === 'InvisibleHand'} onClick={() => setTab('InvisibleHand')} className={`text-xs font-mono px-3 py-1.5 rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/50 ${tab === 'InvisibleHand' ? 'bg-[#ffffff1a] text-violet-400' : 'text-[#888] hover:text-violet-400'}`}>Invisible Hand</button>
                        <button role="tab" aria-selected={tab === 'Trigger'} onClick={() => setTab('Trigger')} className={`text-xs font-mono px-3 py-1.5 rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/50 ${tab === 'Trigger' ? 'bg-[#ffffff1a] text-orange-400' : 'text-[#888] hover:text-orange-400'}`}>The Trigger</button>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4" role="tabpanel" aria-label={`${tab === 'All' ? 'All' : tab} presets`}>
                        {filteredPresets.map(preset => (
                            <div key={preset.id} className="bg-[#2a2c34] border border-[#ffffff0a] rounded-lg p-4 flex flex-col gap-2 relative group hover:border-[#ffffff2a] transition-all">
                                <div className="flex justify-between items-start">
                                    <div className="flex flex-col">
                                        <span className="text-sm font-semibold text-white">{preset.name}</span>
                                        <span className="text-[12px] text-[#888] font-mono">{preset.module}</span>
                                    </div>
                                    <div className="flex items-center gap-1">
                                        {preset.isDefault ? (
                                            <span className="text-[12px] uppercase tracking-wider bg-blue-500/20 text-blue-400 px-1.5 py-0.5 rounded">System</span>
                                        ) : (
                                            <button 
                                                onClick={() => {
                                                    deleteUserPreset(preset.id);
                                                    setRenderTick(t => t+1);
                                                }}
                                                className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100 p-1 hover:bg-red-500/20 text-red-500 rounded transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/50"
                                                title={`Delete ${preset.name} preset`}
                                                aria-label={`Delete custom preset ${preset.name}`}
                                            >
                                                <Trash2 size={12} aria-hidden="true" />
                                            </button>
                                        )}
                                    </div>
                                </div>
                                
                                {preset.description && (
                                    <p className="text-xs text-[#aaa] mt-1 line-clamp-2">{preset.description}</p>
                                )}

                                <div className="mt-3 flex justify-end">
                                    <button 
                                        onClick={() => onLoad(preset)}
                                        className="text-xs bg-[#ffffff1a] hover:bg-[#ffffff2a] text-white px-3 py-1.5 rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/50"
                                        aria-label={`Load and run ${preset.name}`}
                                    >
                                        Load & Run
                                    </button>
                                </div>
                            </div>
                        ))}
                        {filteredPresets.length === 0 && (
                            <div className="col-span-full py-12 flex flex-col items-center justify-center text-[#888] font-mono text-xs text-center border border-dashed border-[#ffffff1a] rounded-lg" role="status">
                                <Database size={24} className="mb-2 opacity-50" aria-hidden="true" />
                                No presets found for this category.
                            </div>
                        )}
                    </div>
                </div>

                {/* Export/Import section */}
                <div className="px-5 py-3 border-t border-white/5 bg-black/10 flex items-center gap-3">
                  <button
                    onClick={() => {
                      const json = exportPresetsToJSON();
                      const blob = new Blob([json], { type: 'application/json' });
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement('a');
                      a.href = url;
                      a.download = 'myra_scanner_presets.json';
                      a.click();
                      URL.revokeObjectURL(url);
                    }}
                    className="text-[12px] font-mono text-[#888] hover:text-white border border-white/10 px-2.5 py-1 rounded flex items-center gap-1.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/50"
                    aria-label="Export presets to JSON file"
                  >
                    <Download size={10} aria-hidden="true" /> Export
                  </button>
                  <label
                    className="text-[12px] font-mono text-[#888] hover:text-white border border-white/10 px-2.5 py-1 rounded flex items-center gap-1.5 cursor-pointer focus-within:outline-none focus-within:ring-2 focus-within:ring-cyan-500/50"
                    aria-label="Import presets from JSON file"
                  >
                    <Upload size={10} aria-hidden="true" /> Import
                    <input
                      type="file"
                      accept=".json"
                      className="sr-only"
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        if (!file) return;
                        const reader = new FileReader();
                        reader.onload = () => {
                          const count = importPresetsFromJSON(reader.result as string);
                          if (count > 0) alert(`Imported ${count} presets.`);
                          else if (count === 0) alert('No new presets to import.');
                          else alert('Invalid JSON file.');
                          forceUpdate(n => n + 1);
                        };
                        reader.readAsText(file);
                      }}
                    />
                  </label>
                </div>
            </div>
        </div>
    );
}
