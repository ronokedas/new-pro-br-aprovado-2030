"""
app/radar/engine/predictor.py
=============================
v4.0 - Modo Dual Híbrido Travado + Auditoria por PREÇO DE ENTRADA REAL + Confluências M5/15s

Fluxo operacional:
  1. open_t + 0s : Produto A (REF_basis_z60_Q80 via BBO Spot+Perp) + veto L2 + confluências. Se disparar, TRAVA CALL/PUT.
  2. open_t + 5s : se ainda WAIT, Produto B (Follow_5s / SNR_5s >= 0.50 + veto L2 + confluências). Se disparar, TRAVA.
  3. Uma vez travado, NUNCA muda até o fechamento da vela.

Auditoria Científica e Anti-Repaint:
  * WIN/LOSS é medido pelo PREÇO DE ENTRADA REAL (close vs lock_price). A métrica antiga (cor open->close)
    continua exibida lado a lado como `color_winrate`, demonstrando a diferença real de slippage/deslocamento.
  * Placar AO VIVO separado do backtest de 120 min. O ao-vivo é persistido em audit_data/live_signals.jsonl
    e recarregado automaticamente na inicialização.
  * Somente o scheduler trava a vela (o loop de broadcast a 20Hz é somente-leitura, eliminando race condition).
  * Camadas de confluência causais (app.radar.engine.context): M5 (regime/volatilidade) e 15s (microestrutura).
"""
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.radar.engine.basis_signal import (
    compute_basis_signal,
    compute_product_b_signal,
    compute_live_signal2,
)
from app.radar.engine.context import m5_context, micro15, verdict
from app.radar.engine.indicators import compute_all_indicators
from app.radar.engine.sr_absorption import detect_sr_absorption

log = logging.getLogger("radar.predictor")

PRODUCT_B_WAIT_SEC = 5
BASE_DIR = Path(__file__).resolve().parent.parent
LIVE_LOG = BASE_DIR / "audit_data" / "live_signals.jsonl"

DEFAULT_CFG: Dict[str, Dict[str, Any]] = {
    "m5": {"mode": "shadow", "trend_min_bps": 0.8},
    "micro15": {"mode": "shadow", "thr": 0.25},
}

DEFAULT_TRIGGERS_CFG: Dict[str, bool] = {
    "enableProductA": True,
    "enableProductB": True,
    "enableL2Veto": True,
    "enableM5Filter": False,
    "enableMicro15s": False,
    "enableInstitutional10": False,
    "enableAbsorptionSR": False,
}


def load_confluence_cfg() -> Dict[str, Dict[str, Any]]:
    cfg = {k: dict(v) for k, v in DEFAULT_CFG.items()}
    try:
        import yaml
        p = BASE_DIR / "config" / "settings.yaml"
        if p.exists():
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            for layer, vals in (data.get("confluence") or {}).items():
                if layer in cfg and isinstance(vals, dict):
                    cfg[layer].update(vals)
    except Exception as e:
        log.warning("Config de confluência inválida (%s) - usando padrão", e)
    return cfg


def _new_bucket() -> Dict[str, Any]:
    return {
        "total": 0,
        "wins": 0,
        "losses": 0,
        "dojis": 0,
        "winrate": 0.0,
        "color_wins": 0,
        "color_winrate": 0.0,
    }


def _bump(bucket: Dict[str, Any], win_entry: bool, doji_entry: bool, win_color: bool) -> None:
    bucket["total"] += 1
    if doji_entry:
        bucket["dojis"] = bucket.get("dojis", 0) + 1
    if win_entry:
        bucket["wins"] += 1
    else:
        bucket["losses"] = bucket.get("losses", 0) + 1
    if win_color:
        bucket["color_wins"] = bucket.get("color_wins", 0) + 1
    bucket["winrate"] = round(bucket["wins"] / bucket["total"] * 100, 1)
    bucket["color_winrate"] = round(bucket.get("color_wins", 0) / bucket["total"] * 100, 1)


class RealtimePredictor:
    def __init__(
        self,
        primary_symbol: str = "BTCUSDT",
        product_b_wait_sec: int = PRODUCT_B_WAIT_SEC,
        live_log_path: Optional[Path] = None,
        cfg: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        self.primary_symbol = primary_symbol.upper()
        self.product_b_wait_sec = int(product_b_wait_sec)
        self.cfg = cfg or load_confluence_cfg()
        self.triggers_cfg: Dict[str, bool] = dict(DEFAULT_TRIGGERS_CFG)
        self.c15_provider: Optional[Callable[[float], List[Dict[str, Any]]]] = None

        self.locked_prediction: Optional[Dict[str, Any]] = None
        self.locked_candle_open_time: int = 0
        self._locked_is_auditable: bool = False
        self._audited_open_times: set = set()
        self._pending_audits: Dict[int, Dict[str, Any]] = {}
        self._pending_cf: Dict[int, List[Dict[str, Any]]] = {}

        # Placar AO VIVO (entrada real). Persistido em jsonl.
        self.audit_summary: Dict[str, Any] = {**_new_bucket(), "recent_results": []}
        self.audit_by_product: Dict[str, Dict[str, Any]] = {
            "product_a_0s": _new_bucket(),
            "product_b_5s": _new_bucket(),
        }
        self.confluence_stats: Dict[str, Dict[str, Any]] = {}
        self.veto_stats: Dict[str, Dict[str, Any]] = {}
        self.audit_baseline: Dict[str, Any] = {}

        self.live_log_path = Path(live_log_path) if live_log_path else LIVE_LOG
        self._load_history()

    def _load_history(self, max_age_days: float = 7.0) -> None:
        """Recarrega o placar ao vivo dos últimos dias do jsonl."""
        try:
            if not self.live_log_path.exists():
                return
            cutoff = (time.time() - max_age_days * 86400) * 1000
            n = 0
            with self.live_log_path.open("r", encoding="utf-8") as fp:
                for line in fp:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get("open_time", 0) < cutoff:
                        continue
                    self._apply_record(rec, push_recent=True)
                    n += 1
            if n:
                log.info("Placar ao vivo recarregado de %s: %d registros", self.live_log_path.name, n)
        except Exception as e:
            log.warning("Falha ao recarregar histórico ao vivo: %s", e)

    def _append_log(self, rec: Dict[str, Any]) -> None:
        try:
            self.live_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.live_log_path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            log.warning("Falha ao gravar live_signals.jsonl: %s", e)

    def _apply_record(self, rec: Dict[str, Any], push_recent: bool = True) -> None:
        prod = rec.get("product", "A")
        win_e, win_c = bool(rec.get("win_entry")), bool(rec.get("win_color"))
        doji_e = bool(rec.get("doji_entry"))
        if rec.get("vetoed"):
            key = f"{prod}|{rec.get('vetoed_by', '?')}"
            _bump(self.veto_stats.setdefault(key, _new_bucket()), win_e, doji_e, win_c)
            return
        _bump(self.audit_summary, win_e, doji_e, win_c)
        pk = "product_a_0s" if prod == "A" else "product_b_5s"
        _bump(self.audit_by_product[pk], win_e, doji_e, win_c)
        for layer, v in (rec.get("verdicts") or {}).items():
            _bump(self.confluence_stats.setdefault(f"{prod}|{layer}_{v}", _new_bucket()), win_e, doji_e, win_c)
        if push_recent:
            tag = "+0s" if prod == "A" else f"+{self.product_b_wait_sec}s"
            t = time.strftime("%H:%M", time.gmtime(int(rec.get("open_time", 0)) / 1000))
            self.audit_summary["recent_results"].append({
                "time": f"{t} {tag}",
                "predicted": rec.get("direction"),
                "actual": rec.get("entry_dir"),
                "result": "WIN" if win_e else "LOSS",
                "color_result": "WIN" if win_c else "LOSS",
            })
            if len(self.audit_summary["recent_results"]) > 20:
                self.audit_summary["recent_results"].pop(0)

    def set_baseline(self, audit_res: Dict[str, Any]) -> None:
        """Guarda o backtest 120m SEPARADO do placar ao vivo."""
        self.audit_baseline = {
            "total": audit_res.get("total_signals", 0),
            "wins": audit_res.get("wins", 0),
            "losses": audit_res.get("losses", 0),
            "winrate": audit_res.get("winrate_pct", 0.0),
            "color_winrate": audit_res.get("color_winrate_pct", 0.0),
            "coverage_pct": audit_res.get("coverage_pct", 0.0),
            "product_a_0s": audit_res.get("product_a_0s"),
            "product_b_5s": audit_res.get("product_b_5s"),
            "note": "backtest causal das últimas 120 velas (referência histórica)",
        }

    def update_triggers_config(self, new_cfg: Dict[str, Any]) -> Dict[str, bool]:
        """Atualiza dinamicamente as preferências de gatilhos e sincroniza camadas."""
        for k in self.triggers_cfg.keys():
            if k in new_cfg:
                self.triggers_cfg[k] = bool(new_cfg[k])

        # Sincroniza modos em self.cfg para M5 e micro15
        if self.triggers_cfg.get("enableM5Filter"):
            self.cfg.setdefault("m5", {})["mode"] = "veto"
        else:
            self.cfg.setdefault("m5", {})["mode"] = "shadow"

        if self.triggers_cfg.get("enableMicro15s"):
            self.cfg.setdefault("micro15", {})["mode"] = "veto"
        else:
            self.cfg.setdefault("micro15", {})["mode"] = "shadow"

        log.info("🎯 Configurações de gatilhos atualizadas no previsor: %s", self.triggers_cfg)
        return dict(self.triggers_cfg)

    def reset_to_default_triggers(self) -> Dict[str, bool]:
        """Restaura o previsor para a estratégia padrão pura e validada cientificamente (Base Intacta)."""
        self.triggers_cfg = dict(DEFAULT_TRIGGERS_CFG)
        self.cfg.setdefault("m5", {})["mode"] = "shadow"
        self.cfg.setdefault("micro15", {})["mode"] = "shadow"
        log.info("🔄 Previsor restaurado para a estratégia padrão comprovada (Base Intacta): %s", self.triggers_cfg)
        return dict(self.triggers_cfg)

    def _register_pending_audit(
        self,
        open_ts: int,
        direction: str,
        product: str,
        lock_price: Optional[float],
        verdicts: Dict[str, str],
        extra: Dict[str, Any],
    ):
        if open_ts <= 0 or direction not in ("CALL", "PUT") or open_ts in self._audited_open_times:
            return
        self._pending_audits[open_ts] = {
            "direction": direction,
            "product": product,
            "lock_price": lock_price,
            "verdicts": verdicts,
            "extra": extra,
        }
        if len(self._pending_audits) > 15:
            self._pending_audits.pop(min(self._pending_audits.keys()), None)

    def _register_cf(
        self,
        open_ts: int,
        direction: str,
        product: str,
        lock_price: Optional[float],
        verdicts: Dict[str, str],
        vetoed_by: str,
        extra: Dict[str, Any],
    ):
        if open_ts <= 0 or direction not in ("CALL", "PUT"):
            return
        self._pending_cf.setdefault(open_ts, []).append({
            "direction": direction,
            "product": product,
            "lock_price": lock_price,
            "verdicts": verdicts,
            "vetoed_by": vetoed_by,
            "extra": extra,
        })
        if len(self._pending_cf) > 15:
            self._pending_cf.pop(min(self._pending_cf.keys()), None)

    @staticmethod
    def _outcome(closed: Dict[str, Any], lock_price: Optional[float], direction: str):
        o, c = float(closed["open"]), float(closed["close"])
        color = "CALL" if c > o else ("PUT" if c < o else "DOJI")
        ref = float(lock_price) if lock_price and float(lock_price) > 0 else o
        entry = "CALL" if c > ref else ("PUT" if c < ref else "DOJI")
        return color, entry, direction == color, direction == entry

    def audit_closed_candle(self, closed_candle: Dict[str, Any]):
        """Audita a vela fechada contra o registro travado dela."""
        open_ts = int(closed_candle.get("open_time", 0))
        if open_ts <= 0:
            return
        cfs = self._pending_cf.pop(open_ts, None) or []
        pending = None
        if open_ts not in self._audited_open_times:
            pending = self._pending_audits.pop(open_ts, None)
        if pending is None and not cfs:
            return

        entries = []
        if pending is not None:
            self._audited_open_times.add(open_ts)
            if len(self._audited_open_times) > 200:
                self._audited_open_times = set(sorted(self._audited_open_times)[-100:])
            entries.append((pending, False))
        entries.extend((cf, True) for cf in cfs)

        for p, is_cf in entries:
            color, entry, win_c, win_e = self._outcome(closed_candle, p.get("lock_price"), p["direction"])
            rec = {
                "open_time": open_ts,
                "product": p["product"],
                "direction": p["direction"],
                "lock_price": p.get("lock_price"),
                "open": closed_candle["open"],
                "close": closed_candle["close"],
                "color_dir": color,
                "entry_dir": entry,
                "win_entry": win_e,
                "win_color": win_c,
                "doji_entry": entry == "DOJI",
                "vetoed": is_cf,
                "vetoed_by": p.get("vetoed_by"),
                "verdicts": p.get("verdicts"),
                "ctx": p.get("extra"),
            }
            self._apply_record(rec, push_recent=True)
            self._append_log(rec)
            if not is_cf:
                log.info(
                    "✅ [%s] Vela %s fechou (cor=%s entrada=%s). %s | Ao vivo=%dW/%dL (%.1f%% entrada | %.1f%% cor) [A=%.1f%%, B=%.1f%%]",
                    p["product"],
                    open_ts,
                    color,
                    entry,
                    "WIN" if win_e else "LOSS",
                    self.audit_summary["wins"],
                    self.audit_summary["losses"],
                    self.audit_summary["winrate"],
                    self.audit_summary["color_winrate"],
                    self.audit_by_product["product_a_0s"]["winrate"],
                    self.audit_by_product["product_b_5s"]["winrate"],
                )
            else:
                log.info(
                    "🧪 [CF veto %s/%s] Vela %s: teria %s (entrada=%s)",
                    p["product"],
                    p.get("vetoed_by"),
                    open_ts,
                    "GANHADO" if win_e else "PERDIDO",
                    entry,
                )

    def audit_stale_pending(
        self,
        lookup: Callable[[int], Optional[Dict[str, Any]]],
        now_ms: float,
        grace_ms: int = 8000,
    ) -> None:
        """Rede de segurança: se o kline oficial atrasar, audita com a vela local congelada."""
        for ts in set(list(self._pending_audits.keys()) + list(self._pending_cf.keys())):
            age = now_ms - (ts + 60_000)
            if age <= grace_ms:
                continue
            c = lookup(ts)
            if c:
                self.audit_closed_candle(c)
            elif age > 180_000:
                self._pending_audits.pop(ts, None)
                self._pending_cf.pop(ts, None)

    def _build_context(self, minute_open_ms: int, causal_spot: List[Dict[str, Any]]) -> Dict[str, Any]:
        ctx: Dict[str, Any] = {"m5": {"ready": False}, "micro15": {"ready": False}}
        sigma = 4.0
        m5c = self.cfg["m5"]
        if m5c.get("mode", "off") != "off":
            try:
                m5 = m5_context(causal_spot, minute_open_ms, trend_min_bps=float(m5c.get("trend_min_bps", 0.8)))
                ctx["m5"] = m5
                if m5.get("ready") and m5.get("sigma_m1_bps", 0) > 0:
                    sigma = float(m5["sigma_m1_bps"])
            except Exception as e:
                log.warning("m5_context falhou: %s", e)
        mc = self.cfg["micro15"]
        if mc.get("mode", "off") != "off" and self.c15_provider is not None:
            try:
                ctx["micro15"] = micro15(
                    self.c15_provider(minute_open_ms + 1),
                    sigma_bps=sigma,
                    thr=float(mc.get("thr", 0.25)),
                )
            except Exception as e:
                log.warning("micro15 falhou: %s", e)
        return ctx

    @staticmethod
    def _verdicts(
        direction: str,
        ctx: Dict[str, Any],
        fast_snapshot: Optional[Dict[str, Any]] = None,
        include_perp: bool = False,
    ) -> Dict[str, str]:
        v: Dict[str, str] = {}
        m5 = ctx.get("m5", {})
        v["m5"] = verdict(direction, m5.get("bias", "NEUTRO")) if m5.get("ready") else "neutral"
        mi = ctx.get("micro15", {})
        v["m15s"] = verdict(direction, mi.get("direction", "NEUTRO")) if mi.get("ready") else "neutral"
        if include_perp and fast_snapshot:
            cons = fast_snapshot.get("multi_perp_consensus_bps")
            if cons is not None:
                ref = "CALL" if cons >= 0.3 else ("PUT" if cons <= -0.3 else "NEUTRO")
                v["perp"] = verdict(direction, ref)
        return v

    def _veto_layer(self, v: Dict[str, str]) -> Optional[str]:
        if self.cfg["m5"].get("mode") == "veto" and v.get("m5") == "disagree":
            return "M5"
        if self.cfg["micro15"].get("mode") == "veto" and v.get("m15s") == "disagree":
            return "15s"
        return None

    @staticmethod
    def _compact_ctx(ctx: Dict[str, Any]) -> Dict[str, Any]:
        m5, mi = ctx.get("m5", {}), ctx.get("micro15", {})
        return {
            "m5_bias": m5.get("bias"),
            "m5_trend_bps": m5.get("trend_bps"),
            "m5_pos": m5.get("pos_in_m5"),
            "m5_partial_bps": m5.get("partial_ret_bps"),
            "vol": m5.get("vol_regime"),
            "m15s_dir": mi.get("direction"),
            "m15s_score": mi.get("score"),
        }

    def lock_at_minute_open(
        self,
        minute_open_ms: int,
        spot_history: List[Dict[str, Any]],
        perp_history: List[Dict[str, Any]],
        fast_snapshot: Optional[Dict[str, Any]] = None,
        l2_snapshot: Optional[Dict[str, Any]] = None,
        auditable: bool = True,
    ) -> Dict[str, Any]:
        """
        Passo 1 (open_t + 0s): Produto A + veto L2 + camadas de confluência.
        NUNCA retrocede para um open_time anterior/igual.
        """
        if minute_open_ms <= 0 or minute_open_ms <= self.locked_candle_open_time:
            return self.locked_prediction or {}

        # Audita velas OFICIAIS pendentes
        if spot_history and (self._pending_audits or self._pending_cf):
            for c in spot_history[-5:]:
                if not c.get("local"):
                    self.audit_closed_candle(c)

        causal_spot = [c for c in spot_history if int(c.get("close_time", 0)) < minute_open_ms]
        causal_perp = [c for c in perp_history if int(c.get("close_time", 0)) < minute_open_ms]

        # 0. Avaliação dos 10 indicadores técnicos institucionais causais
        indicators_vote = compute_all_indicators(causal_spot[-200:]) if len(causal_spot) >= 20 else {}
        ind_summary = indicators_vote.get("summary", {})
        call_votes = int(ind_summary.get("call", 0))
        put_votes = int(ind_summary.get("put", 0))

        # 1. Absorção em Suporte/Resistência e Lote Solitário L2 (Reversão de Alta Confluência)
        abs_res = None
        if self.triggers_cfg.get("enableAbsorptionSR", True):
            abs_res = detect_sr_absorption(causal_spot, l2_snapshot=l2_snapshot, indicators_vote=indicators_vote)

        if abs_res and abs_res.get("detected"):
            direction = abs_res["direction"]
            pred_color_raw = abs_res["predicted_color"]
            conf_pct = abs_res["confidence_pct"]
            src_tag = abs_res["source"]
            flow_bias = abs_res["flow_bias"]
            alta_pts = 1 if direction == "CALL" else 0
            baixa_pts = 1 if direction == "PUT" else 0
            neutro_pts = 0
            analysis = {
                "direction": direction,
                "predicted_color": pred_color_raw,
                "confidence_pct": conf_pct,
                "source": src_tag,
                "basis_z60": 0.0,
                "basis_dev_bps": 0.0,
                "threshold_q80": 1.28,
                "bias_summary": {"alta": alta_pts, "neutro": neutro_pts, "baixa": baixa_pts},
                "flow": {"cvd_1m": 0.0, "cvd_5m": 0.0, "aggression_ratio": 1.0, "flow_bias": flow_bias},
                "indicators": {"price": causal_spot[-1]["close"] if causal_spot else 0.0},
                "product_type": "ABSORPTION",
            }
        elif self.triggers_cfg.get("enableProductA", True):
            analysis = compute_basis_signal(
                causal_spot,
                causal_perp,
                instant_bbo=fast_snapshot,
                minute_open_ms=minute_open_ms,
            )
            analysis["product_type"] = "A"
        else:
            analysis = {
                "direction": "WAIT",
                "predicted_color": "PRODUTO A DESATIVADO",
                "confidence_pct": 50,
                "source": "CFG",
                "bias_summary": {"alta": 0, "neutro": 1, "baixa": 0},
                "flow": {"cvd_1m": 0.0, "cvd_5m": 0.0, "aggression_ratio": 1.0, "flow_bias": "Produto A desativado"},
                "indicators": {},
                "product_type": "WAIT",
            }

        # 2. Veto L2 (absorção/spoofing forte contra o sinal) se ativado
        if self.triggers_cfg.get("enableL2Veto", True) and analysis["direction"] in ("CALL", "PUT") and l2_snapshot and l2_snapshot.get("ready"):
            l2_reg = l2_snapshot.get("regime", "NEUTRO")
            l2_sig = l2_snapshot.get("shadow_signal", "WAIT")
            if l2_sig in ("CALL", "PUT") and l2_sig != analysis["direction"] and l2_reg in ("ABSORCAO_REVERSAO", "SPOOF_CONTINUACAO"):
                analysis["direction"] = "WAIT"
                analysis["confidence_pct"] = 50
                analysis["bias_summary"] = {"alta": 0, "neutro": 1, "baixa": 0}
                analysis["flow"]["flow_bias"] = f"[VETO L2 +0s] {l2_reg} contra {analysis.get('product_type', 'sinal')}"

        # 3. FILTRO VEREDITO INSTITUCIONAL (10 INDICADORES) - REGRA ANTI-CONTRADIÇÃO ESTRITA
        if self.triggers_cfg.get("enableInstitutional10", True) and analysis["direction"] in ("CALL", "PUT"):
            if analysis["direction"] == "PUT" and call_votes > put_votes:
                log.info(
                    "⛔ [VETO INSTITUCIONAL] Veto de PUT na vela %s: Maioria em ALTA (%d Alta vs %d Baixa)",
                    minute_open_ms, call_votes, put_votes
                )
                analysis["direction"] = "WAIT"
                analysis["confidence_pct"] = 50
                analysis["bias_summary"] = {"alta": 0, "neutro": 1, "baixa": 0}
                analysis["predicted_color"] = f"VETADO: MAIORIA DOS INDICADORES EM ALTA ({call_votes}A vs {put_votes}B)"
                analysis["flow"]["flow_bias"] = f"[VETO INSTITUCIONAL] Bloqueado PUT contra {call_votes} indicadores de Alta"

            elif analysis["direction"] == "CALL" and put_votes > call_votes:
                log.info(
                    "⛔ [VETO INSTITUCIONAL] Veto de CALL na vela %s: Maioria em BAIXA (%d Baixa vs %d Alta)",
                    minute_open_ms, put_votes, call_votes
                )
                analysis["direction"] = "WAIT"
                analysis["confidence_pct"] = 50
                analysis["bias_summary"] = {"alta": 0, "neutro": 1, "baixa": 0}
                analysis["predicted_color"] = f"VETADO: MAIORIA DOS INDICADORES EM BAIXA ({put_votes}B vs {call_votes}A)"
                analysis["flow"]["flow_bias"] = f"[VETO INSTITUCIONAL] Bloqueado CALL contra {put_votes} indicadores de Baixa"

        lock_px = fast_snapshot.get("binance_spot_mid") if fast_snapshot else (causal_spot[-1]["close"] if causal_spot else None)
        ctx = self._build_context(minute_open_ms, causal_spot)
        verdicts: Dict[str, str] = {}
        cf_dir = None
        cf_layer = None
        if analysis["direction"] in ("CALL", "PUT"):
            verdicts = self._verdicts(analysis["direction"], ctx)
            cf_layer = self._veto_layer(verdicts)
            if cf_layer:
                cf_dir = analysis["direction"]
                analysis["direction"] = "WAIT"
                analysis["confidence_pct"] = 50
                analysis["bias_summary"] = {"alta": 0, "neutro": 1, "baixa": 0}
                analysis["flow"]["flow_bias"] = f"[VETO {cf_layer} +0s] contra o contexto"

            # Ajuste de confiança por volatilidade (baseado em evidência empírica 1m)
            vreg = ctx.get("m5", {}).get("vol_regime")
            if vreg == "BAIXA":
                analysis["confidence_pct"] = min(88, analysis["confidence_pct"] + 4)
            elif vreg == "ALTA":
                analysis["confidence_pct"] = max(52, analysis["confidence_pct"] - 6)

        is_locked_now = analysis["direction"] in ("CALL", "PUT")
        prod_tag = analysis.get("product_type", "A")
        if is_locked_now:
            if prod_tag == "ABSORPTION":
                pred_color = f"{analysis['predicted_color']}"
            else:
                pred_color = f"{analysis['predicted_color']} (+0s BASIS)"
        else:
            if not self.triggers_cfg.get("enableProductB", True):
                pred_color = "AGUARDANDO PRÓXIMA VELA"
            else:
                pred_color = f"ANALISANDO (+{self.product_b_wait_sec}s)..."

        self.locked_candle_open_time = minute_open_ms
        self._locked_is_auditable = auditable
        offset_ms = round((fast_snapshot["server_now_ms"] - minute_open_ms), 1) if fast_snapshot else 0.0
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
            "lock_price": lock_px if is_locked_now else None,
            "locked_at": time.time(),
            "locked_offset_ms": offset_ms,
            "context": self._compact_ctx(ctx),
            "verdicts": verdicts,
        }
        extra = {**self._compact_ctx(ctx), "offset_ms": offset_ms, "z60": analysis.get("basis_z60")}
        if is_locked_now and auditable:
            self._register_pending_audit(minute_open_ms, analysis["direction"], "A", lock_px, verdicts, extra)
        if cf_dir and auditable:
            self._register_cf(minute_open_ms, cf_dir, "A", lock_px, verdicts, cf_layer, extra)

        log.info(
            "⚡ [Passo 1 +0s] Vela %s: %s (%s) ctx=%s",
            minute_open_ms,
            self.locked_prediction["predicted_color"],
            self.locked_prediction["direction"],
            verdicts or "-",
        )
        return self.locked_prediction

    def lock_product_b_if_waiting(
        self,
        minute_open_ms: int,
        fast_snapshot: Dict[str, Any],
        spot_history: Optional[List[Dict[str, Any]]] = None,
        l2_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Passo 2 (open_t + 5s): se a vela ainda está em WAIT, avalia Produto B (SNR_5s + L2 + confluências).
        """
        if not self.locked_prediction or self.locked_candle_open_time != minute_open_ms:
            return self.locked_prediction or {}
        if self.locked_prediction.get("direction") in ("CALL", "PUT"):
            return self.locked_prediction  # já travado em +0s — imutável!

        if not self.triggers_cfg.get("enableProductB", True):
            log.info("ℹ️ Produto B (:05s) desativado nas preferências do usuário.")
            return self.locked_prediction

        causal_spot = [c for c in (spot_history or []) if int(c.get("close_time", 0)) < minute_open_ms]
        res_b = compute_product_b_signal(
            fast_snapshot,
            spot_candles=causal_spot,
            l2_snapshot=l2_snapshot,
            wait_sec=self.product_b_wait_sec,
            min_snr=0.50,
        )
        direction = res_b["direction"]
        ctx = self._build_context(minute_open_ms, causal_spot)
        verdicts: Dict[str, str] = {}
        cf_dir, cf_layer = None, None
        if direction in ("CALL", "PUT"):
            # Veto institucional anti-contradição também no Produto B
            if self.triggers_cfg.get("enableInstitutional10", True):
                _b_ind = compute_all_indicators(causal_spot[-200:]) if len(causal_spot) >= 20 else {}
                _b_sum = _b_ind.get("summary", {})
                _b_call = int(_b_sum.get("call", 0))
                _b_put = int(_b_sum.get("put", 0))
                if direction == "PUT" and _b_call > _b_put:
                    cf_dir = direction
                    direction = "WAIT"
                    res_b["predicted_color"] = f"VETADO: INDICADORES EM ALTA ({_b_call}A vs {_b_put}B)"
                    res_b["confidence_pct"] = 50
                    res_b["flow_bias"] = f"[VETO INSTITUCIONAL +{self.product_b_wait_sec}s] Contra {_b_call} Alta"
                elif direction == "CALL" and _b_put > _b_call:
                    cf_dir = direction
                    direction = "WAIT"
                    res_b["predicted_color"] = f"VETADO: INDICADORES EM BAIXA ({_b_put}B vs {_b_call}A)"
                    res_b["confidence_pct"] = 50
                    res_b["flow_bias"] = f"[VETO INSTITUCIONAL +{self.product_b_wait_sec}s] Contra {_b_put} Baixa"

        if direction in ("CALL", "PUT"):
            verdicts = self._verdicts(direction, ctx, fast_snapshot, include_perp=True)
            cf_layer = self._veto_layer(verdicts)
            if cf_layer:
                cf_dir = direction
                direction = "WAIT"
                res_b["predicted_color"] = f"VETADO PELO CONTEXTO ({cf_layer})"
                res_b["confidence_pct"] = 50
                res_b["flow_bias"] = f"[VETO {cf_layer} +{self.product_b_wait_sec}s] {res_b.get('flow_bias', '')}"

            # Se M5 discorda de B, a evidência empírica mostra aumento de acerto de 51% para 57%
            if verdicts.get("m5") == "disagree":
                res_b["confidence_pct"] = min(82, res_b["confidence_pct"] + 3)

        alta_pts = 1 if direction == "CALL" else 0
        baixa_pts = 1 if direction == "PUT" else 0
        neutro_pts = 1 if direction == "WAIT" else 0
        lock_px = fast_snapshot.get("binance_spot_mid")
        offset_ms = round((fast_snapshot["server_now_ms"] - minute_open_ms), 1)

        self.locked_prediction = {
            "direction": direction,
            "predicted_color": res_b["predicted_color"],
            "confidence_pct": res_b["confidence_pct"],
            "product": "B" if direction in ("CALL", "PUT") else "OUT",
            "source": "BBO_5s",
            "bias_summary": {"alta": alta_pts, "neutro": neutro_pts, "baixa": baixa_pts},
            "flow": {
                "cvd_1m": None,
                "cvd_5m": None,
                "aggression_ratio": None,
                "flow_bias": res_b.get("flow_bias", f"Follow_{self.product_b_wait_sec}s"),
            },
            "indicators": {
                "ema_cross": "NEUTRO",
                "cci_direction": "NEUTRO",
                "sr_proximity": "LONGE",
                "flow_pressure": "NEUTRO",
            },
            "candle_open_time": minute_open_ms,
            "lock_price": lock_px if direction in ("CALL", "PUT") else None,
            "locked_at": time.time(),
            "locked_offset_ms": offset_ms,
            "context": self._compact_ctx(ctx),
            "verdicts": verdicts,
        }
        extra = {
            **self._compact_ctx(ctx),
            "offset_ms": offset_ms,
            "intra_ret_bps": res_b.get("intra_ret_bps"),
            "snr_5s": res_b.get("snr_5s"),
        }
        if direction in ("CALL", "PUT") and self._locked_is_auditable:
            self._register_pending_audit(minute_open_ms, direction, "B", lock_px, verdicts, extra)
        if cf_dir and self._locked_is_auditable:
            self._register_cf(minute_open_ms, cf_dir, "B", lock_px, verdicts, cf_layer, extra)

        log.info(
            "⚡ [Passo 2 +5s] Vela %s: %s (%s) lock_px=%s ctx=%s ret_5s=%.2f bps snr=%.2f",
            minute_open_ms,
            res_b["predicted_color"],
            direction,
            lock_px,
            verdicts or "-",
            res_b.get("intra_ret_bps", 0.0),
            res_b.get("snr_5s", 0.0),
        )
        return self.locked_prediction

    def update_with_candle(
        self,
        current_candle: Dict[str, Any],
        history_candles: List[Dict[str, Any]],
        perp_history_candles: Optional[List[Dict[str, Any]]] = None,
        fast_snapshot: Optional[Dict[str, Any]] = None,
        l2_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        SOMENTE LEITURA do estado travado, exceto no cold start (sem nenhum lock ainda; não auditável).
        O travamento de cada minuto é exclusivo do minute_boundary_scheduler.
        """
        if self.locked_prediction is None and history_candles:
            candle_open_time = int(current_candle.get("open_time", 0)) if current_candle else 0
            open_ms = candle_open_time or (
                int(fast_snapshot["minute_open_ms"]) if fast_snapshot else int(time.time() * 1000)
            )
            return self.lock_at_minute_open(
                open_ms,
                history_candles,
                perp_history_candles or [],
                fast_snapshot,
                l2_snapshot,
                auditable=False,
            )
        return self.locked_prediction or {}

    def generate_live_state(
        self,
        current_candle: Dict[str, Any],
        history_candles: List[Dict[str, Any]],
        perp_history_candles: Optional[List[Dict[str, Any]]] = None,
        fast_snapshot: Optional[Dict[str, Any]] = None,
        l2_snapshot: Optional[Dict[str, Any]] = None,
        latency_telemetry: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = time.time()
        pred = self.update_with_candle(
            current_candle, history_candles, perp_history_candles, fast_snapshot, l2_snapshot
        )
        signal2 = compute_live_signal2(pred, fast_snapshot, l2_snapshot)

        if fast_snapshot and fast_snapshot.get("server_now_ms"):
            now_ms = float(fast_snapshot["server_now_ms"])
            minute_close_ms = (int(now_ms // 60_000) + 1) * 60_000
            seconds_left = max(0, min(59, int((minute_close_ms - now_ms) / 1000.0)))
            cur_price = fast_snapshot.get("binance_spot_mid") or (current_candle or {}).get("close", 0.0)
        else:
            now_ms = int(now * 1000)
            close_time = (current_candle or {}).get("close_time", now_ms + 60000)
            seconds_left = max(0, int((close_time - now_ms) / 1000))
            if seconds_left > 60:
                seconds_left = seconds_left % 60
            cur_price = (current_candle or {}).get("close", 0.0)

        # Calcula indicadores técnicos sobre buffer de velas fechadas (causalidade estrita)
        _hist = history_candles or []
        indicators_vote = compute_all_indicators(_hist[-200:]) if len(_hist) >= 20 else {}

        # Atualiza bias_summary com contagem real dos indicadores votantes
        _summary = indicators_vote.get("summary", {})
        if _summary and "call" in _summary:
            _call_n = int(_summary.get("call", 0))
            _put_n = int(_summary.get("put", 0))
            _neu_n = int(_summary.get("neutro", 0))
            bias_from_indicators = {"alta": _call_n, "neutro": _neu_n, "baixa": _put_n}
        else:
            bias_from_indicators = pred.get("bias_summary", {"baixa": 0, "neutro": 1, "alta": 0})

        cc = current_candle or {}
        return {
            "symbol": self.primary_symbol,
            "current_price": cur_price,
            "seconds_left": seconds_left,
            "current_candle": {
                "time": int(cc.get("open_time", 0)) // 1000,
                "open": float(cc.get("open", 0.0)),
                "high": float(cc.get("high", 0.0)),
                "low": float(cc.get("low", 0.0)),
                "close": float(cc.get("close", 0.0)),
                "volume": float(cc.get("volume", 0.0)),
                "is_closed": bool(cc.get("is_closed", False)),
            } if cc and int(cc.get("open_time", 0)) > 0 else None,
            "prediction": {
                "direction": pred.get("direction", "WAIT"),
                "predicted_color": pred.get("predicted_color", f"ANALISANDO (+{self.product_b_wait_sec}s)..."),
                "confidence_pct": pred.get("confidence_pct", 50),
                "product": pred.get("product", "PENDING"),
                "source": pred.get("source", "BBO"),
                "lock_price": pred.get("lock_price"),
                "locked_offset_ms": pred.get("locked_offset_ms"),
            },
            "signal2": signal2,
            "confluence": {
                "modes": {
                    "m5": self.cfg["m5"].get("mode"),
                    "micro15": self.cfg["micro15"].get("mode"),
                },
                "context": pred.get("context", {}),
                "verdicts": pred.get("verdicts", {}),
            },
            "orderbook_l2": {
                "ready": l2_snapshot.get("ready", False) if l2_snapshot else False,
                "imb5_spot": l2_snapshot.get("imb5_spot", 0.0) if l2_snapshot else 0.0,
                "imb5_perp": l2_snapshot.get("imb5_perp", 0.0) if l2_snapshot else 0.0,
                "ofi_spot_5s": l2_snapshot.get("ofi_spot_5s", 0.0) if l2_snapshot else 0.0,
                "ofi_perp_5s": l2_snapshot.get("ofi_perp_5s", 0.0) if l2_snapshot else 0.0,
                "regime": l2_snapshot.get("regime", "AGUARDANDO") if l2_snapshot else "AGUARDANDO",
                "max_bid_wall": l2_snapshot.get("max_bid_wall") if l2_snapshot else None,
                "max_ask_wall": l2_snapshot.get("max_ask_wall") if l2_snapshot else None,
            },
            "bias_summary": bias_from_indicators,
            "flow": pred.get("flow", {"flow_bias": "NEUTRO"}),
            "indicators": pred.get("indicators", {}),
            "indicators_vote": indicators_vote,
            "audit_summary": self.audit_summary,
            "audit_by_product": self.audit_by_product,
            "audit_baseline": self.audit_baseline,
            "confluence_stats": self.confluence_stats,
            "veto_stats": self.veto_stats,
            "fast_bbo": fast_snapshot,
            "latency_telemetry": latency_telemetry,
            "triggers_cfg": dict(self.triggers_cfg),
            "timestamp": now,
        }
