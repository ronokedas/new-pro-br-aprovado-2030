import React, { useEffect, useMemo, useRef, useState } from 'react';
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
import { Activity, CandlestickChart as CandleIcon, LineChart as LineIcon, RefreshCw } from 'lucide-react';
import { formatPrice } from '../lib/format';
import type { ChartCandle, NexusTradeItem } from '../types/api';

export interface OpenTradeVisual {
  symbol: string;
  direction: 'MULTUP' | 'MULTDOWN' | 'CALL' | 'PUT' | string;
  entry_spot: number;
  current_spot?: number;
  current_profit_usd?: number;
  stake_usd?: number;
  layers_count?: number;
}

interface NexusLineChartProps {
  symbol: string;
  category?: string;
  granularity: number; // 1 (1T), 60 (1m), 300 (5m)
  candles: ChartCandle[];
  liveTick?: { price: number; time: number } | null;
  recentTrades?: NexusTradeItem[];
  openTrade?: OpenTradeVisual | null;
  indicators?: {
    ma6?: number | null;
    ma14?: number | null;
    ma250?: number | null;
    rsi5?: number | null;
  } | null;
  onTimeframeChange?: (tf: number) => void;
  onRefresh?: () => void;
  loading?: boolean;
}

const GRANULARITY_OPTIONS = [
  { value: 1, label: '1T (Tick a Tick)' },
  { value: 60, label: '1m (M1)' },
  { value: 300, label: '5m (M5)' },
];

function normalizeTime(t: number): UTCTimestamp {
  if (!t || t < 1e8) {
    return Math.floor(Date.now() / 1000) as UTCTimestamp;
  }
  const sec = t > 1e12 ? Math.floor(t / 1000) : Math.floor(t);
  return sec as UTCTimestamp;
}

function computeAdaptiveSMA(
  data: { time: UTCTimestamp; value: number }[],
  targetPeriod: number
): { time: UTCTimestamp; value: number }[] {
  const result: { time: UTCTimestamp; value: number }[] = [];
  if (data.length === 0) return result;
  let sum = 0;
  for (let i = 0; i < data.length; i++) {
    sum += data[i].value;
    if (i >= targetPeriod) {
      sum -= data[i - targetPeriod].value;
      result.push({ time: data[i].time, value: sum / targetPeriod });
    } else {
      // Warmup phase: progressive moving average so line is always smoothly drawn from candle 0
      result.push({ time: data[i].time, value: sum / (i + 1) });
    }
  }
  return result;
}

export const NexusLineChart: React.FC<NexusLineChartProps> = ({
  symbol,
  category,
  granularity,
  candles,
  liveTick,
  recentTrades = [],
  openTrade,
  indicators,
  onTimeframeChange,
  onRefresh,
  loading = false,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);

  // Series refs
  const areaSeriesRef = useRef<ISeriesApi<'Area'> | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const ma6SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const ma14SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const ma250SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const entryPriceLineRef = useRef<IPriceLine | null>(null);

  const lastTimeRef = useRef<number>(0);
  const currentCandleOpenRef = useRef<number | null>(null);
  const currentCandleEpochRef = useRef<number | null>(null);
  const currentCandleHighRef = useRef<number | null>(null);
  const currentCandleLowRef = useRef<number | null>(null);
  const [chartType, setChartType] = useState<'CANDLES' | 'LINE'>('CANDLES');

  // Compute category label
  const resolvedCategory = useMemo(() => {
    if (category) return category;
    if (symbol.startsWith('cry')) return 'Cripto';
    if (symbol.startsWith('frx')) return 'Forex';
    return 'Sintéticos';
  }, [category, symbol]);

  // Initialize or re-create chart whenever container or chartType or granularity changes
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
      areaSeriesRef.current = null;
      candleSeriesRef.current = null;
      ma6SeriesRef.current = null;
      ma14SeriesRef.current = null;
      ma250SeriesRef.current = null;
      entryPriceLineRef.current = null;
    }

    const chart = createChart(el, {
      width: el.clientWidth,
      height: 440,
      layout: {
        background: { type: ColorType.Solid, color: '#090d16' },
        textColor: '#94a3b8',
        fontFamily: 'JetBrains Mono, ui-monospace, monospace',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: 'rgba(30, 41, 59, 0.35)' },
        horzLines: { color: 'rgba(30, 41, 59, 0.35)' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: 'rgba(56, 189, 248, 0.45)',
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: '#0f172a',
        },
        horzLine: {
          color: 'rgba(56, 189, 248, 0.45)',
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: '#0f172a',
        },
      },
      rightPriceScale: {
        borderColor: 'rgba(51, 65, 85, 0.5)',
        scaleMargins: { top: 0.12, bottom: 0.12 },
      },
      timeScale: {
        borderColor: 'rgba(51, 65, 85, 0.5)',
        timeVisible: true,
        secondsVisible: granularity === 1,
      },
    });

    if (chartType === 'CANDLES') {
      // 1. Candlestick Series (M1 / M5)
      const cSeries = chart.addCandlestickSeries({
        upColor: '#10b981',
        downColor: '#f43f5e',
        borderVisible: false,
        wickUpColor: '#10b981',
        wickDownColor: '#f43f5e',
        priceLineVisible: true,
      });
      candleSeriesRef.current = cSeries;

      // 2. Moving Average Overlays (3 MAs: MA6 Green, MA14 White, MA250 Red)
      const ma6 = chart.addLineSeries({
        color: '#10b981', // Verde
        lineWidth: 1,
        title: 'MA 6',
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      });
      ma6SeriesRef.current = ma6;

      const ma14 = chart.addLineSeries({
        color: '#f8fafc', // Branca
        lineWidth: 1,
        title: 'MA 14',
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      });
      ma14SeriesRef.current = ma14;

      const ma250 = chart.addLineSeries({
        color: '#ef4444', // Vermelha
        lineWidth: 2,
        title: 'MA 250',
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      });
      ma250SeriesRef.current = ma250;
    } else {
      // Line / Area Series (Tick a Tick)
      const aSeries = chart.addAreaSeries({
        topColor: 'rgba(14, 165, 233, 0.28)',
        bottomColor: 'rgba(14, 165, 233, 0.01)',
        lineColor: '#38bdf8',
        lineWidth: 2,
        priceLineVisible: true,
        priceLineColor: '#38bdf8',
        crosshairMarkerVisible: true,
        crosshairMarkerRadius: 4,
        crosshairMarkerBorderColor: '#0369a1',
        crosshairMarkerBackgroundColor: '#38bdf8',
      });
      areaSeriesRef.current = aSeries;
    }

    chartRef.current = chart;

    const handleResize = () => {
      if (el && chart) {
        chart.applyOptions({ width: el.clientWidth });
      }
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
      chartRef.current = null;
      areaSeriesRef.current = null;
      candleSeriesRef.current = null;
      ma6SeriesRef.current = null;
      ma14SeriesRef.current = null;
      ma250SeriesRef.current = null;
      entryPriceLineRef.current = null;
      lastTimeRef.current = 0;
      currentCandleOpenRef.current = null;
      currentCandleEpochRef.current = null;
      currentCandleHighRef.current = null;
      currentCandleLowRef.current = null;
    };
  }, [chartType, granularity, symbol]);

  // Populate data whenever candles change
  useEffect(() => {
    if (!candles || candles.length === 0) return;

    if (chartType === 'CANDLES' && candleSeriesRef.current) {
      const cPoints: CandlestickData<Time>[] = [];
      const closePoints: { time: UTCTimestamp; value: number }[] = [];
      const seen = new Set<number>();

      for (const c of candles) {
        if (!Number.isFinite(c.time) || !Number.isFinite(c.close)) continue;
        const ts = normalizeTime(c.time);
        if (seen.has(ts)) continue;
        seen.add(ts);

        const op = Number.isFinite(c.open) ? c.open : c.close;
        const hi = Number.isFinite(c.high) ? Math.max(c.high, op, c.close) : Math.max(op, c.close);
        const lo = Number.isFinite(c.low) ? Math.min(c.low, op, c.close) : Math.min(op, c.close);

        cPoints.push({
          time: ts,
          open: op,
          high: hi,
          low: lo,
          close: c.close,
        });

        closePoints.push({ time: ts, value: c.close });
      }

      cPoints.sort((a, b) => Number(a.time) - Number(b.time));
      closePoints.sort((a, b) => Number(a.time) - Number(b.time));

      if (cPoints.length > 0) {
        candleSeriesRef.current.setData(cPoints);
        lastTimeRef.current = Number(cPoints[cPoints.length - 1].time);

        // Compute and populate MAs
        if (ma6SeriesRef.current) {
          ma6SeriesRef.current.setData(computeAdaptiveSMA(closePoints, 6));
        }
        if (ma14SeriesRef.current) {
          ma14SeriesRef.current.setData(computeAdaptiveSMA(closePoints, 14));
        }
        if (ma250SeriesRef.current) {
          ma250SeriesRef.current.setData(computeAdaptiveSMA(closePoints, 250));
        }

        chartRef.current?.timeScale().fitContent();
      }
    } else if (chartType === 'LINE' && areaSeriesRef.current) {
      const linePoints: { time: Time; value: number }[] = [];
      const seen = new Set<number>();

      for (const c of candles) {
        if (!Number.isFinite(c.time) || !Number.isFinite(c.close)) continue;
        const ts = normalizeTime(c.time);
        if (seen.has(ts)) continue;
        seen.add(ts);
        linePoints.push({ time: ts, value: c.close });
      }

      linePoints.sort((a, b) => Number(a.time) - Number(b.time));
      if (linePoints.length > 0) {
        areaSeriesRef.current.setData(linePoints);
        lastTimeRef.current = Number(linePoints[linePoints.length - 1].time);
        chartRef.current?.timeScale().fitContent();
      }
    }
  }, [candles, chartType]);

  // Real-time tick update
  useEffect(() => {
    if (!liveTick || !Number.isFinite(liveTick.price) || liveTick.price <= 0) return;

    const tickTime = normalizeTime(liveTick.time || Date.now() / 1000);
    const effectiveTime = Math.max(tickTime, (lastTimeRef.current || 0) + (granularity === 1 ? 1 : 0));
    lastTimeRef.current = effectiveTime;

    if (chartType === 'LINE' && areaSeriesRef.current) {
      areaSeriesRef.current.update({
        time: effectiveTime as UTCTimestamp,
        value: liveTick.price,
      });
    } else if (chartType === 'CANDLES' && candleSeriesRef.current) {
      // In candles mode, update forming candle preserving open, high and low
      const bucket = granularity === 1 ? 60 : granularity;
      const candleEpoch = (Math.floor(effectiveTime / bucket) * bucket) as UTCTimestamp;

      if (currentCandleEpochRef.current !== candleEpoch) {
        currentCandleEpochRef.current = candleEpoch;
        currentCandleOpenRef.current = liveTick.price;
        currentCandleHighRef.current = liveTick.price;
        currentCandleLowRef.current = liveTick.price;
      } else {
        currentCandleHighRef.current = Math.max(currentCandleHighRef.current ?? liveTick.price, liveTick.price);
        currentCandleLowRef.current = Math.min(currentCandleLowRef.current ?? liveTick.price, liveTick.price);
      }

      const openVal = currentCandleOpenRef.current ?? liveTick.price;
      const highVal = currentCandleHighRef.current ?? Math.max(openVal, liveTick.price);
      const lowVal = currentCandleLowRef.current ?? Math.min(openVal, liveTick.price);

      candleSeriesRef.current.update({
        time: candleEpoch,
        open: openVal,
        high: highVal,
        low: lowVal,
        close: liveTick.price,
      });
    }
  }, [liveTick, granularity, chartType]);

  // Trade Entry Line on the chart (Linha de Preço de Entrada)
  useEffect(() => {
    const targetSeries = chartType === 'CANDLES' ? candleSeriesRef.current : areaSeriesRef.current;
    if (!targetSeries) return;

    // Remove existing price line if any
    if (entryPriceLineRef.current) {
      try {
        targetSeries.removePriceLine(entryPriceLineRef.current);
      } catch {
        // ignore
      }
      entryPriceLineRef.current = null;
    }

    if (openTrade && openTrade.entry_spot > 0 && openTrade.symbol === symbol) {
      const isCall = openTrade.direction === 'MULTUP' || openTrade.direction === 'CALL';
      const lineColor = isCall ? '#10b981' : '#f43f5e';
      const dirText = isCall ? 'COMPRA (MULTUP)' : 'VENDA (MULTDOWN)';
      const pnlText =
        openTrade.current_profit_usd !== undefined
          ? ` [PnL: ${openTrade.current_profit_usd >= 0 ? '+' : ''}$${openTrade.current_profit_usd.toFixed(2)}]`
          : '';

      try {
        const newLine = targetSeries.createPriceLine({
          price: openTrade.entry_spot,
          color: lineColor,
          lineWidth: 2,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: `🎯 ENTRADA ${dirText} @ ${formatPrice(openTrade.entry_spot)}${pnlText}`,
        });
        entryPriceLineRef.current = newLine;
      } catch {
        // ignore
      }
    }
  }, [openTrade, symbol, chartType]);

  // Visual Markers: Arrows on Trade Points
  useEffect(() => {
    const targetSeries = chartType === 'CANDLES' ? candleSeriesRef.current : areaSeriesRef.current;
    if (!targetSeries || !recentTrades || recentTrades.length === 0) return;

    const markers: SeriesMarker<Time>[] = [];
    const tradesForSymbol = recentTrades.filter((t) => !t.symbol || t.symbol === symbol).slice(0, 30);
    const sortedTrades = [...tradesForSymbol].sort((a, b) => Number(a.epoch || 0) - Number(b.epoch || 0));
    const seenTimes = new Set<number>();

    for (const t of sortedTrades) {
      const isWin = t.is_win;
      let tEpoch = Number(t.epoch ? normalizeTime(t.epoch) : Math.floor(Date.now() / 1000));
      while (seenTimes.has(tEpoch)) {
        tEpoch += 1;
      }
      seenTimes.add(tEpoch);

      const profitFormatted = isWin
        ? `+${t.profit_usd.toFixed(2)}`
        : `-${Math.abs(t.profit_usd).toFixed(2)}`;

      markers.push({
        time: tEpoch as UTCTimestamp,
        position: isWin ? 'aboveBar' : 'belowBar',
        color: isWin ? '#10b981' : '#f43f5e',
        shape: isWin ? 'arrowUp' : 'arrowDown',
        text: profitFormatted,
        size: 1.2,
      });
    }

    try {
      targetSeries.setMarkers(markers);
    } catch {
      // ignore transient marker timestamp mismatch
    }
  }, [recentTrades, symbol, chartType]);

  const lastPrice = liveTick?.price || (candles.length > 0 ? candles[candles.length - 1].close : 0);

  return (
    <div className="flex flex-col h-full rounded-2xl border border-slate-800/80 bg-slate-900/60 backdrop-blur-sm overflow-hidden shadow-xl">
      {/* Chart Top Header & Controls */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800/80 px-4 py-3 bg-slate-950/40">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="font-mono text-base font-bold text-slate-100">{symbol}</span>
            <span
              className={`rounded-md border px-2 py-0.5 text-[11px] font-semibold ${
                resolvedCategory === 'Cripto'
                  ? 'border-amber-500/30 bg-amber-500/10 text-amber-300'
                  : resolvedCategory === 'Forex'
                  ? 'border-blue-500/30 bg-blue-500/10 text-blue-300'
                  : 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300'
              }`}
            >
              {resolvedCategory}
            </span>
          </div>

          {lastPrice > 0 && (
            <div className="flex items-center gap-1.5 font-mono text-base font-bold text-slate-100">
              <span className="inline-block h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
              <span>{formatPrice(lastPrice, 4)}</span>
            </div>
          )}
        </div>

        {/* Chart Style Toggle + Granularity Selector & Controls */}
        <div className="flex items-center gap-2 flex-wrap">
          {/* Chart Style Switch: Candlesticks vs Line */}
          <div className="inline-flex rounded-xl border border-slate-800 bg-slate-950/80 p-0.5">
            <button
              type="button"
              onClick={() => setChartType('CANDLES')}
              className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-semibold transition-all ${
                chartType === 'CANDLES'
                  ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
              title="Exibir gráfico em Velas Japonesas (Candlesticks M1 com Médias Móveis)"
            >
              <CandleIcon className="h-3.5 w-3.5" />
              Velas M1
            </button>
            <button
              type="button"
              onClick={() => setChartType('LINE')}
              className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-semibold transition-all ${
                chartType === 'LINE'
                  ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
              title="Exibir gráfico em Linha contínua (Tick a Tick)"
            >
              <LineIcon className="h-3.5 w-3.5" />
              Linha
            </button>
          </div>

          {/* Granularity Selector */}
          <div className="inline-flex rounded-xl border border-slate-800 bg-slate-950/80 p-0.5">
            {GRANULARITY_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => onTimeframeChange?.(opt.value)}
                className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-all ${
                  granularity === opt.value
                    ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 shadow-sm'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>

          {onRefresh && (
            <button
              type="button"
              onClick={onRefresh}
              disabled={loading}
              className="flex h-8 w-8 items-center justify-center rounded-xl border border-slate-800 bg-slate-950/80 text-slate-400 hover:text-slate-200 hover:border-slate-700 transition-colors disabled:opacity-50"
              title="Atualizar dados do gráfico"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin text-cyan-400' : ''}`} />
            </button>
          )}
        </div>
      </div>

      {/* Chart Canvas */}
      <div className="relative flex-1 min-h-[420px]" ref={containerRef}>
        {loading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-slate-950/60 backdrop-blur-[2px]">
            <div className="flex items-center gap-2 rounded-xl border border-cyan-500/30 bg-slate-900/90 px-4 py-2 text-xs font-medium text-cyan-300 shadow-xl">
              <Activity className="h-4 w-4 animate-spin" />
              <span>Sincronizando velas M1 e feeds ao vivo...</span>
            </div>
          </div>
        )}
      </div>

      {/* Chart Footer Indicator Legend */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-800/80 px-4 py-2 text-[11px] text-slate-400 bg-slate-950/40">
        <div className="flex items-center gap-4 flex-wrap">
          {chartType === 'CANDLES' ? (
            <>
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-sm bg-emerald-400" />
                <span className="text-slate-300">Vela Alta</span>
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-sm bg-rose-400" />
                <span className="text-slate-300">Vela Baixa</span>
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-1.5 w-3 rounded bg-emerald-500" />
                <span className="text-emerald-400 font-mono">MA 6</span>
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-1.5 w-3 rounded bg-slate-100" />
                <span className="text-slate-100 font-mono">MA 14</span>
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-1.5 w-3 rounded bg-rose-500" />
                <span className="text-rose-400 font-mono">MA 250</span>
              </span>
            </>
          ) : (
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-cyan-400" />
              <span>Linha Tick a Tick</span>
            </span>
          )}

          {openTrade && openTrade.symbol === symbol && (
            <span className="flex items-center gap-1.5 font-bold text-amber-300 animate-pulse">
              <span className="h-1.5 w-3 border-t-2 border-dashed border-amber-400" />
              <span>Linha de Entrada</span>
            </span>
          )}
        </div>
        <div className="font-mono text-[10px] text-slate-500">
          Katie Tutorials M1 + Preço Médio • WebSocket v3
        </div>
      </div>
    </div>
  );
};
