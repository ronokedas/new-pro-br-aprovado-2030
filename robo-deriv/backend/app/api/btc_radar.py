import asyncio
import json
import logging
from typing import Any, Optional

import httpx
import websockets
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status

from app.core.security import decode_token, get_active_user
from app.services.subscription import is_user_expired
from shared.config import get_settings
from shared.database import get_session_factory
from shared.models import User

logger = logging.getLogger("api.btc_radar")

router = APIRouter(prefix="/api/btc-radar", tags=["BTC Radar M1"])
ws_router = APIRouter(tags=["BTC Radar WebSocket"])

BTC_RADAR_HTTP_BASE = "http://host.docker.internal:8888"
BTC_RADAR_WS_URL = "ws://host.docker.internal:8888/ws/live"


def verify_btc_radar_permission(user: User) -> None:
    """Ensure user has permission to access the BTC Next Candle Radar."""
    if user.status != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sua conta está suspensa. Contate o administrador.",
        )
    if is_user_expired(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sua licença expirou. Renove sua assinatura para acessar o Radar BTC.",
        )
    if not getattr(user, "can_access_btc_radar", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso ao Radar BTC não liberado pelo Administrador.",
        )


@router.get("/state")
async def get_btc_radar_state(
    current_user: User = Depends(get_active_user),
) -> dict[str, Any]:
    verify_btc_radar_permission(current_user)
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(f"{BTC_RADAR_HTTP_BASE}/api/state")
            if resp.status_code == 200:
                return resp.json()
            raise HTTPException(status_code=resp.status_code, detail="Radar BTC indisponível.")
    except httpx.RequestError as exc:
        logger.error("Falha ao comunicar com engine BTC Radar: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Serviço do Radar BTC indisponível no momento.",
        )


@router.get("/telemetry")
async def get_btc_radar_telemetry(
    current_user: User = Depends(get_active_user),
) -> dict[str, Any]:
    verify_btc_radar_permission(current_user)
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(f"{BTC_RADAR_HTTP_BASE}/api/fast-telemetry")
            if resp.status_code == 200:
                return resp.json()
            raise HTTPException(status_code=resp.status_code, detail="Telemetria do Radar BTC indisponível.")
    except httpx.RequestError as exc:
        logger.error("Falha ao buscar telemetria do Radar BTC: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Serviço de telemetria do Radar BTC indisponível.",
        )


@router.get("/audit-120m")
async def get_btc_radar_audit_120m(
    current_user: User = Depends(get_active_user),
) -> dict[str, Any]:
    verify_btc_radar_permission(current_user)
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(f"{BTC_RADAR_HTTP_BASE}/api/audit-120m")
            if resp.status_code == 200:
                return resp.json()
            raise HTTPException(status_code=resp.status_code, detail="Auditoria do Radar BTC indisponível.")
    except httpx.RequestError as exc:
        logger.error("Falha ao buscar auditoria do Radar BTC: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Serviço de auditoria do Radar BTC indisponível.",
        )


def _cluster_levels(levels: list[float], reverse: bool = False, tolerance: float = 0.0015) -> list[float]:
    levels.sort(reverse=reverse)
    clustered: list[float] = []
    for lv in levels:
        if not any(abs(c - lv) / lv < tolerance for c in clustered):
            clustered.append(round(lv, 2))
    return clustered


def _extract_sr_from_klines(klines: list[list[Any]], current_price: float) -> tuple[list[float], list[float]]:
    if len(klines) < 10:
        return [], []
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    res: list[float] = []
    sup: list[float] = []
    n = len(klines)
    for i in range(2, n - 2):
        if highs[i] > highs[i - 1] and highs[i] > highs[i - 2] and highs[i] >= highs[i + 1] and highs[i] >= highs[i + 2]:
            if highs[i] > current_price:
                res.append(highs[i])
        if lows[i] < lows[i - 1] and lows[i] < lows[i - 2] and lows[i] <= lows[i + 1] and lows[i] <= lows[i + 2]:
            if lows[i] < current_price:
                sup.append(lows[i])
    return _cluster_levels(res, reverse=False), _cluster_levels(sup, reverse=True)


@router.get("/klines")
async def get_btc_klines(
    symbol: str = Query(default="BTCUSDT"),
    interval: str = Query(default="1m"),
    limit: int = Query(default=120, ge=10, le=500),
    current_user: User = Depends(get_active_user),
) -> list[dict[str, Any]]:
    verify_btc_radar_permission(current_user)
    binance_url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(binance_url)
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail="Falha ao obter candles da Binance.")
            raw_candles = resp.json()
            formatted = []
            for c in raw_candles:
                formatted.append({
                    "time": int(c[0]) // 1000,
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5]),
                })
            return formatted
    except httpx.RequestError as exc:
        logger.error("Erro ao conectar com API Binance: %s", exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="API Binance indisponível.")


@router.get("/sr-zones")
async def get_btc_sr_zones(
    symbol: str = Query(default="BTCUSDT"),
    current_user: User = Depends(get_active_user),
) -> dict[str, Any]:
    verify_btc_radar_permission(current_user)
    try:
        async with httpx.AsyncClient(timeout=7.0) as client:
            m5_resp, m15_resp = await asyncio.gather(
                client.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=5m&limit=100"),
                client.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit=100"),
            )
            if m5_resp.status_code != 200 or m15_resp.status_code != 200:
                raise HTTPException(status_code=502, detail="Falha ao buscar dados de zonas na Binance.")

            k5 = m5_resp.json()
            k15 = m15_resp.json()
            current_price = float(k5[-1][4]) if k5 else 0.0

            res15, sup15 = _extract_sr_from_klines(k15, current_price)
            res5, sup5 = _extract_sr_from_klines(k5, current_price)

            zones = []
            for p in res15[:3]:
                zones.append({
                    "price": p,
                    "timeframe": "M15",
                    "type": "RESISTANCE",
                    "label": f"Resistência M15: ${p:,.2f}",
                    "color": "#ff3355",
                    "line_style": 0,
                    "line_width": 2,
                })
            for p in sup15[:3]:
                zones.append({
                    "price": p,
                    "timeframe": "M15",
                    "type": "SUPPORT",
                    "label": f"Suporte M15: ${p:,.2f}",
                    "color": "#00ff88",
                    "line_style": 0,
                    "line_width": 2,
                })
            for p in res5[:2]:
                if not any(abs(z["price"] - p) / p < 0.001 for z in zones):
                    zones.append({
                        "price": p,
                        "timeframe": "M5",
                        "type": "RESISTANCE",
                        "label": f"Resistência M5: ${p:,.2f}",
                        "color": "#ff9900",
                        "line_style": 2,
                        "line_width": 1,
                    })
            for p in sup5[:2]:
                if not any(abs(z["price"] - p) / p < 0.001 for z in zones):
                    zones.append({
                        "price": p,
                        "timeframe": "M5",
                        "type": "SUPPORT",
                        "label": f"Suporte M5: ${p:,.2f}",
                        "color": "#00e5ff",
                        "line_style": 2,
                        "line_width": 1,
                    })

            return {
                "symbol": symbol,
                "current_price": current_price,
                "zones": zones,
            }
    except httpx.RequestError as exc:
        logger.error("Erro ao calcular zonas S/R Binance: %s", exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Falha ao calcular zonas S/R.")


@ws_router.websocket("/ws/btc-radar")
async def btc_radar_websocket(
    websocket: WebSocket,
    token: Optional[str] = Query(default=None),
) -> None:
    if not token:
        await websocket.close(code=4001, reason="Token ausente")
        return

    try:
        payload = decode_token(token, expected_type="access")
        user_id = int(payload["sub"])
    except Exception:
        await websocket.close(code=4001, reason="Token inválido ou expirado")
        return

    # Database check for user permissions
    db = get_session_factory()()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user or user.status != "ACTIVE":
            await websocket.close(code=4003, reason="Usuário suspenso")
            return
        if is_user_expired(user):
            await websocket.close(code=4003, reason="Licença expirada")
            return
        if not getattr(user, "can_access_btc_radar", True):
            await websocket.close(code=4403, reason="Acesso ao Radar BTC não liberado pelo administrador")
            return
    finally:
        db.close()

    await websocket.accept()
    logger.info("[ws-btc] Usuário #%d conectado ao stream do Radar BTC", user_id)

    # Bridge between btc radar engine and authenticated frontend client
    stop_event = asyncio.Event()

    async def _forward_upstream_to_client():
        while not stop_event.is_set():
            try:
                async with websockets.connect(
                    BTC_RADAR_WS_URL,
                    open_timeout=5.0,
                    ping_interval=20,
                    ping_timeout=10,
                ) as upstream_ws:
                    while not stop_event.is_set():
                        msg = await upstream_ws.recv()
                        await websocket.send_text(msg)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if not stop_event.is_set():
                    logger.debug("[ws-btc] Conexão com upstream radar caiu: %s. Reconectando em 1s...", exc)
                    await asyncio.sleep(1.0)

    async def _listen_client_messages():
        try:
            while not stop_event.is_set():
                # Read pings or client messages
                _ = await websocket.receive_text()
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        finally:
            stop_event.set()

    task_up = asyncio.create_task(_forward_upstream_to_client())
    task_down = asyncio.create_task(_listen_client_messages())

    try:
        done, pending = await asyncio.wait(
            [task_up, task_down],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    finally:
        stop_event.set()
        logger.info("[ws-btc] Sessão do usuário #%d encerrada", user_id)
