import React, { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, Loader2, ShieldAlert } from 'lucide-react';
import { Modal } from './Modal';
import { formatUsd } from '../lib/format';
import type { DerivAccount } from '../types/api';

interface RealAccountModalProps {
  open: boolean;
  account: DerivAccount | null;
  onClose: () => void;
  onConfirm: (accountId: number) => Promise<void>;
}

export const RealAccountModal: React.FC<RealAccountModalProps> = ({
  open,
  account,
  onClose,
  onConfirm,
}) => {
  const [checked, setChecked] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) setChecked(false);
  }, [open, account?.id]);

  if (!account) return null;

  const handleConfirm = async () => {
    if (!checked) return;
    setSubmitting(true);
    try {
      await onConfirm(account.id);
      onClose();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Atenção: Ativar Conta REAL da Deriv"
      subtitle="Confirmação obrigatória de risco financeiro"
      maxWidthClass="max-w-lg"
      preventClose={submitting}
    >
      <div className="space-y-4">
        <div className="flex items-start gap-3 rounded-xl border border-amber-500/40 bg-amber-500/10 p-4">
          <AlertTriangle className="h-6 w-6 shrink-0 text-amber-400 mt-0.5" />
          <div className="space-y-1.5 text-sm text-amber-100">
            <p className="font-semibold">Você está alternando para dinheiro REAL.</p>
            <p className="text-xs text-amber-200/90 leading-relaxed">
              Contratos <strong>Multipliers</strong> envolvem alavancagem elevada, comissões de abertura da Deriv e risco real de perda integral do stake alocado em cada camada do cesto DCA. Resultados passados ou em conta DEMO não garantem lucros futuros.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3 rounded-xl border border-slate-800 bg-slate-950/70 p-4 text-xs">
          <div>
            <span className="text-slate-500 block">Conta selecionada</span>
            <span className="mt-1 inline-flex items-center gap-1.5 font-mono text-sm font-semibold text-emerald-300">
              <ShieldAlert className="h-4 w-4 text-emerald-400" />
              {account.loginid}
            </span>
          </div>
          <div>
            <span className="text-slate-500 block">Saldo atual</span>
            <span className="mt-1 block font-mono text-sm font-semibold text-slate-100">
              {formatUsd(account.balance, account.currency)}
            </span>
          </div>
        </div>

        <label className="flex items-start gap-3 rounded-xl border border-slate-800 bg-slate-900/60 p-3.5 cursor-pointer hover:border-slate-700 transition-colors">
          <input
            type="checkbox"
            checked={checked}
            onChange={(e) => setChecked(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-slate-600 bg-slate-950 text-emerald-500 focus:ring-emerald-500/40"
          />
          <span className="text-xs text-slate-300 leading-relaxed">
            Compreendo integralmente os riscos financeiros das operações automatizadas em <strong>CONTA REAL ({account.loginid})</strong>, revisei meus limites de stake, stop global e stop diário, e assumo total responsabilidade pelas ordens executadas.
          </span>
        </label>

        <div className="flex items-center justify-end gap-3 pt-1">
          <button type="button" onClick={onClose} disabled={submitting} className="btn-secondary">
            Manter Conta Atual
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={!checked || submitting}
            className="btn-primary"
          >
            {submitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Ativando...
              </>
            ) : (
              <>
                <CheckCircle2 className="h-4 w-4" />
                Confirmar e Ativar Conta REAL
              </>
            )}
          </button>
        </div>
      </div>
    </Modal>
  );
};
