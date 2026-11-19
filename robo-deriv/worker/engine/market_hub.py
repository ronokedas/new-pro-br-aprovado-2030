"""Shared Multi-User Market Hub (Single Public Deriv WS Feed -> Redis + In-Process Listeners).

Responsibilities:
- Maintains ONE shared public Deriv WebSocket connection for ALL active users.
- Refreshes multiplier-eligible symbols (`active_symbols` + `contracts_for` for curated
  Crypto, Forex, Synthetics, and Commodities) into Redis `KEY_MARKET_SYMBOLS` every 10 min.
- Maintains rolling M1 (60s), M5 (300s), M15 (900s) (+ optional H1=3600s) candle buffers
  for all tracked symbols (always includes `cryBTCUSD` by default).
- Computes causal `ChannelDetector` on every candle close/update, persists JSON into Redis
  `key_market_candles` and `key_market_channel`, and publishes live ticks to `ch_market_tick`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Optional, Sequence

from redis.asyncio import Redis

from engine.channel_detector import ChannelDetector, ChannelSnapshot
from engine.deriv_client import DerivClient
from shared.config import get_settings
from shared.redis_keys import (
    KEY_MARKET_SYMBOLS,
    ch_market_tick,
    key_market_candles,
    key_market_channel,
)

logger = logging.getLogger("worker.market_hub")

DEFAULT_GRANULARITIES = (60, 300, 900)
MAX_CANDLES_BUFFER = 300

CURATED_MULTIPLIER_SYMBOLS = {
    # Crypto
    "cryBTCUSD",
    "cryETHUSD",
    # Forex Majors
    "frxEURUSD",
    "frxGBPUSD",
    "frxUSDJPY",
    "frxAUDUSD",
    "frxUSDCAD",
    "frxUSDCHF",
    "frxEURGBP",
    "frxEURJPY",
    # Synthetics / Volatility / Crash-Boom
    "R_10",
    "R_25",
    "R_50",
    "R_75",
    "R_100",
    "1HZ10V",
    "1HZ25V",
    "1HZ50V",
    "1HZ75V",
    "1HZ100V",
    "BOOM300N",
    "BOOM500",
    "BOOM1000",
    "CRASH300N",
    "CRASH500",
    "CRASH1000",
    "stpRNG",
    # Commodities / Metals
    "frxXAUUSD",
    "frxXAGUSD",
}

MARKET_CATEGORY_MAP = {
    "cryptocurrency": "Cripto",
    "forex": "Forex",
    "synthetic_index": "Sintéticos",
    "commodities": "Commodities",
    "indices": "Índices",
}

TickListener = Callable[[str, float, int], Awaitable[None]]
CandleCloseListener = Callable[[str, int, list[dict[str, Any]], Optional[ChannelSnapshot]], Awaitable[None]]


class MarketHub:
    """Shared Deriv public market data aggregator and channel calculator."""

    def __init__(
        self,
        redis_client: Redis,
        deriv_client: Optional[DerivClient] = None,
        channel_detector: Optional[ChannelDetector] = None,
        symbol_refresh_interval_sec: float = 600.0,
    ) -> None:
        settings = get_settings()
        self.redis = redis_client
        v3_public = f"{settings.DERIV_AUTH_WS_BASE}?app_id={settings.DERIV_APP_ID}"
        self.client = deriv_client or DerivClient(
            ws_url=v3_public,
            fallback_ws_url=settings.DERIV_PUBLIC_WS,
            on_reconnect=self._handle_reconnect,
            label="market-hub",
        )
        self.detector = channel_detector or ChannelDetector(window=60)
        self.symbol_refresh_interval_sec = float(symbol_refresh_interval_sec)

        # symbol -> set of granularities (always includes 60, 300, 900)
        self._tracked: dict[str, set[int]] = {"cryBTCUSD": set(DEFAULT_GRANULARITIES)}
        self._subscribed_ticks: set[str] = set()

        # (symbol, granularity) -> list[dict] (closed candles + last forming candle)
        self._candles: dict[tuple[str, int], list[dict[str, Any]]] = {}
        self._channels: dict[tuple[str, int], ChannelSnapshot] = {}
        self._last_prices: dict[str, float] = {}
        self._last_candle_flush: dict[tuple[str, int], float] = {}
        self._symbols_catalog: list[dict[str, Any]] = []

        self._tick_listeners: list[TickListener] = []
        self._candle_close_listeners: list[CandleCloseListener] = []
        self._symbol_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._refresh_task: Optional[asyncio.Task[None]] = None

    @property
    def is_connected(self) -> bool:
        return self.client.is_connected

    @property
    def tracked_symbols(self) -> list[str]:
        return sorted(self._tracked.keys())

    def add_tick_listener(self, listener: TickListener) -> None:
        if listener not in self._tick_listeners:
            self._tick_listeners.append(listener)

    def remove_tick_listener(self, listener: TickListener) -> None:
        if listener in self._tick_listeners:
            self._tick_listeners.remove(listener)

    def add_candle_close_listener(self, listener: CandleCloseListener) -> None:
        if listener not in self._candle_close_listeners:
            self._candle_close_listeners.append(listener)

    def remove_candle_close_listener(self, listener: CandleCloseListener) -> None:
        if listener in self._candle_close_listeners:
            self._candle_close_listeners.remove(listener)

    def get_candles(self, symbol: str, granularity: int) -> list[dict[str, Any]]:
        return list(self._candles.get((symbol, int(granularity)), []))

    def get_channel(self, symbol: str, granularity: int) -> Optional[ChannelSnapshot]:
        return self._channels.get((symbol, int(granularity)))

    def get_last_price(self, symbol: str) -> Optional[float]:
        return self._last_prices.get(symbol)

    def get_symbol_meta(self, symbol: str) -> Optional[dict[str, Any]]:
        for item in self._symbols_catalog:
            if item.get("symbol") == symbol:
                return item
        return None

    async def start(self) -> None:
        self._stop_event.clear()
        await self.client.connect(timeout=20.0)
        # Bootstrap default symbol immediately
        await self.ensure_symbol("cryBTCUSD", 300)
        # Kick off background symbol catalog refresh
        self._refresh_task = asyncio.create_task(
            self._symbol_refresh_loop(), name="market-hub-symbol-refresh"
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None
        await self.client.close()

    async def _handle_reconnect(self, _client: DerivClient) -> None:
        logger.info("MarketHub reconectado — re-hidratando candles e assinaturas de ticks...")
        self._subscribed_ticks.clear()
        for sym, grans in list(self._tracked.items()):
            for g in sorted(grans):
                try:
                    await self._hydrate_candles(sym, g)
                except Exception as exc:
                    logger.warning("Falha ao re-hidratar %s (%ss): %s", sym, g, exc)
            try:
                await self._subscribe_symbol_ticks(sym)
            except Exception as exc:
                logger.warning("Falha ao re-assinar ticks de %s: %s", sym, exc)

    async def ensure_symbol(self, symbol: str, granularity: int = 300) -> None:
        """Ensure a symbol + granularity is hydrated and receiving live ticks."""
        sym = symbol.strip()
        if not sym:
            return
        g_req = int(granularity)

        async with self._symbol_lock:
            grans = self._tracked.setdefault(sym, set(DEFAULT_GRANULARITIES))
            grans.add(g_req)
            needed_grans = [g for g in sorted(grans) if (sym, g) not in self._candles]

            for g in needed_grans:
                try:
                    await self._hydrate_candles(sym, g)
                except Exception as exc:
                    logger.warning("Falha ao carregar histórico %s (%ss): %s", sym, g, exc)

            if sym not in self._subscribed_ticks:
                try:
                    await self._subscribe_symbol_ticks(sym)
                except Exception as exc:
                    logger.warning("Falha ao assinar ticks de %s: %s", sym, exc)

    async def sync_tracked_symbols(self, pairs: Sequence[tuple[str, int]]) -> None:
        """Ensure all active user (symbol, granularity) pairs are tracked."""
        await self.ensure_symbol("cryBTCUSD", 300)
        for sym, gran in pairs:
            await self.ensure_symbol(sym, gran)

    async def _hydrate_candles(self, symbol: str, granularity: int) -> None:
        resp = await self.client.ticks_history(
            symbol=symbol,
            granularity=granularity,
            count=260,
            style="candles",
        )
        raw_candles = resp.get("candles") or []
        if not raw_candles:
            return

        now_epoch = int(time.time())
        current_bucket = (now_epoch // granularity) * granularity
        parsed: list[dict[str, Any]] = []
        for idx, rc in enumerate(raw_candles):
            ep = int(rc["epoch"])
            is_last = idx == len(raw_candles) - 1
            is_closed = not (is_last and ep >= current_bucket)
            parsed.append(
                {
                    "time": ep,
                    "epoch": ep,
                    "open": float(rc["open"]),
                    "high": float(rc["high"]),
                    "low": float(rc["low"]),
                    "close": float(rc["close"]),
                    "is_closed": is_closed,
                }
            )

        parsed = parsed[-MAX_CANDLES_BUFFER:]
        self._candles[(symbol, granularity)] = parsed
        self._last_prices[symbol] = parsed[-1]["close"]

        closed = [c for c in parsed if c.get("is_closed", True)]
        snap = self.detector.detect(closed, symbol=symbol, granularity=granularity)
        if snap is not None:
            self._channels[(symbol, granularity)] = snap

        await self._persist_candles_and_channel(symbol, granularity, parsed, snap)

    async def _subscribe_symbol_ticks(self, symbol: str) -> None:
        if symbol in self._subscribed_ticks:
            return

        async def _on_tick_msg(msg: dict[str, Any]) -> None:
            tick = msg.get("tick")
            if not tick:
                return
            try:
                sym = str(tick.get("symbol") or symbol)
                price = float(tick["quote"])
                epoch = int(tick["epoch"])
            except (KeyError, ValueError, TypeError):
                return
            await self.process_tick(sym, price, epoch)

        await self.client.subscribe_ticks(symbol, _on_tick_msg)
        self._subscribed_ticks.add(symbol)
        logger.info("MarketHub inscrito em ticks ao vivo de %s", symbol)

    async def process_tick(self, symbol: str, price: float, epoch: int) -> None:
        """Ingest one tick, update all tracked timeframe buffers, compute channels, and publish."""
        self._last_prices[symbol] = price
        grans = sorted(self._tracked.get(symbol, DEFAULT_GRANULARITIES))
        now_mono = time.monotonic()

        forming_summary: dict[str, Any] = {}

        for g in grans:
            buf = self._candles.setdefault((symbol, g), [])
            bucket = (int(epoch) // g) * g
            candle_closed = False

            if not buf:
                buf.append(
                    {
                        "time": bucket,
                        "epoch": bucket,
                        "open": price,
                        "high": price,
                        "low": price,
                        "close": price,
                        "is_closed": False,
                    }
                )
            else:
                last = buf[-1]
                if bucket > int(last["epoch"]):
                    # Previous candle is now strictly closed!
                    last["is_closed"] = True
                    candle_closed = True
                    buf.append(
                        {
                            "time": bucket,
                            "epoch": bucket,
                            "open": price,
                            "high": price,
                            "low": price,
                            "close": price,
                            "is_closed": False,
                        }
                    )
                    if len(buf) > MAX_CANDLES_BUFFER:
                        del buf[:-MAX_CANDLES_BUFFER]
                elif bucket == int(last["epoch"]):
                    last["high"] = max(float(last["high"]), price)
                    last["low"] = min(float(last["low"]), price)
                    last["close"] = price

            if g == 60:
                forming_summary["candle_m1"] = buf[-1]
            elif g == 300:
                forming_summary["candle_m5"] = buf[-1]
            elif g == 900:
                forming_summary["candle_m15"] = buf[-1]

            if candle_closed:
                closed = [c for c in buf if c.get("is_closed", True)]
                snap = self.detector.detect(closed, symbol=symbol, granularity=g)
                if snap is not None:
                    self._channels[(symbol, g)] = snap
                await self._persist_candles_and_channel(symbol, g, buf, snap)
                self._last_candle_flush[(symbol, g)] = now_mono

                # Notify in-process listeners (e.g., UserSession strategy evaluation)
                for listener in list(self._candle_close_listeners):
                    try:
                        await listener(symbol, g, list(buf), snap)
                    except Exception as exc:
                        logger.warning("Erro em candle_close_listener (%s %ss): %s", symbol, g, exc)
            else:
                last_flush = self._last_candle_flush.get((symbol, g), 0.0)
                if (now_mono - last_flush) >= 1.0:
                    self._last_candle_flush[(symbol, g)] = now_mono
                    try:
                        await self.redis.set(
                            key_market_candles(symbol, g),
                            json.dumps(buf[-220:]),
                            ex=3600,
                        )
                    except Exception as exc:
                        logger.debug("Redis flush candles falhou: %s", exc)

        # Publish live tick to Redis pub/sub for backend/frontend WebSockets
        tick_payload = {
            "type": "tick",
            "symbol": symbol,
            "price": price,
            "quote": price,
            "epoch": int(epoch),
            **forming_summary,
        }
        try:
            await self.redis.publish(ch_market_tick(symbol), json.dumps(tick_payload))
        except Exception as exc:
            logger.debug("Redis publish tick falhou: %s", exc)

        # Notify in-process tick listeners
        for t_listener in list(self._tick_listeners):
            try:
                await t_listener(symbol, price, int(epoch))
            except Exception as exc:
                logger.warning("Erro em tick_listener (%s): %s", symbol, exc)

    async def _persist_candles_and_channel(
        self,
        symbol: str,
        granularity: int,
        candles: list[dict[str, Any]],
        channel: Optional[ChannelSnapshot],
    ) -> None:
        try:
            pipe = self.redis.pipeline()
            pipe.set(key_market_candles(symbol, granularity), json.dumps(candles[-220:]), ex=3600)
            if channel is not None:
                pipe.set(key_market_channel(symbol, granularity), json.dumps(channel.to_dict()), ex=3600)
            await pipe.execute()
        except Exception as exc:
            logger.warning("Falha ao persistir candles/canal no Redis (%s %ss): %s", symbol, granularity, exc)

    async def _symbol_refresh_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.refresh_multiplier_symbols()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Erro ao atualizar catálogo de símbolos Deriv: %s", exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.symbol_refresh_interval_sec)
            except asyncio.TimeoutError:
                pass

    async def refresh_multiplier_symbols(self) -> list[dict[str, Any]]:
        """Query `active_symbols` + `contracts_for` for curated symbols and cache in Redis."""
        raw_symbols = await self.client.active_symbols(mode="brief")
        if not raw_symbols:
            return self._symbols_catalog

        candidates = [
            s for s in raw_symbols
            if s.get("symbol") in CURATED_MULTIPLIER_SYMBOLS
            or s.get("market") in ("cryptocurrency", "synthetic_index")
        ]
        # Limit total contracts_for fan-out to avoid Deriv rate-limits while covering all curated pairs
        candidates.sort(key=lambda s: (0 if s.get("symbol") in CURATED_MULTIPLIER_SYMBOLS else 1, s.get("symbol", "")))
        candidates = candidates[:45]

        sem = asyncio.Semaphore(3)
        enriched: list[dict[str, Any]] = []

        async def _probe(sym_obj: dict[str, Any]) -> Optional[dict[str, Any]]:
            sym = str(sym_obj.get("symbol", ""))
            if not sym:
                return None
            async with sem:
                try:
                    cf = await self.client.contracts_for(sym)
                except Exception:
                    return None
                await asyncio.sleep(0.08)

            available = cf.get("available") or []
            mult_contracts = [
                c for c in available
                if c.get("contract_category") == "multiplier"
                or c.get("contract_type") in ("MULTUP", "MULTDOWN")
            ]
            if not mult_contracts:
                return None

            multipliers: set[int] = set()
            min_stake = 1.0
            max_stake = 1000.0
            for mc in mult_contracts:
                for m in mc.get("multiplier_range") or []:
                    try:
                        multipliers.add(int(m))
                    except (TypeError, ValueError):
                        pass
                if mc.get("min_contract_duration") is not None:
                    pass
                min_s = mc.get("min_stake") or (mc.get("stake") or {}).get("min")
                max_s = mc.get("max_stake") or (mc.get("stake") or {}).get("max")
                if min_s is not None:
                    try:
                        min_stake = float(min_s)
                    except (TypeError, ValueError):
                        pass
                if max_s is not None:
                    try:
                        max_stake = float(max_s)
                    except (TypeError, ValueError):
                        pass

            sorted_mults = sorted(multipliers) if multipliers else [100]
            market_key = str(sym_obj.get("market", "other"))
            category = MARKET_CATEGORY_MAP.get(market_key, "Outros")
            is_open = bool(int(sym_obj.get("exchange_is_open", 0)) == 1) and not bool(
                int(sym_obj.get("is_trading_suspended", 0)) == 1
            )

            return {
                "symbol": sym,
                "display_name": str(sym_obj.get("display_name") or sym),
                "market": market_key,
                "market_display_name": str(sym_obj.get("market_display_name") or category),
                "submarket": str(sym_obj.get("submarket") or ""),
                "submarket_display_name": str(sym_obj.get("submarket_display_name") or ""),
                "category": category,
                "is_open": is_open,
                "exchange_is_open": 1 if is_open else 0,
                "pip": float(sym_obj.get("pip") or 0.01),
                "min_stake": round(min_stake, 2),
                "max_stake": round(max_stake, 2),
                "multipliers": sorted_mults,
                "default_multiplier": sorted_mults[0],
                "currency": "USD",
            }

        results = await asyncio.gather(*(_probe(s) for s in candidates), return_exceptions=True)
        for item in results:
            if isinstance(item, dict):
                enriched.append(item)

        if enriched:
            enriched.sort(key=lambda x: (0 if x["symbol"] == "cryBTCUSD" else 1, x["category"], x["display_name"]))
            self._symbols_catalog = enriched
            await self.redis.set(KEY_MARKET_SYMBOLS, json.dumps(enriched), ex=900)
            logger.info("Catálogo Deriv atualizado no Redis: %d ativos com Multipliers", len(enriched))

        return self._symbols_catalog
