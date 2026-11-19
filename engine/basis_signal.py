"""
engine/basis_signal.py
======================
Unico sinal que sobreviveu ao protocolo adversario (incluindo holdout 100% virgem de 30 dias,
N=44.421 velas: Acc=54.25% [+4.85pp vs B2], WAcc=58.14%, Dir=+0.54bp, 13/13 semanas positivas):
  REF_basis_z60_Q80 — Desvio padronizado (z-score 60m) do basis Perp - Spot no fechamento de t-1.

AVISO DE REALISMO OPERACIONAL (nao remover):
  - Este e um sinal de convergencia de microestrutura Perp -> Spot (~0.54 a 0.70 bp bruto em t+0s).
  - 61% a 78% do efeito evapora em 1-2 segundos de latencia.
  - Nao cobre taxa taker round-trip (4-10 bps) em Spot/Perp nem transfere para corretoras binarias.
  - Em ~80% das velas (|z60| < Q80 causal), o sinal e explicitamente "WAIT" (sem edge).
"""
from typing import Any, Dict, List, Optional
import numpy as np


def compute_basis_signal(
    spot_candles: List[Dict[str, Any]],
    perp_candles: List[Dict[str, Any]],
    z_window: int = 60,
    quantile_q: float = 0.80,
    instant_bbo: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Calcula o sinal causal REF_basis_z60_Q80 usando ESTRITAMENTE velas M1 fechadas
    alinhadas 1:1 por open_time (Spot e Perp).
    Se `instant_bbo` for fornecido na virada exata do minuto (00.000s), usa o mid real
    sem throttle de @bookTicker como observacao de fechamento de t-1 (eliminando o delay
    do evento @kline_1m), mantendo a calibracao de media/std/Q80 estritamente no passado.
    """
    if not spot_candles or not perp_candles:
        return _neutral_payload(0.0, 0.0, 0.0, 0.0, reason="Sem histórico Spot+Perp")

    # Alinhamento estrito 1:1 por open_time (zero desalinhamento)
    perp_by_ts = {int(c["open_time"]): float(c["close"]) for c in perp_candles if "open_time" in c}
    aligned_spot = []
    aligned_perp = []
    for sc in spot_candles:
        ts = int(sc["open_time"])
        if ts in perp_by_ts:
            s_c = float(sc["close"])
            p_c = perp_by_ts[ts]
            if s_c > 0 and p_c > 0:
                aligned_spot.append(s_c)
                aligned_perp.append(p_c)

    # Se recebemos BBO fresco (< 1500ms) na virada exata do minuto, anexamos como ponto t-1 instantaneo
    used_instant_bbo = False
    if instant_bbo:
        s_mid = instant_bbo.get("binance_spot_mid")
        p_mid = instant_bbo.get("binance_perp_mid")
        ages = instant_bbo.get("ages_ms", {})
        s_age = ages.get("binance_spot")
        p_age = ages.get("binance_perp")
        if s_mid and p_mid and s_age is not None and p_age is not None and s_age < 1500 and p_age < 1500:
            aligned_spot.append(float(s_mid))
            aligned_perp.append(float(p_mid))
            used_instant_bbo = True

    if len(aligned_spot) < z_window + 20:
        cur_p = float(spot_candles[-1]["close"]) if spot_candles else 0.0
        return _neutral_payload(cur_p, 0.0, 0.0, 1.28, reason="Aquecendo janela 60m")

    s_arr = np.asarray(aligned_spot, dtype=float)
    p_arr = np.asarray(aligned_perp, dtype=float)
    basis = (p_arr - s_arr) / s_arr  # em fração (x1e4 = bps)

    # Z-score causal rolling de 60 barras sobre todo o buffer alinhado
    n = len(basis)
    z_vals = np.full(n, np.nan, dtype=float)
    for i in range(z_window - 1, n):
        win = basis[i - z_window + 1:i + 1]
        std = float(np.std(win, ddof=1))
        if std > 1e-12:
            z_vals[i] = (basis[i] - float(np.mean(win))) / std

    last_z = float(z_vals[-1]) if not np.isnan(z_vals[-1]) else 0.0
    last_basis_bps = float(basis[-1] * 1e4)
    mean60_bps = float(np.mean(basis[-z_window:]) * 1e4)
    dev_bps = last_basis_bps - mean60_bps

    # Limiar Q80 calibrado estritamente sobre o histórico PASSADO (exclui a própria barra -1)
    hist_abs_z = np.abs(z_vals[:-1])
    hist_abs_z = hist_abs_z[~np.isnan(hist_abs_z)]
    th_q80 = float(np.quantile(hist_abs_z, quantile_q)) if len(hist_abs_z) >= 60 else 1.28

    cur_price = float(s_arr[-1])
    last_sc = spot_candles[-1]
    last_v = float(last_sc.get("volume", 0.0))
    last_buy = float(last_sc.get("taker_buy_base", 0.0))
    last_sell = max(0.0001, last_v - last_buy)
    cvd_1m = last_buy - last_sell
    agg_ratio = last_buy / last_sell

    src_tag = "BBO" if used_instant_bbo else "KLINE"
    # Decisão causal (Q80)
    if abs(last_z) >= th_q80 and abs(last_z) > 0:
        if last_z > 0:
            direction = "CALL"
            predicted_color = "VERDE"
            alta_pts, baixa_pts, neutro_pts = 1, 0, 0
            flow_bias = f"[{src_tag}] Perp +{dev_bps:.2f}bp (z={last_z:+.2f})"
        else:
            direction = "PUT"
            predicted_color = "VERMELHA"
            alta_pts, baixa_pts, neutro_pts = 0, 1, 0
            flow_bias = f"[{src_tag}] Perp {dev_bps:.2f}bp (z={last_z:+.2f})"

        # Probabilidade calibrada empiricamente (faixa medida no OOS: 54% a 58%, NUNCA 80-92%)
        excess = min(abs(last_z) - th_q80, 2.0)
        conf_pct = int(round(54 + excess * 2.0))
        conf_pct = max(54, min(58, conf_pct))
    else:
        direction = "WAIT"
        predicted_color = "AGUARDAR (SEM EDGE)"
        alta_pts, baixa_pts, neutro_pts = 0, 0, 1
        conf_pct = 50
        flow_bias = f"[{src_tag}] Neutro |z|={abs(last_z):.2f} < Q80({th_q80:.2f})"

    return {
        "direction": direction,
        "predicted_color": predicted_color,
        "confidence_pct": conf_pct,
        "source": src_tag,
        "basis_z60": round(last_z, 3),
        "basis_dev_bps": round(dev_bps, 3),
        "threshold_q80": round(th_q80, 3),
        "bias_summary": {
            "alta": alta_pts,
            "neutro": neutro_pts,
            "baixa": baixa_pts,
        },
        "flow": {
            "cvd_1m": round(cvd_1m, 2),
            "cvd_5m": 0.0,
            "aggression_ratio": round(agg_ratio, 2),
            "flow_bias": flow_bias,
        },
        "indicators": {
            "basis_z60": round(last_z, 3),
            "basis_dev_bps": round(dev_bps, 3),
            "threshold_q80": round(th_q80, 3),
            "price": cur_price,
        },
    }


def _compute_recent_sigma_m1(spot_candles: List[Dict[str, Any]], window: int = 15) -> float:
    """Volatilidade realizada causal (std dos retornos M1 fechados das ultimas `window` velas)."""
    if len(spot_candles) < 5:
        return 0.0005  # 5 bps fallback seguro
    closes = np.asarray([float(c["close"]) for c in spot_candles[-(window + 1):]], dtype=float)
    if len(closes) < 3 or np.any(closes <= 0):
        return 0.0005
    rets = np.diff(closes) / closes[:-1]
    sig = float(np.std(rets, ddof=1)) if len(rets) > 1 else 0.0005
    return max(sig, 5e-5)  # piso 0.5 bp


def compute_product_b_signal(
    fast_snapshot: Optional[Dict[str, Any]],
    spot_candles: Optional[List[Dict[str, Any]]] = None,
    l2_snapshot: Optional[Dict[str, Any]] = None,
    wait_sec: int = 5,
    min_snr: float = 0.50,
) -> Dict[str, Any]:
    """
    Produto B seletivo (FISICA_SNR5s_Q70 + Filtro de Livro de Ofertas L2):
      - Calcula SNR_5s = ret_5s / (sigma_15m * sqrt((60 - wait_sec)/60)).
      - So trava CALL/PUT se |SNR_5s| >= min_snr (0.50 ~ top 30% das velas mais fortes,
        onde Cor 0->60s = ~74.4% no OOS!) E se o Livro de Ofertas L2 NAO mostrar absorcao
        ou spoofing forte contra a direcao.
      - Caso contrario, retorna WAIT (FICA DE FORA — nao opera vela lateral/fraca!).
    """
    if not fast_snapshot:
        return {
            "direction": "WAIT",
            "predicted_color": "FORA DO MERCADO (SEM EDGE)",
            "confidence_pct": 50,
            "intra_ret_bps": 0.0,
            "snr_5s": 0.0,
            "multi_perp_bps": None,
            "flow_bias": f"Aguardando +{wait_sec}s",
        }

    intra_bps = fast_snapshot.get("intra_spot_ret_bps")
    cons_bps = fast_snapshot.get("multi_perp_consensus_bps")
    if intra_bps is None or abs(intra_bps) < 0.05:
        return {
            "direction": "WAIT",
            "predicted_color": "FORA DO MERCADO (FRACA)",
            "confidence_pct": 50,
            "intra_ret_bps": intra_bps or 0.0,
            "snr_5s": 0.0,
            "multi_perp_bps": cons_bps,
            "flow_bias": f"[B +{wait_sec}s] Vela parada ({intra_bps or 0.0:+.2f}bp) — Fora",
        }

    sigma_m1 = _compute_recent_sigma_m1(spot_candles or [], window=15)
    rem_factor = float(np.sqrt(max(1.0, 60.0 - wait_sec) / 60.0))
    ret_frac = intra_bps / 1e4
    snr_5s = ret_frac / (sigma_m1 * rem_factor)

    # Filtro 1: Forca minima fisica (SNR_5s >= 0.50 -> fica de fora nas ~70% velas fracas!)
    if abs(snr_5s) < min_snr:
        return {
            "direction": "WAIT",
            "predicted_color": f"FORA (SNR={abs(snr_5s):.2f} < {min_snr:.2f})",
            "confidence_pct": 50,
            "intra_ret_bps": intra_bps,
            "snr_5s": round(snr_5s, 2),
            "multi_perp_bps": cons_bps,
            "flow_bias": f"[B +{wait_sec}s] Fora: SNR {snr_5s:+.2f} (mov {intra_bps:+.2f}bp)",
        }

    cand_dir = "CALL" if snr_5s > 0 else "PUT"

    # Filtro 2: Confluencia / Veto pelo Livro de Ofertas L2 (nao entra contra absorcao/spoofing forte!)
    l2_tag = ""
    if l2_snapshot and l2_snapshot.get("ready"):
        l2_reg = l2_snapshot.get("regime", "NEUTRO")
        l2_sig = l2_snapshot.get("shadow_signal", "WAIT")
        imb5 = float(l2_snapshot.get("imb5_spot", 0.0))
        ofi5 = float(l2_snapshot.get("ofi_spot_5s", 0.0))

        # Veto se L2 detectou absorcao/spoofing/sweep apontando estritamente o LADO OPOSTO
        if l2_sig in ("CALL", "PUT") and l2_sig != cand_dir and l2_reg in ("ABSORCAO_REVERSAO", "SPOOF_CONTINUACAO", "SWEEP_CONTINUACAO"):
            return {
                "direction": "WAIT",
                "predicted_color": f"VETADO PELO LIVRO L2 ({l2_reg})",
                "confidence_pct": 50,
                "intra_ret_bps": intra_bps,
                "snr_5s": round(snr_5s, 2),
                "multi_perp_bps": cons_bps,
                "flow_bias": f"[VETO L2] {l2_reg} contra {cand_dir} (imb={imb5:+.2f}, ofi={ofi5:+.2f})",
            }
        # Veto se livro Spot top-5 E OFI 5s estao ambos fortemente contra
        if cand_dir == "CALL" and imb5 < -0.50 and ofi5 < -1.5:
            return {
                "direction": "WAIT",
                "predicted_color": "VETADO PELO LIVRO L2 (VENDA FORTE)",
                "confidence_pct": 50,
                "intra_ret_bps": intra_bps,
                "snr_5s": round(snr_5s, 2),
                "multi_perp_bps": cons_bps,
                "flow_bias": f"[VETO L2] Book vendedor (imb={imb5:+.2f}, ofi={ofi5:+.2f})",
            }
        if cand_dir == "PUT" and imb5 > 0.50 and ofi5 > 1.5:
            return {
                "direction": "WAIT",
                "predicted_color": "VETADO PELO LIVRO L2 (COMPRA FORTE)",
                "confidence_pct": 50,
                "intra_ret_bps": intra_bps,
                "snr_5s": round(snr_5s, 2),
                "multi_perp_bps": cons_bps,
                "flow_bias": f"[VETO L2] Book comprador (imb={imb5:+.2f}, ofi={ofi5:+.2f})",
            }

        if l2_sig == cand_dir:
            l2_tag = f" + L2 {l2_reg}"
        else:
            l2_tag = f" | L2 imb={imb5:+.2f}"

    # Probabilidade teorica aproximada da cor final 0->60s calibrada ao OOS (~69% a 78%)
    conf = int(min(78, max(68, round(66 + min(abs(snr_5s), 1.5) * 8.0))))
    if cand_dir == "CALL":
        return {
            "direction": "CALL",
            "predicted_color": f"VERDE (+{wait_sec}s SNR={abs(snr_5s):.2f})",
            "confidence_pct": conf,
            "intra_ret_bps": intra_bps,
            "snr_5s": round(snr_5s, 2),
            "multi_perp_bps": cons_bps,
            "flow_bias": f"[B +{wait_sec}s] +{intra_bps:.2f}bp SNR={snr_5s:+.2f}{l2_tag}",
        }
    return {
        "direction": "PUT",
        "predicted_color": f"VERMELHA (+{wait_sec}s SNR={abs(snr_5s):.2f})",
        "confidence_pct": conf,
        "intra_ret_bps": intra_bps,
        "snr_5s": round(snr_5s, 2),
        "multi_perp_bps": cons_bps,
        "flow_bias": f"[B +{wait_sec}s] {intra_bps:.2f}bp SNR={snr_5s:+.2f}{l2_tag}",
    }


def compute_live_signal2(
    locked_pred: Optional[Dict[str, Any]],
    fast_snapshot: Optional[Dict[str, Any]],
    l2_snapshot: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Sinal 2 (VELA 2 — OSCILANTE EM TEMPO REAL via Livro de Ofertas L2 + Tape + BBO):
    Fornece leitura em tempo real da pressao imediata e detecta o melhor ponto de entrada
    no recuo quando o preco vai contra o Sinal 1 e o Sinal 2 volta a virar a favor!
    """
    if not l2_snapshot or not l2_snapshot.get("ready"):
        return {
            "direction": "WAIT",
            "label": "AGUARDANDO L2...",
            "score": 0.0,
            "entry_status": "AGUARDANDO DADOS L2",
            "entry_code": "WAIT",
            "regime": "AGUARDANDO_L2",
        }

    imb5_s = float(l2_snapshot.get("imb5_spot", 0.0))
    imb5_p = float(l2_snapshot.get("imb5_perp", 0.0))
    ofi_s = float(l2_snapshot.get("ofi_spot_5s", 0.0))
    ofi_p = float(l2_snapshot.get("ofi_perp_5s", 0.0))
    tape_d = float(l2_snapshot.get("tape_delta_5s_btc", 0.0))
    dpx = float(l2_snapshot.get("d_bid_px_5s_bps", 0.0))
    regime = l2_snapshot.get("regime", "NEUTRO")

    # Score continuo [-1, +1] combinando Livro L2 (Spot+Perp), OFI, Tape e momentum 5s
    score = (
        0.30 * imb5_s
        + 0.20 * imb5_p
        + 0.20 * float(np.tanh(ofi_s / 2.0))
        + 0.15 * float(np.tanh(ofi_p / 4.0))
        + 0.15 * float(np.tanh(tape_d / 1.0))
    )
    if score >= 0.12:
        s2_dir = "CALL"
        s2_label = "VERDE (L2 COMPRADOR)"
    elif score <= -0.12:
        s2_dir = "PUT"
        s2_label = "VERMELHA (L2 VENDEDOR)"
    else:
        s2_dir = "WAIT"
        s2_label = "NEUTRO (L2 EQUILIBRADO)"

    # Analise de timing de entrada no recuo em relacao ao Sinal 1 travado
    s1_dir = locked_pred.get("direction", "WAIT") if locked_pred else "WAIT"
    lock_px = locked_pred.get("lock_price") if locked_pred else None
    cur_px = fast_snapshot.get("binance_spot_mid") if fast_snapshot else None
    open_px = None
    if fast_snapshot and cur_px and fast_snapshot.get("intra_spot_ret_bps") is not None:
        ret_f = fast_snapshot["intra_spot_ret_bps"] / 1e4
        if (1.0 + ret_f) > 0:
            open_px = cur_px / (1.0 + ret_f)

    entry_status = "FORA DO MERCADO (SINAL 1 EM ESPERA)"
    entry_code = "WAIT"

    if s1_dir == "CALL" and cur_px and lock_px:
        in_pullback = (cur_px < lock_px) or (open_px is not None and cur_px <= open_px)
        if in_pullback and s2_dir == "CALL":
            entry_status = "🎯 RECUO + SINAL 2 VERDE (MELHOR PONTO CALL)"
            entry_code = "BEST_ENTRY"
        elif in_pullback and s2_dir != "CALL":
            entry_status = "⚠️ RECUANDO CONTRA — AGUARDE SINAL 2 VIRAR VERDE"
            entry_code = "WAIT_PULLBACK"
        elif s2_dir == "CALL":
            entry_status = "✅ SINAL 1 + SINAL 2 ALINHADOS (CALL)"
            entry_code = "ALIGNED"
        else:
            entry_status = "⏳ SINAL 1 CALL | SINAL 2 PERDENDO FORÇA"
            entry_code = "CAUTION"
    elif s1_dir == "PUT" and cur_px and lock_px:
        in_pullback = (cur_px > lock_px) or (open_px is not None and cur_px >= open_px)
        if in_pullback and s2_dir == "PUT":
            entry_status = "🎯 RECUO + SINAL 2 VERMELHA (MELHOR PONTO PUT)"
            entry_code = "BEST_ENTRY"
        elif in_pullback and s2_dir != "PUT":
            entry_status = "⚠️ SUBINDO CONTRA — AGUARDE SINAL 2 VIRAR VERMELHA"
            entry_code = "WAIT_PULLBACK"
        elif s2_dir == "PUT":
            entry_status = "✅ SINAL 1 + SINAL 2 ALINHADOS (PUT)"
            entry_code = "ALIGNED"
        else:
            entry_status = "⏳ SINAL 1 PUT | SINAL 2 PERDENDO FORÇA"
            entry_code = "CAUTION"

    return {
        "direction": s2_dir,
        "label": s2_label,
        "score": round(score, 3),
        "entry_status": entry_status,
        "entry_code": entry_code,
        "regime": regime,
        "dpx_5s_bps": round(dpx, 2),
    }


def compute_product_b_3s_signal(fast_snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Alias de compatibilidade."""
    return compute_product_b_signal(fast_snapshot, wait_sec=3)


def _neutral_payload(price: float, z: float, dev_bps: float, th: float, reason: str) -> Dict[str, Any]:
    return {
        "direction": "WAIT",
        "predicted_color": "AGUARDAR (SEM EDGE)",
        "confidence_pct": 50,
        "basis_z60": round(z, 3),
        "basis_dev_bps": round(dev_bps, 3),
        "threshold_q80": round(th, 3),
        "bias_summary": {"alta": 0, "neutro": 1, "baixa": 0},
        "flow": {"cvd_1m": 0.0, "cvd_5m": 0.0, "aggression_ratio": 1.0, "flow_bias": reason},
        "indicators": {"basis_z60": round(z, 3), "basis_dev_bps": round(dev_bps, 3), "threshold_q80": round(th, 3), "price": price},
    }
