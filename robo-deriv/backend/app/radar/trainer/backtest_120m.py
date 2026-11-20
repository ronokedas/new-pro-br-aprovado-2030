"""
app/radar/trainer/backtest_120m.py
==================================
Validação histórica causal das últimas 120 velas M1 FECHADAS do BTCUSDT Spot
reproduzindo fielmente o modo híbrido travado do sistema ao vivo:
  - Passo 1 (Produto A, +0s): REF_basis_z60_Q80 (Spot + Perp fechados). Se disparar CALL/PUT -> trava em +0s.
  - Passo 2 (Produto B, +5s): se Produto A ficou em WAIT, avalia os primeiros 5s reais da vela
    via klines 1s da Binance (Follow_5s) e trava em +5s.
  - Reporta separadamente Produto A (+0s), Produto B (+5s) e o combinado, sem vela em formação
    e medindo o WIN/LOSS tanto pelo PREÇO DE ENTRADA REAL quanto pela COR DA VELA (open->close).
"""
import time
from typing import Any, Dict, List
import requests

from app.radar.engine.basis_signal import compute_basis_signal, compute_product_b_signal

BINANCE_SPOT_URL = "https://api.binance.com/api/v3/klines"
BINANCE_PERP_URL = "https://fapi.binance.com/fapi/v1/klines"


def _fetch_closed_klines(
    url: str,
    interval: str = "1m",
    limit: int = 500,
    start_ms: int = None,
    end_ms: int = None,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {"symbol": "BTCUSDT", "interval": interval, "limit": limit}
    if start_ms is not None:
        params["startTime"] = start_ms
    if end_ms is not None:
        params["endTime"] = end_ms
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    now_ms = int(time.time() * 1000)
    candles = []
    for item in data:
        close_time = int(item[6])
        if close_time >= now_ms:
            continue
        candles.append({
            "open_time": int(item[0]),
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]),
            "close_time": close_time,
            "taker_buy_base": float(item[9]),
            "num_trades": int(item[8]),
        })
    return candles


def _fetch_first_5s_map(start_open_ms: int, end_open_ms: int, wait_sec: int = 5) -> Dict[int, tuple]:
    """Baixa klines 1s para as últimas 120 velas e retorna {minute_open_ms: (ret_first_5s_bps, preco_aos_+5s)}."""
    ret_map: Dict[int, tuple] = {}
    cur = start_open_ms
    all_1s = []
    while cur <= end_open_ms + 59_000:
        chunk = _fetch_closed_klines(
            BINANCE_SPOT_URL,
            interval="1s",
            limit=1000,
            start_ms=cur,
            end_ms=end_open_ms + 59_999,
        )
        if not chunk:
            break
        all_1s.extend(chunk)
        last_t = chunk[-1]["open_time"]
        if last_t <= cur:
            break
        cur = last_t + 1000

    by_min: Dict[int, List[Dict[str, Any]]] = {}
    for row in all_1s:
        m_open = (row["open_time"] // 60_000) * 60_000
        by_min.setdefault(m_open, []).append(row)

    for m_open, rows in by_min.items():
        rows.sort(key=lambda x: x["open_time"])
        first_n = [r for r in rows if (r["open_time"] - m_open) < wait_sec * 1000]
        if len(first_n) >= wait_sec:
            o0 = first_n[0]["open"]
            cn = first_n[-1]["close"]
            if o0 > 0:
                ret_map[m_open] = (((cn - o0) / o0) * 1e4, cn)
    return ret_map


def run_120m_audit(wait_sec: int = 5) -> Dict[str, Any]:
    spot_candles = _fetch_closed_klines(BINANCE_SPOT_URL, interval="1m", limit=500)
    perp_candles = _fetch_closed_klines(BINANCE_PERP_URL, interval="1m", limit=500)

    perp_map = {c["open_time"]: c for c in perp_candles}
    aligned_spot = [c for c in spot_candles if c["open_time"] in perp_map]
    aligned_perp = [perp_map[c["open_time"]] for c in aligned_spot]

    if len(aligned_spot) < 250:
        return {"error": "Dados fechados alinhados Spot+Perp insuficientes"}

    test_range = range(len(aligned_spot) - 121, len(aligned_spot) - 1)
    first_test_ts = aligned_spot[test_range.start + 1]["open_time"]
    last_test_ts = aligned_spot[test_range.stop]["open_time"]

    try:
        ret5s_map = _fetch_first_5s_map(first_test_ts, last_test_ts, wait_sec=wait_sec)
    except Exception:
        ret5s_map = {}

    history = []
    wins = losses = dojis = total_candles = color_wins = 0
    prod_a = {
        "total": 0,
        "wins": 0,
        "losses": 0,
        "dojis": 0,
        "winrate": 0.0,
        "color_wins": 0,
        "color_winrate": 0.0,
    }
    prod_b = {
        "total": 0,
        "wins": 0,
        "losses": 0,
        "dojis": 0,
        "winrate": 0.0,
        "color_wins": 0,
        "color_winrate": 0.0,
    }

    for idx in test_range:
        total_candles += 1
        s_win = aligned_spot[:idx + 1]
        p_win = aligned_perp[:idx + 1]
        next_candle = aligned_spot[idx + 1]
        m_open = next_candle["open_time"]

        # Passo 1: Produto A (+0s)
        analysis = compute_basis_signal(s_win, p_win)
        pred_dir = analysis["direction"]
        prod_tag = "A"
        conf = analysis["confidence_pct"]
        entry_px = next_candle["open"]  # A entra na abertura

        # Passo 2: Produto B seletivo (+5s com filtro FISICA_SNR5s >= 0.50) se Produto A ficou em WAIT
        if pred_dir not in ("CALL", "PUT"):
            r5 = ret5s_map.get(m_open)
            if r5 is None:
                continue
            ret5_bps, px5 = r5
            res_b = compute_product_b_signal(
                {"intra_spot_ret_bps": ret5_bps, "multi_perp_consensus_bps": None},
                spot_candles=s_win,
                l2_snapshot=None,
                wait_sec=wait_sec,
                min_snr=0.50,
            )
            pred_dir = res_b["direction"]
            if pred_dir not in ("CALL", "PUT"):
                continue  # ficou FORA DO MERCADO (vela fraca!)
            prod_tag = "B"
            conf = res_b["confidence_pct"]
            entry_px = px5  # B entra aos +5s: o preço de entrada NÃO é a abertura da vela

        o, cl = next_candle["open"], next_candle["close"]
        color_dir = "CALL" if cl > o else ("PUT" if cl < o else "DOJI")
        # RESULTADO REAL: fechamento contra o PREÇO DE ENTRADA
        actual_dir = "CALL" if cl > entry_px else ("PUT" if cl < entry_px else "DOJI")
        if actual_dir == "DOJI":
            dojis += 1

        is_win = (pred_dir == actual_dir)
        is_color_win = (pred_dir == color_dir)
        if is_win:
            wins += 1
        else:
            losses += 1
        if is_color_win:
            color_wins += 1

        bucket = prod_a if prod_tag == "A" else prod_b
        bucket["total"] += 1
        if actual_dir == "DOJI":
            bucket["dojis"] += 1
        if is_win:
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1
        if is_color_win:
            bucket["color_wins"] += 1
        bucket["winrate"] = round((bucket["wins"] / bucket["total"]) * 100, 1)
        bucket["color_winrate"] = round((bucket["color_wins"] / bucket["total"]) * 100, 1)

        t_str = time.strftime("%H:%M", time.gmtime(m_open / 1000))
        tag_s = "+0s" if prod_tag == "A" else f"+{wait_sec}s"
        history.append({
            "time": f"{t_str} {tag_s}",
            "product": prod_tag,
            "price_open": o,
            "price_entry": entry_px,
            "price_close": cl,
            "predicted": pred_dir,
            "actual": actual_dir,
            "color": color_dir,
            "result": "WIN" if is_win else "LOSS",
            "color_result": "WIN" if is_color_win else "LOSS",
            "confidence": conf,
        })

    total_signals = wins + losses
    winrate = round((wins / total_signals) * 100, 1) if total_signals > 0 else 0.0
    color_winrate = round((color_wins / total_signals) * 100, 1) if total_signals > 0 else 0.0
    coverage = round((total_signals / total_candles) * 100, 1) if total_candles > 0 else 0.0

    return {
        "total_candles": total_candles,
        "total_signals": total_signals,
        "coverage_pct": coverage,
        "wins": wins,
        "losses": losses,
        "dojis": dojis,
        "winrate_pct": winrate,
        "color_winrate_pct": color_winrate,
        "product_a_0s": prod_a,
        "product_b_5s": prod_b,
        "recent_history": history[-15:],
        "full_history": history,
    }
