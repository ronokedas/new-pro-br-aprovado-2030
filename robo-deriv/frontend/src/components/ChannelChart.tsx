import React, { useEffect, useRef } from 'react';
import {
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts';
import { Activity, RefreshCw, Target, ShieldAlert } from 'lucide-react';
import { formatPrice } from '../lib/format';
import type {
  ArmedOrderInfo,
  BasketItem,
  BotConfigData,
  BotEventLogItem,
  ChannelData,
  ChartCandle,
  ContractItem,
  SyntraTradeItem,
} from '../types/api';

interface ChannelChartProps {
  symbol: string;
  granularity: number;
  candles: ChartCandle[];
  channel?: ChannelData | null;
  openBasket?: BasketItem | null;
  openContracts?: ContractItem[];
  basketTpUsd?: number;
  liveTick?: { price: number; time: number } | null;
  loading?: boolean;
  armedOrder?: ArmedOrderInfo | null;
  config?: BotConfigData | null;
  recentEvents?: BotEventLogItem[];
  recentTrades?: SyntraTradeItem[];
  onRefresh?: () => void;
}

const TIMEFRAME_LABELS: Record<number, string> = {
  1: '1s (Tick)',
  60: 'M1',
  180: 'M3',
  300: 'M5',
  900: 'M15',
  1800: 'M30',
  3600: 'H1',
};

function normalizeTime(t: number): UTCTimestamp {
  const sec = t > 1e12 ? Math.floor(t / 1000) : Math.floor(t);
  return sec as UTCTimestamp;
}

function dedupeSortCandles(candles: ChartCandle[]): CandlestickData<Time>[] {
  const map = new Map<number, CandlestickData<Time>>();
  for (const c of candles) {
    if (
      !Number.isFinite(c.time) ||
      !Number.isFinite(c.open) ||
      !Number.isFinite(c.high) ||
      !Number.isFinite(c.low) ||
      !Number.isFinite(c.close)
    ) {
      continue;
    }
    const ts = normalizeTime(c.time);
    map.set(ts, {
      time: ts,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    });
  }
  return Array.from(map.values()).sort((a, b) => Number(a.time) - Number(b.time));
}

export const ChannelChart: React.FC<ChannelChartProps> = ({
  symbol,
  granularity,
  candles,
  openBasket,
  openContracts = [],
  basketTpUsd = 0,
  liveTick,
  loading = false,
  config,
  recentTrades = [],
  onRefresh,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const lastCandleRef = useRef<CandlestickData<Time> | null>(null);

  // Initialize TradingView Lightweight Chart (Clean Candlestick View)
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = createChart(el, {
      width: el.clientWidth,
      height: 420,
      layout: {
        background: { type: ColorType.Solid, color: '#070b16' },
        textColor: '#94a3b8',
        fontFamily: 'JetBrains Mono, ui-monospace, monospace',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: 'rgba(30, 41, 59, 0.45)' },
        horzLines: { color: 'rgba(30, 41, 59, 0.45)' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: 'rgba(6, 182, 212, 0.5)',
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: '#0e172a',
        },
        horzLine: {
          color: 'rgba(6, 182, 212, 0.5)',
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: '#0e172a',
        },
      },
      rightPriceScale: {
        borderColor: 'rgba(51, 65, 85, 0.6)',
        scaleMargins: { top: 0.12, bottom: 0.14 },
      },
      timeScale: {
        borderColor: 'rgba(51, 65, 85, 0.6)',
        timeVisible: true,
        secondsVisible: false,
      },
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#f43f5e',
      borderUpColor: '#10b981',
      borderDownColor: '#f43f5e',
      wickUpColor: '#10b981',
      wickDownColor: '#f43f5e',
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;

    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        if (entry.contentRect.width > 0) {
          chart.applyOptions({ width: Math.floor(entry.contentRect.width) });
        }
      }
    });
    resizeObserver.observe(el);

    return () => {
      resizeObserver.disconnect();
      priceLinesRef.current = [];
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
    };
  }, []);

  // Update candles
  useEffect(() => {
    const candleSeries = candleSeriesRef.current;
    const chart = chartRef.current;
    if (!candleSeries || !chart) return;

    const sortedCandles = dedupeSortCandles(candles);
    candleSeries.setData(sortedCandles);
    lastCandleRef.current = sortedCandles.length > 0 ? sortedCandles[sortedCandles.length - 1] : null;

    if (sortedCandles.length > 0) {
      chart.timeScale().fitContent();
    }
  }, [candles]);

  // Update open position markings (DCA entries, Breakeven, Take Profit, Stop Loss)
  useEffect(() => {
    const candleSeries = candleSeriesRef.current;
    if (!candleSeries) return;

    // Clear existing price lines
    for (const line of priceLinesRef.current) {
      try {
        candleSeries.removePriceLine(line);
      } catch {
        // ignore
      }
    }
    priceLinesRef.current = [];

    const activeContracts = openContracts.filter((c) => c.status === 'OPEN' && c.entry_spot > 0);

    // Position is open: show entry spots, average price, target and stop
    if (openBasket && activeContracts.length > 0) {
      // Plot each DCA layer entry line
      activeContracts.forEach((c) => {
        const line = candleSeries.createPriceLine({
          price: c.entry_spot,
          color: '#22d3ee',
          lineWidth: 2,
          lineStyle: LineStyle.Solid,
          axisLabelVisible: true,
          title: `ENTRADA #${c.layer_index} ($${c.stake_usd.toFixed(2)}) @ ${formatPrice(c.entry_spot)}`,
        });
        priceLinesRef.current.push(line);
      });

      // Breakeven / Preço Médio
      if (openBasket.avg_entry_price > 0) {
        priceLinesRef.current.push(
          candleSeries.createPriceLine({
            price: openBasket.avg_entry_price,
            color: '#f59e0b',
            lineWidth: 2,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title: `PREÇO MÉDIO @ ${formatPrice(openBasket.avg_entry_price)}`,
          })
        );
      }

      // Dynamic Take Profit & Stop Loss
      const dirSign = openBasket.direction === 'MULTDOWN' ? -1 : 1;
      const totalExposure = activeContracts.reduce((acc, c) => acc + c.stake_usd * c.multiplier, 0);

      if (totalExposure > 0 && openBasket.avg_entry_price > 0) {
        const targetTp = basketTpUsd > 0 ? basketTpUsd : 0.50;
        const commission = openBasket.total_commission_usd || (activeContracts.length * 0.05);
        const neededMovePct = (targetTp + commission) / totalExposure;
        const estimatedTp = openBasket.avg_entry_price * (1 + dirSign * neededMovePct);
        if (estimatedTp > 0) {
          priceLinesRef.current.push(
            candleSeries.createPriceLine({
              price: estimatedTp,
              color: '#10b981',
              lineWidth: 2,
              lineStyle: LineStyle.Dotted,
              axisLabelVisible: true,
              title: `🎯 ALVO TP (+$${targetTp.toFixed(2)}) @ ${formatPrice(estimatedTp)}`,
            })
          );
        }

        const maxLoss = config?.max_basket_loss_usd ?? 5.0;
        if (maxLoss > 0) {
          const neededLossPct = maxLoss / totalExposure;
          const estimatedSl = openBasket.avg_entry_price * (1 - dirSign * neededLossPct);
          if (estimatedSl > 0) {
            priceLinesRef.current.push(
              candleSeries.createPriceLine({
                price: estimatedSl,
                color: '#ef4444',
                lineWidth: 2,
                lineStyle: LineStyle.Dashed,
                axisLabelVisible: true,
                title: `🛑 STOP LOSS (-$${maxLoss.toFixed(2)}) @ ${formatPrice(estimatedSl)}`,
              })
            );
          }
        }
      }
    }

    // Add entry markers on candles for open contracts and recent tick trade outcomes
    if (candles.length > 0) {
      const sortedTimes = candles
        .map((c) => normalizeTime(c.time))
        .sort((a, b) => a - b);
      const markers: SeriesMarker<Time>[] = [];

      for (const c of activeContracts) {
        const entrySec = Math.floor(new Date(c.opened_at).getTime() / 1000);
        if (!Number.isFinite(entrySec)) continue;
        let matched = sortedTimes[sortedTimes.length - 1];
        for (let i = sortedTimes.length - 1; i >= 0; i -= 1) {
          if (sortedTimes[i] <= entrySec) {
            matched = sortedTimes[i];
            break;
          }
        }
        if (matched !== undefined) {
          const isUp = c.contract_type !== 'MULTDOWN' && c.contract_type !== 'PUT';
          markers.push({
            time: matched as Time,
            position: isUp ? 'belowBar' : 'aboveBar',
            color: isUp ? '#10b981' : '#f43f5e',
            shape: isUp ? 'arrowUp' : 'arrowDown',
            text: `E#${c.layer_index} ${isUp ? 'COMPRA' : 'VENDA'}`,
          });
        }
      }

      // Add floating profit / loss tags from recent tick trades (matching Syntra video)
      if (recentTrades && recentTrades.length > 0) {
        for (const t of recentTrades) {
          if (t.symbol && symbol && t.symbol !== symbol) continue;
          const tradeSec = Math.floor(t.epoch);
          let matched = sortedTimes[sortedTimes.length - 1];
          for (let i = sortedTimes.length - 1; i >= 0; i -= 1) {
            if (sortedTimes[i] <= tradeSec) {
              matched = sortedTimes[i];
              break;
            }
          }
          if (matched !== undefined) {
            const isWin = t.profit_usd > 0;
            markers.push({
              time: matched as Time,
              position: isWin ? 'aboveBar' : 'belowBar',
              color: isWin ? '#10b981' : '#f43f5e',
              shape: isWin ? 'arrowUp' : 'arrowDown',
              text: isWin ? `+$${t.profit_usd.toFixed(2)}` : `-$${Math.abs(t.profit_usd).toFixed(2)}`,
            });
          }
        }
      }

      markers.sort((a, b) => Number(a.time) - Number(b.time));
      candleSeries.setMarkers(markers);
    } else {
      candleSeries.setMarkers([]);
    }
  }, [openBasket, openContracts, basketTpUsd, candles, config, recentTrades, symbol]);

  // Apply live WebSocket tick to latest candle smoothly
  useEffect(() => {
    if (!liveTick || !candleSeriesRef.current || !lastCandleRef.current) return;
    if (!Number.isFinite(liveTick.price) || liveTick.price <= 0) return;

    const bucketSec = Math.max(60, granularity || 60);
    const tickSec = normalizeTime(liveTick.time || Math.floor(Date.now() / 1000));
    const bucketTime = (Math.floor(tickSec / bucketSec) * bucketSec) as UTCTimestamp;
    const prev = lastCandleRef.current;
    const prevTime = Number(prev.time);

    if (bucketTime < prevTime) return;

    const updated: CandlestickData<Time> =
      bucketTime === prevTime
        ? {
            time: prev.time,
            open: prev.open,
            high: Math.max(prev.high, liveTick.price),
            low: Math.min(prev.low, liveTick.price),
            close: liveTick.price,
          }
        : {
            time: bucketTime,
            open: prev.close,
            high: Math.max(prev.close, liveTick.price),
            low: Math.min(prev.close, liveTick.price),
            close: liveTick.price,
          };

    try {
      candleSeriesRef.current.update(updated);
      lastCandleRef.current = updated;
    } catch {
      // ignore out-of-order updates
    }
  }, [liveTick, granularity]);

  const tfLabel = TIMEFRAME_LABELS[granularity] || `${Math.round(granularity / 60)}m`;

  return (
    <div className="glass-card overflow-hidden">
      {/* Header Telemetry Bar - Clean View */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800/80 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-emerald-400" />
            <span className="font-mono text-sm font-bold text-slate-100">{symbol}</span>
            <span className="rounded-md border border-slate-700 bg-slate-800/80 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-cyan-300">
              {tfLabel}
            </span>
          </div>

          {liveTick && (
            <span className="rounded-lg border border-slate-800 bg-slate-950/80 px-2.5 py-1 font-mono text-xs font-semibold text-slate-100 tabular-nums">
              Spot: {formatPrice(liveTick.price)}
            </span>
          )}
        </div>

        {/* Legend / Refresh Bar */}
        <div className="flex items-center gap-2">
          {openBasket && (
            <div className="hidden sm:flex flex-wrap items-center gap-2.5 text-[11px] text-slate-400 mr-2">
              <span className="inline-flex items-center gap-1 text-emerald-300 font-semibold">
                <Target className="h-3 w-3" /> Alvo TP
              </span>
              <span className="inline-flex items-center gap-1 text-rose-400 font-semibold">
                <ShieldAlert className="h-3 w-3" /> Stop SL
              </span>
            </div>
          )}
          {onRefresh && (
            <button
              type="button"
              onClick={onRefresh}
              disabled={loading}
              className="rounded-lg border border-slate-800 bg-slate-900/90 p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100 transition-colors"
              title="Recarregar velas"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>
          )}
        </div>
      </div>

      {/* Chart Canvas */}
      <div className="relative">
        <div ref={containerRef} className="w-full h-[420px]" />
        {!loading && candles.length === 0 && (
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center bg-slate-950/60 text-center px-6">
            <Activity className="h-8 w-8 text-slate-600 mb-2" />
            <p className="text-sm font-medium text-slate-300">
              Aguardando dados de velas para {symbol}
            </p>
            <p className="mt-1 text-xs text-slate-500 max-w-md">
              Os candles aparecerão automaticamente assim que sincronizados pela Deriv.
            </p>
          </div>
        )}
      </div>
    </div>
  );
};
