"""
confluences.py (v2.1)
Soma de confluências para determinar a REAL INTENÇÃO da próxima vela de 1 minuto do BTC:
- Fluxo Real e Agressão: CVD (Cumulative Volume Delta) e Razão de Agressão Taker Buy/Sell
- Ação do Preço: Rejeição de pavios (absorção institucional) e força do fechamento
- Cesta Completa de Osciladores e Médias Móveis (Estilo TradingView)
"""
import numpy as np
import pandas as pd
from typing import Dict, Any, List

def compute_technical_bias(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Analisa todo o histórico até a vela recém-fechada.
    Retorna o viés consolidado para a PRÓXIMA vela.
    """
    if len(candles) < 10:
        return {
            "bias_summary": {"alta": 5, "neutro": 2, "baixa": 5},
            "flow": {"cvd_1m": 0.0, "cvd_5m": 0.0, "aggression_ratio": 1.0, "flow_bias": "Neutro"},
            "indicators": {"rsi": 50.0, "cci": 0.0, "stoch_k": 50.0, "macd_hist": 0.0, "price": 0.0},
            "score": 0.50,
            "direction": "CALL",
            "confidence_pct": 52
        }

    df = pd.DataFrame(candles)
    for col in ["open", "high", "low", "close", "volume", "taker_buy_base"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            df[col] = 0.0

    c = df["close"]
    h = df["high"]
    l = df["low"]
    o = df["open"]
    v = df["volume"]
    tbb = df["taker_buy_base"]

    alta_points = 0
    baixa_points = 0
    neutro_points = 0

    cur_p = float(c.iloc[-1])

    # ─────────────────────────────────────────────────────────────
    # 1. FLUXO REAL: CVD & AGRESSÃO (Peso Forte: dados de agressão a mercado)
    # ─────────────────────────────────────────────────────────────
    last_v = float(v.iloc[-1]) if v.iloc[-1] > 0 else 1.0
    last_buy = float(tbb.iloc[-1])
    last_sell = max(0.0001, last_v - last_buy)
    
    cvd_1m = last_buy - last_sell
    aggression_ratio = last_buy / last_sell

    # CVD das últimas 5 velas fechadas
    cvd_5m = float((tbb - (v - tbb)).rolling(min(len(df), 5)).sum().iloc[-1])

    if aggression_ratio >= 1.20 or cvd_1m > 1.5:
        flow_bias = "Comprador Forte"
        alta_points += 4
    elif aggression_ratio <= 0.83 or cvd_1m < -1.5:
        flow_bias = "Vendedor Forte"
        baixa_points += 4
    else:
        flow_bias = "Equilibrado"
        neutro_points += 2

    # ─────────────────────────────────────────────────────────────
    # 2. AÇÃO DO PREÇO DA VELA ANTERIOR (Absorção e Pavios)
    # ─────────────────────────────────────────────────────────────
    last_o = float(o.iloc[-1])
    last_h = float(h.iloc[-1])
    last_l = float(l.iloc[-1])
    last_c = float(c.iloc[-1])
    candle_range = max(0.01, last_h - last_l)

    lower_wick = (min(last_o, last_c) - last_l) / candle_range
    upper_wick = (last_h - max(last_o, last_c)) / candle_range

    # Rejeição de fundo com volume (absorção compradora)
    if lower_wick >= 0.40 and cvd_1m >= 0:
        alta_points += 3
    # Rejeição de topo com volume (absorção vendedora)
    elif upper_wick >= 0.40 and cvd_1m <= 0:
        baixa_points += 3

    # ─────────────────────────────────────────────────────────────
    # 3. OSCILADORES (TradingView)
    # ─────────────────────────────────────────────────────────────
    # RSI (14)
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi14 = float((100 - (100 / (1 + rs))).iloc[-1])
    if np.isnan(rsi14): rsi14 = 50.0

    if rsi14 < 35:
        alta_points += 2
    elif rsi14 > 65:
        baixa_points += 2
    else:
        neutro_points += 1

    # Estocástico %K (14, 3, 3)
    w_stoch = min(len(df), 14)
    low14 = l.rolling(w_stoch, min_periods=3).min()
    high14 = h.rolling(w_stoch, min_periods=3).max()
    stoch_k = float(((c - low14) / (high14 - low14 + 1e-9) * 100).rolling(3, min_periods=1).mean().iloc[-1])
    if np.isnan(stoch_k): stoch_k = 50.0

    if stoch_k < 20:
        alta_points += 2
    elif stoch_k > 80:
        baixa_points += 2
    else:
        neutro_points += 1

    # CCI (20)
    w_cci = min(len(df), 20)
    tp = (h + l + c) / 3.0
    tp_ma = tp.rolling(w_cci, min_periods=3).mean()
    tp_md = tp.rolling(w_cci, min_periods=3).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True).replace(0, np.nan)
    cci20 = float(((tp - tp_ma) / (0.015 * tp_md)).iloc[-1])
    if np.isnan(cci20): cci20 = 0.0

    if cci20 < -100:
        alta_points += 2
    elif cci20 > 100:
        baixa_points += 2
    else:
        neutro_points += 1

    # MACD (12, 26, 9)
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd_l = ema12 - ema26
    macd_sig = macd_l.ewm(span=9, adjust=False).mean()
    macd_hist = float((macd_l - macd_sig).iloc[-1])

    if macd_hist > 0:
        alta_points += 1
    elif macd_hist < 0:
        baixa_points += 1
    else:
        neutro_points += 1

    # ─────────────────────────────────────────────────────────────
    # 4. MÉDIAS MÓVEIS (Trend Following)
    # ─────────────────────────────────────────────────────────────
    for span in [10, 20, 50, 100, 200]:
        if len(c) >= min(span, 5):
            ema_val = float(c.ewm(span=span, adjust=False).mean().iloc[-1])
            if cur_p > ema_val:
                alta_points += 1
            else:
                baixa_points += 1

    # VWMA (20)
    pv = c * v
    vwma20 = float((pv.rolling(min(len(df), 20), min_periods=3).sum() / v.rolling(min(len(df), 20), min_periods=3).sum().replace(0, np.nan)).iloc[-1])
    if not np.isnan(vwma20):
        if cur_p > vwma20:
            alta_points += 1
        else:
            baixa_points += 1

    # ─────────────────────────────────────────────────────────────
    # CONSOLIDAÇÃO FINAL
    # ─────────────────────────────────────────────────────────────
    total_direcionais = alta_points + baixa_points
    if total_direcionais == 0:
        total_direcionais = 1

    prob_alta = alta_points / total_direcionais

    if prob_alta >= 0.50:
        direction = "CALL"
        predicted_color = "VERDE"
        conf_pct = int(52 + (prob_alta - 0.50) * 80)
    else:
        direction = "PUT"
        predicted_color = "VERMELHA"
        conf_pct = int(52 + (0.50 - prob_alta) * 80)

    conf_pct = min(92, max(52, conf_pct))

    return {
        "bias_summary": {
            "alta": int(alta_points),
            "neutro": int(neutro_points),
            "baixa": int(baixa_points)
        },
        "flow": {
            "cvd_1m": round(cvd_1m, 2),
            "cvd_5m": round(cvd_5m, 2),
            "aggression_ratio": round(aggression_ratio, 2),
            "flow_bias": flow_bias
        },
        "indicators": {
            "rsi": round(rsi14, 1),
            "cci": round(cci20, 1),
            "stoch_k": round(stoch_k, 1),
            "macd_hist": round(macd_hist, 2),
            "price": cur_p
        },
        "direction": direction,
        "predicted_color": predicted_color,
        "confidence_pct": conf_pct
    }
