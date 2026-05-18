import { useState, useEffect, useCallback } from 'react';
import { Librarian } from '../lib/Librarian';
import { BrainCircuit, ChevronDown, ChevronRight, Activity, Cpu, Play, SlidersHorizontal, Rocket, Tag } from 'lucide-react';
import { ResponsiveContainer, BarChart, CartesianGrid, XAxis, YAxis, Tooltip, Bar } from 'recharts';

interface MLStatus {
  trained: boolean;
  training_date?: string;
  accuracy?: number;
  n_symbols?: number;
  n_features?: number;
  history?: any[];
}

interface LaunchpadStatus {
  exists: boolean;
  training_date?: string;
  n_labeled_events?: number;
  accuracy?: number;
  rmse_return?: number;
  rmse_days?: number;
}

interface FeatureImportance {
  feature: string;
  importance: number;
}

interface Prediction {
  symbol: string;
  predicted_class: 'TOP_QUARTILE' | 'MIDDLE' | 'BOTTOM_QUARTILE';
  confidence: number;
}

const FORWARD_RETURN_FEATURES = [
  'delivery_pct', 'delivery_divergence_score', 'volatility_compression_score',
  'relative_volume_score', 'nifty_outperformance_score', 'stock_return',
  'bullish_fvg', 'bearish_fvg', 'has_bullish_fvg', 'fvg_freshness',
  'liquidity_distance', 'close', 'volume', 'delivery', 'market_return',
  'fvg_top', 'fvg_bottom', 'swing_high', 'swing_low', 'trend_alignment',
  'delivery_ma_60'
];

const LAUNCHPAD_FEATURES = [
  'delivery_pct', 'relative_volume', 'fvg_distance', 'days_since_trigger',
  'digestion_volatility', 'z_score', 'trend_alignment', 'breakout_volume_mult'
];

export default function MLLabView({ lib }: { lib: Librarian }) {
  const [labMode, setLabMode] = useState<'forward_return' | 'launchpad'>('forward_return');
  const [toast, setToast] = useState<{message: string, type: 'success'|'error'} | null>(null);

  const showToast = (message: string, type: 'success' | 'error') => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  };

  // --- FORWARD RETURN STATE ---
  const [status, setStatus] = useState<MLStatus | null>(null);
  const [showFeatures, setShowFeatures] = useState(true);
  const [showHyperparams, setShowHyperparams] = useState(true);
  const [selectedFeatures, setSelectedFeatures] = useState<string[]>([]);
  const [hyperparams, setHyperparams] = useState({
    lookback_days: 252, forward_prediction_days: 5, n_estimators: 200,
    max_depth: 5, learning_rate: 0.05, test_split_pct: 20
  });
  const [training, setTraining] = useState(false);
  const [predicting, setPredicting] = useState(false);
  const [fetchingImportance, setFetchingImportance] = useState(false);
  const [activeTab, setActiveTab] = useState<'predictions' | 'importance' | 'history'>('predictions');
  const [predictions, setPredictions] = useState<Prediction[] | null>(null);
  const [importance, setImportance] = useState<FeatureImportance[] | null>(null);

  // --- LAUNCHPAD STATE ---
  const [lpStatus, setLpStatus] = useState<LaunchpadStatus | null>(null);
  const [showLpLabeling, setShowLpLabeling] = useState(true);
  const [showLpFeatures, setShowLpFeatures] = useState(true);
  const [showLpHyperparams, setShowLpHyperparams] = useState(true);
  const [lpSelectedFeatures, setLpSelectedFeatures] = useState<string[]>([]);
  const [lpHyperparams, setLpHyperparams] = useState({
    n_estimators: 200, max_depth: 5, learning_rate: 0.05, subsample: 0.8, colsample_bytree: 0.8
  });
  const [lpLabelConfig, setLpLabelConfig] = useState({
    z_score_min: 2.0, delivery_pct_min: 50, digestion_window_days: 10, breakout_volume_mult: 2.5
  });
  const [lpLabeling, setLpLabeling] = useState(false);
  const [lpTraining, setLpTraining] = useState(false);
  const [lpFetchingImportance, setLpFetchingImportance] = useState(false);
  const [lpImportance, setLpImportance] = useState<FeatureImportance[] | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch('http://localhost:8000/api/ml/status');
      if (res.ok) {
        setStatus(await res.json());
      }
    } catch (e) { console.warn("Failed FR status"); }
  }, []);

  const fetchLpStatus = useCallback(async () => {
    try {
      const res = await fetch('http://localhost:8000/api/ml/launchpad/status');
      if (res.ok) {
        setLpStatus(await res.json());
      }
    } catch (e) { console.warn("Failed LP status"); }
  }, []);

  const fetchConfig = useCallback(async () => {
    try {
      const res = await fetch('http://localhost:8000/api/ml/config');
      if (res.ok) {
        const data = await res.json();
        // FR
        if (data.features) setSelectedFeatures(data.features);
        if (data.hyperparameters) setHyperparams(prev => ({ ...prev, ...data.hyperparameters }));
        // LP
        if (data.launchpad_features) setLpSelectedFeatures(data.launchpad_features);
        if (data.launchpad_hyperparameters) setLpHyperparams(prev => ({ ...prev, ...data.launchpad_hyperparameters }));
        if (data.launchpad_label_config) setLpLabelConfig(prev => ({ ...prev, ...data.launchpad_label_config }));
      }
    } catch (e) { console.warn("Failed config"); }
  }, []);

  useEffect(() => {
    fetchStatus();
    fetchLpStatus();
    fetchConfig();
  }, [fetchStatus, fetchLpStatus, fetchConfig]);

  const saveConfig = async (payload: any) => {
    try {
      await fetch('http://localhost:8000/api/ml/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
    } catch (e) { console.warn("Save config failed"); }
  };

  // --- FORWARD RETURN ACTIONS ---
  const handleTrain = async () => {
    setTraining(true);
    try {
      const res = await fetch('http://localhost:8000/api/ml/train', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ features: selectedFeatures, hyperparameters: hyperparams })
      });
      if (res.ok) {
        showToast("Model trained successfully", "success");
        await fetchStatus();
      } else { showToast("Training failed", "error"); }
    } catch (e: any) { showToast(e.message, "error"); }
    finally { setTraining(false); }
  };

  const handlePredict = async () => {
    setPredicting(true);
    setActiveTab('predictions');
    try {
      const res = await fetch('http://localhost:8000/api/ml/predict');
      if (res.ok) {
        const data = await res.json();
        setPredictions(data.predictions || []);
      } else { showToast("Prediction failed", "error"); }
    } catch (e: any) { showToast(e.message, "error"); }
    finally { setPredicting(false); }
  };

  const handleImportance = async () => {
    setFetchingImportance(true);
    setActiveTab('importance');
    try {
      const res = await fetch('http://localhost:8000/api/ml/feature-importance');
      if (res.ok) {
        const data = await res.json();
        setImportance(data.importance || []);
      } else { showToast("Importance failed", "error"); }
    } catch (e: any) { showToast(e.message, "error"); }
    finally { setFetchingImportance(false); }
  };

  // --- LAUNCHPAD ACTIONS ---
  const handleLpLabel = async () => {
    setLpLabeling(true);
    try {
      const res = await fetch('http://localhost:8000/api/ml/launchpad/label', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(lpLabelConfig)
      });
      if (res.ok) {
        const data = await res.json();
        showToast(`Labelling complete. ${data.labeled_count || 0} events found.`, "success");
        await fetchLpStatus();
      } else { showToast("Labelling failed", "error"); }
    } catch (e: any) { showToast(e.message, "error"); }
    finally { setLpLabeling(false); }
  };

  const handleLpTrain = async () => {
    setLpTraining(true);
    try {
      const res = await fetch('http://localhost:8000/api/ml/launchpad/train', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ features: lpSelectedFeatures, hyperparameters: lpHyperparams })
      });
      if (res.ok) {
        showToast("Launchpad model trained", "success");
        await fetchLpStatus();
      } else { showToast("Training failed", "error"); }
    } catch (e: any) { showToast(e.message, "error"); }
    finally { setLpTraining(false); }
  };

  const handleLpImportance = async () => {
    setLpFetchingImportance(true);
    try {
      const res = await fetch('http://localhost:8000/api/ml/launchpad/feature-importance');
      if (res.ok) {
        const data = await res.json();
        setLpImportance(data.importance || []);
      } else { showToast("Importance failed", "error"); }
    } catch (e: any) { showToast(e.message, "error"); }
    finally { setLpFetchingImportance(false); }
  };

  return (
    <div className="flex flex-col h-full relative">
      {toast && (
        <div className={`absolute top-4 right-4 z-50 px-4 py-2 rounded text-sm font-mono shadow-lg border ${
          toast.type === 'success' ? 'bg-green-900/50 border-green-500/50 text-green-300' : 'bg-red-900/50 border-red-500/50 text-red-300'
        }`}>
          {toast.message}
        </div>
      )}

      <div className="flex items-center gap-3 mb-2 p-4 pb-0">
        <div className="bg-indigo-500/20 p-2 rounded">
          <BrainCircuit className="text-indigo-400" size={24} />
        </div>
        <div>
          <h1 className="text-xl font-bold tracking-tight text-[#fafafa]">ML Lab</h1>
          <p className="text-sm text-[#888]">MYRA Machine Learning Pipeline Control</p>
        </div>
      </div>

      <div className="flex gap-2 px-4 py-2 border-b border-[#ffffff1a]">
        <button
          onClick={() => setLabMode('forward_return')}
          className={`px-4 py-2 text-xs font-semibold uppercase tracking-wider rounded ${labMode === 'forward_return' ? 'bg-indigo-600 text-white' : 'bg-[#ffffff0a] text-[#888] hover:text-[#ccc]'}`}
        >
          Forward Return
        </button>
        <button
          onClick={() => setLabMode('launchpad')}
          className={`px-4 py-2 text-xs font-semibold uppercase tracking-wider rounded flex items-center gap-2 ${labMode === 'launchpad' ? 'bg-red-600 text-white' : 'bg-[#ffffff0a] text-[#888] hover:text-[#ccc]'}`}
        >
          Launchpad
        </button>
      </div>

      {labMode === 'forward_return' ? (
        <div className="flex flex-1 overflow-hidden p-4 gap-4 pt-2">
          {/* LEFT PANEL - FORWARD RETURN */}
          <div className="w-80 flex flex-col gap-4 overflow-y-auto shrink-0 pr-2">
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded-xl p-4 flex flex-col gap-2">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-[#888] flex items-center gap-2">
                <Activity size={14} /> Model Status
              </h3>
              {status ? (
                <div className="space-y-2 mt-2">
                  <div className="flex justify-between items-center text-xs font-mono">
                    <span className="text-[#666]">Status:</span>
                    <span className={status.trained ? "text-green-400 font-bold" : "text-yellow-400 font-bold"}>
                      {status.trained ? `Trained (${status.training_date?.split('T')[0] || 'Unknown'})` : 'Not trained'}
                    </span>
                  </div>
                  {status.trained && (
                    <>
                      <div className="flex justify-between items-center text-xs font-mono">
                        <span className="text-[#666]">Accuracy:</span>
                        <span className="text-[#ccc]">{(status.accuracy || 0).toFixed(2)}%</span>
                      </div>
                      <div className="flex justify-between items-center text-xs font-mono">
                         <span className="text-[#666]">Symbols:</span>
                         <span className="text-[#ccc]">{status.n_symbols || 0}</span>
                      </div>
                      <div className="flex justify-between items-center text-xs font-mono">
                         <span className="text-[#666]">Features:</span>
                         <span className="text-[#ccc]">{status.n_features || 0}</span>
                      </div>
                    </>
                  )}
                </div>
              ) : <div className="text-xs text-[#666] font-mono mt-2">Loading...</div>}
            </div>

            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded-xl overflow-hidden">
              <button onClick={() => setShowFeatures(!showFeatures)} className="w-full p-4 flex justify-between items-center bg-[#ffffff05] hover:bg-[#ffffff0a] transition-colors">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-[#888] flex items-center gap-2">
                  <Cpu size={14} /> Feature Selection
                </h3>
                {showFeatures ? <ChevronDown size={14} className="text-[#666]" /> : <ChevronRight size={14} className="text-[#666]" />}
              </button>
              {showFeatures && (
                <div className="p-4 pt-2 border-t border-[#ffffff0a]">
                  <div className="flex gap-2 mb-3">
                    <button onClick={() => { setSelectedFeatures(FORWARD_RETURN_FEATURES); saveConfig({ features: FORWARD_RETURN_FEATURES, hyperparameters: hyperparams }); }} className="text-[10px] bg-[#ffffff0a] hover:bg-[#ffffff1a] text-[#aaa] px-2 py-1 rounded">All</button>
                    <button onClick={() => { setSelectedFeatures([]); saveConfig({ features: [], hyperparameters: hyperparams }); }} className="text-[10px] bg-[#ffffff0a] hover:bg-[#ffffff1a] text-[#aaa] px-2 py-1 rounded">None</button>
                  </div>
                  <div className="space-y-1.5 max-h-48 overflow-y-auto pr-1">
                    {FORWARD_RETURN_FEATURES.map(feat => (
                      <label key={feat} className="flex items-center gap-2 text-xs font-mono text-[#ccc] hover:text-white cursor-pointer group">
                        <input type="checkbox" className="accent-indigo-500"
                          checked={selectedFeatures.includes(feat)}
                          onChange={() => {
                            const nf = selectedFeatures.includes(feat) ? selectedFeatures.filter(f => f !== feat) : [...selectedFeatures, feat];
                            setSelectedFeatures(nf);
                            saveConfig({ features: nf, hyperparameters: hyperparams });
                          }} />
                        <span className="truncate">{feat}</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded-xl overflow-hidden">
              <button onClick={() => setShowHyperparams(!showHyperparams)} className="w-full p-4 flex justify-between items-center bg-[#ffffff05] hover:bg-[#ffffff0a] transition-colors">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-[#888] flex items-center gap-2">
                  <SlidersHorizontal size={14} /> Hyperparameters
                </h3>
                {showHyperparams ? <ChevronDown size={14} className="text-[#666]" /> : <ChevronRight size={14} className="text-[#666]" />}
              </button>
              {showHyperparams && (
                <div className="p-4 pt-2 space-y-4 border-t border-[#ffffff0a]">
                  {Object.entries(hyperparams).map(([key, val]) => (
                    <div key={key} className="space-y-1">
                      <div className="flex justify-between text-[10px] font-mono text-[#888]">
                        <span>{key}</span><span className="text-[#ccc]">{val}</span>
                      </div>
                      <input type="range" min={key === 'learning_rate' ? 0.01 : 1} max={key === 'lookback_days' ? 500 : key === 'n_estimators' ? 500 : 30} step={key === 'learning_rate' ? 0.01 : 1} 
                        className="w-full" value={val} 
                        onChange={e => {
                          const nHp = { ...hyperparams, [key]: key === 'learning_rate' ? parseFloat(e.target.value) : parseInt(e.target.value) };
                          setHyperparams(nHp);
                          saveConfig({ features: selectedFeatures, hyperparameters: nHp });
                        }} />
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="mt-auto pt-4 space-y-2">
              <button onClick={handleTrain} disabled={training || selectedFeatures.length === 0} className="w-full py-2.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white rounded text-xs font-semibold flex justify-center items-center gap-2 transition-colors">
                {training ? <span className="animate-spin text-lg leading-none">⚙</span> : <Play size={14} fill="currentColor" />} {training ? "Training..." : "Train Model"}
              </button>
              <div className="flex gap-2">
                <button onClick={handlePredict} disabled={predicting || !status?.trained} className="flex-1 py-2 bg-[#ffffff1a] hover:bg-[#ffffff2a] disabled:opacity-50 rounded text-[11px] font-mono text-[#ccc] transition-colors">
                  {predicting ? "..." : "Predict Today"}
                </button>
                <button onClick={handleImportance} disabled={fetchingImportance || !status?.trained} className="flex-1 py-2 bg-[#ffffff1a] hover:bg-[#ffffff2a] disabled:opacity-50 rounded text-[11px] font-mono text-[#ccc] transition-colors">
                  {fetchingImportance ? "..." : "Importance"}
                </button>
              </div>
            </div>
          </div>

          {/* RIGHT PANEL - FORWARD RETURN */}
          <div className="flex-1 flex flex-col bg-[#0e1117] border border-[#ffffff1a] rounded-xl overflow-hidden relative">
            <div className="flex border-b border-[#ffffff1a] bg-[#1a1c24]">
              {['predictions', 'importance', 'history'].map(tab => (
                 <button key={tab} onClick={() => setActiveTab(tab as any)} className={`px-4 py-3 text-xs font-semibold uppercase tracking-wider transition-colors ${activeTab === tab ? 'text-indigo-400 border-b-2 border-indigo-500' : 'text-[#888] hover:text-[#ccc]'}`}>
                    {tab}
                 </button>
              ))}
            </div>
            <div className="flex-1 overflow-auto p-4">
              {!status?.trained && activeTab !== 'history' && !training ? (
                <div className="h-full flex items-center justify-center flex-col gap-2 text-[#666]">
                  <p className="text-sm font-mono">Not trained yet.</p>
                </div>
              ) : activeTab === 'predictions' ? (
                <div className="space-y-4">
                  {predicting ? <div className="text-xs text-[#888] font-mono animate-pulse">Running inferences...</div> : predictions ? (
                    <div className="border border-[#ffffff0a] rounded overflow-hidden">
                      <table className="w-full text-left text-xs font-mono">
                        <thead className="bg-[#1a1c24] text-[#888]">
                          <tr>
                            <th className="px-4 py-2 font-normal">Symbol</th>
                            <th className="px-4 py-2 font-normal">Predicted Class</th>
                            <th className="px-4 py-2 font-normal text-right">Confidence</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-[#ffffff0a]">
                          {predictions.map((p, i) => (
                            <tr key={i} className="hover:bg-[#1a1c24] transition-colors">
                              <td className="px-4 py-2 text-[#fafafa] font-bold">{p.symbol}</td>
                              <td className={`px-4 py-2 ${p.predicted_class === 'TOP_QUARTILE' ? 'text-green-400' : p.predicted_class === 'BOTTOM_QUARTILE' ? 'text-red-400' : 'text-[#ccc]'}`}>{p.predicted_class}</td>
                              <td className="px-4 py-2 text-right text-[#aaa]">{(p.confidence * 100).toFixed(1)}%</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : <div className="text-xs text-[#666] font-mono">Click Predict Today to run batch inference.</div>}
                </div>
              ) : activeTab === 'importance' ? (
                <div className="h-full flex flex-col">
                  {fetchingImportance ? <div className="text-xs text-[#888] font-mono animate-pulse">Fetching importance...</div> : importance ? (
                    <div className="flex-1 w-full relative min-h-[400px]">
                       <ResponsiveContainer width="100%" height="100%">
                           <BarChart data={importance} layout="vertical" margin={{ top: 5, right: 30, left: 100, bottom: 5 }}>
                               <CartesianGrid strokeDasharray="3 3" stroke="#ffffff1a" horizontal={false} />
                               <XAxis type="number" stroke="#666" tick={{fill: '#666', fontSize: 10}} />
                               <YAxis type="category" dataKey="feature" stroke="#666" tick={{fill: '#888', fontSize: 10}} width={120} />
                               <Tooltip contentStyle={{backgroundColor: '#1a1c24', borderColor: '#333', fontSize: '11px', fontFamily: 'monospace'}} itemStyle={{color: '#fafafa'}} formatter={(val: number) => [val.toFixed(4), 'Importance']} />
                               <Bar dataKey="importance" fill="#6366f1" radius={[0, 4, 4, 0]} />
                           </BarChart>
                       </ResponsiveContainer>
                    </div>
                  ) : <div className="text-xs text-[#666] font-mono">Click Importance.</div>}
                </div>
              ) : (
                <div className="border border-[#ffffff0a] rounded overflow-hidden">
                   <table className="w-full text-left text-xs font-mono">
                      <thead className="bg-[#1a1c24] text-[#888]">
                         <tr><th className="px-4 py-2">Date</th><th className="px-4 py-2 text-right">Accuracy</th><th className="px-4 py-2 text-right">Symbols</th><th className="px-4 py-2 text-right">Features</th></tr>
                      </thead>
                      <tbody className="divide-y divide-[#ffffff0a]">
                         {status?.history?.map((h, i) => (
                           <tr key={i} className="hover:bg-[#1a1c24] transition-colors"><td className="px-4 py-2 text-[#ccc]">{h.training_date?.split('.')[0].replace('T', ' ')}</td><td className="px-4 py-2 text-right font-bold text-indigo-400">{(h.accuracy || 0).toFixed(2)}%</td><td className="px-4 py-2 text-right text-[#aaa]">{h.n_symbols}</td><td className="px-4 py-2 text-right text-[#aaa]">{h.n_features}</td></tr>
                         ))}
                      </tbody>
                   </table>
                </div>
              )}
            </div>
          </div>
        </div>
      ) : (
        /* ================= LAUNCHPAD MODE ================= */
        <div className="flex flex-1 overflow-hidden p-4 gap-4 pt-2">
          {/* LEFT PANEL - LAUNCHPAD */}
          <div className="w-80 flex flex-col gap-4 overflow-y-auto shrink-0 pr-2">
            
            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded-xl p-4 flex flex-col gap-2">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-[#888] flex items-center gap-2">
                <Activity size={14} /> Launchpad Status
              </h3>
              {lpStatus ? (
                <div className="space-y-2 mt-2">
                  <div className="flex justify-between items-center text-xs font-mono">
                    <span className="text-[#666]">Status:</span>
                    <span className={lpStatus.exists ? "text-green-400 font-bold" : "text-yellow-400 font-bold"}>
                      {lpStatus.exists ? `Trained (${lpStatus.training_date?.split('T')[0] || 'Unknown'})` : 'Not trained'}
                    </span>
                  </div>
                  <div className="flex justify-between items-center text-xs font-mono">
                    <span className="text-[#666]">Labeled Events:</span>
                    <span className="text-[#ccc]">{lpStatus.n_labeled_events !== undefined ? lpStatus.n_labeled_events : '-'}</span>
                  </div>
                  {lpStatus.exists && (
                    <>
                      <div className="flex justify-between items-center text-xs font-mono">
                         <span className="text-[#666]">Accuracy:</span>
                         <span className="text-[#ccc]">{lpStatus.accuracy !== undefined ? `${(lpStatus.accuracy * 100).toFixed(2)}%` : '-'}</span>
                      </div>
                      <div className="flex justify-between items-center text-xs font-mono">
                         <span className="text-[#666]">RMSE (Return):</span>
                         <span className="text-[#ccc]">{lpStatus.rmse_return !== undefined ? lpStatus.rmse_return.toFixed(3) : '-'}</span>
                      </div>
                      <div className="flex justify-between items-center text-xs font-mono">
                         <span className="text-[#666]">RMSE (Days):</span>
                         <span className="text-[#ccc]">{lpStatus.rmse_days !== undefined ? lpStatus.rmse_days.toFixed(2) : '-'}</span>
                      </div>
                    </>
                  )}
                </div>
              ) : <div className="text-xs text-[#666] font-mono mt-2">Loading...</div>}
            </div>

            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded-xl overflow-hidden">
               <button onClick={() => setShowLpLabeling(!showLpLabeling)} className="w-full p-4 flex justify-between items-center bg-[#ffffff05] hover:bg-[#ffffff0a] transition-colors">
                 <h3 className="text-xs font-semibold uppercase tracking-wider text-[#888] flex items-center gap-2">
                   <Tag size={14} /> Event Labelling
                 </h3>
                 {showLpLabeling ? <ChevronDown size={14} className="text-[#666]" /> : <ChevronRight size={14} className="text-[#666]" />}
               </button>
               {showLpLabeling && (
                 <div className="p-4 pt-2 space-y-4 border-t border-[#ffffff0a]">
                   {Object.entries(lpLabelConfig).map(([key, val]) => (
                     <div key={key} className="space-y-1">
                       <div className="flex justify-between text-[10px] font-mono text-[#888]">
                          <span>{key}</span><span className="text-[#ccc]">{val}</span>
                       </div>
                       <input type="range" 
                          min={key.includes('pct') ? 10 : key.includes('vol') ? 1 : 0.5} 
                          max={key.includes('pct') ? 90 : key.includes('window') ? 30 : 5} 
                          step={key.includes('days') ? 1 : key.includes('pct') ? 5 : 0.5} 
                          className="w-full accent-red-500" value={val} 
                          onChange={e => {
                             const nv = { ...lpLabelConfig, [key]: key.includes('days') ? parseInt(e.target.value) : parseFloat(e.target.value) };
                             setLpLabelConfig(nv);
                             saveConfig({ launchpad_label_config: nv });
                          }} />
                     </div>
                   ))}
                   <button onClick={handleLpLabel} disabled={lpLabeling} className="w-full py-2 mt-2 bg-[#ffffff1a] hover:bg-[#ffffff2a] disabled:opacity-50 text-white rounded text-xs transition-colors flex justify-center items-center">
                     {lpLabeling ? 'Running...' : 'Run Event Labelling'}
                   </button>
                 </div>
               )}
            </div>

            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded-xl overflow-hidden">
              <button onClick={() => setShowLpFeatures(!showLpFeatures)} className="w-full p-4 flex justify-between items-center bg-[#ffffff05] hover:bg-[#ffffff0a] transition-colors">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-[#888] flex items-center gap-2">
                  <Cpu size={14} /> Model Features
                </h3>
                {showLpFeatures ? <ChevronDown size={14} className="text-[#666]" /> : <ChevronRight size={14} className="text-[#666]" />}
              </button>
              {showLpFeatures && (
                <div className="p-4 pt-2 border-t border-[#ffffff0a]">
                  <div className="space-y-1.5 max-h-48 overflow-y-auto pr-1">
                    {LAUNCHPAD_FEATURES.map(feat => (
                      <label key={feat} className="flex items-center gap-2 text-xs font-mono text-[#ccc] hover:text-white cursor-pointer group">
                        <input type="checkbox" className="accent-red-500"
                          checked={lpSelectedFeatures.includes(feat)}
                          onChange={() => {
                            const nf = lpSelectedFeatures.includes(feat) ? lpSelectedFeatures.filter(f => f !== feat) : [...lpSelectedFeatures, feat];
                            setLpSelectedFeatures(nf);
                            saveConfig({ launchpad_features: nf, launchpad_hyperparameters: lpHyperparams, launchpad_label_config: lpLabelConfig });
                          }} />
                        <span className="truncate">{feat}</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="bg-[#1a1c24] border border-[#ffffff1a] rounded-xl overflow-hidden">
              <button onClick={() => setShowLpHyperparams(!showLpHyperparams)} className="w-full p-4 flex justify-between items-center bg-[#ffffff05] hover:bg-[#ffffff0a] transition-colors">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-[#888] flex items-center gap-2">
                  <SlidersHorizontal size={14} /> Hyperparameters
                </h3>
                {showLpHyperparams ? <ChevronDown size={14} className="text-[#666]" /> : <ChevronRight size={14} className="text-[#666]" />}
              </button>
              {showLpHyperparams && (
                <div className="p-4 pt-2 space-y-4 border-t border-[#ffffff0a]">
                  {Object.entries(lpHyperparams).map(([key, val]) => (
                    <div key={key} className="space-y-1">
                      <div className="flex justify-between text-[10px] font-mono text-[#888]">
                        <span>{key}</span><span className="text-[#ccc]">{val}</span>
                      </div>
                      <input type="range" 
                        min={key === 'n_estimators' ? 50 : key === 'learning_rate' ? 0.01 : 0.1} 
                        max={key === 'n_estimators' ? 500 : key === 'max_depth' ? 10 : 1.0} 
                        step={key === 'n_estimators' || key === 'max_depth' ? 1 : 0.05} 
                        className="w-full accent-red-500" value={val} 
                        onChange={e => {
                          const nHp = { ...lpHyperparams, [key]: key === 'n_estimators' || key === 'max_depth' ? parseInt(e.target.value) : parseFloat(e.target.value) };
                          setLpHyperparams(nHp);
                          saveConfig({ launchpad_hyperparameters: nHp, launchpad_features: lpSelectedFeatures, launchpad_label_config: lpLabelConfig });
                        }} />
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="mt-auto pt-4 space-y-2">
              <button onClick={handleLpTrain} disabled={lpTraining || lpSelectedFeatures.length === 0} className="w-full py-2.5 bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white rounded text-xs font-semibold flex justify-center items-center gap-2 transition-colors">
                {lpTraining ? <span className="animate-spin text-lg leading-none">⚙</span> : <Rocket size={14} fill="currentColor" />} {lpTraining ? "Training..." : "Train Launchpad Model"}
              </button>
            </div>
          </div>

          {/* RIGHT PANEL - LAUNCHPAD RESULTS (Feature Importance) */}
          <div className="flex-1 flex flex-col bg-[#0e1117] border border-[#ffffff1a] rounded-xl overflow-hidden relative p-4">
             <div className="flex justify-between items-center mb-4">
                <h3 className="text-sm font-semibold uppercase tracking-wider text-[#fafafa] flex items-center gap-2">
                   Feature Importance
                </h3>
                <button onClick={handleLpImportance} disabled={lpFetchingImportance || !lpStatus?.exists} className="px-4 py-2 bg-[#ffffff1a] hover:bg-[#ffffff2a] disabled:opacity-50 text-[#ccc] rounded text-xs transition-colors">
                  {lpFetchingImportance ? "Fetching..." : "Fetch Importance"}
                </button>
             </div>
             
             <div className="flex-1 w-full bg-[#1a1c24] border border-[#ffffff1a] rounded">
                {!lpImportance ? (
                  <div className="h-full flex items-center justify-center text-xs font-mono text-[#666]">
                     {lpStatus?.exists ? 'Click "Fetch Importance" to load.' : 'Model not trained yet.'}
                  </div>
                ) : (
                  <ResponsiveContainer width="100%" height="100%">
                     <BarChart data={lpImportance} layout="vertical" margin={{ top: 20, right: 30, left: 100, bottom: 20 }}>
                         <CartesianGrid strokeDasharray="3 3" stroke="#ffffff1a" horizontal={false} />
                         <XAxis type="number" stroke="#666" tick={{fill: '#666', fontSize: 10}} />
                         <YAxis type="category" dataKey="feature" stroke="#666" tick={{fill: '#888', fontSize: 10}} width={120} />
                         <Tooltip contentStyle={{backgroundColor: '#0e1117', borderColor: '#333', fontSize: '11px', fontFamily: 'monospace'}} itemStyle={{color: '#fafafa'}} formatter={(val: number) => [val.toFixed(4), 'Importance']} />
                         <Bar dataKey="importance" fill="#ef4444" radius={[0, 4, 4, 0]} />
                     </BarChart>
                  </ResponsiveContainer>
                )}
             </div>
          </div>

        </div>
      )}
    </div>
  );
}
