"""
research/fetch_fresh.py
Baixa dados NOVOS (posteriores aos conjuntos ja usados nas auditorias antigas, que terminam em 2026-09-13)
para validar confluencias M5 / 15s fora da amostra.

Uso:
    python research/fetch_fresh.py 1m          # Spot + Perp M1 desde 2026-09-14 ate agora
    python research/fetch_fresh.py 1s 7        # Spot 1s dos ultimos 7 dias
"""
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

OUT = Path(__file__).resolve().parent.parent / "audit_data"
OUT.mkdir(parents=True, exist_ok=True)

SPOT = "https://api.binance.com/api/v3/klines"
PERP = "https://fapi.binance.com/fapi/v1/klines"
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "qav",
        "num_trades", "taker_buy_base", "taker_buy_quote", "ignore"]


def fetch(url: str, interval: str, start_ms: int, end_ms: int, step_ms: int) -> pd.DataFrame:
    rows, cur, n_req = [], start_ms, 0
    while cur < end_ms:
        data = None
        for _ in range(6):
            try:
                r = requests.get(url, params={"symbol": "BTCUSDT", "interval": interval,
                                              "startTime": cur, "limit": 1000}, timeout=20)
                if r.status_code in (418, 429):
                    time.sleep(10)
                    continue
                r.raise_for_status()
                data = r.json()
                break
            except Exception:
                time.sleep(2)
        if data is None:
            raise RuntimeError("falha repetida ao baixar klines")
        if not data:
            break
        rows.extend(data)
        cur = int(data[-1][0]) + step_ms
        n_req += 1
        if n_req % 50 == 0:
            print(f"  {interval}: {len(rows)} linhas ...", flush=True)
        if len(data) < 1000:
            break
        time.sleep(0.05)
    df = pd.DataFrame(rows, columns=COLS)
    for c in ["open", "high", "low", "close", "volume", "taker_buy_base"]:
        df[c] = df[c].astype(float)
    df["open_time"] = df["open_time"].astype("int64")
    df["close_time"] = df["close_time"].astype("int64")
    df["num_trades"] = df["num_trades"].astype("int64")
    # descarta barra ainda aberta
    now_ms = int(time.time() * 1000)
    df = df[df["close_time"] < now_ms].drop(columns=["qav", "taker_buy_quote", "ignore"])
    return df.drop_duplicates("open_time").reset_index(drop=True)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "1m"
    now_ms = int(time.time() * 1000)
    if mode == "1m":
        start = int(datetime(2026, 9, 14, tzinfo=timezone.utc).timestamp() * 1000)
        for name, url in (("spot", SPOT), ("perp", PERP)):
            print(f"Baixando {name} 1m ...", flush=True)
            df = fetch(url, "1m", start, now_ms, 60_000)
            df.to_parquet(OUT / f"fresh_{name}_1m.parquet")
            print(f"OK {name}: {len(df)} velas  {pd.to_datetime(df.open_time.iloc[0], unit='ms')} -> "
                  f"{pd.to_datetime(df.open_time.iloc[-1], unit='ms')}", flush=True)
    elif mode == "1s":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
        start = now_ms - days * 86_400_000
        start = (start // 60_000) * 60_000
        print(f"Baixando spot 1s ({days} dias) ...", flush=True)
        df = fetch(SPOT, "1s", start, now_ms, 1000)
        df.to_parquet(OUT / "fresh_spot_1s.parquet")
        print(f"OK spot 1s: {len(df)} linhas", flush=True)
    print("FIM", flush=True)
