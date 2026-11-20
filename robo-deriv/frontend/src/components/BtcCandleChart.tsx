import React, { useEffect, useRef, useState } from 'react';
import {
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts';
import { Activity, Bell, Eye, EyeOff, Maximize2, RefreshCw } from 'lucide-react';
import { audioAlerts } from '../lib/audioAlerts';
import type { BtcKlineCandle, BtcSrZone } from '../types/api';

interface BtcCandleChartProps {
  candles: BtcKlineCandle[];
  zones: BtcSrZone[];
  livePrice?: number | null;
  secondsLeft?: number;
  loading?: boolean;
  onRefresh?: () => void;
}

export const BtcCandleChart: React.FC<BtcCandleChartProps> = ({
  candles,
  zones,
  livePrice,
  secondsLeft,
  loading = false,
  onRefresh,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);

  const [showM15, setShowM15] = useState(true);
  const [showM5, setShowM5] = useState(true);
  const [hoverData, setHoverData] = useState<{
    open: number;
    high: number;
    low: number;
    close: number;
  } | null>(null);
  const [touchAlert, setTouchAlert] = useState<{
    zone: BtcSrZone;
    time: string;
  } | null>(null);
  const touchTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Initialize Chart
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#0b0f17' },
        textColor: '#7b88a1',
        fontSize: 11,
        fontFamily: 'monospace, -apple-system, BlinkMacSystemFont, Segoe UI, Roboto',
      },
      grid: {
        vertLines: { color: 'rgba(255, 255, 255, 0.04)' },
        horzLines: { color: 'rgba(255, 255, 255, 0.04)' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: 'rgba(0, 229, 255, 0.4)',
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: '#171d2b',
        },
        horzLine: {
          color: 'rgba(0, 229, 255, 0.4)',
          width: 1,
          style: LineStyle.Dashed,
          labelBackgroundColor: '#171d2b',
        },
      },
      rightPriceScale: {
        borderColor: '#202738',
        scaleMargins: { top: 0.12, bottom: 0.12 },
        autoScale: true,
      },
      timeScale: {
        borderColor: '#202738',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 6,
        barSpacing: 8,
      },
      handleScroll: true,
      handleScale: true,
    });

    const series = chart.addCandlestickSeries({
      upColor: '#00ff88',
      downColor: '#ff3355',
      borderVisible: false,
      wickUpColor: '#00ff88',
      wickDownColor: '#ff3355',
    });

    chartRef.current = chart;
    seriesRef.current = series;

    // Crosshair move listener for OHLC display
    chart.subscribeCrosshairMove((param) => {
      if (param.time && param.seriesData.get(series)) {
        const data = param.seriesData.get(series) as CandlestickData<Time>;
        if (data) {
          setHoverData({
            open: data.open,
            high: data.high,
            low: data.low,
            close: data.close,
          });
        }
      } else {
        setHoverData(null);
      }
    });

    // Resize observer
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        if (entry.contentRect.width > 0 && entry.contentRect.height > 0) {
          chart.applyOptions({
            width: entry.contentRect.width,
            height: entry.contentRect.height,
          });
        }
      }
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  // Update Candles Data
  useEffect(() => {
    if (!seriesRef.current || !candles.length) return;

    const formatted: CandlestickData<Time>[] = candles
      .map((c) => ({
        time: c.time as UTCTimestamp,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }))
      .sort((a, b) => Number(a.time) - Number(b.time));

    seriesRef.current.setData(formatted);

    // Initial fit
    if (chartRef.current) {
      chartRef.current.timeScale().fitContent();
    }
  }, [candles]);

  // Update Live Candle Price from WebSocket
  useEffect(() => {
    if (!seriesRef.current || !livePrice || !candles.length) return;

    const last = candles[candles.length - 1];
    if (!last) return;

    // Compute minute bucket timestamp
    const nowSec = Math.floor(Date.now() / 1000);
    const minuteBucket = Math.floor(nowSec / 60) * 60;
    const candleTime = (last.time > minuteBucket ? last.time : minuteBucket) as UTCTimestamp;

    const openPrice = last.time === minuteBucket ? last.open : livePrice;
    const highPrice = Math.max(last.high || livePrice, livePrice);
    const lowPrice = Math.min(last.low || livePrice, livePrice);

    seriesRef.current.update({
      time: candleTime,
      open: openPrice,
      high: highPrice,
      low: lowPrice,
      close: livePrice,
    });
  }, [livePrice, secondsLeft, candles]);

  // Plot Support and Resistance Zones
  useEffect(() => {
    if (!seriesRef.current) return;

    // Remove existing lines
    for (const line of priceLinesRef.current) {
      try {
        seriesRef.current.removePriceLine(line);
      } catch {
        // ignore
      }
    }
    priceLinesRef.current = [];

    // Filter by toggles
    const activeZones = zones.filter((z) => {
      if (z.timeframe === 'M15' && !showM15) return false;
      if (z.timeframe === 'M5' && !showM5) return false;
      return true;
    });

    for (const z of activeZones) {
      const line = seriesRef.current.createPriceLine({
        price: z.price,
        color: z.color,
        lineWidth: (z.line_width || 1) as 1 | 2 | 3 | 4,
        lineStyle: z.line_style === 0 ? LineStyle.Solid : LineStyle.Dashed,
        axisLabelVisible: true,
        title: z.label,
      });
      priceLinesRef.current.push(line);
    }
  }, [zones, showM15, showM5]);

  // Detector de toque em Suporte ou Resistência com alerta sonoro e visual
  useEffect(() => {
    if (!livePrice || !zones.length) return;

    const activeZones = zones.filter((z) => {
      if (z.timeframe === 'M15' && !showM15) return false;
      if (z.timeframe === 'M5' && !showM5) return false;
      return true;
    });

    for (const z of activeZones) {
      const distPct = Math.abs(livePrice - z.price) / z.price;
      // Proximidade <= 0.04% (~$25 no BTC)
      if (distPct <= 0.0004) {
        const key = `${z.timeframe}_${z.type}_${Math.round(z.price)}`;
        const triggered = audioAlerts.handleSrTouch(key, z.type === 'SUPPORT');
        if (triggered) {
          setTouchAlert({
            zone: z,
            time: new Date().toLocaleTimeString(),
          });
          if (touchTimeoutRef.current) clearTimeout(touchTimeoutRef.current);
          touchTimeoutRef.current = setTimeout(() => {
            setTouchAlert(null);
          }, 6000);
          break;
        }
      }
    }
  }, [livePrice, zones, showM15, showM5]);

  const handleFit = () => {
    if (chartRef.current) {
      chartRef.current.timeScale().fitContent();
    }
  };

  const latestCandle = candles[candles.length - 1];
  const displayOhlc = hoverData || latestCandle;

  return (
    <div className="flex h-full flex-col rounded-2xl border border-slate-800 bg-[#0b0f17] overflow-hidden shadow-2xl">
      {/* Chart Top Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800/80 bg-[#0d121c] px-4 py-3">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="flex h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
            <h2 className="text-xs font-bold uppercase tracking-wider text-slate-100">
              BTC/USDT • Tempo M1
            </h2>
          </div>

          <span className="rounded bg-cyan-500/15 border border-cyan-500/30 px-2 py-0.5 font-mono text-[10px] font-bold text-cyan-300">
            Binance Spot 1m
          </span>

          {/* OHLC Badges */}
          {displayOhlc && (
            <div className="hidden sm:flex items-center gap-2 text-[11px] font-mono text-slate-400">
              <span>
                O: <strong className="text-slate-200">{displayOhlc.open.toFixed(2)}</strong>
              </span>
              <span>
                H: <strong className="text-emerald-400">{displayOhlc.high.toFixed(2)}</strong>
              </span>
              <span>
                L: <strong className="text-rose-400">{displayOhlc.low.toFixed(2)}</strong>
              </span>
              <span>
                C:{' '}
                <strong
                  className={displayOhlc.close >= displayOhlc.open ? 'text-emerald-400' : 'text-rose-400'}
                >
                  {displayOhlc.close.toFixed(2)}
                </strong>
              </span>
            </div>
          )}
        </div>

        {/* Zone Filters & Actions */}
        <div className="flex items-center gap-2">
          {/* M15 Zones Toggle */}
          <button
            type="button"
            onClick={() => setShowM15(!showM15)}
            className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[11px] font-bold font-mono transition-colors ${
              showM15
                ? 'border-rose-500/50 bg-rose-500/15 text-rose-300 shadow-sm shadow-rose-950/20'
                : 'border-slate-800 bg-slate-900 text-slate-500 hover:text-slate-300'
            }`}
            title="Alternar Zonas de M15 (Sólidas)"
          >
            {showM15 ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
            <span>Zonas M15</span>
          </button>

          {/* M5 Zones Toggle */}
          <button
            type="button"
            onClick={() => setShowM5(!showM5)}
            className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[11px] font-bold font-mono transition-colors ${
              showM5
                ? 'border-amber-500/50 bg-amber-500/15 text-amber-300 shadow-sm shadow-amber-950/20'
                : 'border-slate-800 bg-slate-900 text-slate-500 hover:text-slate-300'
            }`}
            title="Alternar Zonas de M5 (Tracejadas)"
          >
            {showM5 ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
            <span>Zonas M5</span>
          </button>

          {/* Fit Button */}
          <button
            type="button"
            onClick={handleFit}
            className="rounded-lg border border-slate-800 bg-slate-900 p-1.5 text-slate-400 hover:text-slate-100 hover:border-slate-700 transition-colors"
            title="Ajustar escala e centralizar"
          >
            <Maximize2 className="h-3.5 w-3.5" />
          </button>

          {/* Refresh Button */}
          {onRefresh && (
            <button
              type="button"
              disabled={loading}
              onClick={onRefresh}
              className="rounded-lg border border-slate-800 bg-slate-900 p-1.5 text-cyan-400 hover:border-cyan-500/50 hover:bg-cyan-500/15 transition-colors disabled:opacity-50"
              title="Recarregar candles e zonas"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>
          )}
        </div>
      </div>

      {/* Floating Touch Alert Banner */}
      {touchAlert && (
        <div
          className={`mx-4 mt-2.5 flex items-center justify-between rounded-xl border px-3.5 py-2 text-xs font-mono font-bold animate-fadeIn transition-all ${
            touchAlert.zone.type === 'SUPPORT'
              ? 'border-emerald-500/50 bg-emerald-500/20 text-emerald-300 shadow-lg shadow-emerald-950/40'
              : 'border-rose-500/50 bg-rose-500/20 text-rose-300 shadow-lg shadow-rose-950/40'
          }`}
        >
          <div className="flex items-center gap-2">
            <span className="flex h-2 w-2 rounded-full bg-current animate-ping" />
            <span>
              ⚡ TOQUE NO {touchAlert.zone.label.toUpperCase()} @ ${livePrice?.toFixed(2)}
            </span>
          </div>
          <span className="text-[10px] opacity-80">{touchAlert.time}</span>
        </div>
      )}

      {/* Candlestick Canvas */}
      <div className="relative flex-1 min-h-[480px] w-full">
        <div ref={containerRef} className="absolute inset-0 h-full w-full" />
      </div>

      {/* Chart Footer with Zone Legend */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-800/80 bg-[#0d121c] px-4 py-2 text-[10px] font-mono text-slate-400">
        <div className="flex items-center gap-4">
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-4 bg-[#ff3355] rounded-sm inline-block" />
            <span>Resistência M15</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-4 bg-[#00ff88] rounded-sm inline-block" />
            <span>Suporte M15</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-0.5 w-4 border-b-2 border-dashed border-[#ff9900] inline-block" />
            <span>Resistência M5</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-0.5 w-4 border-b-2 border-dashed border-[#00e5ff] inline-block" />
            <span>Suporte M5</span>
          </span>
        </div>

        <div className="text-slate-500">
          Zonas calculadas por Pivôs Fractais e Concentração de Volume • Anti-Repaint
        </div>
      </div>
    </div>
  );
};

export default BtcCandleChart;
