import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertTriangle, CheckCircle2, Loader2 } from 'lucide-react';
import { ApiError, derivApi } from '../lib/api';
import type { DerivOAuthAccountInput } from '../types/api';

function parseDerivOAuthAccounts(search: string): DerivOAuthAccountInput[] {
  const params = new URLSearchParams(search);
  const accounts: DerivOAuthAccountInput[] = [];
  let idx = 1;
  while (idx <= 30) {
    const account = params.get(`acct${idx}`);
    const token = params.get(`token${idx}`);
    const currency = params.get(`cur${idx}`) || 'USD';
    if (!account && !token) break;
    if (account && token) {
      accounts.push({ account: account.trim(), token: token.trim(), currency: currency.trim() });
    }
    idx += 1;
  }
  return accounts;
}

export const DerivCallback: React.FC = () => {
  const navigate = useNavigate();
  const processedRef = useRef(false);
  const [status, setStatus] = useState<'processing' | 'success' | 'error'>('processing');
  const [message, setMessage] = useState('Processando autorização OAuth da Deriv...');

  useEffect(() => {
    if (processedRef.current) return;
    processedRef.current = true;

    const rawSearch = window.location.search;
    const parsedAccounts = parseDerivOAuthAccounts(rawSearch);

    // CRITICAL SECURITY: immediately wipe OAuth tokens from browser URL/history
    window.history.replaceState({}, '', '/deriv-connect');

    if (parsedAccounts.length === 0) {
      setStatus('error');
      setMessage('Nenhuma conta/token OAuth encontrado na resposta da Deriv.');
      const t = window.setTimeout(() => {
        navigate('/deriv-connect', {
          replace: true,
          state: { oauthError: 'Nenhuma conta foi retornada pelo OAuth da Deriv.' },
        });
      }, 1800);
      return () => window.clearTimeout(t);
    }

    (async () => {
      try {
        const connected = await derivApi.connectOAuth(parsedAccounts);
        setStatus('success');
        setMessage(`${connected.length} conta(s) Deriv conectada(s) com segurança! Redirecionando...`);
        window.setTimeout(() => {
          navigate('/deriv-connect', {
            replace: true,
            state: {
              oauthSuccess: `${connected.length} conta(s) sincronizada(s) via OAuth Oficial da Deriv.`,
            },
          });
        }, 900);
      } catch (err) {
        const detail =
          err instanceof ApiError
            ? err.detail
            : 'Falha ao salvar suas contas Deriv. Verifique se sua sessão está ativa.';
        setStatus('error');
        setMessage(detail);
        window.setTimeout(() => {
          navigate('/deriv-connect', {
            replace: true,
            state: { oauthError: detail },
          });
        }, 2200);
      }
    })();
  }, [navigate]);

  return (
    <div className="min-h-screen bg-slate-950 bg-quant-grid flex items-center justify-center p-4">
      <div className="glass-card max-w-md w-full p-7 text-center space-y-4">
        {status === 'processing' && (
          <Loader2 className="mx-auto h-10 w-10 animate-spin text-cyan-400" />
        )}
        {status === 'success' && (
          <CheckCircle2 className="mx-auto h-10 w-10 text-emerald-400" />
        )}
        {status === 'error' && (
          <AlertTriangle className="mx-auto h-10 w-10 text-rose-400" />
        )}
        <h1 className="text-lg font-semibold text-slate-100">
          {status === 'processing'
            ? 'Conectando à Deriv'
            : status === 'success'
              ? 'Conexão Concluída'
              : 'Erro na Autorização'}
        </h1>
        <p className="text-xs text-slate-400 leading-relaxed">{message}</p>
        <p className="text-[11px] text-slate-500">
          Os tokens foram removidos automaticamente da URL do navegador por segurança.
        </p>
      </div>
    </div>
  );
};
