import React, { useCallback, useEffect, useState } from 'react';
import {
  Activity,
  AlertOctagon,
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  DollarSign,
  Layers,
  Loader2,
  Play,
  Power,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  Sliders,
  Square,
  Target,
  Trophy,
  Wallet,
  Wifi,
  WifiOff,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { ChannelChart } from '../components/ChannelChart';
import { EventFeed } from '../components/EventFeed';
import { KillSwitchModal } from '../components/KillSwitchModal';
import { KpiCards } from '../components/KpiCards';
import { useAuth } from '../hooks/useAuth';
import { useLiveSocket } from '../hooks/useLiveSocket';
import { ApiError, botApi, marketApi, reportsApi } from '../lib/api';
import { formatDuration, formatPrice, formatUsd } from '../lib/format';
import type {
  ArmedOrderInfo,
  BasketItem,
  BotConfigData,
  BotEventLogItem,
  ChannelData,
  ChartCandle,
  ContractItem,
  DerivAccount,
  ReportsSummaryResponse,
  StrategyMode,
  SyntraTelemetry,
  SyntraTradeItem,
  WsDashboardMessage,
} from '../types/api';

const QUICK_ASSETS = [
  { sym: 'JD25', name: 'Jump 25 Index', badge: 'Ativo Vídeo Syntra' },
  { sym: 'RDBULL', name: 'Bull Market Index', badge: 'Ativo Vídeo Syntra' },
  { sym: '1HZ100V', name: 'Vol 100 (1s)', badge: 'Sintético 1Hz' },
  { sym: 'cryBTCUSD', name: 'BTC/USD', badge: 'Cripto' },
];

export const Dashboard: React.FC = () => {
  const { user } = useAuth();

  const [config, setConfig] = useState<BotConfigData | null>(null);
  const [activeAccount, setActiveAccount] = useState<DerivAccount | null>(null);
  const [openBasket, setOpenBasket] = useState<BasketItem | null>(null);
  const [openContracts, setOpenContracts] = useState<ContractItem[]>([]);
  const [events, setEvents] = useState<BotEventLogItem[]>([]);
  const [summary, setSummary] = useState<ReportsSummaryResponse | null>(null);

  // Active chart symbol (default JD25 or config symbol)
  const [activeSymbol, setActiveSymbol] = useState<string>('JD25');
  const [candles, setCandles] = useState<ChartCandle[]>([]);
  const [channel, setChannel] = useState<ChannelData | null>(null);
  const [liveTick, setLiveTick] = useState<{ price: number; time: number } | null>(null);
  const [armedOrder, setArmedOrder] = useState<ArmedOrderInfo | null>(null);

  // Quick configuration inputs
  const [stakeInput, setStakeInput] = useState<number>(0.60);
  const [targetInput, setTargetInput] = useState<number>(50.0);
  const [lossInput, setLossInput] = useState<number>(100.0);

  // Syntra Deep Analysis Telemetry
  const [syntraTelemetry, setSyntraTelemetry] = useState<SyntraTelemetry | null>(null);

  // Strategy Selector state (Default SYNTRA_TICKS)
  const [selectedStrategy, setSelectedStrategy] = useState<StrategyMode>('SYNTRA_TICKS');
  const [tickEma20, setTickEma20] = useState<number | null>(null);
  const [tickStd60, setTickStd60] = useState<number | null>(null);
  const [tickZScore, setTickZScore] = useState<number | null>(null);

  const [loadingOverview, setLoadingOverview] = useState(true);
  const [loadingChart, setLoadingChart] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [resettingSession, setResettingSession] = useState(false);
  const [savingQuickConfig, setSavingQuickConfig] = useState(false);
  const [killModalOpen, setKillModalOpen] = useState(false);
  const [banner, setBanner] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const loadOverview = useCallback(async () => {
    try {
      const data = await botApi.getConfig();
      setConfig(data.config);
      setActiveAccount(data.active_account);
      setOpenBasket(data.open_basket);
      const initialContracts = Array.isArray(data.open_basket?.contracts)
        ? data.open_basket.contracts
        : Array.isArray(data.open_contracts)
          ? data.open_contracts
          : [];
      setOpenContracts(initialContracts);
      if (Array.isArray(data.recent_events)) {
        setEvents(data.recent_events);
      }

      if (data.config) {
        setStakeInput(data.config.stake_usd || 0.60);
        setTargetInput(data.config.daily_profit_target_usd || 50.0);
        setLossInput(data.config.max_daily_loss_usd || 100.0);
        setSelectedStrategy((data.config.strategy_mode as StrategyMode) || 'SYNTRA_TICKS');
      }

      if (data.live_state) {
        if (data.live_state.syntra_telemetry) {
          setSyntraTelemetry(data.live_state.syntra_telemetry as SyntraTelemetry);
        }
        const rawE = data.live_state.tick_ema_20;
        const rawS = data.live_state.tick_std_60;
        const rawZ = data.live_state.tick_z_score;
        if (typeof rawE === 'number') setTickEma20(rawE);
        if (typeof rawS === 'number') setTickStd60(rawS);
        if (typeof rawZ === 'number') setTickZScore(rawZ);
      }

      if (data.open_basket?.symbol) {
        setActiveSymbol(data.open_basket.symbol);
      } else if (data.config?.symbol) {
        setActiveSymbol(data.config.symbol);
      }

      const accType = data.active_account?.account_type || 'DEMO';
      reportsApi
        .getSummary(accType, 30, '')
        .then((rep) => setSummary(rep))
        .catch(() => {});
    } catch (err) {
      setBanner({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Erro ao carregar estado do robô.',
      });
    } finally {
      setLoadingOverview(false);
    }
  }, []);

  const loadChart = useCallback(async (sym: string, granularity: number) => {
    if (!sym) return;
    setLoadingChart(true);
    try {
      const res = await marketApi.getChart(sym, granularity);
      setCandles(Array.isArray(res.candles) ? res.candles : []);
      setChannel(res.channel ?? null);
    } catch {
      // Keep previous candles on transient error
    } finally {
      setLoadingChart(false);
    }
  }, []);

  useEffect(() => {
    loadOverview();
  }, [loadOverview]);

  // High frequency M1 timeframe
  const currentGranularity = 60;

  // Auto-reload chart periodically and when active symbol changes
  useEffect(() => {
    if (!activeSymbol) return;
    loadChart(activeSymbol, currentGranularity);
    const interval = window.setInterval(() => {
      loadChart(activeSymbol, currentGranularity);
    }, 12000);
    return () => window.clearInterval(interval);
  }, [activeSymbol, currentGranularity, loadChart]);

  // Handle live WebSocket messages
  const handleWsMessage = useCallback(
    (msg: WsDashboardMessage) => {
      const stateObj = (msg.state && typeof msg.state === 'object' ? msg.state : null) as Record<string, unknown> | null;
      const dataObj = (msg.data && typeof msg.data === 'object' ? msg.data : null) as Record<string, unknown> | null;
      const payload = (dataObj || stateObj || msg) as Record<string, unknown>;

      // Live tick update
      if (
        msg.type === 'tick' ||
        payload.price !== undefined ||
        payload.quote !== undefined ||
        payload.spot !== undefined
      ) {
        const msgSymbol = (msg.symbol || (payload.symbol as string | undefined)) ?? '';
        const price = Number(msg.price ?? msg.quote ?? msg.spot ?? payload.price ?? payload.quote ?? payload.spot);
        const time = Number(msg.epoch ?? msg.time ?? payload.epoch ?? payload.time ?? Math.floor(Date.now() / 1000));
        if (Number.isFinite(price) && price > 0 && (!msgSymbol || msgSymbol === activeSymbol)) {
          setLiveTick({ price, time });
        }
      }

      // Live balance update
      const bal = msg.balance ?? (payload.balance as number | undefined);
      if (typeof bal === 'number' && Number.isFinite(bal)) {
        setActiveAccount((prev) => (prev ? { ...prev, balance: bal } : prev));
      }

      // Bot status / session progress update
      const enabledVal =
        msg.bot_enabled ?? (payload.bot_enabled as boolean | undefined) ?? (payload.enabled as boolean | undefined);
      const statusTxt = msg.status_text ?? (payload.status_text as string | undefined);
      const sessionProf = msg.session_profit_usd ?? (payload.session_profit_usd as number | undefined);
      const sessionCyc = msg.session_cycles_count ?? (payload.session_cycles_count as number | undefined);

      if (
        typeof enabledVal === 'boolean' ||
        typeof statusTxt === 'string' ||
        typeof sessionProf === 'number'
      ) {
        setConfig((prev) =>
          prev
            ? {
                ...prev,
                enabled: typeof enabledVal === 'boolean' ? enabledVal : prev.enabled,
                status_text: typeof statusTxt === 'string' ? statusTxt : prev.status_text,
                session_profit_usd: typeof sessionProf === 'number' ? sessionProf : prev.session_profit_usd,
                session_cycles_count: typeof sessionCyc === 'number' ? sessionCyc : prev.session_cycles_count,
              }
            : prev
        );
      }

      // Armed order update from worker state
      if (payload.armed_order !== undefined) {
        setArmedOrder((payload.armed_order as ArmedOrderInfo | null) ?? null);
      }

      // Open basket & contracts live PnL update
      if (msg.open_basket !== undefined || payload.open_basket !== undefined) {
        const nextBasket = (msg.open_basket !== undefined ? msg.open_basket : payload.open_basket) as BasketItem | null;
        setOpenBasket((prev) => {
          if (!prev && nextBasket && nextBasket.symbol) {
            setActiveSymbol(nextBasket.symbol);
          }
          if (prev && !nextBasket) {
            const accType = activeAccount?.account_type || 'DEMO';
            reportsApi.getSummary(accType, 30, '').then(setSummary).catch(() => {});
          }
          return nextBasket;
        });
        if (nextBasket && Array.isArray(nextBasket.contracts)) {
          setOpenContracts(nextBasket.contracts);
        } else if (!nextBasket) {
          setOpenContracts([]);
        }
      }

      if (Array.isArray(msg.open_contracts) || Array.isArray(payload.open_contracts)) {
        const nextContracts = (
          Array.isArray(msg.open_contracts) ? msg.open_contracts : payload.open_contracts
        ) as ContractItem[];
        setOpenContracts(nextContracts);
      }

      // Live 1Hz tick engine metrics
      const rawEma = payload.tick_ema_20 ?? stateObj?.tick_ema_20 ?? (msg as any).tick_ema_20;
      const rawStd = payload.tick_std_60 ?? stateObj?.tick_std_60 ?? (msg as any).tick_std_60;
      const rawZ = payload.tick_z_score ?? stateObj?.tick_z_score ?? (msg as any).tick_z_score;
      if (rawEma !== undefined) setTickEma20(rawEma as number | null);
      if (rawStd !== undefined) setTickStd60(rawStd as number | null);
      if (rawZ !== undefined) setTickZScore(rawZ as number | null);

      // Syntra Deep Analysis telemetry
      const synTel = payload.syntra_telemetry ?? stateObj?.syntra_telemetry ?? (msg as any).syntra_telemetry;
      if (synTel) {
        setSyntraTelemetry(synTel as SyntraTelemetry);
      }

      // Live event log push
      const incomingEvent = (msg.event ?? (msg.type === 'event' ? payload : null)) as BotEventLogItem | null;
      if (incomingEvent && typeof incomingEvent.message === 'string') {
        const normalized: BotEventLogItem = {
          id: incomingEvent.id ?? Date.now(),
          level: incomingEvent.level || 'INFO',
          symbol: incomingEvent.symbol ?? null,
          message: incomingEvent.message,
          details_json: incomingEvent.details_json ?? null,
          created_at: incomingEvent.created_at || new Date().toISOString(),
        };
        setEvents((prev) => [normalized, ...prev.slice(0, 79)]);
      }
    },
    [activeSymbol, activeAccount]
  );

  const { status: wsStatus } = useLiveSocket({
    onMessage: handleWsMessage,
  });

  const handleSelectStrategy = async (strat: StrategyMode) => {
    setSelectedStrategy(strat);
    if (!config) return;
    try {
      const res = await botApi.updateConfig({
        ...config,
        strategy_mode: strat,
        timeframe_sec: 60,
      });
      setConfig(res.config);
      setBanner({
        type: 'success',
        text: '✨ Robô Syntra (Modo Deep Analysis) configurado e pronto para operar!',
      });
    } catch {
      // keep state
    }
  };

  const handleSaveQuickConfig = async (
    newStake?: number,
    newTarget?: number,
    newLoss?: number,
    newSymbol?: string
  ) => {
    if (!config) return;
    setSavingQuickConfig(true);
    try {
      const res = await botApi.updateConfig({
        ...config,
        stake_usd: Number(newStake ?? stakeInput) || 0.60,
        daily_profit_target_usd: Number(newTarget ?? targetInput) || 50.0,
        max_daily_loss_usd: Number(newLoss ?? lossInput) || 100.0,
        symbol: newSymbol ?? config.symbol,
        timeframe_sec: 60,
        strategy_mode: 'SYNTRA_TICKS',
      });
      setConfig(res.config);
      setBanner({
        type: 'success',
        text: `Configurações do Robô Syntra salvas! Stake: $${(res.config.stake_usd || 0.60).toFixed(2)} | Meta: +$${res.config.daily_profit_target_usd} USD | Stop: -$${res.config.max_daily_loss_usd} USD`,
      });
    } catch (err) {
      setBanner({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Erro ao salvar configuração.',
      });
    } finally {
      setSavingQuickConfig(false);
    }
  };

  const handleToggleBot = async () => {
    if (!config) return;
    setToggling(true);
    setBanner(null);
    const nextState = !config.enabled;
    try {
      if (nextState) {
        // Save current quick settings on start
        await botApi.updateConfig({
          ...config,
          stake_usd: Number(stakeInput) || 0.60,
          daily_profit_target_usd: Number(targetInput) || 50.0,
          max_daily_loss_usd: Number(lossInput) || 100.0,
          symbol: activeSymbol || 'JD25',
          timeframe_sec: 60,
          strategy_mode: 'SYNTRA_TICKS',
        });
      }
      const res = await botApi.toggle(nextState);
      setConfig((prev) =>
        prev
          ? {
              ...prev,
              enabled: res.enabled ?? nextState,
              status_text: res.status_text ?? (nextState ? 'LIGADO' : 'OFF'),
            }
          : prev
      );
      setBanner({
        type: 'success',
        text: nextState
          ? '✨ Robô Syntra (Deep Analysis) INICIADO! Operando Jump 25 e Bull Market com risco fixo estrito ($0.60 por trade) até bater a Meta de $50 USD!'
          : 'Robô desligado com segurança. Nenhuma nova ordem será aberta.',
      });
    } catch (err) {
      setBanner({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Falha ao alterar estado do robô.',
      });
    } finally {
      setToggling(false);
    }
  };

  const handleResetSession = async () => {
    setResettingSession(true);
    try {
      const res = await botApi.resetSession();
      setConfig(res.config);
      setBanner({
        type: 'success',
        text: 'Sessão zerada com sucesso! Você pode ligar o robô para buscar uma nova meta.',
      });
    } catch (err) {
      setBanner({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Erro ao zerar sessão.',
      });
    } finally {
      setResettingSession(false);
    }
  };

  const handleKillSwitch = async () => {
    try {
      await botApi.killSwitch();
      setOpenBasket(null);
      setOpenContracts([]);
      setConfig((prev) => (prev ? { ...prev, enabled: false, status_text: 'KILL SWITCH' } : prev));
      setBanner({
        type: 'success',
        text: 'KILL SWITCH executado! Todos os contratos foram encerrados a mercado imediatamente.',
      });
      loadOverview();
    } catch (err) {
      setBanner({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Falha ao executar Kill Switch.',
      });
    }
  };

  const botEnabled = Boolean(config?.enabled);
  const currency = activeAccount?.currency || 'USD';
  const daysRemaining = user?.days_remaining ?? 0;

  const sessionProfit = config?.session_profit_usd || 0.0;
  const targetProfit = config?.daily_profit_target_usd || 10.0;
  const maxLoss = config?.max_daily_loss_usd || 20.0;
  const cyclesCount = config?.session_cycles_count || 0;
  const progressPct = Math.min(100, Math.max(0, (sessionProfit / (targetProfit || 1)) * 100));
  const isTargetHit = sessionProfit >= targetProfit && targetProfit > 0;
  const isLossHit = sessionProfit <= -maxLoss && maxLoss > 0;

  return (
    <div className="space-y-5">
      {/* TOP STATUS BAR */}
      <div className="glass-card p-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex flex-wrap items-center gap-3">
            {/* Status Pill */}
            <div
              className={`inline-flex items-center gap-2 rounded-xl border px-3 py-2 text-xs font-bold tracking-wide ${
                isTargetHit
                  ? 'border-emerald-500 bg-emerald-500/20 text-emerald-300'
                  : isLossHit
                    ? 'border-rose-500 bg-rose-500/20 text-rose-300'
                    : botEnabled
                      ? 'border-emerald-500/50 bg-emerald-500/15 text-emerald-300 shadow-glow'
                      : 'border-slate-800 bg-slate-900/80 text-slate-400'
              }`}
            >
              <span
                className={`h-2.5 w-2.5 rounded-full ${
                  isTargetHit
                    ? 'bg-emerald-400 animate-bounce'
                    : botEnabled
                      ? 'bg-emerald-400 animate-pulse'
                      : 'bg-slate-500'
                }`}
              />
              <span className="uppercase font-mono">
                {isTargetHit
                  ? 'META ATINGIDA 🎯'
                  : isLossHit
                    ? 'STOP LOSS 🛑'
                    : botEnabled
                      ? 'LIGADO • MODO DEEP ANALYSIS'
                      : config?.status_text || 'DESLIGADO'}
              </span>
            </div>

            {/* Strategy Mode Badge */}
            <span className="rounded-xl border border-cyan-500/40 bg-cyan-500/15 px-3 py-2 font-mono text-xs font-bold text-cyan-300 shadow-glow flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-cyan-400 animate-ping" />
              ✨ MODO DEEP ANALYSIS
            </span>

            {/* Active Account Pill */}
            {activeAccount ? (
              <div className="inline-flex items-center gap-2 rounded-xl border border-slate-800 bg-slate-950/70 px-3 py-2 text-xs">
                <span
                  className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                    activeAccount.account_type === 'DEMO'
                      ? 'bg-amber-500/20 text-amber-300'
                      : 'bg-emerald-500/20 text-emerald-300'
                  }`}
                >
                  {activeAccount.account_type}
                </span>
                <span className="font-mono font-bold text-slate-200">
                  {formatUsd(activeAccount.balance, activeAccount.currency)}
                </span>
              </div>
            ) : (
              <Link
                to="/deriv-connect"
                className="inline-flex items-center gap-1.5 rounded-xl border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs font-semibold text-amber-300 hover:bg-amber-500/20 transition-colors"
              >
                <Wallet className="h-3.5 w-3.5" />
                Conectar Conta Deriv
              </Link>
            )}

            {/* Subscription Validity */}
            <div
              className={`inline-flex items-center gap-1.5 rounded-xl border px-3 py-2 text-xs ${
                user?.is_expired
                  ? 'border-rose-500/50 bg-rose-500/10 text-rose-300 font-bold'
                  : daysRemaining <= 3
                    ? 'border-amber-500/50 bg-amber-500/10 text-amber-300'
                    : 'border-slate-800 bg-slate-950/70 text-slate-300'
              }`}
            >
              <CalendarClock className="h-3.5 w-3.5 text-slate-400" />
              <span className="font-mono">
                {user?.is_expired ? 'Expirada' : `${daysRemaining} dias`}
              </span>
            </div>

            {/* Live WebSocket Indicator */}
            <div
              className={`inline-flex items-center gap-1.5 rounded-xl border px-2.5 py-2 text-[11px] font-medium ${
                wsStatus === 'connected'
                  ? 'border-emerald-500/25 bg-emerald-500/10 text-emerald-300'
                  : 'border-slate-800 bg-slate-950/70 text-slate-400'
              }`}
            >
              {wsStatus === 'connected' ? (
                <>
                  <Wifi className="h-3.5 w-3.5 text-emerald-400" />
                  <span className="text-emerald-300">Tempo Real</span>
                </>
              ) : (
                <>
                  <WifiOff className="h-3.5 w-3.5 text-rose-400" />
                  <span className="text-rose-300">Conectando</span>
                </>
              )}
            </div>
          </div>

          {/* Quick Actions */}
          <div className="flex items-center gap-2.5">
            <button
              type="button"
              onClick={() => setKillModalOpen(true)}
              className="btn-danger py-2 px-3 text-xs"
              title="Kill Switch: encerra todas as ordens abertas a mercado"
            >
              <Power className="h-3.5 w-3.5" />
              <span>Kill Switch</span>
            </button>
          </div>
        </div>
      </div>

      {/* FEEDBACK BANNER */}
      {banner && (
        <div
          className={`flex items-start gap-2.5 rounded-xl border px-4 py-3 text-xs ${
            banner.type === 'success'
              ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
              : 'border-rose-500/40 bg-rose-500/10 text-rose-200'
          }`}
        >
          {banner.type === 'success' ? (
            <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400 mt-0.5" />
          ) : (
            <AlertTriangle className="h-4 w-4 shrink-0 text-rose-400 mt-0.5" />
          )}
          <span>{banner.text}</span>
        </div>
      )}

      {/* CELEBRATION CARD: TARGET HIT */}
      {isTargetHit && (
        <div className="rounded-2xl border border-emerald-500/60 bg-gradient-to-r from-emerald-950/90 via-slate-900 to-emerald-950/80 p-5 shadow-glow">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3.5">
              <div className="rounded-xl bg-amber-500/20 p-2.5 text-amber-400">
                <Trophy className="h-7 w-7 animate-bounce" />
              </div>
              <div>
                <h2 className="text-base font-extrabold text-emerald-200">
                  🎯 PARABÉNS! META DA SESSÃO ATINGIDA COM SUCESSO!
                </h2>
                <p className="text-xs text-slate-300">
                  O robô acumulou{' '}
                  <strong className="font-mono text-emerald-400">
                    +${sessionProfit.toFixed(2)} USD
                  </strong>{' '}
                  em {cyclesCount} ciclos e finalizou automaticamente para garantir seu ganho no bolso.
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={handleResetSession}
              disabled={resettingSession}
              className="btn-primary py-2 px-4 text-xs font-bold bg-emerald-500 hover:bg-emerald-400 text-slate-950 inline-flex items-center gap-2"
            >
              <RotateCcw className={`h-4 w-4 ${resettingSession ? 'animate-spin' : ''}`} />
              Iniciar Nova Sessão de Meta
            </button>
          </div>
        </div>
      )}

      {/* PROTECTION CARD: MAX LOSS HIT */}
      {isLossHit && (
        <div className="rounded-2xl border border-rose-500/60 bg-gradient-to-r from-rose-950/90 via-slate-900 to-rose-950/80 p-5">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3.5">
              <div className="rounded-xl bg-rose-500/20 p-2.5 text-rose-400">
                <AlertOctagon className="h-7 w-7" />
              </div>
              <div>
                <h2 className="text-base font-extrabold text-rose-200">
                  🛑 LIMITE MÁXIMO DE PERDA ATINGIDO!
                </h2>
                <p className="text-xs text-slate-300">
                  O robô desligou automaticamente para proteger o capital da sua conta. Perda na sessão:{' '}
                  <strong className="font-mono text-rose-400">
                    -${Math.abs(sessionProfit).toFixed(2)} USD
                  </strong>.
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={handleResetSession}
              disabled={resettingSession}
              className="btn-secondary py-2 px-4 text-xs font-bold inline-flex items-center gap-2"
            >
              <RotateCcw className={`h-4 w-4 ${resettingSession ? 'animate-spin' : ''}`} />
              Zerar Contador e Reiniciar
            </button>
          </div>
        </div>
      )}

      {/* HERO CONTROL CARD: SYNTRA DEEP ANALYSIS */}
      <div className="glass-card p-5 space-y-4 transition-all border-cyan-500/40 bg-slate-950/95 ring-1 ring-cyan-500/20 shadow-glow">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800/80 pb-4">
          <div className="flex items-center gap-3">
            <div className="rounded-xl p-2.5 bg-cyan-500/15 text-cyan-400">
              <Activity className="h-5 w-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-bold text-slate-100 uppercase tracking-wider">
                  ✨ Robô Syntra • Modo Deep Analysis
                </h2>
                <span className="rounded-full px-2 py-0.5 text-[10px] font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-500/30">
                  ALTA FREQUÊNCIA 1HZ • 5 TICKS RISE/FALL • RISCO FIXO
                </span>
              </div>
              <p className="text-xs text-slate-400 mt-0.5">
                Risco estritamente fixo ($0.60 por ordem). Sem taxas de multiplicador, sem DCA e sem risco de cascata. Monitora Jump 25 Index e Bull Market Index.
              </p>
              <div className="flex flex-wrap items-center gap-3 mt-1.5 font-mono text-[11px]">
                <span className="text-slate-400">
                  Z-Score:{' '}
                  <strong className={
                    (tickZScore ?? 0) <= -1.3 ? 'text-emerald-400 font-bold' : (tickZScore ?? 0) >= 1.3 ? 'text-rose-400 font-bold' : 'text-slate-200'
                  }>
                    {tickZScore !== null ? (tickZScore > 0 ? `+${tickZScore.toFixed(2)}` : tickZScore.toFixed(2)) : 'Calculando...'}
                  </strong>
                </span>
                <span className="text-slate-400">
                  EMA-20: <strong className="text-slate-200">{tickEma20 ? tickEma20.toFixed(2) : '—'}</strong>
                </span>
                <span className="text-slate-400">
                  σ60: <strong className="text-slate-200">{tickStd60 ? tickStd60.toFixed(2) : '—'}</strong>
                </span>
                <span className="text-cyan-300 font-semibold">
                  • Risco por Trade: ${(config?.stake_usd || 0.60).toFixed(2)} • Retorno Médio: +$0.55 USD (~92%)
                </span>
              </div>
            </div>
          </div>

          {/* Big Action Buttons */}
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={handleToggleBot}
              disabled={toggling || user?.is_expired}
              className={`inline-flex items-center gap-2.5 rounded-xl px-6 py-3 text-xs font-extrabold uppercase tracking-wide transition-all shadow-lg ${
                botEnabled
                  ? 'bg-rose-500 text-white hover:bg-rose-600'
                  : 'bg-blue-600 text-white hover:bg-blue-500 shadow-glow'
              }`}
            >
              {toggling ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : botEnabled ? (
                <Square className="h-4 w-4 fill-current" />
              ) : (
                <Play className="h-4 w-4 fill-current" />
              )}
              <span>
                {botEnabled ? 'Parar IA' : 'Iniciar IA'}
              </span>
            </button>

            <button
              type="button"
              onClick={handleResetSession}
              disabled={resettingSession}
              className="rounded-xl border border-slate-700 bg-slate-900/90 p-2.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100 transition-colors"
              title="Zerar Lucro e Ciclos da Sessão"
            >
              <RotateCcw className={`h-4 w-4 ${resettingSession ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        {/* Live Active Markets Widget (Matching Video) */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {/* Market 1: Jump 25 Index */}
          <div
            onClick={() => {
              setActiveSymbol('JD25');
              handleSaveQuickConfig(stakeInput, targetInput, lossInput, 'JD25');
            }}
            className={`cursor-pointer rounded-xl border p-3.5 transition-all ${
              activeSymbol === 'JD25'
                ? 'border-cyan-500/60 bg-cyan-950/20 shadow-glow'
                : 'border-slate-800/80 bg-slate-900/50 hover:border-slate-700'
            }`}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-cyan-400 animate-pulse" />
                <span className="font-bold text-xs text-slate-100">Jump 25 Index</span>
              </div>
              <span className="rounded px-1.5 py-0.5 text-[10px] font-mono bg-cyan-500/20 text-cyan-300">
                JD25 • 5 Ticks
              </span>
            </div>
            <div className="mt-2 flex items-center justify-between">
              <span className="font-mono text-base font-extrabold text-slate-200">
                {syntraTelemetry?.markets?.JD25?.spot
                  ? formatPrice(syntraTelemetry.markets.JD25.spot)
                  : (activeSymbol === 'JD25' && liveTick?.price ? formatPrice(liveTick.price) : '1,490.50')}
              </span>
              <span className={`text-[11px] font-mono font-semibold ${
                syntraTelemetry?.markets?.JD25?.has_open_trade
                  ? 'text-amber-400 animate-pulse'
                  : 'text-cyan-400'
              }`}>
                {syntraTelemetry?.markets?.JD25?.has_open_trade
                  ? '⚡ operação aberta 1.15'
                  : '🔍 buscando trades (analisando)'}
              </span>
            </div>
          </div>

          {/* Market 2: Bull Market Index */}
          <div
            onClick={() => {
              setActiveSymbol('RDBULL');
              handleSaveQuickConfig(stakeInput, targetInput, lossInput, 'RDBULL');
            }}
            className={`cursor-pointer rounded-xl border p-3.5 transition-all ${
              activeSymbol === 'RDBULL'
                ? 'border-cyan-500/60 bg-cyan-950/20 shadow-glow'
                : 'border-slate-800/80 bg-slate-900/50 hover:border-slate-700'
            }`}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                <span className="font-bold text-xs text-slate-100">Bull Market Index</span>
              </div>
              <span className="rounded px-1.5 py-0.5 text-[10px] font-mono bg-emerald-500/20 text-emerald-300">
                RDBULL • 5 Ticks
              </span>
            </div>
            <div className="mt-2 flex items-center justify-between">
              <span className="font-mono text-base font-extrabold text-slate-200">
                {syntraTelemetry?.markets?.RDBULL?.spot
                  ? formatPrice(syntraTelemetry.markets.RDBULL.spot)
                  : (activeSymbol === 'RDBULL' && liveTick?.price ? formatPrice(liveTick.price) : '8,620.10')}
              </span>
              <span className={`text-[11px] font-mono font-semibold ${
                syntraTelemetry?.markets?.RDBULL?.has_open_trade
                  ? 'text-amber-400 animate-pulse'
                  : 'text-emerald-400'
              }`}>
                {syntraTelemetry?.markets?.RDBULL?.has_open_trade
                  ? '⚡ operação aberta 1.14'
                  : '🔍 buscando trades (analisando)'}
              </span>
            </div>
          </div>
        </div>

        {/* 3 Simple Configuration Inputs */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {/* Input 1: Valor de Entrada */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3.5 space-y-1.5">
            <label className="flex items-center justify-between text-xs font-semibold text-slate-300">
              <span className="flex items-center gap-1.5">
                <DollarSign className="h-3.5 w-3.5 text-cyan-400" />
                Valor de Entrada (Stake)
              </span>
              <span className="text-[10px] text-cyan-400 font-bold">Risco Fixo Estrito</span>
            </label>
            <div className="relative">
              <span className="absolute left-3 top-2 text-xs font-mono text-slate-500">$</span>
              <input
                type="number"
                min="0.60"
                step="0.10"
                disabled={botEnabled}
                value={stakeInput}
                onChange={(e) => setStakeInput(parseFloat(e.target.value) || 0.60)}
                onBlur={() => handleSaveQuickConfig(stakeInput, targetInput, lossInput)}
                className="w-full rounded-lg border border-slate-700 bg-slate-950 py-1.5 pl-7 pr-3 font-mono text-sm font-bold text-slate-100 focus:border-cyan-500 focus:outline-none"
              />
            </div>
            <p className="text-[10px] text-slate-500">Valor fixo por ordem (exatamente como no vídeo: $0.60).</p>
          </div>

          {/* Input 2: Meta de Lucro */}
          <div className="rounded-xl border border-emerald-500/30 bg-emerald-950/20 p-3.5 space-y-1.5">
            <label className="flex items-center justify-between text-xs font-semibold text-emerald-300">
              <span className="flex items-center gap-1.5">
                <Target className="h-3.5 w-3.5 text-emerald-400" />
                Meta de Lucro (Target)
              </span>
              <span className="text-[10px] text-emerald-400/80 font-bold">Para no Lucro 🎯</span>
            </label>
            <div className="relative">
              <span className="absolute left-3 top-2 text-xs font-mono text-emerald-500">+$</span>
              <input
                type="number"
                min="1"
                step="1"
                disabled={botEnabled}
                value={targetInput}
                onChange={(e) => setTargetInput(parseFloat(e.target.value) || 50.0)}
                onBlur={() => handleSaveQuickConfig(stakeInput, targetInput, lossInput)}
                className="w-full rounded-lg border border-emerald-500/40 bg-slate-950 py-1.5 pl-8 pr-3 font-mono text-sm font-bold text-emerald-300 focus:border-emerald-400 focus:outline-none"
              />
            </div>
            <p className="text-[10px] text-slate-500">O robô desliga assim que atingir essa meta ($50).</p>
          </div>

          {/* Input 3: Perda Máxima */}
          <div className="rounded-xl border border-rose-500/30 bg-rose-950/20 p-3.5 space-y-1.5">
            <label className="flex items-center justify-between text-xs font-semibold text-rose-300">
              <span className="flex items-center gap-1.5">
                <ShieldAlert className="h-3.5 w-3.5 text-rose-400" />
                Perda Máxima (Stop Loss)
              </span>
              <span className="text-[10px] text-rose-400/80 font-bold">Proteção 🛑</span>
            </label>
            <div className="relative">
              <span className="absolute left-3 top-2 text-xs font-mono text-rose-500">-$</span>
              <input
                type="number"
                min="1"
                step="1"
                disabled={botEnabled}
                value={lossInput}
                onChange={(e) => setLossInput(parseFloat(e.target.value) || 100.0)}
                onBlur={() => handleSaveQuickConfig(stakeInput, targetInput, lossInput)}
                className="w-full rounded-lg border border-rose-500/40 bg-slate-950 py-1.5 pl-8 pr-3 font-mono text-sm font-bold text-rose-300 focus:border-rose-400 focus:outline-none"
              />
            </div>
            <p className="text-[10px] text-slate-500">Limite máximo de perda tolerada na sessão ($100).</p>
          </div>
        </div>

        {/* Progress Bar towards Target */}
        <div className="rounded-xl border border-slate-800 bg-slate-900/90 p-3.5 space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <div className="flex items-center gap-2">
              <span className="font-bold text-slate-200">Progresso da Meta:</span>
              <span
                className={`font-mono font-extrabold ${
                  sessionProfit >= 0 ? 'text-emerald-400' : 'text-rose-400'
                }`}
              >
                {sessionProfit >= 0
                  ? `+$${sessionProfit.toFixed(2)}`
                  : `-$${Math.abs(sessionProfit).toFixed(2)}`}
              </span>
              <span className="text-slate-500">/</span>
              <span className="font-mono font-bold text-slate-300">+${targetProfit.toFixed(2)} USD</span>
            </div>
            <div className="flex items-center gap-3 text-[11px] font-mono text-slate-400">
              <span>
                Ganhos / Perdas: <strong className="text-emerald-400">{syntraTelemetry?.total_wins ?? summary?.winning_cycles ?? 0}W</strong> / <strong className="text-rose-400">{syntraTelemetry?.total_losses ?? summary?.losing_cycles ?? 0}L</strong>
              </span>
              <span>
                Assertividade: <strong className="text-emerald-400">{(syntraTelemetry ? syntraTelemetry.win_rate : (summary?.win_rate ?? 0)).toFixed(1)}%</strong>
              </span>
            </div>
          </div>
          <div className="h-3 w-full rounded-full bg-slate-950 overflow-hidden border border-slate-800">
            <div
              className={`h-full transition-all duration-500 ${
                sessionProfit >= 0
                  ? 'bg-gradient-to-r from-emerald-600 to-emerald-400 shadow-glow'
                  : 'bg-gradient-to-r from-rose-600 to-rose-400'
              }`}
              style={{ width: `${Math.min(100, Math.max(sessionProfit > 0 ? 5 : 0, progressPct))}%` }}
            />
          </div>
        </div>
      </div>

      {/* KPI CARDS */}
      <KpiCards
        activeAccount={activeAccount}
        summary={summary}
        openBasket={openBasket}
        config={config}
        syntraTelemetry={syntraTelemetry}
      />

      {/* MAIN CHART + LIVE EVENT FEED */}
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-12">
        <div className="xl:col-span-8 space-y-5">
          <ChannelChart
            symbol={activeSymbol}
            granularity={currentGranularity}
            candles={candles}
            channel={channel}
            openBasket={openBasket}
            openContracts={openContracts}
            basketTpUsd={config?.basket_tp_usd ?? 0}
            liveTick={liveTick}
            loading={loadingChart}
            armedOrder={armedOrder}
            config={config}
            recentEvents={events}
            recentTrades={syntraTelemetry?.recent_trades || []}
            onRefresh={() => loadChart(activeSymbol, currentGranularity)}
          />

          {/* Syntra Recent Trades Table (matching video feed) */}
          {syntraTelemetry?.recent_trades && syntraTelemetry.recent_trades.length > 0 && (
            <div className="glass-card overflow-hidden">
              <div className="flex items-center justify-between border-b border-slate-800/80 px-4 py-3">
                <div className="flex items-center gap-2">
                  <Activity className="h-4 w-4 text-cyan-400" />
                  <h3 className="text-sm font-semibold text-slate-100">
                    Histórico de Operações em Ticks (Deep Analysis)
                  </h3>
                </div>
                <span className="font-mono text-xs text-slate-400">
                  Total: {syntraTelemetry.recent_trades.length} trades
                </span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="border-b border-slate-800/70 bg-slate-950/60 text-[11px] uppercase text-slate-500">
                    <tr>
                      <th className="px-4 py-2.5">Ativo</th>
                      <th className="px-4 py-2.5">Contrato</th>
                      <th className="px-4 py-2.5">Duração</th>
                      <th className="px-4 py-2.5">Saída Spot</th>
                      <th className="px-4 py-2.5 text-right">Resultado</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y border-slate-800/50 font-mono">
                    {syntraTelemetry.recent_trades.map((t, idx) => (
                      <tr key={idx} className="hover:bg-slate-800/30">
                        <td className="px-4 py-2.5 font-bold text-slate-200">
                          {t.symbol === 'JD25' ? 'Jump 25 Index' : t.symbol === 'RDBULL' ? 'Bull Market Index' : t.symbol}
                        </td>
                        <td className="px-4 py-2.5 text-slate-400">
                          #{t.contract_id}
                        </td>
                        <td className="px-4 py-2.5 text-cyan-300">
                          5 Ticks (5s)
                        </td>
                        <td className="px-4 py-2.5 text-slate-200">
                          {t.exit_spot > 0 ? formatPrice(t.exit_spot) : '—'}
                        </td>
                        <td
                          className={`px-4 py-2.5 text-right font-bold ${
                            t.is_win ? 'text-emerald-400' : 'text-rose-400'
                          }`}
                        >
                          {t.is_win ? `+$${t.profit_usd.toFixed(2)} USD` : `-$${Math.abs(t.profit_usd).toFixed(2)} USD`}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Open Basket Contracts Breakdown */}
          {openContracts.length > 0 && (
            <div className="glass-card overflow-hidden">
              <div className="flex items-center justify-between border-b border-slate-800/80 px-4 py-3">
                <div className="flex items-center gap-2">
                  <Layers className="h-4 w-4 text-cyan-400" />
                  <h3 className="text-sm font-semibold text-slate-100">
                    Contratos Abertos no Cesto ({openContracts.length})
                  </h3>
                </div>
                {openBasket && (
                  <span className="font-mono text-xs text-slate-400">
                    Tempo aberto: {formatDuration(openBasket.opened_at)}
                  </span>
                )}
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="border-b border-slate-800/70 bg-slate-950/60 text-[11px] uppercase text-slate-500">
                    <tr>
                      <th className="px-4 py-2.5">Camada</th>
                      <th className="px-4 py-2.5">Contrato Deriv</th>
                      <th className="px-4 py-2.5">Stake / Mult</th>
                      <th className="px-4 py-2.5">Entrada</th>
                      <th className="px-4 py-2.5">Stop-Out</th>
                      <th className="px-4 py-2.5">Comissão</th>
                      <th className="px-4 py-2.5 text-right">PnL Líquido</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y border-slate-800/50 font-mono">
                    {openContracts.map((c) => (
                      <tr key={c.id} className="hover:bg-slate-800/30">
                        <td className="px-4 py-2.5 font-semibold text-cyan-300">
                          #{c.layer_index}
                        </td>
                        <td className="px-4 py-2.5 text-slate-300">
                          {c.deriv_contract_id || `#${c.id}`}
                        </td>
                        <td className="px-4 py-2.5 text-slate-200">
                          {formatUsd(c.stake_usd, currency)} • {c.multiplier}x
                        </td>
                        <td className="px-4 py-2.5 text-slate-200">{formatPrice(c.entry_spot)}</td>
                        <td className="px-4 py-2.5 text-rose-300">
                          {c.stop_out_spot ? formatPrice(c.stop_out_spot) : '—'}
                        </td>
                        <td className="px-4 py-2.5 text-slate-400">
                          {formatUsd(c.commission_usd, currency)}
                        </td>
                        <td
                          className={`px-4 py-2.5 text-right font-semibold ${
                            c.current_profit_usd >= 0 ? 'text-emerald-400' : 'text-rose-400'
                          }`}
                        >
                          {formatUsd(c.current_profit_usd, currency, true)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>

        {/* Live Event Feed */}
        <div className="xl:col-span-4">
          <EventFeed events={events} maxHeightClass="max-h-[580px]" />
        </div>
      </div>

      {/* Kill Switch Modal */}
      <KillSwitchModal
        open={killModalOpen}
        onClose={() => setKillModalOpen(false)}
        onConfirm={handleKillSwitch}
        openBasket={openBasket}
        currency={currency}
      />
    </div>
  );
};
