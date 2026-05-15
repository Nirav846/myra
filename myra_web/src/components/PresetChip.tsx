import { useState, useEffect } from 'react';
import { Save, Plus } from 'lucide-react';
import { 
    ScannerModule, 
    ScannerPreset, 
    getAllPresets, 
    saveUserPreset
} from '../lib/scannerPresets';

interface PresetChipProps {
    module: ScannerModule;
    currentConfig: any;
    onLoad: (config: any) => void;
    accentColor?: string;
}

export default function PresetChip({ module, currentConfig, onLoad, accentColor = 'blue' }: PresetChipProps) {
    const [presets, setPresets] = useState<ScannerPreset[]>([]);
    const [isSaving, setIsSaving] = useState(false);
    const [saveName, setSaveName] = useState('');

    useEffect(() => {
        setPresets(getAllPresets().filter(p => p.module === module));
    }, [module]);

    const handleSave = () => {
        if (!saveName.trim()) return;
        saveUserPreset({
            name: saveName.trim(),
            module,
            config: currentConfig
        });
        setPresets(getAllPresets().filter(p => p.module === module));
        setIsSaving(false);
        setSaveName('');
    };

    const handleLoad = (preset: ScannerPreset) => {
        onLoad(preset.config);
    };

    const isMatch = (preset: ScannerPreset) => {
        const keys = Object.keys(preset.config);
        // Avoid comparing deep objects, just shallow matching config
        return keys.every(k => preset.config[k] === currentConfig[k]);
    };

    return (
        <div className="flex flex-col gap-2 p-3 bg-[#0e1117] border border-[#ffffff0a] rounded-lg">
            <div className="flex items-center justify-between">
                <label className="text-[10px] text-[#888] font-mono uppercase">Strategy Presets</label>
            </div>
            <div className="flex flex-wrap gap-2 items-center">
                {presets.map(p => {
                    const active = isMatch(p);
                    // Use a safe standard fallback for tailwind dynamic classes if specific accent isn't generated
                    const isOrange = accentColor === 'orange';
                    const isCyan = accentColor === 'cyan';
                    
                    let activeClasses = 'border-[#ffffff1a] bg-[#1a1c24] text-[#aaa] hover:border-[#ffffff3a]';
                    if (active) {
                         if (isOrange) activeClasses = 'border-orange-500 bg-orange-500/10 text-orange-400';
                         else if (isCyan) activeClasses = 'border-cyan-500 bg-cyan-500/10 text-cyan-400';
                         else activeClasses = `border-blue-500 bg-blue-500/10 text-blue-400`;
                    }

                    return (
                        <button
                            key={p.id}
                            onClick={() => handleLoad(p)}
                            title={p.description || p.name}
                            className={`px-3 py-1.5 rounded-full text-[10px] font-mono whitespace-nowrap transition-colors border ${activeClasses}`}
                        >
                            {p.name}
                        </button>
                    )
                })}
                {!isSaving ? (
                    <button 
                        onClick={() => setIsSaving(true)}
                        className="px-2 py-1.5 rounded-full text-[10px] font-mono whitespace-nowrap transition-colors border border-[#ffffff1a] bg-[#1a1c24] text-[#888] hover:text-[#fff] flex items-center gap-1"
                    >
                        <Plus size={10} /> Save Current
                    </button>
                ) : (
                    <div className="flex items-center gap-1">
                        <input 
                            type="text" 
                            value={saveName} 
                            onChange={(e) => setSaveName(e.target.value)}
                            placeholder="Preset name..."
                            autoFocus
                            onKeyDown={e => e.key === 'Enter' && handleSave()}
                            className="bg-[#1a1c24] border border-[#ffffff1a] rounded px-2 py-1 text-[10px] text-[#fafafa] outline-none"
                        />
                        <button onClick={handleSave} className="p-1 bg-green-500/20 text-green-400 rounded hover:bg-green-500/30">
                            <Save size={12} />
                        </button>
                        <button onClick={() => setIsSaving(false)} className="p-1 bg-red-500/20 text-red-400 rounded hover:bg-red-500/30 text-[10px]">
                            ✕
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}
