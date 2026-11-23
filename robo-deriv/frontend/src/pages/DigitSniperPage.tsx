import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  AlertOctagon,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  CheckCircle2,
  ChevronRight,
  Clock,
  Crosshair,
  Flame,
  Layers,
  Percent,
  Play,
  RotateCcw,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Snowflake,
  Sparkles,
  Square,
  Trophy,
  Volume2,
  VolumeX,
  Wallet,
  Zap,
} from 'lucide-react';
import { useAuth } from '../hooks/useAuth';
import { botApi, derivApi } from '../lib/api';
import { formatUsd } from '../lib/format';

// -------------------------------------------------------------
// Tipos e Configurações de Ativos Pré-Configurados
// -------------------------------------------------------------
export interface DigitAsset {
  symbol: string;
  name: string;
  pip_size: number;
  tick_speed: string;
}

export const PRECONFIGURED_ASSETS: DigitAsset[] = [
  { symbol: 'MULTI_RADAR', name: '🎯 Scanner 5 Ativos (IA Multi-Pares)', pip_size: 2, tick_speed: 'Varredura Global' },
  { symbol: '1HZ100V', name: 'Volatility 100 (1s) Index', pip_size: 2, tick_speed: '1 tick / seg' },
  { symbol: 'R_100', name: 'Volatility 100 Index', pip_size: 2, tick_speed: '2 seg / tick' },
  { symbol: '1HZ75V', name: 'Volatility 75 (1s) Index', pip_size: 2, tick_speed: '1 tick / seg' },
  { symbol: '1HZ50V', name: 'Volatility 50 (1s) Index', pip_size: 4, tick_speed: '1 tick / seg' },
  { symbol: '1HZ25V', name: 'Volatility 25 (1s) Index', pip_size: 2, tick_speed: '1 tick / seg' },
];

export type SniperSubMode = 'MULTI_CONFLUENCE_AI' | 'OVER1_UNDER8_HFT' | 'DIGITDIFF_COLD' | 'OVER_UNDER_AUTO' | 'EVEN_ODD_SNIPER';

export interface TickRecord {
  epoch: number;
  quote: number;
  pip_size: number;
  digit: number;
  symbol: string;
}

export interface SniperTrade {
  id: string;
  time: string;
  symbol: string;
  contract_type: string;
  barrier: string;
  stake: number;
  profit: number;
  is_win: boolean;
  entry_digit?: number;
  exit_digit?: number;
  is_recovery?: boolean;
}

const round2 = (val: number): number => Math.round((val + Number.EPSILON) * 100) / 100;

export function extractLastDigit(quote: number, symbol: string, tickPipSize?: number): number {
  const asset = PRECONFIGURED_ASSETS.find((a) => a.symbol === symbol);
  const pip = typeof tickPipSize === 'number' ? tickPipSize : (asset?.pip_size ?? 2);
  const formatted = Number(quote).toFixed(pip);
  return parseInt(formatted.slice(-1), 10);
}

// -------------------------------------------------------------
// Componente Principal
// -------------------------------------------------------------
export const DigitSniperPage: React.FC = () => {
  const { user } = useAuth();

  // Contas & Conexão Deriv
  const [activeAccount, setActiveAccount] = useState<{
    token: string;
    app_id: string;
    ws_url?: string;
    is_pat?: boolean;
    loginid: string;
    account_type: 'DEMO' | 'REAL';
    currency: string;
    balance: number;
  } | null>(null);
  const [loadingToken, setLoadingToken] = useState(true);
  const [derivWsStatus, setDerivWsStatus] = useState<'connecting' | 'connected' | 'disconnected'>('disconnected');

  // Parâmetros de Configuração
  const [selectedSymbol, setSelectedSymbol] = useState<string>('MULTI_RADAR');
  const [subMode, setSubMode] = useState<SniperSubMode>('MULTI_CONFLUENCE_AI');
  const [virtualLossesRequired, setVirtualLossesRequired] = useState<number>(1);
  const [initialStake, setInitialStake] = useState<number>(0.35);
  const [splitInstallments, setSplitInstallments] = useState<number>(3);
  const [maxRecoveryStake, setMaxRecoveryStake] = useState<number>(3.50);
  const [useMarkovFilter, setUseMarkovFilter] = useState<boolean>(true);
  const [stopWin, setStopWin] = useState<number>(10.0);
  const [stopLoss, setStopLoss] = useState<number>(15.0);
  const [soundEnabled, setSoundEnabled] = useState<boolean>(true);

  // Estado da Máquina de Estados Operacional
  const [isRunning, setIsRunning] = useState<boolean>(false);
  const [stateStatusText, setStateStatusText] = useState<string>('PARADO');
  const [virtualLossCount, setVirtualLossCount] = useState<number>(0);
  const [cooldownRemaining, setCooldownRemaining] = useState<number>(0);
  const [orderInFlight, setOrderInFlight] = useState<boolean>(false);

  // Split Recovery
  const [lossPool, setLossPool] = useState<number>(0.0);
  const [remainingInstallments, setRemainingInstallments] = useState<number>(3);
  const [currentStake, setCurrentStake] = useState<number>(0.35);

  // Métricas da Sessão (Placar Individual)
  const [sessionWins, setSessionWins] = useState<number>(0);
  const [sessionLosses, setSessionLosses] = useState<number>(0);
  const [sessionProfit, setSessionProfit] = useState<number>(0.0);
  const [recentTrades, setRecentTrades] = useState<SniperTrade[]>([]);

  // Ticks & Buffer de Dígitos (Janela de 100 ticks)
  const [ticksHistory, setTicksHistory] = useState<TickRecord[]>([]);
  const [lastTick, setLastTick] = useState<TickRecord | null>(null);

  // Travas Globais Ativas
  const [lockReason, setLockReason] = useState<string | null>(null);

  // Refs de Estado Mutável para o Loop de WebSocket
  const wsRef = useRef<WebSocket | null>(null);
  const isRunningRef = useRef(isRunning);
  isRunningRef.current = isRunning;
  const orderInFlightRef = useRef(orderInFlight);
  orderInFlightRef.current = orderInFlight;
  const ticksHistoryRef = useRef<TickRecord[]>([]);
  ticksHistoryRef.current = ticksHistory;
  const cooldownRef = useRef(cooldownRemaining);
  cooldownRef.current = cooldownRemaining;
  const lossPoolRef = useRef(lossPool);
  lossPoolRef.current = lossPool;
  const remainingInstallmentsRef = useRef(remainingInstallments);
  remainingInstallmentsRef.current = remainingInstallments;
  const currentStakeRef = useRef(currentStake);
  currentStakeRef.current = currentStake;
  const sessionProfitRef = useRef(sessionProfit);
  sessionProfitRef.current = sessionProfit;
  const virtualLossCountRef = useRef(virtualLossCount);
  virtualLossCountRef.current = virtualLossCount;
  const activeSubRef = useRef<string | null>(null);
  const activeProposalSubRef = useRef<string | null>(null);
  const symbolRef = useRef(selectedSymbol);
  symbolRef.current = selectedSymbol;
  const onTickStrategyRef = useRef<(tick: TickRecord) => void>(() => {});
  const activeAccountRef = useRef(activeAccount);
  activeAccountRef.current = activeAccount;
  const prevTradesCountRef = useRef<number>(0);
  const isConfigHydratedRef = useRef(false);

  // Carrega credenciais ativas da Deriv
  const loadActiveDerivToken = useCallback(async () => {
    setLoadingToken(true);
    try {
      const data = await derivApi.getActiveToken();
      setActiveAccount(data);
    } catch (err) {
      console.warn('Erro ao carregar token ativo da Deriv:', err);
    } finally {
      setLoadingToken(false);
    }
  }, []);

  useEffect(() => {
    loadActiveDerivToken();
  }, [loadActiveDerivToken]);

  // Atualiza parâmetros de configuração no backend de forma transparente
  const handleUpdateConfig = useCallback((patch: Record<string, unknown>) => {
    botApi.updateSniperConfig(patch).catch((err) => {
      console.warn('Erro ao atualizar config do sniper no backend:', err);
    });
  }, []);

  // Síntese de Efeitos Sonoros via Web Audio API
  const playBeep = useCallback((freq = 880, duration = 0.15, type: OscillatorType = 'sine') => {
    if (!soundEnabled) return;
    try {
      const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new AudioCtx();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = type;
      osc.frequency.setValueAtTime(freq, ctx.currentTime);
      gain.gain.setValueAtTime(0.15, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + duration);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + duration);
    } catch {
      // ignore
    }
  }, [soundEnabled]);

  const playSuccessSound = useCallback(() => {
    playBeep(587.33, 0.1);
    setTimeout(() => playBeep(880, 0.15), 100);
    setTimeout(() => playBeep(1174.66, 0.25), 220);
  }, [playBeep]);

  const playLossSound = useCallback(() => {
    playBeep(330, 0.2, 'sawtooth');
    setTimeout(() => playBeep(261.63, 0.35, 'sawtooth'), 180);
  }, [playBeep]);

  const playAlertGoalSound = useCallback(() => {
    playBeep(523.25, 0.12);
    setTimeout(() => playBeep(659.25, 0.12), 120);
    setTimeout(() => playBeep(783.99, 0.15), 240);
    setTimeout(() => playBeep(1046.5, 0.35), 380);
  }, [playBeep]);

  // Carrega estado persistente do Sniper no Backend (24/7)
  const loadSniperState = useCallback(async () => {
    try {
      const state = await botApi.getSniperState();
      setIsRunning(Boolean(state.is_running));
      if (typeof state.session_wins === 'number') setSessionWins(state.session_wins);
      if (typeof state.session_losses === 'number') setSessionLosses(state.session_losses);
      if (typeof state.session_profit === 'number') setSessionProfit(state.session_profit);
      if (typeof state.loss_pool === 'number') setLossPool(state.loss_pool);
      if (typeof state.remaining_installments === 'number') setRemainingInstallments(state.remaining_installments);
      if (typeof state.current_stake === 'number') setCurrentStake(state.current_stake);
      if (typeof state.virtual_loss_count === 'number') setVirtualLossCount(state.virtual_loss_count);
      if (typeof state.cooldown === 'number') setCooldownRemaining(state.cooldown);
      if (typeof state.status_text === 'string') setStateStatusText(state.status_text);
      if (typeof state.lock_reason === 'string' || state.lock_reason === null) setLockReason(state.lock_reason);

      if (!isConfigHydratedRef.current) {
        if (state.symbol && typeof state.symbol === 'string') {
          setSelectedSymbol(state.symbol);
          symbolRef.current = state.symbol;
        }
        if (state.sub_mode && typeof state.sub_mode === 'string') {
          setSubMode(state.sub_mode as SniperSubMode);
        }
        if (typeof state.virtual_losses_required === 'number') {
          setVirtualLossesRequired(state.virtual_losses_required);
        }
        if (typeof state.initial_stake === 'number') {
          setInitialStake(state.initial_stake);
        }
        if (typeof state.split_installments === 'number') {
          setSplitInstallments(state.split_installments);
        }
        if (typeof state.max_recovery_stake === 'number') {
          setMaxRecoveryStake(state.max_recovery_stake);
        }
        if (typeof state.use_markov_filter === 'boolean') {
          setUseMarkovFilter(state.use_markov_filter);
        }
        if (typeof state.stop_win === 'number') {
          setStopWin(state.stop_win);
        }
        if (typeof state.stop_loss === 'number') {
          setStopLoss(state.stop_loss);
        }
        isConfigHydratedRef.current = true;
      }

      if (Array.isArray(state.recent_trades)) {
        // Toca som se novos trades forem detectados
        if (state.recent_trades.length > prevTradesCountRef.current && prevTradesCountRef.current > 0) {
          const newest = state.recent_trades[0];
          if (newest?.is_win) {
            playSuccessSound();
          } else {
            playLossSound();
          }
        }
        prevTradesCountRef.current = state.recent_trades.length;
        setRecentTrades(state.recent_trades);
      }

      // Sincroniza histórico de dígitos e último tick direto do backend (garantia 100% resiliente)
      if (Array.isArray(state.last_100_digits) && state.last_100_digits.length > 0) {
        setTicksHistory((prev) => {
          if (prev.length < 5) {
            const sym = symbolRef.current;
            return state.last_100_digits!.map((digit, idx) => ({
              epoch: Date.now() / 1000 - (state.last_100_digits!.length - idx),
              quote: 0,
              pip_size: 2,
              digit,
              symbol: sym,
            }));
          }
          return prev;
        });
      }
      if (state.last_tick) {
        setLastTick((prev) => {
          if (!prev || prev.digit !== state.last_tick!.digit) {
            return {
              epoch: state.last_tick!.epoch || Date.now() / 1000,
              quote: state.last_tick!.quote,
              pip_size: 2,
              digit: state.last_tick!.digit,
              symbol: state.last_tick!.symbol,
            };
          }
          return prev;
        });
      }
    } catch (err) {
      console.warn('Erro ao carregar estado do sniper:', err);
    }
  }, [playLossSound, playSuccessSound]);

  // Polling a cada 2s para sincronizar estado em background e trades
  useEffect(() => {
    loadSniperState();
    const interval = window.setInterval(loadSniperState, 2000);
    return () => clearInterval(interval);
  }, [loadSniperState]);

  // Cálculos Estatísticos da Janela de 100 Ticks
  const digitStats = useMemo(() => {
    const counts = Array(10).fill(0);
    const total = ticksHistory.length;
    ticksHistory.forEach((t) => {
      counts[t.digit] = (counts[t.digit] || 0) + 1;
    });

    const percentages = counts.map((c) => (total > 0 ? (c / total) * 100 : 10.0));
    let coldestDigit = 0;
    let hottestDigit = 0;
    let minPct = 101;
    let maxPct = -1;

    percentages.forEach((pct, digit) => {
      if (pct < minPct) {
        minPct = pct;
        coldestDigit = digit;
      }
      if (pct > maxPct) {
        maxPct = pct;
        hottestDigit = digit;
      }
    });

    // Análise de Vício nos últimos 50 ticks
    const last50 = ticksHistory.slice(-50);
    const counts50 = Array(10).fill(0);
    last50.forEach((t) => {
      counts50[t.digit] = (counts50[t.digit] || 0) + 1;
    });
    const addictedDigits = counts50
      .map((c, d) => ({ digit: d, pct: (c / (last50.length || 1)) * 100 }))
      .filter((item) => item.pct > 14.0)
      .map((item) => item.digit);

    return {
      counts,
      percentages,
      total,
      coldestDigit,
      minPct,
      hottestDigit,
      maxPct,
      zeroPct: percentages[0],
      ninePct: percentages[9],
      addictedDigits,
    };
  }, [ticksHistory]);

  // Conexão WebSocket Pública na Deriv para Ticks em Tempo Real & Gráfico de Dígitos (60 FPS)
  useEffect(() => {
    let ws: WebSocket | null = null;
    let pingTimer: number | null = null;
    let reconnectTimer: number | null = null;
    let isDisposed = false;

    const connectDerivWs = () => {
      if (isDisposed) return;
      setDerivWsStatus('connecting');

      const wsUrl = 'wss://red.derivws.com/websockets/v3?app_id=1089';

      try {
        ws = new WebSocket(wsUrl);
        wsRef.current = ws;
      } catch (err) {
        console.warn('Falha ao instanciar WebSocket Deriv:', err);
        scheduleReconnect();
        return;
      }

      ws.onopen = () => {
        if (isDisposed) {
          ws?.close();
          return;
        }
        setDerivWsStatus('connected');

        const currentSym = symbolRef.current;
        // Carga Inicial dos últimos 100 ticks para alimentar a barra de frequência de imediato
        ws?.send(
          JSON.stringify({
            ticks_history: currentSym,
            end: 'latest',
            count: 100,
            style: 'ticks',
          })
        );

        // Subscrição contínua de Ticks em tempo real
        ws?.send(JSON.stringify({ ticks: currentSym, subscribe: 1 }));

        // Heartbeat a cada 20s
        pingTimer = window.setInterval(() => {
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ ping: 1 }));
          }
        }, 20000);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);

          // 1. Carga Histórica dos 100 Ticks
          if ((data.msg_type === 'ticks_history' || data.msg_type === 'history' || data.history) && data.history) {
            const history = data.history;
            if (Array.isArray(history.prices) && Array.isArray(history.times)) {
              const currentSym = symbolRef.current;
              const pip = 2;
              const records: TickRecord[] = history.prices.map((price: number, idx: number) => ({
                epoch: Number(history.times[idx] || Date.now() / 1000),
                quote: Number(price),
                pip_size: pip,
                digit: extractLastDigit(Number(price), currentSym, pip),
                symbol: currentSym,
              }));
              setTicksHistory(records.slice(-100));
              if (records.length > 0) {
                setLastTick(records[records.length - 1]);
              }
            }
          }

          // 2. Fluxo Contínuo de Ticks em Tempo Real
          if (data.msg_type === 'tick' && data.tick) {
            const t = data.tick;
            if (t.symbol === symbolRef.current) {
              const quote = Number(t.quote);
              const pip = typeof t.pip_size === 'number' ? t.pip_size : 2;
              const digit = extractLastDigit(quote, t.symbol, pip);

              const record: TickRecord = {
                epoch: Number(t.epoch || Date.now() / 1000),
                quote,
                pip_size: pip,
                digit,
                symbol: t.symbol,
              };

              setLastTick(record);
              setTicksHistory((prev) => {
                const updated = [...prev, record];
                return updated.length > 100 ? updated.slice(-100) : updated;
              });
            }
          }
        } catch (err) {
          console.warn('Erro ao processar mensagem Deriv WS:', err);
        }
      };

      ws.onerror = () => {
        setDerivWsStatus('disconnected');
      };

      ws.onclose = () => {
        setDerivWsStatus('disconnected');
        scheduleReconnect();
      };
    };

    const scheduleReconnect = () => {
      if (isDisposed || reconnectTimer !== null) return;
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        if (!isDisposed) {
          connectDerivWs();
        }
      }, 3000);
    };

    connectDerivWs();

    return () => {
      isDisposed = true;
      if (pingTimer !== null) clearInterval(pingTimer);
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      if (ws) {
        ws.close();
      }
    };
  }, []);

  // Troca de Símbolo Ativo no WebSocket sem fechar conexão
  const handleSelectSymbol = (sym: string) => {
    if (sym === selectedSymbol) return;
    setSelectedSymbol(sym);
    symbolRef.current = sym;
    setTicksHistory([]);
    setLastTick(null);
    setVirtualLossCount(0);
    virtualLossCountRef.current = 0;

    handleUpdateConfig({ symbol: sym });

    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ forget_all: 'ticks' }));
      ws.send(
        JSON.stringify({
          ticks_history: sym,
          end: 'latest',
          count: 100,
          style: 'ticks',
        })
      );
      ws.send(JSON.stringify({ ticks: sym, subscribe: 1 }));
    }
  };

  // Alterna Bot Ligado / Desligado de forma autônoma no Servidor (24/7)
  const handleToggleBot = async () => {
    const nextState = !isRunning;
    setIsRunning(nextState);
    if (nextState) {
      setLockReason(null);
      setStateStatusText('🚀 INICIANDO MODO SOMBRA NO SERVIDOR (24/7)...');
      playBeep(880, 0.1);
    } else {
      setStateStatusText('⏸️ PAUSANDO ROBÔ NO SERVIDOR...');
    }

    try {
      const res = await botApi.toggleSniper(nextState, {
        symbol: selectedSymbol,
        sub_mode: subMode,
        virtual_losses_required: virtualLossesRequired,
        initial_stake: initialStake,
        split_installments: splitInstallments,
        max_recovery_stake: maxRecoveryStake,
        use_markov_filter: useMarkovFilter,
        stop_win: stopWin,
        stop_loss: stopLoss,
      });
      setIsRunning(Boolean(res.is_running));
      if (typeof res.status_text === 'string') setStateStatusText(res.status_text);
    } catch (err: any) {
      console.error('Falha ao alternar robô sniper no servidor:', err);
      setIsRunning(!nextState);
      alert('Falha ao comunicar com o servidor: ' + (err?.message || 'Erro de conexão'));
    }
  };

  // Zerar Placar da Sessão (Exclusivo e Individual deste Usuário)
  const handleResetSessionScoreboard = async () => {
    if (!window.confirm('Deseja zerar o placar e lucros desta sessão do Sniper de Dígitos?')) return;
    try {
      const res = await botApi.resetSniperSession();
      setSessionWins(0);
      setSessionLosses(0);
      setSessionProfit(0.0);
      sessionProfitRef.current = 0.0;
      setLossPool(0.0);
      lossPoolRef.current = 0.0;
      setRemainingInstallments(splitInstallments);
      setCurrentStake(initialStake);
      setLockReason(null);
      if (typeof res.status_text === 'string') setStateStatusText(res.status_text);
    } catch (err) {
      console.warn('Erro ao zerar placar do sniper no servidor:', err);
    }
  };

  const totalTrades = sessionWins + sessionLosses;
  const winrate = totalTrades > 0 ? (sessionWins / totalTrades) * 100 : 0.0;

  return (
    <div className="space-y-6">
      {/* 1. TOPO: Título, Status da Conexão e Botão Ligar/Desligar */}
      <div className="glass-card p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-3.5">
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-cyan-400 via-sky-500 to-indigo-600 text-slate-950 font-bold shadow-glow">
              <Crosshair className="h-6 w-6 animate-pulse" />
            </div>
            <div>
              <div className="flex items-center gap-2.5">
                <h1 className="text-lg font-black tracking-tight text-slate-100">
                  Sniper de Dígitos <span className="text-cyan-400">Deriv</span>
                </h1>
                <span className="rounded-full bg-cyan-500/15 border border-cyan-500/30 px-2.5 py-0.5 text-[11px] font-mono font-bold text-cyan-300">
                  Virtual Loss + Split Recovery
                </span>
                <span className="rounded-full bg-emerald-500/15 border border-emerald-500/30 px-2 py-0.5 text-[10px] font-mono text-emerald-300">
                  97% - 99% Ciclo
                </span>
              </div>
              <p className="text-xs text-slate-400 mt-0.5">
                Opera invisível em Modo Sombra a cada tick • Dispara ordem real de 1 tick somente após derrota estatística
              </p>
            </div>
          </div>

          {/* Badges de Conexão e Ações Rápidas */}
          <div className="flex flex-wrap items-center gap-2.5">
            {/* Status da Conta */}
            <div className="flex items-center gap-2 rounded-xl border border-slate-800 bg-slate-950/70 px-3 py-1.5 text-xs font-mono">
              <Wallet className="h-3.5 w-3.5 text-cyan-400" />
              <span className="text-slate-400">{activeAccount?.loginid || 'Desconectado'}:</span>
              <span className="font-bold text-slate-200">
                {activeAccount ? `$${activeAccount.balance.toFixed(2)} ${activeAccount.currency}` : '...'}
              </span>
              <span
                className={`text-[9px] font-bold px-1.5 py-0.2 rounded uppercase ${
                  activeAccount?.account_type === 'REAL'
                    ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30'
                    : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                }`}
              >
                {activeAccount?.account_type || 'DEMO'}
              </span>
            </div>

            {/* Status do WebSocket */}
            <div className="flex items-center gap-1.5 rounded-xl border border-slate-800 bg-slate-950/70 px-2.5 py-1.5 text-[11px] font-mono">
              <span
                className={`h-2 w-2 rounded-full ${
                  derivWsStatus === 'connected'
                    ? 'bg-emerald-400 animate-pulse'
                    : derivWsStatus === 'connecting'
                    ? 'bg-amber-400 animate-pulse'
                    : 'bg-rose-400'
                }`}
              />
              <span className="text-slate-400">Deriv WS:</span>
              <span className="text-slate-200 uppercase">{derivWsStatus}</span>
            </div>

            {/* Mudo / Som */}
            <button
              type="button"
              onClick={() => setSoundEnabled(!soundEnabled)}
              className="rounded-xl border border-slate-800 bg-slate-950/70 p-2 text-slate-400 hover:text-slate-200 hover:border-slate-700 transition-colors"
              title={soundEnabled ? 'Desativar Som' : 'Ativar Som'}
            >
              {soundEnabled ? <Volume2 className="h-4 w-4 text-cyan-400" /> : <VolumeX className="h-4 w-4" />}
            </button>

            {/* Botão LIGAR / DESLIGAR ROBÔ */}
            <button
              type="button"
              onClick={handleToggleBot}
              disabled={loadingToken || !activeAccount}
              className={`flex items-center gap-2 rounded-xl px-5 py-2.5 text-xs font-bold font-mono tracking-wider transition-all shadow-glow ${
                isRunning
                  ? 'bg-rose-500 hover:bg-rose-600 text-white shadow-rose-500/20'
                  : 'bg-emerald-500 hover:bg-emerald-400 text-slate-950 shadow-emerald-500/20'
              } disabled:opacity-50`}
            >
              {isRunning ? (
                <>
                  <Square className="h-4 w-4 fill-current" />
                  <span>DESLIGAR ROBÔ</span>
                </>
              ) : (
                <>
                  <Play className="h-4 w-4 fill-current" />
                  <span>INICIAR SNIPER</span>
                </>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Alerta de Trava de Segurança */}
      {lockReason && (
        <div className="p-3.5 rounded-xl bg-rose-500/15 border border-rose-500/30 flex items-center justify-between text-xs text-rose-300 font-mono">
          <div className="flex items-center gap-2.5">
            <ShieldAlert className="h-5 w-5 text-rose-400 shrink-0" />
            <span>{lockReason}</span>
          </div>
          <button
            type="button"
            onClick={() => setLockReason(null)}
            className="text-rose-400 hover:text-rose-200 underline text-[11px]"
          >
            Dispensar
          </button>
        </div>
      )}

      {/* 2. PLACAR DA SESSÃO & SPLIT RECOVERY TRACKER */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Card 1: Resultado Financeiro */}
        <div className="glass-card p-4 space-y-1 relative overflow-hidden">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span className="flex items-center gap-1.5 font-semibold uppercase tracking-wider">
              <Sparkles className="h-3.5 w-3.5 text-cyan-400" />
              Lucro Líquido
            </span>
            <button
              type="button"
              onClick={handleResetSessionScoreboard}
              title="Zerar Placar da Sessão"
              className="hover:text-slate-200 transition-colors"
            >
              <RotateCcw className="h-3.5 w-3.5" />
            </button>
          </div>
          <div
            className={`text-2xl font-black font-mono tracking-tight ${
              sessionProfit >= 0 ? 'text-emerald-400' : 'text-rose-400'
            }`}
          >
            {sessionProfit >= 0 ? `+${formatUsd(sessionProfit)}` : `-${formatUsd(Math.abs(sessionProfit))}`}
          </div>
          <div className="flex items-center justify-between text-[11px] font-mono text-slate-400 pt-1">
            <span>Meta: +{formatUsd(stopWin)}</span>
            <span>Stop: -{formatUsd(stopLoss)}</span>
          </div>
        </div>

        {/* Card 2: Placar & Winrate */}
        <div className="glass-card p-4 space-y-1">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span className="flex items-center gap-1.5 font-semibold uppercase tracking-wider">
              <Trophy className="h-3.5 w-3.5 text-amber-400" />
              Placar da Sessão
            </span>
            <span className="text-[10px] font-mono text-emerald-400 font-bold">{winrate.toFixed(1)}% WR</span>
          </div>
          <div className="flex items-baseline gap-2 font-mono">
            <span className="text-2xl font-black text-emerald-400">{sessionWins}W</span>
            <span className="text-slate-600 font-bold">/</span>
            <span className="text-2xl font-black text-rose-400">{sessionLosses}L</span>
            <span className="text-xs text-slate-400 ml-1">({totalTrades} ops)</span>
          </div>
          <div className="text-[11px] font-mono text-slate-400 pt-1">
            Status: <span className="text-cyan-300 font-bold">{isRunning ? 'RODANDO' : 'PAUSADO'}</span>
          </div>
        </div>

        {/* Card 3: Split Recovery (LossPool) */}
        <div className="glass-card p-4 space-y-1">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span className="flex items-center gap-1.5 font-semibold uppercase tracking-wider">
              <Layers className="h-3.5 w-3.5 text-indigo-400" />
              Fila de Recuperação
            </span>
            <span
              className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                lossPool > 0
                  ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30 animate-pulse'
                  : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
              }`}
            >
              {lossPool > 0 ? 'RECUPERANDO' : 'ZERADA'}
            </span>
          </div>
          <div className="text-2xl font-black font-mono text-slate-100">
            {lossPool > 0 ? `-${formatUsd(lossPool)}` : '$0.00'}
          </div>
          <div className="flex items-center justify-between text-[11px] font-mono text-slate-400 pt-1">
            <span>Parcelas: {lossPool > 0 ? `${remainingInstallments} restantes` : `${splitInstallments}x`}</span>
            <span>Próx. Stake: ${currentStake.toFixed(2)}</span>
          </div>
        </div>

        {/* Card 4: Monitor de Sombra (Virtual Loss Tracker) */}
        <div className="glass-card p-4 space-y-1 border-cyan-500/30">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span className="flex items-center gap-1.5 font-semibold uppercase tracking-wider">
              <Activity className="h-3.5 w-3.5 text-cyan-400" />
              Monitor de Sombra
            </span>
            <span className="text-[10px] font-mono text-cyan-300 font-bold">
              {virtualLossCount}/{virtualLossesRequired} PERDAS
            </span>
          </div>
          <div className="text-sm font-bold font-mono text-cyan-300 truncate" title={stateStatusText}>
            {orderInFlight ? (
              <span className="text-amber-400 animate-pulse flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-amber-400" />
                ORDEM EM ANDAMENTO...
              </span>
            ) : cooldownRemaining > 0 ? (
              <span className="text-sky-300">COOLDOWN ({cooldownRemaining}t)</span>
            ) : (
              stateStatusText
            )}
          </div>
          <div className="text-[11px] font-mono text-slate-400 pt-1 flex items-center justify-between">
            <span>Último Tick: {lastTick ? lastTick.quote.toFixed(lastTick.pip_size) : '---'}</span>
            <span className="text-emerald-400 font-bold">Dígito [{lastTick?.digit ?? '-'}]</span>
          </div>
        </div>
      </div>

      {/* 3. BARRA DE FREQUÊNCIA DOS 10 DÍGITOS (0 A 9) */}
      <div className="glass-card p-5 space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-300 flex items-center gap-1.5">
              <Activity className="h-3.5 w-3.5 text-cyan-400" />
              Frequência dos 10 Dígitos (Últimos 100 Ticks)
            </span>
            <span className="text-[10px] font-mono text-slate-500">
              • Ativo: <strong className="text-slate-300">{selectedSymbol}</strong>
            </span>
          </div>
          <div className="flex items-center gap-3 text-[11px] font-mono">
            <span className="flex items-center gap-1 text-emerald-400 font-bold">
              <Snowflake className="h-3 w-3" />
              Mais Frio: Dígito {digitStats.coldestDigit} ({digitStats.minPct.toFixed(1)}%)
            </span>
            <span className="flex items-center gap-1 text-rose-400 font-bold">
              <Flame className="h-3 w-3" />
              Mais Quente: Dígito {digitStats.hottestDigit} ({digitStats.maxPct.toFixed(1)}%)
            </span>
          </div>
        </div>

        {/* Grade Visual dos 10 Dígitos */}
        <div className="grid grid-cols-5 sm:grid-cols-10 gap-2.5">
          {Array.from({ length: 10 }).map((_, digit) => {
            const count = digitStats.counts[digit];
            const pct = digitStats.percentages[digit];
            const isColdest = digit === digitStats.coldestDigit;
            const isHottest = digit === digitStats.hottestDigit;
            const isCurrent = lastTick?.digit === digit;
            const isAddicted = digitStats.addictedDigits.includes(digit);

            let borderStyle = 'border-slate-800 bg-slate-950/70';
            let textTone = 'text-slate-200';
            if (isColdest) {
              borderStyle = 'border-emerald-500/50 bg-emerald-500/10 shadow-sm shadow-emerald-500/10';
              textTone = 'text-emerald-300';
            } else if (isHottest) {
              borderStyle = 'border-rose-500/50 bg-rose-500/10 shadow-sm shadow-rose-500/10';
              textTone = 'text-rose-300';
            }

            return (
              <div
                key={digit}
                className={`relative rounded-xl border p-2.5 text-center transition-all ${borderStyle} ${
                  isCurrent ? 'ring-2 ring-cyan-400 ring-offset-1 ring-offset-slate-950 scale-105' : ''
                }`}
              >
                {/* Badges de Destaque */}
                {isColdest && (
                  <span className="absolute -top-2 left-1/2 -translate-x-1/2 bg-emerald-500 text-slate-950 text-[9px] font-black px-1.5 py-0.2 rounded-full uppercase">
                    Frio
                  </span>
                )}
                {isHottest && (
                  <span className="absolute -top-2 left-1/2 -translate-x-1/2 bg-rose-500 text-white text-[9px] font-black px-1.5 py-0.2 rounded-full uppercase">
                    Quente
                  </span>
                )}

                <div className={`text-xl font-black font-mono ${textTone}`}>{digit}</div>
                <div className="text-[11px] font-mono font-bold text-slate-300">{pct.toFixed(1)}%</div>
                <div className="text-[9px] font-mono text-slate-500">{count}x</div>

                {/* Micro barra de progresso */}
                <div className="w-full bg-slate-800 h-1 rounded-full mt-1.5 overflow-hidden">
                  <div
                    className={`h-full ${isColdest ? 'bg-emerald-400' : isHottest ? 'bg-rose-400' : 'bg-cyan-500'}`}
                    style={{ width: `${Math.min(100, pct * 4)}%` }}
                  />
                </div>

                {isAddicted && (
                  <div className="text-[8px] font-mono text-rose-400 mt-0.5 font-bold uppercase">Viciado</div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* 4. PAINEL DE CONFIGURAÇÃO RÁPIDA & SELETOR DE ATIVOS */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Coluna 1 & 2: Seleção de Ativos e Parâmetros */}
        <div className="lg:col-span-2 glass-card p-5 space-y-5">
          <div className="flex items-center justify-between border-b border-slate-800/80 pb-3">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-300 flex items-center gap-1.5">
              <Shield className="h-3.5 w-3.5 text-cyan-400" />
              Parâmetros da Estratégia Sniper
            </span>
            <span className="text-[11px] font-mono text-slate-400">Ativo Padrão: 1HZ100V</span>
          </div>

          {/* Seletor de Ativos Pré-Configurados */}
          <div className="space-y-2">
            <label className="text-xs font-medium text-slate-300 block">Ativo Monitorado (Distribuição Uniforme 0-9):</label>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-2">
              {PRECONFIGURED_ASSETS.map((asset) => {
                const isSel = asset.symbol === selectedSymbol;
                return (
                  <button
                    key={asset.symbol}
                    type="button"
                    onClick={() => handleSelectSymbol(asset.symbol)}
                    className={`p-2.5 rounded-xl border text-left transition-all ${
                      isSel
                        ? 'bg-cyan-500/15 border-cyan-500/40 text-cyan-300 shadow-sm'
                        : 'bg-slate-950/70 border-slate-800 text-slate-400 hover:border-slate-700 hover:text-slate-200'
                    }`}
                  >
                    <div className="font-mono text-xs font-bold">{asset.symbol}</div>
                    <div className="text-[10px] text-slate-500 truncate">{asset.name}</div>
                    <div className="text-[9px] font-mono text-cyan-400/80 mt-1">{asset.tick_speed}</div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Sub-Modo da Estratégia */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3 pt-2">
            <button
              type="button"
              onClick={() => {
                setSubMode('MULTI_CONFLUENCE_AI');
                handleUpdateConfig({ sub_mode: 'MULTI_CONFLUENCE_AI' });
              }}
              className={`p-3 rounded-xl border text-left transition-all relative overflow-hidden ${
                subMode === 'MULTI_CONFLUENCE_AI'
                  ? 'bg-cyan-500/15 border-cyan-500/40 text-cyan-300 shadow-md shadow-cyan-500/10'
                  : 'bg-slate-950/70 border-slate-800 text-slate-400 hover:border-slate-700'
              }`}
            >
              <div className="font-bold text-xs flex items-center justify-between">
                <span className="flex items-center gap-1">
                  <Sparkles className="h-3 w-3 text-cyan-400" />
                  Sub-Modo 1: IA Confluência
                </span>
                <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-cyan-500/20 text-cyan-300 font-black">
                  ⭐ IA MASTER
                </span>
              </div>
              <p className="text-[11px] text-slate-400 mt-1">
                Varredura contínua nos 5 pares sintéticos. Dispara na melhor anomalia instantânea (Dígitos Adormecidos, Over/Under e Differs).
              </p>
            </button>

            <button
              type="button"
              onClick={() => {
                setSubMode('OVER1_UNDER8_HFT');
                handleUpdateConfig({ sub_mode: 'OVER1_UNDER8_HFT' });
              }}
              className={`p-3 rounded-xl border text-left transition-all ${
                subMode === 'OVER1_UNDER8_HFT'
                  ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-300 shadow-md shadow-emerald-500/10'
                  : 'bg-slate-950/70 border-slate-800 text-slate-400 hover:border-slate-700'
              }`}
            >
              <div className="font-bold text-xs flex items-center justify-between">
                <span>Sub-Modo 2: Over 1 / Under 8</span>
                <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-emerald-500/20 text-emerald-300 font-bold">
                  +EV (23.5% Payout)
                </span>
              </div>
              <p className="text-[11px] text-slate-400 mt-1">
                Alta frequência matemática (81-83% WR). Ganha em 8 dígitos com payout 2.5x superior, permitindo recuperação leve sem elevar stakes.
              </p>
            </button>

            <button
              type="button"
              onClick={() => {
                setSubMode('DIGITDIFF_COLD');
                handleUpdateConfig({ sub_mode: 'DIGITDIFF_COLD' });
              }}
              className={`p-3 rounded-xl border text-left transition-all ${
                subMode === 'DIGITDIFF_COLD'
                  ? 'bg-indigo-500/15 border-indigo-500/40 text-indigo-300 shadow-md shadow-indigo-500/10'
                  : 'bg-slate-950/70 border-slate-800 text-slate-400 hover:border-slate-700'
              }`}
            >
              <div className="font-bold text-xs flex items-center justify-between">
                <span>Sub-Modo 3: Differs Adormecidos</span>
                <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-indigo-500/20 text-indigo-300">~91% WR</span>
              </div>
              <p className="text-[11px] text-slate-400 mt-1">
                Caça dígitos adormecidos (≥14 ticks sem sair) ou dígitos com menor frequência (≤9%). Dispara DIFF assim que o dígito acorda.
              </p>
            </button>

            <button
              type="button"
              onClick={() => {
                setSubMode('OVER_UNDER_AUTO');
                handleUpdateConfig({ sub_mode: 'OVER_UNDER_AUTO' });
              }}
              className={`p-3 rounded-xl border text-left transition-all ${
                subMode === 'OVER_UNDER_AUTO'
                  ? 'bg-cyan-500/15 border-cyan-500/40 text-cyan-300 shadow-md shadow-cyan-500/10'
                  : 'bg-slate-950/70 border-slate-800 text-slate-400 hover:border-slate-700'
              }`}
            >
              <div className="font-bold text-xs flex items-center justify-between">
                <span>Sub-Modo 4: Over 0 / Under 9</span>
                <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-cyan-500/20 text-cyan-300">~90% WR</span>
              </div>
              <p className="text-[11px] text-slate-400 mt-1">
                Filtro adaptativo dinâmico (≤11%). Opera DIGITOVER 0 após perda virtual em 0, ou DIGITUNDER 9 após perda virtual em 9.
              </p>
            </button>
          </div>

          {/* Parâmetros Quantitativos: Stakes, Perdas Virtuais e Limites */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 pt-2">
            <div>
              <label className="text-[11px] font-mono text-slate-400 block mb-1">Perdas Virtuais:</label>
              <select
                value={virtualLossesRequired}
                onChange={(e) => {
                  const val = Number(e.target.value);
                  setVirtualLossesRequired(val);
                  handleUpdateConfig({ virtual_losses_required: val });
                }}
                className="w-full rounded-xl border border-slate-800 bg-slate-950/80 px-3 py-2 text-xs font-mono text-slate-200 focus:border-cyan-500 focus:outline-none"
              >
                <option value={1}>1 Perda (~90% real)</option>
                <option value={2}>2 Perdas (~99% ultra)</option>
                <option value={3}>3 Perdas (Extremo)</option>
              </select>
            </div>

            <div>
              <label className="text-[11px] font-mono text-slate-400 block mb-1">Stake Inicial ($):</label>
              <input
                type="number"
                min="0.35"
                step="0.05"
                value={initialStake}
                onChange={(e) => {
                  const cleaned = String(e.target.value).replace(',', '.');
                  const val = Math.max(0.35, parseFloat(cleaned) || 0.35);
                  setInitialStake(val);
                  if (lossPool === 0) setCurrentStake(val);
                  handleUpdateConfig({ initial_stake: val });
                }}
                className="w-full rounded-xl border border-slate-800 bg-slate-950/80 px-3 py-2 text-xs font-mono text-slate-200 focus:border-cyan-500 focus:outline-none"
              />
            </div>

            <div>
              <label className="text-[11px] font-mono text-slate-400 block mb-1">Parcelas Split Recovery:</label>
              <select
                value={splitInstallments}
                onChange={(e) => {
                  const val = Number(e.target.value);
                  setSplitInstallments(val);
                  handleUpdateConfig({ split_installments: val });
                }}
                className="w-full rounded-xl border border-slate-800 bg-slate-950/80 px-3 py-2 text-xs font-mono text-slate-200 focus:border-cyan-500 focus:outline-none"
              >
                <option value={2}>2 Parcelas</option>
                <option value={3}>3 Parcelas (Padrão)</option>
                <option value={4}>4 Parcelas (Conservador)</option>
                <option value={5}>5 Parcelas</option>
              </select>
            </div>

            <div>
              <label className="text-[11px] font-mono text-slate-400 block mb-1" title="Teto máximo absoluto de stake para impedir explosão de capital">
                Teto Stake Recup. ($):
              </label>
              <input
                type="number"
                min="0.50"
                step="0.50"
                value={maxRecoveryStake}
                onChange={(e) => {
                  const cleaned = String(e.target.value).replace(',', '.');
                  const val = Math.max(0.50, parseFloat(cleaned) || 3.50);
                  setMaxRecoveryStake(val);
                  handleUpdateConfig({ max_recovery_stake: val });
                }}
                className="w-full rounded-xl border border-slate-800 bg-slate-950/80 px-3 py-2 text-xs font-mono text-amber-300 focus:border-amber-500 focus:outline-none"
                title="Teto máximo para a stake na recuperação (evita Martingale perigoso)"
              />
            </div>

            <div>
              <label className="text-[11px] font-mono text-slate-400 block mb-1">Filtro Markov IA:</label>
              <button
                type="button"
                onClick={() => {
                  const nextVal = !useMarkovFilter;
                  setUseMarkovFilter(nextVal);
                  handleUpdateConfig({ use_markov_filter: nextVal });
                }}
                className={`w-full py-2 px-2.5 rounded-xl border text-xs font-mono font-bold transition-all ${
                  useMarkovFilter
                    ? 'bg-emerald-500/20 border-emerald-500/40 text-emerald-300'
                    : 'bg-slate-900 border-slate-800 text-slate-500'
                }`}
                title="Filtro que veta entradas se houver inércia de repetição de dígitos (cluster)"
              >
                {useMarkovFilter ? '🧠 ATIVO (VETO > 8%)' : 'DESATIVADO'}
              </button>
            </div>

            <div>
              <label className="text-[11px] font-mono text-slate-400 block mb-1">Stop Win / Stop Loss ($):</label>
              <div className="flex gap-1.5">
                <input
                  type="number"
                  min="1"
                  step="1"
                  value={stopWin}
                  onChange={(e) => {
                    const val = Math.max(1, parseFloat(e.target.value) || 10);
                    setStopWin(val);
                    handleUpdateConfig({ stop_win: val });
                  }}
                  className="w-1/2 rounded-xl border border-slate-800 bg-slate-950/80 px-2 py-2 text-xs font-mono text-emerald-400 focus:border-emerald-500 focus:outline-none"
                  title="Meta de Lucro (Stop Win)"
                />
                <input
                  type="number"
                  min="1"
                  step="1"
                  value={stopLoss}
                  onChange={(e) => {
                    const val = Math.max(1, parseFloat(e.target.value) || 15);
                    setStopLoss(val);
                    handleUpdateConfig({ stop_loss: val });
                  }}
                  className="w-1/2 rounded-xl border border-slate-800 bg-slate-950/80 px-2 py-2 text-xs font-mono text-rose-400 focus:border-rose-500 focus:outline-none"
                  title="Limite Máximo de Perda (Stop Loss)"
                />
              </div>
            </div>
          </div>
        </div>

        {/* Coluna 3: Regras & Travas de Segurança Operacional */}
        <div className="glass-card p-5 space-y-4">
          <div className="flex items-center gap-2 border-b border-slate-800/80 pb-3">
            <ShieldCheck className="h-4 w-4 text-emerald-400" />
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-200">
              Travas Globais Ativas
            </h3>
          </div>

          <div className="space-y-2.5 text-xs font-mono">
            <div className="flex items-start gap-2 p-2 rounded-lg bg-slate-950/60 border border-slate-800/80">
              <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />
              <div>
                <span className="font-bold text-slate-200">Anti-Martingale 11x:</span>
                <p className="text-[11px] text-slate-400">Recuperação fracionada via LossPool parcelado em {splitInstallments} vitórias.</p>
              </div>
            </div>

            <div className="flex items-start gap-2 p-2 rounded-lg bg-slate-950/60 border border-slate-800/80">
              <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />
              <div>
                <span className="font-bold text-slate-200">Retorno Obrigatório à Sombra:</span>
                <p className="text-[11px] text-slate-400">Após derrota real, aguarda 5 ticks e exige nova Perda Virtual antes de cada parcela.</p>
              </div>
            </div>

            <div className="flex items-start gap-2 p-2 rounded-lg bg-slate-950/60 border border-slate-800/80">
              <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />
              <div>
                <span className="font-bold text-slate-200">Trava de Dígito Viciado:</span>
                <p className="text-[11px] text-slate-400">Bloqueia entradas se o dígito perdedor passar de 14% nos últimos 50 ticks.</p>
              </div>
            </div>

            <div className="flex items-start gap-2 p-2 rounded-lg bg-slate-950/60 border border-slate-800/80">
              <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />
              <div>
                <span className="font-bold text-slate-200">Lock de Ordem Única:</span>
                <p className="text-[11px] text-slate-400">Garante zero ordens duplicadas aguardando a liquidação oficial na Deriv.</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 5. HISTÓRICO DAS ÚLTIMAS OPERAÇÕES DO SNIPER */}
      <div className="glass-card p-5 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Clock className="h-4 w-4 text-cyan-400" />
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-200">
              Fita de Operações do Sniper ao Vivo
            </h3>
          </div>
          <span className="text-[11px] font-mono text-slate-400">
            {recentTrades.length} operações registradas nesta sessão
          </span>
        </div>

        {recentTrades.length === 0 ? (
          <div className="text-center py-8 text-xs font-mono text-slate-500">
            Aguardando primeira operação real disparada pela máquina de Perda Virtual...
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs font-mono">
              <thead>
                <tr className="border-b border-slate-800 text-slate-400">
                  <th className="pb-2">Hora</th>
                  <th className="pb-2">Ativo</th>
                  <th className="pb-2">Contrato</th>
                  <th className="pb-2">Barreira</th>
                  <th className="pb-2">Dígito Saída</th>
                  <th className="pb-2">Stake</th>
                  <th className="pb-2">Resultado</th>
                  <th className="pb-2 text-right">Lucro/Prejuízo</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {recentTrades.map((t) => (
                  <tr key={t.id} className="hover:bg-slate-900/40 transition-colors">
                    <td className="py-2.5 text-slate-400">{t.time}</td>
                    <td className="py-2.5 font-bold text-slate-200">{t.symbol}</td>
                    <td className="py-2.5">
                      <span className="rounded px-1.5 py-0.5 text-[10px] font-bold bg-slate-800 text-cyan-300">
                        {t.contract_type}
                      </span>
                    </td>
                    <td className="py-2.5 font-bold text-slate-300">[{t.barrier}]</td>
                    <td className="py-2.5">
                      <span className="rounded px-1.5 py-0.5 font-bold bg-slate-800 text-slate-100">
                        {t.exit_digit ?? '-'}
                      </span>
                    </td>
                    <td className="py-2.5 text-slate-300">${t.stake.toFixed(2)}</td>
                    <td className="py-2.5">
                      <span
                        className={`rounded px-2 py-0.5 font-bold text-[10px] ${
                          t.is_win
                            ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                            : 'bg-rose-500/20 text-rose-300 border border-rose-500/30'
                        }`}
                      >
                        {t.is_win ? 'VITÓRIA' : 'DERROTA'}
                      </span>
                    </td>
                    <td
                      className={`py-2.5 text-right font-bold ${
                        t.profit >= 0 ? 'text-emerald-400' : 'text-rose-400'
                      }`}
                    >
                      {t.profit >= 0 ? `+${formatUsd(t.profit)}` : `-${formatUsd(Math.abs(t.profit))}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
