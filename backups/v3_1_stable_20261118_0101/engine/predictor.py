"""
predictor.py (v3.1 - Modo Dual Hibrido Travado: Produto A +0s | Produto B +5s)
Fluxo operacional solicitado pelo usuario, mantendo auditoria separada e honesta:
  1. Em t = open_t + 0s: avalia Produto A (REF_basis_z60_Q80 via BBO Spot+Perp).
     - Se |z60| >= Q80 (~20% das velas): TRAVA CALL ou PUT imediatamente pelos 60s inteiros.
     - Senao (~80% das velas): fica em WAIT aguardando o segundo +5s.
  2. Em t = open_t + 5s: se a vela ainda esta em WAIT, avalia Produto B (Follow_5s via BBO).
     - Se houve definicao (|ret_5s| >= 0.05bp): TRAVA CALL ou PUT pelos 55s restantes.
     - Uma vez travado em CALL ou PUT, NUNCA mais muda ate o fechamento da vela.
  3. No fechamento da vela: se fechou na cor travada (CALL=verde, PUT=vermelha; DOJI=erro),
     computa WIN/LOSS no resumo principal E mantem contadores separados para Produto A (+0s)
     e Produto B (+5s) para transparencia total.
"""
import time
import logging
from typing import Dict, Any, List, Optional
from engine.basis_signal import compute_basis_signal, compute_product_b_signal

log = logging.getLogger("predictor")

PRODUCT_B_WAIT_SEC = 5


class RealtimePredictor:
    def __init__(self, primary_symbol: str = "BTCUSDT", product_b_wait_sec: int = PRODUCT_B_WAIT_SEC):
        self.primary_symbol = primary_symbol.upper()
        self.product_b_wait_sec = int(product_b_wait_sec)

        # Previsao Travada da Vela Atual (nunca retrocede open_time)
        self.locked_prediction: Optional[Dict[str, Any]] = None
        self.locked_candle_open_time: int = 0
        self._locked_is_auditable: bool = False
        self._audited_open_times: set[int] = set()
        # Fila de previsoes travadas por open_time para auditoria 100% imune a race condition
        # entre o gatilho BBO em :00.005s e a chegada de k.x=True do @kline_1m
        self._pending_audits: Dict[int, Dict[str, Any]] = {}

        # Estatisticas acumuladas (somente sinais travados CALL/PUT)
        self.audit_summary: Dict[str, Any] = {
            "total": 0,
            "wins": 0,
            "losses": 0,
            "dojis": 0,
            "winrate": 0.0,
            "recent_results": []
        }

        # Auditoria separada por produto (nunca oculta a origem do acerto)
        self.audit_by_product: Dict[str, Dict[str, Any]] = {
            "product_a_0s": {"total": 0, "wins": 0, "losses": 0, "dojis": 0, "winrate": 0.0},
            "product_b_5s": {"total": 0, "wins": 0, "losses": 0, "dojis": 0, "winrate": 0.0},
        }

    def _record_audit_bucket(self, bucket: Dict[str, Any], is_win: bool, is_doji: bool):
        bucket["total"] += 1
        if is_doji:
            bucket["dojis"] = bucket.get("dojis", 0) + 1
        if is_win:
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1
        bucket["winrate"] = round((bucket["wins"] / bucket["total"]) * 100, 1)

    def _register_pending_audit(self, open_ts: int, direction: str, product: str):
        """Registra previsao travada (CALL/PUT) para ser auditada assim que a vela fechar."""
        if open_ts <= 0 or direction not in ("CALL", "PUT") or open_ts in self._audited_open_times:
            return
        self._pending_audits[open_ts] = {"direction": direction, "product": product}
        # Mantem fila limpa (ultimos 15 minutos)
        if len(self._pending_audits) > 15:
            oldest = min(self._pending_audits.keys())
            self._pending_audits.pop(oldest, None)

    def audit_closed_candle(self, closed_candle: Dict[str, Any]):
        """Audita a vela recem-fechada contra o registro travado dela (imune a atraso de kline)."""
        open_ts = int(closed_candle.get("open_time", 0))
        if open_ts <= 0 or open_ts in self._audited_open_times:
            return

        pending = self._pending_audits.pop(open_ts, None)
        if pending is None:
            return

        self._audited_open_times.add(open_ts)
        if len(self._audited_open_times) > 200:
            self._audited_open_times = set(sorted(self._audited_open_times)[-100:])

        pred_dir = pending["direction"]
        if closed_candle["close"] > closed_candle["open"]:
            actual_dir = "CALL"
        elif closed_candle["close"] < closed_candle["open"]:
            actual_dir = "PUT"
        else:
            actual_dir = "DOJI"

        is_doji = (actual_dir == "DOJI")
        is_win = (pred_dir == actual_dir)

        self._record_audit_bucket(self.audit_summary, is_win, is_doji)
        prod_key = "product_a_0s" if pending["product"] == "A" else "product_b_5s"
        self._record_audit_bucket(self.audit_by_product[prod_key], is_win, is_doji)

        time_str = time.strftime("%H:%M", time.gmtime(open_ts / 1000))
        tag = "+0s" if prod_key == "product_a_0s" else f"+{self.product_b_wait_sec}s"
        self.audit_summary["recent_results"].append({
            "time": f"{time_str} {tag}",
            "predicted": pred_dir,
            "actual": actual_dir,
            "result": "WIN" if is_win else "LOSS"
        })
        if len(self.audit_summary["recent_results"]) > 20:
            self.audit_summary["recent_results"].pop(0)

        log.info("✅ [%s] Vela %s fechou (%s). %s | Placar=%dW/%dL (%s%%) [A=%s%%, B=%s%%]",
                 prod_key, time_str, actual_dir, "WIN" if is_win else "LOSS",
                 self.audit_summary["wins"], self.audit_summary["losses"],
                 self.audit_summary["winrate"],
                 self.audit_by_product["product_a_0s"]["winrate"],
                 self.audit_by_product["product_b_5s"]["winrate"])

    def lock_at_minute_open(
        self,
        minute_open_ms: int,
        spot_history: List[Dict[str, Any]],
        perp_history: List[Dict[str, Any]],
        fast_snapshot: Optional[Dict[str, Any]] = None,
        auditable: bool = True,
    ) -> Dict[str, Any]:
        """
        Passo 1 (open_t + 0s): avalia Produto A (REF_basis_z60_Q80 via BBO).
        Se disparar CALL/PUT -> TRAVA imediatamente pelos 60s.
        Se retornar WAIT -> aguarda o passo 2 (+5s).
        NUNCA retrocede para um open_time anterior/igual.
        """
        if minute_open_ms <= 0 or minute_open_ms <= self.locked_candle_open_time:
            return self.locked_prediction or {}

        # Audita qualquer vela fechada pendente no historico
        if spot_history and self._pending_audits:
            for c in spot_history[-5:]:
                self.audit_closed_candle(c)

        causal_spot = [c for c in spot_history if int(c.get("close_time", 0)) < minute_open_ms]
        causal_perp = [c for c in perp_history if int(c.get("close_time", 0)) < minute_open_ms]
        analysis = compute_basis_signal(causal_spot, causal_perp, instant_bbo=fast_snapshot)

        is_locked_now = analysis["direction"] in ("CALL", "PUT")
        pred_color = (
            f"{analysis['predicted_color']} (TRAVADO +0s)"
            if is_locked_now
            else f"ANALISANDO (+{self.product_b_wait_sec}s)..."
        )

        self.locked_candle_open_time = minute_open_ms
        self._locked_is_auditable = auditable
        self.locked_prediction = {
            "direction": analysis["direction"],
            "predicted_color": pred_color,
            "confidence_pct": analysis["confidence_pct"],
            "product": "A" if is_locked_now else "PENDING",
            "source": analysis.get("source", "BBO"),
            "bias_summary": analysis["bias_summary"],
            "flow": analysis["flow"],
            "indicators": analysis["indicators"],
            "candle_open_time": minute_open_ms,
            "locked_at": time.time(),
            "locked_offset_ms": round((fast_snapshot["server_now_ms"] - minute_open_ms), 1) if fast_snapshot else 0.0,
        }
        if is_locked_now and auditable:
            self._register_pending_audit(minute_open_ms, analysis["direction"], "A")

        log.info("⚡ [Passo 1 +0s] Vela %s: %s (%s)",
                 minute_open_ms, self.locked_prediction["predicted_color"], self.locked_prediction["direction"])
        return self.locked_prediction

    def lock_product_b_if_waiting(self, minute_open_ms: int, fast_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Passo 2 (open_t + 5s): se a vela ainda estiver em WAIT (Produto A nao travou em +0s),
        avalia o movimento real dos primeiros 5s (Produto B) e TRAVA definitivamente pelos 55s restantes.
        Se ja estava travada em CALL/PUT pelo Produto A, NAO altera nada!
        """
        if not self.locked_prediction or self.locked_candle_open_time != minute_open_ms:
            return self.locked_prediction or {}
        if self.locked_prediction.get("direction") in ("CALL", "PUT"):
            return self.locked_prediction  # ja travado em +0s — imutavel!

        res_b = compute_product_b_signal(fast_snapshot, wait_sec=self.product_b_wait_sec)
        direction = res_b["direction"]
        alta_pts = 1 if direction == "CALL" else 0
        baixa_pts = 1 if direction == "PUT" else 0
        neutro_pts = 1 if direction == "WAIT" else 0

        self.locked_prediction.update({
            "direction": direction,
            "predicted_color": res_b["predicted_color"],
            "confidence_pct": res_b["confidence_pct"],
            "product": "B",
            "source": f"BBO_+{self.product_b_wait_sec}S",
            "bias_summary": {"alta": alta_pts, "neutro": neutro_pts, "baixa": baixa_pts},
            "flow": {
                **self.locked_prediction.get("flow", {}),
                "flow_bias": res_b["flow_bias"],
            },
            "locked_at": time.time(),
            "locked_offset_ms": round((fast_snapshot["server_now_ms"] - minute_open_ms), 1),
        })
        if direction in ("CALL", "PUT"):
            self._register_pending_audit(minute_open_ms, direction, "B")

        log.info("🔒 [Passo 2 +%ds] TRAVADO vela %s: %s (%s) ret=%+.2fbp",
                 self.product_b_wait_sec, minute_open_ms,
                 self.locked_prediction["predicted_color"], direction, res_b["intra_ret_bps"])
        return self.locked_prediction

    def update_with_candle(
        self,
        current_candle: Dict[str, Any],
        history_candles: List[Dict[str, Any]],
        perp_history_candles: Optional[List[Dict[str, Any]]] = None,
        fast_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        candle_open_time = int(current_candle.get("open_time", 0))
        perp_hist = perp_history_candles or []

        # So avanca se o open_time for ESTRITAMENTE MAIOR que o travado (nunca retrocede!)
        if candle_open_time > self.locked_candle_open_time:
            was_init = self.locked_prediction is not None
            return self.lock_at_minute_open(
                candle_open_time, history_candles, perp_hist, fast_snapshot, auditable=was_init
            )

        if self.locked_prediction is None and history_candles:
            open_ms = candle_open_time or (int(fast_snapshot["minute_open_ms"]) if fast_snapshot else int(time.time() * 1000))
            return self.lock_at_minute_open(
                open_ms, history_candles, perp_hist, fast_snapshot, auditable=False
            )

        return self.locked_prediction or {}

    def generate_live_state(
        self,
        current_candle: Dict[str, Any],
        history_candles: List[Dict[str, Any]],
        perp_history_candles: Optional[List[Dict[str, Any]]] = None,
        fast_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = time.time()
        pred = self.update_with_candle(current_candle, history_candles, perp_history_candles, fast_snapshot)

        if fast_snapshot and fast_snapshot.get("server_now_ms"):
            now_ms = float(fast_snapshot["server_now_ms"])
            minute_close_ms = (int(now_ms // 60_000) + 1) * 60_000
            seconds_left = max(0, min(59, int((minute_close_ms - now_ms) / 1000.0)))
            cur_price = fast_snapshot.get("binance_spot_mid") or current_candle.get("close", 0.0)
        else:
            now_ms = int(now * 1000)
            close_time = current_candle.get("close_time", now_ms + 60000)
            seconds_left = max(0, int((close_time - now_ms) / 1000))
            if seconds_left > 60:
                seconds_left = seconds_left % 60
            cur_price = current_candle.get("close", 0.0)

        return {
            "symbol": self.primary_symbol,
            "current_price": cur_price,
            "seconds_left": seconds_left,
            "prediction": {
                "direction": pred.get("direction", "WAIT"),
                "predicted_color": pred.get("predicted_color", f"ANALISANDO (+{self.product_b_wait_sec}s)..."),
                "confidence_pct": pred.get("confidence_pct", 50),
                "product": pred.get("product", "PENDING"),
                "source": pred.get("source", "BBO"),
                "locked_offset_ms": pred.get("locked_offset_ms"),
            },
            "bias_summary": pred.get("bias_summary", {"alta": 0, "neutro": 1, "baixa": 0}),
            "flow": pred.get("flow", {}),
            "indicators": pred.get("indicators", {}),
            "audit_summary": self.audit_summary,
            "audit_by_product": self.audit_by_product,
            "fast_bbo": fast_snapshot,
            "timestamp": now,
        }
