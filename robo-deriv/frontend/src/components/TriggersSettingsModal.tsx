import React from 'react';
import { CheckCircle2, Sliders, X, Zap } from 'lucide-react';

export interface TriggersPreferences {
  enableProductA: boolean;       // Produto A (:00s Basis Perp-Spot)
  enableProductB: boolean;       // Produto B (:05s Momentum SNR 5s)
  enableL2Veto: boolean;         // Veto do Livro de Ofertas L2 (Absorção/Spoof)
  enableM5Filter: boolean;       // Filtro de Tendência M5
  enableMicro15s: boolean;       // Filtro de Microestrutura 15s
  enableInstitutional10: boolean;// Confluência Obrigatória dos 10 Indicadores
  enableAbsorptionSR: boolean;   // Absorção em Suporte/Resistência & Paredes L2
}

export const DEFAULT_TRIGGERS: TriggersPreferences = {
  enableProductA: true,
  enableProductB: true,
  enableL2Veto: true,
  enableM5Filter: false,
  enableMicro15s: false,
  enableInstitutional10: false,
  enableAbsorptionSR: false,
};

interface TriggersSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  preferences: TriggersPreferences;
  onPreferencesChange: (newPrefs: TriggersPreferences) => void;
}

export const TriggersSettingsModal: React.FC<TriggersSettingsModalProps> = ({
  isOpen,
  onClose,
  preferences,
  onPreferencesChange,
}) => {
  if (!isOpen) return null;

  const toggle = (key: keyof TriggersPreferences) => {
    const updated = { ...preferences, [key]: !preferences[key] };
    onPreferencesChange(updated);
    try {
      localStorage.setItem('btc_radar_triggers_cfg', JSON.stringify(updated));
    } catch {
      // ignore
    }
  };

  const handleResetToDefault = () => {
    onPreferencesChange(DEFAULT_TRIGGERS);
    try {
      localStorage.setItem('btc_radar_triggers_cfg', JSON.stringify(DEFAULT_TRIGGERS));
    } catch {
      // ignore
    }
  };

  const isDefaultOnly =
    preferences.enableProductA &&
    preferences.enableProductB &&
    preferences.enableL2Veto &&
    !preferences.enableM5Filter &&
    !preferences.enableMicro15s &&
    !preferences.enableInstitutional10 &&
    !preferences.enableAbsorptionSR;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="w-full max-w-lg rounded-2xl border border-slate-800 bg-[#0d121c] p-6 shadow-2xl space-y-5 animate-in fade-in duration-200">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-800 pb-3">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-cyan-500/15 border border-cyan-500/30 text-cyan-400">
              <Sliders className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-sm font-bold text-slate-100 font-mono tracking-tight">
                Gatilhos de Entrada & Padrões
              </h2>
              <p className="text-[11px] text-slate-400">
                Ative ou desative cada motor e confluência individualmente
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-200 transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Botão de Atalho Rápido para o Padrão Original */}
        <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3.5 flex items-center justify-between">
          <div className="space-y-0.5">
            <div className="flex items-center gap-1.5 text-xs font-bold text-emerald-400 font-mono">
              <Zap className="h-4 w-4" />
              <span>Gatilho Padrão Original (Base Intacta)</span>
            </div>
            <p className="text-[10px] text-slate-300">
              Mantém apenas Produto A (:00s Basis Q80) + Produto B (:05s SNR 5s) com Veto L2.
            </p>
          </div>
          <button
            type="button"
            onClick={handleResetToDefault}
            className={`px-3 py-1.5 rounded-lg text-xs font-bold font-mono transition-all ${
              isDefaultOnly
                ? 'bg-emerald-500 text-slate-950 shadow-md shadow-emerald-500/20'
                : 'bg-slate-800 text-slate-200 hover:bg-slate-700 border border-slate-700'
            }`}
          >
            {isDefaultOnly ? '✓ Ativo' : 'Restaurar'}
          </button>
        </div>

        {/* Lista de Gatilhos e Filtros */}
        <div className="space-y-3">
          {/* Gatilho 1: Produto A */}
          <div className="flex items-center justify-between rounded-xl border border-slate-800/80 bg-[#121722] p-3">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-xs font-bold text-slate-200 font-mono">
                  Gatilho 1 • Produto A (:00s)
                </span>
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-cyan-500/15 text-cyan-300 border border-cyan-500/30 font-mono font-bold">
                  PADRÃO
                </span>
              </div>
              <p className="text-[10px] text-slate-400">
                Convergência Perp-Spot Z-Score 60m (|z| &ge; Q80 no :00s).
              </p>
            </div>
            <button
              type="button"
              onClick={() => toggle('enableProductA')}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                preferences.enableProductA ? 'bg-cyan-500' : 'bg-slate-800'
              }`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  preferences.enableProductA ? 'translate-x-6' : 'translate-x-1'
                }`}
              />
            </button>
          </div>

          {/* Gatilho 2: Produto B */}
          <div className="flex items-center justify-between rounded-xl border border-slate-800/80 bg-[#121722] p-3">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-xs font-bold text-slate-200 font-mono">
                  Gatilho 2 • Produto B (:05s)
                </span>
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-cyan-500/15 text-cyan-300 border border-cyan-500/30 font-mono font-bold">
                  PADRÃO
                </span>
              </div>
              <p className="text-[10px] text-slate-400">
                Aceleração física inicial (SNR_5s &ge; 0.50 após os primeiros 5s).
              </p>
            </div>
            <button
              type="button"
              onClick={() => toggle('enableProductB')}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                preferences.enableProductB ? 'bg-cyan-500' : 'bg-slate-800'
              }`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  preferences.enableProductB ? 'translate-x-6' : 'translate-x-1'
                }`}
              />
            </button>
          </div>

          {/* Filtro: Veto L2 */}
          <div className="flex items-center justify-between rounded-xl border border-slate-800/80 bg-[#121722] p-3">
            <div>
              <span className="text-xs font-bold text-slate-200 font-mono">
                Filtro de Livro L2 (Veto de Paredes)
              </span>
              <p className="text-[10px] text-slate-400">
                Bloqueia sinais contra paredes fortes de absorção ou spoofing L2.
              </p>
            </div>
            <button
              type="button"
              onClick={() => toggle('enableL2Veto')}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                preferences.enableL2Veto ? 'bg-emerald-500' : 'bg-slate-800'
              }`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  preferences.enableL2Veto ? 'translate-x-6' : 'translate-x-1'
                }`}
              />
            </button>
          </div>

          {/* Filtro: Tendência M5 */}
          <div className="flex items-center justify-between rounded-xl border border-slate-800/80 bg-[#121722] p-3">
            <div>
              <span className="text-xs font-bold text-slate-200 font-mono">
                Confluência de Regime M5
              </span>
              <p className="text-[10px] text-slate-400">
                Exige alinhamento com a tendência fechada do gráfico de 5 minutos.
              </p>
            </div>
            <button
              type="button"
              onClick={() => toggle('enableM5Filter')}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                preferences.enableM5Filter ? 'bg-amber-500' : 'bg-slate-800'
              }`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  preferences.enableM5Filter ? 'translate-x-6' : 'translate-x-1'
                }`}
              />
            </button>
          </div>

          {/* Filtro: Microestrutura 15s */}
          <div className="flex items-center justify-between rounded-xl border border-slate-800/80 bg-[#121722] p-3">
            <div>
              <span className="text-xs font-bold text-slate-200 font-mono">
                Microestrutura 15s (4 Sub-velas)
              </span>
              <p className="text-[10px] text-slate-400">
                Avalia a aceleração dos últimos 4 sub-candles de 15 segundos da vela anterior.
              </p>
            </div>
            <button
              type="button"
              onClick={() => toggle('enableMicro15s')}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                preferences.enableMicro15s ? 'bg-amber-500' : 'bg-slate-800'
              }`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  preferences.enableMicro15s ? 'translate-x-6' : 'translate-x-1'
                }`}
              />
            </button>
          </div>

          {/* Filtro: Veredito Institucional dos 10 Indicadores */}
          <div className="flex items-center justify-between rounded-xl border border-slate-800/80 bg-[#121722] p-3">
            <div>
              <span className="text-xs font-bold text-slate-200 font-mono">
                Filtro Veredito Institucional (10 Indicadores)
              </span>
              <p className="text-[10px] text-slate-400">
                Bloqueia terminantemente entradas contra a maioria dos 10 indicadores (RSI, MACD, etc.).
              </p>
            </div>
            <button
              type="button"
              onClick={() => toggle('enableInstitutional10')}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                preferences.enableInstitutional10 ? 'bg-purple-500' : 'bg-slate-800'
              }`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  preferences.enableInstitutional10 ? 'translate-x-6' : 'translate-x-1'
                }`}
              />
            </button>
          </div>

          {/* Gatilho 3: Absorção em Suporte/Resistência & Paredes L2 */}
          <div className="flex items-center justify-between rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-3">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-xs font-bold text-emerald-300 font-mono">
                  Absorção em Suporte / Resistência & Lote L2
                </span>
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-mono font-bold">
                  REVERSÃO
                </span>
              </div>
              <p className="text-[10px] text-slate-300">
                Dispara CALL em teste de Suporte com absorção compradora (ou PUT em Resistência) com confluência de indicadores.
              </p>
            </div>
            <button
              type="button"
              onClick={() => toggle('enableAbsorptionSR')}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                preferences.enableAbsorptionSR ? 'bg-emerald-500' : 'bg-slate-800'
              }`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  preferences.enableAbsorptionSR ? 'translate-x-6' : 'translate-x-1'
                }`}
              />
            </button>
          </div>
        </div>

        {/* Footer */}
        <div className="pt-2 flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="px-5 py-2 rounded-xl bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-bold text-xs font-mono transition-colors shadow-lg shadow-cyan-500/20"
          >
            Confirmar e Fechar
          </button>
        </div>
      </div>
    </div>
  );
};
