import json
import logging
import time
from typing import Any, Optional

import redis
import redis.asyncio as aioredis

from shared.config import get_settings
from shared.redis_keys import CH_CONTROL, ch_user_events

logger = logging.getLogger(__name__)

_sync_client: Optional[redis.Redis] = None
_redis_down_until: float = 0.0
_BACKOFF_SECONDS = 5.0


def get_sync_redis() -> Optional[redis.Redis]:
    """Return a sync Redis client, or None if Redis is currently unreachable."""
    global _sync_client, _redis_down_until

    now = time.monotonic()
    if now < _redis_down_until:
        return None

    if _sync_client is None:
        settings = get_settings()
        try:
            _sync_client = redis.Redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=1.0,
                socket_timeout=1.5,
            )
            _sync_client.ping()
        except Exception as exc:
            logger.debug("Redis sync unavailable: %s", exc)
            _sync_client = None
            _redis_down_until = now + _BACKOFF_SECONDS
            return None
    return _sync_client


def reset_sync_redis() -> None:
    """Reset cached sync Redis connection (useful in tests)."""
    global _sync_client, _redis_down_until
    if _sync_client is not None:
        try:
            _sync_client.close()
        except Exception:
            pass
    _sync_client = None
    _redis_down_until = 0.0


def get_async_redis() -> aioredis.Redis:
    """Create an async Redis client bound to configured REDIS_URL."""
    settings = get_settings()
    return aioredis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=1.5,
        socket_timeout=2.0,
    )


def redis_get_json(key: str) -> Optional[Any]:
    client = get_sync_redis()
    if client is None:
        return None
    try:
        raw = client.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        logger.debug("Redis GET failed for %s: %s", key, exc)
        return None


def redis_set_json(key: str, value: Any, ttl: int = 300) -> bool:
    client = get_sync_redis()
    if client is None:
        return False
    try:
        client.setex(key, ttl, json.dumps(value))
        return True
    except Exception as exc:
        logger.debug("Redis SETEX failed for %s: %s", key, exc)
        return False


def publish_control_event(payload: dict[str, Any]) -> bool:
    """Publish a control command (START, STOP, RELOAD_CONFIG, KILL_SWITCH) to CH_CONTROL."""
    client = get_sync_redis()
    if client is None:
        return False
    try:
        client.publish(CH_CONTROL, json.dumps(payload))
        return True
    except Exception as exc:
        logger.warning("Failed to publish control event to Redis: %s", exc)
        return False


def publish_user_event(user_id: int, payload: dict[str, Any]) -> bool:
    """Publish a real-time user event to ch_user_events(user_id)."""
    client = get_sync_redis()
    if client is None:
        return False
    try:
        client.publish(ch_user_events(user_id), json.dumps(payload))
        return True
    except Exception as exc:
        logger.debug("Failed to publish user event to Redis: %s", exc)
        return False


def ping_redis() -> bool:
    """Check whether Redis responds to PING."""
    settings = get_settings()
    try:
        client = redis.Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
        ok = bool(client.ping())
        client.close()
        return ok
    except Exception:
        return False
