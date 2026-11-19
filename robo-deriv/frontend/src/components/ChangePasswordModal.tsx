import React, { useState } from 'react';
import { KeyRound, Loader2, Lock } from 'lucide-react';
import { Modal } from './Modal';
import { useAuth } from '../hooks/useAuth';
import { ApiError } from '../lib/api';

interface ChangePasswordModalProps {
  open: boolean;
  mandatory?: boolean;
  onClose: () => void;
}

export const ChangePasswordModal: React.FC<ChangePasswordModalProps> = ({
  open,
  mandatory = false,
  onClose,
}) => {
  const { changePassword } = useAuth();
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccess(null);

    if (newPassword.length < 8) {
      setError('A nova senha deve ter pelo menos 8 caracteres.');
      return;
    }
    if (newPassword !== confirmPassword) {
      setError('A confirmação de senha não confere.');
      return;
    }

    setSubmitting(true);
    try {
      await changePassword(currentPassword, newPassword);
      setSuccess('Senha atualizada com sucesso!');
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
      setTimeout(() => {
        setSuccess(null);
        onClose();
      }, 900);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Não foi possível alterar a senha.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={mandatory ? 'Troca Obrigatória de Senha' : 'Alterar Senha de Acesso'}
      subtitle={
        mandatory
          ? 'Por segurança, defina uma nova senha pessoal antes de continuar usando a plataforma.'
          : 'Atualize sua senha de acesso ao terminal.'
      }
      maxWidthClass="max-w-md"
      preventClose={mandatory || submitting}
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && (
          <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 px-3.5 py-2.5 text-xs text-rose-200">
            {error}
          </div>
        )}
        {success && (
          <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/10 px-3.5 py-2.5 text-xs text-emerald-200">
            {success}
          </div>
        )}

        <div>
          <label className="mb-1.5 block text-xs font-medium text-slate-300">Senha atual</label>
          <div className="relative">
            <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
            <input
              type="password"
              required
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              className="input-dark pl-9"
              placeholder="Digite sua senha atual"
            />
          </div>
        </div>

        <div>
          <label className="mb-1.5 block text-xs font-medium text-slate-300">Nova senha</label>
          <div className="relative">
            <KeyRound className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
            <input
              type="password"
              required
              minLength={8}
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="input-dark pl-9"
              placeholder="Mínimo 8 caracteres"
            />
          </div>
        </div>

        <div>
          <label className="mb-1.5 block text-xs font-medium text-slate-300">Confirmar nova senha</label>
          <div className="relative">
            <KeyRound className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
            <input
              type="password"
              required
              minLength={8}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="input-dark pl-9"
              placeholder="Repita a nova senha"
            />
          </div>
        </div>

        <div className="flex items-center justify-end gap-3 pt-2">
          {!mandatory && (
            <button type="button" onClick={onClose} disabled={submitting} className="btn-secondary">
              Cancelar
            </button>
          )}
          <button type="submit" disabled={submitting} className="btn-primary">
            {submitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Salvando...
              </>
            ) : (
              'Atualizar Senha'
            )}
          </button>
        </div>
      </form>
    </Modal>
  );
};
