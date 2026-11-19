import React from 'react';
import {
  Activity,
  Award,
  Layers,
  Scale,
  TrendingDown,
  TrendingUp,
  Wallet,
} from 'lucide-react';
import { formatPct, formatPrice, formatUsd } from '../lib/format';
import type {
  BasketItem,
  BotConfigData,
  DerivAccount,
  ReportsSummaryResponse,
} from '../types/api';

interface KpiCardsProps {
  activeAccount: DerivAccount | null;
  summary: ReportsSummaryResponse | null;
  openBasket: BasketItem | null;
  config: BotConfigData | null;
}

export const KpiCards: React.FC<KpiCardsProps> = ({
  activeAccount,
  summary,
  openBasket,
  config,
}) => {
  const currency = activeAccount?.currency || 'USD';
  const balance = activeAccount?.balance ?? 0;
  const todayPnl = summary?.today_pnl_usd ?? 0;
  const todayPct =
    summary?.today_pnl_pct ?? (balance > 0 ? (todayPnl / balance) * 100 : 0);
  const totalPnl = summary?.total_pnl_usd ?? 0;
  const winRate = summary?.win_rate ?? 0;
  const profitFactor = summary?.profit_factor ?? 0;
  const maxDd = summary?.max_drawdown_usd ?? 0;
  const maxLayers = config?.max_layers ?? 3;

  return (
    <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 xl:grid-cols-4">
      {/* Saldo Deriv */}
      <div className="glass-card p-4 flex flex-col justify-between">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-slate-400">Saldo Deriv</span>
          <div className="rounded-lg bg-cyan-500/15 p-1.5 text-cyan-400">
            <Wallet className="h-4 w-4" />
          </div>
        </div>
        <div className="mt-3">
          <div className="font-mono text-2xl font-bold tracking-tight text-slate-100 tabular-nums">
            {activeAccount ? formatUsd(balance, currency) : '—'}
          </div>
          <div className="mt-1 flex items-center gap-2 text-[11px] text-slate-400">
            {activeAccount ? (
              <>
                <span
                  className={`rounded px-1.5 py-0.5 font-mono font-semibold ${
                    activeAccount.account_type === 'REAL'
                      ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30'
                      : 'bg-sky-500/15 text-sky-300 border border-sky-500/30'
                  }`}
                >
                  {activeAccount.account_type}
                </span>
                <span className="font-mono text-slate-500">{activeAccount.loginid}</span>
              </>
            ) : (
              <span className="text-amber-400">Nenhuma conta conectada</span>
            )}
          </div>
        </div>
      </div>

      {/* Lucro Hoje */}
      <div className="glass-card p-4 flex flex-col justify-between">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-slate-400">Lucro Hoje</span>
          <div
            className={`rounded-lg p-1.5 ${
              todayPnl >= 0
                ? 'bg-emerald-500/15 text-emerald-400'
                : 'bg-rose-500/15 text-rose-400'
            }`}
          >
            {todayPnl >= 0 ? (
              <TrendingUp className="h-4 w-4" />
            ) : (
              <TrendingDown className="h-4 w-4" />
            )}
          </div>
        </div>
        <div className="mt-3">
          <div
            className={`font-mono text-2xl font-bold tracking-tight tabular-nums ${
              todayPnl > 0
                ? 'text-emerald-400'
                : todayPnl < 0
                  ? 'text-rose-400'
                  : 'text-slate-100'
            }`}
          >
            {formatUsd(todayPnl, currency, true)}
          </div>
          <div className="mt-1 flex items-center justify-between text-[11px] text-slate-400">
            <span>Variação diária</span>
            <span
              className={`font-mono font-semibold ${
                todayPct >= 0 ? 'text-emerald-400' : 'text-rose-400'
              }`}
            >
              {formatPct(todayPct, true)}
            </span>
          </div>
        </div>
      </div>

      {/* Lucro Total (30d) */}
      <div className="glass-card p-4 flex flex-col justify-between">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-slate-400">Lucro Acumulado (30d)</span>
          <div className="rounded-lg bg-emerald-500/15 p-1.5 text-emerald-400">
            <Activity className="h-4 w-4" />
          </div>
        </div>
        <div className="mt-3">
          <div
            className={`font-mono text-2xl font-bold tracking-tight tabular-nums ${
              totalPnl > 0
                ? 'text-emerald-400'
                : totalPnl < 0
                  ? 'text-rose-400'
                  : 'text-slate-100'
            }`}
          >
            {formatUsd(totalPnl, currency, true)}
          </div>
          <div className="mt-1 flex items-center justify-between text-[11px] text-slate-400">
            <span>Ciclos fechados</span>
            <span className="font-mono text-slate-300">
              {summary?.total_cycles ?? 0} ({summary?.winning_cycles ?? 0}W / {summary?.losing_cycles ?? 0}L)
            </span>
          </div>
        </div>
      </div>

      {/* Assertividade + Profit Factor + Max DD */}
      <div className="glass-card p-4 flex flex-col justify-between">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-slate-400">Assertividade • PF • DD</span>
          <div className="rounded-lg bg-amber-500/15 p-1.5 text-amber-400">
            <Award className="h-4 w-4" />
          </div>
        </div>
        <div className="mt-3 grid grid-cols-3 gap-2">
          <div>
            <div className="font-mono text-lg font-bold text-emerald-400 tabular-nums">
              {formatPct(winRate, false, 1)}
            </div>
            <div className="text-[10px] text-slate-500 uppercase">Winrate</div>
          </div>
          <div>
            <div className="font-mono text-lg font-bold text-cyan-300 tabular-nums flex items-center gap-1">
              <Scale className="h-3.5 w-3.5 text-cyan-500 hidden sm:inline" />
              {Number.isFinite(profitFactor) ? profitFactor.toFixed(2) : '0.00'}
            </div>
            <div className="text-[10px] text-slate-500 uppercase">Profit F.</div>
          </div>
          <div>
            <div className="font-mono text-lg font-bold text-rose-400 tabular-nums">
              -{formatUsd(Math.abs(maxDd), currency)}
            </div>
            <div className="text-[10px] text-slate-500 uppercase">Max DD</div>
          </div>
        </div>
      </div>

      {/* Active Basket Wide Strip */}
      <div className="glass-card p-4 sm:col-span-2 xl:col-span-4 border-slate-800/90">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-3">
            <div
              className={`flex h-10 w-10 items-center justify-center rounded-xl border ${
                openBasket
                  ? 'border-cyan-500/40 bg-cyan-500/15 text-cyan-300'
                  : 'border-slate-800 bg-slate-900 text-slate-500'
              }`}
            >
              <Layers className="h-5 w-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-slate-100">Cesto DCA Ativo</h3>
                {openBasket ? (
                  <span
                    className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${
                      openBasket.direction === 'MULTUP'
                        ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30'
                        : 'bg-rose-500/15 text-rose-300 border border-rose-500/30'
                    }`}
                  >
                    {openBasket.direction === 'MULTUP' ? 'COMPRA (MULTUP)' : 'VENDA (MULTDOWN)'}
                  </span>
                ) : (
                  <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[10px] font-medium text-slate-400">
                    Aguardando sinal confirmado
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-400">
                {openBasket
                  ? `Ativo ${openBasket.symbol} • Alvo líquido: ${formatUsd(config?.basket_tp_usd ?? 0, currency)} • Stop global: -${formatUsd(config?.max_basket_loss_usd ?? 0, currency)}`
                  : `Monitorando ${config?.symbol || '—'} em busca de toque na banda do canal + confirmação`}
              </p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 md:gap-6">
            <div>
              <span className="text-[10px] uppercase tracking-wider text-slate-500 block">Camadas</span>
              <span className="font-mono text-base font-bold text-slate-100">
                {openBasket ? openBasket.layers_count : 0} / {maxLayers}
              </span>
            </div>
            <div>
              <span className="text-[10px] uppercase tracking-wider text-slate-500 block">Preço Médio</span>
              <span className="font-mono text-base font-bold text-cyan-300 tabular-nums">
                {openBasket && openBasket.avg_entry_price > 0
                  ? formatPrice(openBasket.avg_entry_price)
                  : '—'}
              </span>
            </div>
            <div>
              <span className="text-[10px] uppercase tracking-wider text-slate-500 block">Stake Exposto</span>
              <span className="font-mono text-base font-bold text-slate-200 tabular-nums">
                {openBasket ? formatUsd(openBasket.total_stake_usd, currency) : formatUsd(0, currency)}
              </span>
            </div>
            <div>
              <span className="text-[10px] uppercase tracking-wider text-slate-500 block">PnL Líquido</span>
              <span
                className={`font-mono text-base font-bold tabular-nums ${
                  !openBasket
                    ? 'text-slate-400'
                    : openBasket.current_pnl_usd >= 0
                      ? 'text-emerald-400'
                      : 'text-rose-400'
                }`}
              >
                {openBasket ? formatUsd(openBasket.current_pnl_usd, currency, true) : formatUsd(0, currency)}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
