# Standardized Redis keys and pub/sub channels shared between Backend and Worker

KEY_MARKET_SYMBOLS = "market:symbols"
KEY_WORKER_HEARTBEAT = "worker:heartbeat"
CH_CONTROL = "cmd:control"


def key_market_candles(symbol: str, granularity: int) -> str:
    return f"market:candles:{symbol}:{granularity}"


def key_market_channel(symbol: str, granularity: int) -> str:
    return f"market:channel:{symbol}:{granularity}"


def ch_market_tick(symbol: str) -> str:
    return f"market:tick:{symbol}"


def key_user_state(user_id: int) -> str:
    return f"user:state:{user_id}"


def ch_user_events(user_id: int) -> str:
    return f"user:events:{user_id}"


def key_order_lock(user_id: int, symbol: str) -> str:
    return f"lock:order:{user_id}:{symbol}"
