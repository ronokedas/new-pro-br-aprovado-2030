"""
server.py (v3.0 - Baixa Latencia BBO Multi-Exchange + Trigger Exato 00.000s / +3.000s)
Servidor FastAPI e WebSocket Hub para transmissao em tempo real:
  - FastBBOFeed: Binance Spot/Perp + Bybit Perp + OKX Perp sem throttle + clock sync UTC
  - Trigger de alta precisao na virada do minuto (:00.000) travando Produto A via BBO
    com push imediato para todos os clientes WebSocket (<10ms apos abertura, sem esperar kline_1m)
  - Trigger separado em +3.0s travando Produto B (Follow_3s + consenso multi-perp)
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

from data_feed.binance_ws import BinanceDataFeed
from data_feed.fast_bbo_feed import FastBBOFeed
from data_feed.local_candle_builder import LocalCandleBuilder
from data_feed.orderbook_engine import OrderBookEngine
from engine.predictor import RealtimePredictor
from trainer.backtest_120m import run_120m_audit

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("server")

BASE_DIR = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = BASE_DIR / "dashboard"

app = FastAPI(title="BTC Realtime M1 Radar & Predictor v3.0 (Low-Latency BBO & Zero-Lag Local Feed)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

predictor = RealtimePredictor(primary_symbol="BTCUSDT")
fast_bbo = FastBBOFeed(symbol="BTCUSDT")
candle_builder = LocalCandleBuilder(symbol="BTCUSDT", clock_fn=fast_bbo.server_now_ms)
feed = BinanceDataFeed(primary_symbol="BTCUSDT")
orderbook = OrderBookEngine(symbol="BTCUSDT")

connected_clients: Set[WebSocket] = set()


async def broadcast_now():
    """Envia estado imediatamente para todos os clientes WebSocket (sem esperar sleep)."""
    if not connected_clients:
        return
    current_c = candle_builder.get_current_candle("BINANCE") or feed.current_candle
    lat_snap = candle_builder.get_telemetry()
    state = predictor.generate_live_state(
        current_c,
        feed.candle_buffers.get(feed.primary_symbol, []),
        feed.perp_candle_buffers.get(feed.primary_symbol, []),
        fast_bbo.get_snapshot(),
        orderbook.get_snapshot(),
        latency_telemetry=lat_snap,
    )
    payload_str = json.dumps(state)
    to_remove = set()
    for client in list(connected_clients):
        try:
            await client.send_text(payload_str)
        except Exception:
            to_remove.add(client)
    for dead_client in to_remove:
        connected_clients.discard(dead_client)


# Callback de vela fechada -> audita imediatamente o resultado oficial da vela fechada
async def on_candle_closed(candle, buffer):
    candle_builder.on_official_kline_received(candle, "BINANCE_SPOT")
    predictor.audit_closed_candle(candle)
    orderbook.finalize_closed_candle(candle)
    await broadcast_now()


async def on_perp_candle_closed(candle, buffer):
    candle_builder.on_official_kline_received(candle, "BINANCE_FUTURES")


feed.on_candle_closed = on_candle_closed
feed.on_perp_candle_closed = on_perp_candle_closed


async def minute_boundary_scheduler():
    """
    Agendador de alta precisao (relogio UTC sincronizado com a Binance):
      - Acorda em :00.005s -> Passo 1 (Produto A: REF_basis_z60_Q80 via BBO) + snapshot L2 + push imediato.
      - Acorda em :05.005s -> Passo 2 (Produto B: +5s se ainda em WAIT) + snapshot L2 + push imediato.
    """
    wait_ms = predictor.product_b_wait_sec * 1000
    last_a_min = 0
    last_b_min = 0
    while True:
        try:
            now_ms = fast_bbo.server_now_ms()
            cur_min = int(now_ms // 60_000) * 60_000
            ms_in_min = now_ms - cur_min

            # 1) Gatilho Passo 1 na virada do minuto (0ms..1500ms)
            if cur_min != last_a_min and ms_in_min < 1500:
                # Congela imediatamente a vela M1 local encerrada em :59.999s com ZERO delay
                frozen_local = candle_builder.freeze_minute_at_boundary(cur_min - 60_000)
                buf = feed.candle_buffers.setdefault(feed.primary_symbol, [])
                if frozen_local and (not buf or buf[-1]["open_time"] < frozen_local["open_time"]):
                    buf.append(frozen_local)
                    if len(buf) > 500:
                        buf.pop(0)

                snap = fast_bbo.get_snapshot()
                l2_snap = orderbook.get_snapshot()
                orderbook.record_minute_snapshot(cur_min, "0s")
                predictor.lock_at_minute_open(
                    cur_min,
                    feed.candle_buffers.get(feed.primary_symbol, []),
                    feed.perp_candle_buffers.get(feed.primary_symbol, []),
                    snap,
                    l2_snap,
                    auditable=True,
                )
                last_a_min = cur_min
                await broadcast_now()

            # 2) Gatilho Passo 2 em +5.0s (FISICA_SNR5s_Q70 + filtro L2)
            if cur_min != last_b_min and wait_ms <= ms_in_min < (wait_ms + 2500):
                snap = fast_bbo.get_snapshot()
                l2_snap = orderbook.get_snapshot()
                orderbook.record_minute_snapshot(cur_min, "5s")
                predictor.lock_product_b_if_waiting(
                    cur_min,
                    snap,
                    spot_history=feed.candle_buffers.get(feed.primary_symbol, []),
                    l2_snapshot=l2_snap,
                )
                last_b_min = cur_min
                await broadcast_now()

            if ms_in_min < wait_ms:
                wait_s = max(0.005, (wait_ms + 5.0 - ms_in_min) / 1000.0)
            else:
                wait_s = max(0.005, (60_005.0 - ms_in_min) / 1000.0)
            await asyncio.sleep(min(wait_s, 1.0))
        except Exception as e:
            log.error("Erro no minute_boundary_scheduler: %s", e)
            await asyncio.sleep(0.1)


@app.on_event("startup")
async def startup_event():
    log.info("Executando auditoria inicial dos ultimos 120 minutos (REF_basis_z60_Q80)...")
    try:
        audit_res = run_120m_audit()
        predictor.audit_summary["total"] = audit_res.get("total_signals", audit_res.get("total_candles", 0))
        predictor.audit_summary["wins"] = audit_res.get("wins", 0)
        predictor.audit_summary["losses"] = audit_res.get("losses", 0)
        predictor.audit_summary["dojis"] = audit_res.get("dojis", 0)
        predictor.audit_summary["winrate"] = audit_res.get("winrate_pct", 0.0)
        if audit_res.get("product_a_0s"):
            predictor.audit_by_product["product_a_0s"] = audit_res["product_a_0s"]
        if audit_res.get("product_b_5s"):
            predictor.audit_by_product["product_b_5s"] = audit_res["product_b_5s"]
        predictor.audit_summary["recent_results"] = [
            {"time": h["time"], "predicted": h["predicted"], "actual": h["actual"], "result": h["result"]}
            for h in audit_res.get("recent_history", [])
        ]
        log.info("✅ Auditoria 120m carregada! Winrate sinais: %s%% (%dW/%dL | cob: %s%%)",
                 predictor.audit_summary["winrate"],
                 predictor.audit_summary["wins"],
                 predictor.audit_summary["losses"],
                 audit_res.get("coverage_pct", 0.0))
    except Exception as e:
        log.error("Erro ao carregar auditoria 120m: %s", e)

    buf = feed.candle_buffers.get(feed.primary_symbol, [])
    perp_buf = feed.perp_candle_buffers.get(feed.primary_symbol, [])
    if buf:
        predictor.update_with_candle(feed.current_candle or buf[-1], buf, perp_buf, fast_bbo.get_snapshot())

    log.info("Iniciando LocalCandleBuilder + FastBBOFeed + OrderBookEngine L2 + BinanceDataFeed + Agendador 00.000s/+5.000s...")
    asyncio.create_task(candle_builder.start())
    asyncio.create_task(fast_bbo.start())
    asyncio.create_task(orderbook.start())
    asyncio.create_task(feed.start())
    asyncio.create_task(minute_boundary_scheduler())
    asyncio.create_task(broadcast_loop())


async def broadcast_loop():
    """Atualizacao continua a cada 50ms (20Hz), alem dos pushes imediatos nos gatilhos da vela."""
    while True:
        try:
            await broadcast_now()
        except Exception as e:
            log.error("Erro no broadcast: %s", e)
        await asyncio.sleep(0.05)


@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.add(websocket)
    log.info("Cliente conectado ao dashboard. Total ativos: %d", len(connected_clients))
    try:
        current_c = candle_builder.get_current_candle("BINANCE") or feed.current_candle
        lat_snap = candle_builder.get_telemetry()
        initial_state = predictor.generate_live_state(
            current_c,
            feed.candle_buffers.get(feed.primary_symbol, []),
            feed.perp_candle_buffers.get(feed.primary_symbol, []),
            fast_bbo.get_snapshot(),
            orderbook.get_snapshot(),
            latency_telemetry=lat_snap,
        )
        await websocket.send_text(json.dumps(initial_state))
    except Exception as e:
        log.warning("Falha ao enviar estado inicial: %s", e)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        connected_clients.discard(websocket)
        log.info("Cliente desconectado. Total ativos: %d", len(connected_clients))


@app.get("/api/state")
async def get_state():
    current_c = candle_builder.get_current_candle("BINANCE") or feed.current_candle
    lat_snap = candle_builder.get_telemetry()
    return predictor.generate_live_state(
        current_c,
        feed.candle_buffers.get(feed.primary_symbol, []),
        feed.perp_candle_buffers.get(feed.primary_symbol, []),
        fast_bbo.get_snapshot(),
        orderbook.get_snapshot(),
        latency_telemetry=lat_snap,
    )


@app.get("/api/fast-telemetry")
async def get_fast_telemetry():
    return {
        "fast_bbo": fast_bbo.get_snapshot(),
        "orderbook_l2": orderbook.get_snapshot(),
        "latency_telemetry": candle_builder.get_telemetry(),
        "locked_prediction": predictor.locked_prediction,
        "audit_summary": predictor.audit_summary,
        "audit_by_product": predictor.audit_by_product,
    }


@app.get("/api/latency-telemetry")
async def get_latency_telemetry():
    return candle_builder.get_telemetry()


@app.get("/api/audit-120m")
async def get_audit():
    return run_120m_audit()


@app.get("/")
async def serve_index():
    index_path = DASHBOARD_DIR / "index.html"
    return FileResponse(index_path)


if DASHBOARD_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")


if __name__ == "__main__":
    uvicorn.run("api.server:app", host="0.0.0.0", port=8888, reload=False)
