import asyncio
import json
import logging
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status

from app.core.security import decode_token, get_active_user
from app.radar.service import RadarService
from app.services.subscription import is_user_expired
from shared.config import get_settings
from shared.database import get_session_factory
from shared.models import User

logger = logging.getLogger("api.btc_radar")

router = APIRouter(prefix="/api/btc-radar", tags=["BTC Radar M1"])
ws_router = APIRouter(tags=["BTC Radar WebSocket"])


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
        return RadarService.get_instance().get_state_for_user(current_user.id)
    except Exception as exc:
        logger.error("Falha ao obter estado do Radar BTC nativo: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Falha ao obter estado do Radar BTC.",
        )


@router.get("/telemetry")
async def get_btc_radar_telemetry(
    current_user: User = Depends(get_active_user),
) -> dict[str, Any]:
    verify_btc_radar_permission(current_user)
    try:
        service = RadarService.get_instance()
        base_tel = service.get_telemetry()
        cutoff_ms = service.user_reset_timestamps.get(current_user.id, 0)
        if cutoff_ms > 0:
            user_aud, user_prod = service.predictor.get_user_audit_summary(cutoff_ms)
            base_tel["audit_summary"] = user_aud
            base_tel["audit_by_product"] = user_prod
        return base_tel
    except Exception as exc:
        logger.error("Falha ao obter telemetria do Radar BTC nativo: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Falha ao obter telemetria do Radar BTC.",
        )


@router.get("/audit-120m")
async def get_btc_radar_audit_120m(
    current_user: User = Depends(get_active_user),
) -> dict[str, Any]:
    verify_btc_radar_permission(current_user)
    try:
        return await RadarService.get_instance().get_audit_120m()
    except Exception as exc:
        logger.error("Falha ao obter auditoria 120m do Radar BTC nativo: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Falha ao obter auditoria do Radar BTC.",
        )


@router.get("/triggers-config")
async def get_triggers_config(
    current_user: User = Depends(get_active_user),
) -> dict[str, Any]:
    verify_btc_radar_permission(current_user)
    try:
        return RadarService.get_instance().get_triggers_config()
    except Exception as exc:
        logger.error("Falha ao obter configuração de gatilhos: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Falha ao obter configuração de gatilhos.",
        )


@router.post("/triggers-config")
async def update_triggers_config(
    config: dict[str, Any],
    current_user: User = Depends(get_active_user),
) -> dict[str, Any]:
    verify_btc_radar_permission(current_user)
    try:
        return RadarService.get_instance().update_triggers_config(config)
    except Exception as exc:
        logger.error("Falha ao atualizar configuração de gatilhos: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Falha ao atualizar configuração de gatilhos.",
        )


@router.post("/triggers-config/reset")
async def reset_triggers_config(
    current_user: User = Depends(get_active_user),
) -> dict[str, Any]:
    verify_btc_radar_permission(current_user)
    try:
        return RadarService.get_instance().reset_triggers_config()
    except Exception as exc:
        logger.error("Falha ao resetar configuração de gatilhos: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Falha ao resetar configuração de gatilhos.",
        )


@router.post("/reset-scoreboard")
async def reset_scoreboard(
    current_user: User = Depends(get_active_user),
) -> dict[str, Any]:
    verify_btc_radar_permission(current_user)
    try:
        service = RadarService.get_instance()
        res = service.reset_user_scoreboard(current_user.id)
        await service.broadcast_now()
        return {"status": "ok", "message": "Placar zerado com sucesso para este usuário", "audit_summary": res}
    except Exception as exc:
        logger.error("Falha ao zerar placar do Radar BTC para usuário #%d: %s", current_user.id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Falha ao zerar placar do Radar BTC.",
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
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(binance_url)
            if resp.status_code == 200:
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
    except Exception as exc:
        logger.warning("Falha ao obter klines da Binance online, usando buffer local: %s", exc)

    # Fallback transparente de alta disponibilidade: buffer local em memória
    try:
        from app.radar.service import RadarService
        service = RadarService.get_instance()
        buf = service.feed.candle_buffers.get(symbol, [])
        if buf:
            return [
                {
                    "time": int(c.get("open_time", 0)) // 1000,
                    "open": float(c.get("open", 0.0)),
                    "high": float(c.get("high", 0.0)),
                    "low": float(c.get("low", 0.0)),
                    "close": float(c.get("close", 0.0)),
                    "volume": float(c.get("volume", 0.0)),
                }
                for c in buf[-limit:]
            ]
    except Exception as e:
        logger.error("Erro no fallback de klines locais: %s", e)

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
    logger.info("[ws-btc] Usuário #%d conectado ao stream nativo do Radar BTC", user_id)

    service = RadarService.get_instance()
    await service.register_client(websocket, user_id=user_id)

    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        service.unregister_client(websocket)
        logger.info("[ws-btc] Sessão do usuário #%d encerrada", user_id)
