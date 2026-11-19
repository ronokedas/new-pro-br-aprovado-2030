"""
audit_live_multivenue_leadlag.py
================================
Medicao AO VIVO (60 segundos) com TIMESTAMPS LOCAIS DE CHEGADA (100% causal, prompat.txt Secao 2 item 7):
Conecta simultaneamente aos feeds BBO/Ticker mais rapidos de 5 venues:
  1. Binance Spot  (wss://stream.binance.com:9443/ws/btcusdt@bookTicker + btcusdt@kline_1m)
  2. Binance Perp  (wss://fstream.binance.com/ws/btcusdt@bookTicker)
  3. OKX Perp      (wss://ws.okx.com:8443/ws/v5/public -> bbo-tbt BTC-USDT-SWAP)
  4. Bybit Perp    (wss://stream.bybit.com/v5/public/linear -> orderbook.1.BTCUSDT)
  5. Coinbase Spot (wss://ws-feed.exchange.coinbase.com -> ticker BTC-USD)

Mede empiricamente (NADA inventado):
  - Frequencia real de ticks (ticks/s) de cada venue
  - Atraso real de chegada do evento kline_1m (k.x=True) vs virada exata do minuto (:00.000)
  - Cross-correlation (lead-lag real em lags de -1000ms a +1000ms, passo 100ms) usando
    ESTRITAMENTE timestamps locais de chegada (perf_counter) contra Binance Spot mid-price!
  - Previsibilidade de 1s e 5s da variacao do lider em [t-1s, t] -> retorno Binance Spot em [t, t+1s] e [t, t+5s].
"""
import asyncio
import json
import time
import numpy as np
import aiohttp

DURATION_SEC = 65.0

ticks = {
    "binance_spot": [],
    "binance_perp": [],
    "okx_perp": [],
    "bybit_perp": [],
    "coinbase_spot": [],
}
kline_close_delays_ms = []
start_wall = 0.0
start_perf = 0.0


def _rec(venue: str, bid: float, ask: float, exch_ts_ms: float | None = None):
    if bid <= 0 or ask <= 0:
        return
    t_rel = time.perf_counter() - start_perf
    mid = 0.5 * (bid + ask)
    wall_ms = time.time() * 1000.0
    lat_ms = (wall_ms - exch_ts_ms) if exch_ts_ms else np.nan
    ticks[venue].append((t_rel, mid, ask - bid, lat_ms))


async def feed_binance_spot(session: aiohttp.ClientSession, stop_at: float):
    url = "wss://stream.binance.com:9443/stream?streams=btcusdt@bookTicker/btcusdt@kline_1m"
    try:
        async with session.ws_connect(url, timeout=15.0) as ws:
            async for msg in ws:
                if time.perf_counter() >= stop_at:
                    break
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                d = json.loads(msg.data)
                stream = d.get("stream", "")
                p = d.get("data", {})
                if "bookTicker" in stream:
                    _rec("binance_spot", float(p["b"]), float(p["a"]), None)
                elif "kline" in stream:
                    k = p.get("k", {})
                    if k.get("x", False):
                        # Atraso entre close_time oficial (T+1ms = :00.000) e chegada local
                        delay = time.time() * 1000.0 - (int(k["T"]) + 1)
                        kline_close_delays_ms.append(delay)
    except Exception as e:
        print(f"[binance_spot ERR] {e}")


async def feed_binance_perp(session: aiohttp.ClientSession, stop_at: float):
    url = "wss://fstream.binance.com/ws/btcusdt@bookTicker"
    try:
        async with session.ws_connect(url, timeout=15.0) as ws:
            async for msg in ws:
                if time.perf_counter() >= stop_at:
                    break
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                p = json.loads(msg.data)
                if "b" in p and "a" in p:
                    _rec("binance_perp", float(p["b"]), float(p["a"]), float(p.get("E", 0)) or None)
    except Exception as e:
        print(f"[binance_perp ERR] {e}")


async def feed_okx_perp(session: aiohttp.ClientSession, stop_at: float):
    url = "wss://ws.okx.com:8443/ws/v5/public"
    try:
        async with session.ws_connect(url, timeout=15.0) as ws:
            await ws.send_json({"op": "subscribe", "args": [{"channel": "bbo-tbt", "instId": "BTC-USDT-SWAP"}]})
            async for msg in ws:
                if time.perf_counter() >= stop_at:
                    break
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                d = json.loads(msg.data)
                data = d.get("data")
                if data:
                    row = data[0]
                    bids = row.get("bids", [])
                    asks = row.get("asks", [])
                    if bids and asks:
                        ts = float(row.get("ts", 0)) or None
                        _rec("okx_perp", float(bids[0][0]), float(asks[0][0]), ts)
    except Exception as e:
        print(f"[okx_perp ERR] {e}")


async def feed_bybit_perp(session: aiohttp.ClientSession, stop_at: float):
    url = "wss://stream.bybit.com/v5/public/linear"
    try:
        async with session.ws_connect(url, timeout=15.0) as ws:
            await ws.send_json({"op": "subscribe", "args": ["orderbook.1.BTCUSDT"]})
            best_b = 0.0
            best_a = 0.0
            async for msg in ws:
                if time.perf_counter() >= stop_at:
                    break
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                d = json.loads(msg.data)
                if d.get("topic") == "orderbook.1.BTCUSDT":
                    data = d.get("data", {})
                    if data.get("b"):
                        best_b = float(data["b"][0][0])
                    if data.get("a"):
                        best_a = float(data["a"][0][0])
                    if best_b > 0 and best_a > 0:
                        ts = float(d.get("ts", 0)) or None
                        _rec("bybit_perp", best_b, best_a, ts)
    except Exception as e:
        print(f"[bybit_perp ERR] {e}")


async def feed_coinbase_spot(session: aiohttp.ClientSession, stop_at: float):
    url = "wss://ws-feed.exchange.coinbase.com"
    try:
        async with session.ws_connect(url, timeout=15.0) as ws:
            await ws.send_json({"type": "subscribe", "product_ids": ["BTC-USD"], "channels": ["ticker"]})
            async for msg in ws:
                if time.perf_counter() >= stop_at:
                    break
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                d = json.loads(msg.data)
                if d.get("type") == "ticker" and "best_bid" in d and "best_ask" in d:
                    _rec("coinbase_spot", float(d["best_bid"]), float(d["best_ask"]), None)
    except Exception as e:
        print(f"[coinbase_spot ERR] {e}")


def resample_grid(venue_ticks: list, grid_t: np.ndarray) -> np.ndarray:
    """Forward-fill estritamente causal: para cada t da grade, usa o último tick chegado <= t."""
    if not venue_ticks:
        return np.full(len(grid_t), np.nan)
    ts = np.array([x[0] for x in venue_ticks], dtype=float)
    mids = np.array([x[1] for x in venue_ticks], dtype=float)
    idx = np.searchsorted(ts, grid_t, side="right") - 1
    out = np.where(idx >= 0, mids[np.maximum(idx, 0)], np.nan)
    return out


async def main():
    global start_wall, start_perf
    print(f"Coletando ticks BBO ao vivo de 5 venues por {DURATION_SEC:.0f}s (timestamps locais de chegada)...")
    start_wall = time.time()
    start_perf = time.perf_counter()
    stop_at = start_perf + DURATION_SEC

    async with aiohttp.ClientSession() as session:
        await asyncio.gather(
            feed_binance_spot(session, stop_at),
            feed_binance_perp(session, stop_at),
            feed_okx_perp(session, stop_at),
            feed_bybit_perp(session, stop_at),
            feed_coinbase_spot(session, stop_at),
        )

    print("\n" + "=" * 110)
    print("1. TELEMETRIA REAL DE VELOCIDADE E LATENCIA (A PARTIR DESTA MAQUINA)")
    print("=" * 110)
    for v, arr in ticks.items():
        if not arr:
            print(f"  {v:<16}: 0 ticks (FALHOU/BLOQUEADO)")
            continue
        rate = len(arr) / DURATION_SEC
        spreads_bps = np.mean([x[2] / x[1] * 1e4 for x in arr])
        lats = [x[3] for x in arr if not np.isnan(x[3])]
        lat_str = f"lat_relogio={np.median(lats):+.0f}ms" if lats else "lat_relogio=N/A"
        print(f"  {v:<16}: {len(arr):>6} ticks ({rate:6.1f} ticks/s) | spread medio={spreads_bps:.3f}bp | {lat_str}")

    if kline_close_delays_ms:
        print(f"\n  Atraso medido do evento kline_1m (k.x=True) apos :00.000: {[round(x, 1) for x in kline_close_delays_ms]} ms")
    else:
        print("\n  Atraso kline_1m: nenhum fechamento capturado na janela.")

    # Grade de 50ms a partir de t=3s (apos todos os WS conectarem)
    grid = np.arange(3.0, DURATION_SEC - 1.0, 0.05)  # 50ms
    series = {v: resample_grid(arr, grid) for v, arr in ticks.items() if len(arr) >= 20}
    if "binance_spot" not in series:
        print("Sem dados suficientes de binance_spot.")
        return

    # Retornos em janela de 200ms (4 passos de 50ms) para cross-correlation limpa
    spot = series["binance_spot"]
    valid_all = ~np.isnan(spot)
    for v in series:
        valid_all &= ~np.isnan(series[v])

    if valid_all.sum() < 200:
        print("Sobreposicao insuficiente entre venues.")
        return

    first_valid = int(np.argmax(valid_all))
    spot_c = spot[first_valid:]
    ret_spot_200ms = np.diff(np.log(spot_c), n=1)  # 50ms returns

    print("\n" + "=" * 110)
    print("2. LEAD-LAG REAL EM MILISSEGUNDOS (TIMESTAMPS LOCAIS DE CHEGADA — PASSO 50ms)")
    print("   Lag > 0ms significa: o VENUE se moveu X ms ANTES da Binance Spot chegar aqui!")
    print("=" * 110)
    lags_steps = list(range(-10, 11))  # -500ms a +500ms em passos de 50ms
    for v, arr in series.items():
        if v == "binance_spot":
            continue
        vc = arr[first_valid:]
        ret_v = np.diff(np.log(vc), n=1)
        corrs = {}
        for k in lags_steps:
            if k > 0:
                x, y = ret_v[:-k], ret_spot_200ms[k:]
            elif k < 0:
                x, y = ret_v[-k:], ret_spot_200ms[:k]
            else:
                x, y = ret_v, ret_spot_200ms
            if np.std(x) > 0 and np.std(y) > 0:
                corrs[k * 50] = float(np.corrcoef(x, y)[0, 1])
            else:
                corrs[k * 50] = 0.0
        best_lag = max(corrs, key=lambda lg: corrs[lg])
        c0 = corrs[0]
        c_p50 = corrs[50]
        c_p100 = corrs[100]
        c_p200 = corrs[200]
        c_m100 = corrs[-100]
        print(f"  {v:<16} -> binance_spot | pico={best_lag:+4d}ms (r={corrs[best_lag]:+.3f}) | "
              f"r(-100ms)={c_m100:+.3f} r(0ms)={c0:+.3f} r(+50ms)={c_p50:+.3f} r(+100ms)={c_p100:+.3f} r(+200ms)={c_p200:+.3f}")

    # 3. Previsao causal em horizonte de 1s e 5s: ret_lider[t-1s, t] -> ret_spot[t, t+1s] e [t, t+5s]
    print("\n" + "=" * 110)
    print("3. PODER PREDITIVO CAUSAL (ret_lider[t-1s, t] -> ret_binance_spot[t, t+H])")
    print("=" * 110)
    # 1s = 20 passos de 50ms, 5s = 100 passos
    for v, arr in series.items():
        if v == "binance_spot":
            continue
        vc = arr[first_valid:]
        n_g = len(vc)
        if n_g < 250:
            continue
        r_lead_1s = np.log(vc[20:-100] / vc[:-120])
        r_spot_next_1s = np.log(spot_c[40:-80] / spot_c[20:-100])
        r_spot_next_5s = np.log(spot_c[120:] / spot_c[20:-100])
        # Acertos direcionais quando líder moveu > 0.3 bp no último 1s
        nz = np.abs(r_lead_1s) > 3e-5
        if nz.sum() > 20:
            corr1 = float(np.corrcoef(r_lead_1s, r_spot_next_1s)[0, 1]) if np.std(r_lead_1s) > 0 and np.std(r_spot_next_1s) > 0 else 0.0
            corr5 = float(np.corrcoef(r_lead_1s, r_spot_next_5s)[0, 1]) if np.std(r_lead_1s) > 0 and np.std(r_spot_next_5s) > 0 else 0.0
            acc1 = float((np.sign(r_lead_1s[nz]) == np.sign(r_spot_next_1s[nz])).mean())
            acc5 = float((np.sign(r_lead_1s[nz]) == np.sign(r_spot_next_5s[nz])).mean())
            print(f"  {v:<16} | N_mov(>0.3bp)={int(nz.sum()):>4} | H=1s: r={corr1:+.3f} acc={acc1*100:5.1f}% | H=5s: r={corr5:+.3f} acc={acc5*100:5.1f}%")
        else:
            print(f"  {v:<16} | poucos movimentos >0.3bp nesta amostra de 65s (N={int(nz.sum())})")


if __name__ == "__main__":
    asyncio.run(main())
