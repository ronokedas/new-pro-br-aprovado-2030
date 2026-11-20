"""
data_feed/fast_bbo_feed.py
==========================
Feed BBO multi-exchange SEM THROTTLE (tick-by-tick) para eliminar o delay de @kline_1m
e capturar a convergencia Perp -> Spot nos primeiros milissegundos da vela M1.

Venues conectados (selecionados por medicao empirica ao vivo em audit_live_multivenue_leadlag.py):
  1. Binance USD-M Perp (@bookTicker)         — ~611 ticks/s, r(0ms)=+0.455, r(+50ms)=+0.183
  2. Binance Spot       (@bookTicker)         — ~184 ticks/s (ativo alvo)
  3. Bybit Linear Perp  (orderbook.1.BTCUSDT) — ~33 ticks/s,  r(0ms)=+0.472, r(+50ms)=+0.202
  4. OKX Perp           (bbo-tbt)             — ~35 ticks/s,  r(0ms)=+0.264, r(+50ms)=+0.192

Nota: Coinbase Spot foi excluida como lider pois a medicao ao vivo mostrou pico em -100ms (atrasada).
Inclui sincronizacao automatica de clock com GET /api/v3/time da Binance para compensar
eventual desvio do relogio local do sistema operacional.
"""
import asyncio
import json
import logging
import socket
import struct
import time
from typing import Any, Dict, Optional
import aiohttp
import requests

log = logging.getLogger("fast_bbo_feed")


class FastBBOFeed:
    def __init__(self, symbol: str = "BTCUSDT"):
        self.symbol = symbol.upper()
        self.symbol_lower = symbol.lower()
        self._running = False

        # Compensacao de relogio: UTC externo - relogio local do container.
        # O desvio e medido primeiro via NTP (baixa latencia) e usa Binance HTTP como fallback.
        self.clock_offset_ms: float = 0.0
        self.clock_sync_rtt_ms: Optional[float] = None
        self.clock_sync_source: str = "UNSYNCED"
        self.sync_server_clock()

        # Estado em tempo real (mid = (bid + ask) / 2, spread_bps, local_perf, updated_server_ms)
        self.venues: Dict[str, Dict[str, float]] = {
            "binance_spot": {"bid": 0.0, "ask": 0.0, "mid": 0.0, "updated_ms": 0.0, "ticks": 0},
            "binance_perp": {"bid": 0.0, "ask": 0.0, "mid": 0.0, "updated_ms": 0.0, "ticks": 0},
            "bybit_perp":   {"bid": 0.0, "ask": 0.0, "mid": 0.0, "updated_ms": 0.0, "ticks": 0},
            "okx_perp":     {"bid": 0.0, "ask": 0.0, "mid": 0.0, "updated_ms": 0.0, "ticks": 0},
        }

        # Rastreia abertura exata da vela M1 atual via BBO (sem esperar kline_1m)
        self.current_minute_open_ms: int = 0
        self.minute_spot_open_mid: float = 0.0

    def sync_server_clock(self) -> float:
        """Sincroniza UTC usando NTP; usa a menor RTT para reduzir erro por assimetria."""
        ntp_samples = []
        ntp_epoch = 2_208_988_800
        hosts = ("pool.ntp.org", "time.windows.com")

        for host in hosts:
            for _ in range(3):
                try:
                    addr = socket.getaddrinfo(host, 123, family=socket.AF_INET, type=socket.SOCK_DGRAM)[0][4]
                    packet = bytearray(48)
                    packet[0] = 0x23  # NTP v4, modo cliente

                    t1 = time.time()
                    tx = t1 + ntp_epoch
                    tx_sec = int(tx)
                    tx_frac = int((tx - tx_sec) * (2 ** 32))
                    packet[40:48] = struct.pack("!II", tx_sec, tx_frac)

                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                        sock.settimeout(1.0)
                        sock.sendto(packet, addr)
                        data, _ = sock.recvfrom(512)
                    t4 = time.time()

                    if len(data) < 48 or data[1] == 0:
                        continue

                    def ntp_timestamp(offset: int) -> float:
                        sec, frac = struct.unpack("!II", data[offset:offset + 8])
                        return (sec - ntp_epoch) + frac / (2 ** 32)

                    t2 = ntp_timestamp(32)
                    t3 = ntp_timestamp(40)
                    rtt_ms = ((t4 - t1) - (t3 - t2)) * 1000.0
                    offset_ms = (((t2 - t1) + (t3 - t4)) / 2.0) * 1000.0

                    if 0.0 <= rtt_ms <= 1000.0 and abs(offset_ms) <= 5000.0:
                        ntp_samples.append((rtt_ms, offset_ms, host))
                except Exception:
                    continue

        if ntp_samples:
            best_rtt, best_offset, source = min(ntp_samples, key=lambda sample: sample[0])
            self.clock_offset_ms = best_offset
            self.clock_sync_rtt_ms = best_rtt
            self.clock_sync_source = f"NTP:{source}"
            log.info(
                "UTC sincronizado via %s: offset=%+.1f ms RTT=%.1f ms amostras=%d",
                source, self.clock_offset_ms, best_rtt, len(ntp_samples)
            )
            return self.clock_offset_ms

        # Fallback: mede a hora do servidor Binance com varias amostras e escolhe a menor RTT.
        http_samples = []
        try:
            with requests.Session() as session:
                for _ in range(3):
                    try:
                        t0 = time.time() * 1000.0
                        response = session.get("https://api.binance.com/api/v3/time", timeout=(3, 5))
                        t1 = time.time() * 1000.0
                        response.raise_for_status()
                        server_ms = float(response.json()["serverTime"])
                        http_samples.append((t1 - t0, server_ms - ((t0 + t1) / 2.0)))
                    except Exception:
                        continue

            if http_samples:
                best_rtt, best_offset = min(http_samples, key=lambda sample: sample[0])
                self.clock_offset_ms = best_offset
                self.clock_sync_rtt_ms = best_rtt
                self.clock_sync_source = "BINANCE_HTTP_FALLBACK"
                log.warning(
                    "NTP indisponivel; clock via Binance HTTP: offset=%+.1f ms RTT=%.1f ms",
                    self.clock_offset_ms, best_rtt
                )
            else:
                log.warning("Falha em NTP e Binance HTTP; mantendo ultimo offset %.1f ms", self.clock_offset_ms)
        except Exception as e:
            log.warning("Falha ao sincronizar clock: %s", e)

        return self.clock_offset_ms

    def server_now_ms(self) -> float:
        """Timestamp atual em ms no relogio UTC sincronizado."""
        return time.time() * 1000.0 + self.clock_offset_ms

    def _update_venue(self, name: str, bid: float, ask: float):
        if bid <= 0.0 or ask <= 0.0:
            return
        now_ms = self.server_now_ms()
        mid = 0.5 * (bid + ask)
        v = self.venues[name]
        v["bid"] = bid
        v["ask"] = ask
        v["mid"] = mid
        v["updated_ms"] = now_ms
        v["ticks"] += 1

        # Registra o primeiro mid do minuto atual na Binance Spot (referencia do Produto B)
        if name == "binance_spot":
            min_open = int(now_ms // 60_000) * 60_000
            if min_open != self.current_minute_open_ms:
                self.current_minute_open_ms = min_open
                self.minute_spot_open_mid = mid

    def get_snapshot(self) -> Dict[str, Any]:
        """Snapshot instantaneo causal de todos os venues e desvios frente a Binance Spot (bps)."""
        now_ms = self.server_now_ms()
        s_mid = self.venues["binance_spot"]["mid"]
        p_mid = self.venues["binance_perp"]["mid"]
        by_mid = self.venues["bybit_perp"]["mid"]
        ok_mid = self.venues["okx_perp"]["mid"]

        def _bps(other: float) -> Optional[float]:
            if s_mid > 0 and other > 0:
                return round(((other - s_mid) / s_mid) * 1e4, 3)
            return None

        def _age_ms(name: str) -> Optional[int]:
            u = self.venues[name]["updated_ms"]
            return int(max(0.0, now_ms - u)) if u > 0 else None

        # Consenso multi-perp (media dos desvios frescos < 2s de Binance Perp, Bybit Perp e OKX Perp)
        fresh_perps = []
        for vn in ("binance_perp", "bybit_perp", "okx_perp"):
            age = _age_ms(vn)
            vm = self.venues[vn]["mid"]
            if age is not None and age < 2000 and vm > 0 and s_mid > 0:
                fresh_perps.append((vm - s_mid) / s_mid * 1e4)
        consensus_bps = round(sum(fresh_perps) / len(fresh_perps), 3) if fresh_perps else None

        # Retorno intra-vela atual desde o segundo 00.000 da vela M1 (para Produto B)
        intra_ret_bps = None
        if self.minute_spot_open_mid > 0 and s_mid > 0:
            intra_ret_bps = round(((s_mid - self.minute_spot_open_mid) / self.minute_spot_open_mid) * 1e4, 3)

        sec_in_minute = (now_ms % 60_000) / 1000.0

        return {
            "server_now_ms": int(now_ms),
            "clock_offset_ms": round(self.clock_offset_ms, 1),
            "clock_sync_rtt_ms": round(self.clock_sync_rtt_ms, 1) if self.clock_sync_rtt_ms is not None else None,
            "clock_sync_source": self.clock_sync_source,
            "sec_in_minute": round(sec_in_minute, 2),
            "minute_open_ms": int(now_ms // 60_000) * 60_000,
            "binance_spot_mid": round(s_mid, 2) if s_mid > 0 else None,
            "binance_perp_mid": round(p_mid, 2) if p_mid > 0 else None,
            "bybit_perp_mid": round(by_mid, 2) if by_mid > 0 else None,
            "okx_perp_mid": round(ok_mid, 2) if ok_mid > 0 else None,
            "basis_binance_bps": _bps(p_mid),
            "basis_bybit_bps": _bps(by_mid),
            "basis_okx_bps": _bps(ok_mid),
            "multi_perp_consensus_bps": consensus_bps,
            "intra_spot_ret_bps": intra_ret_bps,
            "ages_ms": {
                "binance_spot": _age_ms("binance_spot"),
                "binance_perp": _age_ms("binance_perp"),
                "bybit_perp": _age_ms("bybit_perp"),
                "okx_perp": _age_ms("okx_perp"),
            },
            "ticks_total": {k: int(v["ticks"]) for k, v in self.venues.items()},
        }

    async def start(self):
        self._running = True
        log.info("Iniciando FastBBOFeed multi-exchange (Binance Spot/Perp + Bybit Perp + OKX Perp)...")
        await asyncio.gather(
            self._ws_binance_spot(),
            self._ws_binance_perp(),
            self._ws_bybit_perp(),
            self._ws_okx_perp(),
            self._clock_resync_loop(),
        )

    def stop(self):
        self._running = False

    async def _clock_resync_loop(self):
        while self._running:
            await asyncio.sleep(300)
            if self._running:
                await asyncio.to_thread(self.sync_server_clock)

    async def _ws_binance_spot(self):
        url = f"wss://stream.binance.com:9443/ws/{self.symbol_lower}@bookTicker"
        while self._running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(url, timeout=15.0, heartbeat=15.0) as ws:
                        async for msg in ws:
                            if not self._running:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                p = json.loads(msg.data)
                                if "b" in p and "a" in p:
                                    self._update_venue("binance_spot", float(p["b"]), float(p["a"]))
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except Exception as e:
                log.warning("FastBBO binance_spot reconectando: %s", e)
                await asyncio.sleep(2)

    async def _ws_binance_perp(self):
        url = f"wss://fstream.binance.com/ws/{self.symbol_lower}@bookTicker"
        while self._running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(url, timeout=15.0, heartbeat=15.0) as ws:
                        async for msg in ws:
                            if not self._running:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                p = json.loads(msg.data)
                                if "b" in p and "a" in p:
                                    self._update_venue("binance_perp", float(p["b"]), float(p["a"]))
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except Exception as e:
                log.warning("FastBBO binance_perp reconectando: %s", e)
                await asyncio.sleep(2)

    async def _ws_bybit_perp(self):
        url = "wss://stream.bybit.com/v5/public/linear"
        while self._running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(url, timeout=15.0, heartbeat=15.0) as ws:
                        await ws.send_json({"op": "subscribe", "args": [f"orderbook.1.{self.symbol}"]})
                        best_b, best_a = 0.0, 0.0
                        async for msg in ws:
                            if not self._running:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                d = json.loads(msg.data)
                                if d.get("topic") == f"orderbook.1.{self.symbol}":
                                    data = d.get("data", {})
                                    if data.get("b"):
                                        best_b = float(data["b"][0][0])
                                    if data.get("a"):
                                        best_a = float(data["a"][0][0])
                                    if best_b > 0 and best_a > 0:
                                        self._update_venue("bybit_perp", best_b, best_a)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except Exception as e:
                log.warning("FastBBO bybit_perp reconectando: %s", e)
                await asyncio.sleep(3)

    async def _ws_okx_perp(self):
        url = "wss://ws.okx.com:8443/ws/v5/public"
        inst_id = f"{self.symbol[:-4]}-{self.symbol[-4:]}-SWAP" if self.symbol.endswith("USDT") else "BTC-USDT-SWAP"
        while self._running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(url, timeout=15.0, heartbeat=15.0) as ws:
                        await ws.send_json({"op": "subscribe", "args": [{"channel": "bbo-tbt", "instId": inst_id}]})
                        async for msg in ws:
                            if not self._running:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                d = json.loads(msg.data)
                                data = d.get("data")
                                if data:
                                    row = data[0]
                                    bids = row.get("bids", [])
                                    asks = row.get("asks", [])
                                    if bids and asks:
                                        self._update_venue("okx_perp", float(bids[0][0]), float(asks[0][0]))
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except Exception as e:
                log.warning("FastBBO okx_perp reconectando: %s", e)
                await asyncio.sleep(3)
