import React, { useMemo, useState } from 'react';
import { Check, Coins, Cpu, DollarSign, Globe, Layers, Search } from 'lucide-react';
import { formatUsd } from '../lib/format';
import type { MarketSymbol } from '../types/api';

interface PairSelectorProps {
  symbols: MarketSymbol[];
  selectedSymbol: string;
  onSelect: (symbol: MarketSymbol) => void;
  loading?: boolean;
}

type CategoryKey = 'ALL' | 'CRYPTO' | 'FOREX' | 'SYNTHETIC' | 'COMMODITIES' | 'OTHER';

function classifyMarket(sym: MarketSymbol): Exclude<CategoryKey, 'ALL'> {
  const m = (sym.market || '').toLowerCase();
  const md = (sym.market_display_name || '').toLowerCase();
  const s = (sym.symbol || '').toLowerCase();

  if (m.includes('crypto') || md.includes('crypto') || s.startsWith('cry')) {
    return 'CRYPTO';
  }
  if (m.includes('forex') || md.includes('forex') || s.startsWith('frx')) {
    return 'FOREX';
  }
  if (
    m.includes('synthetic') ||
    m.includes('derived') ||
    md.includes('synthetic') ||
    md.includes('derived') ||
    s.startsWith('r_') ||
    s.startsWith('1hz') ||
    s.startsWith('boom') ||
    s.startsWith('crash') ||
    s.startsWith('stp') ||
    s.startsWith('jd')
  ) {
    return 'SYNTHETIC';
  }
  if (m.includes('commod') || md.includes('commod') || s.startsWith('wl') || s.includes('xau')) {
    return 'COMMODITIES';
  }
  return 'OTHER';
}

const CATEGORIES: {
  key: CategoryKey;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}[] = [
  { key: 'ALL', label: 'Todos', icon: Globe },
  { key: 'CRYPTO', label: 'Cripto', icon: Coins },
  { key: 'FOREX', label: 'Forex', icon: DollarSign },
  { key: 'SYNTHETIC', label: 'Sintéticos', icon: Cpu },
  { key: 'COMMODITIES', label: 'Commodities', icon: Layers },
];

const CATEGORY_TITLES: Record<Exclude<CategoryKey, 'ALL'>, string> = {
  CRYPTO: 'Criptomoedas',
  FOREX: 'Forex',
  SYNTHETIC: 'Índices Sintéticos',
  COMMODITIES: 'Commodities',
  OTHER: 'Outros Ativos',
};

export const PairSelector: React.FC<PairSelectorProps> = ({
  symbols,
  selectedSymbol,
  onSelect,
  loading = false,
}) => {
  const [search, setSearch] = useState('');
  const [activeCategory, setActiveCategory] = useState<CategoryKey>('ALL');
  const [onlyOpen, setOnlyOpen] = useState(false);

  const grouped = useMemo(() => {
    const query = search.trim().toLowerCase();
    const filtered = symbols.filter((sym) => {
      if (onlyOpen && !sym.is_open) return false;
      const cat = classifyMarket(sym);
      if (activeCategory !== 'ALL' && cat !== activeCategory) return false;
      if (!query) return true;
      return (
        sym.symbol.toLowerCase().includes(query) ||
        sym.display_name.toLowerCase().includes(query) ||
        sym.market_display_name.toLowerCase().includes(query) ||
        sym.submarket_display_name.toLowerCase().includes(query)
      );
    });

    const buckets: Record<Exclude<CategoryKey, 'ALL'>, MarketSymbol[]> = {
      CRYPTO: [],
      FOREX: [],
      SYNTHETIC: [],
      COMMODITIES: [],
      OTHER: [],
    };

    for (const sym of filtered) {
      buckets[classifyMarket(sym)].push(sym);
    }

    return buckets;
  }, [symbols, search, activeCategory, onlyOpen]);

  const totalMatches = Object.values(grouped).reduce((acc, list) => acc + list.length, 0);

  return (
    <div className="glass-card flex flex-col overflow-hidden">
      <div className="border-b border-slate-800/80 p-4 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-base font-semibold text-slate-100">Seletor Dinâmico de Pares Deriv</h2>
            <p className="text-xs text-slate-400">
              Ativos compatíveis com <span className="font-mono text-cyan-300">MULTUP / MULTDOWN</span> (sem expiração por tempo)
            </p>
          </div>
          <label className="inline-flex items-center gap-2 text-xs text-slate-300 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={onlyOpen}
              onChange={(e) => setOnlyOpen(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-slate-600 bg-slate-950 text-emerald-500 focus:ring-emerald-500/40"
            />
            Apenas abertos
          </label>
        </div>

        {/* Search Input */}
        <div className="relative">
          <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Buscar por par ou símbolo (ex.: cryBTCUSD, EUR/USD, R_75, 1HZ100V)..."
            className="input-dark pl-10"
          />
        </div>

        {/* Category Filter Tabs */}
        <div className="flex flex-wrap gap-1.5">
          {CATEGORIES.map((cat) => {
            const Icon = cat.icon;
            const active = activeCategory === cat.key;
            return (
              <button
                key={cat.key}
                type="button"
                onClick={() => setActiveCategory(cat.key)}
                className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-all ${
                  active
                    ? 'border-emerald-500/40 bg-emerald-500/15 text-emerald-300'
                    : 'border-slate-800 bg-slate-950/70 text-slate-400 hover:bg-slate-800/80 hover:text-slate-200'
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                {cat.label}
              </button>
            );
          })}
          <span className="ml-auto self-center font-mono text-[11px] text-slate-500">
            {totalMatches} ativo{totalMatches === 1 ? '' : 's'}
          </span>
        </div>
      </div>

      {/* Grouped List */}
      <div className="max-h-[420px] overflow-y-auto divide-y divide-slate-800/70">
        {loading ? (
          <div className="p-8 text-center text-xs text-slate-400">
            Consultando ativos e multiplicadores ao vivo na API da Deriv...
          </div>
        ) : totalMatches === 0 ? (
          <div className="p-8 text-center text-xs text-slate-400">
            Nenhum ativo encontrado com os filtros atuais.
          </div>
        ) : (
          (Object.keys(grouped) as Exclude<CategoryKey, 'ALL'>[]).map((catKey) => {
            const list = grouped[catKey];
            if (list.length === 0) return null;
            return (
              <div key={catKey}>
                <div className="sticky top-0 z-10 flex items-center justify-between bg-slate-950/95 backdrop-blur px-4 py-2 border-b border-slate-800/70">
                  <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
                    {CATEGORY_TITLES[catKey]}
                  </span>
                  <span className="font-mono text-[10px] text-slate-500">{list.length}</span>
                </div>
                <div className="divide-y divide-slate-800/40">
                  {list.map((sym) => {
                    const isSelected = sym.symbol === selectedSymbol;
                    const mults = Array.isArray(sym.multipliers) ? sym.multipliers : [];
                    return (
                      <button
                        key={sym.symbol}
                        type="button"
                        onClick={() => onSelect(sym)}
                        className={`w-full text-left px-4 py-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between transition-colors ${
                          isSelected
                            ? 'bg-emerald-500/10 border-l-2 border-l-emerald-400'
                            : 'hover:bg-slate-800/40 border-l-2 border-l-transparent'
                        }`}
                      >
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-sm font-semibold text-slate-100">
                              {sym.display_name}
                            </span>
                            <span className="rounded bg-slate-950 border border-slate-800 px-1.5 py-0.5 font-mono text-[11px] text-cyan-300">
                              {sym.symbol}
                            </span>
                            <span
                              className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                                sym.is_open
                                  ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30'
                                  : 'bg-rose-500/15 text-rose-300 border border-rose-500/30'
                              }`}
                            >
                              {sym.is_open ? 'ABERTO' : 'FECHADO'}
                            </span>
                          </div>
                          <div className="mt-1 flex flex-wrap items-center gap-3 text-[11px] text-slate-400">
                            <span>{sym.submarket_display_name || sym.market_display_name}</span>
                            <span className="font-mono">
                              Stake mín.:{' '}
                              <strong className="text-slate-200">{formatUsd(sym.min_stake)}</strong>
                            </span>
                          </div>
                        </div>

                        <div className="flex items-center gap-2 shrink-0">
                          <div className="flex flex-wrap gap-1 justify-end max-w-[220px]">
                            {mults.slice(0, 5).map((m) => (
                              <span
                                key={m}
                                className="rounded bg-slate-900 border border-slate-700/80 px-1.5 py-0.5 font-mono text-[10px] text-slate-300"
                              >
                                {m}x
                              </span>
                            ))}
                            {mults.length > 5 && (
                              <span className="font-mono text-[10px] text-slate-500 self-center">
                                +{mults.length - 5}
                              </span>
                            )}
                          </div>
                          <span
                            className={`inline-flex h-7 w-7 items-center justify-center rounded-lg border transition-colors ${
                              isSelected
                                ? 'border-emerald-400 bg-emerald-500 text-slate-950'
                                : 'border-slate-700 bg-slate-900 text-slate-500'
                            }`}
                          >
                            <Check className="h-4 w-4" />
                          </span>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
