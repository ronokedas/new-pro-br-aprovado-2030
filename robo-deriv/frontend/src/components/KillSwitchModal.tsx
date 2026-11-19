import React, { useState } from 'react';
import { AlertOctagon, Loader2, ShieldAlert } from 'lucide-react';
import { Modal } from './Modal';
import { formatUsd } from '../lib/format';
import type { BasketItem } from '../types/api';

interface KillSwitchModalProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => Promise<void>;
  openBasket: BasketItem | null;
  currency?: string;
}

export const KillSwitchModal: React.FC<KillSwitchModalProps> = ({
  open,
  onClose,
  onConfirm,
  openBasket,
  currency = 'USD',
}) => {
  const [submitting, setSubmitting] = useState(false);

  const handleConfirm = async () => {
    setSubmitting(true);
    try {
      await onConfirm();
      onClose();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Acionar Kill Switch de Emergência"
      subtitle="Ação imediata e irreversível no motor quantitativo"
      maxWidthClass="max-w-md"
      preventClose={submitting}
    >
      <div className="space-y-4">
        <div className="flex items-start gap-3 rounded-xl border border-rose-500/40 bg-rose-500/10 p-4">
          <AlertOctagon className="h-6 w-6 shrink-0 text-rose-400 mt-0.5" />
          <div className="text-sm text-rose-100 space-y-1.5">
            <p className="font-semibold">O que acontecerá imediatamente:</p>
            <ul className="list-disc pl-4 space-y-1 text-xs text-rose-200/90">
              <li>
                Todos os contratos abertos do seu cesto na Deriv serão vendidos a mercado (
                <code className="font-mono">sell price=0</code>).
              </li>
              <li>O robô será desligado automaticamente e novas entradas serão bloqueadas.</li>
              <li>Um registro de auditoria será gravado no histórico de ciclos.</li>
            </ul>
          </div>
        </div>

        {openBasket ? (
          <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-3.5 text-xs space-y-2">
            <div className="flex items-center justify-between text-slate-400">
              <span>Cesto aberto atual</span>
              <span className="font-mono font-semibold text-slate-200">
                {openBasket.symbol} ({openBasket.layers_count} camada{openBasket.layers_count === 1 ? '' : 's'})
              </span>
            </div>
            <div className="flex items-center justify-between text-slate-400">
              <span>Stake total exposto</span>
              <span className="font-mono text-slate-200">
                {formatUsd(openBasket.total_stake_usd, currency)}
              </span>
            </div>
            <div className="flex items-center justify-between text-slate-400">
              <span>PnL líquido estimado agora</span>
              <span
                className={`font-mono font-semibold ${
                  openBasket.current_pnl_usd >= 0 ? 'text-emerald-400' : 'text-rose-400'
                }`}
              >
                {formatUsd(openBasket.current_pnl_usd, currency, true)}
              </span>
            </div>
          </div>
        ) : (
          <div className="flex items-center gap-2 rounded-xl border border-slate-800 bg-slate-950/60 px-3.5 py-3 text-xs text-slate-400">
            <ShieldAlert className="h-4 w-4 text-amber-400 shrink-0" />
            <span>Nenhum cesto aberto no momento — o Kill Switch desligará o robô e encerrará qualquer posição pendente na Deriv.</span>
          </div>
        )}

        <div className="flex items-center justify-end gap-3 pt-2">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="btn-secondary"
          >
            Cancelar
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={submitting}
            className="btn-danger"
          >
            {submitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Encerrando tudo...
              </>
            ) : (
              <>
                <AlertOctagon className="h-4 w-4" />
                Confirmar Kill Switch
              </>
            )}
          </button>
        </div>
      </div>
    </Modal>
  );
};
