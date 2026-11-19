"""Multi-User Quantitative Worker Coordinator.

Supervises:
- Shared `MarketHub` (single public Deriv WS feed for all symbols & users).
- Optional `BinanceLeadFilter` (public btcusdt@aggTrade order-flow lead filter).
- Isolated per-user `UserSession` tasks.
- Redis `CH_CONTROL` command listener (`START`, `STOP`, `KILL_SWITCH`, `RELOAD_CONFIG`, `SYNC_ACCOUNT`).
- Periodic Postgres sync for enabled bots, open baskets (Safe Expiration Draining), and tracked symbols.
- Periodic `KEY_WORKER_HEARTBEAT` publishing every 5 seconds.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from redis.asyncio import Redis

from engine.binance_lead import BinanceLeadFilter
from engine.market_hub import MarketHub
from engine.user_session import UserSession
from shared.config import get_settings
from shared.database import init_db, session_scope
from shared.models import Basket, BotConfig, utcnow
from shared.redis_keys import CH_CONTROL, KEY_WORKER_HEARTBEAT

logger = logging.getLogger("worker.coordinator")


class WorkerCoordinator:
    """Top-level orchestrator for the multi-user trading worker."""

    def __init__(
        self,
        redis_client: Optional[Redis] = None,
        db_sync_interval_sec: float = 10.0,
        heartbeat_interval_sec: float = 5.0,
    ) -> None:
        settings = get_settings()
        self.redis: Redis = redis_client or Redis.from_url(settings.REDIS_URL, decode_responses=True)
        self.db_sync_interval_sec = float(db_sync_interval_sec)
        self.heartbeat_interval_sec = float(heartbeat_interval_sec)

        self.market_hub = MarketHub(redis_client=self.redis)
        self.binance_lead = BinanceLeadFilter()
        self.sessions: dict[int, UserSession] = {}

        self._session_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        """Initialize DB, start MarketHub + BinanceLeadFilter, and launch supervisor loops."""
        self._stop_event.clear()
        await self._wait_for_db()

        logger.info("Iniciando MarketHub compartilhado e BinanceLeadFilter...")
        await self.market_hub.start()
        await self.binance_lead.start()

        # Initial DB sync before listening to control channel
        await self.sync_from_database()

        self._tasks = [
            asyncio.create_task(self._control_listener_loop(), name="coord-control-listener"),
            asyncio.create_task(self._db_sync_loop(), name="coord-db-sync"),
            asyncio.create_task(self._heartbeat_loop(), name="coord-heartbeat"),
        ]
        logger.info("WorkerCoordinator operacional e escutando %s", CH_CONTROL)

    async def stop(self) -> None:
        """Gracefully shut down all supervisor tasks, user sessions, and market feeds."""
        logger.info("Encerrando WorkerCoordinator...")
        self._stop_event.set()

        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        async with self._session_lock:
            for uid, sess in list(self.sessions.items()):
                try:
                    await sess.stop()
                except Exception as exc:
                    logger.warning("Erro ao parar sessão do usuário %d: %s", uid, exc)
            self.sessions.clear()

        await self.binance_lead.stop()
        await self.market_hub.stop()
        try:
            await self.redis.aclose()
        except Exception:
            pass
        logger.info("WorkerCoordinator finalizado com segurança.")

    async def run_forever(self) -> None:
        await self.start()
        try:
            await self._stop_event.wait()
        finally:
            await self.stop()

    def request_shutdown(self) -> None:
        self._stop_event.set()

    async def _wait_for_db(self, max_attempts: int = 20) -> None:
        for attempt in range(1, max_attempts + 1):
            try:
                await asyncio.to_thread(init_db)
                return
            except Exception as exc:
                logger.warning("Aguardando PostgreSQL (%d/%d): %s", attempt, max_attempts, exc)
                await asyncio.sleep(2.0)
        raise RuntimeError("PostgreSQL indisponível após múltiplas tentativas")

    async def _heartbeat_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                binance_state = self.binance_lead.get_state()
                payload = {
                    "status": "ONLINE",
                    "timestamp": utcnow().isoformat(),
                    "epoch": time.time(),
                    "active_sessions": len(self.sessions),
                    "active_user_ids": sorted(self.sessions.keys()),
                    "tracked_symbols": self.market_hub.tracked_symbols,
                    "market_hub_connected": self.market_hub.is_connected,
                    "binance_lead_available": bool(binance_state.get("available", False)),
                }
                await self.redis.set(KEY_WORKER_HEARTBEAT, json.dumps(payload), ex=30)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Erro ao publicar heartbeat do worker: %s", exc)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.heartbeat_interval_sec)
            except asyncio.TimeoutError:
                pass

    async def _db_sync_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.db_sync_interval_sec)
                break
            except asyncio.TimeoutError:
                pass
            try:
                await self.sync_from_database()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Erro na sincronização periódica com Postgres: %s", exc)

    async def sync_from_database(self) -> None:
        """Start sessions for enabled configs or users with OPEN baskets; stop idle disabled ones."""
        def _query_needed() -> tuple[set[int], list[tuple[str, int]]]:
            with session_scope() as db:
                enabled_cfgs = db.query(BotConfig).filter(BotConfig.enabled.is_(True)).all()
                open_baskets = db.query(Basket).filter(Basket.status == "OPEN").all()

                needed_users: set[int] = set()
                pairs: set[tuple[str, int]] = {("cryBTCUSD", 300)}

                for cfg in enabled_cfgs:
                    needed_users.add(int(cfg.user_id))
                    pairs.add((str(cfg.symbol), int(cfg.timeframe_sec)))
                    if getattr(cfg, "multi_pair_enabled", False):
                        raw_sc = getattr(cfg, "scanner_symbols_json", None)
                        if raw_sc:
                            try:
                                syms = json.loads(raw_sc)
                                if isinstance(syms, list):
                                    for s in syms:
                                        if isinstance(s, str) and s.strip():
                                            pairs.add((s.strip(), int(cfg.timeframe_sec or 300)))
                            except Exception:
                                pass

                for b in open_baskets:
                    needed_users.add(int(b.user_id))
                    pairs.add((str(b.symbol), 300))

                return needed_users, sorted(pairs)

        needed_users, symbol_pairs = await asyncio.to_thread(_query_needed)
        await self.market_hub.sync_tracked_symbols(symbol_pairs)

        async with self._session_lock:
            # Start missing sessions
            for uid in sorted(needed_users):
                sess = self.sessions.get(uid)
                if sess is None or not sess.is_running:
                    await self._start_user_session_unlocked(uid)

            # Stop sessions that are neither enabled nor holding an open basket
            for uid in list(self.sessions.keys()):
                if uid not in needed_users:
                    sess = self.sessions.pop(uid)
                    if not sess.has_open_basket:
                        logger.info("[coord] Encerrando sessão ociosa do usuário %d", uid)
                        await sess.stop(reason_status="OFF")
                    else:
                        # Keep managing open basket until drained
                        self.sessions[uid] = sess

    async def _start_user_session_unlocked(self, user_id: int) -> Optional[UserSession]:
        existing = self.sessions.get(user_id)
        if existing is not None and existing.is_running:
            return existing

        sess = UserSession(
            user_id=user_id,
            redis_client=self.redis,
            market_hub=self.market_hub,
            binance_lead=self.binance_lead,
        )
        try:
            await sess.start()
            if sess.is_running:
                self.sessions[user_id] = sess
                return sess
        except Exception as exc:
            logger.warning("[coord] Falha ao iniciar UserSession(%d): %s", user_id, exc)
        return None

    async def _control_listener_loop(self) -> None:
        while not self._stop_event.is_set():
            pubsub = self.redis.pubsub()
            try:
                await pubsub.subscribe(CH_CONTROL)
                logger.info("Escutando comandos em Redis pub/sub '%s'", CH_CONTROL)
                while not self._stop_event.is_set():
                    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if msg is None:
                        continue
                    raw_data = msg.get("data")
                    if not isinstance(raw_data, str):
                        continue
                    try:
                        cmd = json.loads(raw_data)
                    except json.JSONDecodeError:
                        continue
                    await self.handle_control_command(cmd)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Erro no listener de controle Redis: %s. Reconectando em 3s...", exc)
                await asyncio.sleep(3.0)
            finally:
                try:
                    await pubsub.unsubscribe(CH_CONTROL)
                    await pubsub.aclose()
                except Exception:
                    pass

    async def handle_control_command(self, cmd: dict[str, Any]) -> None:
        """Dispatch START, STOP, KILL_SWITCH, RELOAD_CONFIG, and SYNC_ACCOUNT commands."""
        action = str(cmd.get("action") or cmd.get("command") or cmd.get("type") or "").upper()
        raw_uid = cmd.get("user_id")
        if not action or raw_uid is None:
            return

        try:
            user_id = int(raw_uid)
        except (TypeError, ValueError):
            return

        logger.info("[coord] Comando recebido: action=%s user_id=%d", action, user_id)

        if action == "START":
            async with self._session_lock:
                sess = self.sessions.get(user_id)
                if sess is not None and sess.is_running:
                    await sess.reload_config()
                else:
                    await self._start_user_session_unlocked(user_id)

        elif action == "STOP":
            async with self._session_lock:
                sess = self.sessions.get(user_id)
                if sess is not None:
                    if sess.has_open_basket:
                        # Keep draining open basket safely; just reload so enabled=False takes effect
                        await sess.reload_config()
                    else:
                        self.sessions.pop(user_id, None)
                        await sess.stop(reason_status="OFF")

        elif action == "KILL_SWITCH":
            async with self._session_lock:
                sess = self.sessions.get(user_id)
                ephemeral = False
                if sess is None:
                    ephemeral = True
                    sess = UserSession(
                        user_id=user_id,
                        redis_client=self.redis,
                        market_hub=self.market_hub,
                        binance_lead=self.binance_lead,
                    )
                    loaded = await asyncio.to_thread(sess._load_state_from_db)
                    if loaded and sess._token_encrypted:
                        try:
                            await sess._connect_deriv_client()
                        except Exception as exc:
                            logger.warning("[coord] Kill Switch não pôde conectar Deriv user=%d: %s", user_id, exc)
                await sess.kill_switch()
                self.sessions.pop(user_id, None)
                if ephemeral or not sess.has_open_basket:
                    await sess.stop(reason_status="KILL SWITCH")

        elif action == "RELOAD_CONFIG":
            should_sync = False
            async with self._session_lock:
                sess = self.sessions.get(user_id)
                if sess is not None and sess.is_running:
                    await sess.reload_config()
                else:
                    should_sync = True
            if should_sync:
                await self.sync_from_database()

        elif action == "SYNC_ACCOUNT":
            async with self._session_lock:
                sess = self.sessions.get(user_id)
                if sess is not None and sess.is_running:
                    await sess.reload_config()
                    await sess.sync_account()
                    await sess.publish_state()
                else:
                    # One-shot account balance sync even when bot is OFF
                    temp = UserSession(
                        user_id=user_id,
                        redis_client=self.redis,
                        market_hub=self.market_hub,
                        binance_lead=self.binance_lead,
                    )
                    loaded = await asyncio.to_thread(temp._load_state_from_db)
                    if loaded and temp._token_encrypted:
                        try:
                            await temp._connect_deriv_client()
                            await temp.sync_account()
                            await temp.publish_state()
                        except Exception as exc:
                            logger.warning("[coord] SYNC_ACCOUNT falhou user=%d: %s", user_id, exc)
                        finally:
                            await temp.stop()
