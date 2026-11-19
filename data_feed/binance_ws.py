"""
binance_ws.py
Cliente WebSocket da Binance que inicializa o buffer imediatamente com 150 velas históricas
via REST API para que todos os indicadores (EMA, RSI, CVD) estejam 100% aquecidos desde o segundo 1.
"""
import asyncio
import json
import logging
import time
from typing import Dict, Any, List, Callable, Optional
import aiohttp
import requests

log = logging.getLogger("binance_ws")

BINANCE_REST_URL = "https://api.binance.com/api/v3/klines"
BINANCE_PERP_REST_URL = "https://fapi.binance.com/fapi/v1/klines"

class BinanceDataFeed:
    def __init__(self, primary_symbol: str = "BTCUSDT", lead_symbols: List[str] = None):
        self.primary_symbol = primary_symbol.lower()
        self.candle_buffers: Dict[str, List[Dict[str, Any]]] = {self.primary_symbol: []}
        self.perp_candle_buffers: Dict[str, List[Dict[str, Any]]] = {self.primary_symbol: []}
        self.current_candle: Dict[str, Any] = {}
        self.last_price: float = 0.0

        # Callbacks
        self.on_candle_update: Optional[Callable] = None
        self.on_candle_closed: Optional[Callable] = None

        self._running = False
        self._ws_url = f"wss://stream.binance.com:9443/ws/{self.primary_symbol}@kline_1m"
        self._perp_ws_url = f"wss://fstream.binance.com/ws/{self.primary_symbol}@kline_1m"

        # Pré-carrega os buffers Spot e Perp imediatamente
        self._preload_historical_candles()
        self._preload_perp_candles()

    def _preload_historical_candles(self):
        """Baixa 500 velas históricas da Binance e separa fechadas vs aberta por close_time < now_ms."""
        try:
            log.info("Pré-carregando 500 velas M1 históricas da Binance para %s...", self.primary_symbol.upper())
            params = {
                "symbol": self.primary_symbol.upper(),
                "interval": "1m",
                "limit": 500
            }
            r = requests.get(BINANCE_REST_URL, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            now_ms = int(time.time() * 1000)

            closed = []
            open_candle = None
            for item in data:
                c_close_time = int(item[6])
                is_closed = c_close_time < now_ms
                candle = {
                    "symbol": self.primary_symbol.upper(),
                    "open_time": int(item[0]),
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]),
                    "close_time": c_close_time,
                    "taker_buy_base": float(item[9]),
                    "num_trades": int(item[8]),
                    "is_closed": is_closed
                }
                if is_closed:
                    closed.append(candle)
                else:
                    open_candle = candle

            if closed:
                self.candle_buffers[self.primary_symbol] = closed
                self.current_candle = open_candle if open_candle is not None else closed[-1]
                self.last_price = self.current_candle["close"]
                log.info("✅ Buffer histórico pronto com %d velas M1 fechadas!", len(closed))
        except Exception as e:
            log.error("Erro ao pré-carregar histórico: %s", e)

    def _preload_perp_candles(self):
        """Baixa 500 velas M1 fechadas do Perp (fapi/v1/klines) para cálculo causal do basis."""
        try:
            params = {
                "symbol": self.primary_symbol.upper(),
                "interval": "1m",
                "limit": 500
            }
            r = requests.get(BINANCE_PERP_REST_URL, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            now_ms = int(time.time() * 1000)
            closed = []
            for item in data:
                c_close_time = int(item[6])
                if c_close_time >= now_ms:
                    continue
                closed.append({
                    "symbol": self.primary_symbol.upper(),
                    "open_time": int(item[0]),
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]),
                    "close_time": c_close_time,
                    "taker_buy_base": float(item[9]),
                    "num_trades": int(item[8]),
                    "is_closed": True
                })
            if closed:
                self.perp_candle_buffers[self.primary_symbol] = closed
                log.info("✅ Buffer Perp histórico pronto com %d velas M1 fechadas!", len(closed))
        except Exception as e:
            log.error("Erro ao pré-carregar histórico Perp: %s", e)

    async def start(self):
        self._running = True
        log.info("Iniciando WebSockets Spot + Perp da Binance para %s...", self.primary_symbol)
        await asyncio.gather(
            self._run_spot_ws(),
            self._run_perp_ws(),
        )

    async def _run_spot_ws(self):
        while self._running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(self._ws_url, timeout=20.0, heartbeat=15.0) as ws:
                        log.info("Conectado ao WebSocket Spot Binance (%s)!", self.primary_symbol.upper())
                        async for msg in ws:
                            if not self._running:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                await self._handle_kline(data)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                log.warning("WebSocket Spot desconectado, reconectando...")
                                break
            except Exception as e:
                log.error("Erro no WebSocket Spot: %s. Reconectando em 3s...", e)
                await asyncio.sleep(3)

    async def _run_perp_ws(self):
        while self._running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(self._perp_ws_url, timeout=20.0, heartbeat=15.0) as ws:
                        log.info("Conectado ao WebSocket Perp Binance (%s)!", self.primary_symbol.upper())
                        async for msg in ws:
                            if not self._running:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                self._handle_perp_kline(data)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                log.warning("WebSocket Perp desconectado, reconectando...")
                                break
            except Exception as e:
                log.error("Erro no WebSocket Perp: %s. Reconectando em 3s...", e)
                await asyncio.sleep(3)

    def _handle_perp_kline(self, data: Dict[str, Any]):
        k = data.get("k", {})
        if not k or not k.get("x", False):
            return
        candle = {
            "symbol": self.primary_symbol.upper(),
            "open_time": int(k.get("t", 0)),
            "close_time": int(k.get("T", 0)),
            "open": float(k.get("o", 0)),
            "high": float(k.get("h", 0)),
            "low": float(k.get("l", 0)),
            "close": float(k.get("c", 0)),
            "volume": float(k.get("v", 0)),
            "taker_buy_base": float(k.get("V", 0)),
            "num_trades": int(k.get("n", 0)),
            "is_closed": True
        }
        buf = self.perp_candle_buffers.setdefault(self.primary_symbol, [])
        if not buf or buf[-1]["open_time"] != candle["open_time"]:
            buf.append(candle)
        else:
            buf[-1] = candle
        if len(buf) > 500:
            buf.pop(0)

    def stop(self):
        self._running = False

    async def _handle_kline(self, data: Dict[str, Any]):
        k = data.get("k", {})
        if not k:
            return

        is_closed = k.get("x", False)
        open_time = int(k.get("t", 0))
        close_time = int(k.get("T", 0))

        candle = {
            "symbol": self.primary_symbol.upper(),
            "open_time": open_time,
            "close_time": close_time,
            "open": float(k.get("o", 0)),
            "high": float(k.get("h", 0)),
            "low": float(k.get("l", 0)),
            "close": float(k.get("c", 0)),
            "volume": float(k.get("v", 0)),
            "taker_buy_base": float(k.get("V", 0)),
            "num_trades": int(k.get("n", 0)),
            "is_closed": is_closed
        }

        self.current_candle = candle
        self.last_price = candle["close"]

        buf = self.candle_buffers.setdefault(self.primary_symbol, [])

        if is_closed:
            if not buf or buf[-1]["open_time"] != candle["open_time"]:
                buf.append(candle)
            else:
                buf[-1] = candle
            if len(buf) > 500:
                buf.pop(0)
            if self.on_candle_closed:
                await self.on_candle_closed(candle, buf)
        else:
            if self.on_candle_update:
                await self.on_candle_update(candle, buf)
