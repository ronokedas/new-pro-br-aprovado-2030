import React, { useEffect, useState } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  BarChart2,
  Check,
  CheckCircle2,
  Cpu,
  Flame,
  FlaskConical,
  HelpCircle,
  Layers,
  LineChart,
  Loader2,
  Play,
  RotateCcw,
  Shield,
  Sparkles,
  Trophy,
  X,
  Zap,
} from 'lucide-react';
import { ApiError, botApi } from '../lib/api';
import { formatUsd } from '../lib/format';
import type { BacktestReportResponse, BacktestResultItem } from '../types/api';

interface BacktestModalProps {
  open: boolean;
  onClose: () => void;
  onAppliedTop10?: (symbols: string[]) => void;
}

export const BacktestModal: React.FC<BacktestModalProps> = ({
  open,
  onClose,
  onAppliedTop10,
}) => {
  const [candleCount, setCandleCount] = useState<number>(1000);
  const [loading, setLoading] = useState<boolean>(false);
  const [report, setReport] = useState<BacktestReportResponse | null>(null);
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>([]);
  const [applying, setApplying] = useState<boolean>(false);
  const [filterCat, setFilterCat] = useState<'TODOS' | 'Forex' | 'Cripto' | 'Sintéticos'>('TODOS');
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Load latest backtest cache on open
  useEffect(() => {
    if (!open) return;
    botApi
      .getLatestBacktest()
      .then((rep) => {
        setReport(rep);
        setSelectedSymbols(rep.top10_symbols || []);
      })
      .catch(() => {
        // No backtest cached yet
      });
  }, [open]);

  if (!open) return null;

  const handleRunBacktest = async () => {
    setLoading(true);
    setFeedback(null);
    try {
      const res = await botApi.runBacktest(candleCount);
      setReport(res);
      setSelectedSymbols(res.top10_symbols || []);
      setFeedback({
        type: 'success',
        text: `Backtest concluído com sucesso! ${res.total_pairs_tested} pares auditados com ${res.candle_count} velas reais da Deriv cada.`,
      });
    } catch (err) {
      setFeedback({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Falha ao executar backtest real na Deriv.',
      });
    } finally {
      setLoading(false);
    }
  };

  const handleToggleSymbol = (sym: string) => {
    setSelectedSymbols((prev) =>
      prev.includes(sym) ? prev.filter((s) => s !== sym) : [...prev, sym]
    );
  };

  const handleApplyTop10 = async () => {
    if (selectedSymbols.length === 0) {
      setFeedback({ type: 'error', text: 'Selecione pelo menos 1 par para aplicar no robô.' });
      return;
    }
    setApplying(true);
    try {
      const res = await botApi.applyTop10(selectedSymbols);
      setFeedback({
        type: 'success',
        text: `Top ${res.active_indices.length} pares aplicados no robô! O monitor foi atualizado.`,
      });
      if (onAppliedTop10) {
        onAppliedTop10(res.active_indices);
      }
      setTimeout(() => {
        onClose();
      }, 1500);
    } catch (err) {
      setFeedback({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Erro ao aplicar Top 10 no robô.',
      });
    } finally {
      setApplying(false);
    }
  };

  const filteredResults = report?.results.filter(
    (r) => filterCat === 'TODOS' || r.category === filterCat
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-4 bg-slate-950/85 backdrop-blur-md animate-fade-in">
      <div className="relative w-full max-w-4xl rounded-2xl border border-slate-800 bg-slate-900/95 p-5 sm:p-6 shadow-2xl space-y-4 max-h-[92vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-800 pb-3.5">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-cyan-500/20 to-blue-600/20 border border-cyan-500/40 text-cyan-400 shadow-glow">
              <FlaskConical className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-base font-extrabold text-slate-100 flex items-center gap-2">
                Laboratório de Backtest Real Deriv
                <span className="rounded bg-cyan-500/20 px-2 py-0.5 text-[10px] font-mono text-cyan-300 border border-cyan-500/30">
                  TOP 10 MAIS ASSERTIVOS
                </span>
              </h2>
              <p className="text-xs text-slate-400">
                Auditoria 100% real com histórico de velas M1 da Deriv • Estratégia Katie Tutorials + Preço Médio Inteligente.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-200 transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Feedback Alert */}
        {feedback && (
          <div
            className={`flex items-center justify-between gap-2 rounded-xl border px-3.5 py-2.5 text-xs font-mono ${
              feedback.type === 'success'
                ? 'border-emerald-500/40 bg-emerald-950/40 text-emerald-200'
                : 'border-rose-500/40 bg-rose-950/40 text-rose-200'
            }`}
          >
            <div className="flex items-center gap-2">
              {feedback.type === 'success' ? (
                <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0" />
              ) : (
                <AlertTriangle className="h-4 w-4 text-rose-400 shrink-0" />
              )}
              <span>{feedback.text}</span>
            </div>
            <button
              onClick={() => setFeedback(null)}
              className="text-slate-400 hover:text-slate-200 text-xs px-1"
            >
              ✕
            </button>
          </div>
        )}

        {/* Action Bar / Controls */}
        <div className="flex flex-wrap items-center justify-between gap-3 bg-slate-950/70 p-3 rounded-xl border border-slate-800">
          <div className="flex items-center gap-3">
            <label className="text-xs font-semibold text-slate-300 flex items-center gap-1.5 font-mono">
              <Layers className="h-3.5 w-3.5 text-cyan-400" />
              Amostra de Velas M1:
            </label>
            <select
              value={candleCount}
              onChange={(e) => setCandleCount(Number(e.target.value))}
              disabled={loading}
              className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-mono font-bold text-slate-100 focus:border-cyan-500 focus:outline-none"
            >
              <option value={500}>500 Velas (~8.3 Horas Reais)</option>
              <option value={1000}>1.000 Velas (~16.6 Horas Reais)</option>
              <option value={2000}>2.000 Velas (~33.3 Horas Reais)</option>
            </select>
          </div>

          <button
            type="button"
            onClick={handleRunBacktest}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-cyan-500 to-blue-600 hover:from-cyan-400 hover:to-blue-500 text-slate-950 font-black text-xs px-5 py-2.5 shadow-glow transition-all disabled:opacity-50"
          >
            {loading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin text-slate-950" />
                <span>Auditando 20 Pares na Deriv WS...</span>
              </>
            ) : (
              <>
                <Play className="h-4 w-4 fill-current text-slate-950" />
                <span>Executar Backtest em Tempo Real</span>
              </>
            )}
          </button>
        </div>

        {/* Loading Spinner Screen */}
        {loading && (
          <div className="py-12 flex flex-col items-center justify-center gap-3 rounded-xl border border-cyan-500/30 bg-cyan-950/10 p-6 text-center">
            <Loader2 className="h-9 w-9 animate-spin text-cyan-400" />
            <div className="space-y-1">
              <h4 className="text-sm font-extrabold text-cyan-200">
                Baixando Velas M1 e Simulando Estratégia Katie Tutorials...
              </h4>
              <p className="text-xs text-slate-400 font-mono">
                Processando 20 pares (Forex, Cripto e Sintéticos) com médias móveis MA6, MA14, MA250, RSI 5 e Preço Médio 2x.
              </p>
            </div>
          </div>
        )}

        {/* Results Screen */}
        {!loading && report && (
          <div className="space-y-4">
            {/* KPI Cards: Best Pair, Avg Winrate, Tested Pool */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              {/* Best Pair */}
              <div className="glass-card p-3.5 border-emerald-500/40 bg-emerald-950/20 space-y-1">
                <span className="text-[10px] uppercase font-bold text-emerald-400 tracking-wider flex items-center gap-1">
                  <Trophy className="h-3.5 w-3.5" />
                  Melhor Par Encontrado
                </span>
                <div className="text-base font-extrabold text-slate-100 flex items-baseline justify-between">
                  <span>{report.best_pair?.name}</span>
                  <span className="text-emerald-400 font-mono font-black">
                    {report.best_pair?.winrate_pct.toFixed(1)}% Win
                  </span>
                </div>
                <div className="text-[11px] font-mono text-slate-400">
                  {report.best_pair?.trades_count} trades • PnL: +${report.best_pair?.total_pnl_usd.toFixed(2)} USD
                </div>
              </div>

              {/* Top 10 Average Winrate */}
              <div className="glass-card p-3.5 border-cyan-500/40 bg-cyan-950/20 space-y-1">
                <span className="text-[10px] uppercase font-bold text-cyan-400 tracking-wider flex items-center gap-1">
                  <Sparkles className="h-3.5 w-3.5" />
                  Assertividade Média do Top 10
                </span>
                <div className="text-2xl font-black font-mono text-cyan-300">
                  {report.avg_top10_winrate_pct.toFixed(1)}%
                </div>
                <div className="text-[11px] font-mono text-slate-400">
                  Calculado sobre os 10 pares mais assertivos
                </div>
              </div>

              {/* Total Sample */}
              <div className="glass-card p-3.5 space-y-1">
                <span className="text-[10px] uppercase font-bold text-slate-400 tracking-wider flex items-center gap-1">
                  <Cpu className="h-3.5 w-3.5" />
                  Amostra Histórica Auditada
                </span>
                <div className="text-2xl font-black font-mono text-slate-100">
                  {report.total_pairs_tested} Pares
                </div>
                <div className="text-[11px] font-mono text-slate-400">
                  {report.candle_count} velas M1 / par • {report.total_trades_simulated} trades reais
                </div>
              </div>
            </div>

            {/* Category Filter Tabs */}
            <div className="flex items-center justify-between gap-2 flex-wrap">
              <div className="flex items-center gap-1.5 p-1 bg-slate-950 rounded-lg border border-slate-800 text-[11px] font-mono">
                {(['TODOS', 'Forex', 'Cripto', 'Sintéticos'] as const).map((cat) => (
                  <button
                    key={cat}
                    type="button"
                    onClick={() => setFilterCat(cat)}
                    className={`px-3 py-1 rounded font-bold transition-all ${
                      filterCat === cat
                        ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 shadow-glow'
                        : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    {cat}
                  </button>
                ))}
              </div>

              <div className="text-xs font-mono text-slate-400">
                <strong className="text-cyan-300">{selectedSymbols.length}</strong> selecionado(s) para o robô
              </div>
            </div>

            {/* Ranking Table */}
            <div className="rounded-xl border border-slate-800 bg-slate-950/60 overflow-hidden">
              <div className="overflow-x-auto max-h-72 overflow-y-auto">
                <table className="w-full text-left text-xs font-mono">
                  <thead className="bg-slate-900/90 text-[10px] text-slate-400 uppercase sticky top-0 border-b border-slate-800 z-10 backdrop-blur-sm">
                    <tr>
                      <th className="px-3 py-2 text-center w-10">Ativar</th>
                      <th className="px-3 py-2 text-center w-12">Rank</th>
                      <th className="px-3 py-2">Ativo / Par</th>
                      <th className="px-3 py-2">Categoria</th>
                      <th className="px-3 py-2 text-center">Trades (W/L)</th>
                      <th className="px-3 py-2 text-right">Assertividade</th>
                      <th className="px-3 py-2 text-right">Lucro Líquido</th>
                      <th className="px-3 py-2 text-right">Drawdown</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60">
                    {filteredResults && filteredResults.length > 0 ? (
                      filteredResults.map((r) => {
                        const isSelected = selectedSymbols.includes(r.symbol);
                        const isTop = r.is_top10;
                        const isProfit = r.total_pnl_usd >= 0;

                        return (
                          <tr
                            key={r.symbol}
                            onClick={() => handleToggleSymbol(r.symbol)}
                            className={`cursor-pointer transition-colors hover:bg-slate-900/60 ${
                              isSelected ? 'bg-cyan-950/25' : ''
                            }`}
                          >
                            <td className="px-3 py-2.5 text-center" onClick={(e) => e.stopPropagation()}>
                              <input
                                type="checkbox"
                                checked={isSelected}
                                onChange={() => handleToggleSymbol(r.symbol)}
                                className="h-3.5 w-3.5 rounded border-slate-700 bg-slate-900 text-cyan-500 focus:ring-0 cursor-pointer"
                              />
                            </td>
                            <td className="px-3 py-2.5 text-center font-bold">
                              {r.rank && r.rank <= 3 ? (
                                <span className="inline-flex items-center justify-center h-5 w-5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/40 text-[10px]">
                                  #{r.rank}
                                </span>
                              ) : (
                                <span className="text-slate-500 text-[11px]">#{r.rank}</span>
                              )}
                            </td>
                            <td className="px-3 py-2.5">
                              <div className="flex items-center gap-1.5 flex-wrap">
                                <span className="font-bold text-slate-200">{r.name}</span>
                                <span className="text-[10px] text-slate-500">({r.symbol})</span>
                                {isTop && (
                                  <span className="rounded bg-emerald-500/20 px-1.5 py-0.2 text-[8px] font-bold text-emerald-300 border border-emerald-500/30">
                                    TOP 10
                                  </span>
                                )}
                              </div>
                            </td>
                            <td className="px-3 py-2.5">
                              <span
                                className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${
                                  r.category === 'Forex'
                                    ? 'bg-blue-500/20 text-blue-300 border border-blue-500/30'
                                    : r.category === 'Cripto'
                                    ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                                    : 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/30'
                                }`}
                              >
                                {r.category}
                              </span>
                            </td>
                            <td className="px-3 py-2.5 text-center">
                              <span className="font-bold text-slate-200">{r.trades_count}</span>{' '}
                              <span className="text-slate-500">
                                ({r.wins}W / {r.losses}L)
                              </span>
                            </td>
                            <td className="px-3 py-2.5 text-right">
                              <span
                                className={`font-black ${
                                  r.winrate_pct >= 60
                                    ? 'text-emerald-400'
                                    : r.winrate_pct >= 50
                                    ? 'text-amber-400'
                                    : 'text-rose-400'
                                }`}
                              >
                                {r.winrate_pct.toFixed(1)}%
                              </span>
                            </td>
                            <td className="px-3 py-2.5 text-right">
                              <span
                                className={`font-black ${
                                  isProfit ? 'text-emerald-400' : 'text-rose-400'
                                }`}
                              >
                                {isProfit ? `+$${r.total_pnl_usd.toFixed(2)}` : `-$${Math.abs(r.total_pnl_usd).toFixed(2)}`}
                              </span>
                            </td>
                            <td className="px-3 py-2.5 text-right text-slate-400">
                              -${r.max_drawdown_usd.toFixed(2)}
                            </td>
                          </tr>
                        );
                      })
                    ) : (
                      <tr>
                        <td colSpan={8} className="px-4 py-6 text-center text-xs text-slate-500">
                          Nenhum resultado disponível no filtro.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Bottom Actions */}
            <div className="flex items-center justify-between pt-2 border-t border-slate-800">
              <p className="text-[11px] text-slate-500">
                💡 O robô monitorará em paralelo os pares selecionados e abrirá ordens na confluência M1.
              </p>

              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={onClose}
                  className="rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 px-4 py-2 font-bold text-xs transition-colors"
                >
                  Fechar
                </button>
                <button
                  type="button"
                  onClick={handleApplyTop10}
                  disabled={applying || selectedSymbols.length === 0}
                  className="inline-flex items-center gap-2 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-black text-xs px-5 py-2.5 shadow-glow transition-all disabled:opacity-50"
                >
                  {applying ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin text-slate-950" />
                      <span>Salvando no Robô...</span>
                    </>
                  ) : (
                    <>
                      <Check className="h-4 w-4 stroke-[3]" />
                      <span>Ativar Top {selectedSymbols.length} no Robô Agora</span>
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Empty State before first backtest */}
        {!loading && !report && (
          <div className="py-12 flex flex-col items-center justify-center gap-3 text-center rounded-xl border border-dashed border-slate-800 p-8">
            <FlaskConical className="h-10 w-10 text-cyan-400/60" />
            <div className="space-y-1">
              <h4 className="text-sm font-bold text-slate-200">
                Nenhum Backtest Executado Nesta Sessão
              </h4>
              <p className="text-xs text-slate-400 max-w-md mx-auto">
                Clique no botão acima para conectar diretamente à API da Deriv, baixar 1.000 velas M1 de 20 pares candidatos e rankear automaticamente o Top 10 mais assertivo.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
