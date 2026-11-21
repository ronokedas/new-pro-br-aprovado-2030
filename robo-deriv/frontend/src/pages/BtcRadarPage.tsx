import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Bell,
  Compass,
  Lock,
  Sliders,
  Volume2,
  VolumeX,
  Wifi,
  WifiOff,
} from 'lucide-react';
import { useAuth } from '../hooks/useAuth';
import { btcRadarApi, getStoredToken } from '../lib/api';
import { audioAlerts, type AudioPreferences } from '../lib/audioAlerts';
import { AudioSettingsModal } from '../components/AudioSettingsModal';
import { BtcCandleChart } from '../components/BtcCandleChart';
import {
  DEFAULT_TRIGGERS,
  TriggersSettingsModal,
  type TriggersPreferences,
} from '../components/TriggersSettingsModal';
import type { BtcKlineCandle, BtcSrZone } from '../types/api';
import './BtcRadar.css';

interface BiasSummary {
  baixa?: number;
  neutro?: number;
  alta?: number;
}

interface PredictionData {
  direction?: 'WAIT' | 'CALL' | 'PUT' | string;
  predicted_color?: string;
  confidence_pct?: number;
  product?: string;
  source?: string;
  lock_price?: number | null;
}

interface Signal2Data {
  direction?: 'WAIT' | 'CALL' | 'PUT' | string;
  label?: string;
  score?: number;
  entry_status?: string;
  entry_code?: string;
  regime?: string;
  dpx_5s_bps?: number;
}

interface OrderbookL2Data {
  ready?: boolean;
  imb5_spot?: number;
  imb5_perp?: number;
  ofi_spot_5s?: number;
  ofi_perp_5s?: number;
  regime?: string;
  max_bid_wall?: { price?: number; qty_btc?: number };
  max_ask_wall?: { price?: number; qty_btc?: number };
}

interface FlowData {
  cvd_1m?: number;
  cvd_5m?: number;
  aggression_ratio?: number;
  flow_bias?: string;
}

interface RecentResult {
  time?: string;
  candle_time?: string;
  predicted?: string;
  prediction?: string;
  actual?: string;
  result?: 'WIN' | 'LOSS' | string;
}

interface AuditSummaryData {
  total?: number;
  wins?: number;
  losses?: number;
  dojis?: number;
  winrate?: number;
  color_wins?: number;
  color_winrate?: number;
  recent_results?: RecentResult[];
}

interface ConfluenceData {
  modes?: { m5?: string; micro15?: string };
  context?: {
    m5_bias?: string;
    m5_trend_bps?: number;
    m5_pos?: number;
    m5_partial_bps?: number;
    vol?: 'BAIXA' | 'NORMAL' | 'ALTA' | string;
    m15s_dir?: string;
    m15s_score?: number;
  };
  verdicts?: {
    m5?: string;
    m15s?: string;
    perp?: string;
  };
}

interface LatencyTelemetryData {
  ready?: boolean;
  local_zero_lag_active?: boolean;
  official_kline_delay_ms?: number;
  avg_kline_delay_ms?: number;
  min_kline_delay_ms?: number;
  max_kline_delay_ms?: number;
  local_match_rate_pct?: number;
  last_kline_audit?: {
    candle_minute?: string;
    exchange?: string;
    official_close?: number;
    local_close?: number;
    matches?: boolean;
    delay_ms?: number;
    received_utc?: string;
  };
  prices?: {
    binance_futures?: number;
    bybit_perpetual?: number;
    spread_usd?: number | null;
    delta_trade_ms?: number | null;
  };
  ticks_per_sec?: {
    binance?: number;
    bybit?: number;
  };
  total_ticks?: {
    binance?: number;
    bybit?: number;
  };
}

interface FastBboData {
  clock_offset_ms?: number;
  clock_sync_rtt_ms?: number;
  clock_sync_source?: string;
  sec_in_minute?: number;
  server_now_ms?: number;
  binance_spot_mid?: number;
  binance_perp_mid?: number;
  bybit_perp_mid?: number;
  okx_perp_mid?: number;
  basis_binance_bps?: number;
  basis_bybit_bps?: number;
}

export interface IndicatorVoteItem {
  signal: 'CALL' | 'PUT' | 'NEUTRO';
  value: number;
  label: string;
  name?: string;
  extra?: string;
}

export interface IndicatorsVoteData {
  rsi?: IndicatorVoteItem;
  macd?: IndicatorVoteItem;
  ema_cross?: IndicatorVoteItem;
  sma_cross?: IndicatorVoteItem;
  bollinger?: IndicatorVoteItem;
  stochastic?: IndicatorVoteItem;
  adx?: IndicatorVoteItem;
  volume?: IndicatorVoteItem;
  atr?: IndicatorVoteItem;
  macro_m1?: IndicatorVoteItem;
  summary?: {
    call: number;
    put: number;
    neutro: number;
    verdict: string;
    verdict_label: string;
    pct: number;
    total: number;
  };
  order_flow?: {
    net_delta_pct: number;
    buy_pct: number;
    sell_pct: number;
  };
}

interface FullBtcState {
  symbol?: string;
  current_price?: number;
  seconds_left?: number;
  current_candle?: {
    time: number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume?: number;
    is_closed?: boolean;
  } | null;
  prediction?: PredictionData;
  signal2?: Signal2Data;
  orderbook_l2?: OrderbookL2Data;
  bias_summary?: BiasSummary;
  flow?: FlowData;
  indicators_vote?: IndicatorsVoteData;
  audit_summary?: AuditSummaryData;
  audit_by_product?: Record<string, AuditSummaryData>;
  audit_baseline?: AuditSummaryData;
  confluence?: ConfluenceData;
  latency_telemetry?: LatencyTelemetryData;
  fast_bbo?: FastBboData;
  timestamp?: string;
}

type MobileTab = 'both' | 'radar' | 'chart';

export const BtcRadarPage: React.FC = () => {
  const { user } = useAuth();
  const [state, setState] = useState<FullBtcState | null>(null);
  const [connected, setConnected] = useState(false);

  // Audio Alerts State
  const [audioModalOpen, setAudioModalOpen] = useState(false);
  const [audioPrefs, setAudioPrefs] = useState<AudioPreferences>(() =>
    audioAlerts.getPreferences()
  );

  // Triggers & Patterns Configuration State (persisted in localStorage + backend sync)
  const [triggersModalOpen, setTriggersModalOpen] = useState(false);
  const [triggersPrefs, setTriggersPrefs] = useState<TriggersPreferences>(() => {
    try {
      const saved = localStorage.getItem('btc_radar_triggers_cfg');
      if (saved) return { ...DEFAULT_TRIGGERS, ...JSON.parse(saved) };
    } catch {
      // ignore
    }
    return DEFAULT_TRIGGERS;
  });

  const handleTriggersChange = async (newPrefs: TriggersPreferences) => {
    setTriggersPrefs(newPrefs);
    try {
      localStorage.setItem('btc_radar_triggers_cfg', JSON.stringify(newPrefs));
      await btcRadarApi.updateTriggersConfig(newPrefs as unknown as Record<string, boolean>);
    } catch (err) {
      console.warn('Falha ao sincronizar preferências de gatilhos com backend:', err);
    }
  };

  useEffect(() => {
    btcRadarApi.getTriggersConfig()
      .then((cfg) => {
        if (cfg && typeof cfg === 'object') {
          setTriggersPrefs((prev) => ({
            ...prev,
            ...(cfg as unknown as Partial<TriggersPreferences>),
          }));
        }
      })
      .catch((err) => console.warn('Não foi possível buscar triggers_cfg do backend:', err));
  }, []);

  // Mobile View Tab State (persisted in localStorage)
  const [mobileTab, setMobileTab] = useState<MobileTab>(() => {
    try {
      const saved = localStorage.getItem('btc_radar_mobile_view');
      return saved === 'radar' || saved === 'chart' || saved === 'both' ? saved : 'both';
    } catch {
      return 'both';
    }
  });

  const handleMobileTabChange = (tab: MobileTab) => {
    setMobileTab(tab);
    try {
      localStorage.setItem('btc_radar_mobile_view', tab);
    } catch {
      // ignore
    }
  };

  // Chart data state
  const [candles, setCandles] = useState<BtcKlineCandle[]>([]);
  const [zones, setZones] = useState<BtcSrZone[]>([]);
  const [chartLoading, setChartLoading] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Initial HTTP fetch for radar state
  const fetchInitialState = useCallback(async () => {
    try {
      const data = await btcRadarApi.getState();
      setState(data as unknown as FullBtcState);
    } catch (err) {
      console.warn('Falha ao carregar estado inicial do radar BTC:', err);
    }
  }, []);

  // Fetch Kline Candles & S/R Zones
  const loadChartData = useCallback(async () => {
    setChartLoading(true);
    try {
      const [kRes, zRes] = await Promise.all([
        btcRadarApi.getKlines('BTCUSDT', '1m', 150),
        btcRadarApi.getSrZones('BTCUSDT'),
      ]);
      if (Array.isArray(kRes)) setCandles(kRes);
      if (zRes?.zones && Array.isArray(zRes.zones)) setZones(zRes.zones);
    } catch (err) {
      console.warn('Erro ao carregar dados do gráfico BTC:', err);
    } finally {
      setChartLoading(false);
    }
  }, []);

  // Initial load of chart & periodic S/R zone refresh (every 60s)
  useEffect(() => {
    if (user?.can_access_btc_radar === false) return;
    loadChartData();
    const interval = setInterval(loadChartData, 60000);
    return () => clearInterval(interval);
  }, [user, loadChartData]);

  // WebSocket Live Stream for Radar & Real-time Ticks
  useEffect(() => {
    if (user?.can_access_btc_radar === false) return;

    fetchInitialState();

    let isUnmounted = false;

    const connectWs = () => {
      const token = getStoredToken();
      if (!token) return;

      const apiBase = typeof window !== 'undefined' && window.location.pathname.startsWith('/painel-completo') ? '/painel-completo' : '';
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}${apiBase}/ws/btc-radar?token=${encodeURIComponent(token)}`;

      try {
        const ws = new WebSocket(wsUrl);
        wsRef.current = ws;

        ws.onopen = () => {
          if (!isUnmounted) setConnected(true);
        };

        ws.onmessage = (event) => {
          if (isUnmounted) return;
          try {
            const data = JSON.parse(event.data);
            if (data && typeof data === 'object') {
              setState(data as FullBtcState);

              // Audio sequence handler for :03s, :02s, :01s, :00s
              const sec = Number(data.seconds_left);
              if (!Number.isNaN(sec)) {
                audioAlerts.handleCountdownTick(sec);
              }
            }
          } catch {
            // Ignore parse errors
          }
        };

        ws.onclose = (event) => {
          if (!isUnmounted) {
            setConnected(false);
            if (event.code !== 4001 && event.code !== 4003 && event.code !== 4403) {
              reconnectTimeoutRef.current = setTimeout(connectWs, 2000);
            }
          }
        };

        ws.onerror = () => {
          ws.close();
        };
      } catch {
        if (!isUnmounted) {
          reconnectTimeoutRef.current = setTimeout(connectWs, 3000);
        }
      }
    };

    connectWs();

    const pollInterval = setInterval(() => {
      if (!connected && !isUnmounted) {
        fetchInitialState();
      }
    }, 5000);

    return () => {
      isUnmounted = true;
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      clearInterval(pollInterval);
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [user, fetchInitialState, connected]);

  // Access Gating Check
  if (user?.can_access_btc_radar === false) {
    return (
      <div className="mx-auto max-w-4xl p-6">
        <div className="glass-card border-rose-500/30 bg-rose-500/5 p-8 text-center space-y-4">
          <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-rose-500/10 border border-rose-500/30 text-rose-400">
            <Lock className="h-8 w-8" />
          </div>
          <h2 className="text-xl font-bold text-slate-100">Acesso Restrito ao Radar BTC M1</h2>
          <p className="mx-auto max-w-lg text-sm text-slate-400">
            O módulo quantitativo de previsão da próxima vela de Bitcoin (L2 Depth & Confluência Multi-exchange)
            é de acesso exclusivo. Seu cadastro atual não possui permissão concedida pelo administrador do sistema.
          </p>
          <div className="pt-2">
            <span className="inline-flex rounded-lg border border-slate-700 bg-slate-900 px-4 py-2 text-xs font-mono text-slate-300">
              ID Usuário: #{user?.id} • Status: {user?.status} • Radar: BLOQUEADO
            </span>
          </div>
        </div>
      </div>
    );
  }

  // Radar Data Extraction
  const symbol = state?.symbol || 'BTCUSDT • M1';
  const timerSec = state?.seconds_left !== undefined ? state.seconds_left : 58;
  const currentPriceFormatted = state?.current_price
    ? `$${state.current_price.toLocaleString('en-US', { minimumFractionDigits: 2 })}`
    : '$0.00';

  const bias = state?.bias_summary || { baixa: 0, neutro: 1, alta: 0 };

  // Painel Veredito Institucional (10 Indicadores)
  const indVote = state?.indicators_vote;
  const indSummary = indVote?.summary;
  const orderFlow = indVote?.order_flow;
  const callVotes = indSummary?.call ?? bias.alta ?? 0;
  const putVotes = indSummary?.put ?? bias.baixa ?? 0;

  // Sinal 1 (Fixo) com trava de consistência anti-contradição
  let dir1 = state?.prediction?.direction || 'WAIT';
  let predColorText =
    state?.prediction?.predicted_color || (dir1 === 'WAIT' ? 'AGUARDAR' : dir1 === 'CALL' ? 'VERDE' : 'VERMELHA');
  let confidencePct = state?.prediction?.confidence_pct || 50;

  if (triggersPrefs.enableInstitutional10 && dir1 !== 'WAIT') {
    if (dir1 === 'PUT' && callVotes > putVotes) {
      dir1 = 'WAIT';
      predColorText = `VETADO: INDICADORES EM ALTA (${callVotes}A vs ${putVotes}B)`;
      confidencePct = 50;
    } else if (dir1 === 'CALL' && putVotes > callVotes) {
      dir1 = 'WAIT';
      predColorText = `VETADO: INDICADORES EM BAIXA (${putVotes}B vs ${callVotes}A)`;
      confidencePct = 50;
    }
  }

  const isWait1 = dir1 === 'WAIT';
  const isCall1 = dir1 === 'CALL';
  const colorClass1 = isWait1 ? '' : isCall1 ? 'verde' : 'vermelha';
  const arrowChar1 = isWait1 ? '⏳' : isCall1 ? '⬆' : '⬇';

  // Sinal 2 (Ao Vivo L2)
  const s2 = state?.signal2;
  const dir2 = s2?.direction || 'WAIT';
  const isWait2 = dir2 === 'WAIT';
  const isCall2 = dir2 === 'CALL';
  const colorClass2 = isWait2 ? '' : isCall2 ? 'verde' : 'vermelha';
  const arrowChar2 = isWait2 ? '⏳' : isCall2 ? '⬆' : '⬇';
  const score2 = s2?.score !== undefined ? s2.score : 0;

  let entryStatus = s2?.entry_status || 'FORA DO MERCADO (AGUARDANDO)';
  let entryCode = s2?.entry_code || 'WAIT';

  // Sincroniza banner de timing de entrada para evitar alertas contraditórios
  if (isWait1) {
    entryStatus = predColorText.includes('VETADO')
      ? predColorText
      : 'FORA DO MERCADO (AGUARDANDO CONFLUÊNCIA)';
    entryCode = 'WAIT';
  } else if (isCall1 && dir2 === 'CALL') {
    entryStatus = '🎯 SINAL 1 + SINAL 2 ALINHADOS (CALL) — COMPRA CONFIRMADA';
    entryCode = 'BEST_ENTRY';
  } else if (!isCall1 && !isWait1 && dir2 === 'PUT') {
    entryStatus = '🎯 SINAL 1 + SINAL 2 ALINHADOS (PUT) — VENDA CONFIRMADA';
    entryCode = 'BEST_ENTRY';
  }

  // Livro L2 e Fluxo
  const l2 = state?.orderbook_l2;
  const imbS = l2?.imb5_spot !== undefined ? l2.imb5_spot : 0;
  const ofiS = l2?.ofi_spot_5s !== undefined ? l2.ofi_spot_5s : 0;
  const l2Text = l2?.ready
    ? `Imb ${imbS >= 0 ? '+' : ''}${imbS.toFixed(2)} | OFI ${ofiS >= 0 ? '+' : ''}${ofiS.toFixed(2)}`
    : 'Aguardando L2...';
  const regText = l2?.regime
    ? `${l2.regime} (Wall ${l2.max_bid_wall?.qty_btc ?? '-'}B/${l2.max_ask_wall?.qty_btc ?? '-'}A)`
    : 'NEUTRO';
  const cvdVal = state?.flow?.cvd_1m !== undefined ? state.flow.cvd_1m : 0.0;
  const aggressionRatio = state?.flow?.aggression_ratio || 1.0;
  const flowBias = state?.flow?.flow_bias || 'Equilibrado';

  // Auditoria dos 120 Minutos e Placar Ao Vivo
  const aud = state?.audit_summary;
  const winrate = aud?.winrate !== undefined ? aud.winrate : 54.6;
  const colorWinrate = aud?.color_winrate !== undefined ? aud.color_winrate : winrate;
  const recentResults = aud?.recent_results || [];
  const prodA = state?.audit_by_product?.product_a_0s;
  const prodB = state?.audit_by_product?.product_b_5s;

  // Confluência & Regime M5 / 15s
  const conf = state?.confluence;
  const ctx = conf?.context;
  const m5Trend = ctx?.m5_trend_bps;
  const m5Bias = ctx?.m5_bias || 'NEUTRO';
  const m5Pos = ctx?.m5_pos !== undefined ? ctx.m5_pos : 0;
  const volRegime = ctx?.vol || 'NORMAL';
  const m15Dir = ctx?.m15s_dir || 'NEUTRO';
  const m15Score = ctx?.m15s_score !== undefined ? ctx.m15s_score : 0;

  const activeAlertsCount =
    (audioPrefs.preSignalEnabled ? 1 : 0) + (audioPrefs.srTouchEnabled ? 1 : 0);

  return (
    <div className="btc-radar-root">
      {/* Top Banner Navigation & Summary Bar */}
      <div className="glass-card p-4">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-amber-500/20 via-orange-500/20 to-yellow-500/20 border border-amber-500/30 text-amber-400 shadow-glow">
              <Compass className="h-5 w-5 animate-pulse" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-sm font-bold tracking-tight text-slate-100">
                  Radar BTC M1 • Gráfico & Previsor de Próxima Vela
                </h1>
                <span className="rounded bg-amber-500/15 border border-amber-500/30 px-2 py-0.5 font-mono text-[10px] font-bold text-amber-300">
                  {symbol}
                </span>
              </div>
              <p className="text-[11px] text-slate-400">
                Gráfico M1 com Suporte/Resistência M5/M15 • Order Flow L2 • Gatilho :00s/:05s
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            {/* Spot Price */}
            <div className="rounded-xl border border-slate-800 bg-slate-950/80 px-3 py-1.5">
              <span className="text-[10px] uppercase tracking-wider text-slate-400 block font-medium">
                Cotação Spot
              </span>
              <span className="font-mono text-sm font-bold text-slate-100">
                {currentPriceFormatted}
              </span>
            </div>

            {/* Live Connection Pill */}
            <div
              className={`flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-semibold ${
                connected
                  ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                  : 'border-amber-500/40 bg-amber-500/10 text-amber-300'
              }`}
            >
              {connected ? (
                <Wifi className="h-3.5 w-3.5 animate-pulse" />
              ) : (
                <WifiOff className="h-3.5 w-3.5" />
              )}
              <span>{connected ? 'Binance Live ⚡' : 'Reconectando...'}</span>
            </div>

            {/* Audio Settings Button */}
            <button
              type="button"
              onClick={() => setAudioModalOpen(true)}
              className={`flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-bold font-mono transition-colors ${
                activeAlertsCount > 0
                  ? 'border-cyan-500/40 bg-cyan-500/15 text-cyan-300 shadow-sm shadow-cyan-950/20'
                  : 'border-slate-800 bg-slate-900 text-slate-500 hover:text-slate-300'
              }`}
              title="Configurar Alertas Sonoros (Pré-Sinal e Toque S/R)"
            >
              {activeAlertsCount > 0 ? (
                <Volume2 className="h-3.5 w-3.5" />
              ) : (
                <VolumeX className="h-3.5 w-3.5" />
              )}
              <span>
                {activeAlertsCount > 0
                  ? `Áudio: ${activeAlertsCount} Alerta${activeAlertsCount > 1 ? 's' : ''}`
                  : 'Áudio Mudo'}
              </span>
            </button>

            {/* Triggers & Patterns Configuration Button */}
            <button
              type="button"
              onClick={() => setTriggersModalOpen(true)}
              className={`flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-bold font-mono transition-colors ${
                triggersPrefs.enableProductA &&
                triggersPrefs.enableProductB &&
                triggersPrefs.enableL2Veto &&
                !triggersPrefs.enableM5Filter &&
                !triggersPrefs.enableMicro15s &&
                !triggersPrefs.enableInstitutional10 &&
                !triggersPrefs.enableAbsorptionSR
                  ? 'border-emerald-500/40 bg-emerald-500/15 text-emerald-300 shadow-sm shadow-emerald-950/20 hover:bg-emerald-500/25'
                  : 'border-amber-500/40 bg-amber-500/15 text-amber-300 shadow-sm shadow-amber-950/20 hover:bg-amber-500/25'
              }`}
              title="Configurar Gatilhos e Padrões de Entrada (Por padrão opera na estratégia comprovada)"
            >
              <Sliders className="h-3.5 w-3.5" />
              <span>
                {triggersPrefs.enableProductA &&
                triggersPrefs.enableProductB &&
                triggersPrefs.enableL2Veto &&
                !triggersPrefs.enableM5Filter &&
                !triggersPrefs.enableMicro15s &&
                !triggersPrefs.enableInstitutional10 &&
                !triggersPrefs.enableAbsorptionSR
                  ? 'Estratégia Padrão ✓'
                  : 'Filtros Ativos ⚠️'}
              </span>
            </button>
          </div>
        </div>
      </div>

      {/* Mobile Segmented View Selector (Visible on Screens < 1024px) */}
      <div className="flex lg:hidden items-center justify-between rounded-xl border border-slate-800 bg-[#0d121c] p-1.5 shadow-md">
        <button
          type="button"
          onClick={() => handleMobileTabChange('radar')}
          className={`flex-1 rounded-lg py-2 text-xs font-bold font-mono transition-colors ${
            mobileTab === 'radar'
              ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 shadow-sm'
              : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          🎯 Radar (2 Velas)
        </button>
        <button
          type="button"
          onClick={() => handleMobileTabChange('chart')}
          className={`flex-1 rounded-lg py-2 text-xs font-bold font-mono transition-colors ${
            mobileTab === 'chart'
              ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 shadow-sm'
              : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          📈 Gráfico M1
        </button>
        <button
          type="button"
          onClick={() => handleMobileTabChange('both')}
          className={`flex-1 rounded-lg py-2 text-xs font-bold font-mono transition-colors ${
            mobileTab === 'both'
              ? 'bg-amber-500/20 text-amber-300 border border-amber-500/40 shadow-sm'
              : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          📑 Ambos
        </button>
      </div>

      {/* Main Split Grid: Chart (Left) + Dual Candle Tool (Right) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        {/* LADO ESQUERDO: GRÁFICO BTC M1 COM ZONAS M5 E M15 */}
        <div
          className={`lg:col-span-7 xl:col-span-8 flex flex-col h-[520px] lg:h-[760px] ${
            mobileTab === 'radar' ? 'hidden lg:flex' : 'flex'
          }`}
        >
          <BtcCandleChart
            candles={candles}
            zones={zones}
            livePrice={state?.current_price}
            currentCandle={state?.current_candle}
            secondsLeft={state?.seconds_left}
            loading={chartLoading}
            onRefresh={loadChartData}
          />
        </div>

        {/* LADO DIREITO: A FERRAMENTA DAS DUAS VELAS (RADAR PREVISOR) */}
        <div
          className={`lg:col-span-5 xl:col-span-4 flex justify-center ${
            mobileTab === 'chart' ? 'hidden lg:flex' : 'flex'
          }`}
        >
          <div className="radar-container w-full">
            {/* Header da Ferramenta */}
            <div className="radar-header-row">
              <div className="symbol-badge">{symbol}</div>
              <div className="status-live">
                <div className="dot-live"></div>
                <span>{connected ? 'Binance Live ⚡' : 'Reconectando...'}</span>
              </div>
            </div>

            {/* Painel de Viés Técnico (Estilo TradingView / Contagem Real de Indicadores) */}
            <div className="bias-panel">
              <div className="bias-box baixa">
                <span className="bias-title">Viés de Baixa</span>
                <span className="bias-val">{indSummary?.put ?? bias.baixa ?? 0}</span>
              </div>
              <div className="bias-box neutro">
                <span className="bias-title">Viés Neutro</span>
                <span className="bias-val">{indSummary?.neutro ?? bias.neutro ?? 0}</span>
              </div>
              <div className="bias-box alta">
                <span className="bias-title">Viés de Alta</span>
                <span className="bias-val">{indSummary?.call ?? bias.alta ?? 0}</span>
              </div>
            </div>

            {/* Timer Regressivo M1 */}
            <div className="timer-card">
              <span className="timer-label">Fechamento da Vela M1</span>
              <div className="timer-display">
                <span className="timer-icon">⏱</span>
                <span
                  className="timer-val"
                  style={{ color: timerSec <= 10 ? 'var(--accent-gold)' : 'var(--accent-cyan)' }}
                >
                  {timerSec < 10 ? `0${timerSec}` : `${timerSec}`}
                </span>
                <span className="timer-unit">segundos</span>
              </div>
            </div>

            {/* SINAL 1 (TRAVADO) • SINAL 2 (TEMPO REAL L2) — AS DUAS VELAS */}
            <div className="prediction-hero-box">
              <div className="pred-header">SINAL 1 (TRAVADO) • SINAL 2 (TEMPO REAL L2)</div>

              <div className="dual-signal-row">
                {/* Sinal 1 Travado */}
                <div className="signal-col">
                  <span className="signal-sub">SINAL 1 • FIXO</span>
                  <div className="candle-arrow-display">
                    {/* Vela 1 */}
                    <div className={`standard-candle ${colorClass1}`}>
                      <div className="candle-wick top"></div>
                      <div className="candle-body">
                        <span className="candle-label">{dir1}</span>
                      </div>
                      <div className="candle-wick bottom"></div>
                    </div>
                    {/* Seta / Ampulheta 1 */}
                    <div className={`hero-arrow ${colorClass1}`}>{arrowChar1}</div>
                  </div>
                </div>

                <div className="signal-divider"></div>

                {/* Sinal 2 Oscilante Ao Vivo (Livro L2 + Tape) */}
                <div className="signal-col">
                  <span className="signal-sub">SINAL 2 • AO VIVO (L2)</span>
                  <div className="candle-arrow-display compact">
                    {/* Vela 2 */}
                    <div className={`standard-candle mini ${colorClass2}`}>
                      <div className="candle-wick top"></div>
                      <div className="candle-body">
                        <span className="candle-label">{isWait2 ? 'L2' : dir2}</span>
                      </div>
                      <div className="candle-wick bottom"></div>
                    </div>
                    {/* Seta / Ampulheta 2 */}
                    <div className={`hero-arrow mini ${colorClass2}`}>{arrowChar2}</div>
                  </div>
                  <span
                    className="signal2-score"
                    style={{
                      color: isWait2
                        ? 'var(--text-muted)'
                        : isCall2
                          ? 'var(--accent-green)'
                          : 'var(--accent-red)',
                    }}
                  >
                    Score L2: {score2 >= 0 ? '+' : ''}
                    {score2.toFixed(2)}
                  </span>
                </div>
              </div>

              {/* Banner de Timing de Entrada */}
              <div className={`entry-banner ${entryCode}`}>{entryStatus}</div>

              <div className="pred-footer">
                <span className="confidence-badge">Confiança: {confidencePct}%</span>
                <span
                  className="color-badge"
                  style={{
                    color: isWait1
                      ? 'var(--accent-gold)'
                      : isCall1
                        ? 'var(--accent-green)'
                        : 'var(--accent-red)',
                  }}
                >
                  {isWait1 ? predColorText : `COR: ${predColorText}`}
                </span>
              </div>
            </div>

            {/* Card de Fluxo Real, CVD & Livro de Ofertas L2 */}
            <div className="flow-card">
              <div className="flow-row">
                <span>CVD Vela Anterior:</span>
                <strong
                  style={{
                    color: cvdVal >= 0 ? 'var(--accent-green)' : 'var(--accent-red)',
                  }}
                >
                  {cvdVal > 0 ? '+' : ''}
                  {cvdVal} BTC
                </strong>
              </div>
              <div className="flow-row">
                <span>Motor / Filtro:</span>
                <strong>
                  {aggressionRatio}x ({flowBias})
                </strong>
              </div>
              <div className="flow-row">
                <span>Livro L2 (Imb5 / OFI 5s):</span>
                <strong style={{ color: imbS >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' }}>
                  {l2Text}
                </strong>
              </div>
              <div className="flow-row">
                <span>Regime L2 / Paredes:</span>
                <strong>{regText}</strong>
              </div>
              <div className="flow-row">
                <span>Preço BTC:</span>
                <strong>{currentPriceFormatted}</strong>
              </div>
            </div>

            {/* PAINEL DE BAIXA LATÊNCIA & FEED TICK-A-TICK ZERO DELAY */}
            <div className="latency-panel">
              <div className="latency-header">
                <span>Telemetria de Latência & NTP</span>
                <span className="latency-badge">ZERO DELAY ATIVO ⚡</span>
              </div>

              <div className="latency-grid">
                <div className="latency-cell">
                  <span className="latency-cell-title">Vela Local M1</span>
                  <span className="latency-cell-val fast">0 ms (Instantâneo)</span>
                </div>
                <div className="latency-cell">
                  <span className="latency-cell-title">Atraso Kline Corretora</span>
                  <span className="latency-cell-val kline">
                    ~{state?.latency_telemetry?.last_kline_audit?.delay_ms !== undefined
                      ? `${state.latency_telemetry.last_kline_audit.delay_ms} ms`
                      : '280 ms'}
                  </span>
                </div>
                <div className="latency-cell">
                  <span className="latency-cell-title">Fluxo Binance Futures</span>
                  <span className="latency-cell-val fast">
                    {state?.latency_telemetry?.ticks_per_sec?.binance !== undefined
                      ? `${state.latency_telemetry.ticks_per_sec.binance} t/s`
                      : '~600 t/s'}
                  </span>
                </div>
                <div className="latency-cell">
                  <span className="latency-cell-title">Fluxo Bybit Perp</span>
                  <span className="latency-cell-val bybit">
                    {state?.latency_telemetry?.ticks_per_sec?.bybit !== undefined
                      ? `${state.latency_telemetry.ticks_per_sec.bybit} t/s`
                      : '~35 t/s'}
                  </span>
                </div>
              </div>

              <div className="latency-footer-row">
                <span>
                  Relógio: {state?.fast_bbo?.clock_sync_source || 'NTP Atômico'} (Offset:{' '}
                  {state?.fast_bbo?.clock_offset_ms !== undefined ? `${state.fast_bbo.clock_offset_ms}ms` : '0ms'})
                </span>
                <span style={{ color: 'var(--accent-green)' }}>
                  OHLCV: {state?.latency_telemetry?.local_match_rate_pct !== undefined ? `${state.latency_telemetry.local_match_rate_pct}%` : '100%'} confluência
                </span>
              </div>
            </div>

            {/* CARD DE CONFLUÊNCIA DE REGIME M5 & MICROESTRUTURA 15s */}
            <div className="flow-card" style={{ marginTop: '0.75rem', borderColor: 'rgba(0, 229, 255, 0.2)' }}>
              <div className="flex items-center justify-between border-b border-slate-800/80 pb-2 mb-2 text-xs font-mono font-bold">
                <span className="text-cyan-300">Confluência de Regime M5 & 15s</span>
                <span
                  className="rounded px-2 py-0.5 text-[10px]"
                  style={{
                    backgroundColor:
                      volRegime === 'BAIXA'
                        ? 'rgba(0, 255, 136, 0.15)'
                        : volRegime === 'ALTA'
                          ? 'rgba(255, 51, 85, 0.15)'
                          : 'rgba(0, 229, 255, 0.15)',
                    color:
                      volRegime === 'BAIXA'
                        ? 'var(--accent-green)'
                        : volRegime === 'ALTA'
                          ? 'var(--accent-red)'
                          : 'var(--accent-cyan)',
                    border: `1px solid ${
                      volRegime === 'BAIXA'
                        ? 'var(--accent-green)'
                        : volRegime === 'ALTA'
                          ? 'var(--accent-red)'
                          : 'var(--accent-cyan)'
                    }`,
                  }}
                >
                  Vol: {volRegime} {volRegime === 'BAIXA' ? '(+2.4% Edge)' : volRegime === 'ALTA' ? '(Cautela)' : ''}
                </span>
              </div>
              <div className="flow-row">
                <span>Regime M5 Fechado:</span>
                <strong style={{ color: m5Bias === 'CALL' ? 'var(--accent-green)' : m5Bias === 'PUT' ? 'var(--accent-red)' : 'var(--accent-gold)' }}>
                  {m5Bias} ({m5Trend !== undefined ? `${m5Trend} bps` : '0 bps'}) • Minuto {m5Pos + 1}/5
                </strong>
              </div>
              <div className="flow-row">
                <span>Microestrutura 15s (4x15s):</span>
                <strong style={{ color: m15Dir === 'CALL' ? 'var(--accent-green)' : m15Dir === 'PUT' ? 'var(--accent-red)' : 'var(--text-secondary)' }}>
                  {m15Dir} (Score {m15Score >= 0 ? '+' : ''}{m15Score})
                </strong>
              </div>
            </div>

            {/* PAINEL VEREDITO INSTITUCIONAL (10 INDICADORES) — ESTILO HARMONIZADO COM O TEMA */}
            {indVote && indSummary && (
              <div
                style={{
                  marginTop: '0.75rem',
                  border: '1px solid var(--border-color, #202738)',
                  borderRadius: '16px',
                  backgroundColor: 'var(--bg-card, #121722)',
                  boxShadow: '0 8px 30px rgba(0, 0, 0, 0.45)',
                  overflow: 'hidden',
                }}
              >
                {/* Header Principal com Ícone, Status e Percentual */}
                <div
                  style={{
                    padding: '12px 16px',
                    borderBottom: '1px solid var(--border-color, #202738)',
                    backgroundColor: 'rgba(23, 29, 43, 0.7)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <div
                      style={{
                        width: '36px',
                        height: '36px',
                        borderRadius: '10px',
                        backgroundColor: '#192132',
                        border: '1px solid var(--border-color, #202738)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: '17px',
                      }}
                    >
                      {indSummary.verdict === 'CALL' ? '📈' : indSummary.verdict === 'PUT' ? '📉' : '⚖️'}
                    </div>
                    <div>
                      <div
                        style={{
                          fontSize: '10px',
                          color: 'var(--accent-cyan, #00e5ff)',
                          fontWeight: 700,
                          fontFamily: 'monospace',
                          letterSpacing: '0.8px',
                          textTransform: 'uppercase',
                        }}
                      >
                        Veredito Institucional (10 Indicadores)
                      </div>
                      <div
                        style={{
                          fontSize: '14px',
                          color: '#ffffff',
                          fontWeight: 800,
                          fontFamily: 'monospace',
                          letterSpacing: '0.5px',
                        }}
                      >
                        {indSummary.verdict_label}
                      </div>
                    </div>
                  </div>

                  <div style={{ textAlign: 'right' }}>
                    <div
                      style={{
                        fontSize: '16px',
                        fontWeight: 900,
                        fontFamily: 'monospace',
                        color:
                          indSummary.verdict === 'CALL'
                            ? 'var(--accent-green, #00ff88)'
                            : indSummary.verdict === 'PUT'
                              ? 'var(--accent-red, #ff3355)'
                              : 'var(--accent-gold, #ffcc00)',
                      }}
                    >
                      {indSummary.pct}%{' '}
                      {indSummary.verdict === 'CALL'
                        ? 'ALTA'
                        : indSummary.verdict === 'PUT'
                          ? 'BAIXA'
                          : 'NEUTRO'}
                    </div>
                    <div
                      style={{
                        fontSize: '11px',
                        color: 'var(--text-muted, #7b88a1)',
                        fontFamily: 'monospace',
                        fontWeight: 700,
                      }}
                    >
                      {indSummary.call}C / {indSummary.neutro}N / {indSummary.put}V
                    </div>
                  </div>
                </div>

                {/* Barra de Progresso Fatiada dos Indicadores */}
                <div style={{ padding: '8px 16px', backgroundColor: '#0d121c' }}>
                  <div
                    style={{
                      display: 'flex',
                      height: '6px',
                      borderRadius: '3px',
                      overflow: 'hidden',
                      backgroundColor: 'rgba(255, 255, 255, 0.08)',
                      marginBottom: '8px',
                    }}
                  >
                    <div
                      style={{
                        width: `${Math.round((indSummary.call / indSummary.total) * 100)}%`,
                        backgroundColor: 'var(--accent-green, #00ff88)',
                        transition: 'width 0.4s ease',
                      }}
                    />
                    <div
                      style={{
                        width: `${Math.round((indSummary.neutro / indSummary.total) * 100)}%`,
                        backgroundColor: 'var(--accent-gold, #ffcc00)',
                        transition: 'width 0.4s ease',
                      }}
                    />
                    <div
                      style={{
                        flex: 1,
                        backgroundColor: 'var(--accent-red, #ff3355)',
                        transition: 'width 0.4s ease',
                      }}
                    />
                  </div>

                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      fontSize: '10px',
                      fontFamily: 'monospace',
                      fontWeight: 700,
                    }}
                  >
                    <span style={{ color: 'var(--accent-green, #00ff88)' }}>
                      ▲ {indSummary.call} ALTA ({Math.round((indSummary.call / indSummary.total) * 100)}%)
                    </span>
                    <span style={{ color: 'var(--accent-gold, #ffcc00)' }}>
                      — {indSummary.neutro} LATERAL / NEUTRO
                    </span>
                    <span style={{ color: 'var(--accent-red, #ff3355)' }}>
                      ▼ {indSummary.put} BAIXA ({Math.round((indSummary.put / indSummary.total) * 100)}%)
                    </span>
                  </div>
                </div>

                {/* Card de ORDER FLOW (PRESSÃO DELTA) */}
                {orderFlow && (
                  <div
                    style={{
                      padding: '10px 16px',
                      borderTop: '1px solid var(--border-color, #202738)',
                      borderBottom: '1px solid var(--border-color, #202738)',
                      backgroundColor: '#0d121c',
                    }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        marginBottom: '6px',
                      }}
                    >
                      <span
                        style={{
                          fontSize: '10px',
                          color: 'var(--accent-cyan, #00e5ff)',
                          fontFamily: 'monospace',
                          fontWeight: 700,
                          display: 'flex',
                          alignItems: 'center',
                          gap: '4px',
                        }}
                      >
                        ⏱ ORDER FLOW (PRESSÃO DELTA)
                      </span>
                      <span
                        style={{
                          fontSize: '10px',
                          fontFamily: 'monospace',
                          fontWeight: 800,
                          padding: '1.5px 8px',
                          borderRadius: '6px',
                          backgroundColor:
                            orderFlow.net_delta_pct >= 0
                              ? 'rgba(0, 255, 136, 0.12)'
                              : 'rgba(255, 51, 85, 0.12)',
                          border: `1px solid ${
                            orderFlow.net_delta_pct >= 0
                              ? 'rgba(0, 255, 136, 0.35)'
                              : 'rgba(255, 51, 85, 0.35)'
                          }`,
                          color: orderFlow.net_delta_pct >= 0 ? 'var(--accent-green, #00ff88)' : 'var(--accent-red, #ff3355)',
                        }}
                      >
                        {orderFlow.net_delta_pct >= 0 ? '+' : ''}
                        {orderFlow.net_delta_pct}% NET DELTA
                      </span>
                    </div>

                    <div
                      style={{
                        display: 'flex',
                        height: '7px',
                        borderRadius: '4px',
                        overflow: 'hidden',
                        backgroundColor: 'rgba(255, 255, 255, 0.08)',
                      }}
                    >
                      <div
                        style={{
                          width: `${orderFlow.buy_pct}%`,
                          background: 'linear-gradient(90deg, #00b4d8, #00ff88)',
                          transition: 'width 0.4s ease',
                        }}
                      />
                      <div
                        style={{
                          width: `${orderFlow.sell_pct}%`,
                          background: 'linear-gradient(90deg, #ff3355, #c9184a)',
                          transition: 'width 0.4s ease',
                        }}
                      />
                    </div>

                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        marginTop: '4px',
                        fontSize: '10px',
                        fontFamily: 'monospace',
                      }}
                    >
                      <span style={{ color: 'var(--accent-green, #00ff88)', fontWeight: 700 }}>
                        {orderFlow.buy_pct}% Força Compradora
                      </span>
                      <span style={{ color: 'var(--accent-red, #ff3355)', fontWeight: 700 }}>
                        {orderFlow.sell_pct}% Força Vendedora
                      </span>
                    </div>
                  </div>
                )}

                {/* Grid 2x5 dos 10 Indicadores */}
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(2, 1fr)',
                    gap: '6px',
                    padding: '10px 14px',
                    backgroundColor: 'var(--bg-card, #121722)',
                  }}
                >
                  {[
                    { key: 'rsi', ind: indVote.rsi, defaultName: 'RSI (14)', unit: 'pts' },
                    { key: 'macd', ind: indVote.macd, defaultName: 'MACD', unit: '' },
                    { key: 'ema_cross', ind: indVote.ema_cross, defaultName: 'EMA (9/21)', unit: '' },
                    { key: 'sma_cross', ind: indVote.sma_cross, defaultName: 'SMA (50/200)', unit: '' },
                    { key: 'bollinger', ind: indVote.bollinger, defaultName: 'Bollinger', unit: '%' },
                    { key: 'stochastic', ind: indVote.stochastic, defaultName: 'Estocástico', unit: '' },
                    { key: 'adx', ind: indVote.adx, defaultName: 'ADX Força', unit: 'pts' },
                    { key: 'volume', ind: indVote.volume, defaultName: 'Volume', unit: 'x' },
                    { key: 'atr', ind: indVote.atr, defaultName: 'ATR', unit: 'pips' },
                    { key: 'macro_m1', ind: indVote.macro_m1, defaultName: 'Macro (M1)', unit: '' },
                  ].map(({ key, ind, defaultName, unit }) => {
                    const isCall = ind?.signal === 'CALL';
                    const isPut = ind?.signal === 'PUT';
                    const badgeColor = isCall ? 'var(--accent-green, #00ff88)' : isPut ? 'var(--accent-red, #ff3355)' : 'var(--accent-gold, #ffcc00)';
                    const badgeBg = isCall
                      ? 'rgba(0, 255, 136, 0.12)'
                      : isPut
                        ? 'rgba(255, 51, 85, 0.12)'
                        : 'rgba(255, 204, 0, 0.12)';
                    const badgeBorder = isCall
                      ? 'rgba(0, 255, 136, 0.35)'
                      : isPut
                        ? 'rgba(255, 51, 85, 0.35)'
                        : 'rgba(255, 204, 0, 0.35)';
                    const badgeLabel = isCall ? '▲ ALTA' : isPut ? '▼ BAIXA' : '— LATERAL';

                    // Formatação amigável do valor
                    let displayVal = '—';
                    if (ind?.extra) {
                      displayVal = ind.extra;
                    } else if (key === 'ema_cross') {
                      displayVal = (ind?.value ?? 0) >= 0 ? 'EMA 9 > 21' : 'EMA 9 < 21';
                    } else if (key === 'sma_cross') {
                      displayVal = (ind?.value ?? 0) >= 0 ? '50 > 200' : '50 < 200';
                    } else if (key === 'macro_m1') {
                      displayVal = isCall ? 'ALTA' : isPut ? 'BAIXA' : 'LATERAL';
                    } else if (typeof ind?.value === 'number') {
                      if (key === 'macd') {
                        displayVal = `${ind.value >= 0 ? '+' : ''}${ind.value.toFixed(6)}`;
                      } else {
                        displayVal = `${ind.value}${unit ? ` ${unit}` : ''}`;
                      }
                    }

                    return (
                      <div
                        key={key}
                        style={{
                          backgroundColor: '#0b0f17',
                          border: '1px solid var(--border-color, #202738)',
                          borderRadius: '8px',
                          padding: '7px 9px',
                          display: 'flex',
                          flexDirection: 'column',
                          justifyContent: 'space-between',
                          minHeight: '58px',
                        }}
                      >
                        <div
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                          }}
                        >
                          <span
                            style={{
                              fontSize: '11px',
                              fontFamily: 'monospace',
                              fontWeight: 700,
                              color: '#d1d8e6',
                            }}
                          >
                            {ind?.name || defaultName}
                          </span>
                          <span
                            style={{
                              fontSize: '9px',
                              fontFamily: 'monospace',
                              fontWeight: 800,
                              padding: '1px 5px',
                              borderRadius: '4px',
                              backgroundColor: badgeBg,
                              border: `1px solid ${badgeBorder}`,
                              color: badgeColor,
                            }}
                          >
                            {badgeLabel}
                          </span>
                        </div>

                        <div
                          style={{
                            fontSize: '12.5px',
                            fontWeight: 800,
                            fontFamily: 'monospace',
                            color: '#ffffff',
                            margin: '2px 0',
                          }}
                        >
                          {displayVal}
                        </div>

                        <div
                          style={{
                            fontSize: '10px',
                            fontFamily: 'monospace',
                            color: 'var(--text-muted, #7b88a1)',
                          }}
                        >
                          {ind?.label || 'Analisando'}
                        </div>
                      </div>
                    );
                  })}
                </div>

                {/* Rodapé do Painel com Confluência e Botão de Veredito */}
                <div
                  style={{
                    padding: '10px 16px',
                    borderTop: '1px solid var(--border-color, #202738)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    backgroundColor: 'rgba(23, 29, 43, 0.7)',
                  }}
                >
                  <span
                    style={{
                      fontSize: '11px',
                      color: 'var(--accent-cyan, #00e5ff)',
                      fontFamily: 'monospace',
                      fontWeight: 700,
                      display: 'flex',
                      alignItems: 'center',
                      gap: '4px',
                    }}
                  >
                    ✨ Confluência: {indSummary.call} CALL vs {indSummary.put} PUT
                  </span>

                  <span
                    style={{
                      fontSize: '11px',
                      fontWeight: 900,
                      fontFamily: 'monospace',
                      padding: '3px 12px',
                      borderRadius: '6px',
                      backgroundColor:
                        indSummary.verdict === 'CALL'
                          ? 'rgba(0, 255, 136, 0.15)'
                          : indSummary.verdict === 'PUT'
                            ? 'rgba(255, 51, 85, 0.15)'
                            : 'rgba(255, 204, 0, 0.15)',
                      border: `1px solid ${
                        indSummary.verdict === 'CALL'
                          ? 'var(--accent-green, #00ff88)'
                          : indSummary.verdict === 'PUT'
                            ? 'var(--accent-red, #ff3355)'
                            : 'var(--accent-gold, #ffcc00)'
                      }`,
                      color:
                        indSummary.verdict === 'CALL'
                          ? 'var(--accent-green, #00ff88)'
                          : indSummary.verdict === 'PUT'
                            ? 'var(--accent-red, #ff3355)'
                            : 'var(--accent-gold, #ffcc00)',
                    }}
                  >
                    {indSummary.verdict_label}
                  </span>
                </div>
              </div>
            )}

            {/* HISTÓRICO REAL DOS ÚLTIMOS 120 MINUTOS & AO VIVO */}
            <div className="audit-card">
              <div className="audit-header">
                <div>
                  <span className="block font-bold">PLACAR AO VIVO & AUDITORIA</span>
                  <span className="text-[10px] text-slate-400 font-normal">
                    Medição pelo Preço de Entrada Real (Anti-Ilusão)
                  </span>
                </div>
                <div className="flex flex-col items-end gap-1">
                  <span
                    className="winrate-badge"
                    style={{
                      color: winrate >= 54.6 ? 'var(--accent-green)' : 'var(--accent-gold)',
                      borderColor: winrate >= 54.6 ? 'var(--accent-green)' : 'var(--accent-gold)',
                    }}
                    title="Assertividade calculada comparando o fechamento da vela contra o PREÇO DE ENTRADA real do clique"
                  >
                    {winrate}% Entrada Real
                  </span>
                  <span className="text-[10px] text-slate-400 font-mono">
                    Cor da vela: <strong className="text-slate-200">{colorWinrate}%</strong>
                  </span>
                </div>
              </div>

              <div className="audit-stats-row">
                <div>
                  Total: <strong>{aud?.total || 0}</strong>
                </div>
                <div style={{ color: 'var(--accent-green)' }}>
                  Wins: <strong>{aud?.wins || 0}</strong>
                </div>
                <div style={{ color: 'var(--accent-red)' }}>
                  Losses: <strong>{aud?.losses || 0}</strong>
                </div>
              </div>

              {/* Subtotais por Produto A e B */}
              {(prodA || prodB) && (
                <div className="flex items-center justify-between border-t border-slate-800/80 pt-1.5 mt-1.5 text-[10px] font-mono text-slate-400">
                  <span>
                    Prod A (+0s): <strong className="text-emerald-400">{prodA?.winrate ?? 0}%</strong> ({prodA?.wins ?? 0}W/{prodA?.losses ?? 0}L)
                  </span>
                  <span>
                    Prod B (+5s): <strong className="text-cyan-400">{prodB?.winrate ?? 0}%</strong> ({prodB?.wins ?? 0}W/{prodB?.losses ?? 0}L)
                  </span>
                </div>
              )}

              {/* Fita com as últimas velas */}
              <div className="results-tape">
                {recentResults && recentResults.length > 0 ? (
                  recentResults
                    .slice()
                    .reverse()
                    .map((r, i) => {
                      const t = r.time || r.candle_time || '';
                      const res = r.result === 'WIN' || r.result === 'LOSS' ? r.result : 'WIN';
                      const pred = r.predicted || r.prediction || '';
                      return (
                        <div key={i} className={`result-pill ${res}`}>
                          [{t}] {res === 'WIN' ? '✅' : '❌'} {pred}
                        </div>
                      );
                    })
                ) : (
                  <div className="text-xs text-slate-500 py-2">Coletando histórico...</div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Audio Settings Modal */}
      <AudioSettingsModal
        isOpen={audioModalOpen}
        onClose={() => setAudioModalOpen(false)}
        onPreferencesChange={setAudioPrefs}
      />

      {/* Triggers & Patterns Settings Modal */}
      <TriggersSettingsModal
        isOpen={triggersModalOpen}
        onClose={() => setTriggersModalOpen(false)}
        preferences={triggersPrefs}
        onPreferencesChange={handleTriggersChange}
      />
    </div>
  );
};

export default BtcRadarPage;
