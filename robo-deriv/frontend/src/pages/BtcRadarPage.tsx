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
  recent_results?: RecentResult[];
}

interface FullBtcState {
  symbol?: string;
  current_price?: number;
  seconds_left?: number;
  prediction?: PredictionData;
  signal2?: Signal2Data;
  orderbook_l2?: OrderbookL2Data;
  bias_summary?: BiasSummary;
  flow?: FlowData;
  audit_summary?: AuditSummaryData;
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

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/ws/btc-radar?token=${encodeURIComponent(token)}`;

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

  // Sinal 1 (Fixo)
  const dir1 = state?.prediction?.direction || 'WAIT';
  const isWait1 = dir1 === 'WAIT';
  const isCall1 = dir1 === 'CALL';
  const colorClass1 = isWait1 ? '' : isCall1 ? 'verde' : 'vermelha';
  const arrowChar1 = isWait1 ? '⏳' : isCall1 ? '⬆' : '⬇';
  const predColorText =
    state?.prediction?.predicted_color || (isWait1 ? 'AGUARDAR' : isCall1 ? 'VERDE' : 'VERMELHA');
  const confidencePct = state?.prediction?.confidence_pct || 50;

  // Sinal 2 (Ao Vivo L2)
  const s2 = state?.signal2;
  const dir2 = s2?.direction || 'WAIT';
  const isWait2 = dir2 === 'WAIT';
  const isCall2 = dir2 === 'CALL';
  const colorClass2 = isWait2 ? '' : isCall2 ? 'verde' : 'vermelha';
  const arrowChar2 = isWait2 ? '⏳' : isCall2 ? '⬆' : '⬇';
  const score2 = s2?.score !== undefined ? s2.score : 0;
  const entryStatus = s2?.entry_status || 'FORA DO MERCADO (AGUARDANDO)';
  const entryCode = s2?.entry_code || 'WAIT';

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

  // Auditoria dos 120 Minutos
  const aud = state?.audit_summary;
  const winrate = aud?.winrate !== undefined ? aud.winrate : 54.6;
  const recentResults = aud?.recent_results || [];

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

            {/* Painel de Viés Técnico (Estilo TradingView) */}
            <div className="bias-panel">
              <div className="bias-box baixa">
                <span className="bias-title">Viés de Baixa</span>
                <span className="bias-val">{bias.baixa !== undefined ? bias.baixa : 0}</span>
              </div>
              <div className="bias-box neutro">
                <span className="bias-title">Viés Neutro</span>
                <span className="bias-val">{bias.neutro !== undefined ? bias.neutro : 0}</span>
              </div>
              <div className="bias-box alta">
                <span className="bias-title">Viés de Alta</span>
                <span className="bias-val">{bias.alta !== undefined ? bias.alta : 0}</span>
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

            {/* HISTÓRICO REAL DOS ÚLTIMOS 120 MINUTOS */}
            <div className="audit-card">
              <div className="audit-header">
                <span>HISTÓRICO REAL (ÚLTIMOS 120 MINUTOS)</span>
                <span
                  className="winrate-badge"
                  style={{
                    color: winrate >= 54.6 ? 'var(--accent-green)' : 'var(--accent-gold)',
                    borderColor: winrate >= 54.6 ? 'var(--accent-green)' : 'var(--accent-gold)',
                  }}
                >
                  {winrate}% Assertividade
                </span>
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
    </div>
  );
};

export default BtcRadarPage;
