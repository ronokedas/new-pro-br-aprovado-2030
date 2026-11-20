"""
app/radar/engine/sr_absorption.py
==================================
Módulo de detecção de absorção institucional em Suporte / Resistência e Livro L2.

Princípio de Microestrutura:
1. Quando o preço testa um nível chave de Suporte (calculado via pivôs fractais
   ou paredes/lotes solitários no L2):
   - Se os vendedores agridem a mercado mas são completamente absorvidos pelas
     ordens passivas (deixando pavio inferior de rejeição e/ou CVD positivo e/ou
     OFI/desbalanceamento comprador no L2);
   - E a maioria dos indicadores técnicos concorda com alta (ou não está vendida):
   -> Dispara gatilho de reversão e continuidade compradora: CALL (Próxima vela Verde).

2. Simetricamente, quando o preço testa uma Resistência ou lote solitário de venda:
   - Se compradores são absorvidos (pavio superior de rejeição e/ou CVD negativo
     e/ou OFI vendedor no L2);
   - E os indicadores técnicos apontam baixa:
   -> Dispara gatilho de reversão vendedora: PUT (Próxima vela Vermelha).
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("radar.sr_absorption")


def extract_pivots(
    candles: List[Dict[str, Any]],
    current_price: float,
    left: int = 2,
    right: int = 2,
    tolerance: float = 0.0015,
) -> Tuple[List[float], List[float]]:
    """
    Extrai níveis de suporte e resistência via pivôs fractais estritamente causais.
    """
    if len(candles) < (left + right + 1):
        return [], []

    highs = [float(c.get("high", 0.0)) for c in candles]
    lows = [float(c.get("low", 0.0)) for c in candles]
    n = len(candles)

    raw_res: List[float] = []
    raw_sup: List[float] = []

    for i in range(left, n - right):
        # Pivô de alta (Swing High)
        if all(highs[i] >= highs[i - j] for j in range(1, left + 1)) and all(
            highs[i] >= highs[i + j] for j in range(1, right + 1)
        ):
            raw_res.append(highs[i])

        # Pivô de baixa (Swing Low)
        if all(lows[i] <= lows[i - j] for j in range(1, left + 1)) and all(
            lows[i] <= lows[i + j] for j in range(1, right + 1)
        ):
            raw_sup.append(lows[i])

    # Clusteriza níveis próximos
    res_clustered: List[float] = []
    for r in sorted(raw_res):
        if not any(abs(c - r) / r < tolerance for c in res_clustered):
            res_clustered.append(round(r, 2))

    sup_clustered: List[float] = []
    for s in sorted(raw_sup, reverse=True):
        if not any(abs(c - s) / s < tolerance for c in sup_clustered):
            sup_clustered.append(round(s, 2))

    return res_clustered, sup_clustered


def detect_sr_absorption(
    spot_candles: List[Dict[str, Any]],
    l2_snapshot: Optional[Dict[str, Any]] = None,
    indicators_vote: Optional[Dict[str, Any]] = None,
    tolerance_pct: float = 0.0025,  # 0.25% de proximidade (~$150 em $60k)
    min_wick_ratio: float = 0.20,   # 20% do range da vela como pavio de rejeição
) -> Optional[Dict[str, Any]]:
    """
    Verifica se a última vela fechada ou preço atual confirmou absorção em Suporte/Resistência.
    """
    if not spot_candles or len(spot_candles) < 5:
        return None

    last_c = spot_candles[-1]
    o = float(last_c.get("open", 0.0))
    h = float(last_c.get("high", 0.0))
    l = float(last_c.get("low", 0.0))
    c = float(last_c.get("close", 0.0))
    rng = max(1e-5, h - l)

    # Pavios de rejeição
    lower_wick = (min(o, c) - l) / rng
    upper_wick = (h - max(o, c)) / rng

    # Extrai suportes e resistências causais
    resistances, supports = extract_pivots(spot_candles[-60:], current_price=c)

    # Paredes L2 (lotes solitários / ordens em bloco)
    bid_wall_px = None
    bid_wall_qty = 0.0
    ask_wall_px = None
    ask_wall_qty = 0.0
    l2_ready = False
    imb5 = 0.0
    ofi5 = 0.0
    regime = "NEUTRO"

    if l2_snapshot and l2_snapshot.get("ready"):
        l2_ready = True
        imb5 = float(l2_snapshot.get("imb5_spot", 0.0))
        ofi5 = float(l2_snapshot.get("ofi_spot_5s", 0.0))
        regime = str(l2_snapshot.get("regime", "NEUTRO"))

        bw = l2_snapshot.get("max_bid_wall") or {}
        bid_wall_px = float(bw.get("price", 0.0)) if bw.get("price") else None
        bid_wall_qty = float(bw.get("qty_btc", 0.0)) if bw.get("qty_btc") else 0.0

        aw = l2_snapshot.get("max_ask_wall") or {}
        ask_wall_px = float(aw.get("price", 0.0)) if aw.get("price") else None
        ask_wall_qty = float(aw.get("qty_btc", 0.0)) if aw.get("qty_btc") else 0.0

    # Agrega suportes (fractais + lote solitário L2 se expressivo)
    all_supports = list(supports)
    if bid_wall_px and bid_wall_px > 0 and bid_wall_qty >= 2.0:
        all_supports.append(bid_wall_px)

    # Agrega resistências (fractais + lote solitário L2 se expressivo)
    all_resistances = list(resistances)
    if ask_wall_px and ask_wall_px > 0 and ask_wall_qty >= 2.0:
        all_resistances.append(ask_wall_px)

    # Votos dos indicadores técnicos institucionais
    summary = (indicators_vote or {}).get("summary", {})
    call_votes = int(summary.get("call", 0))
    put_votes = int(summary.get("put", 0))

    # Volume & CVD da vela anterior
    vol = float(last_c.get("volume", 0.0))
    taker_buy = float(last_c.get("taker_buy_base", 0.0))
    taker_sell = max(0.0001, vol - taker_buy)
    cvd_1m = taker_buy - taker_sell

    # -------------------------------------------------------------
    # 1. VERIFICAÇÃO DE ABSORÇÃO COMPRADORA EM SUPORTE (CALL)
    # -------------------------------------------------------------
    touched_support = False
    matched_support_lvl = None
    for sup in all_supports:
        # A mínima tocou o suporte ou chegou muito perto
        dist_pct = abs(l - sup) / sup
        if dist_pct <= tolerance_pct or (l <= sup <= max(o, c)):
            touched_support = True
            matched_support_lvl = sup
            break

    bullish_wick = lower_wick >= min_wick_ratio
    bullish_cvd = cvd_1m > 0.3 or (vol > 0 and (taker_buy / vol) >= 0.50)
    bullish_l2 = imb5 > 0.05 or ofi5 > 0.05 or regime in ("ABSORCAO_REVERSAO", "SPOOF_CONTINUACAO")
    institutions_permit_call = call_votes >= put_votes  # Maioria concorda ou empate

    if touched_support and (bullish_wick or bullish_cvd or bullish_l2) and institutions_permit_call:
        confidence = 68
        if call_votes >= 4:
            confidence += 4
        if bullish_wick and bullish_l2:
            confidence += 4
        confidence = min(82, confidence)

        reason_parts = []
        if matched_support_lvl:
            reason_parts.append(f"Suporte ${matched_support_lvl:,.1f}")
        if bullish_wick:
            reason_parts.append(f"Pavio Inf {lower_wick*100:.0f}%")
        if bullish_cvd:
            reason_parts.append(f"CVD +{cvd_1m:.1f} BTC")
        if bullish_l2:
            reason_parts.append(f"L2 Imb +{imb5:.2f}")

        reason = " + ".join(reason_parts)
        log.info("🎯 [ABSORÇÃO EM SUPORTE] Disparando CALL com confluência: %s", reason)
        return {
            "detected": True,
            "direction": "CALL",
            "predicted_color": "VERDE (ABSORÇÃO EM SUPORTE)",
            "confidence_pct": confidence,
            "source": "ABSORPTION_SR",
            "support_level": matched_support_lvl,
            "reason": reason,
            "flow_bias": f"[ABSORÇÃO SUPORTE] {reason} | Ind: {call_votes}A vs {put_votes}B",
        }

    # -------------------------------------------------------------
    # 2. VERIFICAÇÃO DE ABSORÇÃO VENDEDORA EM RESISTÊNCIA (PUT)
    # -------------------------------------------------------------
    touched_resistance = False
    matched_res_lvl = None
    for res in all_resistances:
        dist_pct = abs(h - res) / res
        if dist_pct <= tolerance_pct or (min(o, c) <= res <= h):
            touched_resistance = True
            matched_res_lvl = res
            break

    bearish_wick = upper_wick >= min_wick_ratio
    bearish_cvd = cvd_1m < -0.3 or (vol > 0 and (taker_buy / vol) <= 0.50)
    bearish_l2 = imb5 < -0.05 or ofi5 < -0.05 or regime in ("ABSORCAO_REVERSAO", "SPOOF_CONTINUACAO")
    institutions_permit_put = put_votes >= call_votes  # Maioria concorda ou empate

    if touched_resistance and (bearish_wick or bearish_cvd or bearish_l2) and institutions_permit_put:
        confidence = 68
        if put_votes >= 4:
            confidence += 4
        if bearish_wick and bearish_l2:
            confidence += 4
        confidence = min(82, confidence)

        reason_parts = []
        if matched_res_lvl:
            reason_parts.append(f"Resistência ${matched_res_lvl:,.1f}")
        if bearish_wick:
            reason_parts.append(f"Pavio Sup {upper_wick*100:.0f}%")
        if bearish_cvd:
            reason_parts.append(f"CVD {cvd_1m:.1f} BTC")
        if bearish_l2:
            reason_parts.append(f"L2 Imb {imb5:.2f}")

        reason = " + ".join(reason_parts)
        log.info("🎯 [ABSORÇÃO EM RESISTÊNCIA] Disparando PUT com confluência: %s", reason)
        return {
            "detected": True,
            "direction": "PUT",
            "predicted_color": "VERMELHA (ABSORÇÃO EM RESISTÊNCIA)",
            "confidence_pct": confidence,
            "source": "ABSORPTION_SR",
            "resistance_level": matched_res_lvl,
            "reason": reason,
            "flow_bias": f"[ABSORÇÃO RESISTÊNCIA] {reason} | Ind: {put_votes}B vs {call_votes}A",
        }

    return None
