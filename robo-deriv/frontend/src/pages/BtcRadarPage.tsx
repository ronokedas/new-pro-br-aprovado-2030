import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Activity,
  AlertCircle,
  ArrowDownCircle,
  ArrowUpCircle,
  CheckCircle2,
  Clock,
  Compass,
  Layers,
  Lock,
  PauseCircle,
  RefreshCw,
  TrendingDown,
  TrendingUp,
  Volume2,
  VolumeX,
  Wifi,
  WifiOff,
  Zap,
} from 'lucide-react';
import { useAuth } from '../hooks/useAuth';
import { btcRadarApi, getStoredToken } from '../lib/api';
import type { BtcRadarState } from '../types/api';

export const BtcRadarPage: React.FC = () => {
  const { user } = useAuth();
  const [state, setState] = useState<BtcRadarState | null>(null);
  const [connected, setConnected] = useState(false);
  const [soundEnabled, setSoundEnabled] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date());
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const lastBeepSecondRef = useRef<number>(-1);

  // Sound alert beep for upcoming minute boundary (:03s)
  const playAlertSound = useCallback((freq = 880, duration = 0.15) => {
    if (!soundEnabled) return;
    try {
      const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
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
      // Audio autoplay policy catch
    }
  }, [soundEnabled]);

  // Initial HTTP fetch
  const fetchInitialState = useCallback(async () => {
    try {
      const data = await btcRadarApi.getState();
      setState(data);
      setLastUpdated(new Date());
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
              setState(data as BtcRadarState);
              setLastUpdated(new Date());

              // Audio alert at :03s
              const sec = Number(data.seconds_left);
              if (sec === 3 && lastBeepSecondRef.current !== 3) {
                lastBeepSecondRef.current = 3;
                playAlertSound(1046.5, 0.25); // C6 alert
              } else if (sec !== 3) {
                lastBeepSecondRef.current = sec;
              }
            }
          } catch {
            // Ignore parse errors on raw ping/pong
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

    // Fallback polling every 5s if WS disconnected
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

  const prediction = state?.prediction;
  const isCall = prediction?.direction === 'CALL';
  const isPut = prediction?.direction === 'PUT';
  const isWait = !isCall && !isPut;
  const secondsLeft = state?.seconds_left ?? 60;
  const progressPct = Math.max(0, Math.min(100, ((60 - secondsLeft) / 60) * 100));
  const orderbook = state?.orderbook_l2;
  const audit = state?.audit_summary;
  const signal2 = state?.signal2;

  // Imbalance calculations
  const bidVol = orderbook?.bid_vol ?? 0;
  const askVol = orderbook?.ask_vol ?? 0;
  const totalVol = bidVol + askVol;
  const bidPct = totalVol > 0 ? (bidVol / totalVol) * 100 : 50;
  const askPct = totalVol > 0 ? (askVol / totalVol) * 100 : 50;

  return (
    <div className="space-y-6">
      {/* Top Banner & Header */}
      <div className="glass-card p-5">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-amber-500/20 via-orange-500/20 to-yellow-500/20 border border-amber-500/30 text-amber-400 shadow-glow">
              <Compass className="h-6 w-6 animate-pulse" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-lg font-bold tracking-tight text-slate-100">
                  Radar BTC M1 • Previsor de Próxima Vela
                </h1>
                <span className="rounded bg-amber-500/15 border border-amber-500/30 px-2 py-0.5 font-mono text-[10px] font-bold text-amber-300">
                  {state?.symbol || 'BTCUSDT'}
                </span>
              </div>
              <p className="text-xs text-slate-400">
                Order Flow L2 Depth • BBO Multi-exchange • Gatilho :00s/:05s • Anti-Repaint
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            {/* Live Price Tag */}
            <div className="rounded-xl border border-slate-800 bg-slate-950/80 px-3.5 py-2">
              <span className="text-[10px] uppercase tracking-wider text-slate-400 block font-medium">
                Cotação Spot
              </span>
              <span className="font-mono text-base font-bold text-slate-100">
                {state?.current_price ? `$${state.current_price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—'}
              </span>
            </div>

            {/* Connection Status Pill */}
            <div
              className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-xs font-semibold ${
                connected
                  ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                  : 'border-amber-500/40 bg-amber-500/10 text-amber-300'
              }`}
            >
              {connected ? <Wifi className="h-4 w-4 animate-pulse" /> : <WifiOff className="h-4 w-4" />}
              <span>{connected ? 'STREAM ATIVO' : 'CONECTANDO...'}</span>
            </div>

            {/* Sound Alert Toggle */}
            <button
              type="button"
              onClick={() => setSoundEnabled(!soundEnabled)}
              className={`rounded-xl border p-2 transition-colors ${
                soundEnabled
                  ? 'border-cyan-500/40 bg-cyan-500/15 text-cyan-300'
                  : 'border-slate-800 bg-slate-900 text-slate-500 hover:text-slate-300'
              }`}
              title={soundEnabled ? 'Alerta sonoro ativo (:03s)' : 'Alerta sonoro desativado'}
            >
              {soundEnabled ? <Volume2 className="h-5 w-5" /> : <VolumeX className="h-5 w-5" />}
            </button>
          </div>
        </div>
      </div>

      {/* Grid: Big Prediction Card & Countdown Clock */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* BIG PREDICTION CARD (Takes 2 cols) */}
        <div
          className={`lg:col-span-2 glass-card p-6 border transition-all ${
            isCall
              ? 'border-emerald-500/50 bg-gradient-to-br from-emerald-950/30 via-slate-900/80 to-slate-950 shadow-lg shadow-emerald-950/30'
              : isPut
                ? 'border-rose-500/50 bg-gradient-to-br from-rose-950/30 via-slate-900/80 to-slate-950 shadow-lg shadow-rose-950/30'
                : 'border-slate-800 bg-slate-900/60'
          }`}
        >
          <div className="flex items-center justify-between border-b border-slate-800/80 pb-4 mb-5">
            <div className="flex items-center gap-2">
              <Zap className={`h-4 w-4 ${isCall ? 'text-emerald-400' : isPut ? 'text-rose-400' : 'text-slate-400'}`} />
              <span className="text-xs font-bold uppercase tracking-wider text-slate-300">
                Previsão da Cor da Próxima Vela (M1)
              </span>
            </div>
            <span className="text-[11px] font-mono text-slate-500">
              Atualizado: {lastUpdated.toLocaleTimeString()}
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 items-center">
            {/* Huge Direction Pill */}
            <div className="text-center md:text-left space-y-3">
              <div
                className={`inline-flex items-center gap-3 rounded-2xl border px-6 py-4 shadow-xl ${
                  isCall
                    ? 'border-emerald-500/60 bg-emerald-500/20 text-emerald-300 shadow-emerald-500/10'
                    : isPut
                      ? 'border-rose-500/60 bg-rose-500/20 text-rose-300 shadow-rose-500/10'
                      : 'border-slate-700 bg-slate-800/60 text-slate-300'
                }`}
              >
                {isCall && <ArrowUpCircle className="h-10 w-10 text-emerald-400 animate-bounce" />}
                {isPut && <ArrowDownCircle className="h-10 w-10 text-rose-400 animate-bounce" />}
                {isWait && <PauseCircle className="h-10 w-10 text-slate-400" />}

                <div>
                  <div className="text-3xl font-extrabold tracking-tight">
                    {isCall ? 'CALL (VERDE)' : isPut ? 'PUT (VERMELHO)' : 'AGUARDANDO'}
                  </div>
                  <div className="text-xs font-mono font-medium opacity-90 mt-0.5">
                    {prediction?.predicted_color || 'ANALISANDO FLUXO...'}
                  </div>
                </div>
              </div>

              <div className="flex flex-wrap gap-2 pt-1">
                <span className="rounded-md border border-slate-700 bg-slate-800/80 px-2.5 py-1 text-[11px] font-mono text-slate-300">
                  Origem: <strong className="text-cyan-400">{prediction?.source || 'REALTIME'}</strong>
                </span>
                <span className="rounded-md border border-slate-700 bg-slate-800/80 px-2.5 py-1 text-[11px] font-mono text-slate-300">
                  Modelo: <strong className="text-emerald-400">{prediction?.product || 'L2+GBOOST'}</strong>
                </span>
                {prediction?.lock_price && (
                  <span className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2.5 py-1 text-[11px] font-mono text-amber-300">
                    Trava @ {prediction.lock_price.toFixed(2)}
                  </span>
                )}
              </div>
            </div>

            {/* Confidence & Probability Bars */}
            <div className="space-y-4 rounded-xl border border-slate-800 bg-slate-950/60 p-4">
              <div>
                <div className="flex justify-between text-xs font-medium mb-1.5">
                  <span className="text-slate-400">Grau de Confiança da IA</span>
                  <span className="font-mono font-bold text-cyan-300">
                    {prediction?.confidence_pct ? `${prediction.confidence_pct}%` : '50%'}
                  </span>
                </div>
                <div className="h-2.5 w-full rounded-full bg-slate-800 overflow-hidden">
                  <div
                    className={`h-full transition-all duration-500 rounded-full ${
                      isCall
                        ? 'bg-gradient-to-r from-emerald-500 to-teal-400'
                        : isPut
                          ? 'bg-gradient-to-r from-rose-500 to-orange-400'
                          : 'bg-slate-600'
                    }`}
                    style={{ width: `${prediction?.confidence_pct || 50}%` }}
                  />
                </div>
              </div>

              {/* Sub-probabilities if available */}
              {(prediction?.p_green !== undefined || prediction?.p_red !== undefined) && (
                <div className="grid grid-cols-2 gap-3 pt-1">
                  <div className="rounded-lg bg-emerald-500/10 border border-emerald-500/20 p-2 text-center">
                    <span className="block text-[10px] text-emerald-400 font-medium">P(Verde / Call)</span>
                    <span className="font-mono text-sm font-bold text-emerald-300">
                      {prediction?.p_green ? `${(prediction.p_green * 100).toFixed(1)}%` : '—'}
                    </span>
                  </div>
                  <div className="rounded-lg bg-rose-500/10 border border-rose-500/20 p-2 text-center">
                    <span className="block text-[10px] text-rose-400 font-medium">P(Vermelho / Put)</span>
                    <span className="font-mono text-sm font-bold text-rose-300">
                      {prediction?.p_red ? `${(prediction.p_red * 100).toFixed(1)}%` : '—'}
                    </span>
                  </div>
                </div>
              )}

              {/* Signal 2 Secondary Confluence */}
              {signal2 && (
                <div className="border-t border-slate-800/80 pt-2 text-[11px] text-slate-400 flex items-center justify-between">
                  <span>Confluência Secundária:</span>
                  <span className="font-mono font-semibold text-slate-200">{signal2.label}</span>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* CANDLE COUNTDOWN CLOCK CARD (1 col) */}
        <div className="glass-card p-6 flex flex-col justify-between border-slate-800 bg-slate-900/60">
          <div>
            <div className="flex items-center gap-2 border-b border-slate-800/80 pb-3 mb-4">
              <Clock className="h-4 w-4 text-cyan-400" />
              <span className="text-xs font-bold uppercase tracking-wider text-slate-300">
                Fechamento Vela M1
              </span>
            </div>

            <div className="text-center py-4">
              <div
                className={`text-6xl font-black font-mono tracking-tight ${
                  secondsLeft <= 5
                    ? 'text-amber-400 animate-pulse'
                    : secondsLeft <= 10
                      ? 'text-cyan-300'
                      : 'text-slate-100'
                }`}
              >
                {secondsLeft.toString().padStart(2, '0')}s
              </div>
              <p className="text-xs text-slate-400 mt-2">
                {secondsLeft <= 5
                  ? '⚡ ZONA DE ENTRADA IMINENTE (:00s)'
                  : secondsLeft <= 15
                    ? 'Preparando entrada para o fechamento'
                    : 'Avaliando confluências de microestrutura'}
              </p>
            </div>
          </div>

          <div className="space-y-2 pt-2">
            <div className="flex justify-between text-[11px] font-mono text-slate-400">
              <span>0s</span>
              <span>{Math.round(progressPct)}%</span>
              <span>60s</span>
            </div>
            <div className="h-2 w-full rounded-full bg-slate-800 overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-cyan-500 to-emerald-400 transition-all duration-300 rounded-full"
                style={{ width: `${progressPct}%` }}
              />
            </div>
            <div className="text-[10px] text-center text-slate-500 pt-1 font-mono">
              Janela de trava do sinal: aos :05s da nova vela
            </div>
          </div>
        </div>
      </div>

      {/* Grid: Order Book L2 Depth & Microstructure Stats */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* ORDER BOOK L2 DEPTH WIDGET */}
        <div className="glass-card p-5">
          <div className="flex items-center justify-between border-b border-slate-800/80 pb-3 mb-4">
            <div className="flex items-center gap-2">
              <Layers className="h-4 w-4 text-emerald-400" />
              <span className="text-xs font-bold uppercase tracking-wider text-slate-300">
                Livro de Ofertas L2 (Profundidade & Desbalanceamento)
              </span>
            </div>
            {orderbook?.imbalance_ratio !== undefined && (
              <span className="rounded bg-cyan-500/15 border border-cyan-500/30 px-2 py-0.5 font-mono text-[11px] font-bold text-cyan-300">
                Imbalance: {(orderbook.imbalance_ratio * 100).toFixed(1)}%
              </span>
            )}
          </div>

          {/* Visual Imbalance Bar */}
          <div className="space-y-2 mb-4">
            <div className="flex justify-between text-xs font-mono">
              <span className="text-emerald-400 font-bold">COMPRA: {bidPct.toFixed(1)}%</span>
              <span className="text-rose-400 font-bold">VENDA: {askPct.toFixed(1)}%</span>
            </div>
            <div className="flex h-3 w-full rounded-full overflow-hidden bg-slate-800">
              <div className="bg-emerald-500 transition-all duration-300" style={{ width: `${bidPct}%` }} />
              <div className="bg-rose-500 transition-all duration-300" style={{ width: `${askPct}%` }} />
            </div>
            <div className="flex justify-between text-[11px] font-mono text-slate-400">
              <span>Vol Bids: {bidVol.toFixed(2)} BTC</span>
              <span>Vol Asks: {askVol.toFixed(2)} BTC</span>
            </div>
          </div>

          {/* Mini Depth Table */}
          <div className="grid grid-cols-2 gap-3 text-[11px] font-mono">
            {/* Bids column */}
            <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-3 space-y-1.5">
              <div className="flex justify-between font-bold text-emerald-400 border-b border-slate-800/80 pb-1 text-[10px] uppercase">
                <span>Preço Bid</span>
                <span>Volume</span>
              </div>
              {orderbook?.bids?.slice(0, 5).map(([px, vol], i) => (
                <div key={i} className="flex justify-between text-slate-300">
                  <span className="text-emerald-300 font-semibold">{Number(px).toFixed(2)}</span>
                  <span className="text-slate-400">{Number(vol).toFixed(3)}</span>
                </div>
              )) || (
                <div className="text-slate-500 text-center py-2">Sem dados de bids</div>
              )}
            </div>

            {/* Asks column */}
            <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-3 space-y-1.5">
              <div className="flex justify-between font-bold text-rose-400 border-b border-slate-800/80 pb-1 text-[10px] uppercase">
                <span>Preço Ask</span>
                <span>Volume</span>
              </div>
              {orderbook?.asks?.slice(0, 5).map(([px, vol], i) => (
                <div key={i} className="flex justify-between text-slate-300">
                  <span className="text-rose-300 font-semibold">{Number(px).toFixed(2)}</span>
                  <span className="text-slate-400">{Number(vol).toFixed(3)}</span>
                </div>
              )) || (
                <div className="text-slate-500 text-center py-2">Sem dados de asks</div>
              )}
            </div>
          </div>
        </div>

        {/* AUDIT SUMMARY & ANTI-REPAINT VERIFICATION */}
        <div className="glass-card p-5">
          <div className="flex items-center justify-between border-b border-slate-800/80 pb-3 mb-4">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="h-4 w-4 text-cyan-400" />
              <span className="text-xs font-bold uppercase tracking-wider text-slate-300">
                Auditoria e Assertividade Histórica (Anti-Repaint)
              </span>
            </div>
            <span className="rounded bg-emerald-500/15 border border-emerald-500/30 px-2 py-0.5 font-mono text-[11px] font-bold text-emerald-300">
              AUDITADO AO VIVO
            </span>
          </div>

          {/* Quick Metrics Cards */}
          <div className="grid grid-cols-4 gap-2 mb-4 text-center">
            <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-2.5">
              <span className="text-[10px] text-slate-400 block font-medium">Assertividade</span>
              <span className="font-mono text-lg font-bold text-emerald-400">
                {audit?.winrate !== undefined
                  ? `${(audit.winrate > 1 ? audit.winrate : audit.winrate * 100).toFixed(1)}%`
                  : '—'}
              </span>
            </div>
            <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-2.5">
              <span className="text-[10px] text-slate-400 block font-medium">Vitórias</span>
              <span className="font-mono text-lg font-bold text-emerald-300">
                {audit?.wins ?? 0}
              </span>
            </div>
            <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-2.5">
              <span className="text-[10px] text-slate-400 block font-medium">Derrotas</span>
              <span className="font-mono text-lg font-bold text-rose-300">
                {audit?.losses ?? 0}
              </span>
            </div>
            <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-2.5">
              <span className="text-[10px] text-slate-400 block font-medium">Total Velas</span>
              <span className="font-mono text-lg font-bold text-slate-200">
                {audit?.total ?? 0}
              </span>
            </div>
          </div>

          {/* Recent Candle Audit Feed */}
          <div className="rounded-xl border border-slate-800 bg-slate-950/70 overflow-hidden">
            <div className="border-b border-slate-800/80 px-3 py-2 text-[10px] uppercase font-semibold text-slate-400 flex justify-between">
              <span>Velas Recentes Verificadas</span>
              <span>Resultado Real</span>
            </div>
            <div className="divide-y divide-slate-800/50 max-h-48 overflow-y-auto">
              {audit?.recent_results && audit.recent_results.length > 0 ? (
                audit.recent_results.map((res, i) => {
                  const cTime = res.time || res.candle_time || '—';
                  const pred = res.predicted || res.prediction || '—';
                  const actual = res.actual || '—';
                  const isWin = res.result === 'WIN' || res.is_win === true;

                  return (
                    <div key={i} className="px-3 py-2 flex items-center justify-between text-xs font-mono">
                      <div className="flex items-center gap-2">
                        <span className="text-slate-400 text-[11px]">{cTime}</span>
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                            pred === 'CALL'
                              ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30'
                              : 'bg-rose-500/15 text-rose-300 border border-rose-500/30'
                          }`}
                        >
                          {pred}
                        </span>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-slate-400 text-[11px]">Real: {actual}</span>
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                            isWin
                              ? 'bg-emerald-500/20 text-emerald-300'
                              : 'bg-rose-500/20 text-rose-300'
                          }`}
                        >
                          {isWin ? 'WIN ✓' : 'LOSS ✗'}
                        </span>
                      </div>
                    </div>
                  );
                })
              ) : (
                <div className="px-3 py-6 text-center text-xs text-slate-500 font-mono">
                  Coletando histórico minuto a minuto...
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default BtcRadarPage;
