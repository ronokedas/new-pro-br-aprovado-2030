"""
app/radar/engine/context.py
===========================
Confluencias de CONTEXTO (nao sao "mais um indicador votando"): ficam em camadas.

  Camada 1 (regime)      : M5 fechado  -> tendencia, posicao dentro do M5, volatilidade relativa
  Camada 2 (microestrut.): ultimas 4 velas de 15s (= o minuto anterior inteiro) -> fluxo, aceleracao, rejeicao

Tudo e CAUSAL: so usa velas ja fechadas antes de `minute_open_ms`.
O mesmo codigo e usado ao vivo (predictor) e no estudo offline (research/study_confluence.py),
para que o que foi medido seja exatamente o que roda.
"""
import math
from typing import Any, Dict, List, Optional

import numpy as np

M5_MS = 300_000
M1_MS = 60_000


def _ema_last(values: np.ndarray, span: int) -> float:
    a = 2.0 / (span + 1.0)
    e = float(values[0])
    for v in values[1:]:
        e = a * float(v) + (1.0 - a) * e
    return e


def _uniq_sorted(m1: List[Dict[str, Any]], minute_open_ms: int) -> List[Dict[str, Any]]:
    """M1 fechadas ANTES de minute_open_ms, sem duplicatas de open_time, em ordem."""
    d: Dict[int, Dict[str, Any]] = {}
    for c in m1:
        ot = int(c.get("open_time", 0))
        if ot + M1_MS <= minute_open_ms:
            d[ot] = c
    return [d[k] for k in sorted(d)]


def build_m5(m1_sorted: List[Dict[str, Any]], minute_open_ms: int) -> List[Dict[str, Any]]:
    """Agrupa M1 em barras M5 COMPLETAS (5 velas) e ja encerradas antes de minute_open_ms."""
    buckets: Dict[int, List[Dict[str, Any]]] = {}
    for c in m1_sorted:
        b = (int(c.get("open_time", 0)) // M5_MS) * M5_MS
        buckets.setdefault(b, []).append(c)
    bars = []
    for b in sorted(buckets):
        rows = buckets[b]
        if len(rows) != 5 or b + M5_MS > minute_open_ms:
            continue
        bars.append({
            "open_time": b,
            "open": float(rows[0]["open"]),
            "high": max(float(r["high"]) for r in rows),
            "low": min(float(r["low"]) for r in rows),
            "close": float(rows[-1]["close"]),
        })
    return bars


def m5_context(
    m1_closed: List[Dict[str, Any]],
    minute_open_ms: int,
    trend_min_bps: float = 0.8,
    ema_fast: int = 3,
    ema_slow: int = 8,
) -> Dict[str, Any]:
    """Regime M5 para a vela M1 que abre em `minute_open_ms`."""
    pos = int((minute_open_ms % M5_MS) // M1_MS)  # 0..4 : em qual minuto do M5 estamos
    out: Dict[str, Any] = {"ready": False, "pos_in_m5": pos, "bias": "NEUTRO"}
    m1 = _uniq_sorted(m1_closed, minute_open_ms)
    if len(m1) < 60:
        return out
    bars = build_m5(m1, minute_open_ms)
    if len(bars) < ema_slow + 2:
        return out

    closes = np.asarray([b["close"] for b in bars[-60:]], dtype=float)
    ef, es = _ema_last(closes, ema_fast), _ema_last(closes, ema_slow)
    trend_bps = (ef - es) / es * 1e4 if es > 0 else 0.0
    last = bars[-1]
    last_ret_bps = (last["close"] - last["open"]) / last["open"] * 1e4 if last["open"] > 0 else 0.0

    cur_b = (minute_open_ms // M5_MS) * M5_MS
    part = [c for c in m1 if int(c.get("open_time", 0)) >= cur_b]
    partial_bps = 0.0
    if part and float(part[0]["open"]) > 0:
        partial_bps = (float(part[-1]["close"]) - float(part[0]["open"])) / float(part[0]["open"]) * 1e4

    tail = m1[-241:]
    c1 = np.asarray([float(c["close"]) for c in tail], dtype=float)
    rets = np.diff(c1) / c1[:-1] * 1e4 if len(c1) > 2 else np.asarray([])
    sig_s = float(np.std(rets[-15:], ddof=1)) if len(rets) >= 15 else 0.0
    sig_l = float(np.std(rets, ddof=1)) if len(rets) >= 60 else 0.0
    vol_ratio = (sig_s / sig_l) if sig_l > 1e-9 else 1.0
    vol_regime = "BAIXA" if vol_ratio < 0.7 else ("ALTA" if vol_ratio > 1.4 else "NORMAL")

    if trend_bps >= trend_min_bps:
        bias = "CALL"
    elif trend_bps <= -trend_min_bps:
        bias = "PUT"
    else:
        bias = "NEUTRO"

    out.update({
        "ready": True,
        "bias": bias,
        "trend_bps": round(trend_bps, 3),
        "last_m5_ret_bps": round(last_ret_bps, 3),
        "partial_ret_bps": round(partial_bps, 3),
        "sigma_m1_bps": round(sig_s if sig_s > 0 else sig_l, 3),
        "vol_ratio": round(vol_ratio, 3),
        "vol_regime": vol_regime,
    })
    return out


def micro15(c15: List[Dict[str, Any]], sigma_bps: float = 4.0, thr: float = 0.25) -> Dict[str, Any]:
    """
    Microestrutura das ultimas 4 velas de 15s (cronologicas, ja fechadas).
    Retorna a DIRECAO DO FLUXO (momentum do minuto anterior). Se isso significa continuacao ou reversao
    no proximo minuto e uma pergunta empirica: o predictor mede o placar 'agree' vs 'disagree'.
    """
    if not c15 or len(c15) < 4:
        return {"ready": False, "direction": "NEUTRO", "score": 0.0}
    c4 = c15[-4:]
    rets, signed_vol, vols, cpos = [], [], [], []
    for c in c4:
        o, h, l, cl = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
        v, tb = float(c.get("volume", 0.0)), float(c.get("taker_buy_base", 0.0))
        if o <= 0:
            return {"ready": False, "direction": "NEUTRO", "score": 0.0}
        rets.append((cl - o) / o * 1e4)
        vols.append(v)
        signed_vol.append(2.0 * tb - v)
        rng = h - l
        cpos.append((cl - l) / rng if rng > 0 else 0.5)

    net_ret = float(sum(rets))
    tot_v = float(sum(vols))
    net_flow = float(sum(signed_vol) / tot_v) if tot_v > 0 else 0.0
    cp = (float(np.mean(cpos)) - 0.5) * 2.0
    score = (
        0.50 * math.tanh(net_ret / max(sigma_bps, 0.5))
        + 0.35 * max(-1.0, min(1.0, net_flow))
        + 0.15 * cp
    )
    same = sum(1 for r in rets if r * net_ret > 0) / 4.0 if net_ret != 0 else 0.0
    accel = (abs(rets[2]) + abs(rets[3])) - (abs(rets[0]) + abs(rets[1]))  # >0 acelerando

    if score >= thr:
        direction = "CALL"
    elif score <= -thr:
        direction = "PUT"
    else:
        direction = "NEUTRO"
    return {
        "ready": True,
        "direction": direction,
        "score": round(score, 3),
        "net_ret_bps": round(net_ret, 3),
        "net_flow": round(net_flow, 3),
        "close_pos": round(cp, 3),
        "consistency": round(same, 2),
        "accel_bps": round(accel, 3),
    }


def verdict(direction: str, ref_dir: str) -> str:
    """'agree' | 'disagree' | 'neutral' de uma camada (ref_dir) frente ao sinal principal (direction)."""
    if direction not in ("CALL", "PUT") or ref_dir not in ("CALL", "PUT"):
        return "neutral"
    return "agree" if ref_dir == direction else "disagree"
