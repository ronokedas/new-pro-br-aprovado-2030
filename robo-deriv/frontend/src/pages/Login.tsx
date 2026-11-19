import React, { useState } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  Bot,
  Eye,
  EyeOff,
  Loader2,
  Lock,
  Mail,
  ShieldCheck,
} from 'lucide-react';
import { useAuth } from '../hooks/useAuth';
import { ApiError } from '../lib/api';

export const Login: React.FC = () => {
  const { user, loading, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const from = (location.state as { from?: string } | null)?.from || '/dashboard';

  if (!loading && user) {
    return <Navigate to={from} replace />;
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      navigate(from, { replace: true });
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail);
      } else {
        setError('Erro ao conectar ao servidor. Tente novamente em instantes.');
      }
    } finally {
      setSubmitting(false);
    }
  };

  const isStatusWarning =
    error &&
    (error.toLowerCase().includes('expir') ||
      error.toLowerCase().includes('suspend') ||
      error.toLowerCase().includes('bloque'));

  return (
    <div className="min-h-screen bg-slate-950 bg-quant-grid flex flex-col justify-center px-4 py-10">
      <div className="mx-auto w-full max-w-md space-y-6">
        {/* Brand Header */}
        <div className="text-center space-y-3">
          <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-emerald-500 to-cyan-500 text-slate-950 shadow-glow">
            <Bot className="h-7 w-7" />
          </div>
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-slate-100">
              QuantChannel DCA
            </h1>
            <p className="mt-1 text-xs text-slate-400">
              Plataforma Quantitativa Multi-Usuário • Canal + Preço Médio Controlado (Deriv API)
            </p>
          </div>
        </div>

        {/* Login Card */}
        <div className="glass-card p-6 sm:p-7 space-y-5">
          <div>
            <h2 className="text-base font-semibold text-slate-100">Acesso ao Terminal</h2>
            <p className="mt-0.5 text-xs text-slate-400">
              Entre com seu e-mail e senha autorizados pelo administrador.
            </p>
          </div>

          {error && (
            <div
              className={`flex items-start gap-2.5 rounded-xl border p-3.5 text-xs leading-relaxed ${
                isStatusWarning
                  ? 'border-amber-500/40 bg-amber-500/10 text-amber-100'
                  : 'border-rose-500/40 bg-rose-500/10 text-rose-200'
              }`}
            >
              <AlertTriangle
                className={`h-4 w-4 shrink-0 mt-0.5 ${
                  isStatusWarning ? 'text-amber-400' : 'text-rose-400'
                }`}
              />
              <div>
                <p className="font-semibold">
                  {isStatusWarning ? 'Atenção à sua assinatura/status' : 'Falha na autenticação'}
                </p>
                <p className="mt-0.5 opacity-90">{error}</p>
              </div>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-300">E-mail</label>
              <div className="relative">
                <Mail className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                <input
                  type="email"
                  required
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="input-dark pl-10"
                  placeholder="seu@email.com"
                />
              </div>
            </div>

            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-300">Senha</label>
              <div className="relative">
                <Lock className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                <input
                  type={showPassword ? 'text' : 'password'}
                  required
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="input-dark pl-10 pr-10"
                  placeholder="••••••••"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
                  tabIndex={-1}
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            <button
              type="submit"
              disabled={submitting}
              className="btn-primary w-full py-3"
            >
              {submitting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Autenticando...
                </>
              ) : (
                'Entrar no Terminal'
              )}
            </button>
          </form>

          <div className="flex items-center gap-2 rounded-xl border border-slate-800/80 bg-slate-950/60 px-3.5 py-2.5 text-[11px] text-slate-400">
            <ShieldCheck className="h-4 w-4 shrink-0 text-emerald-400" />
            <span>
              Conexão direta via WebSocket Oficial da Deriv • Tokens criptografados em repouso (AES-256).
            </span>
          </div>
        </div>

        <p className="text-center text-[11px] text-slate-500">
          Operações em Multipliers envolvem risco financeiro real. Sempre valide sua configuração em conta DEMO antes de operar em REAL.
        </p>
      </div>
    </div>
  );
};
