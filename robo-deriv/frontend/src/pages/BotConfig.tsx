import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Save,
  Shield,
  Sliders,
  Sparkles,
} from 'lucide-react';
import { PairSelector } from '../components/PairSelector';
import { RiskCalculator } from '../components/RiskCalculator';
import { useAuth } from '../hooks/useAuth';
import { ApiError, botApi, marketApi } from '../lib/api';
import { formatUsd, parseLayerMultipliers } from '../lib/format';
import type {
  BotConfigUpdatePayload,
  DirectionMode,
  MarketSymbol,
  StrategyMode,
} from '../types/api';

const TIMEFRAMES = [
  { value: 60, label: 'M1 (1 min)' },
  { value: 300, label: 'M5 (5 min)' },
  { value: 900, label: 'M15 (15 min — Recomendado)' },
  { value: 3600, label: 'H1 (1 hora)' },
];

const DIRECTIONS: { value: DirectionMode; label: string; desc: string }[] = [
  { value: 'LONG_ONLY', label: 'Somente Alta (MULTUP)', desc: 'Opera apenas canal de alta' },
  { value: 'SHORT_ONLY', label: 'Somente Baixa (MULTDOWN)', desc: 'Opera apenas canal de baixa' },
  { value: 'BOTH', label: 'Ambos (Alta e Baixa)', desc: 'Detecta canais nos dois sentidos' },
];

const LAYER_PRESETS = [
  { label: 'Conservador [1.0, 1.0, 1.5]', values: [1.0, 1.0, 1.5] },
  { label: 'Uniforme [1.0, 1.0, 1.0]', values: [1.0, 1.0, 1.0] },
  { label: 'Moderado [1.0, 1.3, 1.7]', values: [1.0, 1.3, 1.7] },
];

export const BotConfig: React.FC = () => {
  const { user } = useAuth();

  const [symbols, setSymbols] = useState<MarketSymbol[]>([]);
  const [loadingSymbols, setLoadingSymbols] = useState(true);
  const [loadingConfig, setLoadingConfig] = useState(true);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const [form, setForm] = useState<BotConfigUpdatePayload>({
    symbol: 'cryBTCUSD',
    timeframe_sec: 300,
    direction_mode: 'LONG_ONLY',
    stake_usd: 1.0,
    multiplier: 100,
    max_layers: 3,
    layer_multipliers_json: '[1.0, 1.0, 1.5]',
    basket_tp_usd: 0.15,
    trailing_enabled: true,
    max_basket_loss_usd: 2.5,
    max_daily_loss_usd: 10.0,
    daily_profit_target_usd: 15.0,
    max_consecutive_losses: 3,
    max_hold_minutes: 180,
    use_binance_filter: true,
    strategy_mode: 'SMART_CHANNEL_DCA',
    channel_window: 60,
    channel_sigma: 2.0,
    atr_period: 10,
    tp_atr_mult: 0.75,
    sl_atr_mult: 1.9,
    use_crash_guard: true,
    multi_pair_enabled: false,
    scanner_symbols_json: '["cryBTCUSD", "cryETHUSD", "frxEURUSD", "frxGBPUSD", "frxUSDJPY", "1HZ100V", "R_75"]',
  });

  const loadAll = useCallback(async () => {
    setLoadingConfig(true);
    setLoadingSymbols(true);
    try {
      const [cfgRes, symRes] = await Promise.all([
        botApi.getConfig(),
        marketApi.getSymbols().catch(() => [] as MarketSymbol[]),
      ]);
      const c = cfgRes.config;
      if (c) {
        setForm({
          symbol: c.symbol,
          timeframe_sec: c.timeframe_sec,
          direction_mode: c.direction_mode,
          stake_usd: c.stake_usd,
          multiplier: c.multiplier,
          max_layers: c.max_layers,
          layer_multipliers_json: c.layer_multipliers_json || '[1.0, 1.0, 1.5]',
          basket_tp_usd: c.basket_tp_usd,
          trailing_enabled: c.trailing_enabled,
          max_basket_loss_usd: c.max_basket_loss_usd,
          max_daily_loss_usd: c.max_daily_loss_usd,
          daily_profit_target_usd: c.daily_profit_target_usd,
          max_consecutive_losses: c.max_consecutive_losses,
          max_hold_minutes: c.max_hold_minutes,
          use_binance_filter: c.use_binance_filter,
          strategy_mode: (c.strategy_mode as StrategyMode) || 'SMART_CHANNEL_DCA',
          channel_window: c.channel_window ?? 60,
          channel_sigma: c.channel_sigma ?? 2.0,
          atr_period: c.atr_period ?? 10,
          tp_atr_mult: c.tp_atr_mult ?? 0.75,
          sl_atr_mult: c.sl_atr_mult ?? 1.9,
          use_crash_guard: c.use_crash_guard ?? true,
          multi_pair_enabled: c.multi_pair_enabled ?? false,
          scanner_symbols_json: c.scanner_symbols_json || '["cryBTCUSD", "cryETHUSD", "frxEURUSD", "frxGBPUSD", "frxUSDJPY", "1HZ100V", "R_75"]',
        });
      }
      setSymbols(Array.isArray(symRes) ? symRes : []);
    } catch (err) {
      setFeedback({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Erro ao carregar configurações do robô.',
      });
    } finally {
      setLoadingConfig(false);
      setLoadingSymbols(false);
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const selectedSymbolObj = useMemo(
    () => symbols.find((s) => s.symbol === form.symbol) ?? null,
    [symbols, form.symbol]
  );

  const availableMultipliers = useMemo(() => {
    if (selectedSymbolObj && selectedSymbolObj.multipliers.length > 0) {
      return [...selectedSymbolObj.multipliers].sort((a, b) => a - b);
    }
    return [form.multiplier || 100];
  }, [selectedSymbolObj, form.multiplier]);

  const layerRatios = useMemo(
    () => parseLayerMultipliers(form.layer_multipliers_json, form.max_layers),
    [form.layer_multipliers_json, form.max_layers]
  );

  const handleSelectSymbol = (sym: MarketSymbol) => {
    const sortedMults = [...(sym.multipliers || [])].sort((a, b) => a - b);
    const lowestMult = sortedMults[0] ?? form.multiplier;
    const minStake = sym.min_stake > 0 ? sym.min_stake : 1.0;

    setForm((prev) => ({
      ...prev,
      symbol: sym.symbol,
      multiplier: sortedMults.includes(prev.multiplier) ? prev.multiplier : lowestMult,
      stake_usd: Math.max(prev.stake_usd, minStake),
      use_binance_filter: sym.symbol.toUpperCase().includes('BTC') ? prev.use_binance_filter : false,
    }));
  };

  const updateLayerRatio = (index: number, rawValue: number) => {
    const clamped = Math.min(2.0, Math.max(0.5, Number.isFinite(rawValue) ? rawValue : 1.0));
    const next = [...layerRatios];
    next[index] = Number(clamped.toFixed(2));
    setForm((prev) => ({ ...prev, layer_multipliers_json: JSON.stringify(next) }));
  };

  const applyLayerPreset = (values: number[]) => {
    const resized = parseLayerMultipliers(JSON.stringify(values), form.max_layers);
    setForm((prev) => ({ ...prev, layer_multipliers_json: JSON.stringify(resized) }));
  };

  const handleMaxLayersChange = (newMax: number) => {
    const clamped = Math.min(5, Math.max(1, newMax));
    const resized = parseLayerMultipliers(form.layer_multipliers_json, clamped);
    setForm((prev) => ({
      ...prev,
      max_layers: clamped,
      layer_multipliers_json: JSON.stringify(resized),
    }));
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setFeedback(null);
    try {
      await botApi.updateConfig(form);
      setFeedback({
        type: 'success',
        text: 'Configurações do robô salvas e sincronizadas com o motor quantitativo!',
      });
    } catch (err) {
      setFeedback({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Erro ao salvar configurações.',
      });
    } finally {
      setSaving(false);
    }
  };

  if (loadingConfig) {
    return (
      <div className="flex min-h-[55vh] items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-emerald-400" />
      </div>
    );
  }

  const minStakeForSymbol = selectedSymbolObj?.min_stake ?? 1.0;
  const isBtcSymbol = form.symbol.toUpperCase().includes('BTC');

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-slate-100">
            Configurações do Robô & Seletor de Pares
          </h1>
          <p className="text-xs text-slate-400">
            Escolha o ativo Deriv com suporte a Multipliers, ajuste o motor de Canal + Smart DCA e valide o risco ao vivo.
          </p>
        </div>
        {user && (
          <div className="rounded-xl border border-slate-800 bg-slate-900/80 px-3.5 py-2 text-xs text-slate-300">
            Stake máximo do plano:{' '}
            <span className="font-mono font-semibold text-emerald-300">
              {formatUsd(user.max_stake_usd)}
            </span>
          </div>
        )}
      </div>

      {feedback && (
        <div
          className={`flex items-start gap-2.5 rounded-xl border px-4 py-3 text-xs ${
            feedback.type === 'success'
              ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
              : 'border-rose-500/40 bg-rose-500/10 text-rose-200'
          }`}
        >
          {feedback.type === 'success' ? (
            <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400 mt-0.5" />
          ) : (
            <AlertTriangle className="h-4 w-4 shrink-0 text-rose-400 mt-0.5" />
          )}
          <span>{feedback.text}</span>
        </div>
      )}

      {/* Live Risk Calculator */}
      <RiskCalculator
        symbol={form.symbol}
        stakeUsd={form.stake_usd}
        multiplier={form.multiplier}
        maxLayers={form.max_layers}
        layerMultipliersJson={form.layer_multipliers_json}
        basketTpUsd={form.basket_tp_usd}
        maxBasketLossUsd={form.max_basket_loss_usd}
        directionMode={form.direction_mode}
        maxAllowedStakeUsd={user?.max_stake_usd}
      />

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-12">
        {/* Left: Dynamic Pair Selector */}
        <div className="xl:col-span-5">
          <PairSelector
            symbols={symbols}
            selectedSymbol={form.symbol}
            onSelect={handleSelectSymbol}
            loading={loadingSymbols}
          />
        </div>

        {/* Right: Strategy, DCA & Risk Form */}
        <form onSubmit={handleSave} className="xl:col-span-7 space-y-5">
          {/* Strategy Mode Selector Card */}
          <div className="glass-card p-5 space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/80 pb-3">
              <div className="flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-cyan-400" />
                <h2 className="text-sm font-semibold text-slate-100">
                  Modo de Estratégia do Robô
                </h2>
              </div>
              {form.strategy_mode === 'LINREG_ATR_VIDEO' && (
                <button
                  type="button"
                  onClick={() =>
                    setForm((prev) => ({
                      ...prev,
                      strategy_mode: 'LINREG_ATR_VIDEO',
                      direction_mode: 'BOTH',
                      channel_window: 50,
                      channel_sigma: 2.0,
                      atr_period: 10,
                      tp_atr_mult: 0.75,
                      sl_atr_mult: 1.9,
                      use_crash_guard: true,
                    }))
                  }
                  className="rounded-lg border border-cyan-500/40 bg-cyan-500/10 px-2.5 py-1 text-[11px] font-semibold text-cyan-200 hover:bg-cyan-500/20 transition-colors"
                >
                  Restaurar Preset do Vídeo (50 ±2σ • ATR10 0.75/1.90)
                </button>
              )}
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <button
                type="button"
                onClick={() =>
                  setForm((prev) => ({
                    ...prev,
                    strategy_mode: 'GRADIENTE_LINEAR',
                    timeframe_sec: 60,
                  }))
                }
                className={`rounded-xl border p-3.5 text-left transition-all ${
                  form.strategy_mode === 'GRADIENTE_LINEAR'
                    ? 'border-emerald-500/60 bg-emerald-500/15 ring-2 ring-emerald-500/40 shadow-glow'
                    : 'border-slate-800 bg-slate-950/60 hover:border-slate-700'
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-bold text-slate-100">Gradiente Linear (M1)</span>
                  <span className="rounded bg-cyan-500/20 px-1.5 py-0.5 text-[10px] font-bold text-cyan-300">
                    HFT M1
                  </span>
                </div>
                <p className="mt-1.5 text-[11px] leading-relaxed text-slate-300">
                  Alta frequência no M1 com grade de preço médio dinâmico e parada automática na Meta.
                </p>
              </button>

              <button
                type="button"
                onClick={() =>
                  setForm((prev) => ({
                    ...prev,
                    strategy_mode: 'TICK_MEAN_REVERSION',
                    timeframe_sec: 60,
                  }))
                }
                className={`rounded-xl border p-3.5 text-left transition-all ${
                  form.strategy_mode === 'TICK_MEAN_REVERSION'
                    ? 'border-emerald-500/60 bg-emerald-500/15 ring-2 ring-emerald-500/40 shadow-glow'
                    : 'border-slate-800 bg-slate-950/60 hover:border-slate-700'
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-bold text-slate-100">Reversão à Média (1Hz)</span>
                  <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] font-bold text-emerald-300">
                    NOVO
                  </span>
                </div>
                <p className="mt-1.5 text-[11px] leading-relaxed text-slate-300">
                  Microestrutura 1Hz: Z-Score ±2.0 + EMA-20, micro-DCA volátil e Take Profit blindado contra taxas.
                </p>
              </button>

              <button
                type="button"
                onClick={() =>
                  setForm((prev) => ({
                    ...prev,
                    strategy_mode: 'SMART_CHANNEL_DCA',
                    channel_window: prev.channel_window === 50 ? 60 : prev.channel_window,
                  }))
                }
                className={`rounded-xl border p-3.5 text-left transition-all ${
                  form.strategy_mode === 'SMART_CHANNEL_DCA'
                    ? 'border-emerald-500/60 bg-emerald-500/10 ring-1 ring-emerald-500/30'
                    : 'border-slate-800 bg-slate-950/60 hover:border-slate-700'
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-bold text-slate-100">Smart Channel + DCA</span>
                  <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] font-bold text-slate-400">
                    CANAL M5
                  </span>
                </div>
                <p className="mt-1.5 text-[11px] leading-relaxed text-slate-400">
                  Canal causal + filtro de tendência + confirmações de pavio/RSI/MACD.
                </p>
              </button>

              <button
                type="button"
                onClick={() =>
                  setForm((prev) => ({
                    ...prev,
                    strategy_mode: 'LINREG_ATR_VIDEO',
                    direction_mode: 'BOTH',
                    channel_window: 50,
                    channel_sigma: 2.0,
                    atr_period: 10,
                    tp_atr_mult: 0.75,
                    sl_atr_mult: 1.9,
                  }))
                }
                className={`rounded-xl border p-3.5 text-left transition-all ${
                  form.strategy_mode === 'LINREG_ATR_VIDEO'
                    ? 'border-cyan-500/60 bg-cyan-500/10 ring-1 ring-cyan-500/30'
                    : 'border-slate-800 bg-slate-950/60 hover:border-slate-700'
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-bold text-slate-100">Regressão + ATR</span>
                  <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] font-bold text-slate-400">
                    CANAL 50
                  </span>
                </div>
                <p className="mt-1.5 text-[11px] leading-relaxed text-slate-400">
                  Canal 50 ±2σ, entrada em LinB/LinC com TP (0.75×ATR) e SL (1.90×ATR).
                </p>
              </button>
            </div>

            {form.strategy_mode === 'LINREG_ATR_VIDEO' && (
              <div className="space-y-3 rounded-xl border border-cyan-500/25 bg-cyan-500/5 p-4">
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
                  <div>
                    <label className="mb-1 block text-[11px] font-medium text-slate-300">
                      Candles Canal
                    </label>
                    <input
                      type="number"
                      min={20}
                      max={150}
                      value={form.channel_window}
                      onChange={(e) =>
                        setForm((prev) => ({ ...prev, channel_window: Number(e.target.value) }))
                      }
                      className="input-dark font-mono text-xs"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-[11px] font-medium text-slate-300">
                      Desvio (±σ)
                    </label>
                    <input
                      type="number"
                      step="0.1"
                      min={1.0}
                      max={3.5}
                      value={form.channel_sigma}
                      onChange={(e) =>
                        setForm((prev) => ({ ...prev, channel_sigma: Number(e.target.value) }))
                      }
                      className="input-dark font-mono text-xs"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-[11px] font-medium text-slate-300">
                      Período ATR
                    </label>
                    <input
                      type="number"
                      min={5}
                      max={50}
                      value={form.atr_period}
                      onChange={(e) =>
                        setForm((prev) => ({ ...prev, atr_period: Number(e.target.value) }))
                      }
                      className="input-dark font-mono text-xs"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-[11px] font-medium text-emerald-300">
                      Alvo (× ATR)
                    </label>
                    <input
                      type="number"
                      step="0.05"
                      min={0.15}
                      max={5.0}
                      value={form.tp_atr_mult}
                      onChange={(e) =>
                        setForm((prev) => ({ ...prev, tp_atr_mult: Number(e.target.value) }))
                      }
                      className="input-dark font-mono text-xs"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-[11px] font-medium text-rose-300">
                      Stop (× ATR)
                    </label>
                    <input
                      type="number"
                      step="0.05"
                      min={0.25}
                      max={8.0}
                      value={form.sl_atr_mult}
                      onChange={(e) =>
                        setForm((prev) => ({ ...prev, sl_atr_mult: Number(e.target.value) }))
                      }
                      className="input-dark font-mono text-xs"
                    />
                  </div>
                </div>

                <label className="flex items-start gap-2.5 cursor-pointer text-xs text-slate-300">
                  <input
                    type="checkbox"
                    checked={form.use_crash_guard}
                    onChange={(e) =>
                      setForm((prev) => ({ ...prev, use_crash_guard: e.target.checked }))
                    }
                    className="mt-0.5 h-4 w-4 rounded border-slate-600 bg-slate-950 text-cyan-500"
                  />
                  <span>
                    <strong className="text-slate-100">Ativar Crash Guard (ADX &gt; 35 / ATR Spike)</strong> — recomendado para evitar entradas contra rompimentos verticais.
                  </span>
                </label>

                <p className="text-[11px] text-amber-200/90 leading-relaxed">
                  <strong>Dica de Backtest (Deriv):</strong> Em M1 no cryBTCUSD 100x, a comissão de 7 bps pode superar 0.75×ATR(10). Para testar este modo em Conta Demo, prefira M5/M15 ou ativos com comissão menor como <span className="font-mono">R_75 (50x)</span>. Use <strong>Máx. Camadas = 1</strong> para operar idêntico ao vídeo ou <strong>2–3</strong> para combinar com Preço Médio.
                </p>
              </div>
            )}
          </div>

          {/* Strategy & Execution Card */}
          <div className="glass-card p-5 space-y-4">
            <div className="flex items-center gap-2 border-b border-slate-800/80 pb-3">
              <Sliders className="h-4 w-4 text-emerald-400" />
              <h2 className="text-sm font-semibold text-slate-100">
                Parâmetros do Canal & Execução ({form.symbol})
              </h2>
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-300">
                  Timeframe do Canal
                </label>
                <select
                  value={form.timeframe_sec}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, timeframe_sec: Number(e.target.value) }))
                  }
                  className="input-dark"
                >
                  {TIMEFRAMES.map((tf) => (
                    <option key={tf.value} value={tf.value}>
                      {tf.label}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-300">
                  Direção das Operações
                </label>
                <select
                  value={form.direction_mode}
                  onChange={(e) =>
                    setForm((prev) => ({
                      ...prev,
                      direction_mode: e.target.value as DirectionMode,
                    }))
                  }
                  className="input-dark"
                >
                  {DIRECTIONS.map((d) => (
                    <option key={d.value} value={d.value}>
                      {d.label}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-300">
                  Stake Base — Entrada #1 (USD)
                </label>
                <input
                  type="number"
                  step="0.01"
                  min={minStakeForSymbol}
                  max={user?.max_stake_usd || 10000}
                  required
                  value={form.stake_usd}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, stake_usd: Number(e.target.value) }))
                  }
                  className="input-dark font-mono"
                />
                <p className="mt-1 text-[11px] text-slate-500">
                  Mínimo real da Deriv para {form.symbol}:{' '}
                  <span className="font-mono text-slate-300">{formatUsd(minStakeForSymbol)}</span>
                </p>
              </div>

              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-300">
                  Multiplicador Deriv
                </label>
                <select
                  value={form.multiplier}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, multiplier: Number(e.target.value) }))
                  }
                  className="input-dark font-mono"
                >
                  {availableMultipliers.map((m) => (
                    <option key={m} value={m}>
                      {m}x {m === availableMultipliers[0] ? '(Menor risco / maior distância stop-out)' : ''}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* Binance Order Flow Filter */}
            <label
              className={`flex items-start gap-3 rounded-xl border p-3.5 cursor-pointer transition-colors ${
                isBtcSymbol
                  ? 'border-cyan-500/30 bg-cyan-500/5 hover:border-cyan-500/50'
                  : 'border-slate-800 bg-slate-950/50 opacity-70'
              }`}
            >
              <input
                type="checkbox"
                checked={form.use_binance_filter}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, use_binance_filter: e.target.checked }))
                }
                className="mt-0.5 h-4 w-4 rounded border-slate-600 bg-slate-950 text-cyan-500 focus:ring-cyan-500/40"
              />
              <div className="text-xs">
                <div className="flex items-center gap-1.5 font-semibold text-slate-200">
                  <Sparkles className="h-3.5 w-3.5 text-cyan-400" />
                  Filtro de Fluxo Binance (BTCUSDT aggTrade + Order Book)
                </div>
                <p className="mt-0.5 text-slate-400">
                  {isBtcSymbol
                    ? 'Usa delta comprador e absorção na Binance (~600ms à frente da Deriv) como confirmação extra em cryBTCUSD.'
                    : 'Disponível prioritariamente para pares BTC (cryBTCUSD). Em outros pares é ignorado automaticamente.'}
                </p>
              </div>
            </label>
          </div>

          {/* Smart DCA Card */}
          <div className="glass-card p-5 space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/80 pb-3">
              <h2 className="text-sm font-semibold text-slate-100">
                Smart DCA — Preço Médio Controlado & Saída Coletiva
              </h2>
              <span className="rounded-md bg-amber-500/10 border border-amber-500/30 px-2 py-0.5 text-[10px] font-semibold text-amber-300">
                Anti-Martingale (Teto Seguro)
              </span>
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-300">
                  Máx. Camadas DCA
                </label>
                <select
                  value={form.max_layers}
                  onChange={(e) => handleMaxLayersChange(Number(e.target.value))}
                  className="input-dark font-mono"
                >
                  {[1, 2, 3, 4, 5].map((n) => (
                    <option key={n} value={n}>
                      {n} camada{n === 1 ? '' : 's'} {n === 3 ? '(Padrão)' : ''}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-300">
                  Alvo Líquido do Cesto (USD)
                </label>
                <input
                  type="number"
                  step="0.01"
                  min="0.01"
                  required
                  value={form.basket_tp_usd}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, basket_tp_usd: Number(e.target.value) }))
                  }
                  className="input-dark font-mono"
                />
              </div>

              <div className="flex items-end">
                <label className="flex w-full items-center justify-between rounded-xl border border-slate-800 bg-slate-950/80 px-3.5 py-2.5 cursor-pointer">
                  <span className="text-xs font-medium text-slate-200">Trailing Profit</span>
                  <input
                    type="checkbox"
                    checked={form.trailing_enabled}
                    onChange={(e) =>
                      setForm((prev) => ({ ...prev, trailing_enabled: e.target.checked }))
                    }
                    className="h-4 w-4 rounded border-slate-600 bg-slate-950 text-emerald-500 focus:ring-emerald-500/40"
                  />
                </label>
              </div>
            </div>

            {/* Layer Multipliers Controls */}
            <div className="space-y-2.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <label className="text-xs font-medium text-slate-300">
                  Multiplicadores por Camada (máx. 2.0x por camada)
                </label>
                <div className="flex flex-wrap gap-1.5">
                  {LAYER_PRESETS.map((preset) => (
                    <button
                      key={preset.label}
                      type="button"
                      onClick={() => applyLayerPreset(preset.values)}
                      className="rounded-lg border border-slate-800 bg-slate-950 px-2 py-1 text-[10px] font-mono text-slate-300 hover:border-cyan-500/40 hover:text-cyan-300 transition-colors"
                    >
                      {preset.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-5">
                {layerRatios.map((ratio, idx) => (
                  <div key={idx} className="rounded-xl border border-slate-800 bg-slate-950/70 p-2.5">
                    <span className="block text-[10px] text-slate-500 mb-1">Camada #{idx + 1}</span>
                    <input
                      type="number"
                      step="0.1"
                      min="0.5"
                      max="2.0"
                      value={ratio}
                      onChange={(e) => updateLayerRatio(idx, Number(e.target.value))}
                      className="w-full rounded-lg bg-slate-900 border border-slate-700/80 px-2 py-1 font-mono text-xs text-slate-100 focus:border-emerald-500 focus:outline-none"
                    />
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Risk Guard / Circuit Breakers Card */}
          <div className="glass-card p-5 space-y-4">
            <div className="flex items-center gap-2 border-b border-slate-800/80 pb-3">
              <Shield className="h-4 w-4 text-rose-400" />
              <h2 className="text-sm font-semibold text-slate-100">
                Circuit Breakers & Gestão de Risco
              </h2>
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-300">
                  Stop Global do Cesto (USD)
                </label>
                <input
                  type="number"
                  step="0.1"
                  min="0.1"
                  required
                  value={form.max_basket_loss_usd}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, max_basket_loss_usd: Number(e.target.value) }))
                  }
                  className="input-dark font-mono"
                />
              </div>

              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-300">
                  Stop Loss Diário (USD)
                </label>
                <input
                  type="number"
                  step="0.5"
                  min="0.5"
                  required
                  value={form.max_daily_loss_usd}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, max_daily_loss_usd: Number(e.target.value) }))
                  }
                  className="input-dark font-mono"
                />
              </div>

              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-300">
                  Meta de Lucro Diária (USD)
                </label>
                <input
                  type="number"
                  step="0.5"
                  min="0.5"
                  required
                  value={form.daily_profit_target_usd}
                  onChange={(e) =>
                    setForm((prev) => ({
                      ...prev,
                      daily_profit_target_usd: Number(e.target.value),
                    }))
                  }
                  className="input-dark font-mono"
                />
              </div>

              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-300">
                  Máx. Perdas Consecutivas
                </label>
                <input
                  type="number"
                  step="1"
                  min="1"
                  max="20"
                  required
                  value={form.max_consecutive_losses}
                  onChange={(e) =>
                    setForm((prev) => ({
                      ...prev,
                      max_consecutive_losses: Number(e.target.value),
                    }))
                  }
                  className="input-dark font-mono"
                />
              </div>

              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-300">
                  Timeout Máx. do Cesto (min)
                </label>
                <input
                  type="number"
                  step="5"
                  min="10"
                  max="1440"
                  required
                  value={form.max_hold_minutes}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, max_hold_minutes: Number(e.target.value) }))
                  }
                  className="input-dark font-mono"
                />
              </div>
            </div>

            <div className="flex justify-end pt-2">
              <button type="submit" disabled={saving} className="btn-primary px-6 py-3">
                {saving ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Salvando e Validando...
                  </>
                ) : (
                  <>
                    <Save className="h-4 w-4" />
                    Salvar Configurações do Robô
                  </>
                )}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
};
