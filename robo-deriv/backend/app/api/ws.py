import asyncio
import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from app.core.redis_client import get_async_redis, redis_get_json
from app.core.security import decode_token
from app.services.deriv_service import compute_causal_channel
from app.services.subscription import (
    ensure_bot_config,
    serialize_basket,
    serialize_bot_config,
    serialize_deriv_account,
    serialize_event_log,
    serialize_user,
)
from shared.database import get_session_factory
from shared.models import Basket, BotEventLog, DerivAccount, User
from shared.redis_keys import (
    ch_market_tick,
    ch_user_events,
    key_market_candles,
    key_market_channel,
    key_user_state,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["WebSocket"])


def _load_initial_snapshot(user_id: int) -> Optional[dict[str, Any]]:
    db = get_session_factory()()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None or user.status == "SUSPENDED":
            return None

        cfg = ensure_bot_config(db, user.id)
        active_account = (
            db.query(DerivAccount)
            .filter(
                DerivAccount.user_id == user.id,
                DerivAccount.is_active_selected.is_(True),
            )
            .first()
        )
        open_basket = (
            db.query(Basket)
            .filter(Basket.user_id == user.id, Basket.status == "OPEN")
            .order_by(Basket.opened_at.desc())
            .first()
        )
        recent_events = (
            db.query(BotEventLog)
            .filter(BotEventLog.user_id == user.id)
            .order_by(BotEventLog.created_at.desc())
            .limit(25)
            .all()
        )

        candles = redis_get_json(key_market_candles(cfg.symbol, cfg.timeframe_sec)) or []
        channel = redis_get_json(key_market_channel(cfg.symbol, cfg.timeframe_sec))
        if not isinstance(channel, dict) and candles:
            channel = compute_causal_channel(candles)

        live_state = redis_get_json(key_user_state(user.id)) or {}

        return {
            "user": serialize_user(user),
            "config": serialize_bot_config(cfg),
            "active_account": serialize_deriv_account(active_account) if active_account else None,
            "balance": float(active_account.balance) if active_account else 0.0,
            "currency": active_account.currency if active_account else "USD",
            "open_basket": serialize_basket(open_basket, include_contracts=True) if open_basket else None,
            "recent_events": [serialize_event_log(e) for e in recent_events],
            "worker_state": live_state,
            "chart": {
                "symbol": cfg.symbol,
                "granularity": int(cfg.timeframe_sec),
                "candles": candles[-200:] if isinstance(candles, list) else [],
                "channel": channel,
            },
        }
    finally:
        db.close()


@router.websocket("/ws/dashboard")
async def dashboard_websocket(
    websocket: WebSocket,
    token: Optional[str] = Query(default=None),
):
    if not token:
        await websocket.close(code=4001, reason="Token ausente")
        return

    try:
        payload = decode_token(token, expected_type="access")
        user_id = int(payload["sub"])
    except (HTTPException, ValueError, TypeError):
        await websocket.close(code=4001, reason="Token inválido ou expirado")
        return

    snapshot = _load_initial_snapshot(user_id)
    if snapshot is None:
        await websocket.close(code=4003, reason="Usuário inválido ou suspenso")
        return

    await websocket.accept()
    await websocket.send_json({"type": "snapshot", "data": snapshot})

    current_symbol = snapshot["config"]["symbol"]
    user_channel = ch_user_events(user_id)
    market_channel = ch_market_tick(current_symbol)

    redis_conn = None
    pubsub = None
    listener_task: Optional[asyncio.Task] = None

    async def _redis_listener():
        nonlocal redis_conn, pubsub
        try:
            redis_conn = get_async_redis()
            await redis_conn.ping()
            pubsub = redis_conn.pubsub()
            await pubsub.subscribe(user_channel, market_channel)

            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                channel_name = message.get("channel")
                raw_data = message.get("data")
                try:
                    parsed = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
                except Exception:
                    parsed = {"raw": raw_data}

                if channel_name == user_channel:
                    if isinstance(parsed, dict) and "type" in parsed:
                        await websocket.send_json(parsed)
                    else:
                        await websocket.send_json({"type": "user_event", "data": parsed})
                else:
                    await websocket.send_json({"type": "market_tick", "data": parsed})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("WS Redis pubsub inactive for user %s: %s", user_id, exc)

    listener_task = asyncio.create_task(_redis_listener())

    try:
        while True:
            raw_msg = await websocket.receive_text()
            try:
                client_msg = json.loads(raw_msg)
            except Exception:
                client_msg = {"type": raw_msg}

            msg_type = str(client_msg.get("type", "")).lower()
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg_type == "refresh":
                fresh = _load_initial_snapshot(user_id)
                if fresh is not None:
                    await websocket.send_json({"type": "snapshot", "data": fresh})
            elif msg_type == "subscribe_symbol":
                new_sym = str(client_msg.get("symbol") or "").strip()
                if new_sym and new_sym != current_symbol and pubsub is not None:
                    try:
                        await pubsub.unsubscribe(market_channel)
                        current_symbol = new_sym
                        market_channel = ch_market_tick(current_symbol)
                        await pubsub.subscribe(market_channel)
                        await websocket.send_json(
                            {"type": "subscribed", "data": {"symbol": current_symbol}}
                        )
                    except Exception:
                        pass
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("WS loop terminated for user %s: %s", user_id, exc)
    finally:
        if listener_task is not None and not listener_task.done():
            listener_task.cancel()
            try:
                await listener_task
            except (asyncio.CancelledError, Exception):
                pass
        if pubsub is not None:
            try:
                await pubsub.unsubscribe()
                await pubsub.close()
            except Exception:
                pass
        if redis_conn is not None:
            try:
                await redis_conn.aclose()
            except Exception:
                pass
