"""
app/radar/service.py
====================
Serviço Centralizado do Radar M1 BTC embarcado nativamente no Backend do Robo Deriv.
Elimina o container externo btc_realtime_radar (porta 8888) e o proxy HTTP/WS host.docker.internal.
Transmissão direta em memória a 20Hz (50ms) para os clientes autenticados do frontend.

Melhorias v4.0:
  - Elimina race condition: apenas o minute_boundary_scheduler trava a vela nos segundos :00s e :05s.
  - Auditoria por PREÇO DE ENTRADA REAL (close vs lock_price) + cor da vela lado a lado.
  - run_120m_audit roda em thread separada (asyncio.to_thread) sem bloquear o event loop.
  - Baseline de 120m separado dos contadores ao vivo (persistidos em live_signals.jsonl).
  - Fallback de auditoria local (audit_stale_pending) para tolerância a falhas de rede.
"""
import asyncio
import json
import logging
from typing import Any, Dict, Optional, Set
from fastapi import WebSocket

from app.radar.data_feed.binance_ws import BinanceDataFeed
from app.radar.data_feed.fast_bbo_feed import FastBBOFeed
from app.radar.data_feed.local_candle_builder import LocalCandleBuilder
from app.radar.data_feed.orderbook_engine import OrderBookEngine
from app.radar.engine.predictor import RealtimePredictor
from app.radar.trainer.backtest_120m import run_120m_audit

log = logging.getLogger("radar.service")


class RadarService:
    _instance: Optional["RadarService"] = None

    def __init__(self, symbol: str = "BTCUSDT"):
        self.symbol = symbol.upper()
        self.predictor = RealtimePredictor(primary_symbol=self.symbol)
        self.fast_bbo = FastBBOFeed(symbol=self.symbol)
        self.candle_builder = LocalCandleBuilder(symbol=self.symbol, clock_fn=self.fast_bbo.server_now_ms)
        self.feed = BinanceDataFeed(primary_symbol=self.symbol)
        self.orderbook = OrderBookEngine(symbol=self.symbol)

        self.connected_clients: Set[WebSocket] = set()
        self._running = False
        self._tasks: list[asyncio.Task] = []

        # Conecta callbacks de fechamento oficial de vela
        self.feed.on_candle_closed = self._on_candle_closed
        self.feed.on_perp_candle_closed = self._on_perp_candle_closed

    @classmethod
    def get_instance(cls) -> "RadarService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def _on_candle_closed(self, candle: Dict[str, Any], buffer: list):
        self.candle_builder.on_official_kline_received(candle, "BINANCE_SPOT")
        # Marca como kline oficial da Binance para distinguir de velas locais no audit_stale_pending
        candle_with_flag = {**candle, "is_official": True}
        self.predictor.audit_closed_candle(candle_with_flag)
        self.orderbook.finalize_closed_candle(candle)
        await self.broadcast_now()

    async def _on_perp_candle_closed(self, candle: Dict[str, Any], buffer: list):
        self.candle_builder.on_official_kline_received(candle, "BINANCE_FUTURES")

    async def broadcast_now(self):
        """Dispara atualização em tempo real para todos os WebSockets conectados na memória."""
        if not self.connected_clients:
            return

        state = self.get_state()
        payload_str = json.dumps(state)

        to_remove = set()
        for client in list(self.connected_clients):
            try:
                await client.send_text(payload_str)
            except Exception:
                to_remove.add(client)

        for dead in to_remove:
            self.connected_clients.discard(dead)

    def get_state(self) -> Dict[str, Any]:
        """Gera o estado completo atual com vela instantânea e telemetria de latência."""
        current_c = (
            self.candle_builder.get_current_candle("BINANCE_SPOT")
            or self.candle_builder.get_current_candle("BINANCE")
            or self.feed.current_candle
        )
        lat_snap = self.candle_builder.get_telemetry()
        return self.predictor.generate_live_state(
            current_c,
            self.feed.candle_buffers.get(self.feed.primary_symbol, []),
            self.feed.perp_candle_buffers.get(self.feed.primary_symbol, []),
            self.fast_bbo.get_snapshot(),
            self.orderbook.get_snapshot(),
            latency_telemetry=lat_snap,
        )

    def get_telemetry(self) -> Dict[str, Any]:
        return {
            "fast_bbo": self.fast_bbo.get_snapshot(),
            "orderbook_l2": self.orderbook.get_snapshot(),
            "latency_telemetry": self.candle_builder.get_telemetry(),
            "locked_prediction": self.predictor.locked_prediction,
            "audit_summary": self.predictor.audit_summary,
            "audit_by_product": self.predictor.audit_by_product,
            "audit_baseline": self.predictor.audit_baseline,
            "confluence_stats": self.predictor.confluence_stats,
            "veto_stats": self.predictor.veto_stats,
            "triggers_cfg": self.predictor.triggers_cfg,
        }

    def get_triggers_config(self) -> Dict[str, Any]:
        return dict(self.predictor.triggers_cfg)

    def update_triggers_config(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        res = self.predictor.update_triggers_config(cfg)
        log.info("[radar-service] Preferências de gatilhos salvas: %s", res)
        return res

    def reset_triggers_config(self) -> Dict[str, Any]:
        res = self.predictor.reset_to_default_triggers()
        log.info("[radar-service] Gatilhos resetados para o padrão comprovado: %s", res)
        return res

    def reset_scoreboard(self) -> Dict[str, Any]:
        res = self.predictor.reset_scoreboard()
        log.info("[radar-service] Placar ao vivo zerado com sucesso.")
        return res

    async def get_audit_120m(self) -> Dict[str, Any]:
        """Executa a auditoria de 120m em thread dedicada para não travar o loop de eventos."""
        return await asyncio.to_thread(run_120m_audit)

    async def register_client(self, websocket: WebSocket):
        self.connected_clients.add(websocket)
        log.info("[radar-service] Cliente registrado. Total ativos: %d", len(self.connected_clients))
        try:
            state = self.get_state()
            await websocket.send_text(json.dumps(state))
        except Exception as e:
            log.warning("[radar-service] Falha ao enviar estado inicial: %s", e)

    def unregister_client(self, websocket: WebSocket):
        self.connected_clients.discard(websocket)
        log.info("[radar-service] Cliente desconectado. Total ativos: %d", len(self.connected_clients))

    async def _minute_boundary_scheduler(self):
        """Gatilho de virada instantânea :59.999s / :00.000s com relógio atômico sincronizado."""
        wait_ms = self.predictor.product_b_wait_sec * 1000
        last_a_min = 0
        last_b_min = 0

        while self._running:
            try:
                now_ms = self.fast_bbo.server_now_ms()
                cur_min = int(now_ms // 60_000) * 60_000
                ms_in_min = now_ms - cur_min

                # 1) Gatilho Passo 1 na virada do minuto (0ms..1500ms)
                if cur_min != last_a_min and ms_in_min < 1500:
                    # Congela imediatamente a vela local encerrada em :59.999s com ZERO delay
                    frozen_local = self.candle_builder.freeze_minute_at_boundary(cur_min - 60_000)
                    buf = self.feed.candle_buffers.setdefault(self.feed.primary_symbol, [])
                    if frozen_local and (not buf or buf[-1]["open_time"] < frozen_local["open_time"]):
                        buf.append(frozen_local)
                        if len(buf) > 500:
                            buf.pop(0)

                    snap = self.fast_bbo.get_snapshot()
                    l2_snap = self.orderbook.get_snapshot()
                    self.orderbook.record_minute_snapshot(cur_min, "0s")
                    self.predictor.lock_at_minute_open(
                        cur_min,
                        self.feed.candle_buffers.get(self.feed.primary_symbol, []),
                        self.feed.perp_candle_buffers.get(self.feed.primary_symbol, []),
                        snap,
                        l2_snap,
                        auditable=True,
                    )
                    last_a_min = cur_min
                    await self.broadcast_now()

                # 2) Gatilho Passo 2 em +5.0s (Produto B)
                if cur_min != last_b_min and wait_ms <= ms_in_min < (wait_ms + 2500):
                    snap = self.fast_bbo.get_snapshot()
                    l2_snap = self.orderbook.get_snapshot()
                    self.orderbook.record_minute_snapshot(cur_min, "5s")
                    self.predictor.lock_product_b_if_waiting(
                        cur_min,
                        snap,
                        spot_history=self.feed.candle_buffers.get(self.feed.primary_symbol, []),
                        l2_snapshot=l2_snap,
                    )
                    last_b_min = cur_min
                    await self.broadcast_now()

                # 3) Auditoria de segurança caso o WebSocket oficial atrase mais de 20s
                # Busca primeiro em BINANCE_SPOT (canal correto do spot), depois BINANCE (fallback)
                def _lookup_local(ts: int):
                    closed = self.candle_builder.closed_local_candles
                    return (
                        closed.get("BINANCE_SPOT", {}).get(ts)
                        or closed.get("BINANCE", {}).get(ts)
                    )
                self.predictor.audit_stale_pending(
                    _lookup_local,
                    now_ms,
                )

                if ms_in_min < wait_ms:
                    wait_s = max(0.005, (wait_ms + 5.0 - ms_in_min) / 1000.0)
                else:
                    wait_s = max(0.005, (60_005.0 - ms_in_min) / 1000.0)
                await asyncio.sleep(min(wait_s, 1.0))
            except Exception as e:
                log.error("[radar-service] Erro no minute_boundary_scheduler: %s", e)
                await asyncio.sleep(0.1)

    async def _broadcast_loop(self):
        """Loop contínuo de broadcast a 20Hz (50ms). Apenas lê o estado, sem travar velas."""
        while self._running:
            try:
                await self.broadcast_now()
            except Exception as e:
                log.error("[radar-service] Erro no broadcast_loop: %s", e)
            await asyncio.sleep(0.05)

    async def start(self):
        if self._running:
            return
        self._running = True

        log.info("[radar-service] Carregando baseline inicial de 120 minutos (não bloqueante)...")
        try:
            audit_res = await asyncio.to_thread(run_120m_audit)
            self.predictor.set_baseline(audit_res)
            log.info(
                "✅ [radar-service] Baseline 120m carregado: entrada=%.1f%% | cor=%.1f%% | %dW/%dL",
                audit_res.get("winrate_pct", 0.0),
                audit_res.get("color_winrate_pct", 0.0),
                audit_res.get("wins", 0),
                audit_res.get("losses", 0),
            )
        except Exception as e:
            log.warning("[radar-service] Não foi possível carregar baseline 120m: %s", e)

        buf = self.feed.candle_buffers.get(self.feed.primary_symbol, [])
        perp_buf = self.feed.perp_candle_buffers.get(self.feed.primary_symbol, [])
        if buf:
            self.predictor.update_with_candle(
                self.feed.current_candle or buf[-1],
                buf,
                perp_buf,
                self.fast_bbo.get_snapshot(),
            )

        log.info("[radar-service] Inicializando feeds e scheduler nativos no backend...")
        self._tasks = [
            asyncio.create_task(self.candle_builder.start()),
            asyncio.create_task(self.fast_bbo.start()),
            asyncio.create_task(self.orderbook.start()),
            asyncio.create_task(self.feed.start()),
            asyncio.create_task(self._minute_boundary_scheduler()),
            asyncio.create_task(self._broadcast_loop()),
        ]

    def stop(self):
        self._running = False
        self.candle_builder.stop()
        self.fast_bbo.stop()
        self.orderbook.stop()
        self.feed.stop()
        for t in self._tasks:
            t.cancel()
        log.info("[radar-service] Radar parado com sucesso.")
