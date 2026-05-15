import React, { useState } from 'react';
import { LiqVoidSettings, SmpSettings, VOID_THRESHOLDS, SMP_THRESHOLDS } from '../lib/indicatorConfig';

interface IndicatorSettingsPanelProps {
    bucket: string | null;
    liqVoidSettings: LiqVoidSettings;
    smpSettings: SmpSettings;
    onLiqVoidChange: (s: LiqVoidSettings) => void;
    onSmpChange: (s: SmpSettings) => void;
    onClose: () => void;
}

const SliderRow = ({ label, value, min, max, step, onChange, defaultVal, onReset }: any) => (
    <div className="flex flex-col gap-1 mb-3">
        <div className="flex justify-between items-center text-xs">
            <span className="text-[#aaa]">{label}</span>
            <div className="flex items-center gap-2">
                <span className="font-mono text-cyan-400">{Number(value).toFixed(2)}</span>
                <button onClick={() => onReset(defaultVal)} className="text-[10px] bg-[#ffffff10] hover:bg-[#ffffff20] px-1.5 rounded text-[#888]">Reset</button>
            </div>
        </div>
        <input type="range" min={min} max={max} step={step} value={value} onChange={e => onChange(Number(e.target.value))} className="w-full accent-cyan-500" />
    </div>
);

const ToggleRow = ({ label, checked, onChange }: any) => (
    <label className="flex items-center justify-between text-xs text-[#aaa] cursor-pointer mb-3">
        {label}
        <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} className="accent-cyan-500" />
    </label>
);

export default function IndicatorSettingsPanel({ bucket, liqVoidSettings, smpSettings, onLiqVoidChange, onSmpChange, onClose }: IndicatorSettingsPanelProps) {
    const [tab, setTab] = useState<'lv' | 'smp'>('lv');

    const curBucket = bucket || 'Broader Market (N500)';
    const defLV = VOID_THRESHOLDS[curBucket] || VOID_THRESHOLDS['Broader Market (N500)'];
    const defSMP = SMP_THRESHOLDS[curBucket] || SMP_THRESHOLDS['Broader Market (N500)'];

    return (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded-lg w-full max-w-md overflow-hidden flex flex-col shadow-2xl">
                <div className="flex items-center justify-between p-3 border-b border-[#ffffff1a] bg-[#ffffff05]">
                    <h3 className="text-sm font-bold text-white">Indicator Settings</h3>
                    <button onClick={onClose} className="text-[#888] hover:text-white">✕</button>
                </div>
                
                <div className="flex border-b border-[#ffffff1a]">
                    <button onClick={() => setTab('lv')} className={`flex-1 py-2 text-xs font-semibold ${tab === 'lv' ? 'text-cyan-400 border-b-2 border-cyan-400 bg-[#ffffff0a]' : 'text-[#888] hover:bg-[#ffffff05]'}`}>
                        Liquidity Voids
                    </button>
                    <button onClick={() => setTab('smp')} className={`flex-1 py-2 text-xs font-semibold ${tab === 'smp' ? 'text-cyan-400 border-b-2 border-cyan-400 bg-[#ffffff0a]' : 'text-[#888] hover:bg-[#ffffff05]'}`}>
                        Smart Money Prints
                    </button>
                </div>

                <div className="p-4 bg-[#0e1117] flex-1 overflow-y-auto">
                    {tab === 'lv' && (
                        <div>
                            <div className="text-[10px] text-fuchsia-400 mb-4 bg-fuchsia-400/10 p-2 rounded border border-fuchsia-400/20">
                                Mode: <span className="font-bold">{curBucket}</span>
                            </div>
                            <SliderRow 
                                label="Min ATR Multiplier" value={liqVoidSettings.minAtrMultiplier} min={0.5} max={4.0} step={0.1}
                                onChange={(v: number) => onLiqVoidChange({...liqVoidSettings, minAtrMultiplier: v})}
                                defaultVal={defLV.minAtrMultiplier} onReset={(v: number) => onLiqVoidChange({...liqVoidSettings, minAtrMultiplier: v})}
                            />
                            <SliderRow 
                                label="Max Volume Multiplier" value={liqVoidSettings.maxVolumeMultiplier} min={0.1} max={1.5} step={0.05}
                                onChange={(v: number) => onLiqVoidChange({...liqVoidSettings, maxVolumeMultiplier: v})}
                                defaultVal={defLV.maxVolumeMultiplier} onReset={(v: number) => onLiqVoidChange({...liqVoidSettings, maxVolumeMultiplier: v})}
                            />
                            <SliderRow 
                                label="Min Strength Score" value={liqVoidSettings.minStrengthScore} min={10} max={90} step={5}
                                onChange={(v: number) => onLiqVoidChange({...liqVoidSettings, minStrengthScore: v})}
                                defaultVal={50} onReset={(v: number) => onLiqVoidChange({...liqVoidSettings, minStrengthScore: v})}
                            />
                            <ToggleRow 
                                label="Hide Filled Voids" checked={liqVoidSettings.hideFilledVoids} 
                                onChange={(v: boolean) => onLiqVoidChange({...liqVoidSettings, hideFilledVoids: v})}
                            />
                            <ToggleRow 
                                label="Volatility Regime Scaling" checked={liqVoidSettings.volatilityScaling} 
                                onChange={(v: boolean) => onLiqVoidChange({...liqVoidSettings, volatilityScaling: v})}
                            />
                        </div>
                    )}
                    
                    {tab === 'smp' && (
                        <div>
                            <div className="text-[10px] text-fuchsia-400 mb-4 bg-fuchsia-400/10 p-2 rounded border border-fuchsia-400/20">
                                Mode: <span className="font-bold">{curBucket}</span>
                            </div>
                            <SliderRow 
                                label="Min Volume Spike (xAvg)" value={smpSettings.minVolumeSpike} min={1.0} max={5.0} step={0.1}
                                onChange={(v: number) => onSmpChange({...smpSettings, minVolumeSpike: v})}
                                defaultVal={defSMP.minVolumeSpike} onReset={(v: number) => onSmpChange({...smpSettings, minVolumeSpike: v})}
                            />
                            <SliderRow 
                                label="Min Delivery %" value={smpSettings.minDeliveryPct} min={30} max={95} step={1}
                                onChange={(v: number) => onSmpChange({...smpSettings, minDeliveryPct: v})}
                                defaultVal={defSMP.minDeliveryPct} onReset={(v: number) => onSmpChange({...smpSettings, minDeliveryPct: v})}
                            />
                            <SliderRow 
                                label="Accumulation Zone (> x)" value={smpSettings.accumulationCloseZone} min={0.5} max={1.0} step={0.05}
                                onChange={(v: number) => onSmpChange({...smpSettings, accumulationCloseZone: v})}
                                defaultVal={0.7} onReset={(v: number) => onSmpChange({...smpSettings, accumulationCloseZone: v})}
                            />
                            <SliderRow 
                                label="Distribution Zone (< x)" value={smpSettings.distributionCloseZone} min={0.0} max={0.5} step={0.05}
                                onChange={(v: number) => onSmpChange({...smpSettings, distributionCloseZone: v})}
                                defaultVal={0.3} onReset={(v: number) => onSmpChange({...smpSettings, distributionCloseZone: v})}
                            />
                            
                            <div className="mt-4 pt-4 border-t border-[#ffffff1a]">
                                <h4 className="text-xs font-bold text-[#888] mb-3 uppercase tracking-wider">Signal Types</h4>
                                <ToggleRow 
                                    label="Show Accumulation / Absorption" checked={smpSettings.showAccumulation} 
                                    onChange={(v: boolean) => onSmpChange({...smpSettings, showAccumulation: v})}
                                />
                                <ToggleRow 
                                    label="Show Distribution / Exhaustion" checked={smpSettings.showDistribution} 
                                    onChange={(v: boolean) => onSmpChange({...smpSettings, showDistribution: v})}
                                />
                                <ToggleRow 
                                    label="Hide Indecision" checked={smpSettings.hideIndecision} 
                                    onChange={(v: boolean) => onSmpChange({...smpSettings, hideIndecision: v})}
                                />
                                <ToggleRow 
                                    label="Volatility Regime Scaling" checked={smpSettings.volatilityScaling} 
                                    onChange={(v: boolean) => onSmpChange({...smpSettings, volatilityScaling: v})}
                                />
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
