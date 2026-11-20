import React, { useCallback, useEffect, useState } from 'react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  AlertTriangle,
  BarChart3,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Download,
  Filter,
  Layers,
  Loader2,
  ShieldAlert,
  TrendingUp,
} from 'lucide-react';
import { ApiError, reportsApi } from '../lib/api';
import {
  formatDateTime,
  formatDuration,
  formatPct,
  formatPrice,
  formatUsd,
  translateCloseReason,
} from '../lib/format';
import type {
  AccountType,
  BasketItem,
  ReportsCyclesResponse,
  ReportsSummaryResponse,
} from '../types/api';

const PERIOD_PRESETS = [
  { label: 'Hoje', days: 1 },
  { label: '7 dias', days: 7 },
  { label: '30 dias', days: 30 },
  { label: '90 dias', days: 90 },
];

export const Reports: React.FC = () => {
  const [accountType, setAccountType] = useState<AccountType>('DEMO');
  const [days, setDays] = useState<number>(30);
  const [customDays, setCustomDays] = useState<string>('');
  const [symbolFilter, setSymbolFilter] = useState<string>('');
  const [page, setPage] = useState<number>(1);
  const pageSize = 25;

  const [summary, setSummary] = useState<ReportsSummaryResponse | null>(null);
  const [cyclesData, setCyclesData] = useState<ReportsCyclesResponse | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());

  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadReports = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [sumRes, cycRes] = await Promise.all([
        reportsApi.getSummary(accountType, days, symbolFilter.trim()),
        reportsApi.getCycles({
          account_type: accountType,
          days,
          symbol: symbolFilter.trim(),
          page,
          page_size: pageSize,
        }),
      ]);
      setSummary(sumRes);
      setCyclesData(cycRes);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Erro ao carregar relatórios.');
    } finally {
      setLoading(false);
    }
  }, [accountType, days, symbolFilter, page]);

  useEffect(() => {
    loadReports();
  }, [loadReports]);

  const handleAccountSwitch = (next: AccountType) => {
    setAccountType(next);
    setPage(1);
    setExpandedIds(new Set());
  };

  const handleApplyCustomDays = () => {
    const parsed = parseInt(customDays, 10);
    if (Number.isFinite(parsed) && parsed >= 1 && parsed <= 3650) {
      setDays(parsed);
      setPage(1);
    }
  };

  const handleExportCsv = async () => {
    setExporting(true);
    setError(null);
    try {
      await reportsApi.exportCsv(accountType, days, symbolFilter.trim());
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Falha ao exportar CSV.');
    } finally {
      setExporting(false);
    }
  };

  const toggleExpand = (id: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const equityData = (summary?.equity_curve || []).map((pt: any) => ({
    date: pt.date || pt.timestamp || '',
    equity: Number((pt.equity ?? pt.cumulative_pnl ?? pt.cumulative_pnl_usd ?? 0).toFixed(2)),
  }));

  const dailyPnlData = (summary?.daily_pnl || []).map((d: any) => ({
    date: d.date,
    pnl: Number((d.pnl ?? d.pnl_usd ?? 0).toFixed(2)),
  }));

  const rawLayers = (summary as any)?.layers_distribution || summary?.layer_distribution || [];
  const layerData = rawLayers.map((l: any) => ({
    layer: typeof (l.layer ?? l.layers) === 'number' ? `Camada ${l.layer ?? l.layers}` : String(l.layer ?? l.layers),
    count: l.count,
    pct: l.pct ?? 0,
  }));

  const bySymbol = summary?.by_symbol || [];
  const cycles: BasketItem[] = cyclesData?.items || [];
  const totalCycles = cyclesData?.total ?? 0;
  const totalPages = Math.max(1, cyclesData?.total_pages ?? Math.ceil(totalCycles / pageSize));

  return (
    <div className="space-y-6">
      {/* Header + Strict DEMO vs REAL Switcher */}
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-slate-100">
            Relatórios & Performance Quantitativa
          </h1>
          <p className="text-xs text-slate-400">
            Estatísticas estritamente isoladas entre conta <strong className="text-sky-300">DEMO</strong> e conta{' '}
            <strong className="text-emerald-300">REAL</strong> — nunca misturadas.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          {/* Strict DEMO vs REAL Tabs */}
          <div className="inline-flex rounded-xl border border-slate-800 bg-slate-900/90 p-1">
            <button
              type="button"
              onClick={() => handleAccountSwitch('DEMO')}
              className={`rounded-lg px-4 py-2 text-xs font-bold transition-all ${
                accountType === 'DEMO'
                  ? 'bg-sky-500 text-slate-950 shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              CONTA DEMO
            </button>
            <button
              type="button"
              onClick={() => handleAccountSwitch('REAL')}
              className={`rounded-lg px-4 py-2 text-xs font-bold transition-all ${
                accountType === 'REAL'
                  ? 'bg-emerald-500 text-slate-950 shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              CONTA REAL
            </button>
          </div>

          <button
            type="button"
            onClick={handleExportCsv}
            disabled={exporting}
            className="btn-secondary text-xs"
          >
            {exporting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Download className="h-4 w-4" />
            )}
            Exportar CSV ({accountType})
          </button>
        </div>
      </div>

      {/* Filters Bar */}
      <div className="glass-card p-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1.5 text-xs text-slate-400 mr-1">
            <Filter className="h-3.5 w-3.5" />
            Período:
          </span>
          {PERIOD_PRESETS.map((p) => (
            <button
              key={p.days}
              type="button"
              onClick={() => {
                setDays(p.days);
                setCustomDays('');
                setPage(1);
              }}
              className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
                days === p.days && !customDays
                  ? 'border-emerald-500/40 bg-emerald-500/15 text-emerald-300'
                  : 'border-slate-800 bg-slate-950/70 text-slate-400 hover:text-slate-200'
              }`}
            >
              {p.label}
            </button>
          ))}
          <div className="flex items-center gap-1.5">
            <input
              type="number"
              min={1}
              max={3650}
              value={customDays}
              onChange={(e) => setCustomDays(e.target.value)}
              placeholder="Dias..."
              className="w-20 rounded-lg border border-slate-800 bg-slate-950 px-2.5 py-1.5 font-mono text-xs text-slate-200 focus:border-emerald-500 focus:outline-none"
            />
            <button
              type="button"
              onClick={handleApplyCustomDays}
              className="rounded-lg border border-slate-700 bg-slate-800 px-2.5 py-1.5 text-xs text-slate-200 hover:bg-slate-700"
            >
              Aplicar
            </button>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <input
            type="text"
            value={symbolFilter}
            onChange={(e) => {
              setSymbolFilter(e.target.value);
              setPage(1);
            }}
            placeholder="Filtrar por par (ex.: cryBTCUSD)"
            className="input-dark py-1.5 text-xs font-mono w-full sm:w-56"
          />
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-xl border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-xs text-rose-200">
          <AlertTriangle className="h-4 w-4 shrink-0 text-rose-400" />
          <span>{error}</span>
        </div>
      )}

      {/* KPI Summary Strip */}
      <div className="grid grid-cols-2 gap-3.5 md:grid-cols-3 xl:grid-cols-6">
        <div className="glass-card p-4">
          <span className="text-[11px] text-slate-400 block">Lucro Líquido ({days}d)</span>
          <span
            className={`mt-1.5 block font-mono text-xl font-bold tabular-nums ${
              (summary?.total_pnl_usd ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'
            }`}
          >
            {formatUsd(summary?.total_pnl_usd ?? 0, 'USD', true)}
          </span>
        </div>

        <div className="glass-card p-4">
          <span className="text-[11px] text-slate-400 block">Assertividade (Ciclos)</span>
          <span className="mt-1.5 block font-mono text-xl font-bold text-emerald-300 tabular-nums">
            {formatPct(summary?.win_rate ?? 0, false, 1)}
          </span>
          <span className="text-[10px] font-mono text-slate-500">
            {summary?.winning_cycles ?? 0}W / {summary?.losing_cycles ?? 0}L ({summary?.total_cycles ?? 0})
          </span>
        </div>

        <div className="glass-card p-4">
          <span className="text-[11px] text-slate-400 block">Profit Factor</span>
          <span className="mt-1.5 block font-mono text-xl font-bold text-cyan-300 tabular-nums">
            {(summary?.profit_factor ?? 0).toFixed(2)}
          </span>
        </div>

        <div className="glass-card p-4">
          <span className="text-[11px] text-slate-400 block">Max Drawdown</span>
          <span className="mt-1.5 block font-mono text-xl font-bold text-rose-400 tabular-nums">
            -{formatUsd(Math.abs(summary?.max_drawdown_usd ?? 0))}
          </span>
        </div>

        <div className="glass-card p-4">
          <span className="text-[11px] text-slate-400 block">Comissões Deriv</span>
          <span className="mt-1.5 block font-mono text-xl font-bold text-amber-300 tabular-nums">
            {formatUsd(summary?.total_commission_usd ?? 0)}
          </span>
        </div>

        <div className="glass-card p-4">
          <span className="text-[11px] text-slate-400 block">Expectancy / Ciclo</span>
          <span
            className={`mt-1.5 block font-mono text-xl font-bold tabular-nums ${
              (summary?.expectancy_usd ?? 0) >= 0 ? 'text-emerald-300' : 'text-rose-300'
            }`}
          >
            {formatUsd(
              summary?.expectancy_usd ??
                ((summary?.total_cycles ?? 0) > 0
                  ? (summary?.total_pnl_usd ?? 0) / (summary?.total_cycles ?? 1)
                  : 0),
              'USD',
              true
            )}
          </span>
        </div>
      </div>

      {/* Charts Row 1: Equity Curve + Daily PnL */}
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        {/* Equity Curve */}
        <div className="glass-card p-5 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <TrendingUp className="h-4 w-4 text-emerald-400" />
              <h2 className="text-sm font-semibold text-slate-100">
                Curva de Patrimônio (Equity Curve — {accountType})
              </h2>
            </div>
          </div>

          <div className="h-[260px] w-full">
            {equityData.length === 0 ? (
              <div className="flex h-full items-center justify-center text-xs text-slate-500">
                Sem ciclos fechados neste período para conta {accountType}.
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={equityData} margin={{ top: 10, right: 12, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#10b981" stopOpacity={0.35} />
                      <stop offset="95%" stopColor="#10b981" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(51,65,85,0.35)" />
                  <XAxis dataKey="date" stroke="#64748b" fontSize={11} />
                  <YAxis
                    stroke="#64748b"
                    fontSize={11}
                    tickFormatter={(v) => `$${Number(v).toFixed(0)}`}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#0b1120',
                      borderColor: '#1e293b',
                      borderRadius: '12px',
                      fontSize: '12px',
                    }}
                    formatter={(value: number) => [formatUsd(value, 'USD', true), 'PnL Acumulado']}
                  />
                  <Area
                    type="monotone"
                    dataKey="equity"
                    stroke="#10b981"
                    strokeWidth={2}
                    fill="url(#equityGrad)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        {/* Daily PnL Bar Chart */}
        <div className="glass-card p-5 space-y-3">
          <div className="flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-cyan-400" />
            <h2 className="text-sm font-semibold text-slate-100">PnL Diário ({accountType})</h2>
          </div>

          <div className="h-[260px] w-full">
            {dailyPnlData.length === 0 ? (
              <div className="flex h-full items-center justify-center text-xs text-slate-500">
                Nenhum resultado diário registrado ainda.
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={dailyPnlData} margin={{ top: 10, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(51,65,85,0.35)" />
                  <XAxis dataKey="date" stroke="#64748b" fontSize={11} />
                  <YAxis
                    stroke="#64748b"
                    fontSize={11}
                    tickFormatter={(v) => `$${Number(v).toFixed(0)}`}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#0b1120',
                      borderColor: '#1e293b',
                      borderRadius: '12px',
                      fontSize: '12px',
                    }}
                    formatter={(value: number) => [formatUsd(value, 'USD', true), 'PnL do Dia']}
                  />
                  <Bar dataKey="pnl" radius={[6, 6, 0, 0]}>
                    {dailyPnlData.map((entry, idx) => (
                      <Cell
                        key={`cell-${idx}`}
                        fill={entry.pnl >= 0 ? '#10b981' : '#f43f5e'}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
      </div>

      {/* Charts Row 2: Layer Distribution + Winrate by Symbol */}
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        {/* Layer Distribution */}
        <div className="glass-card p-5 space-y-3">
          <div className="flex items-center gap-2">
            <Layers className="h-4 w-4 text-amber-400" />
            <h2 className="text-sm font-semibold text-slate-100">
              Distribuição de Camadas DCA Utilizadas
            </h2>
          </div>

          <div className="h-[220px] w-full">
            {layerData.length === 0 ? (
              <div className="flex h-full items-center justify-center text-xs text-slate-500">
                Sem distribuição de camadas disponível.
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={layerData} margin={{ top: 10, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(51,65,85,0.35)" />
                  <XAxis dataKey="layer" stroke="#64748b" fontSize={11} />
                  <YAxis stroke="#64748b" fontSize={11} allowDecimals={false} />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#0b1120',
                      borderColor: '#1e293b',
                      borderRadius: '12px',
                      fontSize: '12px',
                    }}
                    formatter={(value: number) => [`${value} ciclo(s)`, 'Frequência']}
                  />
                  <Bar dataKey="count" fill="#06b6d4" radius={[6, 6, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        {/* Winrate by Symbol */}
        <div className="glass-card p-5 space-y-3">
          <div className="flex items-center gap-2">
            <ShieldAlert className="h-4 w-4 text-emerald-400" />
            <h2 className="text-sm font-semibold text-slate-100">Assertividade por Ativo / Par</h2>
          </div>

          {bySymbol.length === 0 ? (
            <div className="flex h-[220px] items-center justify-center text-xs text-slate-500">
              Nenhum ativo operado neste período.
            </div>
          ) : (
            <div className="space-y-3 max-h-[220px] overflow-y-auto pr-1">
              {bySymbol.map((item) => (
                <div
                  key={item.symbol}
                  className="rounded-xl border border-slate-800/80 bg-slate-950/60 p-3 space-y-2"
                >
                  <div className="flex items-center justify-between text-xs">
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-bold text-slate-100">{item.symbol}</span>
                      <span className="text-slate-500">
                        {item.cycles} ciclo{item.cycles === 1 ? '' : 's'} ({item.wins}W)
                      </span>
                    </div>
                    <div className="flex items-center gap-3 font-mono">
                      <span className="text-emerald-300 font-semibold">
                        {formatPct((item as any).win_rate ?? (item as any).win_rate_pct ?? 0, false, 1)}
                      </span>
                      <span
                        className={`font-semibold ${
                          ((item as any).total_pnl ?? (item as any).total_pnl_usd ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'
                        }`}
                      >
                        {formatUsd((item as any).total_pnl ?? (item as any).total_pnl_usd ?? 0, 'USD', true)}
                      </span>
                    </div>
                  </div>
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-emerald-400"
                      style={{ width: `${Math.min(100, Math.max(0, (item as any).win_rate ?? (item as any).win_rate_pct ?? 0))}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Paginated Cycles Table with Expandable Contract Layers */}
      <div className="glass-card overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/80 px-5 py-3.5">
          <div>
            <h2 className="text-sm font-semibold text-slate-100">
              Histórico Detalhado de Ciclos ({accountType})
            </h2>
            <p className="text-[11px] text-slate-400">
              Clique em qualquer ciclo para expandir as camadas individuais (contratos Deriv)
            </p>
          </div>
          <span className="font-mono text-xs text-slate-400">
            Total: {totalCycles} ciclo{totalCycles === 1 ? '' : 's'}
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-slate-800/80 bg-slate-950/70 text-[11px] uppercase text-slate-500">
              <tr>
                <th className="px-4 py-3 w-8" />
                <th className="px-4 py-3">Abertura / Fechamento</th>
                <th className="px-4 py-3">Par / Direção</th>
                <th className="px-4 py-3">Camadas</th>
                <th className="px-4 py-3">Stake Total</th>
                <th className="px-4 py-3">Comissão</th>
                <th className="px-4 py-3">Motivo Saída</th>
                <th className="px-4 py-3 text-right">Lucro Líquido</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {loading ? (
                <tr>
                  <td colSpan={8} className="px-4 py-10 text-center text-slate-400">
                    <Loader2 className="mx-auto h-5 w-5 animate-spin text-cyan-400 mb-2" />
                    Carregando ciclos...
                  </td>
                </tr>
              ) : cycles.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-4 py-10 text-center text-slate-500">
                    Nenhum ciclo concluído encontrado para os filtros selecionados.
                  </td>
                </tr>
              ) : (
                cycles.map((cycle) => {
                  const isExpanded = expandedIds.has(cycle.id);
                  const reasonMeta = translateCloseReason(cycle.close_reason);
                  const pnl = cycle.status === 'CLOSED' ? cycle.realized_pnl_usd : cycle.current_pnl_usd;
                  const contracts = cycle.contracts || [];

                  return (
                    <React.Fragment key={cycle.id}>
                      <tr
                        onClick={() => toggleExpand(cycle.id)}
                        className="cursor-pointer hover:bg-slate-800/35 transition-colors"
                      >
                        <td className="px-4 py-3 text-slate-500">
                          {isExpanded ? (
                            <ChevronUp className="h-4 w-4 text-cyan-400" />
                          ) : (
                            <ChevronDown className="h-4 w-4" />
                          )}
                        </td>
                        <td className="px-4 py-3 font-mono text-[11px]">
                          <div className="text-slate-200">{formatDateTime(cycle.opened_at)}</div>
                          <div className="text-slate-500">
                            {cycle.closed_at
                              ? `${formatDateTime(cycle.closed_at)} (${formatDuration(cycle.opened_at, cycle.closed_at)})`
                              : 'Em aberto'}
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          <div className="font-mono font-semibold text-slate-100">{cycle.symbol}</div>
                          <span
                            className={`inline-block mt-0.5 text-[10px] font-mono ${
                              cycle.direction === 'MULTUP' ? 'text-emerald-400' : 'text-rose-400'
                            }`}
                          >
                            {cycle.direction}
                          </span>
                        </td>
                        <td className="px-4 py-3 font-mono text-slate-200">
                          {cycle.layers_count} camada{cycle.layers_count === 1 ? '' : 's'}
                        </td>
                        <td className="px-4 py-3 font-mono text-slate-200">
                          {formatUsd(cycle.total_stake_usd)}
                        </td>
                        <td className="px-4 py-3 font-mono text-slate-400">
                          {formatUsd(cycle.total_commission_usd)}
                        </td>
                        <td className="px-4 py-3">
                          <span
                            className={`inline-flex rounded-md border px-2 py-0.5 text-[10px] font-semibold ${reasonMeta.tone}`}
                          >
                            {reasonMeta.label}
                          </span>
                        </td>
                        <td
                          className={`px-4 py-3 text-right font-mono font-bold tabular-nums ${
                            pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
                          }`}
                        >
                          {formatUsd(pnl, 'USD', true)}
                        </td>
                      </tr>

                      {isExpanded && (
                        <tr className="bg-slate-950/75">
                          <td colSpan={8} className="px-6 py-4">
                            <div className="text-[11px] font-semibold text-cyan-300 mb-2">
                              Camadas do Cesto #{cycle.id} — Preço Médio: {formatPrice(cycle.avg_entry_price)}
                            </div>
                            {contracts.length === 0 ? (
                              <p className="text-xs text-slate-500">
                                Detalhes individuais de contratos indisponíveis para este ciclo.
                              </p>
                            ) : (
                              <div className="overflow-x-auto rounded-xl border border-slate-800">
                                <table className="w-full text-left font-mono text-[11px]">
                                  <thead className="bg-slate-900/90 text-slate-500 uppercase">
                                    <tr>
                                      <th className="px-3 py-2">Camada</th>
                                      <th className="px-3 py-2">Contract ID</th>
                                      <th className="px-3 py-2">Stake / Mult</th>
                                      <th className="px-3 py-2">Entrada</th>
                                      <th className="px-3 py-2">Saída</th>
                                      <th className="px-3 py-2">Comissão</th>
                                      <th className="px-3 py-2">Status</th>
                                      <th className="px-3 py-2 text-right">Lucro Final</th>
                                    </tr>
                                  </thead>
                                  <tbody className="divide-y divide-slate-800/60">
                                    {contracts.map((ct) => {
                                      const ctPnl = ct.final_profit_usd ?? ct.current_profit_usd ?? 0;
                                      return (
                                        <tr key={ct.id}>
                                          <td className="px-3 py-2 text-cyan-300">#{ct.layer_index}</td>
                                          <td className="px-3 py-2 text-slate-300">
                                            {ct.deriv_contract_id || `#${ct.id}`}
                                          </td>
                                          <td className="px-3 py-2 text-slate-200">
                                            {formatUsd(ct.stake_usd)} ({ct.multiplier}x)
                                          </td>
                                          <td className="px-3 py-2 text-slate-300">
                                            {formatPrice(ct.entry_spot)}
                                          </td>
                                          <td className="px-3 py-2 text-slate-300">
                                            {ct.exit_spot ? formatPrice(ct.exit_spot) : '—'}
                                          </td>
                                          <td className="px-3 py-2 text-slate-400">
                                            {formatUsd(ct.commission_usd)}
                                          </td>
                                          <td className="px-3 py-2 text-slate-300">{ct.status}</td>
                                          <td
                                            className={`px-3 py-2 text-right font-semibold ${
                                              ctPnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
                                            }`}
                                          >
                                            {formatUsd(ctPnl, 'USD', true)}
                                          </td>
                                        </tr>
                                      );
                                    })}
                                  </tbody>
                                </table>
                              </div>
                            )}
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        <div className="flex items-center justify-between border-t border-slate-800/80 px-5 py-3 text-xs text-slate-400">
          <span>
            Página <strong className="font-mono text-slate-200">{page}</strong> de{' '}
            <strong className="font-mono text-slate-200">{totalPages}</strong>
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={page <= 1 || loading}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="btn-secondary py-1.5 px-3 text-xs"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
              Anterior
            </button>
            <button
              type="button"
              disabled={page >= totalPages || loading}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              className="btn-secondary py-1.5 px-3 text-xs"
            >
              Próxima
              <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
