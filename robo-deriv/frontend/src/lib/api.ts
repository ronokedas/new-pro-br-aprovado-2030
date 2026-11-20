import type {
  AccountType,
  AdminCreateUserPayload,
  AdminHealthResponse,
  AdminUpdateUserPayload,
  AdminUserItem,
  BotConfigData,
  BotConfigOverviewResponse,
  BotConfigUpdatePayload,
  BotEventLogItem,
  BtcRadarState,
  DerivAccount,
  DerivOAuthAccountInput,
  DerivOAuthUrlResponse,
  LoginResponse,
  MarketChartResponse,
  MarketSymbol,
  ProposalPreview,
  ReportsCyclesResponse,
  ReportsSummaryResponse,
  ScannerItem,
  User,
} from '../types/api';

const TOKEN_STORAGE_KEY = 'qcd_access_token';

export function getStoredToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setStoredToken(token: string | null): void {
  try {
    if (token) {
      localStorage.setItem(TOKEN_STORAGE_KEY, token);
    } else {
      localStorage.removeItem(TOKEN_STORAGE_KEY);
    }
  } catch {
    // ignore storage errors
  }
}

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

let refreshInFlight: Promise<string | null> | null = null;

async function attemptTokenRefresh(): Promise<string | null> {
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    try {
      const res = await fetch('/api/auth/refresh', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
      });
      if (!res.ok) {
        setStoredToken(null);
        return null;
      }
      const data = (await res.json()) as Partial<LoginResponse>;
      if (data.access_token) {
        setStoredToken(data.access_token);
        return data.access_token;
      }
      return null;
    } catch {
      return null;
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

async function parseErrorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === 'string') return body.detail;
    if (Array.isArray(body?.detail)) {
      return body.detail
        .map((e: { msg?: string; loc?: string[] }) => e.msg || JSON.stringify(e))
        .join('; ');
    }
    if (typeof body?.message === 'string') return body.message;
    if (typeof body?.error === 'string') return body.error;
  } catch {
    // fall through
  }
  return `Erro na requisição (${res.status})`;
}

interface RequestOptions extends RequestInit {
  skipAuthRetry?: boolean;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { skipAuthRetry = false, headers: customHeaders, ...rest } = options;
  const token = getStoredToken();

  const headers = new Headers(customHeaders);
  if (!headers.has('Content-Type') && rest.body && !(rest.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  let res = await fetch(path, {
    credentials: 'include',
    ...rest,
    headers,
  });

  if (res.status === 401 && !skipAuthRetry && !path.startsWith('/api/auth/login') && !path.startsWith('/api/auth/refresh')) {
    const newToken = await attemptTokenRefresh();
    if (newToken) {
      headers.set('Authorization', `Bearer ${newToken}`);
      res = await fetch(path, {
        credentials: 'include',
        ...rest,
        headers,
      });
    }
  }

  if (!res.ok) {
    const detail = await parseErrorDetail(res);
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) {
    return undefined as T;
  }

  const contentType = res.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    return (await res.json()) as T;
  }
  return (await res.text()) as unknown as T;
}

// ============================================================
// Auth API
// ============================================================
export const authApi = {
  login: (email: string, password: string) =>
    apiRequest<LoginResponse>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
      skipAuthRetry: true,
    }),
  refresh: () =>
    apiRequest<LoginResponse>('/api/auth/refresh', {
      method: 'POST',
      skipAuthRetry: true,
    }),
  logout: () =>
    apiRequest<{ ok?: boolean; detail?: string }>('/api/auth/logout', {
      method: 'POST',
      skipAuthRetry: true,
    }),
  me: () => apiRequest<User>('/api/auth/me'),
  changePassword: (current_password: string, new_password: string) =>
    apiRequest<{ detail?: string; message?: string }>('/api/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({ current_password, new_password }),
    }),
};

// ============================================================
// Deriv Connect API
// ============================================================
function unwrapList<T>(res: unknown, key: string): T[] {
  if (Array.isArray(res)) return res as T[];
  if (res && typeof res === 'object' && Array.isArray((res as Record<string, unknown>)[key])) {
    return (res as Record<string, unknown>)[key] as T[];
  }
  return [];
}

export const derivApi = {
  getOAuthUrl: () => apiRequest<DerivOAuthUrlResponse>('/api/deriv/oauth-url'),
  connectOAuth: async (accounts: DerivOAuthAccountInput[]) => {
    const res = await apiRequest<unknown>('/api/deriv/connect-oauth', {
      method: 'POST',
      body: JSON.stringify({ accounts }),
    });
    return unwrapList<DerivAccount>(res, 'accounts');
  },
  connectToken: async (token: string) => {
    const res = await apiRequest<unknown>('/api/deriv/connect-token', {
      method: 'POST',
      body: JSON.stringify({ token }),
    });
    return unwrapList<DerivAccount>(res, 'accounts');
  },
  getAccounts: async () => {
    const res = await apiRequest<unknown>('/api/deriv/accounts');
    return unwrapList<DerivAccount>(res, 'accounts');
  },
  selectAccount: (account_id: number, confirm_real_risk: boolean) =>
    apiRequest<DerivAccount | { account?: DerivAccount; detail?: string }>('/api/deriv/select-account', {
      method: 'POST',
      body: JSON.stringify({ account_id, confirm_real_risk }),
    }),
  deleteAccount: (account_id: number) =>
    apiRequest<{ detail?: string }>(`/api/deriv/accounts/${account_id}`, {
      method: 'DELETE',
    }),
  syncBalance: async () => {
    const res = await apiRequest<unknown>('/api/deriv/sync-balance', {
      method: 'POST',
    });
    return unwrapList<DerivAccount>(res, 'accounts');
  },
};

// ============================================================
// Market API
// ============================================================
export const marketApi = {
  getSymbols: async (): Promise<MarketSymbol[]> => {
    const res = await apiRequest<unknown>('/api/market/symbols');
    const rawList = unwrapList<Record<string, unknown>>(res, 'symbols');
    return rawList.map((s) => ({
      symbol: String(s.symbol ?? ''),
      display_name: String(s.display_name ?? s.symbol ?? ''),
      market: String(s.market ?? 'other'),
      market_display_name: String(s.market_display_name ?? s.category ?? s.market ?? ''),
      submarket_display_name: String(s.submarket_display_name ?? s.category ?? s.submarket ?? ''),
      is_open: Boolean(s.is_open ?? s.exchange_is_open ?? true),
      multipliers: Array.isArray(s.multipliers) ? (s.multipliers as number[]) : [100],
      min_stake: Number(s.min_stake ?? 1.0),
      pip_size: Number(s.pip_size ?? 2),
    }));
  },
  getProposalPreview: (params: {
    symbol: string;
    amount: number;
    multiplier: number;
    contract_type?: string;
  }) => {
    const qs = new URLSearchParams({
      symbol: params.symbol,
      amount: String(params.amount),
      multiplier: String(params.multiplier),
      contract_type: params.contract_type || 'MULTUP',
    });
    return apiRequest<ProposalPreview>(`/api/market/proposal-preview?${qs.toString()}`);
  },
  getChart: (symbol: string, granularity: number) => {
    const qs = new URLSearchParams({
      symbol,
      granularity: String(granularity),
    });
    return apiRequest<MarketChartResponse>(`/api/market/chart?${qs.toString()}`);
  },
  getScanner: (symbols?: string[], granularity: number = 300) => {
    const qs = new URLSearchParams();
    if (symbols && symbols.length > 0) {
      qs.set('symbols', symbols.join(','));
    }
    qs.set('granularity', String(granularity));
    return apiRequest<{ items: ScannerItem[]; count: number }>(`/api/market/scanner?${qs.toString()}`);
  },
};

// ============================================================
// Bot API
// ============================================================
export const botApi = {
  getConfig: () => apiRequest<BotConfigOverviewResponse>('/api/bot/config'),
  updateConfig: (payload: Partial<BotConfigUpdatePayload>) =>
    apiRequest<{ config: BotConfigData }>('/api/bot/config', {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  toggle: (enabled: boolean) =>
    apiRequest<{ enabled?: boolean; status_text?: string; detail?: string }>('/api/bot/toggle', {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    }),
  killSwitch: () =>
    apiRequest<{ closed_contracts?: number; detail?: string; message?: string }>('/api/bot/kill-switch', {
      method: 'POST',
    }),
  resetSession: () =>
    apiRequest<{ status: string; message: string; config: BotConfigData }>('/api/bot/reset-session', {
      method: 'POST',
    }),
  getEvents: async (limit = 50) => {
    const res = await apiRequest<unknown>(`/api/bot/events?limit=${limit}`);
    return unwrapList<BotEventLogItem>(res, 'events');
  },
};

// ============================================================
// Reports API
// ============================================================
export const reportsApi = {
  getSummary: async (account_type: AccountType, days: number, symbol = ''): Promise<ReportsSummaryResponse> => {
    const qs = new URLSearchParams({
      account_type,
      days: String(days),
      symbol,
    });
    const raw = await apiRequest<Record<string, unknown>>(`/api/reports/summary?${qs.toString()}`);
    const eqRaw = Array.isArray(raw.equity_curve) ? (raw.equity_curve as Record<string, unknown>[]) : [];
    const dailyRaw = Array.isArray(raw.daily_pnl) ? (raw.daily_pnl as Record<string, unknown>[]) : [];
    const layerRaw = Array.isArray(raw.layer_distribution)
      ? (raw.layer_distribution as Record<string, unknown>[])
      : Array.isArray(raw.layers_distribution)
      ? (raw.layers_distribution as Record<string, unknown>[])
      : [];
    const symRaw = Array.isArray(raw.by_symbol) ? (raw.by_symbol as Record<string, unknown>[]) : [];
    const totalCycles = Number(raw.total_cycles ?? 0);

    return {
      account_type: (raw.account_type as AccountType) ?? account_type,
      total_cycles: totalCycles,
      winning_cycles: Number(raw.winning_cycles ?? raw.win_cycles ?? 0),
      losing_cycles: Number(raw.losing_cycles ?? raw.loss_cycles ?? 0),
      win_rate: Number(raw.win_rate ?? raw.win_rate_pct ?? 0),
      total_pnl_usd: Number(raw.total_pnl_usd ?? raw.net_pnl_usd ?? 0),
      today_pnl_usd: Number(raw.today_pnl_usd ?? 0),
      today_pnl_pct: raw.today_pnl_pct !== undefined ? Number(raw.today_pnl_pct) : undefined,
      total_commission_usd: Number(raw.total_commission_usd ?? 0),
      profit_factor: Number(raw.profit_factor ?? 0),
      max_drawdown_usd: Number(raw.max_drawdown_usd ?? 0),
      max_drawdown_pct: raw.max_drawdown_pct !== undefined ? Number(raw.max_drawdown_pct) : undefined,
      expectancy_usd: Number(raw.expectancy_usd ?? raw.avg_pnl_usd ?? 0),
      equity_curve: eqRaw.map((p) => ({
        date: String(p.date ?? p.timestamp ?? ''),
        timestamp: String(p.timestamp ?? p.date ?? ''),
        equity: Number(p.equity ?? p.cumulative_pnl_usd ?? 0),
        pnl: Number(p.pnl ?? p.pnl_usd ?? 0),
        cumulative_pnl: Number(p.cumulative_pnl ?? p.cumulative_pnl_usd ?? 0),
      })),
      daily_pnl: dailyRaw.map((d) => ({
        date: String(d.date ?? ''),
        pnl: Number(d.pnl ?? d.pnl_usd ?? 0),
        cycles: Number(d.cycles ?? 0),
        wins: Number(d.wins ?? 0),
      })),
      layer_distribution: layerRaw.map((l) => {
        const cnt = Number(l.count ?? 0);
        return {
          layer: (l.layer ?? l.layers ?? 1) as number | string,
          count: cnt,
          pct: totalCycles > 0 ? Math.round((cnt / totalCycles) * 1000) / 10 : 0,
        };
      }),
      by_symbol: symRaw.map((s) => ({
        symbol: String(s.symbol ?? ''),
        cycles: Number(s.cycles ?? 0),
        wins: Number(s.wins ?? 0),
        win_rate: Number(s.win_rate ?? s.win_rate_pct ?? 0),
        total_pnl: Number(s.total_pnl ?? s.total_pnl_usd ?? 0),
      })),
    };
  },
  getCycles: (params: {
    account_type: AccountType;
    days: number;
    symbol?: string;
    page?: number;
    page_size?: number;
  }) => {
    const qs = new URLSearchParams({
      account_type: params.account_type,
      days: String(params.days),
      symbol: params.symbol || '',
      page: String(params.page ?? 1),
      page_size: String(params.page_size ?? 25),
    });
    return apiRequest<ReportsCyclesResponse>(`/api/reports/cycles?${qs.toString()}`);
  },
  exportCsv: async (account_type: AccountType, days: number, symbol = '') => {
    const qs = new URLSearchParams({
      account_type,
      days: String(days),
      symbol,
    });
    const token = getStoredToken();
    const res = await fetch(`/api/reports/export.csv?${qs.toString()}`, {
      credentials: 'include',
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    });
    if (!res.ok) {
      throw new ApiError(res.status, await parseErrorDetail(res));
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `relatorio_${account_type.toLowerCase()}_${days}d${symbol ? `_${symbol}` : ''}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
};

// ============================================================
// Admin API
// ============================================================
export const adminApi = {
  listUsers: async (): Promise<AdminUserItem[]> => {
    const res = await apiRequest<unknown>('/api/admin/users');
    const rawList = unwrapList<Record<string, unknown>>(res, 'users');
    return rawList.map((u) => ({
      ...(u as unknown as AdminUserItem),
      bot_status_text: (u.bot_status_text ?? u.bot_status ?? null) as string | null,
    }));
  },
  createUser: (payload: AdminCreateUserPayload) =>
    apiRequest<AdminUserItem>('/api/admin/users', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateUser: (id: number, payload: AdminUpdateUserPayload) =>
    apiRequest<AdminUserItem>(`/api/admin/users/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  extendUser: (id: number, days: number) =>
    apiRequest<AdminUserItem>(`/api/admin/users/${id}/extend`, {
      method: 'POST',
      body: JSON.stringify({ days }),
    }),
  deleteUser: (id: number) =>
    apiRequest<{ detail?: string }>(`/api/admin/users/${id}`, {
      method: 'DELETE',
    }),
  getHealth: async (): Promise<AdminHealthResponse> => {
    const raw = await apiRequest<Record<string, unknown>>('/api/admin/health');
    const w = (raw.worker as Record<string, unknown>) || {};
    const hb = (w.heartbeat as Record<string, unknown>) || {};
    const stats = (raw.stats as Record<string, unknown>) || {};
    return {
      status: raw.status as string | undefined,
      postgres: raw.postgres as AdminHealthResponse['postgres'],
      redis: raw.redis as AdminHealthResponse['redis'],
      worker: {
        alive: Boolean(w.alive),
        last_heartbeat: (w.last_heartbeat ?? w.last_seen ?? hb.timestamp ?? null) as string | null,
        age_seconds: w.age_seconds !== undefined ? Number(w.age_seconds) : null,
        active_sessions: Number(w.active_sessions ?? hb.active_sessions ?? 0),
        tracked_symbols: (w.tracked_symbols ?? hb.tracked_symbols ?? []) as string[],
      },
      active_sessions: Number(w.active_sessions ?? hb.active_sessions ?? 0),
      total_users: Number(raw.total_users ?? stats.total_users ?? 0),
      active_bots: Number(raw.active_bots ?? stats.active_bots ?? 0),
      open_baskets: Number(raw.open_baskets ?? stats.open_baskets ?? 0),
      recent_errors: Array.isArray(raw.recent_errors) ? (raw.recent_errors as BotEventLogItem[]) : [],
    };
  },
};

// ============================================================
// BTC Radar API (Próxima Vela M1)
// ============================================================
export const btcRadarApi = {
  getState: () => apiRequest<BtcRadarState>('/api/btc-radar/state'),
  getTelemetry: () => apiRequest<Record<string, unknown>>('/api/btc-radar/telemetry'),
  getAudit120m: () => apiRequest<Record<string, unknown>>('/api/btc-radar/audit-120m'),
};
