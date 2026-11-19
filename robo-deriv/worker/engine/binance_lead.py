"""Optional lightweight Binance public aggTrade lead-flow tracker for cryBTCUSD.

Connects to Binance public WebSocket (`btcusdt@aggTrade`) to compute rolling
15-second and 60-second taker buy/sell USD volume delta and bullish/bearish
absorption states. Designed to fail gracefully and never crash the worker if
Binance is blocked, rate-limited, or unreachable.
"""

from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
import time
from typing import Any, Deque, Optional, Tuple

import websockets

logger = logging.getLogger("worker.binance_lead")

BINANCE_AGGTRADE_URL = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"


class BinanceLeadFilter:
    """Async background monitor for Binance BTCUSDT taker volume delta & absorption."""

    def __init__(
        self,
        url: str = BINANCE_AGGTRADE_URL,
        stale_after_sec: float = 20.0,
        max_backoff_sec: float = 60.0,
    ) -> None:
        self.url = url
        self.stale_after_sec = float(stale_after_sec)
        self.max_backoff_sec = float(max_backoff_sec)

        # Each entry: (ts_epoch_sec, price, notional_usd, is_taker_buy)
        self._trades: Deque[Tuple[float, float, float, bool]] = deque()
        self._last_price: float = 0.0
        self._last_update_ts: float = 0.0
        self._connected: bool = False
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="binance-lead-filter")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._connected = False

    def ingest_trade(self, price: float, qty: float, is_buyer_maker: bool, ts: Optional[float] = None) -> None:
        """Ingest one aggTrade event (also usable directly in unit tests)."""
        now = float(ts) if ts is not None else time.time()
        notional = float(price) * float(qty)
        # In Binance aggTrade: m=True means buyer was maker => aggressive taker SELL
        is_taker_buy = not bool(is_buyer_maker)
        self._trades.append((now, float(price), notional, is_taker_buy))
        self._last_price = float(price)
        self._last_update_ts = now
        self._prune(now)

    def _prune(self, now: float) -> None:
        cutoff = now - 65.0
        while self._trades and self._trades[0][0] < cutoff:
            self._trades.popleft()

    def get_state(self, now: Optional[float] = None) -> dict[str, Any]:
        """Return rolling 15s / 60s delta and absorption state."""
        ts_now = float(now) if now is not None else time.time()
        self._prune(ts_now)

        is_fresh = (
            self._last_update_ts > 0.0
            and (ts_now - self._last_update_ts) <= self.stale_after_sec
            and len(self._trades) > 0
        )

        buy_15 = 0.0
        sell_15 = 0.0
        buy_60 = 0.0
        sell_60 = 0.0
        price_15s_ago: Optional[float] = None

        cutoff_15 = ts_now - 15.0
        cutoff_60 = ts_now - 60.0

        for t_ts, t_price, notional, is_buy in self._trades:
            if t_ts >= cutoff_60:
                if is_buy:
                    buy_60 += notional
                else:
                    sell_60 += notional
            if t_ts >= cutoff_15:
                if price_15s_ago is None:
                    price_15s_ago = t_price
                if is_buy:
                    buy_15 += notional
                else:
                    sell_15 += notional

        delta_15s = buy_15 - sell_15
        delta_60s = buy_60 - sell_60

        absorption_bullish = bool(
            is_fresh
            and price_15s_ago is not None
            and delta_60s < 0.0
            and delta_15s > 0.0
            and self._last_price >= price_15s_ago
        )
        absorption_bearish = bool(
            is_fresh
            and price_15s_ago is not None
            and delta_60s > 0.0
            and delta_15s < 0.0
            and self._last_price <= price_15s_ago
        )

        return {
            "available": bool(is_fresh),
            "connected": bool(self._connected),
            "last_price": round(self._last_price, 2),
            "buy_vol_15s": round(buy_15, 2),
            "sell_vol_15s": round(sell_15, 2),
            "delta_15s": round(delta_15s, 2),
            "buy_vol_60s": round(buy_60, 2),
            "sell_vol_60s": round(sell_60, 2),
            "delta_60s": round(delta_60s, 2),
            "absorption_bullish": absorption_bullish,
            "absorption_bearish": absorption_bearish,
            "updated_at": self._last_update_ts,
        }

    async def _run_loop(self) -> None:
        backoff = 2.0
        warned_once = False
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(
                    self.url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                ) as ws:
                    self._connected = True
                    backoff = 2.0
                    warned_once = False
                    logger.info("BinanceLeadFilter conectado em btcusdt@aggTrade")

                    async for raw in ws:
                        if self._stop_event.is_set():
                            break
                        try:
                            data = json.loads(raw)
                            price = float(data["p"])
                            qty = float(data["q"])
                            is_buyer_maker = bool(data["m"])
                            self.ingest_trade(price=price, qty=qty, is_buyer_maker=is_buyer_maker)
                        except (KeyError, ValueError, TypeError):
                            continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._connected = False
                if not warned_once:
                    logger.warning(
                        "BinanceLeadFilter indisponível (%s). Operando sem filtro de fluxo Binance até reconectar.",
                        exc.__class__.__name__,
                    )
                    warned_once = True
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(self.max_backoff_sec, backoff * 2.0)
            finally:
                self._connected = False
