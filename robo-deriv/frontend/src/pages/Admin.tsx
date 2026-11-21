import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  Ban,
  CheckCircle2,
  Cpu,
  Database,
  Edit3,
  Globe,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  Server,
  Shield,
  Trash2,
  UserCheck,
  Users,
} from 'lucide-react';
import { Modal } from '../components/Modal';
import { ApiError, adminApi } from '../lib/api';
import { formatDateShort, formatDateTime, formatUsd } from '../lib/format';
import type {
  AdminCreateUserPayload,
  AdminHealthResponse,
  AdminUpdateUserPayload,
  AdminUserItem,
  UserRole,
  UserStatus,
} from '../types/api';

type UserFilterTab = 'ALL' | 'ACTIVE' | 'EXPIRING' | 'EXPIRED' | 'SUSPENDED';

function toInputDate(iso?: string | null): string {
  if (!iso) {
    const d = new Date(Date.now() + 30 * 86400 * 1000);
    return d.toISOString().slice(0, 10);
  }
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return new Date(Date.now() + 30 * 86400 * 1000).toISOString().slice(0, 10);
  return d.toISOString().slice(0, 10);
}

function isServiceOk(val: boolean | { ok: boolean } | undefined): boolean {
  if (typeof val === 'boolean') return val;
  return Boolean(val?.ok);
}

export const Admin: React.FC = () => {
  const [users, setUsers] = useState<AdminUserItem[]>([]);
  const [health, setHealth] = useState<AdminHealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const [search, setSearch] = useState('');
  const [filterTab, setFilterTab] = useState<UserFilterTab>('ALL');
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [busyUserId, setBusyUserId] = useState<number | null>(null);

  // Create/Edit Modal State
  const [modalOpen, setModalOpen] = useState(false);
  const [editingUser, setEditingUser] = useState<AdminUserItem | null>(null);
  const [submittingModal, setSubmittingModal] = useState(false);

  const [formName, setFormName] = useState('');
  const [formEmail, setFormEmail] = useState('');
  const [formPassword, setFormPassword] = useState('');
  const [formRole, setFormRole] = useState<UserRole>('USER');
  const [formStatus, setFormStatus] = useState<UserStatus>('ACTIVE');
  const [formExpiresDate, setFormExpiresDate] = useState(toInputDate());
  const [formMaxStake, setFormMaxStake] = useState<number>(50);
  const [formMustChangePwd, setFormMustChangePwd] = useState(false);
  const [formCanAccessBtcRadar, setFormCanAccessBtcRadar] = useState(true);
  const [formCanAccessRoboDeriv, setFormCanAccessRoboDeriv] = useState(true);

  const loadAdminData = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    else setRefreshing(true);
    try {
      const [usersRes, healthRes] = await Promise.all([
        adminApi.listUsers(),
        adminApi.getHealth().catch(() => null),
      ]);
      setUsers(Array.isArray(usersRes) ? usersRes : []);
      setHealth(healthRes);
    } catch (err) {
      setFeedback({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Erro ao carregar dados administrativos.',
      });
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    loadAdminData(false);
  }, [loadAdminData]);

  const openCreateModal = () => {
    setEditingUser(null);
    setFormName('');
    setFormEmail('');
    setFormPassword('');
    setFormRole('USER');
    setFormStatus('ACTIVE');
    setFormExpiresDate(toInputDate());
    setFormMaxStake(50);
    setFormMustChangePwd(true);
    setFormCanAccessBtcRadar(true);
    setFormCanAccessRoboDeriv(true);
    setModalOpen(true);
  };

  const openEditModal = (u: AdminUserItem) => {
    setEditingUser(u);
    setFormName(u.name);
    setFormEmail(u.email);
    setFormPassword('');
    setFormRole(u.role);
    setFormStatus(u.status);
    setFormExpiresDate(toInputDate(u.expires_at));
    setFormMaxStake(u.max_stake_usd);
    setFormMustChangePwd(Boolean(u.must_change_password));
    setFormCanAccessBtcRadar(u.can_access_btc_radar !== false);
    setFormCanAccessRoboDeriv(u.can_access_robo_deriv !== false);
    setModalOpen(true);
  };

  const handleSaveUser = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmittingModal(true);
    setFeedback(null);
    const expiresIso = `${formExpiresDate}T23:59:59Z`;

    try {
      if (editingUser) {
        const payload: AdminUpdateUserPayload = {
          name: formName.trim(),
          email: formEmail.trim(),
          role: formRole,
          status: formStatus,
          expires_at: expiresIso,
          max_stake_usd: Number(formMaxStake),
          can_access_btc_radar: formCanAccessBtcRadar,
          can_access_robo_deriv: formCanAccessRoboDeriv,
          must_change_password: formMustChangePwd,
        };
        if (formPassword.trim()) {
          payload.password = formPassword.trim();
        }
        await adminApi.updateUser(editingUser.id, payload);
        setFeedback({ type: 'success', text: `Usuário ${formName} atualizado com sucesso.` });
      } else {
        const payload: AdminCreateUserPayload = {
          name: formName.trim(),
          email: formEmail.trim(),
          password: formPassword,
          role: formRole,
          status: formStatus,
          expires_at: expiresIso,
          max_stake_usd: Number(formMaxStake),
          can_access_btc_radar: formCanAccessBtcRadar,
          can_access_robo_deriv: formCanAccessRoboDeriv,
          must_change_password: formMustChangePwd,
        };
        await adminApi.createUser(payload);
        setFeedback({ type: 'success', text: `Usuário ${formName} criado com sucesso.` });
      }
      setModalOpen(false);
      await loadAdminData(true);
    } catch (err) {
      setFeedback({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Erro ao salvar usuário.',
      });
    } finally {
      setSubmittingModal(false);
    }
  };

  const handleExtend = async (u: AdminUserItem, daysToAdd: number) => {
    setBusyUserId(u.id);
    setFeedback(null);
    try {
      await adminApi.extendUser(u.id, daysToAdd);
      setFeedback({
        type: 'success',
        text: `Validade de ${u.name} renovada em +${daysToAdd} dias.`,
      });
      await loadAdminData(true);
    } catch (err) {
      setFeedback({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Erro ao renovar assinatura.',
      });
    } finally {
      setBusyUserId(null);
    }
  };

  const handleToggleStatus = async (u: AdminUserItem) => {
    const nextStatus: UserStatus = u.status === 'ACTIVE' ? 'SUSPENDED' : 'ACTIVE';
    setBusyUserId(u.id);
    setFeedback(null);
    try {
      await adminApi.updateUser(u.id, { status: nextStatus });
      setFeedback({
        type: 'success',
        text: `Usuário ${u.name} ${nextStatus === 'ACTIVE' ? 'ativado' : 'suspenso'} com sucesso.`,
      });
      await loadAdminData(true);
    } catch (err) {
      setFeedback({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Erro ao alterar status do usuário.',
      });
    } finally {
      setBusyUserId(null);
    }
  };

  const handleToggleBtcRadar = async (u: AdminUserItem) => {
    setBusyUserId(u.id);
    setFeedback(null);
    const nextVal = !(u.can_access_btc_radar !== false);
    try {
      await adminApi.updateUser(u.id, { can_access_btc_radar: nextVal });
      setUsers((prev) =>
        prev.map((item) => (item.id === u.id ? { ...item, can_access_btc_radar: nextVal } : item))
      );
      setFeedback({
        type: 'success',
        text: `Acesso ao Radar BTC ${nextVal ? 'LIBERADO' : 'BLOQUEADO'} para ${u.name}.`,
      });
    } catch (err) {
      setFeedback({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Erro ao alterar permissão do Radar BTC.',
      });
    } finally {
      setBusyUserId(null);
    }
  };

  const handleToggleRoboDeriv = async (u: AdminUserItem) => {
    setBusyUserId(u.id);
    setFeedback(null);
    const nextVal = !(u.can_access_robo_deriv !== false);
    try {
      await adminApi.updateUser(u.id, { can_access_robo_deriv: nextVal });
      setUsers((prev) =>
        prev.map((item) => (item.id === u.id ? { ...item, can_access_robo_deriv: nextVal } : item))
      );
      setFeedback({
        type: 'success',
        text: `Acesso ao Robô Deriv (Nexus) ${nextVal ? 'LIBERADO' : 'BLOQUEADO'} para ${u.name}.`,
      });
    } catch (err) {
      setFeedback({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Erro ao alterar permissão do Robô Deriv.',
      });
    } finally {
      setBusyUserId(null);
    }
  };

  const handleDeleteUser = async (u: AdminUserItem) => {
    if (!window.confirm(`Excluir permanentemente o usuário ${u.name} (${u.email})?`)) return;
    setBusyUserId(u.id);
    setFeedback(null);
    try {
      await adminApi.deleteUser(u.id);
      setUsers((prev) => prev.filter((item) => item.id !== u.id));
      setFeedback({ type: 'success', text: `Usuário ${u.name} excluído.` });
    } catch (err) {
      setFeedback({
        type: 'error',
        text: err instanceof ApiError ? err.detail : 'Erro ao excluir usuário.',
      });
    } finally {
      setBusyUserId(null);
    }
  };

  const filteredUsers = useMemo(() => {
    const q = search.trim().toLowerCase();
    return users.filter((u) => {
      if (filterTab === 'ACTIVE' && (u.status !== 'ACTIVE' || u.is_expired)) return false;
      if (filterTab === 'EXPIRING' && (u.is_expired || u.days_remaining > 7 || u.status !== 'ACTIVE')) return false;
      if (filterTab === 'EXPIRED' && !u.is_expired) return false;
      if (filterTab === 'SUSPENDED' && u.status !== 'SUSPENDED') return false;
      if (!q) return true;
      return (
        u.name.toLowerCase().includes(q) ||
        u.email.toLowerCase().includes(q) ||
        (u.active_loginid || '').toLowerCase().includes(q)
      );
    });
  }, [users, search, filterTab]);

  const pgOk = isServiceOk(health?.postgres);
  const redisOk = isServiceOk(health?.redis);
  const workerAlive = Boolean(health?.worker?.alive);
  const activeSessions = health?.worker?.active_sessions ?? health?.active_sessions ?? 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-slate-100 flex items-center gap-2">
            <Shield className="h-5 w-5 text-cyan-400" />
            Painel Administrativo & Saúde do Sistema
          </h1>
          <p className="text-xs text-slate-400">
            Controle multi-usuário, gestão de validade de assinaturas e telemetria do bot-worker. (Privacidade preservada: tokens nunca são exibidos.)
          </p>
        </div>

        <div className="flex items-center gap-2.5">
          <button
            type="button"
            onClick={() => loadAdminData(true)}
            disabled={refreshing}
            className="btn-secondary text-xs"
          >
            <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
            Atualizar
          </button>
          <button type="button" onClick={openCreateModal} className="btn-primary text-xs">
            <Plus className="h-4 w-4" />
            Novo Usuário
          </button>
        </div>
      </div>

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

      {/* System Health Cards */}
      <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 xl:grid-cols-4">
        <div className="glass-card p-4 flex items-center justify-between">
          <div>
            <span className="text-xs text-slate-400 block">PostgreSQL</span>
            <span
              className={`mt-1 inline-flex items-center gap-1.5 font-mono text-sm font-bold ${
                pgOk ? 'text-emerald-400' : 'text-rose-400'
              }`}
            >
              <span className={`h-2 w-2 rounded-full ${pgOk ? 'bg-emerald-400' : 'bg-rose-400'}`} />
              {pgOk ? 'ONLINE' : 'OFFLINE'}
            </span>
          </div>
          <div className="rounded-xl bg-slate-800/80 p-2.5 text-cyan-400">
            <Database className="h-5 w-5" />
          </div>
        </div>

        <div className="glass-card p-4 flex items-center justify-between">
          <div>
            <span className="text-xs text-slate-400 block">Redis Pub/Sub & Locks</span>
            <span
              className={`mt-1 inline-flex items-center gap-1.5 font-mono text-sm font-bold ${
                redisOk ? 'text-emerald-400' : 'text-rose-400'
              }`}
            >
              <span className={`h-2 w-2 rounded-full ${redisOk ? 'bg-emerald-400' : 'bg-rose-400'}`} />
              {redisOk ? 'ONLINE' : 'OFFLINE'}
            </span>
          </div>
          <div className="rounded-xl bg-slate-800/80 p-2.5 text-amber-400">
            <Server className="h-5 w-5" />
          </div>
        </div>

        <div className="glass-card p-4 flex items-center justify-between">
          <div>
            <span className="text-xs text-slate-400 block">Bot-Worker Heartbeat</span>
            <span
              className={`mt-1 inline-flex items-center gap-1.5 font-mono text-sm font-bold ${
                workerAlive ? 'text-emerald-400' : 'text-rose-400'
              }`}
            >
              <span
                className={`h-2 w-2 rounded-full ${
                  workerAlive ? 'bg-emerald-400 animate-pulse' : 'bg-rose-400'
                }`}
              />
              {workerAlive ? 'ATIVO' : 'SEM HEARTBEAT'}
            </span>
            {health?.worker?.age_seconds !== undefined && health?.worker?.age_seconds !== null && (
              <span className="block text-[10px] font-mono text-slate-500 mt-0.5">
                Último pulso há {Math.round(health.worker.age_seconds)}s
              </span>
            )}
          </div>
          <div className="rounded-xl bg-slate-800/80 p-2.5 text-emerald-400">
            <Cpu className="h-5 w-5" />
          </div>
        </div>

        <div className="glass-card p-4 flex items-center justify-between">
          <div>
            <span className="text-xs text-slate-400 block">Sessões & Robôs Ativos</span>
            <div className="mt-1 font-mono text-lg font-bold text-slate-100">
              {activeSessions}{' '}
              <span className="text-xs font-normal text-slate-400">
                sessão(ões) • {health?.open_baskets ?? 0} cesto(s)
              </span>
            </div>
            <span className="block text-[10px] text-slate-500 mt-0.5">
              Total usuários: {health?.total_users ?? users.length}
            </span>
          </div>
          <div className="rounded-xl bg-slate-800/80 p-2.5 text-cyan-400">
            <Activity className="h-5 w-5" />
          </div>
        </div>
      </div>

      {/* Users Management Table */}
      <div className="glass-card overflow-hidden">
        <div className="border-b border-slate-800/80 p-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap items-center gap-1.5">
            {(
              [
                { key: 'ALL', label: `Todos (${users.length})` },
                {
                  key: 'ACTIVE',
                  label: `Ativos (${users.filter((u) => u.status === 'ACTIVE' && !u.is_expired).length})`,
                },
                {
                  key: 'EXPIRING',
                  label: `Vencendo ≤7d (${
                    users.filter((u) => u.status === 'ACTIVE' && !u.is_expired && u.days_remaining <= 7).length
                  })`,
                },
                {
                  key: 'EXPIRED',
                  label: `Expirados (${users.filter((u) => u.is_expired).length})`,
                },
                {
                  key: 'SUSPENDED',
                  label: `Suspensos (${users.filter((u) => u.status === 'SUSPENDED').length})`,
                },
              ] as { key: UserFilterTab; label: string }[]
            ).map((tab) => (
              <button
                key={tab.key}
                type="button"
                onClick={() => setFilterTab(tab.key)}
                className={`rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors ${
                  filterTab === tab.key
                    ? 'border-cyan-500/40 bg-cyan-500/15 text-cyan-300'
                    : 'border-slate-800 bg-slate-950/70 text-slate-400 hover:text-slate-200'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="relative w-full lg:w-72">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Buscar nome, e-mail ou loginid..."
              className="input-dark pl-9 py-1.5 text-xs"
            />
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-slate-800/80 bg-slate-950/70 text-[11px] uppercase text-slate-500">
              <tr>
                <th className="px-4 py-3">Usuário</th>
                <th className="px-4 py-3">Status & Validade</th>
                <th className="px-4 py-3">Radar BTC</th>
                <th className="px-4 py-3">Deriv Conectada</th>
                <th className="px-4 py-3">Robô / Cestos</th>
                <th className="px-4 py-3">Renovação Rápida</th>
                <th className="px-4 py-3 text-right">Ações</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {loading ? (
                <tr>
                  <td colSpan={7} className="px-4 py-10 text-center text-slate-400">
                    <Loader2 className="mx-auto h-5 w-5 animate-spin text-cyan-400 mb-2" />
                    Carregando usuários...
                  </td>
                </tr>
              ) : filteredUsers.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-4 py-10 text-center text-slate-500">
                    <Users className="mx-auto h-6 w-6 text-slate-600 mb-2" />
                    Nenhum usuário encontrado neste filtro.
                  </td>
                </tr>
              ) : (
                filteredUsers.map((u) => {
                  const isBusy = busyUserId === u.id;
                  const statusBadge =
                    u.status === 'SUSPENDED'
                      ? { label: 'SUSPENSO', cls: 'border-rose-500/40 bg-rose-500/15 text-rose-300' }
                      : u.is_expired
                        ? { label: 'EXPIRADO', cls: 'border-rose-500/40 bg-rose-500/15 text-rose-300' }
                        : u.days_remaining <= 5
                          ? { label: 'VENCENDO', cls: 'border-amber-500/40 bg-amber-500/15 text-amber-300' }
                          : { label: 'ATIVO', cls: 'border-emerald-500/35 bg-emerald-500/15 text-emerald-300' };

                  return (
                    <tr key={u.id} className="hover:bg-slate-800/30 transition-colors">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-slate-100">{u.name}</span>
                          {u.role === 'ADMIN' && (
                            <span className="rounded bg-cyan-500/15 border border-cyan-500/30 px-1.5 py-0.5 text-[10px] font-bold text-cyan-300">
                              ADMIN
                            </span>
                          )}
                        </div>
                        <div className="text-[11px] text-slate-400">{u.email}</div>
                        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px]">
                          <span className="font-mono text-slate-500">
                            Stake máx.: {formatUsd(u.max_stake_usd)}
                          </span>
                          <span className="text-slate-700">•</span>
                          <span className="inline-flex items-center gap-1 font-mono text-cyan-300 bg-cyan-950/40 border border-cyan-800/40 px-1.5 py-0.5 rounded">
                            <Globe className="h-3 w-3 text-cyan-400" />
                            {u.current_session_ip || 'Sem IP'}
                          </span>
                        </div>
                      </td>

                      <td className="px-4 py-3">
                        <span
                          className={`inline-flex rounded-md border px-2 py-0.5 text-[10px] font-bold ${statusBadge.cls}`}
                        >
                          {statusBadge.label}
                        </span>
                        <div className="mt-1 font-mono text-[11px] text-slate-300">
                          {u.is_expired
                            ? 'Expirada'
                            : `${u.days_remaining} dia${u.days_remaining === 1 ? '' : 's'} restante${
                                u.days_remaining === 1 ? '' : 's'
                              }`}
                        </div>
                        <div className="text-[10px] text-slate-500">
                          Até {formatDateShort(u.expires_at)}
                        </div>
                      </td>

                      <td className="px-4 py-3">
                        <div className="flex flex-col gap-1.5">
                          <button
                            type="button"
                            disabled={isBusy}
                            onClick={() => handleToggleBtcRadar(u)}
                            className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[10px] font-bold transition-colors ${
                              u.can_access_btc_radar !== false
                                ? 'border-emerald-500/40 bg-emerald-500/15 text-emerald-300 hover:bg-emerald-500/25'
                                : 'border-slate-700 bg-slate-900 text-slate-500 hover:text-slate-300'
                            }`}
                            title="Clique para alternar permissão do Radar BTC"
                          >
                            <span
                              className={`h-1.5 w-1.5 rounded-full ${
                                u.can_access_btc_radar !== false ? 'bg-emerald-400' : 'bg-slate-600'
                              }`}
                            />
                            Radar BTC: {u.can_access_btc_radar !== false ? 'SIM' : 'NÃO'}
                          </button>

                          <button
                            type="button"
                            disabled={isBusy}
                            onClick={() => handleToggleRoboDeriv(u)}
                            className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[10px] font-bold transition-colors ${
                              u.can_access_robo_deriv !== false
                                ? 'border-cyan-500/40 bg-cyan-500/15 text-cyan-300 hover:bg-cyan-500/25'
                                : 'border-slate-700 bg-slate-900 text-slate-500 hover:text-slate-300'
                            }`}
                            title="Clique para alternar permissão do Robô Deriv"
                          >
                            <span
                              className={`h-1.5 w-1.5 rounded-full ${
                                u.can_access_robo_deriv !== false ? 'bg-cyan-400' : 'bg-slate-600'
                              }`}
                            />
                            Robô Deriv: {u.can_access_robo_deriv !== false ? 'SIM' : 'NÃO'}
                          </button>
                        </div>
                      </td>

                      <td className="px-4 py-3">
                        {u.deriv_connected ? (
                          <div className="space-y-1">
                            <span
                              className={`inline-flex rounded px-2 py-0.5 font-mono text-[10px] font-bold ${
                                u.active_account_type === 'REAL'
                                  ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30'
                                  : 'bg-sky-500/15 text-sky-300 border border-sky-500/30'
                              }`}
                            >
                              {u.active_account_type || 'DEMO'}
                            </span>
                            {u.active_loginid && (
                              <div className="font-mono text-[11px] text-slate-300">
                                {u.active_loginid}
                              </div>
                            )}
                          </div>
                        ) : (
                          <span className="text-[11px] text-slate-500">Não conectada</span>
                        )}
                      </td>

                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1.5">
                          <span
                            className={`h-2 w-2 rounded-full ${
                              u.bot_enabled ? 'bg-emerald-400 animate-pulse' : 'bg-slate-600'
                            }`}
                          />
                          <span
                            className={`font-mono text-[11px] font-semibold ${
                              u.bot_enabled ? 'text-emerald-300' : 'text-slate-500'
                            }`}
                          >
                            {u.bot_enabled ? 'ON' : 'OFF'}
                          </span>
                          {u.bot_symbol && (
                            <span className="font-mono text-[10px] text-slate-400">
                              ({u.bot_symbol})
                            </span>
                          )}
                        </div>
                        <div className="mt-1 text-[10px] font-mono text-slate-500">
                          Cestos abertos: {u.open_baskets_count ?? 0}
                        </div>
                      </td>

                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1">
                          {[7, 30, 90].map((d) => (
                            <button
                              key={d}
                              type="button"
                              disabled={isBusy}
                              onClick={() => handleExtend(u, d)}
                              className="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-[11px] text-emerald-300 hover:border-emerald-500/50 hover:bg-emerald-500/15 transition-colors disabled:opacity-50"
                            >
                              +{d}d
                            </button>
                          ))}
                        </div>
                      </td>

                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-1.5">
                          <button
                            type="button"
                            disabled={isBusy}
                            onClick={() => handleToggleStatus(u)}
                            className={`rounded-lg border p-1.5 transition-colors ${
                              u.status === 'ACTIVE'
                                ? 'border-slate-700 bg-slate-900 text-amber-300 hover:border-amber-500/50 hover:bg-amber-500/15'
                                : 'border-emerald-500/40 bg-emerald-500/15 text-emerald-300 hover:bg-emerald-500/25'
                            }`}
                            title={u.status === 'ACTIVE' ? 'Suspender usuário' : 'Ativar usuário'}
                          >
                            {u.status === 'ACTIVE' ? (
                              <Ban className="h-3.5 w-3.5" />
                            ) : (
                              <UserCheck className="h-3.5 w-3.5" />
                            )}
                          </button>
                          <button
                            type="button"
                            disabled={isBusy}
                            onClick={() => openEditModal(u)}
                            className="rounded-lg border border-slate-700 bg-slate-900 p-1.5 text-cyan-300 hover:border-cyan-500/50 hover:bg-cyan-500/15 transition-colors"
                            title="Editar usuário"
                          >
                            <Edit3 className="h-3.5 w-3.5" />
                          </button>
                          <button
                            type="button"
                            disabled={isBusy}
                            onClick={() => handleDeleteUser(u)}
                            className="rounded-lg border border-slate-700 bg-slate-900 p-1.5 text-rose-400 hover:border-rose-500/50 hover:bg-rose-500/15 transition-colors"
                            title="Excluir usuário"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Recent Worker / API Errors */}
      {health?.recent_errors && health.recent_errors.length > 0 && (
        <div className="glass-card overflow-hidden">
          <div className="border-b border-slate-800/80 px-5 py-3 flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-rose-400" />
            <h2 className="text-sm font-semibold text-slate-100">
              Erros Recentes de API / Worker
            </h2>
          </div>
          <div className="divide-y divide-slate-800/60 max-h-60 overflow-y-auto">
            {health.recent_errors.map((err) => (
              <div key={err.id} className="px-5 py-2.5 flex items-center justify-between gap-4 text-xs">
                <div className="min-w-0">
                  <span className="font-mono text-[10px] text-rose-400 mr-2">[{err.level}]</span>
                  <span className="text-slate-300 break-words">{err.message}</span>
                </div>
                <span className="shrink-0 font-mono text-[10px] text-slate-500">
                  {formatDateTime(err.created_at)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Create / Edit User Modal */}
      <Modal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        title={editingUser ? `Editar Usuário — ${editingUser.name}` : 'Cadastrar Novo Usuário'}
        subtitle={
          editingUser
            ? 'Atualize validade, perfil, limite de stake ou defina uma nova senha.'
            : 'Crie uma conta com data de validade controlada e limite de stake individual.'
        }
        maxWidthClass="max-w-lg"
        preventClose={submittingModal}
      >
        <form onSubmit={handleSaveUser} className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-300">Nome completo</label>
              <input
                type="text"
                required
                value={formName}
                onChange={(e) => setFormName(e.target.value)}
                className="input-dark"
                placeholder="João Silva"
              />
            </div>

            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-300">E-mail</label>
              <input
                type="email"
                required
                value={formEmail}
                onChange={(e) => setFormEmail(e.target.value)}
                className="input-dark"
                placeholder="joao@email.com"
              />
            </div>

            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-300">
                {editingUser ? 'Nova senha (opcional)' : 'Senha inicial'}
              </label>
              <input
                type="password"
                required={!editingUser}
                minLength={editingUser ? undefined : 6}
                value={formPassword}
                onChange={(e) => setFormPassword(e.target.value)}
                className="input-dark"
                placeholder={editingUser ? 'Deixe vazio para manter' : 'Mínimo 6 caracteres'}
              />
            </div>

            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-300">
                Validade da Assinatura
              </label>
              <input
                type="date"
                required
                value={formExpiresDate}
                onChange={(e) => setFormExpiresDate(e.target.value)}
                className="input-dark font-mono"
              />
            </div>

            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-300">Perfil (Role)</label>
              <select
                value={formRole}
                onChange={(e) => setFormRole(e.target.value as UserRole)}
                className="input-dark"
              >
                <option value="USER">USER (Operador)</option>
                <option value="ADMIN">ADMIN (Administrador)</option>
              </select>
            </div>

            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-300">Status</label>
              <select
                value={formStatus}
                onChange={(e) => setFormStatus(e.target.value as UserStatus)}
                className="input-dark"
              >
                <option value="ACTIVE">ACTIVE (Liberado)</option>
                <option value="SUSPENDED">SUSPENDED (Bloqueado)</option>
              </select>
            </div>

            <div className="sm:col-span-2">
              <label className="mb-1.5 block text-xs font-medium text-slate-300">
                Stake Máximo Permitido por Entrada (USD)
              </label>
              <input
                type="number"
                step="1"
                min="1"
                required
                value={formMaxStake}
                onChange={(e) => setFormMaxStake(Number(e.target.value))}
                className="input-dark font-mono"
              />
            </div>
          </div>

          <div className="space-y-2">
            <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-2.5">
              <label className="flex items-center gap-2.5 text-xs text-slate-300 cursor-pointer">
                <input
                  type="checkbox"
                  checked={formCanAccessBtcRadar}
                  onChange={(e) => setFormCanAccessBtcRadar(e.target.checked)}
                  className="h-4 w-4 rounded border-slate-600 bg-slate-950 text-cyan-500 focus:ring-cyan-500/40"
                />
                <div>
                  <span className="font-semibold text-cyan-300">Módulo 1: Radar BTC M1 (Próxima Vela)</span>
                  <span className="block text-[11px] text-slate-400">
                    Acesso à análise de microestrutura e fluxo BTC Spot/Perp.
                  </span>
                </div>
              </label>
            </div>

            <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-2.5">
              <label className="flex items-center gap-2.5 text-xs text-slate-300 cursor-pointer">
                <input
                  type="checkbox"
                  checked={formCanAccessRoboDeriv}
                  onChange={(e) => setFormCanAccessRoboDeriv(e.target.checked)}
                  className="h-4 w-4 rounded border-slate-600 bg-slate-950 text-indigo-500 focus:ring-indigo-500/40"
                />
                <div>
                  <span className="font-semibold text-indigo-300">Módulo 2: Robô Deriv (NEXUS QUANTUM AI)</span>
                  <span className="block text-[11px] text-slate-400">
                    Acesso ao motor de trading automatizado de índices sintéticos.
                  </span>
                </div>
              </label>
            </div>

            <label className="flex items-center gap-2.5 text-xs text-slate-300 cursor-pointer">
              <input
                type="checkbox"
                checked={formMustChangePwd}
                onChange={(e) => setFormMustChangePwd(e.target.checked)}
                className="h-4 w-4 rounded border-slate-600 bg-slate-950 text-emerald-500 focus:ring-emerald-500/40"
              />
              Exigir troca de senha no próximo login
            </label>
          </div>

          <div className="flex items-center justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={() => setModalOpen(false)}
              disabled={submittingModal}
              className="btn-secondary"
            >
              Cancelar
            </button>
            <button type="submit" disabled={submittingModal} className="btn-primary">
              {submittingModal ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Salvando...
                </>
              ) : editingUser ? (
                'Salvar Alterações'
              ) : (
                'Criar Usuário'
              )}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
};
