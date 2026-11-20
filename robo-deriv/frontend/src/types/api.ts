export type UserRole = 'ADMIN' | 'USER';
export type UserStatus = 'ACTIVE' | 'SUSPENDED';
export type AccountType = 'DEMO' | 'REAL';
export type DirectionMode = 'LONG_ONLY' | 'SHORT_ONLY' | 'BOTH';
export type StrategyMode = 'NEXUS_QUANTUM' | 'SYNTRA_TICKS' | 'SWING_GRID_RESPIROS' | 'BOTH_ROBOTS' | 'GRADIENTE_LINEAR' | 'TICK_MEAN_REVERSION' | 'SMART_CHANNEL_DCA' | 'LINREG_ATR_VIDEO';
export type ContractDirection = 'MULTUP' | 'MULTDOWN';

export interface User {
  id: number;
  name: string;
  email: string;
  role: UserRole;
  status: UserStatus;
  expires_at: string;
  days_remaining: number;
  is_expired: boolean;
  must_change_password: boolean;
  max_stake_usd: number;
  can_access_btc_radar?: boolean;
  can_access_robo_deriv?: boolean;
  allowed_modules?: string[];
  current_session_ip?: string | null;
  created_at?: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface DerivOAuthUrlResponse {
  oauth_url: string;
  app_id: string;
  configured: boolean;
}

export interface DerivOAuthAccountInput {
  account: string;
  token: string;
  currency: string;
}

export interface DerivAccount {
  id: number;
  loginid: string;
  account_type: AccountType;
  currency: string;
  balance: number;
  is_active_selected: boolean;
  last_synced_at: string | null;
}

export interface MarketSymbol {
  symbol: string;
  display_name: string;
  market: string;
  market_display_name: string;
  submarket_display_name: string;
  is_open: boolean;
  multipliers: number[];
  min_stake: number;
  pip_size: number;
}

export interface ProposalPreview {
  ask_price: number;
  commission: number;
  stop_out_value: number;
  spot: number;
  stop_out_pct: number;
  error?: string | null;
}

export interface ChartCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface ChartLinePoint {
  time: number;
  value: number;
}

export interface ArmedOrderInfo {
  symbol: string;
  direction: 'MULTUP' | 'MULTDOWN' | string;
  trigger_price: number;
  atr_value: number;
  tp_price: number;
  sl_price: number;
  lin_upper?: number;
  lin_mid?: number;
  lin_lower?: number;
}

export interface ScannerItem {
  symbol: string;
  spot: number;
  upper: number;
  center: number;
  lower: number;
  atr: number;
  r2: number;
  slope: number;
  dist_upper_pct: number;
  dist_lower_pct: number;
  status: 'TOUCH_UPPER' | 'TOUCH_LOWER' | 'NEAR_UPPER' | 'NEAR_LOWER' | 'MONITORING' | 'ERROR' | string;
  armed_direction?: 'MULTUP' | 'MULTDOWN' | null;
}

export interface ChannelData {
  upper: number | null;
  center: number | null;
  mid?: number | null;
  lower: number | null;
  slope: number | null;
  r2: number | null;
  ema50: number | null;
  ema200: number | null;
  rsi: number | null;
  adx: number | null;
  atr: number | null;
  crash_guard: boolean;
  direction?: string | null;
  upper_line?: ChartLinePoint[];
  center_line?: ChartLinePoint[];
  lower_line?: ChartLinePoint[];
  points?: { time: number; upper?: number; mid?: number; center?: number; lower?: number; value?: number }[];
  series?: { time: number; upper?: number; mid?: number; center?: number; lower?: number; value?: number }[];
}

export interface MarketChartResponse {
  candles: ChartCandle[];
  channel: ChannelData | null;
}

export interface BotConfigData {
  id?: number;
  user_id?: number;
  enabled: boolean;
  symbol: string;
  timeframe_sec: number;
  direction_mode: DirectionMode;
  stake_usd: number;
  multiplier: number;
  max_layers: number;
  layer_multipliers_json: string;
  basket_tp_usd: number;
  trailing_enabled: boolean;
  max_basket_loss_usd: number;
  max_daily_loss_usd: number;
  daily_profit_target_usd: number;
  max_consecutive_losses: number;
  max_hold_minutes: number;
  use_binance_filter: boolean;
  strategy_mode?: StrategyMode;
  channel_window?: number;
  channel_sigma?: number;
  atr_period?: number;
  tp_atr_mult?: number;
  sl_atr_mult?: number;
  use_crash_guard?: boolean;
  multi_pair_enabled?: boolean;
  scanner_symbols_json?: string;
  session_profit_usd?: number;
  session_cycles_count?: number;
  grid_step_pct?: number;
  deep_analysis_enabled?: boolean;
  conservative_mode_enabled?: boolean;
  tick_duration?: number;
  martingale_factor?: number;
  active_indices_json?: string;
  active_indices?: string[];
  status_text: string;
  updated_at?: string;
}

export interface BotConfigUpdatePayload {
  symbol: string;
  timeframe_sec: number;
  direction_mode: DirectionMode;
  stake_usd: number;
  multiplier: number;
  max_layers: number;
  layer_multipliers_json: string;
  basket_tp_usd: number;
  trailing_enabled: boolean;
  max_basket_loss_usd: number;
  max_daily_loss_usd: number;
  daily_profit_target_usd: number;
  max_consecutive_losses: number;
  max_hold_minutes: number;
  use_binance_filter: boolean;
  strategy_mode?: StrategyMode;
  channel_window?: number;
  channel_sigma?: number;
  atr_period?: number;
  tp_atr_mult?: number;
  sl_atr_mult?: number;
  use_crash_guard?: boolean;
  multi_pair_enabled?: boolean;
  scanner_symbols_json?: string;
  session_profit_usd?: number;
  session_cycles_count?: number;
  grid_step_pct?: number;
  deep_analysis_enabled?: boolean;
  conservative_mode_enabled?: boolean;
  tick_duration?: number;
  martingale_factor?: number;
  active_indices_json?: string;
  active_indices?: string[];
}

export interface ContractItem {
  id: number;
  basket_id: number;
  layer_index: number;
  deriv_contract_id: string | null;
  contract_type: ContractDirection | string;
  symbol: string;
  stake_usd: number;
  multiplier: number;
  commission_usd: number;
  entry_spot: number;
  stop_out_spot: number | null;
  current_spot: number | null;
  current_profit_usd: number;
  exit_spot: number | null;
  final_profit_usd: number | null;
  status: 'OPEN' | 'SOLD' | 'STOPPED_OUT' | string;
  opened_at: string;
  closed_at: string | null;
}

export interface BasketItem {
  id: number;
  user_id?: number;
  deriv_account_id?: number | null;
  account_type: AccountType;
  symbol: string;
  direction: ContractDirection | string;
  status: 'OPEN' | 'CLOSED' | string;
  layers_count: number;
  total_stake_usd: number;
  total_commission_usd: number;
  avg_entry_price: number;
  current_pnl_usd: number;
  realized_pnl_usd: number;
  peak_pnl_usd: number;
  close_reason: string | null;
  opened_at: string;
  closed_at: string | null;
  contracts?: ContractItem[];
}

export interface BotEventLogItem {
  id: number;
  level: 'INFO' | 'SIGNAL' | 'TRADE' | 'WARN' | 'ERROR' | string;
  symbol: string | null;
  message: string;
  details_json?: string | null;
  created_at: string;
}

export interface BotConfigOverviewResponse {
  config: BotConfigData;
  active_account: DerivAccount | null;
  open_basket: BasketItem | null;
  open_contracts: ContractItem[];
  recent_events: BotEventLogItem[];
  recent_trades?: NexusTradeItem[];
  live_state?: Record<string, unknown> | null;
}

export interface SessionPairStat {
  symbol: string;
  name: string;
  category: string;
  trades: number;
  wins: number;
  losses: number;
  winrate_pct: number;
  total_pnl_usd: number;
}

export interface SessionReportResponse {
  session_profit_usd: number;
  target_profit_usd: number;
  max_daily_loss_usd: number;
  is_stopped: boolean;
  stop_reason: string;
  total_trades: number;
  total_wins: number;
  total_losses: number;
  winrate_pct: number;
  best_pair: SessionPairStat | null;
  worst_pair: SessionPairStat | null;
  pairs: SessionPairStat[];
}

export interface EquityPoint {
  date: string;
  timestamp?: string;
  equity: number;
  pnl?: number;
  cumulative_pnl?: number;
}

export interface DailyPnlPoint {
  date: string;
  pnl: number;
  cycles?: number;
  wins?: number;
  losses?: number;
}

export interface LayerDistributionItem {
  layer: number | string;
  count: number;
  pct?: number;
  win_rate?: number;
  pnl?: number;
}

export interface SymbolPerformanceItem {
  symbol: string;
  cycles: number;
  wins: number;
  losses?: number;
  win_rate: number;
  total_pnl: number;
  profit_factor?: number;
}

export interface ReportsSummaryResponse {
  account_type: AccountType;
  total_cycles: number;
  winning_cycles: number;
  losing_cycles: number;
  win_rate: number;
  total_pnl_usd: number;
  today_pnl_usd: number;
  today_pnl_pct?: number;
  total_commission_usd: number;
  profit_factor: number;
  max_drawdown_usd: number;
  max_drawdown_pct?: number;
  avg_win_usd?: number;
  avg_loss_usd?: number;
  expectancy_usd?: number;
  equity_curve: EquityPoint[];
  daily_pnl: DailyPnlPoint[];
  layer_distribution: LayerDistributionItem[];
  by_symbol: SymbolPerformanceItem[];
}

export interface ReportsCyclesResponse {
  items: BasketItem[];
  total: number;
  page: number;
  page_size: number;
  total_pages?: number;
}

export interface AdminUserItem extends User {
  deriv_connected?: boolean;
  active_account_type?: AccountType | null;
  active_loginid?: string | null;
  active_balance?: number | null;
  bot_enabled?: boolean;
  bot_status_text?: string | null;
  bot_symbol?: string | null;
  open_baskets_count?: number;
}

export interface AdminCreateUserPayload {
  name: string;
  email: string;
  password: string;
  role: UserRole;
  status: UserStatus;
  expires_at: string;
  max_stake_usd: number;
  can_access_btc_radar?: boolean;
  can_access_robo_deriv?: boolean;
  allowed_modules?: string[];
  must_change_password?: boolean;
}

export interface AdminUpdateUserPayload {
  name?: string;
  email?: string;
  password?: string;
  role?: UserRole;
  status?: UserStatus;
  expires_at?: string;
  max_stake_usd?: number;
  can_access_btc_radar?: boolean;
  can_access_robo_deriv?: boolean;
  allowed_modules?: string[];
  must_change_password?: boolean;
}

export interface AdminHealthResponse {
  status?: string;
  postgres: boolean | { ok: boolean; latency_ms?: number; detail?: string };
  redis: boolean | { ok: boolean; latency_ms?: number; detail?: string };
  worker: {
    alive: boolean;
    last_heartbeat?: string | null;
    age_seconds?: number | null;
    active_sessions?: number;
    tracked_symbols?: string[];
  };
  active_sessions?: number;
  total_users?: number;
  active_bots?: number;
  open_baskets?: number;
  recent_errors?: BotEventLogItem[];
}

export interface GradientLevelItem {
  level_index: number;
  target_price: number;
  stake_usd: number;
  executed: boolean;
}

export interface SyntraMarketItem {
  status: string;
  spot: number;
  z_score?: number | null;
  ema20?: number | null;
  has_open_trade: boolean;
}

export interface SyntraTradeItem {
  symbol: string;
  contract_id: string;
  profit_usd: number;
  is_win: boolean;
  exit_spot: number;
  epoch: number;
}

export interface SyntraTelemetry {
  markets: Record<string, SyntraMarketItem>;
  total_wins: number;
  total_losses: number;
  win_rate: number;
  session_profit_usd: number;
  recent_trades: SyntraTradeItem[];
}

export interface WsDashboardMessage {
  type: 'tick' | 'state' | 'basket' | 'event' | 'balance' | 'status' | 'pong' | 'snapshot' | string;
  symbol?: string;
  price?: number;
  spot?: number;
  quote?: number;
  epoch?: number;
  time?: number;
  balance?: number;
  currency?: string;
  account_type?: AccountType;
  bot_enabled?: boolean;
  status_text?: string;
  open_basket?: BasketItem | null;
  open_contracts?: ContractItem[];
  event?: BotEventLogItem;
  session_profit_usd?: number;
  session_cycles_count?: number;
  daily_profit_target_usd?: number;
  max_daily_loss_usd?: number;
  gradient_levels?: GradientLevelItem[] | null;
  gradient_target_price?: number | null;
  gradient_avg_entry?: number | null;
  tick_ema_20?: number | null;
  tick_std_60?: number | null;
  tick_z_score?: number | null;
  syntra_telemetry?: SyntraTelemetry | null;
  nexus_telemetry?: NexusTelemetry | null;
  data?: Record<string, unknown>;
  state?: Record<string, unknown>;
  live_state?: Record<string, unknown>;
}

export interface BtcRadarPrediction {
  direction: 'CALL' | 'PUT' | 'WAIT' | string;
  predicted_color: string;
  confidence_pct: number;
  product?: string;
  source?: string;
  lock_price?: number | null;
  locked_offset_ms?: number;
  p_green?: number;
  p_red?: number;
}

export interface BtcSignal2 {
  direction: string;
  label: string;
  score: number;
  entry_status: string;
  entry_code: string;
  regime: string;
  dpx_5s_bps: number;
}

export interface BtcL2Snapshot {
  bids: [number, number][];
  asks: [number, number][];
  bid_vol: number;
  ask_vol: number;
  imbalance_ratio: number;
  delta_depth: number;
  spread_usd?: number;
}

export interface BtcAuditRecentResult {
  candle_time?: string;
  time?: string;
  prediction?: string;
  predicted?: string;
  actual?: string;
  result?: string;
  is_win?: boolean;
  product?: string;
  confidence_pct?: number;
}

export interface BtcAuditSummary {
  total: number;
  wins: number;
  losses: number;
  dojis: number;
  winrate: number;
  color_wins?: number;
  color_winrate?: number;
  recent_results?: BtcAuditRecentResult[];
}

export interface BtcAuditByProduct {
  total: number;
  wins: number;
  losses: number;
  dojis?: number;
  winrate: number;
  color_wins?: number;
  color_winrate?: number;
}

export interface BtcCurrentCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
  is_closed?: boolean;
}

export interface BtcRadarState {
  symbol: string;
  current_price: number;
  seconds_left: number;
  current_candle?: BtcCurrentCandle | null;
  prediction: BtcRadarPrediction;
  signal2?: BtcSignal2;
  orderbook_l2?: BtcL2Snapshot;
  bias_summary?: {
    m1_score?: number;
    m5_score?: number;
    m15_score?: number;
    composite_bias?: string;
  };
  flow?: {
    volume_1m?: number;
    delta_1m?: number;
    large_orders_count?: number;
  };
  audit_summary?: BtcAuditSummary;
  audit_by_product?: Record<string, BtcAuditByProduct>;
  timestamp?: string;
}

export interface BtcKlineCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface BtcSrZone {
  price: number;
  timeframe: 'M5' | 'M15' | string;
  type: 'SUPPORT' | 'RESISTANCE' | string;
  label: string;
  color: string;
  line_style: number;
  line_width: number;
}

export interface BtcSrZonesResponse {
  symbol: string;
  current_price: number;
  zones: BtcSrZone[];
}

// ============================================================
// NEXUS QUANTUM AI High-Frequency Tick Trading Interfaces
// ============================================================
export interface NexusMarketItem {
  symbol: string;
  name: string;
  category?: string;
  status: 'ANALISANDO' | 'OPERACAO_ABERTA' | string;
  spot: number;
  z_score: number | null;
  ema10: number | null;
  ema20: number | null;
  ma6?: number | null;
  ma14?: number | null;
  ma250?: number | null;
  rsi5?: number | null;
  has_open_trade: boolean;
  basket_layers?: number;
  is_hydrated?: boolean;
  entry_spot?: number | null;
  direction?: string | null;
  floating_pnl?: number | null;
  stake_usd?: number | null;
}

export interface NexusTradeItem {
  symbol: string;
  contract_id: string;
  stake_usd: number;
  profit_usd: number;
  is_win: boolean;
  exit_spot: number;
  next_stake_usd?: number;
  consecutive_losses?: number;
  epoch: number;
}

export interface NexusTelemetry {
  engine_brand: string;
  strategy_title?: string;
  conservative_mode_enabled?: boolean;
  deep_analysis_enabled: boolean;
  tick_duration: number;
  duration_unit?: string;
  markets: Record<string, NexusMarketItem>;
  total_wins: number;
  total_losses: number;
  win_rate: number;
  session_profit_usd: number;
  current_stake_usd: number;
  initial_stake_usd: number;
  martingale_factor?: number;
  consecutive_losses: number;
  accumulated_loss_streak_usd: number;
  is_locked_stop: boolean;
  lock_reason: string;
  stop_win_usd: number;
  stop_loss_usd: number;
  recent_trades: NexusTradeItem[];
}

export interface BacktestResultItem {
  symbol: string;
  name: string;
  category: 'Forex' | 'Cripto' | 'Sintéticos' | string;
  multiplier: number;
  candles_tested: number;
  trades_count: number;
  wins: number;
  losses: number;
  winrate_pct: number;
  total_pnl_usd: number;
  profit_factor: number;
  max_drawdown_usd: number;
  rank?: number;
  is_top10?: boolean;
}

export interface BacktestReportResponse {
  timestamp: string;
  candle_count: number;
  total_pairs_tested: number;
  total_trades_simulated: number;
  avg_top10_winrate_pct: number;
  best_pair: BacktestResultItem | null;
  worst_pair: BacktestResultItem | null;
  top10_symbols: string[];
  results: BacktestResultItem[];
}

