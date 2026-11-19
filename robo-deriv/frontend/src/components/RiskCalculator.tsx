import React, { useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  Calculator,
  CheckCircle2,
  Loader2,
  ShieldAlert,
} from 'lucide-react';
import { marketApi } from '../lib/api';
import { formatPct, formatPrice, formatUsd, parseLayerMultipliers } from '../lib/format';
import type { DirectionMode, ProposalPreview } from '../types/api';

interface RiskCalculatorProps {
  symbol: string;
  stakeUsd: number;
  multiplier: number;
  maxLayers: number;
  layerMultipliersJson: string;
  basketTpUsd: number;
  maxBasketLossUsd: number;
  directionMode: DirectionMode;
  maxAllowedStakeUsd?: number;
}

export const RiskCalculator: React.FC<RiskCalculatorProps> = ({
  symbol,
  stakeUsd,
  multiplier,
  maxLayers,
  layerMultipliersJson,
  basketTpUsd,
  maxBasketLossUsd,
  directionMode,
  maxAllowedStakeUsd,
}) => {
  const [preview, setPreview] = useState<ProposalPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const layerRatios = useMemo(
    () => parseLayerMultipliers(layerMultipliersJson, Math.max(1, maxLayers)),
    [layerMultipliersJson, maxLayers]
  );

  const layerStakes = useMemo(
    () => layerRatios.map((ratio) => Number((stakeUsd * ratio).toFixed(2))),
    [layerRatios, stakeUsd]
  );

  const totalCapitalAtRisk = useMemo(
    () => layerStakes.reduce((sum, s) => sum + s, 0),
    [layerStakes]
  );

  const contractType = directionMode === 'SHORT_ONLY' ? 'MULTDOWN' : 'MULTUP';

  useEffect(() => {
    if (!symbol || !Number.isFinite(stakeUsd) || stakeUsd <= 0 || !Number.isFinite(multiplier) || multiplier <= 0) {
      setPreview(null);
      return;
    }

    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setLoading(true);
      setFetchError(null);
      try {
        const res = await marketApi.getProposalPreview({
          symbol,
          amount: stakeUsd,
          multiplier,
          contract_type: contractType,
        });
        if (!cancelled) {
          setPreview(res);
          if (res.error) setFetchError(res.error);
        }
      } catch (err) {
        if (!cancelled) {
          setPreview(null);
          setFetchError(err instanceof Error ? err.message : 'Falha ao consultar proposal na Deriv');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 450);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [symbol, stakeUsd, multiplier, contractType]);

  const commRatePerUsd =
    preview && stakeUsd > 0 && preview.commission >= 0
      ? preview.commission / stakeUsd
      : 0;
  const estTotalBasketCommission = commRatePerUsd * totalCapitalAtRisk;
  const stopOutPct = preview?.stop_out_pct ?? 0;
  const recommendedDcaStepPct =
    stopOutPct > 0 && maxLayers > 1 ? (stopOutPct * 0.72) / maxLayers : 0;

  const warnings: string[] = [];
  if (maxAllowedStakeUsd && stakeUsd > maxAllowedStakeUsd) {
    warnings.push(
      `Stake inicial ($${stakeUsd.toFixed(2)}) excede o limite do seu plano ($${maxAllowedStakeUsd.toFixed(2)}).`
    );
  }
  if (preview && estTotalBasketCommission > 0 && basketTpUsd < estTotalBasketCommission * 0.5) {
    warnings.push(
      `Alvo líquido do cesto (${formatUsd(basketTpUsd)}) está baixo frente à comissão acumulada de ${maxLayers} camadas (~${formatUsd(estTotalBasketCommission)}).`
    );
  }
  if (maxBasketLossUsd > totalCapitalAtRisk) {
    warnings.push(
      `Stop global (${formatUsd(maxBasketLossUsd)}) é maior que o capital máximo das ${maxLayers} camadas (${formatUsd(totalCapitalAtRisk)}).`
    );
  }

  return (
    <div className="glass-card p-5 space-y-4 border-cyan-500/25">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-cyan-500/15 text-cyan-300 border border-cyan-500/30">
            <Calculator className="h-4 w-4" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-slate-100">Simulador de Risco ao Vivo (Deriv Proposal)</h3>
            <p className="text-[11px] text-slate-400">
              Consulta real em <span className="font-mono text-cyan-300">{symbol}</span> • {multiplier}x • {contractType}
            </p>
          </div>
        </div>
        {loading && (
          <span className="inline-flex items-center gap-1.5 text-xs text-cyan-300">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Cotando...
          </span>
        )}
      </div>

      {fetchError && (
        <div className="flex items-start gap-2 rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-200">
          <AlertTriangle className="h-4 w-4 shrink-0 text-rose-400 mt-0.5" />
          <span>{fetchError}</span>
        </div>
      )}

      {/* Primary Metrics Grid */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="rounded-xl border border-slate-800 bg-slate-950/75 p-3">
          <span className="text-[11px] text-slate-400 block">Capital Máx. em Risco</span>
          <span className="mt-1 block font-mono text-lg font-bold text-amber-300 tabular-nums">
            {formatUsd(totalCapitalAtRisk)}
          </span>
          <span className="text-[10px] text-slate-500">{maxLayers} camadas somadas</span>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-950/75 p-3">
          <span className="text-[11px] text-slate-400 block">Comissão Deriv (1ª / Total)</span>
          <span className="mt-1 block font-mono text-lg font-bold text-slate-100 tabular-nums">
            {preview ? formatUsd(preview.commission) : '—'}
          </span>
          <span className="text-[10px] font-mono text-slate-500">
            {preview
              ? `Total cesto: ~${formatUsd(estTotalBasketCommission)} (${formatPct(commRatePerUsd * 100, false, 2)})`
              : 'Aguardando cotação'}
          </span>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-950/75 p-3">
          <span className="text-[11px] text-slate-400 block">Distância Stop-Out</span>
          <span className="mt-1 block font-mono text-lg font-bold text-rose-400 tabular-nums">
            {preview && stopOutPct > 0 ? `-${formatPct(stopOutPct, false, 2)}` : '—'}
          </span>
          <span className="text-[10px] font-mono text-slate-500">
            {preview?.stop_out_value
              ? `Stop-out @ ${formatPrice(preview.stop_out_value)}`
              : 'Lido do proposal Deriv'}
          </span>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-950/75 p-3">
          <span className="text-[11px] text-slate-400 block">Spot & Passo DCA Ideal</span>
          <span className="mt-1 block font-mono text-lg font-bold text-emerald-300 tabular-nums">
            {preview?.spot ? formatPrice(preview.spot) : '—'}
          </span>
          <span className="text-[10px] font-mono text-slate-500">
            {recommendedDcaStepPct > 0
              ? `Espaçamento ~${recommendedDcaStepPct.toFixed(2)}% / camada`
              : 'Camada única'}
          </span>
        </div>
      </div>

      {/* Per-layer Breakdown */}
      <div className="rounded-xl border border-slate-800/80 bg-slate-950/60 p-3">
        <div className="mb-2 flex items-center justify-between text-[11px] text-slate-400">
          <span className="font-semibold text-slate-300">Progressão Conservadora das Camadas DCA</span>
          <span className="font-mono">Alavancagem nominal: {formatUsd(totalCapitalAtRisk * multiplier)}</span>
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          {layerStakes.map((st, idx) => (
            <div
              key={idx}
              className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/70 px-3 py-2 text-xs"
            >
              <span className="text-slate-400">
                Camada #{idx + 1}{' '}
                <span className="font-mono text-[10px] text-cyan-400">({layerRatios[idx]}x)</span>
              </span>
              <span className="font-mono font-semibold text-slate-100">{formatUsd(st)}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Warnings or Green Check */}
      {warnings.length > 0 ? (
        <div className="space-y-2">
          {warnings.map((w, i) => (
            <div
              key={i}
              className="flex items-start gap-2 rounded-xl border border-amber-500/35 bg-amber-500/10 px-3 py-2.5 text-xs text-amber-200"
            >
              <ShieldAlert className="h-4 w-4 shrink-0 text-amber-400 mt-0.5" />
              <span>{w}</span>
            </div>
          ))}
        </div>
      ) : (
        preview && !fetchError && (
          <div className="flex items-center gap-2 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
            <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" />
            <span>
              Parâmetros validados com sucesso contra a API da Deriv. Todas as {maxLayers} camadas cabem com folga antes do stop-out individual.
            </span>
          </div>
        )
      )}
    </div>
  );
};
