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


def compute_product_b_signal(fast_snapshot: Optional[Dict[str, Any]], wait_sec: int = 5) -> Dict[str, Any]:
    """
    Produto B (AVALIADO SEPARADO DO PRODUTO A!):
    Sinal travado em open_t + wait_sec (padrao: 5s, dentro de {3, 5, 10}) usando o movimento
    real da Binance Spot nos primeiros N segundos via BBO sem throttle (Follow_N),
    com consenso multi-perp (Binance + Bybit + OKX) exibido como telemetria.
    """
    if not fast_snapshot:
        return {
            "direction": "WAIT",
            "predicted_color": f"AGUARDAR (+{wait_sec}s)",
            "confidence_pct": 50,
            "intra_ret_bps": 0.0,
            "multi_perp_bps": None,
            "flow_bias": f"Aguardando +{wait_sec}s",
        }

    intra_bps = fast_snapshot.get("intra_spot_ret_bps")
    cons_bps = fast_snapshot.get("multi_perp_consensus_bps")
    if intra_bps is None or abs(intra_bps) < 0.05:
        return {
            "direction": "WAIT",
            "predicted_color": f"EMPATE +{wait_sec}s (AGUARDAR)",
            "confidence_pct": 50,
            "intra_ret_bps": intra_bps or 0.0,
            "multi_perp_bps": cons_bps,
            "flow_bias": f"[B +{wait_sec}s] Sem movimento ({intra_bps or 0.0:+.2f}bp)",
        }

    # Confianca calibrada honestamente ao medido empiricamente em Follow_5s (~62-66%)
    conf = int(min(68, max(61, round(62 + min(abs(intra_bps), 3.0) * 1.5))))
    cons_str = f" | Perps {cons_bps:+.2f}bp" if cons_bps is not None else ""
    if intra_bps > 0:
        return {
            "direction": "CALL",
            "predicted_color": f"VERDE (TRAVADO +{wait_sec}s)",
            "confidence_pct": conf,
            "intra_ret_bps": intra_bps,
            "multi_perp_bps": cons_bps,
            "flow_bias": f"[B +{wait_sec}s] Spot +{intra_bps:.2f}bp{cons_str}",
        }
    return {
        "direction": "PUT",
        "predicted_color": f"VERMELHA (TRAVADO +{wait_sec}s)",
        "confidence_pct": conf,
        "intra_ret_bps": intra_bps,
        "multi_perp_bps": cons_bps,
        "flow_bias": f"[B +{wait_sec}s] Spot {intra_bps:.2f}bp{cons_str}",
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
