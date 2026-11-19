"""
indicators.py
Cálculo em tempo real de indicadores técnicos e confluências:
EMA, CCI, Suporte e Resistência (SR), RSI, MACD e CVD.
"""
import numpy as np
import pandas as pd
from typing import Dict, Any, List

def calculate_realtime_indicators(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Recebe lista de velas com chaves: open, high, low, close, volume, taker_buy_base, timestamp
    Retorna indicadores mais recentes e sinais de confluência.
    """
    if len(candles) < 20:
        return {}

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

    # 1. EMAs
    ema9 = c.ewm(span=9, adjust=False).mean()
    ema21 = c.ewm(span=21, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()

    # 2. CCI (20)
    tp = (h + l + c) / 3.0
    tp_ma = tp.rolling(20, min_periods=5).mean()
    tp_md = tp.rolling(20, min_periods=5).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    tp_md = tp_md.replace(0, np.nan)
    cci20 = (tp - tp_ma) / (0.015 * tp_md)
    cci_val = float(cci20.iloc[-1]) if not np.isnan(cci20.iloc[-1]) else 0.0

    # 3. Suporte e Resistência dinâmicos (Donchian / Swings)
    window_sr = min(len(df), 30)
    res_val = float(h.iloc[-window_sr:].max())
    sup_val = float(l.iloc[-window_sr:].min())
    cur_price = float(c.iloc[-1])

    near_support = (cur_price - sup_val) / (res_val - sup_val + 1e-9) < 0.20
    near_resistance = (res_val - cur_price) / (res_val - sup_val + 1e-9) < 0.20

    # 4. RSI (14)
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi14 = 100 - (100 / (1 + rs))
    rsi_val = float(rsi14.iloc[-1]) if not np.isnan(rsi14.iloc[-1]) else 50.0

    # 5. CVD (Delta de Comprador Agressivo)
    delta_vol = (tbb - (v - tbb)).rolling(5, min_periods=1).sum()
    cvd_norm = float((delta_vol / v.rolling(5, min_periods=1).sum().replace(0, np.nan)).iloc[-1])
    if np.isnan(cvd_norm):
        cvd_norm = 0.0

    # Monta features para o modelo ML
    ema9_val = float(ema9.iloc[-1])
    ema21_val = float(ema21.iloc[-1])
    ema50_val = float(ema50.iloc[-1])
    ema200_val = float(ema200.iloc[-1])

    features_dict = {
        "ema9_21_cross": (ema9_val - ema21_val) / cur_price if cur_price > 0 else 0,
        "ema50_150_cross": (ema50_val - ema200_val) / cur_price if cur_price > 0 else 0,
        "price_vs_ema50": (cur_price - ema50_val) / cur_price if cur_price > 0 else 0,
        "price_vs_ema200": (cur_price - ema200_val) / cur_price if cur_price > 0 else 0,
        "rsi14": rsi_val,
        "rsi7": float(rsi14.iloc[-1]),
        "rsi_diff": float(rsi14.diff().iloc[-1]) if len(rsi14) > 1 and not np.isnan(rsi14.diff().iloc[-1]) else 0,
        "macd_line": ema9_val - ema21_val,
        "macd_signal": (ema9 - ema21).ewm(span=9, adjust=False).mean().iloc[-1],
        "macd_hist": (ema9_val - ema21_val) - ((ema9 - ema21).ewm(span=9, adjust=False).mean().iloc[-1]),
        "cci20": cci_val,
        "cci50": cci_val,
        "vol_zscore_20": float(((v - v.rolling(20).mean()) / v.rolling(20).std().replace(0, np.nan)).iloc[-1]) if len(v) >= 20 else 0.0,
        "vol_zscore_60": 0.0,
        "vol_ma_ratio": float((v / v.rolling(20).mean().replace(0, np.nan)).iloc[-1]) if len(v) >= 20 else 1.0,
        "cvd5_norm": cvd_norm,
        "cvd30_norm": cvd_norm,
        "range_pos60": float(((cur_price - sup_val) / (res_val - sup_val + 1e-9))),
        "ret1": float(c.pct_change(1).iloc[-1]) if len(c) > 1 else 0.0,
        "ret3": float(c.pct_change(3).iloc[-1]) if len(c) > 3 else 0.0,
        "ret5": float(c.pct_change(5).iloc[-1]) if len(c) > 5 else 0.0,
        "vol_real_10": float(c.pct_change(1).rolling(10).std().iloc[-1]) if len(c) >= 10 else 0.001,
        "vol_real_60": float(c.pct_change(1).rolling(min(len(c), 60)).std().iloc[-1]) if len(c) >= 10 else 0.001,
        "hl_range": float(((h - l) / l).iloc[-1]) if l.iloc[-1] > 0 else 0.0,
        "body_pct": float((abs(c - o) / o).iloc[-1]) if o.iloc[-1] > 0 else 0.0
    }

    # Limpeza de NaNs nas features
    for k, val in features_dict.items():
        if np.isnan(val) or np.isinf(val):
            features_dict[k] = 0.0

    return {
        "price": cur_price,
        "ema9": ema9_val,
        "ema21": ema21_val,
        "ema_bullish": ema9_val > ema21_val,
        "cci": cci_val,
        "cci_oversold": cci_val < -100,
        "cci_overbought": cci_val > 100,
        "resistance": res_val,
        "support": sup_val,
        "near_support": near_support,
        "near_resistance": near_resistance,
        "rsi": rsi_val,
        "cvd_norm": cvd_norm,
        "features": features_dict
    }
