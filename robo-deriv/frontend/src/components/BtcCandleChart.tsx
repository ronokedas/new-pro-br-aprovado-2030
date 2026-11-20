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
import {
  Eye,
  EyeOff,
  Maximize2,
  RefreshCw,
  RotateCcw,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';
import { audioAlerts } from '../lib/audioAlerts';
import type { BtcKlineCandle, BtcSrZone } from '../types/api';

export interface BtcCurrentCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
  is_closed?: boolean;
}

interface BtcCandleChartProps {
  candles: BtcKlineCandle[];
  zones: BtcSrZone[];
  livePrice?: number | null;
  currentCandle?: BtcCurrentCandle | null;
  secondsLeft?: number;
  loading?: boolean;
  onRefresh?: () => void;
}

export const BtcCandleChart: React.FC<BtcCandleChartProps> = ({
  candles,
  zones,
  livePrice,
  currentCandle,
  secondsLeft,
  loading = false,
  onRefresh,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const liveCandleRef = useRef<CandlestickData<Time> | null>(null);
  const candleMapRef = useRef<Map<number, CandlestickData<Time>>>(new Map());
  const hasInitializedZoomRef = useRef(false);

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
        rightOffset: 12,
        barSpacing: 14,
        minBarSpacing: 5,
        fixLeftEdge: false,
        fixRightEdge: false,
      },
      handleScroll: true,
      handleScale: true,
    });

    const series = chart.addCandlestickSeries({
      upColor: '#00ff88',
      downColor: '#ff3355',
      borderVisible: true,
      borderColor: '#00ff88',
      borderUpColor: '#00ff88',
      borderDownColor: '#ff3355',
      wickUpColor: '#00ff88',
      wickDownColor: '#ff3355',
      wickVisible: true,
      priceFormat: {
        type: 'price',
        precision: 2,
        minMove: 0.01,
      },
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

  // Update Candles Data (Merge inteligente sem reset de zoom indesejado)
  useEffect(() => {
    if (!seriesRef.current || !candles.length) return;

    // Popula o candleMap com as velas históricas recebidas
    for (const c of candles) {
      const t = Number(c.time);
      candleMapRef.current.set(t, {
        time: c.time as UTCTimestamp,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      });
    }

    const formatted: CandlestickData<Time>[] = Array.from(candleMapRef.current.values())
      .sort((a, b) => Number(a.time) - Number(b.time));

    seriesRef.current.setData(formatted);

    // Ajusta o zoom inicial apenas na PRIMEIRA carga para não interferir no zoom do usuário
    if (chartRef.current && formatted.length > 0 && !hasInitializedZoomRef.current) {
      hasInitializedZoomRef.current = true;
      const len = formatted.length;
      chartRef.current.timeScale().setVisibleLogicalRange({
        from: Math.max(0, len - 45),
        to: len + 4,
      });
    }
  }, [candles]);

  // Update Live Candle Price from WebSocket / LocalCandleBuilder (100% Spot Anti-Gap)
  useEffect(() => {
    if (!seriesRef.current) return;

    const nowSec = Math.floor(Date.now() / 1000);
    const minuteBucket = (Math.floor(nowSec / 60) * 60) as UTCTimestamp;

    // Obtém o timestamp do candle alvo
    let targetTime = minuteBucket;
    if (currentCandle && currentCandle.time > 0) {
      targetTime = currentCandle.time as UTCTimestamp;
    }

    // Se o minuto virou em relação à vela anterior em formação, arquiva a vela anterior no mapa
    const currentLive = liveCandleRef.current;
    if (currentLive && Number(currentLive.time) < Number(targetTime)) {
      candleMapRef.current.set(Number(currentLive.time), currentLive);
    }

    // Encontra a vela anterior fechada mais recente
    const sortedTimes = Array.from(candleMapRef.current.keys()).sort((a, b) => a - b);
    const prevTime = sortedTimes.filter((t) => t < Number(targetTime)).pop();
    const prevCandle = prevTime ? candleMapRef.current.get(prevTime) : null;

    // PREÇO DE ABERTURA (OPEN) RIGOROSO:
    // 1. Se a vela já está viva na memória para este mesmo minuto, o open JAMAIS muda!
    // 2. Se a vela está nascendo agora, o Open DEVE ser exatamente o Close da vela anterior fechada (ZERO GAP).
    // 3. Fallback: currentCandle.open ou livePrice.
    let targetOpen: number;
    if (currentLive && Number(currentLive.time) === Number(targetTime) && Number(currentLive.open) > 0) {
      targetOpen = Number(currentLive.open);
    } else if (prevCandle && Number(prevCandle.close) > 0) {
      targetOpen = Number(prevCandle.close);
    } else if (currentCandle && currentCandle.open > 0) {
      targetOpen = currentCandle.open;
    } else {
      targetOpen = livePrice || 0;
    }

    const currentClose = (currentCandle && currentCandle.close > 0)
      ? currentCandle.close
      : (livePrice || targetOpen);

    const prevHigh = (currentLive && Number(currentLive.time) === Number(targetTime))
      ? Number(currentLive.high)
      : targetOpen;
    const prevLow = (currentLive && Number(currentLive.time) === Number(targetTime))
      ? Number(currentLive.low)
      : targetOpen;

    const candleHigh = Math.max(
      targetOpen,
      currentClose,
      prevHigh,
      (currentCandle && currentCandle.high) ? currentCandle.high : targetOpen
    );
    const candleLow = Math.min(
      targetOpen,
      currentClose,
      prevLow,
      (currentCandle && currentCandle.low) ? currentCandle.low : targetOpen
    );

    if (targetOpen > 0 && currentClose > 0) {
      const liveCandle: CandlestickData<Time> = {
        time: targetTime,
        open: targetOpen,
        high: candleHigh,
        low: candleLow,
        close: currentClose,
      };

      seriesRef.current.update(liveCandle);
      liveCandleRef.current = liveCandle;
      candleMapRef.current.set(Number(targetTime), liveCandle);
    }
  }, [currentCandle, livePrice]);

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

  // Zoom Controls
  const handleZoomIn = () => {
    if (!chartRef.current) return;
    const timeScale = chartRef.current.timeScale();
    const range = timeScale.getVisibleLogicalRange();
    if (!range) return;
    const span = range.to - range.from;
    const newSpan = Math.max(10, span * 0.7);
    const center = range.to; // zoom towards latest candle
    timeScale.setVisibleLogicalRange({
      from: center - newSpan,
      to: center,
    });
  };

  const handleZoomOut = () => {
    if (!chartRef.current) return;
    const timeScale = chartRef.current.timeScale();
    const range = timeScale.getVisibleLogicalRange();
    if (!range) return;
    const span = range.to - range.from;
    const newSpan = Math.min(candles.length + 10, span * 1.4);
    const center = range.to;
    timeScale.setVisibleLogicalRange({
      from: Math.max(-2, center - newSpan),
      to: center,
    });
  };

  const handleResetZoom = () => {
    if (!chartRef.current) return;
    const len = candles.length;
    if (len > 0) {
      chartRef.current.timeScale().setVisibleLogicalRange({
        from: Math.max(0, len - 45),
        to: len + 4,
      });
    }
  };

  const handleFitAll = () => {
    if (chartRef.current) {
      chartRef.current.timeScale().fitContent();
    }
  };

  const latestCandle = candles[candles.length - 1];
  const liveDisplayCandle = liveCandleRef.current
    ? {
        open: Number(liveCandleRef.current.open),
        high: Number(liveCandleRef.current.high),
        low: Number(liveCandleRef.current.low),
        close: Number(liveCandleRef.current.close),
      }
    : null;
  const displayOhlc = hoverData || liveDisplayCandle || latestCandle;

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
        <div className="flex items-center gap-1.5 sm:gap-2">
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
            <span className="hidden sm:inline">Zonas</span> M15
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
            <span className="hidden sm:inline">Zonas</span> M5
          </button>

          <div className="h-4 w-px bg-slate-800 mx-0.5" />

          {/* Zoom In */}
          <button
            type="button"
            onClick={handleZoomIn}
            className="rounded-lg border border-slate-800 bg-slate-900 p-1.5 text-slate-300 hover:text-cyan-400 hover:border-cyan-500/50 transition-colors"
            title="Aproximar Zoom (+)"
          >
            <ZoomIn className="h-3.5 w-3.5" />
          </button>

          {/* Zoom Out */}
          <button
            type="button"
            onClick={handleZoomOut}
            className="rounded-lg border border-slate-800 bg-slate-900 p-1.5 text-slate-300 hover:text-cyan-400 hover:border-cyan-500/50 transition-colors"
            title="Afastar Zoom (-)"
          >
            <ZoomOut className="h-3.5 w-3.5" />
          </button>

          {/* Reset Zoom to recent 45m */}
          <button
            type="button"
            onClick={handleResetZoom}
            className="inline-flex items-center gap-1 rounded-lg border border-slate-800 bg-slate-900 px-2 py-1 text-[10px] font-mono font-bold text-slate-300 hover:text-emerald-400 hover:border-emerald-500/50 transition-colors"
            title="Foco Recente (Últimas 45 velas com corpo visível)"
          >
            <RotateCcw className="h-3 w-3" />
            <span>45m</span>
          </button>

          {/* Fit All Button */}
          <button
            type="button"
            onClick={handleFitAll}
            className="rounded-lg border border-slate-800 bg-slate-900 p-1.5 text-slate-400 hover:text-slate-100 hover:border-slate-700 transition-colors"
            title="Ver Todo Histórico (150m)"
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
