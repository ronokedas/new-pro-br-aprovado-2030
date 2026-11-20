import React, { useState } from 'react';
import { Bell, Check, Play, ShieldAlert, Target, Volume2, VolumeX, X } from 'lucide-react';
import { audioAlerts, type AudioPreferences } from '../lib/audioAlerts';

interface AudioSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onPreferencesChange?: (prefs: AudioPreferences) => void;
}

export const AudioSettingsModal: React.FC<AudioSettingsModalProps> = ({
  isOpen,
  onClose,
  onPreferencesChange,
}) => {
  const [prefs, setPrefs] = useState<AudioPreferences>(() => audioAlerts.getPreferences());

  if (!isOpen) return null;

  const handleTogglePreSignal = () => {
    const next = { ...prefs, preSignalEnabled: !prefs.preSignalEnabled };
    setPrefs(next);
    audioAlerts.savePreferences(next);
    onPreferencesChange?.(next);
  };

  const handleToggleSrTouch = () => {
    const next = { ...prefs, srTouchEnabled: !prefs.srTouchEnabled };
    setPrefs(next);
    audioAlerts.savePreferences(next);
    onPreferencesChange?.(next);
  };

  const handleVolumeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = parseFloat(e.target.value);
    const next = { ...prefs, volume: val };
    setPrefs(next);
    audioAlerts.savePreferences(next);
    onPreferencesChange?.(next);
  };

  const handleTestPreSignal = (e: React.MouseEvent) => {
    e.stopPropagation();
    audioAlerts.testPreSignal();
  };

  const handleTestSrTouch = (e: React.MouseEvent) => {
    e.stopPropagation();
    audioAlerts.testSrTouch(true);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fadeIn">
      <div className="relative w-full max-w-md rounded-2xl border border-slate-800 bg-[#0e131f] p-6 shadow-2xl text-slate-100">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-800 pb-4 mb-5">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-cyan-500/15 border border-cyan-500/30 text-cyan-400">
              <Volume2 className="h-5 w-5" />
            </div>
            <div>
              <h3 className="text-sm font-bold tracking-tight text-slate-100">
                Central de Alertas Sonoros
              </h3>
              <p className="text-[11px] text-slate-400">
                Avisos auditivos do Radar BTC e Zonas S/R
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:text-slate-100 hover:bg-slate-800 transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content */}
        <div className="space-y-4">
          {/* Card 1: Pré-Sinal Fixo M1 */}
          <div className="rounded-xl border border-slate-800/80 bg-slate-950/60 p-4 transition-all">
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-start gap-3">
                <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-amber-500/15 border border-amber-500/30 text-amber-400">
                  <Bell className="h-4 w-4" />
                </div>
                <div>
                  <h4 className="text-xs font-bold text-slate-100">
                    Aviso Pré-Sinal Fixo (Fechamento M1)
                  </h4>
                  <p className="text-[11px] text-slate-400 mt-0.5">
                    Toca bips regressivos aos :03s, :02s e :01s com sino aos :00s anunciando o novo sinal travado.
                  </p>
                </div>
              </div>

              {/* Toggle Switch */}
              <button
                type="button"
                onClick={handleTogglePreSignal}
                className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${
                  prefs.preSignalEnabled ? 'bg-emerald-500' : 'bg-slate-700'
                }`}
              >
                <span
                  className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
                    prefs.preSignalEnabled ? 'translate-x-5' : 'translate-x-0'
                  }`}
                />
              </button>
            </div>

            <div className="mt-3 flex items-center justify-between border-t border-slate-800/60 pt-2.5">
              <span className="text-[10px] font-mono text-slate-500">
                Status: {prefs.preSignalEnabled ? 'Ativo' : 'Desativado'}
              </span>
              <button
                type="button"
                onClick={handleTestPreSignal}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1 text-[11px] font-mono text-cyan-300 hover:border-cyan-500/50 hover:bg-cyan-500/10 transition-colors"
              >
                <Play className="h-3 w-3" />
                <span>Testar Som</span>
              </button>
            </div>
          </div>

          {/* Card 2: Toque em Suporte / Resistência */}
          <div className="rounded-xl border border-slate-800/80 bg-slate-950/60 p-4 transition-all">
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-start gap-3">
                <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-emerald-500/15 border border-emerald-500/30 text-emerald-400">
                  <Target className="h-4 w-4" />
                </div>
                <div>
                  <h4 className="text-xs font-bold text-slate-100">
                    Aviso de Toque em Suporte / Resistência
                  </h4>
                  <p className="text-[11px] text-slate-400 mt-0.5">
                    Dispara sinal sonoro harmônico imediato quando o BTC tocar em linhas ativas de M5 ou M15 (com anti-spam de 45s).
                  </p>
                </div>
              </div>

              {/* Toggle Switch */}
              <button
                type="button"
                onClick={handleToggleSrTouch}
                className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${
                  prefs.srTouchEnabled ? 'bg-emerald-500' : 'bg-slate-700'
                }`}
              >
                <span
                  className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
                    prefs.srTouchEnabled ? 'translate-x-5' : 'translate-x-0'
                  }`}
                />
              </button>
            </div>

            <div className="mt-3 flex items-center justify-between border-t border-slate-800/60 pt-2.5">
              <span className="text-[10px] font-mono text-slate-500">
                Status: {prefs.srTouchEnabled ? 'Ativo' : 'Desativado'}
              </span>
              <button
                type="button"
                onClick={handleTestSrTouch}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1 text-[11px] font-mono text-emerald-300 hover:border-emerald-500/50 hover:bg-emerald-500/10 transition-colors"
              >
                <Play className="h-3 w-3" />
                <span>Testar Som</span>
              </button>
            </div>
          </div>

          {/* Volume Control Slider */}
          <div className="rounded-xl border border-slate-800/80 bg-slate-950/60 p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
                {prefs.volume > 0.1 ? <Volume2 className="h-4 w-4 text-cyan-400" /> : <VolumeX className="h-4 w-4 text-slate-500" />}
                Volume dos Alertas
              </span>
              <span className="font-mono text-xs font-bold text-cyan-300">
                {Math.round(prefs.volume * 100)}%
              </span>
            </div>
            <input
              type="range"
              min="0.1"
              max="1.0"
              step="0.05"
              value={prefs.volume}
              onChange={handleVolumeChange}
              className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-cyan-400"
            />
          </div>
        </div>

        {/* Footer */}
        <div className="mt-6 flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="btn-primary w-full py-2.5 text-xs font-bold"
          >
            Salvar e Fechar
          </button>
        </div>
      </div>
    </div>
  );
};

export default AudioSettingsModal;
