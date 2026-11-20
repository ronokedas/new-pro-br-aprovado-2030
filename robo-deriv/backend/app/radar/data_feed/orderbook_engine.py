"""
data_feed/orderbook_engine.py
=============================
Motor causal de Livro de Ofertas L2 (@depth20@100ms) + Tape (@aggTrade) da Binance Spot e Perp.

Implementa as metricas reais de microestrutura para distinguir REVERSAO vs CONTINUACAO:
  1. Imbalance Top-5 e Top-20: (sum_bid - sum_ask) / (sum_bid + sum_ask)
  2. Microprice Skew (Stoikov): ((ask*q_bid + bid*q_ask)/(q_bid+q_ask) - mid) / mid (em bps)
  3. OFI (Order Flow Imbalance — Cont, Kukanov & Stoikov 2014) em janelas de 1s, 5s e 15s
  4. Classificador Book vs Tape (5s):
       - ABSORCAO (candidato a REVERSAO): fluxo agressor forte contra um lado (ex: venda forte),
         mas preco nao rompe e liquidez do lado defendido se mantem/recarrega (iceberg/defesa).
       - SPOOFING (candidato a CONTINUACAO): liquidez no book some (dQ << 0) SEM volume agressor
         equivalente no aggTrade (cancelamento de parede falsa).
       - VARREDURA / SWEEP (candidato a CONTINUACAO): fluxo agressor consome o book e desloca
         o melhor bid/ask na mesma direcao.

MODO SHADOW AUDITADO (prompat.txt):
  - Por seguranca cientifica, este modulo roda em paralelo expondo telemetria real em
    /api/fast-telemetry e gravando snapshots + auditoria shadow em audit_data/l2_live_audit.jsonl
    para validacao fora da amostra antes de alterar a regra validada de decisao.
"""
import asyncio
import collections
import json
import logging
import time
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple
import aiohttp

log = logging.getLogger("orderbook_engine")


class OrderBookEngine:
    def __init__(self, symbol: str = "BTCUSDT", audit_dir: Optional[Path] = None):
        self.symbol = symbol.upper()
        self.symbol_lower = symbol.lower()
        self._running = False

        self.audit_dir = audit_dir or (Path(__file__).resolve().parent.parent / "audit_data")
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.audit_log_path = self.audit_dir / "l2_live_audit.jsonl"

        # Estado atual do livro L2 (Spot e Perp)
        self.spot_book: Dict[str, Any] = {"bids": [], "asks": [], "updated": 0.0}
        self.perp_book: Dict[str, Any] = {"bids": [], "asks": [], "updated": 0.0}

        # Estado anterior top-1 para calculo de OFI (Cont et al. 2014)
        self._prev_top: Dict[str, Optional[Tuple[float, float, float, float]]] = {"spot": None, "perp": None}

        # Janelas rolantes causais: (perf_ts, valor)
        self.ofi_spot: Deque[Tuple[float, float]] = collections.deque(maxlen=4000)
        self.ofi_perp: Deque[Tuple[float, float]] = collections.deque(maxlen=4000)

        # Tape aggTrade Spot: (perf_ts, price, qty, is_buyer_maker)
        # is_buyer_maker == True -> agressao de VENDA; False -> agressao de COMPRA
        self.trades_spot: Deque[Tuple[float, float, float, bool]] = collections.deque(maxlen=15000)

        # Historico curto de profundidade top-5 para detectar cancelamento (spoofing) vs recarga (absorcao)
        # (perf_ts, best_bid, best_ask, sum_bid5, sum_ask5)
        self.depth5_hist: Deque[Tuple[float, float, float, float, float]] = collections.deque(maxlen=600)

        # Shadow audit (100% separado do placar principal!)
        self._pending_shadow: Dict[int, Dict[str, Any]] = {}
        self.shadow_audit: Dict[str, Any] = {
            "total": 0, "wins": 0, "losses": 0, "dojis": 0, "winrate": 0.0,
            "by_regime": {
                "ABSORCAO_REVERSAO": {"total": 0, "wins": 0, "winrate": 0.0},
                "SWEEP_CONTINUACAO": {"total": 0, "wins": 0, "winrate": 0.0},
                "SPOOF_CONTINUACAO": {"total": 0, "wins": 0, "winrate": 0.0},
                "OFI_IMBALANCE":     {"total": 0, "wins": 0, "winrate": 0.0},
            }
        }

    def _compute_ofi_delta(self, side_key: str, best_b: float, q_b: float, best_a: float, q_a: float) -> float:
        prev = self._prev_top[side_key]
        self._prev_top[side_key] = (best_b, q_b, best_a, q_a)
        if prev is None:
            return 0.0
        pb, pqb, pa, pqa = prev
        # Formula canonica OFI (Cont, Kukanov & Stoikov 2014)
        e_b = q_b if best_b > pb else (q_b - pqb if best_b == pb else -pqb)
        e_a = -q_a if best_a < pa else (pqa - q_a if best_a == pa else pqa)
        return e_b + e_a

    def _on_depth(self, market: str, bids_raw: List[List[str]], asks_raw: List[List[str]]):
        if not bids_raw or not asks_raw:
            return
        bids = [(float(p), float(q)) for p, q in bids_raw[:20]]
        asks = [(float(p), float(q)) for p, q in asks_raw[:20]]
        now = time.perf_counter()

        best_b, q_b = bids[0]
        best_a, q_a = asks[0]
        ofi = self._compute_ofi_delta(market, best_b, q_b, best_a, q_a)

        if market == "spot":
            self.spot_book = {"bids": bids, "asks": asks, "updated": now}
            self.ofi_spot.append((now, ofi))
            sum_b5 = sum(q for _, q in bids[:5])
            sum_a5 = sum(q for _, q in asks[:5])
            self.depth5_hist.append((now, best_b, best_a, sum_b5, sum_a5))
        else:
            self.perp_book = {"bids": bids, "asks": asks, "updated": now}
            self.ofi_perp.append((now, ofi))

    def _on_agg_trade_spot(self, price: float, qty: float, is_buyer_maker: bool):
        self.trades_spot.append((time.perf_counter(), price, qty, is_buyer_maker))

    def _sum_window(self, dq: Deque[Tuple[float, float]], now: float, sec: float) -> float:
        cutoff = now - sec
        total = 0.0
        for t, val in reversed(dq):
            if t < cutoff:
                break
            total += val
        return total

    def get_snapshot(self) -> Dict[str, Any]:
        """Calcula estado causal completo do livro L2 + classificacao Absorcao / Spoofing / Sweep."""
        now = time.perf_counter()
        sb = self.spot_book
        pb = self.perp_book

        if not sb["bids"] or not sb["asks"]:
            return {"ready": False, "regime": "AGUARDANDO_L2", "shadow_signal": "WAIT"}

        b_spot, a_spot = sb["bids"], sb["asks"]
        best_b, qb1 = b_spot[0]
        best_a, qa1 = a_spot[0]
        mid = 0.5 * (best_b + best_a)

        # 1. Imbalance Top-5 e Top-20 (Spot)
        sum_b5 = sum(q for _, q in b_spot[:5])
        sum_a5 = sum(q for _, q in a_spot[:5])
        imb5_spot = (sum_b5 - sum_a5) / max(1e-9, sum_b5 + sum_a5)

        sum_b20 = sum(q for _, q in b_spot[:20])
        sum_a20 = sum(q for _, q in a_spot[:20])
        imb20_spot = (sum_b20 - sum_a20) / max(1e-9, sum_b20 + sum_a20)

        # Imbalance Top-5 Perp
        imb5_perp = 0.0
        if pb["bids"] and pb["asks"]:
            pb5 = sum(q for _, q in pb["bids"][:5])
            pa5 = sum(q for _, q in pb["asks"][:5])
            imb5_perp = (pb5 - pa5) / max(1e-9, pb5 + pa5)

        # 2. Microprice Skew (Stoikov) em bps
        microprice = (best_a * qb1 + best_b * qa1) / max(1e-9, qb1 + qa1)
        micro_skew_bps = ((microprice - mid) / mid) * 1e4 if mid > 0 else 0.0

        # 3. OFI acumulado 1s, 5s, 15s (Spot e Perp)
        ofi_s_1s = self._sum_window(self.ofi_spot, now, 1.0)
        ofi_s_5s = self._sum_window(self.ofi_spot, now, 5.0)
        ofi_s_15s = self._sum_window(self.ofi_spot, now, 15.0)
        ofi_p_5s = self._sum_window(self.ofi_perp, now, 5.0)

        # 4. Fluxo agressor do Tape Spot nos ultimos 5s
        cutoff_5s = now - 5.0
        taker_buy_5s = 0.0
        taker_sell_5s = 0.0
        for t, _p, q, is_bm in reversed(self.trades_spot):
            if t < cutoff_5s:
                break
            if is_bm:
                taker_sell_5s += q
            else:
                taker_buy_5s += q
        tape_delta_5s = taker_buy_5s - taker_sell_5s
        tape_total_5s = taker_buy_5s + taker_sell_5s

        # 5. Variacao de preco e liquidez Top-5 nos ultimos 5s (para Absorcao vs Spoofing vs Sweep)
        old_entry = None
        for entry in reversed(self.depth5_hist):
            old_entry = entry
            if entry[0] <= cutoff_5s:
                break

        d_bid_px_bps = 0.0
        d_q_bid5 = 0.0
        d_q_ask5 = 0.0
        if old_entry is not None:
            _, ob, oa, oqb5, oqa5 = old_entry
            d_bid_px_bps = ((best_b - ob) / ob) * 1e4 if ob > 0 else 0.0
            d_q_bid5 = sum_b5 - oqb5
            d_q_ask5 = sum_a5 - oqa5

        # Maior parede no Top-20 (em BTC e distancia em bps do mid)
        max_bid_px, max_bid_q = max(b_spot, key=lambda x: x[1])
        max_ask_px, max_ask_q = max(a_spot, key=lambda x: x[1])
        bid_wall_dist_bps = ((mid - max_bid_px) / mid) * 1e4
        ask_wall_dist_bps = ((max_ask_px - mid) / mid) * 1e4

        # 6. Classificador inteligente Book vs Tape (Reversao por Absorcao vs Continuacao por Sweep/Spoof)
        regime = "NEUTRO"
        shadow_signal = "WAIT"

        # (A) ABSORCAO -> REVERSAO:
        # Forte agressao vendedora no tape, preco segurou (nao caiu > 0.2bp) e liquidez no Bid se manteve/cresceu
        if taker_sell_5s >= 0.8 and tape_delta_5s < -0.4 and d_bid_px_bps >= -0.2 and d_q_bid5 >= -0.2 * taker_sell_5s and imb5_spot > 0.15:
            regime = "ABSORCAO_REVERSAO"
            shadow_signal = "CALL"
        elif taker_buy_5s >= 0.8 and tape_delta_5s > 0.4 and d_bid_px_bps <= 0.2 and d_q_ask5 >= -0.2 * taker_buy_5s and imb5_spot < -0.15:
            regime = "ABSORCAO_REVERSAO"
            shadow_signal = "PUT"
        # (B) SPOOFING -> CONTINUACAO contra a parede falsa que sumiu sem ser executada:
        elif d_q_bid5 < -3.0 and taker_sell_5s < 0.3 * abs(d_q_bid5):
            regime = "SPOOF_CONTINUACAO"
            shadow_signal = "PUT"
        elif d_q_ask5 < -3.0 and taker_buy_5s < 0.3 * abs(d_q_ask5):
            regime = "SPOOF_CONTINUACAO"
            shadow_signal = "CALL"
        # (C) SWEEP / VARREDURA -> CONTINUACAO: fluxo agressor forte + OFI Perp e Spot alinhados + deslocamento de preco
        elif tape_delta_5s > 0.8 and ofi_s_5s > 1.0 and ofi_p_5s > 2.0 and d_bid_px_bps > 0.3:
            regime = "SWEEP_CONTINUACAO"
            shadow_signal = "CALL"
        elif tape_delta_5s < -0.8 and ofi_s_5s < -1.0 and ofi_p_5s < -2.0 and d_bid_px_bps < -0.3:
            regime = "SWEEP_CONTINUACAO"
            shadow_signal = "PUT"
        # (D) Pressao pura de OFI + Imbalance extremo concordando Spot e Perp
        elif imb5_spot > 0.45 and imb5_perp > 0.35 and ofi_p_5s > 1.0:
            regime = "OFI_IMBALANCE"
            shadow_signal = "CALL"
        elif imb5_spot < -0.45 and imb5_perp < -0.35 and ofi_p_5s < -1.0:
            regime = "OFI_IMBALANCE"
            shadow_signal = "PUT"

        return {
            "ready": True,
            "age_spot_ms": int((now - sb["updated"]) * 1000),
            "age_perp_ms": int((now - pb["updated"]) * 1000) if pb["updated"] > 0 else None,
            "imb5_spot": round(imb5_spot, 3),
            "imb20_spot": round(imb20_spot, 3),
            "imb5_perp": round(imb5_perp, 3),
            "micro_skew_bps": round(micro_skew_bps, 4),
            "ofi_spot_1s": round(ofi_s_1s, 3),
            "ofi_spot_5s": round(ofi_s_5s, 3),
            "ofi_spot_15s": round(ofi_s_15s, 3),
            "ofi_perp_5s": round(ofi_p_5s, 3),
            "tape_buy_5s_btc": round(taker_buy_5s, 3),
            "tape_sell_5s_btc": round(taker_sell_5s, 3),
            "tape_delta_5s_btc": round(tape_delta_5s, 3),
            "d_bid_px_5s_bps": round(d_bid_px_bps, 3),
            "d_q_bid5_btc": round(d_q_bid5, 3),
            "d_q_ask5_btc": round(d_q_ask5, 3),
            "max_bid_wall": {"price": max_bid_px, "qty_btc": round(max_bid_q, 2), "dist_bps": round(bid_wall_dist_bps, 2)},
            "max_ask_wall": {"price": max_ask_px, "qty_btc": round(max_ask_q, 2), "dist_bps": round(ask_wall_dist_bps, 2)},
            "regime": regime,
            "shadow_signal": shadow_signal,
            "shadow_audit": self.shadow_audit,
        }

    def record_minute_snapshot(self, minute_open_ms: int, stage: str = "0s"):
        """Congela snapshot L2 no nascimento (+0s) ou +5s para auditoria shadow e gravacao em disco."""
        snap = self.get_snapshot()
        if not snap.get("ready"):
            return
        entry = self._pending_shadow.setdefault(minute_open_ms, {"minute_open_ms": minute_open_ms})
        entry[f"l2_{stage}"] = {k: v for k, v in snap.items() if k != "shadow_audit"}
        if len(self._pending_shadow) > 20:
            oldest = min(self._pending_shadow.keys())
            self._pending_shadow.pop(oldest, None)

    def finalize_closed_candle(self, closed_candle: Dict[str, Any]):
        """Audita o sinal shadow L2 contra o resultado real da vela fechada e persiste em JSONL."""
        open_ts = int(closed_candle.get("open_time", 0))
        rec = self._pending_shadow.pop(open_ts, None)
        if rec is None:
            return

        if closed_candle["close"] > closed_candle["open"]:
            actual = "CALL"
        elif closed_candle["close"] < closed_candle["open"]:
            actual = "PUT"
        else:
            actual = "DOJI"

        rec["open"] = closed_candle["open"]
        rec["close"] = closed_candle["close"]
        rec["actual"] = actual
        rec["ret_bps"] = round(((closed_candle["close"] - closed_candle["open"]) / closed_candle["open"]) * 1e4, 3)

        # Atualiza placar shadow (usa leitura +0s se houver sinal; senao +5s)
        l2_0 = rec.get("l2_0s", {})
        l2_5 = rec.get("l2_5s", {})
        chosen = l2_0 if l2_0.get("shadow_signal") in ("CALL", "PUT") else l2_5
        sig = chosen.get("shadow_signal", "WAIT")
        reg = chosen.get("regime", "NEUTRO")

        if sig in ("CALL", "PUT"):
            is_win = (sig == actual)
            sa = self.shadow_audit
            sa["total"] += 1
            if actual == "DOJI":
                sa["dojis"] += 1
            if is_win:
                sa["wins"] += 1
            else:
                sa["losses"] += 1
            sa["winrate"] = round((sa["wins"] / sa["total"]) * 100, 1)

            if reg in sa["by_regime"]:
                rb = sa["by_regime"][reg]
                rb["total"] += 1
                if is_win:
                    rb["wins"] += 1
                rb["winrate"] = round((rb["wins"] / rb["total"]) * 100, 1)

            log.info("📊 [L2 SHADOW %s] Vela %s -> %s vs %s (%s) | Shadow WR=%s%% (%dW/%dL)",
                     reg, open_ts, sig, actual, "WIN" if is_win else "LOSS",
                     sa["winrate"], sa["wins"], sa["losses"])

        # Persiste em JSONL para analise quantitativa futura fora da amostra
        try:
            with self.audit_log_path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(rec) + "\n")
        except Exception as e:
            log.warning("Falha ao gravar L2 JSONL: %s", e)

    async def start(self):
        self._running = True
        log.info("Iniciando OrderBookEngine L2 (@depth20@100ms Spot+Perp + @aggTrade Spot)...")
        await asyncio.gather(
            self._ws_spot_l2_and_tape(),
            self._ws_perp_l2(),
        )

    def stop(self):
        self._running = False

    async def _ws_spot_l2_and_tape(self):
        url = f"wss://stream.binance.com:9443/stream?streams={self.symbol_lower}@depth20@100ms/{self.symbol_lower}@aggTrade"
        while self._running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(url, timeout=15.0, heartbeat=15.0) as ws:
                        log.info("Conectado ao L2 Depth20 + AggTrade Spot (%s)!", self.symbol)
                        async for msg in ws:
                            if not self._running:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                d = json.loads(msg.data)
                                stream = d.get("stream", "")
                                p = d.get("data", {})
                                if "depth20" in stream:
                                    self._on_depth("spot", p.get("bids", []), p.get("asks", []))
                                elif "aggTrade" in stream:
                                    self._on_agg_trade_spot(float(p["p"]), float(p["q"]), bool(p["m"]))
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except Exception as e:
                log.warning("OrderBookEngine Spot reconectando: %s", e)
                await asyncio.sleep(2)

    async def _ws_perp_l2(self):
        url = f"wss://fstream.binance.com/ws/{self.symbol_lower}@depth20@100ms"
        while self._running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(url, timeout=15.0, heartbeat=15.0) as ws:
                        log.info("Conectado ao L2 Depth20 Perp (%s)!", self.symbol)
                        async for msg in ws:
                            if not self._running:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                p = json.loads(msg.data)
                                self._on_depth("perp", p.get("b", []), p.get("a", []))
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except Exception as e:
                log.warning("OrderBookEngine Perp reconectando: %s", e)
                await asyncio.sleep(2)
