import React, { useCallback, useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  KeyRound,
  Link2,
  Loader2,
  RefreshCw,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  Wallet,
} from 'lucide-react';
import { RealAccountModal } from '../components/RealAccountModal';
import { ApiError, derivApi } from '../lib/api';
import { formatDateTime, formatUsd } from '../lib/format';
import type { DerivAccount, DerivOAuthUrlResponse } from '../types/api';

export const DerivConnect: React.FC = () => {
  const location = useLocation();
  const routeState = location.state as { oauthSuccess?: string; oauthError?: string } | null;

  const [activeTab, setActiveTab] = useState<'oauth' | 'token'>('token');
  const [oauthInfo, setOauthInfo] = useState<DerivOAuthUrlResponse | null>(null);
  const [accounts, setAccounts] = useState<DerivAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [tokenInput, setTokenInput] = useState('');
  const [tokenSubmitting, setTokenSubmitting] = useState(false);
  const [selectingId, setSelectingId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [realModalAccount, setRealModalAccount] = useState<DerivAccount | null>(null);

  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; text: string } | null>(() => {
    if (routeState?.oauthSuccess) return { type: 'success', text: routeState.oauthSuccess };
    if (routeState?.oauthError) return { type: 'error', text: routeState.oauthError };
    return null;
  });

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [oauthRes, accsRes] = await Promise.all([
        derivApi.getOAuthUrl().catch(() => null),
        derivApi.getAccounts(),
      ]);
      if (oauthRes) {
        setOauthInfo(oauthRes);
        if (!oauthRes.configured) {
          setActiveTab('token');
        }
      }
      setAccounts(Array.isArray(accsRes) ? accsRes : []);
    } catch (err) {
      setFeedback({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Erro ao carregar contas Deriv.',
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleSyncBalance = async () => {
    setSyncing(true);
    setFeedback(null);
    try {
      const res = await derivApi.syncBalance();
      if (Array.isArray(res) && res.length > 0) {
        setAccounts(res);
      } else {
        const refreshed = await derivApi.getAccounts();
        setAccounts(refreshed);
      }
      setFeedback({ type: 'success', text: 'Saldos sincronizados em tempo real com a Deriv.' });
    } catch (err) {
      setFeedback({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Falha ao sincronizar saldo.',
      });
    } finally {
      setSyncing(false);
    }
  };

  const handleConnectToken = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleaned = tokenInput.trim();
    if (!cleaned) return;
    setTokenSubmitting(true);
    setFeedback(null);
    try {
      const updated = await derivApi.connectToken(cleaned);
      setAccounts(Array.isArray(updated) ? updated : await derivApi.getAccounts());
      setTokenInput('');
      setFeedback({
        type: 'success',
        text: 'API Token validado e conta Deriv conectada com criptografia AES-256.',
      });
    } catch (err) {
      setFeedback({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Token inválido ou recusado pela Deriv.',
      });
    } finally {
      setTokenSubmitting(false);
    }
  };

  const handleSelectAccountClick = async (acc: DerivAccount) => {
    if (acc.is_active_selected) return;
    if (acc.account_type === 'REAL') {
      setRealModalAccount(acc);
      return;
    }
    setSelectingId(acc.id);
    setFeedback(null);
    try {
      await derivApi.selectAccount(acc.id, false);
      const refreshed = await derivApi.getAccounts();
      setAccounts(refreshed);
      setFeedback({
        type: 'success',
        text: `Conta DEMO ${acc.loginid} ativada para o robô.`,
      });
    } catch (err) {
      setFeedback({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Não foi possível selecionar a conta.',
      });
    } finally {
      setSelectingId(null);
    }
  };

  const handleConfirmRealAccount = async (accountId: number) => {
    setSelectingId(accountId);
    setFeedback(null);
    try {
      await derivApi.selectAccount(accountId, true);
      const refreshed = await derivApi.getAccounts();
      setAccounts(refreshed);
      setFeedback({
        type: 'success',
        text: 'Conta REAL ativada com confirmação explícita de risco.',
      });
    } catch (err) {
      setFeedback({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Falha ao ativar conta REAL.',
      });
    } finally {
      setSelectingId(null);
    }
  };

  const handleDeleteAccount = async (acc: DerivAccount) => {
    if (!window.confirm(`Desconectar a conta ${acc.loginid} (${acc.account_type})?`)) return;
    setDeletingId(acc.id);
    setFeedback(null);
    try {
      await derivApi.deleteAccount(acc.id);
      setAccounts((prev) => prev.filter((a) => a.id !== acc.id));
      setFeedback({ type: 'success', text: `Conta ${acc.loginid} removida com sucesso.` });
    } catch (err) {
      setFeedback({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Erro ao remover conta.',
      });
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-slate-100">Conexão com a Deriv</h1>
          <p className="text-xs text-slate-400">
            Conecte sua própria conta Deriv (Demo <span className="font-mono">VRTC</span> ou Real{' '}
            <span className="font-mono">CR</span>) via OAuth Oficial ou API Token pessoal.
          </p>
        </div>

        <button
          type="button"
          onClick={handleSyncBalance}
          disabled={syncing || accounts.length === 0}
          className="btn-secondary"
        >
          <RefreshCw className={`h-4 w-4 ${syncing ? 'animate-spin' : ''}`} />
          Sincronizar Saldos
        </button>
      </div>

      {/* Feedback Banner */}
      {feedback && (
        <div
          className={`flex items-start gap-2.5 rounded-xl border px-4 py-3 text-xs ${
            feedback.type === 'success'
              ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
              : 'border-rose-500/40 bg-rose-500/10 text-rose-200'
          }`}
        >
          {feedback.type === 'success' ? (
            <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400 mt-0.5" />
          ) : (
            <AlertTriangle className="h-4 w-4 shrink-0 text-rose-400 mt-0.5" />
          )}
          <span>{feedback.text}</span>
        </div>
      )}

      {/* Connection Methods Card */}
      <div className="glass-card overflow-hidden">
        <div className="flex border-b border-slate-800/80">
          <button
            type="button"
            onClick={() => setActiveTab('token')}
            className={`flex-1 flex items-center justify-center gap-2 px-4 py-3.5 text-xs sm:text-sm font-semibold transition-colors ${
              activeTab === 'token'
                ? 'bg-cyan-500/10 text-cyan-300 border-b-2 border-b-cyan-400'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
            }`}
          >
            <KeyRound className="h-4 w-4" />
            API Token Pessoal (Recomendado para Localhost)
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('oauth')}
            className={`flex-1 flex items-center justify-center gap-2 px-4 py-3.5 text-xs sm:text-sm font-semibold transition-colors ${
              activeTab === 'oauth'
                ? 'bg-emerald-500/10 text-emerald-300 border-b-2 border-b-emerald-400'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
            }`}
          >
            <Link2 className="h-4 w-4" />
            OAuth da Deriv (Requer App ID Registrado)
          </button>
        </div>

        <div className="p-5 sm:p-6">
          {activeTab === 'oauth' ? (
            <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
              <div className="space-y-2 max-w-2xl">
                <div className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 text-[11px] font-medium text-emerald-300">
                  <ShieldCheck className="h-3.5 w-3.5" />
                  Fluxo Oficial Deriv OAuth2
                </div>
                <h2 className="text-base font-semibold text-slate-100">
                  Importe automaticamente suas contas DEMO e REAL
                </h2>
                <p className="text-xs text-slate-400 leading-relaxed">
                  Você será redirecionado ao portal oficial da Deriv para autorizar este aplicativo. Ao retornar, os tokens são criptografados com AES-256 no servidor e imediatamente apagados da barra de endereço do seu navegador.
                </p>
                {oauthInfo && !oauthInfo.configured && (
                  <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-200 space-y-1">
                    <p className="font-semibold flex items-center gap-1.5 text-amber-300">
                      <AlertTriangle className="h-4 w-4 shrink-0" />
                      OAuth em Localhost requer App ID cadastrado
                    </p>
                    <p className="text-amber-200/90 leading-relaxed">
                      O App ID padrão (<code className="font-mono">1089</code>) é genérico de testes da Deriv e não redireciona de volta para <code className="font-mono">localhost</code>. 
                      Para conectar em <strong>30 segundos sem complicações</strong>, utilize a aba ao lado <strong>"Conectar via API Token Pessoal"</strong>.
                    </p>
                  </div>
                )}
              </div>

              <div className="shrink-0">
                <a
                  href={oauthInfo?.oauth_url || '#'}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => {
                    if (!oauthInfo?.oauth_url) {
                      e.preventDefault();
                      setFeedback({
                        type: 'error',
                        text: 'URL OAuth da Deriv indisponível. Verifique a configuração do DERIV_APP_ID ou use API Token.',
                      });
                    }
                  }}
                  className={`btn-primary py-3 px-5 ${
                    !oauthInfo?.oauth_url ? 'opacity-50 pointer-events-none' : ''
                  }`}
                >
                  <ExternalLink className="h-4 w-4" />
                  Conectar com a Deriv (OAuth)
                </a>
              </div>
            </div>
          ) : (
            <form onSubmit={handleConnectToken} className="space-y-4">
              <div className="flex items-start gap-3 rounded-xl border border-cyan-500/30 bg-cyan-500/10 p-4">
                <ShieldCheck className="h-5 w-5 shrink-0 text-cyan-400 mt-0.5" />
                <div className="space-y-1.5 text-xs text-cyan-100 flex-1">
                  <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-1">
                    <p className="font-semibold text-cyan-200">Como obter seu Token na Deriv (30 segundos):</p>
                    <a
                      href="https://home.deriv.com/dashboard/profile/api-tokens"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-cyan-300 hover:text-white font-medium underline underline-offset-2"
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                      Abrir página de Tokens na Deriv ↗
                    </a>
                  </div>
                  <ol className="list-decimal list-inside space-y-0.5 text-slate-300">
                    <li>Acesse o link acima ou vá em <em>Configurações da Conta → Segurança e Limites → Token da API</em></li>
                    <li>Dê um nome para o token (ex: <code className="font-mono text-cyan-200">RoboDeriv</code>)</li>
                    <li>Marque <strong>APENAS</strong> as caixas <span className="rounded bg-slate-900 px-1 font-mono text-emerald-300">Read</span> e <span className="rounded bg-slate-900 px-1 font-mono text-emerald-300">Trade</span></li>
                    <li><strong className="text-rose-300">NUNCA</strong> marque <em>Payments</em> ou <em>Admin</em> (o robô recusa tokens com permissão de saque por segurança)</li>
                    <li>Copie o token gerado e cole no campo abaixo:</li>
                  </ol>
                </div>
              </div>

              <div className="flex flex-col gap-3 sm:flex-row">
                <input
                  type="password"
                  required
                  value={tokenInput}
                  onChange={(e) => setTokenInput(e.target.value)}
                  placeholder="Cole aqui seu API Token pessoal da Deriv (apenas Read + Trade)..."
                  className="input-dark flex-1 font-mono"
                />
                <button
                  type="submit"
                  disabled={tokenSubmitting || !tokenInput.trim()}
                  className="btn-primary shrink-0 px-5"
                >
                  {tokenSubmitting ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Validando...
                    </>
                  ) : (
                    <>
                      <KeyRound className="h-4 w-4" />
                      Validar e Conectar
                    </>
                  )}
                </button>
              </div>
            </form>
          )}
        </div>
      </div>

      {/* Connected Accounts Grid */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
            <Wallet className="h-4 w-4 text-cyan-400" />
            Suas Contas Deriv Conectadas ({accounts.length})
          </h2>
          <span className="text-[11px] text-slate-500">
            Apenas 1 conta permanece ativa por vez para execução do robô
          </span>
        </div>

        {loading ? (
          <div className="glass-card p-10 text-center text-xs text-slate-400">
            <Loader2 className="mx-auto h-6 w-6 animate-spin text-cyan-400 mb-2" />
            Carregando contas conectadas...
          </div>
        ) : accounts.length === 0 ? (
          <div className="glass-card p-10 text-center space-y-2">
            <Shield className="mx-auto h-8 w-8 text-slate-600" />
            <p className="text-sm font-medium text-slate-300">Nenhuma conta Deriv conectada ainda</p>
            <p className="text-xs text-slate-500 max-w-md mx-auto">
              Utilize o botão OAuth Oficial da Deriv ou cole seu API Token acima para vincular sua conta DEMO ou REAL.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {accounts.map((acc) => {
              const isReal = acc.account_type === 'REAL';
              const isSelected = acc.is_active_selected;
              return (
                <div
                  key={acc.id}
                  className={`glass-card p-5 flex flex-col justify-between transition-all ${
                    isSelected
                      ? isReal
                        ? 'border-emerald-500/60 shadow-glow'
                        : 'border-sky-500/60 shadow-glow-cyan'
                      : 'hover:border-slate-700'
                  }`}
                >
                  <div className="space-y-4">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="flex items-center gap-2">
                          <span
                            className={`rounded-full px-2.5 py-0.5 text-[11px] font-bold uppercase tracking-wide border ${
                              isReal
                                ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40'
                                : 'bg-sky-500/15 text-sky-300 border-sky-500/40'
                            }`}
                          >
                            {isReal ? 'CONTA REAL' : 'CONTA DEMO'}
                          </span>
                          {isSelected && (
                            <span className="inline-flex items-center gap-1 rounded-full bg-slate-800 border border-slate-700 px-2 py-0.5 text-[10px] font-semibold text-slate-200">
                              <CheckCircle2 className="h-3 w-3 text-emerald-400" />
                              Ativa
                            </span>
                          )}
                        </div>
                        <div className="mt-2 font-mono text-base font-bold text-slate-100">
                          {acc.loginid}
                        </div>
                      </div>

                      <button
                        type="button"
                        onClick={() => handleDeleteAccount(acc)}
                        disabled={deletingId === acc.id}
                        className="rounded-lg p-1.5 text-slate-500 hover:bg-rose-500/15 hover:text-rose-400 transition-colors"
                        title="Remover conta"
                      >
                        {deletingId === acc.id ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <Trash2 className="h-4 w-4" />
                        )}
                      </button>
                    </div>

                    <div className="rounded-xl border border-slate-800/80 bg-slate-950/70 p-3.5">
                      <span className="text-[11px] text-slate-400 block">Saldo Disponível</span>
                      <div className="mt-1 font-mono text-2xl font-bold text-slate-100 tabular-nums">
                        {formatUsd(acc.balance, acc.currency)}
                      </div>
                      <span className="mt-1 block text-[10px] text-slate-500">
                        Última sincronização: {formatDateTime(acc.last_synced_at)}
                      </span>
                    </div>
                  </div>

                  <div className="mt-4 pt-3 border-t border-slate-800/70">
                    <button
                      type="button"
                      disabled={isSelected || selectingId === acc.id}
                      onClick={() => handleSelectAccountClick(acc)}
                      className={`w-full rounded-xl px-4 py-2.5 text-xs font-semibold transition-all flex items-center justify-center gap-2 ${
                        isSelected
                          ? 'bg-slate-800/70 text-slate-400 cursor-default border border-slate-700/60'
                          : isReal
                            ? 'bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300 border border-emerald-500/40'
                            : 'bg-sky-500/15 hover:bg-sky-500/25 text-sky-300 border border-sky-500/40'
                      }`}
                    >
                      {selectingId === acc.id ? (
                        <>
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          Alterando...
                        </>
                      ) : isSelected ? (
                        'Conta Selecionada para o Robô'
                      ) : isReal ? (
                        'Ativar Conta REAL (Requer Confirmação)'
                      ) : (
                        'Selecionar Conta DEMO'
                      )}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <RealAccountModal
        open={Boolean(realModalAccount)}
        account={realModalAccount}
        onClose={() => setRealModalAccount(null)}
        onConfirm={handleConfirmRealAccount}
      />
    </div>
  );
};
