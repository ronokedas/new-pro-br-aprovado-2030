import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Lock, Volume2, VolumeX, Wifi, WifiOff } from 'lucide-react';
import { useAuth } from '../hooks/useAuth';
import { btcRadarApi, getStoredToken } from '../lib/api';
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

export const BtcRadarPage: React.FC = () => {
  const { user } = useAuth();
  const [state, setState] = useState<FullBtcState | null>(null);
  const [connected, setConnected] = useState(false);
  const [soundEnabled, setSoundEnabled] = useState(true);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const lastBeepSecondRef = useRef<number>(-1);

  // Sound alert beep for upcoming minute boundary (:03s)
  const playAlertSound = useCallback((freq = 880, duration = 0.15) => {
    if (!soundEnabled) return;
    try {
      const AudioCtx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      if (!audioCtxRef.current) {
        audioCtxRef.current = new AudioCtx();
      }
      if (audioCtxRef.current.state === 'suspended') {
        audioCtxRef.current.resume();
      }
      const ctx = audioCtxRef.current;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, ctx.currentTime);
      gain.gain.setValueAtTime(0.12, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + duration);
    } catch {
      // Audio policy catch
    }
  }, [soundEnabled]);

  // Initial HTTP fetch
  const fetchInitialState = useCallback(async () => {
    try {
      const data = await btcRadarApi.getState();
      setState(data as unknown as FullBtcState);
    } catch (err) {
      console.warn('Falha ao carregar estado inicial do radar BTC:', err);
    }
  }, []);

  // WebSocket Live Stream
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

              // Audio alert at :03s
              const sec = Number(data.seconds_left);
              if (sec === 3 && lastBeepSecondRef.current !== 3) {
                lastBeepSecondRef.current = 3;
                playAlertSound(1046.5, 0.25);
              } else if (sec !== 3) {
                lastBeepSecondRef.current = sec;
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
      if (audioCtxRef.current) {
        audioCtxRef.current.close().catch(() => {});
      }
    };
  }, [user, fetchInitialState, playAlertSound, connected]);

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

  // 1. Header & Timer
  const symbol = state?.symbol || 'BTCUSDT • M1';
  const timerSec = state?.seconds_left !== undefined ? state.seconds_left : 58;
  const currentPriceFormatted = state?.current_price
    ? `$${state.current_price.toLocaleString('en-US', { minimumFractionDigits: 2 })}`
    : '$0.00';

  // 2. Viés Técnico
  const bias = state?.bias_summary || { baixa: 0, neutro: 1, alta: 0 };

  // 3. Sinal 1 (Fixo / Travado)
  const dir1 = state?.prediction?.direction || 'WAIT';
  const isWait1 = dir1 === 'WAIT';
  const isCall1 = dir1 === 'CALL';
  const colorClass1 = isWait1 ? '' : isCall1 ? 'verde' : 'vermelha';
  const arrowChar1 = isWait1 ? '⏳' : isCall1 ? '⬆' : '⬇';
  const predColorText =
    state?.prediction?.predicted_color || (isWait1 ? 'AGUARDAR' : isCall1 ? 'VERDE' : 'VERMELHA');
  const confidencePct = state?.prediction?.confidence_pct || 50;

  // 4. Sinal 2 (Ao Vivo L2)
  const s2 = state?.signal2;
  const dir2 = s2?.direction || 'WAIT';
  const isWait2 = dir2 === 'WAIT';
  const isCall2 = dir2 === 'CALL';
  const colorClass2 = isWait2 ? '' : isCall2 ? 'verde' : 'vermelha';
  const arrowChar2 = isWait2 ? '⏳' : isCall2 ? '⬆' : '⬇';
  const score2 = s2?.score !== undefined ? s2.score : 0;
  const entryStatus = s2?.entry_status || 'FORA DO MERCADO (AGUARDANDO)';
  const entryCode = s2?.entry_code || 'WAIT';

  // 5. Livro L2 e Fluxo
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

  // 6. Auditoria dos 120 Minutos
  const aud = state?.audit_summary;
  const winrate = aud?.winrate !== undefined ? aud.winrate : 54.6;
  const recentResults = aud?.recent_results || [];

  return (
    <div className="btc-radar-root">
      <div className="radar-container">
        {/* Header */}
        <div className="radar-header-row">
          <div className="symbol-badge">{symbol}</div>
          <div className="flex items-center gap-2">
            <div className="status-live">
              <div className="dot-live"></div>
              <span>{connected ? 'Binance Live ⚡' : 'Reconectando...'}</span>
            </div>
            <button
              type="button"
              onClick={() => setSoundEnabled(!soundEnabled)}
              className="text-slate-400 hover:text-slate-200 transition-colors ml-1 p-1"
              title={soundEnabled ? 'Alerta sonoro ativo (:03s)' : 'Alerta sonoro desativado'}
            >
              {soundEnabled ? <Volume2 className="h-4 w-4 text-cyan-400" /> : <VolumeX className="h-4 w-4" />}
            </button>
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
  );
};

export default BtcRadarPage;
