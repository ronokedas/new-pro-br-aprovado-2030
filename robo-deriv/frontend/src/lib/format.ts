export function formatUsd(value: number | null | undefined, currency = 'USD', signed = false): string {
  const num = typeof value === 'number' && Number.isFinite(value) ? value : 0;
  const abs = Math.abs(num);
  const formatted = abs.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const prefix = currency === 'USD' ? '$' : `${currency} `;
  if (num < 0) return `-${prefix}${formatted}`;
  if (signed && num > 0) return `+${prefix}${formatted}`;
  return `${prefix}${formatted}`;
}

export function formatPrice(value: number | null | undefined, pipSize?: number): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  let decimals = 2;
  if (pipSize && pipSize > 0 && pipSize < 1) {
    decimals = Math.min(6, Math.max(2, Math.round(-Math.log10(pipSize))));
  } else if (Math.abs(value) < 10) {
    decimals = 5;
  } else if (Math.abs(value) < 500) {
    decimals = 3;
  }
  return value.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function formatPct(value: number | null | undefined, signed = false, digits = 2): string {
  const num = typeof value === 'number' && Number.isFinite(value) ? value : 0;
  const sign = signed && num > 0 ? '+' : '';
  return `${sign}${num.toFixed(digits)}%`;
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    year: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function formatDateShort(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });
}

export function formatTimeOnly(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString('pt-BR', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function formatDuration(startIso: string | null | undefined, endIso?: string | null): string {
  if (!startIso) return '—';
  const start = new Date(startIso).getTime();
  const end = endIso ? new Date(endIso).getTime() : Date.now();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return '—';
  const sec = Math.floor((end - start) / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  const remSec = sec % 60;
  if (min < 60) return `${min}m ${remSec}s`;
  const hrs = Math.floor(min / 60);
  const remMin = min % 60;
  return `${hrs}h ${remMin}m`;
}

export function parseLayerMultipliers(jsonStr: string | null | undefined, maxLayers: number): number[] {
  try {
    const parsed = JSON.parse(jsonStr || '[1.0, 1.0, 1.5]');
    if (Array.isArray(parsed) && parsed.length > 0) {
      const nums = parsed.map((v) => {
        const n = Number(v);
        return Number.isFinite(n) && n > 0 ? n : 1.0;
      });
      while (nums.length < maxLayers) {
        nums.push(nums[nums.length - 1] ?? 1.0);
      }
      return nums.slice(0, maxLayers);
    }
  } catch {
    // ignore malformed json
  }
  const fallback = [1.0, 1.0, 1.5, 1.5, 1.5];
  return fallback.slice(0, Math.max(1, maxLayers));
}

export function translateCloseReason(reason: string | null | undefined): { label: string; tone: string } {
  switch ((reason || '').toUpperCase()) {
    case 'TP':
    case 'TAKE_PROFIT':
      return { label: 'Take Profit', tone: 'text-emerald-300 bg-emerald-500/15 border-emerald-500/30' };
    case 'TP_ATR':
      return { label: 'Alvo ATR (Vídeo)', tone: 'text-emerald-300 bg-emerald-500/15 border-emerald-500/30' };
    case 'SL_ATR':
      return { label: 'Stop ATR (Vídeo)', tone: 'text-rose-300 bg-rose-500/15 border-rose-500/30' };
    case 'TRAILING':
    case 'TRAILING_TP':
      return { label: 'Trailing Profit', tone: 'text-cyan-300 bg-cyan-500/15 border-cyan-500/30' };
    case 'STOP_LOSS':
      return { label: 'Stop Global', tone: 'text-rose-300 bg-rose-500/15 border-rose-500/30' };
    case 'STOP_OUT':
      return { label: 'Stop-Out Deriv', tone: 'text-rose-300 bg-rose-500/20 border-rose-500/40' };
    case 'CHANNEL_BREAK':
      return { label: 'Rompimento Canal', tone: 'text-amber-300 bg-amber-500/15 border-amber-500/30' };
    case 'KILL_SWITCH':
      return { label: 'Kill Switch', tone: 'text-rose-200 bg-rose-600/25 border-rose-500/50' };
    case 'TIMEOUT':
      return { label: 'Timeout', tone: 'text-amber-200 bg-amber-500/15 border-amber-500/30' };
    case 'MANUAL':
      return { label: 'Manual', tone: 'text-slate-300 bg-slate-700/40 border-slate-600/50' };
    default:
      return { label: reason || 'Encerrado', tone: 'text-slate-300 bg-slate-800 border-slate-700' };
  }
}

export function translateBotStatus(statusText: string | null | undefined, enabled: boolean): {
  label: string;
  colorClass: string;
  dotClass: string;
} {
  const s = (statusText || (enabled ? 'SCANNING' : 'OFF')).toUpperCase();
  if (s.includes('KILL') || s.includes('STOP_DAILY') || s.includes('DAILY_STOP') || s.includes('STOP DIÁRIO')) {
    return {
      label: 'STOP DIÁRIO',
      colorClass: 'bg-rose-500/15 text-rose-300 border-rose-500/35',
      dotClass: 'bg-rose-400',
    };
  }
  if (s.includes('EXPIRED') || s.includes('EXPIRAD') || s.includes('SUSPENDED')) {
    return {
      label: 'LICENÇA EXPIRADA',
      colorClass: 'bg-rose-500/15 text-rose-300 border-rose-500/35',
      dotClass: 'bg-rose-400',
    };
  }
  if (s.includes('IN_TRADE') || s.includes('OPEN') || s.includes('OPERANDO') || s.includes('EM OPERAÇÃO')) {
    return {
      label: 'EM OPERAÇÃO',
      colorClass: 'bg-cyan-500/15 text-cyan-300 border-cyan-500/35',
      dotClass: 'bg-cyan-400 animate-pulse',
    };
  }
  if (s.includes('COOLDOWN')) {
    return {
      label: 'COOLDOWN',
      colorClass: 'bg-amber-500/15 text-amber-300 border-amber-500/35',
      dotClass: 'bg-amber-400',
    };
  }
  if (s.includes('TARGET') || s.includes('META')) {
    return {
      label: 'META DIÁRIA ATINGIDA',
      colorClass: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/35',
      dotClass: 'bg-emerald-400',
    };
  }
  if (enabled || s === 'ON' || s === 'SCANNING' || s === 'MONITORING' || s === 'RUNNING' || s === 'LIGADO') {
    return {
      label: 'LIGADO • MONITORANDO',
      colorClass: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/35',
      dotClass: 'bg-emerald-400 animate-pulse',
    };
  }
  return {
    label: 'DESLIGADO',
    colorClass: 'bg-slate-800/80 text-slate-400 border-slate-700',
    dotClass: 'bg-slate-500',
  };
}
