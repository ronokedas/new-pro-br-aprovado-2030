"""
app/radar/engine/indicators.py
================================
Módulo de cálculo dos 10 indicadores técnicos para o Painel "Veredito Institucional".

Cada indicador retorna:
  signal: "CALL" | "PUT" | "NEUTRO"
  value:  float  (valor numérico do indicador)
  label:  str    (descrição humana do valor)

A função compute_all_indicators() agrega tudo e retorna também:
  summary: { call: int, put: int, neutro: int, verdict: str, pct: float }
  order_flow: { net_delta_pct: float, buy_pct: float, sell_pct: float }

Causalidade estrita: só usa velas FECHADAS (buffer histórico, não inclui vela atual aberta).
Anti-repaint: nenhum dado futuro é usado. Período mínimo requerido: 20 velas.
"""
import logging
import math
from typing import Any, Dict, List

log = logging.getLogger("radar.indicators")


# ---------------------------------------------------------------------------
# Helpers matemáticos (sem dependências externas = sem ImportError no Docker)
# ---------------------------------------------------------------------------

def _ema(values: List[float], span: int) -> List[float]:
    """EMA exponencial com suavização 2/(span+1)."""
    if not values:
        return []
    alpha = 2.0 / (span + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(alpha * v + (1 - alpha) * result[-1])
    return result


def _sma(values: List[float], period: int) -> List[float]:
    """Média Móvel Simples."""
    result = []
    for i in range(len(values)):
        if i < period - 1:
            result.append(float("nan"))
        else:
            result.append(sum(values[i - period + 1: i + 1]) / period)
    return result


def _rsi(closes: List[float], period: int = 14) -> float:
    """RSI de Wilder usando EMA suavizada."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    # EMA de Wilder (alpha = 1/period)
    alpha = 1.0 / period
    ag = gains[0]
    al = losses[0]
    for i in range(1, len(gains)):
        ag = alpha * gains[i] + (1 - alpha) * ag
        al = alpha * losses[i] + (1 - alpha) * al
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)


def _stdev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def _true_range(high: float, low: float, prev_close: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


# ---------------------------------------------------------------------------
# Indicadores individuais
# ---------------------------------------------------------------------------

def _indicator_rsi(closes: List[float]) -> Dict[str, Any]:
    """RSI(14): <30 = oversold/CALL, >70 = overbought/PUT, outros = conforme 50."""
    val = _rsi(closes[-20:], 14)
    if val < 30:
        signal, label = "CALL", "Sobrevendido"
    elif val < 45:
        signal, label = "CALL", "Neutro"
    elif val <= 55:
        signal, label = "NEUTRO", "Neutro"
    elif val <= 70:
        signal, label = "PUT", "Neutro"
    else:
        signal, label = "PUT", "Sobrecomprado"
    return {"signal": signal, "value": round(val, 1), "label": label, "name": "RSI (14)"}


def _indicator_macd(closes: List[float]) -> Dict[str, Any]:
    """MACD(12,26,9): histograma positivo = CALL, negativo = PUT, próximo de zero = NEUTRO."""
    if len(closes) < 30:
        return {"signal": "NEUTRO", "value": 0.0, "label": "Histograma", "name": "MACD"}
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = [ema12[i] - ema26[i] for i in range(len(closes))]
    signal_line = _ema(macd_line[-15:], 9)
    hist = macd_line[-1] - signal_line[-1]
    # Normaliza pelo preço para ter valor comparável
    price = closes[-1] if closes[-1] > 0 else 1.0
    hist_norm = hist / price * 10000  # em bps ajustados
    threshold = 0.3  # bps mínimo para definir direção
    if hist_norm > threshold:
        signal, label = "CALL", "Histograma"
    elif hist_norm < -threshold:
        signal, label = "PUT", "Histograma"
    else:
        signal, label = "NEUTRO", "Histograma"
    return {"signal": signal, "value": round(hist, 6), "label": label, "name": "MACD"}


def _indicator_ema_cross(closes: List[float]) -> Dict[str, Any]:
    """EMA(9/21): EMA9 > EMA21 = CALL, EMA9 < EMA21 = PUT."""
    if len(closes) < 25:
        return {"signal": "NEUTRO", "value": 0.0, "label": "Curto Prazo", "name": "EMA (9/21)"}
    ema9 = _ema(closes, 9)[-1]
    ema21 = _ema(closes, 21)[-1]
    diff_bps = (ema9 - ema21) / ema21 * 10000 if ema21 > 0 else 0.0
    threshold = 0.5  # 0.05% de diferença mínima
    if diff_bps > threshold:
        signal, label = "CALL", "Curto Prazo"
    elif diff_bps < -threshold:
        signal, label = "PUT", "Curto Prazo"
    else:
        signal, label = "NEUTRO", "Curto Prazo"
    return {"signal": signal, "value": round(diff_bps, 2), "label": label, "name": "EMA (9/21)"}


def _indicator_sma_cross(closes: List[float]) -> Dict[str, Any]:
    """SMA(50/200): preço acima SMA50 e SMA50>SMA200 = CALL (Golden Cross)."""
    if len(closes) < 60:
        # Sem dados suficientes para SMA200, usa SMA50 vs preço
        if len(closes) < 55:
            return {"signal": "NEUTRO", "value": 0.0, "label": "Longo Prazo", "name": "SMA (50/200)"}
        sma50_lst = _sma(closes, 50)
        sma50 = next((v for v in reversed(sma50_lst) if not math.isnan(v)), closes[-1])
        cur = closes[-1]
        diff_bps = (cur - sma50) / sma50 * 10000 if sma50 > 0 else 0.0
        signal = "CALL" if diff_bps > 2 else ("PUT" if diff_bps < -2 else "NEUTRO")
        return {"signal": signal, "value": round(diff_bps, 1), "label": "Longo Prazo", "name": "SMA (50/200)"}

    sma50 = sum(closes[-50:]) / 50
    sma200_period = min(200, len(closes))
    sma200 = sum(closes[-sma200_period:]) / sma200_period
    diff = sma50 - sma200
    label = f"{'50 > 200' if diff > 0 else '50 < 200'}"
    if diff > 0:
        signal = "CALL"
    elif diff < 0:
        signal = "PUT"
    else:
        signal = "NEUTRO"
    return {"signal": signal, "value": round(diff, 2), "label": label, "name": "SMA (50/200)"}


def _indicator_bollinger(closes: List[float]) -> Dict[str, Any]:
    """Bollinger Bands(20, 2): %B posição dentro do canal."""
    period = 20
    if len(closes) < period:
        return {"signal": "NEUTRO", "value": 50.0, "label": "Meio Canal", "name": "Bollinger"}
    window = closes[-period:]
    mid = sum(window) / period
    std = _stdev(window)
    if std == 0:
        return {"signal": "NEUTRO", "value": 50.0, "label": "Sem Volatilidade", "name": "Bollinger"}
    upper = mid + 2 * std
    lower = mid - 2 * std
    cur = closes[-1]
    pct_b = (cur - lower) / (upper - lower) * 100  # 0=banda inferior, 100=superior
    # Próximo da banda inferior → potencial CALL (sobrevendido); superior → PUT
    if pct_b < 20:
        signal, label = "CALL", "Banda Inferior"
    elif pct_b > 80:
        signal, label = "PUT", "Banda Superior"
    else:
        signal, label = "NEUTRO", "Meio Canal"
    return {"signal": signal, "value": round(pct_b, 1), "label": label, "name": "Bollinger"}


def _indicator_stochastic(highs: List[float], lows: List[float], closes: List[float]) -> Dict[str, Any]:
    """Estocástico(14,3,3): K vs D, overbought>80, oversold<20."""
    period_k = 14
    smooth_k = 3
    smooth_d = 3
    if len(closes) < period_k + smooth_k:
        return {"signal": "NEUTRO", "value": 50.0, "label": "Neutro", "name": "Estocástico"}

    # Calcula %K bruto
    raw_k = []
    for i in range(period_k - 1, len(closes)):
        h = max(highs[i - period_k + 1: i + 1])
        l = min(lows[i - period_k + 1: i + 1])
        c = closes[i]
        if h == l:
            raw_k.append(50.0)
        else:
            raw_k.append((c - l) / (h - l) * 100)

    # %K suavizado
    if len(raw_k) < smooth_k:
        return {"signal": "NEUTRO", "value": 50.0, "label": "Neutro", "name": "Estocástico"}
    k_val = sum(raw_k[-smooth_k:]) / smooth_k

    # %D suavizado
    if len(raw_k) < smooth_k + smooth_d - 1:
        d_val = k_val
    else:
        k_series = []
        for j in range(smooth_k - 1, len(raw_k)):
            k_series.append(sum(raw_k[j - smooth_k + 1: j + 1]) / smooth_k)
        d_val = sum(k_series[-smooth_d:]) / smooth_d

    if k_val < 20 and k_val > d_val:
        signal, label = "CALL", "Sobrevendido"
    elif k_val > 80 and k_val < d_val:
        signal, label = "PUT", "Sobrecomprado"
    elif k_val > d_val:
        signal, label = "CALL", "Neutro"
    elif k_val < d_val:
        signal, label = "PUT", "Neutro"
    else:
        signal, label = "NEUTRO", "Neutro"

    return {
        "signal": signal,
        "value": round(k_val, 1),
        "label": label,
        "name": "Estocástico",
        "extra": f"K:{round(k_val, 0):.0f} / D:{round(d_val, 0):.0f}",
    }


def _indicator_adx(highs: List[float], lows: List[float], closes: List[float]) -> Dict[str, Any]:
    """ADX(14): >25 = tendência forte. DI+ vs DI- determina direção."""
    period = 14
    if len(closes) < period + 2:
        return {"signal": "NEUTRO", "value": 0.0, "label": "Sem Tendência", "name": "ADX Força"}

    trs, dm_plus, dm_minus = [], [], []
    for i in range(1, len(closes)):
        tr = _true_range(highs[i], lows[i], closes[i - 1])
        up_move = highs[i] - highs[i - 1]
        dn_move = lows[i - 1] - lows[i]
        trs.append(tr)
        dm_plus.append(up_move if up_move > dn_move and up_move > 0 else 0.0)
        dm_minus.append(dn_move if dn_move > up_move and dn_move > 0 else 0.0)

    # Wilder smoothing
    alpha = 1.0 / period
    atr_w = trs[0]
    dip_w = dm_plus[0]
    dim_w = dm_minus[0]
    dx_vals = []
    for i in range(1, len(trs)):
        atr_w = alpha * trs[i] + (1 - alpha) * atr_w
        dip_w = alpha * dm_plus[i] + (1 - alpha) * dip_w
        dim_w = alpha * dm_minus[i] + (1 - alpha) * dim_w
        if atr_w == 0:
            continue
        di_plus = 100 * dip_w / atr_w
        di_minus = 100 * dim_w / atr_w
        di_sum = di_plus + di_minus
        if di_sum > 0:
            dx_vals.append(100 * abs(di_plus - di_minus) / di_sum)

    if not dx_vals:
        return {"signal": "NEUTRO", "value": 0.0, "label": "Sem Dados", "name": "ADX Força"}

    adx_val = sum(dx_vals[-period:]) / min(period, len(dx_vals))

    # Detecta direção pelo DI+ vs DI- mais recente
    # Re-calcula para o último período
    if len(trs) >= 2:
        atr_last = sum(trs[-period:]) / min(period, len(trs))
        dip_last = sum(dm_plus[-period:]) / min(period, len(dm_plus))
        dim_last = sum(dm_minus[-period:]) / min(period, len(dm_minus))
        di_plus_last = 100 * dip_last / atr_last if atr_last > 0 else 0
        di_minus_last = 100 * dim_last / atr_last if atr_last > 0 else 0
        direction_up = di_plus_last > di_minus_last
    else:
        direction_up = True

    if adx_val >= 25:
        signal = "CALL" if direction_up else "PUT"
        label = "Tendência Forte"
    elif adx_val >= 15:
        signal = "NEUTRO"
        label = "Tendência Fraca"
    else:
        signal = "NEUTRO"
        label = "Sem Tendência"

    return {"signal": signal, "value": round(adx_val, 1), "label": label, "name": "ADX Força"}


def _indicator_volume(volumes: List[float]) -> Dict[str, Any]:
    """Volume: compara volume atual vs média 20. Acima da média = confirma movimento."""
    if len(volumes) < 5:
        return {"signal": "NEUTRO", "value": 1.0, "label": "Volume Normal", "name": "Volume"}
    period = min(20, len(volumes) - 1)
    avg = sum(volumes[-period - 1: -1]) / period if period > 0 else volumes[-1]
    cur = volumes[-1]
    ratio = cur / avg if avg > 0 else 1.0
    # Volume alto por si só é neutro — precisa de contexto, mas indicamos como informação
    if ratio >= 2.0:
        signal, label = "PUT", "Volume Alto"  # Volume extremo geralmente marca exaustão
    elif ratio >= 1.3:
        signal, label = "CALL", "Volume Alto"  # Volume moderadamente alto = momentum
    elif ratio < 0.7:
        signal, label = "NEUTRO", "Volume Baixo"
    else:
        signal, label = "NEUTRO", "Volume Normal"
    return {"signal": signal, "value": round(ratio, 2), "label": label, "name": "Volume"}


def _indicator_atr(highs: List[float], lows: List[float], closes: List[float]) -> Dict[str, Any]:
    """ATR(14): volatilidade. Alto ATR = movimento forte (acompanha tendência), baixo = compressão."""
    period = 14
    if len(closes) < period + 1:
        return {"signal": "NEUTRO", "value": 0.0, "label": "Volatilidade", "name": "ATR"}
    trs = [_true_range(highs[i], lows[i], closes[i - 1]) for i in range(1, len(closes))]
    atr = sum(trs[-period:]) / min(period, len(trs))
    # Normaliza em pips (1 pip = 0.01 USD para BTC)
    atr_pips = atr / 0.01 if atr > 0 else 0.0
    # ATR alto em contexto de tendência = confirma. Sempre retornamos como NEUTRO
    # (ATR é melhor usado como filtro, não como direção)
    signal = "NEUTRO"
    label = "Volatilidade"
    return {"signal": signal, "value": round(atr, 2), "label": label, "name": "ATR"}


def _indicator_macro_m1(closes: List[float]) -> Dict[str, Any]:
    """Macro M1: momentum das últimas 5 velas fechadas. Maioria verde = CALL, vermelha = PUT."""
    if len(closes) < 7:
        return {"signal": "NEUTRO", "value": 0.0, "label": "NEUTRO", "name": "Macro (M1)"}
    # Conta velas verdes (close > open) nas últimas 5
    # Como só temos closes, usa retorno sequencial como proxy
    last5 = closes[-6:]  # 6 fechamentos = 5 retornos
    returns = [last5[i] - last5[i - 1] for i in range(1, len(last5))]
    bulls = sum(1 for r in returns if r > 0)
    bears = sum(1 for r in returns if r < 0)
    mom_bps = (closes[-1] - closes[-6]) / closes[-6] * 10000 if closes[-6] > 0 else 0.0
    if bulls >= 4 or mom_bps > 5:
        signal, label = "CALL", "FORTE"
    elif bears >= 4 or mom_bps < -5:
        signal, label = "PUT", "FORTE"
    elif bulls > bears:
        signal, label = "CALL", "FRACO"
    elif bears > bulls:
        signal, label = "PUT", "FRACO"
    else:
        signal, label = "NEUTRO", "LATERAL"
    return {"signal": signal, "value": round(mom_bps, 1), "label": label, "name": "Macro (M1)"}


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------

def compute_all_indicators(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calcula os 10 indicadores técnicos sobre o buffer de velas FECHADAS.

    Args:
        candles: Lista de dict com chaves open, high, low, close, volume, open_time.
                 Deve conter apenas velas FECHADAS (is_closed=True ou buffer histórico).

    Returns:
        dict com chaves:
          rsi, macd, ema_cross, sma_cross, bollinger, stochastic,
          adx, volume, atr, macro_m1,
          summary: { call, put, neutro, verdict, verdict_label, pct }
          order_flow: { net_delta_pct, buy_pct, sell_pct }
    """
    if len(candles) < 20:
        return _empty_result()

    try:
        closes = [float(c.get("close", 0)) for c in candles]
        highs = [float(c.get("high", 0)) for c in candles]
        lows = [float(c.get("low", 0)) for c in candles]
        volumes = [float(c.get("volume", 0)) for c in candles]
        taker_buy = [float(c.get("taker_buy_base", volumes[i] * 0.5)) for i, c in enumerate(candles)]
    except (TypeError, ValueError) as e:
        log.warning("Erro ao converter dados de velas para indicadores: %s", e)
        return _empty_result()

    # Filtra zeros inválidos
    if not closes or closes[-1] <= 0:
        return _empty_result()

    # Calcula cada indicador (causalidade estrita: só fechados)
    rsi_ind = _indicator_rsi(closes)
    macd_ind = _indicator_macd(closes)
    ema_ind = _indicator_ema_cross(closes)
    sma_ind = _indicator_sma_cross(closes)
    boll_ind = _indicator_bollinger(closes)
    stoch_ind = _indicator_stochastic(highs, lows, closes)
    adx_ind = _indicator_adx(highs, lows, closes)
    vol_ind = _indicator_volume(volumes)
    atr_ind = _indicator_atr(highs, lows, closes)
    macro_ind = _indicator_macro_m1(closes)

    indicators = {
        "rsi": rsi_ind,
        "macd": macd_ind,
        "ema_cross": ema_ind,
        "sma_cross": sma_ind,
        "bollinger": boll_ind,
        "stochastic": stoch_ind,
        "adx": adx_ind,
        "volume": vol_ind,
        "atr": atr_ind,
        "macro_m1": macro_ind,
    }

    # Contagem de votos (exclui ATR que não vota em direção)
    voting_keys = ["rsi", "macd", "ema_cross", "sma_cross", "bollinger", "stochastic", "adx", "volume", "macro_m1"]
    call_count = sum(1 for k in voting_keys if indicators[k]["signal"] == "CALL")
    put_count = sum(1 for k in voting_keys if indicators[k]["signal"] == "PUT")
    neutro_count = sum(1 for k in voting_keys if indicators[k]["signal"] == "NEUTRO")
    total_voting = len(voting_keys)

    # Veredito
    if call_count >= 7:
        verdict, verdict_label = "CALL", "FORTE COMPRA"
        pct = round(call_count / total_voting * 100)
    elif call_count >= 5:
        verdict, verdict_label = "CALL", "COMPRA"
        pct = round(call_count / total_voting * 100)
    elif put_count >= 7:
        verdict, verdict_label = "PUT", "FORTE VENDA"
        pct = round(put_count / total_voting * 100)
    elif put_count >= 5:
        verdict, verdict_label = "PUT", "VENDA"
        pct = round(put_count / total_voting * 100)
    else:
        verdict, verdict_label = "NEUTRO", "LATERAL / NEUTRO"
        pct = round(neutro_count / total_voting * 100)

    # Order Flow: CVD (Delta de Volume Comprador Agressivo) das últimas 5 velas
    sell_vols = [volumes[i] - taker_buy[i] for i in range(len(volumes))]
    last_buy = sum(taker_buy[-5:]) if len(taker_buy) >= 5 else taker_buy[-1] if taker_buy else 0
    last_sell = sum(sell_vols[-5:]) if len(sell_vols) >= 5 else sell_vols[-1] if sell_vols else 0
    total_vol = last_buy + last_sell
    buy_pct = round(last_buy / total_vol * 100) if total_vol > 0 else 50
    sell_pct = 100 - buy_pct
    net_delta_pct = buy_pct - sell_pct  # positivo = pressão compradora

    indicators["summary"] = {
        "call": call_count,
        "put": put_count,
        "neutro": neutro_count,
        "verdict": verdict,
        "verdict_label": verdict_label,
        "pct": pct,
        "total": total_voting,
    }
    indicators["order_flow"] = {
        "net_delta_pct": net_delta_pct,
        "buy_pct": buy_pct,
        "sell_pct": sell_pct,
    }

    return indicators


def _empty_result() -> Dict[str, Any]:
    """Retorna estrutura vazia quando não há dados suficientes."""
    empty_ind = {"signal": "NEUTRO", "value": 0.0, "label": "Aguardando dados..."}
    return {
        "rsi": {**empty_ind, "name": "RSI (14)"},
        "macd": {**empty_ind, "name": "MACD"},
        "ema_cross": {**empty_ind, "name": "EMA (9/21)"},
        "sma_cross": {**empty_ind, "name": "SMA (50/200)"},
        "bollinger": {**empty_ind, "name": "Bollinger"},
        "stochastic": {**empty_ind, "name": "Estocástico"},
        "adx": {**empty_ind, "name": "ADX Força"},
        "volume": {**empty_ind, "name": "Volume"},
        "atr": {**empty_ind, "name": "ATR"},
        "macro_m1": {**empty_ind, "name": "Macro (M1)"},
        "summary": {
            "call": 0, "put": 0, "neutro": 9,
            "verdict": "NEUTRO", "verdict_label": "Coletando Dados...",
            "pct": 0, "total": 9,
        },
        "order_flow": {"net_delta_pct": 0, "buy_pct": 50, "sell_pct": 50},
    }
