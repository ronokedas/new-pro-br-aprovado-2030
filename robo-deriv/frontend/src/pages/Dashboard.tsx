import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertOctagon,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Cpu,
  DollarSign,
  Flame,
  FlaskConical,
  Hash,
  HelpCircle,
  Layers,
  LineChart,
  Loader2,
  Lock,
  Play,
  Power,
  RefreshCw,
  RotateCcw,
  Settings2,
  Shield,
  ShieldAlert,
  Sparkles,
  Square,
  Target,
  TrendingDown,
  TrendingUp,
  Trophy,
  Wallet,
  Wifi,
  WifiOff,
  Zap,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { BacktestModal } from '../components/BacktestModal';
import { KillSwitchModal } from '../components/KillSwitchModal';
import { NexusLineChart } from '../components/NexusLineChart';
import { SessionReportModal } from '../components/SessionReportModal';
import { useAuth } from '../hooks/useAuth';
import { useLiveSocket } from '../hooks/useLiveSocket';
import { ApiError, botApi, marketApi, reportsApi } from '../lib/api';
import { formatPrice, formatUsd } from '../lib/format';
import type {
  BasketItem,
  BotConfigData,
  BotEventLogItem,
  ChartCandle,
  DerivAccount,
  NexusMarketItem,
  NexusTelemetry,
  NexusTradeItem,
  ReportsSummaryResponse,
  SessionReportResponse,
  WsDashboardMessage,
} from '../types/api';

export interface MarketDefinition {
  symbol: string;
  name: string;
  desc: string;
  category: 'Cripto' | 'Forex' | 'Sintéticos';
  badge: string;
}

const AVAILABLE_MARKETS: MarketDefinition[] = [
  // Cripto (Deriv Multipliers com stop e alavancagem flexível)
  { symbol: 'cryBTCUSD', name: 'Bitcoin (BTC/USD)', desc: 'Cripto Multiplicador', category: 'Cripto', badge: 'Alta Liquidez' },
  { symbol: 'cryETHUSD', name: 'Ethereum (ETH/USD)', desc: 'Cripto Multiplicador', category: 'Cripto', badge: 'Volátil' },

  // Forex (Tendência limpa M1 e alta liquidez na Deriv)
  { symbol: 'frxEURUSD', name: 'EUR/USD', desc: 'Forex Major Multiplicador', category: 'Forex', badge: 'Tendência M1' },
  { symbol: 'frxUSDJPY', name: 'USD/JPY', desc: 'Forex Major Multiplicador', category: 'Forex', badge: 'Tendência M1' },
  { symbol: 'frxGBPUSD', name: 'GBP/USD', desc: 'Forex Major Multiplicador', category: 'Forex', badge: 'Forte Volume' },
  { symbol: 'frxAUDUSD', name: 'AUD/USD', desc: 'Forex Major Multiplicador', category: 'Forex', badge: 'Comodities' },
  { symbol: 'frxUSDCAD', name: 'USD/CAD', desc: 'Forex Major Multiplicador', category: 'Forex', badge: 'Petróleo' },
  { symbol: 'frxUSDCHF', name: 'USD/CHF', desc: 'Forex Major Multiplicador', category: 'Forex', badge: 'Refúgio' },
  { symbol: 'frxEURJPY', name: 'EUR/JPY', desc: 'Forex Cross Multiplicador', category: 'Forex', badge: 'Volatilidade' },
  { symbol: 'frxGBPJPY', name: 'GBP/JPY', desc: 'Forex Cross Multiplicador', category: 'Forex', badge: 'Forte Tendência' },
  { symbol: 'frxEURGBP', name: 'EUR/GBP', desc: 'Forex Cross Multiplicador', category: 'Forex', badge: 'Consistente' },
  { symbol: 'frxAUDJPY', name: 'AUD/JPY', desc: 'Forex Cross Multiplicador', category: 'Forex', badge: 'Momentum' },

  // Sintéticos Deriv (Disponíveis 24/7)
  { symbol: 'JD75', name: 'Jump 75 Index', desc: 'Sintético Alta Volatilidade', category: 'Sintéticos', badge: 'Top Backtest' },
  { symbol: 'JD50', name: 'Jump 50 Index', desc: 'Sintético Volatilidade Média', category: 'Sintéticos', badge: 'Moderado' },
  { symbol: 'JD25', name: 'Jump 25 Index', desc: 'Sintético Alta Volatilidade', category: 'Sintéticos', badge: 'Suave' },
  { symbol: '1HZ100V', name: 'Volatility 100 (1s)', desc: 'Ticks de 1 Segundo Frequente', category: 'Sintéticos', badge: 'Alta Freq' },
  { symbol: 'R_100', name: 'Volatility 100 Index', desc: 'Oscilações Amplas de Preço', category: 'Sintéticos', badge: 'Padrão' },
  { symbol: 'R_50', name: 'Volatility 50 Index', desc: 'Volatilidade Balanceada', category: 'Sintéticos', badge: 'Suave' },
  { symbol: 'R_25', name: 'Volatility 25 Index', desc: 'Volatilidade Baixa', category: 'Sintéticos', badge: 'Calmo' },
  { symbol: 'R_10', name: 'Volatility 10 Index', desc: 'Volatilidade Mínima', category: 'Sintéticos', badge: 'Lento' },
];

export const Dashboard: React.FC = () => {
  const { user } = useAuth();

  // Core state
  const [config, setConfig] = useState<BotConfigData | null>(null);
  const [activeAccount, setActiveAccount] = useState<DerivAccount | null>(null);
  const [summary, setSummary] = useState<ReportsSummaryResponse | null>(null);
  const [nexusTelemetry, setNexusTelemetry] = useState<NexusTelemetry | null>(null);

  // Active symbol & chart state
  const [activeSymbol, setActiveSymbol] = useState<string>('cryBTCUSD');
  const [activeTab, setActiveTab] = useState<'GRAFICO' | 'PAINEL' | 'DIGITOS'>('GRAFICO');
  const [granularity, setGranularity] = useState<number>(60); // Default to 60 = 1m (M1 Candles)
  const [candles, setCandles] = useState<ChartCandle[]>([]);
  const [liveTick, setLiveTick] = useState<{ price: number; time: number } | null>(null);
  const [ticksBySymbol, setTicksBySymbol] = useState<Record<string, { price: number; time: number }>>({});
  const [openBasket, setOpenBasket] = useState<BasketItem | null>(null);
  const [closingBasket, setClosingBasket] = useState<boolean>(false);
  const [loadingChart, setLoadingChart] = useState<boolean>(false);

  // Strategy & Risk Inputs
  const [stakeInput, setStakeInput] = useState<number>(1.00);
  const [stopWinInput, setStopWinInput] = useState<number>(50.0);
  const [stopLossInput, setStopLossInput] = useState<number>(50.0);
  const [durationInput, setDurationInput] = useState<number>(1);
  const [martingaleInput, setMartingaleInput] = useState<number>(2.15);
  const [deepAnalysis, setDeepAnalysis] = useState<boolean>(true);
  const [conservativeMode, setConservativeMode] = useState<boolean>(true);
  const [useBinanceFilter, setUseBinanceFilter] = useState<boolean>(true);
  const [cloudBackground, setCloudBackground] = useState<boolean>(true);
  const [monitoredSymbols, setMonitoredSymbols] = useState<string[]>(['cryBTCUSD']);

  // Reports & Historical State
  const [dbTrades, setDbTrades] = useState<NexusTradeItem[]>([]);
  const [sessionReport, setSessionReport] = useState<SessionReportResponse | null>(null);
  const [loadingReport, setLoadingReport] = useState<boolean>(false);
  const [showReportModal, setShowReportModal] = useState<boolean>(false);
  const [showBacktestModal, setShowBacktestModal] = useState<boolean>(false);
  const [marketFilter, setMarketFilter] = useState<'TODOS' | 'Cripto' | 'Forex' | 'Sintéticos'>('TODOS');

  // UI state
  const [settingsOpen, setSettingsOpen] = useState<boolean>(false);
  const [loadingOverview, setLoadingOverview] = useState<boolean>(true);
  const [toggling, setToggling] = useState<boolean>(false);
  const [resettingSession, setResettingSession] = useState<boolean>(false);
  const [savingConfig, setSavingConfig] = useState<boolean>(false);
  const [killModalOpen, setKillModalOpen] = useState<boolean>(false);
  const [banner, setBanner] = useState<{ type: 'success' | 'error' | 'warning'; text: string } | null>(null);

  // Live digit frequency stats (0-9)
  const [digitDistribution, setDigitDistribution] = useState<number[]>(new Array(10).fill(10));
  const [recentDigits, setRecentDigits] = useState<number[]>([]);

  // Load Session Report
  const loadSessionReport = useCallback(async () => {
    setLoadingReport(true);
    try {
      const rep = await botApi.getSessionReport();
      setSessionReport(rep);
    } catch {
      // silent
    } finally {
      setLoadingReport(false);
    }
  }, []);

  // Sound / Audio alert trigger on win / target
  const playAlertSound = useCallback((type: 'win' | 'target' | 'loss') => {
    try {
      const ctx = new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);

      if (type === 'target' || type === 'win') {
        osc.frequency.setValueAtTime(587.33, ctx.currentTime); // D5
        osc.frequency.setValueAtTime(880, ctx.currentTime + 0.1); // A5
        gain.gain.setValueAtTime(0.12, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.35);
        osc.start(ctx.currentTime);
        osc.stop(ctx.currentTime + 0.35);
      } else {
        osc.frequency.setValueAtTime(220, ctx.currentTime); // A3
        gain.gain.setValueAtTime(0.12, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.25);
        osc.start(ctx.currentTime);
        osc.stop(ctx.currentTime + 0.25);
      }
    } catch {
      // Audio not permitted or supported
    }
  }, []);

  // Load state from backend
  const loadOverview = useCallback(async () => {
    try {
      const data = await botApi.getConfig();
      setConfig(data.config);
      setActiveAccount(data.active_account);
      setOpenBasket(data.open_basket || null);

      if (data.config) {
        setStakeInput(data.config.stake_usd || 1.00);
        setStopWinInput(data.config.daily_profit_target_usd || 50.0);
        setStopLossInput(data.config.max_daily_loss_usd || 50.0);
        setDurationInput(data.config.tick_duration || 1);
        setMartingaleInput(data.config.martingale_factor || 2.15);
        setDeepAnalysis(data.config.deep_analysis_enabled !== false);
        setConservativeMode(data.config.conservative_mode_enabled !== false);
        setUseBinanceFilter(data.config.use_binance_filter !== false);

        if (Array.isArray(data.config.active_indices) && data.config.active_indices.length > 0) {
          const valid = data.config.active_indices.filter((s) => s !== 'RDBULL' && s !== 'RDBEAR');
          setMonitoredSymbols(valid.length > 0 ? valid : ['cryBTCUSD']);
        } else if (data.config.active_indices_json) {
          try {
            const parsed = JSON.parse(data.config.active_indices_json);
            if (Array.isArray(parsed) && parsed.length > 0) {
              const valid = parsed.filter((s: string) => s !== 'RDBULL' && s !== 'RDBEAR');
              setMonitoredSymbols(valid.length > 0 ? valid : ['cryBTCUSD']);
            }
          } catch {
            // fallback
          }
        }
      }

      if (data.live_state) {
        const tel = (data.live_state.nexus_telemetry || data.live_state.syntra_telemetry) as NexusTelemetry | undefined;
        if (tel) {
          setNexusTelemetry(tel);
        }
      }

      if (Array.isArray(data.recent_trades) && data.recent_trades.length > 0) {
        setDbTrades(data.recent_trades);
      }

      botApi.getSessionReport().then((r) => setSessionReport(r)).catch(() => {});

      const accType = data.active_account?.account_type || 'DEMO';
      reportsApi
        .getSummary(accType, 30, '')
        .then((rep) => setSummary(rep))
        .catch(() => {});
    } catch (err) {
      setBanner({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Erro ao sincronizar com o robô NEXUS QUANTUM AI.',
      });
    } finally {
      setLoadingOverview(false);
    }
  }, []);

  // Load Candles / Line History
  const loadChart = useCallback(async (sym: string, gran: number) => {
    if (!sym) return;
    setLoadingChart(true);
    try {
      const res = await marketApi.getChart(sym, gran === 1 ? 60 : gran);
      setCandles(Array.isArray(res.candles) ? res.candles : []);
    } catch {
      // keep existing
    } finally {
      setLoadingChart(false);
    }
  }, []);

  useEffect(() => {
    loadOverview();
  }, [loadOverview]);

  useEffect(() => {
    if (!activeSymbol) return;
    setCandles([]);
    loadChart(activeSymbol, granularity);
    const timer = window.setInterval(() => {
      loadChart(activeSymbol, granularity);
    }, 15000);
    return () => window.clearInterval(timer);
  }, [activeSymbol, granularity, loadChart]);

  // Handle WebSocket telemetry
  const handleWsMessage = useCallback(
    (msg: WsDashboardMessage) => {
      const stateObj = (msg.state && typeof msg.state === 'object' ? msg.state : null) as Record<string, unknown> | null;
      const dataObj = (msg.data && typeof msg.data === 'object' ? msg.data : null) as Record<string, unknown> | null;
      const payload = (dataObj || stateObj || msg) as Record<string, unknown>;

      // Ticks & Digits calculation
      const price = Number(msg.price ?? msg.quote ?? msg.spot ?? payload.price ?? payload.quote ?? payload.spot);
      const time = Number(msg.epoch ?? msg.time ?? payload.epoch ?? payload.time ?? Math.floor(Date.now() / 1000));
      const sym = (msg.symbol || (payload.symbol as string | undefined)) ?? '';

      if (Number.isFinite(price) && price > 0) {
        if (sym) {
          setTicksBySymbol((prev) => ({ ...prev, [sym]: { price, time } }));
        }

        if (!sym || sym === activeSymbol) {
          setLiveTick({ price, time });

          // Extract last digit for digit statistics
          const priceStr = price.toFixed(2);
          const lastChar = priceStr.charAt(priceStr.length - 1);
          const digit = parseInt(lastChar, 10);
          if (!isNaN(digit) && digit >= 0 && digit <= 9) {
            setRecentDigits((prev) => {
              const next = [digit, ...prev.slice(0, 99)];
              // calculate frequency distribution
              const counts = new Array(10).fill(0);
              for (const d of next) counts[d]++;
              const total = next.length || 1;
              setDigitDistribution(counts.map((c) => Math.round((c / total) * 100)));
              return next;
            });
          }
        }
      }

      // Live Balance
      const bal = msg.balance ?? (payload.balance as number | undefined);
      if (typeof bal === 'number' && Number.isFinite(bal)) {
        setActiveAccount((prev) => (prev ? { ...prev, balance: bal } : prev));
      }

      // Nexus Telemetry update
      const tel = (payload.nexus_telemetry ??
        payload.syntra_telemetry ??
        msg.nexus_telemetry ??
        msg.syntra_telemetry) as NexusTelemetry | null | undefined;
      if (tel) {
        setNexusTelemetry((prev) => {
          // Play sound on new completed trade
          if (prev && tel.recent_trades && prev.recent_trades) {
            if (tel.recent_trades.length > prev.recent_trades.length) {
              const newest = tel.recent_trades[0];
              if (newest) playAlertSound(newest.is_win ? 'win' : 'loss');
            }
          }
          return tel;
        });
      }

      // Session Profit & Status
      const enabledVal = msg.bot_enabled ?? (payload.bot_enabled as boolean | undefined) ?? (payload.enabled as boolean | undefined);
      const statusTxt = msg.status_text ?? (payload.status_text as string | undefined);
      const sessionProf = msg.session_profit_usd ?? (payload.session_profit_usd as number | undefined);

      if (typeof enabledVal === 'boolean' || typeof statusTxt === 'string' || typeof sessionProf === 'number') {
        setConfig((prev) =>
          prev
            ? {
                ...prev,
                enabled: typeof enabledVal === 'boolean' ? enabledVal : prev.enabled,
                status_text: typeof statusTxt === 'string' ? statusTxt : prev.status_text,
                session_profit_usd: typeof sessionProf === 'number' ? sessionProf : prev.session_profit_usd,
              }
            : prev
        );
      }

      // Open Basket real-time update
      if ('open_basket' in payload || 'open_basket' in msg) {
        const ob = (payload.open_basket ?? msg.open_basket) as BasketItem | null;
        setOpenBasket(ob || null);
      }
    },
    [activeSymbol, playAlertSound]
  );

  const { status: wsStatus } = useLiveSocket({
    onMessage: handleWsMessage,
  });

  // Save Strategy Settings
  const handleSaveSettings = async (overrides?: Partial<BotConfigData>) => {
    if (!config) return;
    setSavingConfig(true);
    try {
      const payload: Partial<BotConfigData> = {
        ...config,
        stake_usd: Number(overrides?.stake_usd ?? stakeInput) || 1.00,
        daily_profit_target_usd: Number(overrides?.daily_profit_target_usd ?? stopWinInput) || 50.0,
        max_daily_loss_usd: Number(overrides?.max_daily_loss_usd ?? stopLossInput) || 50.0,
        tick_duration: Number(overrides?.tick_duration ?? durationInput) || 1,
        martingale_factor: Number(overrides?.martingale_factor ?? martingaleInput) || 2.15,
        deep_analysis_enabled: overrides?.deep_analysis_enabled !== undefined ? overrides.deep_analysis_enabled : deepAnalysis,
        conservative_mode_enabled: overrides?.conservative_mode_enabled !== undefined ? overrides.conservative_mode_enabled : conservativeMode,
        use_binance_filter: overrides?.use_binance_filter !== undefined ? overrides.use_binance_filter : useBinanceFilter,
        active_indices: overrides?.active_indices ?? monitoredSymbols,
        active_indices_json: JSON.stringify(overrides?.active_indices ?? monitoredSymbols),
        strategy_mode: 'NEXUS_QUANTUM',
        timeframe_sec: 60,
      };

      const res = await botApi.updateConfig(payload as any);
      setConfig(res.config);
      setBanner({
        type: 'success',
        text: 'Configurações da NEXUS QUANTUM AI salvas com sucesso!',
      });
      setTimeout(() => setBanner(null), 4000);
    } catch (err) {
      setBanner({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Erro ao salvar configurações.',
      });
    } finally {
      setSavingConfig(false);
    }
  };

  // Toggle Deep Analysis
  const handleToggleDeepAnalysis = async () => {
    const nextVal = !deepAnalysis;
    setDeepAnalysis(nextVal);
    await handleSaveSettings({ deep_analysis_enabled: nextVal });
  };

  // Toggle Monitored Index
  const handleToggleSymbol = async (sym: string) => {
    let nextList = [...monitoredSymbols];
    if (nextList.includes(sym)) {
      if (nextList.length === 1) {
        setBanner({ type: 'warning', text: 'Você precisa manter pelo menos 1 índice ativo para monitoramento.' });
        return;
      }
      nextList = nextList.filter((s) => s !== sym);
    } else {
      nextList.push(sym);
    }
    setMonitoredSymbols(nextList);
    await handleSaveSettings({ active_indices: nextList });
  };

  // Toggle Start / Stop Bot
  const handleToggleBot = async () => {
    if (!config) return;
    setToggling(true);
    setBanner(null);
    const nextState = !config.enabled;

    try {
      if (nextState) {
        // Enforce current inputs on start
        await botApi.updateConfig({
          ...config,
          stake_usd: Number(stakeInput) || 1.00,
          daily_profit_target_usd: Number(stopWinInput) || 50.0,
          max_daily_loss_usd: Number(stopLossInput) || 50.0,
          tick_duration: Number(durationInput) || 1,
          martingale_factor: Number(martingaleInput) || 2.15,
          deep_analysis_enabled: deepAnalysis,
          conservative_mode_enabled: conservativeMode,
          use_binance_filter: useBinanceFilter,
          active_indices: monitoredSymbols,
          active_indices_json: JSON.stringify(monitoredSymbols),
          symbol: activeSymbol || 'cryBTCUSD',
          strategy_mode: 'NEXUS_QUANTUM',
          timeframe_sec: 60,
        } as any);
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

      if (nextState) {
        setBanner({
          type: 'success',
          text: `⚡ NEXUS QUANTUM AI INICIADO! Modo: ${
            deepAnalysis ? 'Deep Analysis (Alta Precisão)' : 'Fast Scalp (1-2 Ticks)'
          } • Meta: +$${stopWinInput.toFixed(2)} USD`,
        });
      } else {
        setBanner({
          type: 'warning',
          text: 'Robô desativado com segurança. Nenhuma nova ordem será aberta.',
        });
      }
    } catch (err) {
      setBanner({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Falha ao alterar estado do robô.',
      });
    } finally {
      setToggling(false);
    }
  };

  // Reset Session
  const handleResetSession = async () => {
    setResettingSession(true);
    try {
      const res = await botApi.resetSession();
      setConfig(res.config);
      setNexusTelemetry((prev) =>
        prev
          ? {
              ...prev,
              session_profit_usd: 0.0,
              total_wins: 0,
              total_losses: 0,
              win_rate: 0.0,
              current_stake_usd: stakeInput,
              consecutive_losses: 0,
              accumulated_loss_streak_usd: 0.0,
              is_locked_stop: false,
              lock_reason: '',
              recent_trades: [],
            }
          : prev
      );
      setBanner({
        type: 'success',
        text: 'Sessão resetada! O placar e os lucros foram zerados para uma nova meta.',
      });
    } catch (err) {
      setBanner({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Erro ao resetar sessão.',
      });
    } finally {
      setResettingSession(false);
    }
  };

  // Kill Switch
  const handleKillSwitch = async () => {
    try {
      await botApi.killSwitch();
      setConfig((prev) => (prev ? { ...prev, enabled: false, status_text: 'KILL SWITCH' } : prev));
      setBanner({
        type: 'warning',
        text: 'KILL SWITCH executado! Robô desligado e operações interrompidas.',
      });
      loadOverview();
    } catch (err) {
      setBanner({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Falha ao executar Kill Switch.',
      });
    }
  };

  // Close Open Basket / Manual Take Profit
  const handleCloseBasket = async () => {
    setClosingBasket(true);
    try {
      await botApi.closeBasket();
      setBanner({
        type: 'success',
        text: 'Ordem de encerramento enviada! A Deriv fechará a operação imediatamente no lucro.',
      });
      setTimeout(() => {
        loadOverview();
      }, 1000);
    } catch (err) {
      setBanner({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Erro ao fechar operação.',
      });
    } finally {
      setClosingBasket(false);
    }
  };

  // Computed Values
  const botEnabled = Boolean(config?.enabled);
  const currency = activeAccount?.currency || 'USD';
  const balance = activeAccount?.balance ?? 0.0;
  const sessionProfit = nexusTelemetry?.session_profit_usd ?? config?.session_profit_usd ?? 0.0;
  const targetProfit = config?.daily_profit_target_usd || stopWinInput || 50.0;
  const maxLoss = config?.max_daily_loss_usd || stopLossInput || 50.0;

  const isTargetHit = sessionProfit >= targetProfit && targetProfit > 0;
  const isLossHit = sessionProfit <= -maxLoss && maxLoss > 0;
  const isLockedStop = Boolean(nexusTelemetry?.is_locked_stop || isTargetHit || isLossHit);

  // Bankroll profit percentage
  const bankrollProfitPct = balance > 0 ? (sessionProfit / balance) * 100 : 0.0;
  const progressPct = Math.min(100, Math.max(0, (sessionProfit / (targetProfit || 1)) * 100));

  // Telemetry KPIs
  const totalWins = nexusTelemetry?.total_wins ?? summary?.winning_cycles ?? 0;
  const totalLosses = nexusTelemetry?.total_losses ?? summary?.losing_cycles ?? 0;
  const totalTrades = totalWins + totalLosses;
  const winrate = totalTrades > 0 ? (totalWins / totalTrades) * 100 : 0.0;
  const currentStake = nexusTelemetry?.current_stake_usd ?? config?.stake_usd ?? stakeInput;
  const consecutiveLosses = nexusTelemetry?.consecutive_losses ?? 0;
  const accumulatedLoss = nexusTelemetry?.accumulated_loss_streak_usd ?? 0.0;

  // Active Market info & strictly symbol-keyed tick
  const activeMarketData = nexusTelemetry?.markets?.[activeSymbol];
  const currentSymbolLiveTick =
    ticksBySymbol[activeSymbol] ||
    (activeMarketData?.spot
      ? { price: activeMarketData.spot, time: Math.floor(Date.now() / 1000) }
      : liveTick?.price && (!activeSymbol || activeSymbol === config?.symbol)
      ? liveTick
      : null);
  const activeSpot = currentSymbolLiveTick?.price || activeMarketData?.spot || 0;
  const isMarketOpenTrade = Boolean(activeMarketData?.has_open_trade);

  const getSymbolDisplayName = useCallback((sym: string) => {
    const found = AVAILABLE_MARKETS.find((m) => m.symbol === sym);
    return found ? found.name : sym;
  }, []);

  // Real-time Active Open Position detection (from openBasket or nexus telemetry)
  const activePosition = useMemo(() => {
    if (openBasket && (openBasket.status === 'OPEN' || !openBasket.closed_at)) {
      const isCall = openBasket.direction === 'MULTUP' || openBasket.direction === 'CALL';
      const entryPrice =
        openBasket.avg_entry_price ||
        (openBasket.contracts && openBasket.contracts[0]?.entry_spot) ||
        0;
      const mData = nexusTelemetry?.markets?.[openBasket.symbol];
      const curSpot =
        ticksBySymbol[openBasket.symbol]?.price ||
        mData?.spot ||
        (openBasket.symbol === activeSymbol ? activeSpot : entryPrice);
      const mult = (openBasket.contracts && openBasket.contracts[0]?.multiplier) || 100;
      const stake = openBasket.total_stake_usd || config?.stake_usd || 1.0;

      let pnl =
        typeof openBasket.current_pnl_usd === 'number' && openBasket.current_pnl_usd !== 0
          ? openBasket.current_pnl_usd
          : typeof openBasket.realized_pnl_usd === 'number' && openBasket.realized_pnl_usd !== 0
          ? openBasket.realized_pnl_usd
          : 0;

      // Real-time tick estimation if Deriv stream hasn't ticked yet or during fast movement
      if (pnl === 0 && entryPrice > 0 && curSpot > 0 && curSpot !== entryPrice) {
        const spotDiff = isCall ? curSpot - entryPrice : entryPrice - curSpot;
        pnl = Number((stake * mult * (spotDiff / entryPrice)).toFixed(2));
      }

      const pnlPct = stake > 0 ? (pnl / stake) * 100 : 0;
      return {
        symbol: openBasket.symbol,
        name: getSymbolDisplayName(openBasket.symbol),
        direction: openBasket.direction,
        isCall,
        entryPrice,
        currentSpot: curSpot,
        pnl,
        pnlPct,
        stake,
        layers: openBasket.layers_count || (openBasket.contracts ? openBasket.contracts.length : 1),
        openedAt: openBasket.opened_at,
      };
    }

    if (nexusTelemetry?.markets) {
      for (const [sym, m] of Object.entries(nexusTelemetry.markets)) {
        if (m.has_open_trade) {
          const isCall = m.direction
            ? (m.direction === 'MULTUP' || m.direction === 'CALL')
            : (m.status?.includes('MULTUP') || m.status?.includes('CALL'));
          const curSpot = ticksBySymbol[sym]?.price || m.spot || 0;
          const entryPrice = m.entry_spot || curSpot;
          const stake = typeof m.stake_usd === 'number' && m.stake_usd > 0 ? m.stake_usd : (config?.stake_usd || 1.0);
          
          let pnl = typeof m.floating_pnl === 'number' && m.floating_pnl !== 0 ? m.floating_pnl : 0;
          // Live tick estimation if Deriv telemetry pnl is 0
          if (pnl === 0 && entryPrice > 0 && curSpot > 0 && curSpot !== entryPrice) {
            const mult = 100;
            const spotDiff = isCall ? curSpot - entryPrice : entryPrice - curSpot;
            pnl = Number((stake * mult * (spotDiff / entryPrice)).toFixed(2));
          }
          const pnlPct = stake > 0 ? (pnl / stake) * 100 : 0;
          return {
            symbol: sym,
            name: getSymbolDisplayName(sym),
            direction: isCall ? 'MULTUP' : 'MULTDOWN',
            isCall,
            entryPrice,
            currentSpot: curSpot,
            pnl,
            pnlPct,
            stake,
            layers: m.basket_layers || 1,
            openedAt: new Date().toISOString(),
          };
        }
      }
    }
    return null;
  }, [
    openBasket,
    nexusTelemetry?.markets,
    ticksBySymbol,
    activeSymbol,
    activeSpot,
    getSymbolDisplayName,
    config?.stake_usd,
  ]);

  // Fallback persistent trades & symbol resolver
  const displayedTrades = useMemo(() => {
    if (nexusTelemetry?.recent_trades && nexusTelemetry.recent_trades.length > 0) {
      return nexusTelemetry.recent_trades;
    }
    return dbTrades;
  }, [nexusTelemetry?.recent_trades, dbTrades]);

  const filteredMarkets = useMemo(() => {
    if (marketFilter === 'TODOS') return AVAILABLE_MARKETS;
    return AVAILABLE_MARKETS.filter((m) => m.category === marketFilter);
  }, [marketFilter]);

  return (
    <div className="space-y-4">
      {/* FEEDBACK BANNER */}
      {banner && (
        <div
          className={`flex items-center justify-between gap-3 rounded-xl border px-4 py-3 text-xs transition-all ${
            banner.type === 'success'
              ? 'border-emerald-500/50 bg-emerald-950/60 text-emerald-200'
              : banner.type === 'error'
              ? 'border-rose-500/50 bg-rose-950/60 text-rose-200'
              : 'border-amber-500/50 bg-amber-950/60 text-amber-200'
          }`}
        >
          <div className="flex items-center gap-2">
            {banner.type === 'success' ? (
              <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0" />
            ) : banner.type === 'error' ? (
              <AlertTriangle className="h-4 w-4 text-rose-400 shrink-0" />
            ) : (
              <Flame className="h-4 w-4 text-amber-400 shrink-0" />
            )}
            <span className="font-medium">{banner.text}</span>
          </div>
          <button
            onClick={() => setBanner(null)}
            className="text-slate-400 hover:text-slate-200 text-xs px-2 py-0.5"
          >
            ✕
          </button>
        </div>
      )}

      {/* STOP LOCK ALERT: TARGET HIT */}
      {isTargetHit && (
        <div className="rounded-2xl border border-emerald-500 bg-gradient-to-r from-emerald-950 via-slate-900 to-emerald-950 p-4 shadow-glow">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="rounded-xl bg-emerald-500/20 p-2.5 text-emerald-400">
                <Trophy className="h-6 w-6 animate-bounce" />
              </div>
              <div>
                <h3 className="text-sm font-extrabold text-emerald-200 uppercase tracking-wide">
                  🎯 META DE LUCRO ATINGIDA (+${sessionProfit.toFixed(2)} USD)!
                </h3>
                <p className="text-xs text-slate-300">
                  A trava de segurança parou a IA para colocar o lucro no bolso. Inicie uma nova sessão quando desejar.
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => {
                  loadSessionReport();
                  setShowReportModal(true);
                }}
                className="inline-flex items-center gap-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-emerald-300 border border-emerald-500/40 px-3.5 py-2 font-bold text-xs transition-colors"
              >
                <BarChart3 className="h-3.5 w-3.5" />
                Relatório por Par
              </button>
              <button
                onClick={handleResetSession}
                disabled={resettingSession}
                className="inline-flex items-center gap-2 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-slate-950 px-4 py-2 font-bold text-xs transition-colors"
              >
                <RotateCcw className={`h-3.5 w-3.5 ${resettingSession ? 'animate-spin' : ''}`} />
                Zerar e Iniciar Nova Meta
              </button>
            </div>
          </div>
        </div>
      )}

      {/* STOP LOCK ALERT: MAX LOSS HIT */}
      {isLossHit && (
        <div className="rounded-2xl border border-rose-500 bg-gradient-to-r from-rose-950 via-slate-900 to-rose-950 p-4 shadow-glow">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="rounded-xl bg-rose-500/20 p-2.5 text-rose-400">
                <AlertOctagon className="h-6 w-6 animate-pulse" />
              </div>
              <div>
                <h3 className="text-sm font-extrabold text-rose-200 uppercase tracking-wide">
                  🛑 STOP LOSS ATINGIDO (-${Math.abs(sessionProfit).toFixed(2)} USD)
                </h3>
                <p className="text-xs text-slate-300">
                  A trava de segurança foi acionada para proteger seu capital contra perdas maiores.
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => {
                  loadSessionReport();
                  setShowReportModal(true);
                }}
                className="inline-flex items-center gap-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-rose-300 border border-rose-500/40 px-3.5 py-2 font-bold text-xs transition-colors"
              >
                <BarChart3 className="h-3.5 w-3.5" />
                Ver Qual Par Foi Pior
              </button>
              <button
                onClick={handleResetSession}
                disabled={resettingSession}
                className="inline-flex items-center gap-2 rounded-xl bg-rose-500 hover:bg-rose-400 text-white px-4 py-2 font-bold text-xs transition-colors"
              >
                <RotateCcw className={`h-3.5 w-3.5 ${resettingSession ? 'animate-spin' : ''}`} />
                Resetar Trava de Stop
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MAIN TWO-COLUMN TRADING TERMINAL */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12 items-start">
        {/* ========================================================= */}
        {/* LEFT COLUMN: CONTROL, RISK & EXECUTION (5 Cols)           */}
        {/* ========================================================= */}
        <div className="lg:col-span-5 space-y-4">
          {/* 1. BRAND HEADER & ACCOUNT CONNECTION */}
          <div className="glass-card p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <div className="relative flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-cyan-500 to-blue-600 shadow-glow">
                  <Cpu className="h-5 w-5 text-white" />
                  <span className="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-slate-950 bg-emerald-400 animate-pulse" />
                </div>
                <div>
                  <h1 className="text-sm font-extrabold tracking-wider text-slate-100 flex items-center gap-1.5">
                    NEXUS QUANTUM AI
                    <span className="rounded bg-cyan-500/20 px-1.5 py-0.5 text-[9px] font-mono font-bold text-cyan-300 border border-cyan-500/30">
                      TICK SCALPER
                    </span>
                  </h1>
                  <p className="text-[11px] text-slate-400 font-mono">
                    High Frequency Synthetic Engine • Anti-Repaint
                  </p>
                </div>
              </div>

              {/* WebSocket Indicator */}
              <div
                className={`flex items-center gap-1.5 rounded-lg border px-2 py-1 text-[10px] font-mono ${
                  wsStatus === 'connected'
                    ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                    : 'border-rose-500/30 bg-rose-500/10 text-rose-300'
                }`}
              >
                {wsStatus === 'connected' ? (
                  <>
                    <Wifi className="h-3 w-3 text-emerald-400" />
                    <span>AO VIVO</span>
                  </>
                ) : (
                  <>
                    <WifiOff className="h-3 w-3 text-rose-400" />
                    <span>RECONECTANDO</span>
                  </>
                )}
              </div>
            </div>

            {/* Account connection & live balance pill */}
            <div className="flex items-center justify-between rounded-xl border border-slate-800 bg-slate-950/70 p-2.5">
              {activeAccount ? (
                <div className="flex items-center gap-2">
                  <span
                    className={`rounded px-2 py-0.5 text-[10px] font-bold tracking-wider font-mono ${
                      activeAccount.account_type === 'DEMO'
                        ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                        : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                    }`}
                  >
                    {activeAccount.account_type}
                  </span>
                  <span className="font-mono text-xs text-slate-400">{activeAccount.loginid}</span>
                </div>
              ) : (
                <Link
                  to="/deriv-connect"
                  className="flex items-center gap-1.5 text-xs font-semibold text-amber-400 hover:text-amber-300 transition-colors"
                >
                  <Wallet className="h-3.5 w-3.5" />
                  Conectar Token Deriv
                </Link>
              )}

              <div className="text-right">
                <span className="text-[10px] uppercase font-semibold text-slate-400 block">Saldo Atual</span>
                <span className="font-mono text-sm font-extrabold text-slate-100">
                  {formatUsd(balance, currency)}
                </span>
              </div>
            </div>
          </div>

          {/* 2. HIGHLIGHTED PROFIT CARD & SESSION PROGRESS */}
          <div className="glass-card p-4 space-y-3 border-cyan-500/30 shadow-glow relative overflow-hidden">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold uppercase tracking-wider text-slate-300 flex items-center gap-1.5">
                <Sparkles className="h-3.5 w-3.5 text-cyan-400" />
                Resultado da Sessão
              </span>
              <button
                type="button"
                onClick={handleResetSession}
                disabled={resettingSession}
                title="Zerar Lucro e Placar da Sessão"
                className="text-slate-400 hover:text-slate-200 transition-colors"
              >
                <RotateCcw className={`h-3.5 w-3.5 ${resettingSession ? 'animate-spin' : ''}`} />
              </button>
            </div>

            {/* Big Profit Number */}
            <div className="flex items-baseline justify-between">
              <div>
                <div
                  className={`text-2xl font-black font-mono tracking-tight ${
                    sessionProfit >= 0 ? 'text-emerald-400' : 'text-rose-400'
                  }`}
                >
                  {sessionProfit >= 0 ? `+$${sessionProfit.toFixed(2)}` : `-$${Math.abs(sessionProfit).toFixed(2)}`}
                  <span className="text-xs ml-1 font-bold text-slate-400">USD</span>
                </div>
                <div className="text-xs font-mono text-slate-400 mt-0.5">
                  Lucro:{' '}
                  <strong className={sessionProfit >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                    {sessionProfit >= 0 ? `+$${sessionProfit.toFixed(2)}` : `-$${Math.abs(sessionProfit).toFixed(2)}`}
                  </strong>{' '}
                  ({bankrollProfitPct >= 0 ? `+${bankrollProfitPct.toFixed(2)}%` : `${bankrollProfitPct.toFixed(2)}%`}{' '}
                  da banca)
                </div>
              </div>

              {/* Status Pill */}
              <div className="text-right">
                <span
                  className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] font-bold font-mono ${
                    isMarketOpenTrade
                      ? 'bg-amber-500/20 text-amber-300 border border-amber-500/40 animate-pulse'
                      : botEnabled
                      ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40'
                      : 'bg-slate-800 text-slate-400 border border-slate-700'
                  }`}
                >
                  <span
                    className={`h-1.5 w-1.5 rounded-full ${
                      isMarketOpenTrade ? 'bg-amber-400' : botEnabled ? 'bg-cyan-400 animate-ping' : 'bg-slate-500'
                    }`}
                  />
                  {isMarketOpenTrade ? 'CONTRATO COMPRADO' : botEnabled ? 'BUSCANDO TRADES' : 'STANDBY'}
                </span>
              </div>
            </div>

            {/* Progress Bar towards Stop Win */}
            <div className="space-y-1.5 pt-1">
              <div className="flex items-center justify-between text-[11px] font-mono">
                <span className="text-slate-400">
                  Meta: <strong className="text-emerald-400">+$${targetProfit.toFixed(2)}</strong>
                </span>
                <span className="text-slate-400">
                  Progresso: <strong className="text-cyan-300">{progressPct.toFixed(1)}%</strong>
                </span>
              </div>
              <div className="h-2 w-full rounded-full bg-slate-900 border border-slate-800 overflow-hidden">
                <div
                  className={`h-full transition-all duration-500 rounded-full ${
                    sessionProfit >= 0
                      ? 'bg-gradient-to-r from-emerald-500 to-cyan-400'
                      : 'bg-gradient-to-r from-rose-600 to-rose-400'
                  }`}
                  style={{ width: `${Math.min(100, Math.max(sessionProfit > 0 ? 4 : 0, progressPct))}%` }}
                />
              </div>
            </div>
          </div>

          {/* 3. QUICK CONTROLS: DEEP ANALYSIS, CLOUD & STRATEGY ACCORDION */}
          <div className="glass-card p-4 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold uppercase tracking-wider text-slate-300 flex items-center gap-1.5">
                <Settings2 className="h-3.5 w-3.5 text-cyan-400" />
                Controles Rápidos
              </span>
              <button
                type="button"
                onClick={() => setKillModalOpen(true)}
                className="text-[11px] font-semibold text-rose-400 hover:text-rose-300 flex items-center gap-1"
                title="Kill switch de emergência"
              >
                <Power className="h-3 w-3" />
                Kill Switch
              </button>
            </div>

            {/* Deep Analysis Button (Orange highlight) */}
            <button
              type="button"
              onClick={handleToggleDeepAnalysis}
              className={`w-full flex items-center justify-between rounded-xl p-3 border font-bold transition-all shadow-md ${
                deepAnalysis
                  ? 'border-amber-500/70 bg-gradient-to-r from-amber-500/20 via-amber-600/15 to-amber-500/20 text-amber-200 shadow-amber-500/10'
                  : 'border-slate-800 bg-slate-900/60 text-slate-400 hover:border-slate-700'
              }`}
            >
              <div className="flex items-center gap-2.5">
                <div
                  className={`rounded-lg p-2 ${
                    deepAnalysis ? 'bg-amber-500 text-slate-950 shadow-glow' : 'bg-slate-800 text-slate-400'
                  }`}
                >
                  <Flame className="h-4 w-4" />
                </div>
                <div className="text-left">
                  <div className="text-xs uppercase tracking-wider flex items-center gap-1.5 font-extrabold text-amber-300">
                    MODO DEEP ANALYSIS
                    {deepAnalysis && (
                      <span className="rounded bg-amber-400/20 px-1.5 py-0.2 text-[9px] font-mono text-amber-300 border border-amber-400/40">
                        ATIVO
                      </span>
                    )}
                  </div>
                  <div className="text-[10px] text-slate-400 font-normal">
                    {deepAnalysis
                      ? 'Filtro Z-Score + EMA (Evita ruído e falso rompimento)'
                      : 'Desativado • Scalp Rápido (Momentum 1-2 Ticks)'}
                  </div>
                </div>
              </div>
              <div
                className={`h-5 w-9 rounded-full transition-colors relative flex items-center px-0.5 ${
                  deepAnalysis ? 'bg-amber-500' : 'bg-slate-800'
                }`}
              >
                <div
                  className={`h-4 w-4 rounded-full bg-white transition-transform ${
                    deepAnalysis ? 'translate-x-4' : 'translate-x-0'
                  }`}
                />
              </div>
            </button>

            {/* Background Cloud Execution Toggle */}
            <div className="flex items-center justify-between rounded-xl border border-slate-800/80 bg-slate-950/50 p-2.5">
              <div className="flex items-center gap-2">
                <div className="rounded-lg p-1.5 bg-cyan-500/15 text-cyan-400">
                  <Zap className="h-3.5 w-3.5" />
                </div>
                <div>
                  <span className="text-xs font-semibold text-slate-200 block">
                    Executar em Segundo Plano / Nuvem
                  </span>
                  <span className="text-[10px] text-slate-400">
                    Mantém o robô operando no servidor mesmo ao fechar a janela
                  </span>
                </div>
              </div>
              <input
                type="checkbox"
                checked={cloudBackground}
                onChange={(e) => setCloudBackground(e.target.checked)}
                className="h-4 w-4 rounded border-slate-700 bg-slate-900 text-cyan-500 focus:ring-0 cursor-pointer"
              />
            </div>

            {/* Accordion: Strategy Settings */}
            <div className="rounded-xl border border-slate-800 bg-slate-950/60 overflow-hidden">
              <button
                type="button"
                onClick={() => setSettingsOpen(!settingsOpen)}
                className="w-full flex items-center justify-between p-3 text-xs font-bold text-slate-300 hover:text-slate-100 transition-colors"
              >
                <div className="flex items-center gap-2">
                  <Settings2 className="h-3.5 w-3.5 text-cyan-400" />
                  <span>Configurações da Estratégia</span>
                </div>
                {settingsOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
              </button>

              {settingsOpen && (
                <div className="p-3 pt-0 border-t border-slate-800/80 space-y-3 text-xs">
                  {/* Stake & Duration */}
                  <div className="grid grid-cols-2 gap-2.5">
                    <div>
                      <label className="text-[10px] font-semibold text-slate-400 block mb-1">
                        Stake Inicial ($) (Mín. $1.00)
                      </label>
                      <input
                        type="number"
                        min="1.00"
                        step="0.10"
                        disabled={botEnabled}
                        value={stakeInput}
                        onChange={(e) => setStakeInput(parseFloat(e.target.value) || 1.00)}
                        className="w-full rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 font-mono text-xs font-bold text-slate-100 focus:border-cyan-500 focus:outline-none"
                      />
                    </div>
                    <div>
                      <label className="text-[10px] font-semibold text-slate-400 block mb-1">
                        Duração do Contrato
                      </label>
                      <select
                        disabled={botEnabled}
                        value={durationInput}
                        onChange={(e) => setDurationInput(parseInt(e.target.value, 10) || 1)}
                        className="w-full rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 font-mono text-xs font-bold text-slate-100 focus:border-cyan-500 focus:outline-none"
                      >
                        <option value={1}>1 Minuto (Vela M1 Conservador)</option>
                        <option value={5}>5 Ticks (Turbo Scalp)</option>
                        <option value={10}>10 Ticks (Scalp Médio)</option>
                      </select>
                    </div>
                  </div>

                  {/* Modo Deep Conservador Toggle */}
                  <div className="flex items-center justify-between p-2.5 rounded-lg border border-cyan-500/30 bg-cyan-950/20">
                    <div>
                      <span className="text-xs font-bold text-cyan-300 block">Modo Deep Conservador (3 MAs + RSI 5)</span>
                      <span className="text-[10px] text-slate-400">Opera alinhamento de MA 6/14/250 e RSI 5 (Níveis 70/30) com trava M1</span>
                    </div>
                    <button
                      type="button"
                      disabled={botEnabled}
                      onClick={() => setConservativeMode(!conservativeMode)}
                      className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${
                        conservativeMode ? 'bg-cyan-500' : 'bg-slate-700'
                      }`}
                    >
                      <span
                        className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-slate-950 shadow ring-0 transition duration-200 ease-in-out ${
                          conservativeMode ? 'translate-x-4' : 'translate-x-0'
                        }`}
                      />
                    </button>
                  </div>

                  {/* Filtro CVD Binance Toggle */}
                  <div className="flex items-center justify-between p-2.5 rounded-lg border border-amber-500/30 bg-amber-950/20">
                    <div>
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs font-bold text-amber-300 block">Filtro CVD Binance (Delta & Absorção)</span>
                        <span className="rounded px-1.5 py-0.2 text-[9px] font-mono font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30">BTC Exclusivo</span>
                      </div>
                      <span className="text-[10px] text-slate-400">Valida delta real e absorção institucional da Binance para entradas no BTC</span>
                    </div>
                    <button
                      type="button"
                      disabled={botEnabled}
                      onClick={() => setUseBinanceFilter(!useBinanceFilter)}
                      className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${
                        useBinanceFilter ? 'bg-amber-500' : 'bg-slate-700'
                      }`}
                    >
                      <span
                        className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-slate-950 shadow ring-0 transition duration-200 ease-in-out ${
                          useBinanceFilter ? 'translate-x-4' : 'translate-x-0'
                        }`}
                      />
                    </button>
                  </div>

                  {/* Stop Win & Stop Loss */}
                  <div className="grid grid-cols-2 gap-2.5">
                    <div>
                      <label className="text-[10px] font-semibold text-emerald-400 block mb-1">
                        Meta Lucro (+$)
                      </label>
                      <input
                        type="number"
                        min="5"
                        step="5"
                        disabled={botEnabled}
                        value={stopWinInput}
                        onChange={(e) => setStopWinInput(parseFloat(e.target.value) || 50.0)}
                        className="w-full rounded-lg border border-emerald-500/30 bg-slate-900 px-2.5 py-1.5 font-mono text-xs font-bold text-emerald-300 focus:border-emerald-400 focus:outline-none"
                      />
                    </div>
                    <div>
                      <label className="text-[10px] font-semibold text-rose-400 block mb-1">
                        Stop Loss (-$)
                      </label>
                      <input
                        type="number"
                        min="5"
                        step="5"
                        disabled={botEnabled}
                        value={stopLossInput}
                        onChange={(e) => setStopLossInput(parseFloat(e.target.value) || 50.0)}
                        className="w-full rounded-lg border border-rose-500/30 bg-slate-900 px-2.5 py-1.5 font-mono text-xs font-bold text-rose-300 focus:border-rose-400 focus:outline-none"
                      />
                    </div>
                  </div>

                  {/* Martingale Recovery Factor */}
                  <div>
                    <label className="text-[10px] font-semibold text-slate-400 block mb-1">
                      Fator Martingale Matemático (Padrão 2.15)
                    </label>
                    <input
                      type="number"
                      min="1.0"
                      max="3.5"
                      step="0.05"
                      disabled={botEnabled}
                      value={martingaleInput}
                      onChange={(e) => setMartingaleInput(parseFloat(e.target.value) || 2.15)}
                      className="w-full rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 font-mono text-xs font-bold text-slate-100 focus:border-cyan-500 focus:outline-none"
                    />
                    <p className="text-[10px] text-slate-500 mt-1">
                      Calcula dinamicamente a próxima entrada no payout para cobrir perdas passadas + lucro inicial.
                    </p>
                  </div>

                  <button
                    type="button"
                    onClick={() => handleSaveSettings()}
                    disabled={savingConfig || botEnabled}
                    className="w-full py-1.5 bg-cyan-600 hover:bg-cyan-500 text-slate-950 font-bold rounded-lg text-xs transition-colors"
                  >
                    {savingConfig ? 'Salvando...' : 'Salvar Parâmetros'}
                  </button>
                </div>
              )}
            </div>
          </div>

          {/* 4. MARKET MONITOR (MONITOR DE MERCADOS) */}
          <div className="glass-card p-4 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold uppercase tracking-wider text-slate-300 flex items-center gap-1.5">
                <BarChart3 className="h-3.5 w-3.5 text-cyan-400" />
                Monitor de Mercados Simultâneos
              </span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setShowBacktestModal(true)}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-cyan-500/20 to-blue-600/20 hover:from-cyan-500/30 hover:to-blue-600/30 text-cyan-300 border border-cyan-500/40 px-2.5 py-0.5 text-[10px] font-mono font-bold shadow-glow transition-all"
                  title="Executar auditoria real de 1.000 velas Deriv para descobrir o Top 10"
                >
                  <FlaskConical className="h-3 w-3 text-cyan-400" />
                  Backtest Top 10
                </button>
                <span className="text-[10px] font-mono text-slate-400">
                  {monitoredSymbols.length} Ativo(s)
                </span>
              </div>
            </div>

            {/* Category Filter Pills: TODOS, Forex, Cripto, Sintéticos */}
            <div className="flex items-center gap-1.5 p-1 bg-slate-950/70 rounded-lg border border-slate-800/80 text-[11px] font-mono">
              {(['TODOS', 'Forex', 'Cripto', 'Sintéticos'] as const).map((cat) => (
                <button
                  key={cat}
                  type="button"
                  onClick={() => setMarketFilter(cat)}
                  className={`flex-1 py-1 rounded font-bold text-center transition-all ${
                    marketFilter === cat
                      ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 shadow-glow'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {cat}
                </button>
              ))}
            </div>

            <div className="space-y-2 max-h-60 overflow-y-auto pr-1">
              {filteredMarkets.map((item) => {
                const isMonitored = monitoredSymbols.includes(item.symbol);
                const marketData = nexusTelemetry?.markets?.[item.symbol];
                const isSelected = activeSymbol === item.symbol;
                const spot = marketData?.spot || (isSelected && liveTick?.price ? liveTick.price : 0);
                const hasOpenTrade = Boolean(marketData?.has_open_trade);

                return (
                  <div
                    key={item.symbol}
                    onClick={() => setActiveSymbol(item.symbol)}
                    className={`cursor-pointer rounded-xl border p-2.5 transition-all flex items-center justify-between ${
                      isSelected
                        ? 'border-cyan-500/70 bg-cyan-950/30 shadow-glow ring-1 ring-cyan-500/30'
                        : 'border-slate-800/80 bg-slate-900/40 hover:border-slate-700'
                    }`}
                  >
                    <div className="flex items-center gap-2.5">
                      <input
                        type="checkbox"
                        checked={isMonitored}
                        onChange={(e) => {
                          e.stopPropagation();
                          handleToggleSymbol(item.symbol);
                        }}
                        disabled={botEnabled}
                        className="h-3.5 w-3.5 rounded border-slate-700 bg-slate-900 text-cyan-500 focus:ring-0 cursor-pointer"
                      />
                      <div>
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span className="font-bold text-xs text-slate-100">{item.name}</span>
                          <span
                            className={`rounded px-1.5 py-0.2 text-[9px] font-mono font-bold ${
                              item.category === 'Forex'
                                ? 'bg-blue-500/20 text-blue-300 border border-blue-500/30'
                                : item.category === 'Cripto'
                                ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                                : 'bg-slate-800 text-slate-300'
                            }`}
                          >
                            {item.category}
                          </span>
                        </div>
                        <div className="text-[10px] text-slate-400 font-mono">
                          {spot > 0 ? formatPrice(spot) : 'Conectando feed...'}
                        </div>
                      </div>
                    </div>

                    <div className="text-right">
                      <span
                        className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[10px] font-mono font-bold ${
                          hasOpenTrade
                            ? 'bg-amber-500/20 text-amber-300 border border-amber-500/40 animate-pulse'
                            : isMonitored
                            ? 'bg-cyan-500/15 text-cyan-300 border border-cyan-500/30'
                            : 'bg-slate-800 text-slate-500'
                        }`}
                      >
                        <span
                          className={`h-1.5 w-1.5 rounded-full ${
                            hasOpenTrade ? 'bg-amber-400' : isMonitored ? 'bg-cyan-400 animate-pulse' : 'bg-slate-600'
                          }`}
                        />
                        {hasOpenTrade ? 'OPERAÇÃO ABERTA' : isMonitored ? 'ANALISANDO' : 'INATIVO'}
                      </span>
                      {marketData?.z_score !== undefined && marketData.z_score !== null && (
                        <div className="text-[9px] font-mono text-slate-400 mt-0.5">
                          Z: {marketData.z_score > 0 ? `+${marketData.z_score.toFixed(2)}` : marketData.z_score.toFixed(2)}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* 5. SCOREBOARD & RECENT OPERATIONS */}
          <div className="glass-card p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-300 flex items-center gap-1.5">
                  <Trophy className="h-3.5 w-3.5 text-cyan-400" />
                  Placar & Histórico ao Vivo
                </span>
                <button
                  type="button"
                  onClick={() => {
                    loadSessionReport();
                    setShowReportModal(true);
                  }}
                  className="rounded-lg bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 px-2 py-0.5 text-[10px] font-mono flex items-center gap-1 transition-colors"
                >
                  <BarChart3 className="h-3 w-3" />
                  Relatório por Par
                </button>
              </div>
              <div className="flex items-center gap-2 font-mono text-xs">
                <span className="text-emerald-400 font-bold">{totalWins}W</span>
                <span className="text-slate-600">/</span>
                <span className="text-rose-400 font-bold">{totalLosses}L</span>
                <span className="text-slate-400">({winrate.toFixed(1)}%)</span>
              </div>
            </div>

            {/* Scrollable list of recent trades (Never empties after stop, falls back to PostgreSQL dbTrades) */}
            <div className="max-h-48 overflow-y-auto space-y-1.5 pr-1">
              {displayedTrades && displayedTrades.length > 0 ? (
                displayedTrades.map((t, idx) => (
                  <div
                    key={`${t.contract_id}-${idx}`}
                    className="flex items-center justify-between rounded-lg border border-slate-800/80 bg-slate-950/50 p-2 text-xs font-mono"
                  >
                    <div className="flex items-center gap-2">
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                          t.is_win ? 'bg-emerald-500/20 text-emerald-300' : 'bg-rose-500/20 text-rose-300'
                        }`}
                      >
                        {t.is_win ? 'GANHO' : 'PERDA'}
                      </span>
                      <div>
                        <span className="font-bold text-slate-200 block">
                          {getSymbolDisplayName(t.symbol)}
                        </span>
                        <span className="text-[10px] text-slate-500">
                          {new Date(t.epoch * 1000).toLocaleTimeString()} • Saída: {formatPrice(t.exit_spot)}
                        </span>
                      </div>
                    </div>

                    <div className="text-right">
                      <span
                        className={`font-bold block ${
                          t.is_win ? 'text-emerald-400' : 'text-rose-400'
                        }`}
                      >
                        {t.is_win ? `+$${t.profit_usd.toFixed(2)}` : `-$${Math.abs(t.profit_usd).toFixed(2)}`}
                      </span>
                      <span className="text-[10px] text-slate-400">
                        Stake: ${t.stake_usd.toFixed(2)}
                      </span>
                    </div>
                  </div>
                ))
              ) : (
                <div className="py-6 text-center text-xs text-slate-500 font-mono">
                  Nenhuma operação registrada no banco ou sessão atual. Inicie o robô para começar.
                </div>
              )}
            </div>
          </div>

          {/* 6. BIG START / STOP IA BUTTON */}
          <div className="pt-1 space-y-2">
            {!botEnabled && (
              <button
                type="button"
                onClick={() => setShowBacktestModal(true)}
                className="w-full py-2 px-3 rounded-xl border border-cyan-500/30 bg-cyan-950/30 hover:bg-cyan-900/40 text-cyan-300 font-mono text-xs font-bold flex items-center justify-center gap-2 transition-colors shadow-sm"
              >
                <FlaskConical className="h-3.5 w-3.5 text-cyan-400" />
                <span>🧪 Testar Top 10 Antes de Iniciar (Backtest Real)</span>
              </button>
            )}

            <button
              type="button"
              onClick={handleToggleBot}
              disabled={toggling || user?.is_expired || isLockedStop}
              className={`w-full py-4 px-6 rounded-2xl font-extrabold text-sm uppercase tracking-wider flex items-center justify-center gap-3 transition-all shadow-xl ${
                isLockedStop
                  ? 'bg-slate-800 text-slate-400 border border-slate-700 cursor-not-allowed'
                  : botEnabled
                  ? 'bg-gradient-to-r from-rose-600 to-rose-500 hover:from-rose-500 hover:to-rose-600 text-white shadow-rose-500/25'
                  : 'bg-gradient-to-r from-cyan-500 via-blue-600 to-cyan-500 hover:from-cyan-400 hover:to-blue-500 text-slate-950 shadow-glow font-black'
              }`}
            >
              {toggling ? (
                <Loader2 className="h-5 w-5 animate-spin" />
              ) : isLockedStop ? (
                <Lock className="h-5 w-5" />
              ) : botEnabled ? (
                <Square className="h-5 w-5 fill-current" />
              ) : (
                <Play className="h-5 w-5 fill-current" />
              )}
              <span>
                {isLockedStop
                  ? 'TRAVA ACIONADA (RESETE A SESSÃO)'
                  : botEnabled
                  ? 'PARAR IA (DESATIVAR)'
                  : 'INICIAR IA (NEXUS QUANTUM)'}
              </span>
            </button>
          </div>
        </div>

        {/* ========================================================= */}
        {/* RIGHT COLUMN: REAL-TIME LINE CHART & KPI TABS (7 Cols)   */}
        {/* ========================================================= */}
        <div className="lg:col-span-7 space-y-4">
          {/* TAB HEADER */}
          <div className="glass-card p-2 flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => setActiveTab('GRAFICO')}
                className={`flex items-center gap-2 rounded-xl px-4 py-2 text-xs font-bold font-mono transition-all ${
                  activeTab === 'GRAFICO'
                    ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 shadow-glow'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                <LineChart className="h-4 w-4" />
                GRÁFICO EM TEMPO REAL
              </button>

              <button
                type="button"
                onClick={() => setActiveTab('PAINEL')}
                className={`flex items-center gap-2 rounded-xl px-4 py-2 text-xs font-bold font-mono transition-all ${
                  activeTab === 'PAINEL'
                    ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 shadow-glow'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                <Activity className="h-4 w-4" />
                PAINEL ESTATÍSTICO
              </button>

              <button
                type="button"
                onClick={() => setActiveTab('DIGITOS')}
                className={`flex items-center gap-2 rounded-xl px-4 py-2 text-xs font-bold font-mono transition-all ${
                  activeTab === 'DIGITOS'
                    ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 shadow-glow'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                <Hash className="h-4 w-4" />
                ESTATÍSTICA DE DÍGITOS
              </button>
            </div>

            {/* Timeframe Selector for Line Chart */}
            {activeTab === 'GRAFICO' && (
              <div className="flex items-center gap-1 bg-slate-950 p-1 rounded-lg border border-slate-800">
                <button
                  type="button"
                  onClick={() => setGranularity(1)}
                  className={`rounded px-2.5 py-1 text-[11px] font-mono font-bold transition-all ${
                    granularity === 1
                      ? 'bg-cyan-500 text-slate-950 shadow-glow'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  1T (Tick)
                </button>
                <button
                  type="button"
                  onClick={() => setGranularity(60)}
                  className={`rounded px-2.5 py-1 text-[11px] font-mono font-bold transition-all ${
                    granularity === 60
                      ? 'bg-cyan-500 text-slate-950 shadow-glow'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  1m
                </button>
                <button
                  type="button"
                  onClick={() => setGranularity(300)}
                  className={`rounded px-2.5 py-1 text-[11px] font-mono font-bold transition-all ${
                    granularity === 300
                      ? 'bg-cyan-500 text-slate-950 shadow-glow'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  5m
                </button>
              </div>
            )}
          </div>

          {/* TAB 1: REAL-TIME CONTINUOUS LINE CHART & CANDLESTICKS */}
          {activeTab === 'GRAFICO' && (
            <div className="space-y-4">
              {/* LIVE ACTIVE OPEN POSITION HUD CARD */}
              {activePosition && (
                <div
                  className={`relative overflow-hidden rounded-2xl border p-4 transition-all shadow-xl backdrop-blur-md ${
                    activePosition.pnl >= 0
                      ? 'border-emerald-500/60 bg-gradient-to-r from-emerald-950/50 via-slate-900/90 to-emerald-950/30 ring-1 ring-emerald-500/40 shadow-emerald-950/30'
                      : 'border-rose-500/60 bg-gradient-to-r from-rose-950/50 via-slate-900/90 to-rose-950/30 ring-1 ring-rose-500/40 shadow-rose-950/30'
                  }`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-4">
                    {/* Left Details */}
                    <div className="space-y-1.5">
                      <div className="flex items-center gap-2">
                        <span className="flex items-center gap-1.5 rounded-full bg-emerald-500/20 border border-emerald-500/40 px-2.5 py-0.5 text-[10px] font-mono font-bold text-emerald-300">
                          <span className="h-2 w-2 rounded-full bg-emerald-400 animate-ping" />
                          OPERAÇÃO EM ANDAMENTO
                        </span>
                        <span className="text-[11px] font-mono text-slate-400">
                          Camada {activePosition.layers} de {config?.max_layers || 3}
                        </span>
                      </div>

                      <div className="flex items-center gap-3">
                        <h3 className="text-base font-extrabold text-slate-100 flex items-center gap-2">
                          {activePosition.name}
                          <span
                            className={`rounded px-2 py-0.5 text-xs font-mono font-bold ${
                              activePosition.isCall
                                ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                                : 'bg-rose-500/20 text-rose-300 border border-rose-500/40'
                            }`}
                          >
                            {activePosition.isCall ? '▲ COMPRA (MULTUP)' : '▼ VENDA (MULTDOWN)'}
                          </span>
                        </h3>

                        {activePosition.symbol !== activeSymbol && (
                          <button
                            type="button"
                            onClick={() => setActiveSymbol(activePosition.symbol)}
                            className="rounded-lg bg-cyan-500/20 border border-cyan-500/40 px-2 py-1 text-[10px] font-mono font-bold text-cyan-300 hover:bg-cyan-500/30 transition-all"
                          >
                            🔍 Ver no Gráfico
                          </button>
                        )}
                      </div>

                      <div className="flex items-center gap-4 text-xs font-mono text-slate-300 flex-wrap">
                        <span>
                          Entrada:{' '}
                          <strong className="text-slate-100">{formatPrice(activePosition.entryPrice)}</strong>
                        </span>
                        <span>•</span>
                        <span>
                          Atual:{' '}
                          <strong className="text-cyan-300">{formatPrice(activePosition.currentSpot)}</strong>
                        </span>
                        <span>•</span>
                        <span>
                          Stake:{' '}
                          <strong className="text-slate-100">${activePosition.stake.toFixed(2)} USD</strong>
                        </span>
                      </div>
                    </div>

                    {/* Right: Floating PnL & Take Profit Button */}
                    <div className="flex items-center gap-4">
                      <div className="text-right">
                        <span className="text-[10px] font-mono uppercase tracking-wider text-slate-400 block">
                          Lucro / Prejuízo Flutuante
                        </span>
                        <div
                          className={`text-2xl font-black font-mono tracking-tight flex items-center justify-end gap-1 ${
                            activePosition.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
                          }`}
                        >
                          {activePosition.pnl >= 0
                            ? `+$${activePosition.pnl.toFixed(2)}`
                            : `-$${Math.abs(activePosition.pnl).toFixed(2)}`}
                          <span className="text-xs font-bold text-slate-400">USD</span>
                        </div>
                        <span
                          className={`text-[11px] font-mono font-bold ${
                            activePosition.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
                          }`}
                        >
                          {activePosition.pnl >= 0 ? '+' : ''}
                          {activePosition.pnlPct.toFixed(1)}% da entrada
                        </span>
                      </div>

                      <button
                        type="button"
                        onClick={handleCloseBasket}
                        disabled={closingBasket}
                        className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-slate-950 font-extrabold px-3.5 py-2.5 text-xs shadow-lg shadow-emerald-950/40 transition-all disabled:opacity-50 cursor-pointer"
                        title="Encerrar posição imediatamente na Deriv e realizar o lucro"
                      >
                        <Zap className={`h-4 w-4 ${closingBasket ? 'animate-spin' : ''}`} />
                        {closingBasket ? 'Encerrando...' : 'Encerrar Operação'}
                      </button>
                    </div>
                  </div>
                </div>
              )}

              <NexusLineChart
                key={activeSymbol}
                symbol={activeSymbol}
                category={AVAILABLE_MARKETS.find((m) => m.symbol === activeSymbol)?.category}
                granularity={granularity}
                candles={candles}
                liveTick={currentSymbolLiveTick}
                recentTrades={nexusTelemetry?.recent_trades || []}
                openTrade={
                  activePosition && activePosition.symbol === activeSymbol
                    ? {
                        symbol: activePosition.symbol,
                        direction: activePosition.direction,
                        entry_spot: activePosition.entryPrice,
                        current_spot: activePosition.currentSpot,
                        current_profit_usd: activePosition.pnl,
                        stake_usd: activePosition.stake,
                        layers_count: activePosition.layers,
                      }
                    : null
                }
                indicators={{
                  ma6: activeMarketData?.ma6,
                  ma14: activeMarketData?.ma14,
                  ma250: activeMarketData?.ma250,
                  rsi5: activeMarketData?.rsi5,
                }}
                onTimeframeChange={setGranularity}
                onRefresh={() => loadChart(activeSymbol, granularity)}
                loading={loadingChart}
              />

              {/* Sub-card: Microstructure Telemetry */}
              <div className="glass-card p-3.5">
                <div className="flex flex-wrap items-center justify-between gap-4 font-mono text-xs">
                  <div className="flex items-center gap-2">
                    <span className="text-slate-400">Ativo Selecionado:</span>
                    <strong className="text-cyan-300">
                      {getSymbolDisplayName(activeSymbol)}
                    </strong>
                  </div>

                  <div className="flex items-center gap-3 sm:gap-6 flex-wrap">
                    {/* MA-6 */}
                    <span className="text-slate-400">
                      MA-6:{' '}
                      <strong
                        className={
                          activeMarketData?.ma6 && activeMarketData?.ma14
                            ? activeMarketData.ma6 >= activeMarketData.ma14
                              ? 'text-emerald-400'
                              : 'text-rose-400'
                            : 'text-slate-200'
                        }
                      >
                        {activeMarketData?.ma6 !== undefined && activeMarketData?.ma6 !== null
                          ? formatPrice(activeMarketData.ma6)
                          : 'Calculando...'}
                      </strong>
                    </span>

                    {/* MA-14 */}
                    <span className="text-slate-400">
                      MA-14:{' '}
                      <strong className="text-slate-200">
                        {activeMarketData?.ma14 !== undefined && activeMarketData?.ma14 !== null
                          ? formatPrice(activeMarketData.ma14)
                          : 'Calculando...'}
                      </strong>
                    </span>

                    {/* MA-250 */}
                    <span className="text-slate-400">
                      MA-250:{' '}
                      <strong
                        className={
                          activeMarketData?.ma250 && activeMarketData?.spot
                            ? activeMarketData.spot >= activeMarketData.ma250
                              ? 'text-emerald-400'
                              : 'text-rose-400'
                            : 'text-slate-200'
                        }
                      >
                        {activeMarketData?.ma250 !== undefined && activeMarketData?.ma250 !== null
                          ? formatPrice(activeMarketData.ma250)
                          : 'Calculando...'}
                      </strong>
                    </span>

                    {/* RSI-5 */}
                    <span className="text-slate-400 flex items-center gap-1.5">
                      RSI-5:{' '}
                      <strong
                        className={`px-1.5 py-0.5 rounded text-[11px] font-mono ${
                          (activeMarketData?.rsi5 ?? 50) >= 70
                            ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40'
                            : (activeMarketData?.rsi5 ?? 50) <= 30
                            ? 'bg-rose-500/20 text-rose-400 border border-rose-500/40'
                            : 'text-cyan-300 font-semibold'
                        }`}
                      >
                        {activeMarketData?.rsi5 !== undefined && activeMarketData?.rsi5 !== null
                          ? activeMarketData.rsi5.toFixed(1)
                          : 'Calculando...'}
                      </strong>
                    </span>

                    {/* Binance CVD Filter status (for BTC) */}
                    {activeSymbol === 'cryBTCUSD' && (
                      <span className="flex items-center gap-1.5">
                        <span className="text-slate-400">Binance CVD:</span>
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] font-mono font-bold border ${
                            useBinanceFilter
                              ? 'bg-amber-500/20 text-amber-300 border-amber-500/40'
                              : 'bg-slate-800 text-slate-400 border-slate-700'
                          }`}
                        >
                          {useBinanceFilter ? '✓ Filtro Ativo' : 'Desativado'}
                        </span>
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* TAB 2: DETAILED STATISTICAL KPIS */}
          {activeTab === 'PAINEL' && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                <div className="glass-card p-3.5 space-y-1">
                  <span className="text-[10px] font-semibold uppercase text-slate-400 block">Taxa de Vitória</span>
                  <div className="text-xl font-bold font-mono text-emerald-400">
                    {winrate.toFixed(1)}%
                  </div>
                  <span className="text-[10px] text-slate-500 font-mono">
                    {totalWins} Wins / {totalLosses} Losses
                  </span>
                </div>

                <div className="glass-card p-3.5 space-y-1">
                  <span className="text-[10px] font-semibold uppercase text-slate-400 block">Próxima Entrada</span>
                  <div className="text-xl font-bold font-mono text-cyan-300">
                    ${currentStake.toFixed(2)} USD
                  </div>
                  <span className="text-[10px] text-slate-500 font-mono">
                    {consecutiveLosses > 0 ? `Nível Martingale #${consecutiveLosses}` : 'Stake Base Inicial'}
                  </span>
                </div>

                <div className="glass-card p-3.5 space-y-1">
                  <span className="text-[10px] font-semibold uppercase text-slate-400 block">Perda na Sequência</span>
                  <div className="text-xl font-bold font-mono text-rose-400">
                    -${accumulatedLoss.toFixed(2)} USD
                  </div>
                  <span className="text-[10px] text-slate-500 font-mono">
                    Recuperação automática na vitória
                  </span>
                </div>

                <div className="glass-card p-3.5 space-y-1">
                  <span className="text-[10px] font-semibold uppercase text-slate-400 block">Payout Médio</span>
                  <div className="text-xl font-bold font-mono text-slate-200">
                    ~92.0%
                  </div>
                  <span className="text-[10px] text-slate-500 font-mono">
                    Retorno líquido Deriv
                  </span>
                </div>

                <div className="glass-card p-3.5 space-y-1">
                  <span className="text-[10px] font-semibold uppercase text-slate-400 block">Tempo Médio Ordem</span>
                  <div className="text-xl font-bold font-mono text-slate-200">
                    {durationInput * 1}s ({durationInput} Ticks)
                  </div>
                  <span className="text-[10px] text-slate-500 font-mono">
                    Ultra Scalp Rise/Fall
                  </span>
                </div>

                <div className="glass-card p-3.5 space-y-1">
                  <span className="text-[10px] font-semibold uppercase text-slate-400 block">Proteção de Banca</span>
                  <div className="text-xl font-bold font-mono text-emerald-400">
                    ATIVA
                  </div>
                  <span className="text-[10px] text-slate-500 font-mono">
                    Trava Stop Loss: -${stopLossInput.toFixed(2)}
                  </span>
                </div>
              </div>

              {/* Mathematical Expectancy Explanation */}
              <div className="glass-card p-4 space-y-2 border-slate-800">
                <h4 className="text-xs font-bold text-slate-200 uppercase tracking-wide flex items-center gap-1.5">
                  <Shield className="h-4 w-4 text-cyan-400" />
                  Arquitetura Anti-Repaint e Martingale Dinâmico
                </h4>
                <p className="text-xs text-slate-400 leading-relaxed">
                  Diferente de sistemas comuns com multiplicador fixo rígido (2.0x) que causam quebra rápida, o{' '}
                  <strong className="text-cyan-300">NEXUS QUANTUM AI</strong> calcula a próxima stake dinamicamente com base no
                  payout retornado em tempo real pela API da Deriv:{' '}
                  <code className="text-amber-300 font-mono text-[11px] bg-slate-900 px-1.5 py-0.5 rounded">
                    next_stake = (perda_acumulada + lucro_base) / payout_ratio
                  </code>
                  . Ao vencer qualquer operação, toda a perda da sequência é imediatamente zerada e o capital retorna ao stake inicial.
                </p>
              </div>
            </div>
          )}

          {/* TAB 3: LIVE DIGIT STATISTICS (0-9) */}
          {activeTab === 'DIGITOS' && (
            <div className="glass-card p-5 space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h4 className="text-xs font-bold text-slate-200 uppercase tracking-wider flex items-center gap-1.5">
                    <Hash className="h-4 w-4 text-cyan-400" />
                    Distribuição Estatística dos Últimos Dígitos (0 a 9)
                  </h4>
                  <p className="text-[11px] text-slate-400 font-mono mt-0.5">
                    Frequência em tempo real dos ticks do {activeSymbol} (amostra dos últimos {recentDigits.length} ticks)
                  </p>
                </div>
                <div className="text-right font-mono text-xs text-slate-400">
                  Último Dígito:{' '}
                  <strong className="text-cyan-300 text-sm">
                    {recentDigits.length > 0 ? recentDigits[0] : '—'}
                  </strong>
                </div>
              </div>

              {/* 10 Digit frequency bars */}
              <div className="grid grid-cols-5 sm:grid-cols-10 gap-2 pt-2">
                {digitDistribution.map((pct, digit) => {
                  const isLast = recentDigits[0] === digit;
                  const isHigh = pct >= 14;
                  const isLow = pct <= 6;

                  return (
                    <div
                      key={digit}
                      className={`rounded-xl border p-2 text-center transition-all ${
                        isLast
                          ? 'border-cyan-400 bg-cyan-950/40 shadow-glow'
                          : isHigh
                          ? 'border-emerald-500/40 bg-emerald-950/20'
                          : isLow
                          ? 'border-rose-500/40 bg-rose-950/20'
                          : 'border-slate-800 bg-slate-950/50'
                      }`}
                    >
                      <div className="font-mono text-base font-extrabold text-slate-100">{digit}</div>
                      <div
                        className={`text-xs font-mono font-bold mt-1 ${
                          isHigh ? 'text-emerald-400' : isLow ? 'text-rose-400' : 'text-slate-400'
                        }`}
                      >
                        {pct}%
                      </div>
                      <div className="h-1.5 w-full bg-slate-900 rounded-full mt-2 overflow-hidden">
                        <div
                          className={`h-full rounded-full ${
                            isHigh ? 'bg-emerald-400' : isLow ? 'bg-rose-400' : 'bg-cyan-500'
                          }`}
                          style={{ width: `${Math.min(100, pct * 4)}%` }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Digit Stream (Last 30 Digits) */}
              <div className="space-y-1.5 pt-2">
                <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider block">
                  Sequência Recente dos Ticks:
                </span>
                <div className="flex flex-wrap gap-1 font-mono text-xs">
                  {recentDigits.slice(0, 30).map((d, i) => (
                    <span
                      key={i}
                      className={`h-6 w-6 rounded flex items-center justify-center font-bold ${
                        i === 0
                          ? 'bg-cyan-500 text-slate-950 shadow-glow'
                          : d % 2 === 0
                          ? 'bg-slate-800 text-slate-300'
                          : 'bg-slate-900 text-slate-400'
                      }`}
                    >
                      {d}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* KILL SWITCH MODAL */}
      <KillSwitchModal
        open={killModalOpen}
        onClose={() => setKillModalOpen(false)}
        onConfirm={handleKillSwitch}
        openBasket={null}
        currency={currency}
      />

      {/* SESSION REPORT MODAL (AUDITORIA POR PAR) */}
      <SessionReportModal
        open={showReportModal}
        onClose={() => setShowReportModal(false)}
        onResetSession={handleResetSession}
        report={sessionReport}
        loading={loadingReport}
        currency={currency}
      />

      {/* BACKTEST TOP 10 MODAL (LABORATÓRIO REAL DERIV) */}
      <BacktestModal
        open={showBacktestModal}
        onClose={() => setShowBacktestModal(false)}
        onAppliedTop10={(syms) => {
          setMonitoredSymbols(syms);
          if (syms.length > 0 && !syms.includes(activeSymbol)) {
            setActiveSymbol(syms[0]);
          }
          loadOverview();
        }}
      />
    </div>
  );
};
