import React, { useState } from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Bot,
  CalendarClock,
  Compass,
  KeyRound,
  Link2,
  LogOut,
  Menu,
  Shield,
  Sliders,
  X,
} from 'lucide-react';
import { useAuth } from '../hooks/useAuth';
import { ChangePasswordModal } from './ChangePasswordModal';

interface NavItem {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  adminOnly?: boolean;
  requiresBtcRadar?: boolean;
  requiresRoboDeriv?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { to: '/dashboard', label: 'Robô Nexus AI', icon: Bot, requiresRoboDeriv: true },
  { to: '/btc-radar', label: 'Radar BTC M1', icon: Compass, requiresBtcRadar: true },
  { to: '/bot-config', label: 'Configurações', icon: Sliders, requiresRoboDeriv: true },
  { to: '/deriv-connect', label: 'Conexão Deriv', icon: Link2, requiresRoboDeriv: true },
  { to: '/reports', label: 'Relatórios', icon: BarChart3 },
  { to: '/admin', label: 'Administração', icon: Shield, adminOnly: true },
];

export const Layout: React.FC = () => {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [passwordModalOpen, setPasswordModalOpen] = useState(false);

  const visibleNav = NAV_ITEMS.filter((item) => {
    if (item.adminOnly && user?.role !== 'ADMIN') return false;
    if (item.requiresBtcRadar && user?.can_access_btc_radar === false) return false;
    if (item.requiresRoboDeriv && user?.can_access_robo_deriv === false) return false;
    return true;
  });

  const handleLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  const daysRemaining = user?.days_remaining ?? 0;
  const expiryBadgeTone = user?.is_expired
    ? 'border-rose-500/40 bg-rose-500/15 text-rose-300'
    : daysRemaining <= 5
      ? 'border-amber-500/40 bg-amber-500/15 text-amber-300'
      : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300';

  const renderSidebarContent = () => (
    <div className="flex h-full flex-col">
      {/* Brand */}
      <div className="flex items-center gap-3 border-b border-slate-800/80 px-5 py-5">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-cyan-400 via-sky-500 to-indigo-600 text-slate-950 shadow-glow font-bold">
          <Bot className="h-5 w-5 text-slate-950" />
        </div>
        <div className="min-w-0">
          <div className="truncate text-sm font-bold tracking-wider text-slate-100 flex items-center gap-1.5">
            <span>NEXUS</span>
            <span className="text-cyan-400 font-extrabold">QUANTUM</span>
          </div>
          <div className="text-[10px] font-semibold tracking-wider text-cyan-300/80 uppercase">AI Synthetic Scalper</div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-1 px-3 py-4">
        {visibleNav.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              onClick={() => setMobileOpen(false)}
              className={({ isActive }) =>
                `group flex items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium transition-all ${
                  isActive
                    ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 shadow-sm shadow-emerald-500/10'
                    : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-100 border border-transparent'
                }`
              }
            >
              <Icon className="h-4 w-4 shrink-0" />
              <span>{item.label}</span>
            </NavLink>
          );
        })}
      </nav>

      {/* User & Subscription Footer */}
      <div className="border-t border-slate-800/80 p-4 space-y-3">
        {user && (
          <div className="rounded-xl border border-slate-800/80 bg-slate-950/70 p-3 space-y-2">
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <p className="truncate text-xs font-semibold text-slate-200">{user.name}</p>
                <p className="truncate text-[11px] text-slate-500">{user.email}</p>
              </div>
              <span
                className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-bold uppercase ${
                  user.role === 'ADMIN'
                    ? 'bg-cyan-500/15 text-cyan-300 border border-cyan-500/30'
                    : 'bg-slate-800 text-slate-400 border border-slate-700'
                }`}
              >
                {user.role}
              </span>
            </div>

            <div
              className={`flex items-center justify-between rounded-lg border px-2.5 py-1.5 text-[11px] font-medium ${expiryBadgeTone}`}
            >
              <span className="inline-flex items-center gap-1.5">
                <CalendarClock className="h-3.5 w-3.5" />
                Assinatura
              </span>
              <span className="font-mono font-semibold">
                {user.is_expired ? 'EXPIRADA' : `${daysRemaining}d restante${daysRemaining === 1 ? '' : 's'}`}
              </span>
            </div>
          </div>
        )}

        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={() => setPasswordModalOpen(true)}
            className="flex items-center justify-center gap-1.5 rounded-xl border border-slate-800 bg-slate-900/80 px-2.5 py-2 text-xs font-medium text-slate-300 hover:bg-slate-800 hover:text-slate-100 transition-colors"
          >
            <KeyRound className="h-3.5 w-3.5" />
            Senha
          </button>
          <button
            type="button"
            onClick={handleLogout}
            className="flex items-center justify-center gap-1.5 rounded-xl border border-slate-800 bg-slate-900/80 px-2.5 py-2 text-xs font-medium text-rose-300 hover:bg-rose-500/15 hover:border-rose-500/30 transition-colors"
          >
            <LogOut className="h-3.5 w-3.5" />
            Sair
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen bg-slate-950 bg-quant-grid text-slate-100 flex">
      {/* Desktop Sidebar */}
      <aside className="hidden lg:flex lg:w-64 lg:flex-col lg:fixed lg:inset-y-0 border-r border-slate-800/80 bg-slate-950/90 backdrop-blur-xl z-30">
        {renderSidebarContent()}
      </aside>

      {/* Mobile Drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden flex">
          <div
            className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="relative z-10 flex w-72 max-w-[82vw] flex-col border-r border-slate-800 bg-slate-950">
            <button
              type="button"
              onClick={() => setMobileOpen(false)}
              className="absolute right-3 top-4 rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100"
              aria-label="Fechar menu"
            >
              <X className="h-5 w-5" />
            </button>
            {renderSidebarContent()}
          </aside>
        </div>
      )}

      {/* Main Content */}
      <div className="flex-1 flex flex-col min-w-0 lg:pl-64">
        {/* Mobile Top Bar */}
        <header className="sticky top-0 z-20 flex items-center justify-between border-b border-slate-800/80 bg-slate-950/85 backdrop-blur-xl px-4 py-3 lg:hidden">
          <button
            type="button"
            onClick={() => setMobileOpen(true)}
            className="rounded-lg border border-slate-800 bg-slate-900 p-2 text-slate-300 hover:text-white"
            aria-label="Abrir menu"
          >
            <Menu className="h-5 w-5" />
          </button>
          <div className="flex items-center gap-2">
            <Bot className="h-5 w-5 text-emerald-400" />
            <span className="text-sm font-bold text-slate-100">QuantChannel DCA</span>
          </div>
          <div className={`rounded-lg border px-2 py-1 text-[11px] font-mono font-semibold ${expiryBadgeTone}`}>
            {user?.is_expired ? 'EXP' : `${daysRemaining}d`}
          </div>
        </header>

        {/* Global Subscription / Suspended Banner */}
        {user && (user.is_expired || user.status === 'SUSPENDED') && (
          <div className="border-b border-rose-500/40 bg-rose-500/15 px-4 py-3 text-xs sm:text-sm text-rose-100">
            <div className="mx-auto flex max-w-[1600px] items-center gap-2.5">
              <AlertTriangle className="h-4 w-4 shrink-0 text-rose-400" />
              <span>
                {user.status === 'SUSPENDED' ? (
                  <>
                    <strong>Conta suspensa pelo administrador.</strong> Novas entradas foram bloqueadas. Caso exista um cesto já aberto na Deriv, o motor continuará gerenciando apenas a saída segura (TP/Stop) até encerrá-lo.
                  </>
                ) : (
                  <>
                    <strong>Sua assinatura expirou.</strong> Novas entradas estão bloqueadas. Cestos já abertos seguem sob gerenciamento seguro de saída até o fechamento. Contate o administrador para renovar.
                  </>
                )}
              </span>
            </div>
          </div>
        )}

        <main className="flex-1 mx-auto w-full max-w-[1600px] px-4 py-5 sm:px-6 lg:px-8">
          <Outlet />
        </main>
      </div>

      {/* Password Change Modal (mandatory if must_change_password === true) */}
      <ChangePasswordModal
        open={Boolean(user?.must_change_password) || passwordModalOpen}
        mandatory={Boolean(user?.must_change_password)}
        onClose={() => setPasswordModalOpen(false)}
      />
    </div>
  );
};
