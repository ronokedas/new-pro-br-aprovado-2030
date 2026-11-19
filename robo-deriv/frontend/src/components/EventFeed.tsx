import React from 'react';
import { AlertTriangle, CheckCircle2, Info, Radio, XCircle, Zap } from 'lucide-react';
import { formatTimeOnly } from '../lib/format';
import type { BotEventLogItem } from '../types/api';

interface EventFeedProps {
  events: BotEventLogItem[];
  maxHeightClass?: string;
}

function getLevelMeta(level: string) {
  switch ((level || 'INFO').toUpperCase()) {
    case 'TRADE':
      return {
        label: 'ORDEM',
        badge: 'border-emerald-500/35 bg-emerald-500/15 text-emerald-300',
        icon: CheckCircle2,
        iconColor: 'text-emerald-400',
      };
    case 'SIGNAL':
      return {
        label: 'SINAL',
        badge: 'border-cyan-500/35 bg-cyan-500/15 text-cyan-300',
        icon: Zap,
        iconColor: 'text-cyan-400',
      };
    case 'WARN':
    case 'WARNING':
      return {
        label: 'ALERTA',
        badge: 'border-amber-500/35 bg-amber-500/15 text-amber-300',
        icon: AlertTriangle,
        iconColor: 'text-amber-400',
      };
    case 'ERROR':
      return {
        label: 'ERRO',
        badge: 'border-rose-500/35 bg-rose-500/15 text-rose-300',
        icon: XCircle,
        iconColor: 'text-rose-400',
      };
    default:
      return {
        label: 'INFO',
        badge: 'border-slate-700 bg-slate-800 text-slate-300',
        icon: Info,
        iconColor: 'text-slate-400',
      };
  }
}

export const EventFeed: React.FC<EventFeedProps> = ({
  events,
  maxHeightClass = 'max-h-[410px]',
}) => {
  return (
    <div className="glass-card flex flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-slate-800/80 px-4 py-3">
        <div className="flex items-center gap-2">
          <Radio className="h-4 w-4 text-cyan-400 animate-pulse" />
          <h3 className="text-sm font-semibold text-slate-100">Feed de Eventos Ao Vivo</h3>
        </div>
        <span className="font-mono text-[11px] text-slate-500">
          {events.length} registro{events.length === 1 ? '' : 's'}
        </span>
      </div>

      <div className={`flex-1 overflow-y-auto divide-y divide-slate-800/60 ${maxHeightClass}`}>
        {events.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
            <Info className="h-7 w-7 text-slate-600 mb-2" />
            <p className="text-xs font-medium text-slate-400">Nenhum evento registrado ainda.</p>
            <p className="mt-1 text-[11px] text-slate-500">
              Sinais de canal, confirmações de entrada, reentradas DCA e encerramentos aparecerão aqui em tempo real.
            </p>
          </div>
        ) : (
          events.map((ev) => {
            const meta = getLevelMeta(ev.level);
            const Icon = meta.icon;
            return (
              <div
                key={`${ev.id}-${ev.created_at}`}
                className="flex items-start gap-3 px-4 py-3 hover:bg-slate-800/30 transition-colors"
              >
                <Icon className={`h-4 w-4 shrink-0 mt-0.5 ${meta.iconColor}`} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5 mb-1">
                    <span
                      className={`rounded border px-1.5 py-0.5 font-mono text-[10px] font-semibold ${meta.badge}`}
                    >
                      {meta.label}
                    </span>
                    {ev.symbol && (
                      <span className="rounded bg-slate-950/80 border border-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                        {ev.symbol}
                      </span>
                    )}
                    <span className="ml-auto font-mono text-[10px] text-slate-500">
                      {formatTimeOnly(ev.created_at)}
                    </span>
                  </div>
                  <p className="text-xs text-slate-200 leading-relaxed break-words">{ev.message}</p>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
