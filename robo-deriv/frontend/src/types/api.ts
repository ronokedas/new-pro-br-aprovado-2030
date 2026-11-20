export type UserRole = 'ADMIN' | 'USER';
export type UserStatus = 'ACTIVE' | 'SUSPENDED';
export type AccountType = 'DEMO' | 'REAL';
export type DirectionMode = 'LONG_ONLY' | 'SHORT_ONLY' | 'BOTH';
export type StrategyMode = 'SYNTRA_TICKS' | 'SWING_GRID_RESPIROS' | 'BOTH_ROBOTS' | 'GRADIENTE_LINEAR' | 'TICK_MEAN_REVERSION' | 'SMART_CHANNEL_DCA' | 'LINREG_ATR_VIDEO';
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
  live_state?: Record<string, unknown> | null;
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
  data?: Record<string, unknown>;
  state?: Record<string, unknown>;
  live_state?: Record<string, unknown>;
}
