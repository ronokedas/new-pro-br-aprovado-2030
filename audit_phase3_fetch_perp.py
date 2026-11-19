"""
audit_phase3_fetch_perp.py
==========================
Coleta causal e auditavel de dados Binance USD-M Futures (Perp BTCUSDT)
para a Fase 3 do protocolo (prompat.txt):
  1. GET https://fapi.binance.com/fapi/v1/klines (interval=1m, mesma janela do Spot 65d)
  2. GET https://fapi.binance.com/futures/data/openInterestHist (period=5m, ultimos ~29d)
"""
import time
from pathlib import Path
import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parent
OUT_DIR = REPO_ROOT / "audit_data"
OUT_DIR.mkdir(exist_ok=True)

SPOT_PARQUET = Path(r"C:\trading-agents\btc_color_research\data\BTCUSDT_1m_65d.parquet")
PERP_OUT = OUT_DIR / "BTCUSDT_perp_1m_65d.parquet"
OI_OUT = OUT_DIR / "BTCUSDT_oi_5m.parquet"

KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_asset_volume", "num_trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]


def _get(url: str, params: dict, retries: int = 5):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 30))
                print(f"Rate limited, aguardando {wait}s...")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))


def fetch_perp_klines(start_ms: int, end_ms: int) -> pd.DataFrame:
    if PERP_OUT.exists():
        print(f"Usando cache Perp: {PERP_OUT}")
        return pd.read_parquet(PERP_OUT)

    rows = []
    cur = start_ms
    req_count = 0
    while cur <= end_ms:
        data = _get(
            "https://fapi.binance.com/fapi/v1/klines",
            {"symbol": "BTCUSDT", "interval": "1m", "startTime": cur, "endTime": end_ms, "limit": 1500},
        )
        req_count += 1
        if not data:
            break
        rows.extend(data)
        last_open = int(data[-1][0])
        if last_open <= cur:
            break
        cur = last_open + 60_000
        time.sleep(0.05)

    df = pd.DataFrame(rows, columns=KLINE_COLS)
    for col in ["open", "high", "low", "close", "volume", "quote_asset_volume", "taker_buy_base", "taker_buy_quote"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["num_trades"] = pd.to_numeric(df["num_trades"], errors="coerce").astype("Int64")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    df.to_parquet(PERP_OUT, index=False)
    print(f"Perp 1m salvo: {len(df)} linhas ({req_count} requests) | {df['open_time'].iloc[0]} -> {df['open_time'].iloc[-1]}")
    return df


def fetch_oi_5m(end_ms: int, days: int = 29) -> pd.DataFrame:
    if OI_OUT.exists():
        print(f"Usando cache OI 5m: {OI_OUT}")
        return pd.read_parquet(OI_OUT)

    start_ms = end_ms - days * 86_400_000
    rows = []
    cur_end = end_ms
    req_count = 0
    # Pagina de trás para frente com endTime para respeitar o limite de 30d da Binance
    while cur_end > start_ms:
        try:
            data = _get(
                "https://fapi.binance.com/futures/data/openInterestHist",
                {"symbol": "BTCUSDT", "period": "5m", "endTime": cur_end, "limit": 500},
            )
        except Exception as e:
            print(f"Fim/erro na paginacao OI 5m em {cur_end}: {e}")
            break
        req_count += 1
        if not data:
            break
        rows.extend(data)
        first_ts = int(data[0]["timestamp"])
        if first_ts >= cur_end:
            break
        cur_end = first_ts - 1
        if len(data) < 500:
            break
        time.sleep(0.08)

    if not rows:
        print("Nenhum registro OI 5m retornado.")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df["sumOpenInterest"] = pd.to_numeric(df["sumOpenInterest"], errors="coerce")
    df["sumOpenInterestValue"] = pd.to_numeric(df["sumOpenInterestValue"], errors="coerce")
    df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    df.to_parquet(OI_OUT, index=False)
    print(f"OI 5m salvo: {len(df)} linhas ({req_count} requests) | {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}")
    return df


def main():
    spot = pd.read_parquet(SPOT_PARQUET)
    start_ms = int(spot["open_time"].iloc[0].timestamp() * 1000)
    end_ms = int(spot["open_time"].iloc[-1].timestamp() * 1000)
    print(f"Janela Spot alvo: {spot['open_time'].iloc[0]} -> {spot['open_time'].iloc[-1]} ({len(spot)} linhas)")
    fetch_perp_klines(start_ms, end_ms)
    fetch_oi_5m(end_ms, days=29)


if __name__ == "__main__":
    main()
