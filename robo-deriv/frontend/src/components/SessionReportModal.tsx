import React from 'react';
import {
  AlertOctagon,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  CheckCircle2,
  HelpCircle,
  Loader2,
  RotateCcw,
  ShieldAlert,
  Sparkles,
  Trophy,
  X,
} from 'lucide-react';
import { formatUsd } from '../lib/format';
import type { SessionReportResponse } from '../types/api';

interface SessionReportModalProps {
  open: boolean;
  onClose: () => void;
  onResetSession?: () => void;
  report: SessionReportResponse | null;
  loading?: boolean;
  currency?: string;
}

export const SessionReportModal: React.FC<SessionReportModalProps> = ({
  open,
  onClose,
  onResetSession,
  report,
  loading = false,
  currency = 'USD',
}) => {
  if (!open) return null;

  const isProfit = (report?.session_profit_usd ?? 0) >= 0;
  const worstPair = report?.worst_pair;
  const bestPair = report?.best_pair;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm animate-fade-in">
      <div className="relative w-full max-w-3xl rounded-2xl border border-slate-800 bg-slate-900/95 p-6 shadow-2xl space-y-5 max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-800 pb-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-cyan-500/10 border border-cyan-500/30 text-cyan-400">
              <BarChart3 className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-base font-extrabold text-slate-100 flex items-center gap-2">
                Relatório de Auditoria por Par
                <span className="rounded bg-cyan-500/20 px-2 py-0.5 text-[10px] font-mono text-cyan-300 border border-cyan-500/30">
                  NEXUS M1 MULTIPLIER
                </span>
              </h2>
              <p className="text-xs text-slate-400">
                Auditoria individual de pares para identificar qual ativo performou melhor ou puxou o Stop Loss.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200 transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {loading ? (
          <div className="py-12 flex flex-col items-center justify-center gap-3 text-slate-400">
            <Loader2 className="h-8 w-8 animate-spin text-cyan-400" />
            <span className="text-xs font-mono">Consolidando dados de auditoria...</span>
          </div>
        ) : !report ? (
          <div className="py-12 text-center text-xs text-slate-500 font-mono">
            Nenhum dado de sessão disponível.
          </div>
        ) : (
          <div className="space-y-5">
            {/* Session KPI Summary Banner */}
            <div
              className={`rounded-xl border p-4 flex flex-wrap items-center justify-between gap-4 ${
                report.is_stopped
                  ? 'border-rose-500/50 bg-rose-950/30 text-rose-200'
                  : isProfit
                  ? 'border-emerald-500/40 bg-emerald-950/20 text-emerald-200'
                  : 'border-slate-800 bg-slate-950/60 text-slate-200'
              }`}
            >
              <div>
                <div className="text-[10px] font-mono font-bold uppercase tracking-wider text-slate-400">
                  Resultado Consolidado da Sessão
                </div>
                <div className="text-2xl font-black font-mono">
                  <span className={isProfit ? 'text-emerald-400' : 'text-rose-400'}>
                    {isProfit ? `+${formatUsd(report.session_profit_usd, currency)}` : `-${formatUsd(Math.abs(report.session_profit_usd), currency)}`}
                  </span>
                </div>
                <div className="text-[11px] font-mono text-slate-400 mt-0.5">
                  Status:{' '}
                  <strong className={report.is_stopped ? 'text-rose-400' : 'text-emerald-400'}>
                    {report.stop_reason || (isProfit ? 'Dentro das metas normais' : 'Operando')}
                  </strong>
                </div>
              </div>

              <div className="flex items-center gap-6 text-xs font-mono">
                <div>
                  <span className="text-slate-500 block text-[10px] uppercase">Operações</span>
                  <strong className="text-slate-200 text-sm">
                    {report.total_trades} ({report.total_wins}W / {report.total_losses}L)
                  </strong>
                </div>
                <div>
                  <span className="text-slate-500 block text-[10px] uppercase">Taxa Assertividade</span>
                  <strong className={`text-sm ${report.winrate_pct >= 60 ? 'text-emerald-400' : report.winrate_pct >= 50 ? 'text-amber-400' : 'text-rose-400'}`}>
                    {report.winrate_pct.toFixed(1)}%
                  </strong>
                </div>
              </div>
            </div>

            {/* HIGHLIGHT CARDS: WORST PAIR VS BEST PAIR */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Worst Pair (O Vilão do Stop) */}
              <div className="rounded-xl border border-rose-500/50 bg-rose-950/20 p-4 space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <AlertOctagon className="h-4 w-4 text-rose-400" />
                    <span className="text-xs font-extrabold uppercase tracking-wide text-rose-300">
                      Pior Par da Sessão
                    </span>
                  </div>
                  <span className="rounded bg-rose-500/20 px-2 py-0.5 text-[9px] font-mono font-bold text-rose-300 border border-rose-500/30">
                    RECOMENDADO DESATIVAR
                  </span>
                </div>

                {worstPair ? (
                  <div>
                    <div className="flex items-baseline justify-between">
                      <span className="text-base font-bold text-slate-100">
                        {worstPair.name} <span className="text-xs font-mono text-slate-400">({worstPair.symbol})</span>
                      </span>
                      <span className="text-base font-mono font-black text-rose-400">
                        -${Math.abs(worstPair.total_pnl_usd).toFixed(2)} USD
                      </span>
                    </div>
                    <div className="flex items-center justify-between text-xs font-mono text-slate-400 mt-2 pt-2 border-t border-rose-950/60">
                      <span>{worstPair.trades} trades ({worstPair.wins}W / {worstPair.losses}L)</span>
                      <span className="font-bold text-rose-300">{worstPair.winrate_pct.toFixed(1)}% assertividade</span>
                    </div>
                  </div>
                ) : (
                  <div className="text-xs text-slate-500 font-mono py-2">
                    Nenhum par negativo registrado na sessão.
                  </div>
                )}
              </div>

              {/* Best Pair (O Mais Lucrativo) */}
              <div className="rounded-xl border border-emerald-500/50 bg-emerald-950/20 p-4 space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Trophy className="h-4 w-4 text-emerald-400" />
                    <span className="text-xs font-extrabold uppercase tracking-wide text-emerald-300">
                      Melhor Par da Sessão
                    </span>
                  </div>
                  <span className="rounded bg-emerald-500/20 px-2 py-0.5 text-[9px] font-mono font-bold text-emerald-300 border border-emerald-500/30">
                    ALTA PERFORMANCE
                  </span>
                </div>

                {bestPair ? (
                  <div>
                    <div className="flex items-baseline justify-between">
                      <span className="text-base font-bold text-slate-100">
                        {bestPair.name} <span className="text-xs font-mono text-slate-400">({bestPair.symbol})</span>
                      </span>
                      <span className={`text-base font-mono font-black ${bestPair.total_pnl_usd >= 0 ? 'text-emerald-400' : 'text-slate-300'}`}>
                        {bestPair.total_pnl_usd >= 0 ? `+` : `-`}${Math.abs(bestPair.total_pnl_usd).toFixed(2)} USD
                      </span>
                    </div>
                    <div className="flex items-center justify-between text-xs font-mono text-slate-400 mt-2 pt-2 border-t border-emerald-950/60">
                      <span>{bestPair.trades} trades ({bestPair.wins}W / {bestPair.losses}L)</span>
                      <span className="font-bold text-emerald-300">{bestPair.winrate_pct.toFixed(1)}% assertividade</span>
                    </div>
                  </div>
                ) : (
                  <div className="text-xs text-slate-500 font-mono py-2">
                    Nenhum trade finalizado ainda.
                  </div>
                )}
              </div>
            </div>

            {/* DETAILED PER-PAIR TABLE */}
            <div className="rounded-xl border border-slate-800 bg-slate-950/50 overflow-hidden">
              <div className="px-4 py-3 border-b border-slate-800 text-xs font-extrabold text-slate-200 uppercase tracking-wider flex items-center justify-between">
                <span>Tabela Individual por Par</span>
                <span className="text-[10px] font-mono text-slate-500">{report.pairs?.length || 0} Ativos Registrados</span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs font-mono">
                  <thead className="bg-slate-900/80 text-[10px] text-slate-400 uppercase border-b border-slate-800">
                    <tr>
                      <th className="px-4 py-2.5">Ativo / Símbolo</th>
                      <th className="px-3 py-2.5">Categoria</th>
                      <th className="px-3 py-2.5 text-center">Trades (W/L)</th>
                      <th className="px-3 py-2.5 text-right">Winrate</th>
                      <th className="px-4 py-2.5 text-right">Lucro Líquido</th>
                      <th className="px-4 py-2.5 text-center">Diagnóstico</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60">
                    {report.pairs && report.pairs.length > 0 ? (
                      report.pairs.map((p) => {
                        const isPairPositive = p.total_pnl_usd >= 0;
                        const isWorst = worstPair?.symbol === p.symbol;
                        const isBest = bestPair?.symbol === p.symbol;

                        return (
                          <tr
                            key={p.symbol}
                            className={`hover:bg-slate-900/50 transition-colors ${
                              isWorst
                                ? 'bg-rose-950/20'
                                : isBest
                                ? 'bg-emerald-950/15'
                                : ''
                            }`}
                          >
                            <td className="px-4 py-3">
                              <span className="font-bold text-slate-100 block">{p.name}</span>
                              <span className="text-[10px] text-slate-400">{p.symbol}</span>
                            </td>
                            <td className="px-3 py-3">
                              <span
                                className={`rounded px-2 py-0.5 text-[9px] font-bold ${
                                  p.category === 'Forex'
                                    ? 'bg-blue-500/20 text-blue-300 border border-blue-500/30'
                                    : p.category === 'Cripto'
                                    ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                                    : 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/30'
                                }`}
                              >
                                {p.category}
                              </span>
                            </td>
                            <td className="px-3 py-3 text-center">
                              <span className="text-slate-200 font-bold">{p.trades}</span>{' '}
                              <span className="text-slate-500">
                                ({p.wins}W / {p.losses}L)
                              </span>
                            </td>
                            <td className="px-3 py-3 text-right">
                              <span
                                className={`font-bold ${
                                  p.winrate_pct >= 60
                                    ? 'text-emerald-400'
                                    : p.winrate_pct >= 50
                                    ? 'text-amber-400'
                                    : 'text-rose-400'
                                }`}
                              >
                                {p.winrate_pct.toFixed(1)}%
                              </span>
                            </td>
                            <td className="px-4 py-3 text-right">
                              <span
                                className={`font-extrabold ${
                                  isPairPositive ? 'text-emerald-400' : 'text-rose-400'
                                }`}
                              >
                                {isPairPositive ? `+$${p.total_pnl_usd.toFixed(2)}` : `-$${Math.abs(p.total_pnl_usd).toFixed(2)}`}
                              </span>
                            </td>
                            <td className="px-4 py-3 text-center">
                              {isWorst ? (
                                <span className="inline-flex items-center gap-1 text-[10px] text-rose-400 font-bold">
                                  <AlertTriangle className="h-3 w-3" /> Pior Par
                                </span>
                              ) : isBest && p.total_pnl_usd > 0 ? (
                                <span className="inline-flex items-center gap-1 text-[10px] text-emerald-400 font-bold">
                                  <Trophy className="h-3 w-3" /> Mais Lucrativo
                                </span>
                              ) : p.winrate_pct >= 50 ? (
                                <span className="inline-flex items-center gap-1 text-[10px] text-cyan-400">
                                  <CheckCircle2 className="h-3 w-3" /> Saudável
                                </span>
                              ) : (
                                <span className="inline-flex items-center gap-1 text-[10px] text-slate-400">
                                  Neutro
                                </span>
                              )}
                            </td>
                          </tr>
                        );
                      })
                    ) : (
                      <tr>
                        <td colSpan={6} className="px-4 py-6 text-center text-xs text-slate-500 font-mono">
                          Nenhum par registrado na sessão.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Footer Buttons */}
            <div className="flex items-center justify-between pt-2 border-t border-slate-800">
              <p className="text-[11px] text-slate-500">
                Dica: Desmarque no monitor da tela principal os pares com taxas de vitória abaixo de 50%.
              </p>
              <div className="flex items-center gap-3">
                {onResetSession && (
                  <button
                    type="button"
                    onClick={() => {
                      onResetSession();
                      onClose();
                    }}
                    className="inline-flex items-center gap-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 px-4 py-2 font-bold text-xs transition-colors border border-slate-700"
                  >
                    <RotateCcw className="h-3.5 w-3.5" />
                    Resetar Sessão Atual
                  </button>
                )}
                <button
                  type="button"
                  onClick={onClose}
                  className="rounded-xl bg-cyan-500 hover:bg-cyan-400 text-slate-950 px-5 py-2 font-bold text-xs transition-colors"
                >
                  Fechar
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
