"""
audit_fetch_extended.py
=======================
Baixa dados públicos causais da Binance para:
  1. NOVO HOLDOUT VIRGEM DE 30 DIAS (2026-08-14 a 2026-09-13 — NUNCA TOCADO ANTES!):
     - BTCUSDT Spot 1m
     - BTCUSDT Perp 1m
     - BTCUSDC Spot 1m
  2. BTCUSDC Spot 1m na janela de 65 dias existente (2026-09-13 a 2026-11-17)
     para testar dislocação multi-venue (Perp + BTCUSDC vs BTCUSDT Spot).
"""
import time
from pathlib import Path
import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parent
OUT_DIR = REPO_ROOT / "audit_data"
OUT_DIR.mkdir(exist_ok=True)

SPOT_65D = Path(r"C:\trading-agents\btc_color_research\data\BTCUSDT_1m_65d.parquet")

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
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))


def fetch_klines(url: str, symbol: str, start_ms: int, end_ms: int, out_path: Path, limit: int = 1000) -> pd.DataFrame:
    if out_path.exists():
        print(f"Cache OK: {out_path.name}")
        return pd.read_parquet(out_path)

    rows = []
    cur = start_ms
    reqs = 0
    while cur <= end_ms:
        data = _get(url, {"symbol": symbol, "interval": "1m", "startTime": cur, "endTime": end_ms, "limit": limit})
        reqs += 1
        if not data:
            break
        rows.extend(data)
        last_open = int(data[-1][0])
        if last_open <= cur:
            break
        cur = last_open + 60_000
        time.sleep(0.03)

    df = pd.DataFrame(rows, columns=KLINE_COLS)
    for col in ["open", "high", "low", "close", "volume", "quote_asset_volume", "taker_buy_base", "taker_buy_quote"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["num_trades"] = pd.to_numeric(df["num_trades"], errors="coerce").astype("Int64")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    df.to_parquet(out_path, index=False)
    print(f"Salvo {out_path.name}: {len(df)} linhas ({reqs} reqs) | {df['open_time'].iloc[0]} -> {df['open_time'].iloc[-1]}")
    return df


def main():
    spot65 = pd.read_parquet(SPOT_65D)
    s65_start = int(spot65["open_time"].iloc[0].timestamp() * 1000)
    s65_end = int(spot65["open_time"].iloc[-1].timestamp() * 1000)

    # Novo Holdout Virgem: 30 dias imediatamente anteriores ao dataset de 65d (+14d warmup interno para rolling/quantis)
    # Baixamos 45 dias antes de s65_start: 15 dias para warmup/treino de quantis + 30 dias exatos de Holdout Virgem!
    virgin_end = s65_start - 60_000
    virgin_start = virgin_end - 45 * 86_400_000

    SPOT_URL = "https://api.binance.com/api/v3/klines"
    PERP_URL = "https://fapi.binance.com/fapi/v1/klines"

    print("=== 1. Baixando BTCUSDC Spot (65d existentes) ===")
    fetch_klines(SPOT_URL, "BTCUSDC", s65_start, s65_end, OUT_DIR / "BTCUSDC_spot_1m_65d.parquet", limit=1000)

    print("=== 2. Baixando NOVO HOLDOUT VIRGEM (45d anteriores: 15d warmup + 30d virgem) ===")
    fetch_klines(SPOT_URL, "BTCUSDT", virgin_start, virgin_end, OUT_DIR / "BTCUSDT_spot_1m_virgin45d.parquet", limit=1000)
    fetch_klines(PERP_URL, "BTCUSDT", virgin_start, virgin_end, OUT_DIR / "BTCUSDT_perp_1m_virgin45d.parquet", limit=1500)
    fetch_klines(SPOT_URL, "BTCUSDC", virgin_start, virgin_end, OUT_DIR / "BTCUSDC_spot_1m_virgin45d.parquet", limit=1000)


if __name__ == "__main__":
    main()
