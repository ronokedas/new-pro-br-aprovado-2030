"""
Auditoria Quantitativa Rigorosa — Modalidade BLITZ (Expiracao 5s e 15s)
- Datasets:
  1) Binance Spot 1s (48h = 172.944s, com volume real e taker_buy_base) -> DEV (24h) vs HOLDOUT (24h)
  2) Deriv cryBTCUSD ticks (24h = 86.247s) -> DEV (12h) vs HOLDOUT (12h)
  3) Binance aggTrades reais (6h = 469.926 trades em resolucao de 1s)
- Regras anti-ilusao (prompat.txt):
  * Cooldown estrito nao-sobreposto (1 trade por vez: apos entrar em t, espera t + H + latencia terminar!)
  * Quantis 100% causais (rolling janela passada com shift(1))
  * Empate (close == entry) contado como ERRO
  * Teste obrigatorio de latencia de clique: 0s (bot perfeito), +1s (clique rapido), +2s (clique humano)
  * Metricas: N, WinEntry, Wilson 95%, Bps, Taxa de Empates (Doji)
"""
import math
import os
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = r"D:\backup importantes\ficar-rico\btc-proxima-vela"
DATA_1S_BIN = r"C:\trading-agents\btc_color_research\data\BTCUSDT_1s_2d.parquet"
DATA_AGG = r"C:\trading-agents\btc_color_research\data\BTCUSDT_aggTrades_6h.parquet"
DATA_DERIV = os.path.join(ROOT, "audit_data", "DERIV_cryBTCUSD_ticks.parquet")


def wilson_ci(k: int, n: int, z: float = 1.959963984540054):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    half = (z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def eval_blitz_nonoverlap(name: str, sig: np.ndarray, prices: np.ndarray, ts_sec: np.ndarray,
                          horizon_s: int, latency_s: int = 0, half_spread_bps: float = 0.0):
    """
    Avalia sinais de Blitz com cooldown nao-sobreposto:
    - Sinal dispara no segundo i (usando apenas dados <= i)
    - Entrada ocorre no segundo i + latency_s (se timestamp exato bater)
    - Expiracao ocorre em (i + latency_s + horizon_s)
    - Proximo trade so pode abrir apos a expiracao do anterior!
    """
    n_tot = len(sig)
    wins = 0
    ties = 0
    trades = 0
    bps_sum = 0.0

    i = 0
    max_i = n_tot - latency_s - horizon_s - 1
    while i <= max_i:
        s = sig[i]
        if s != 0:
            idx_ent = i + latency_s
            idx_exp = idx_ent + horizon_s
            # Garantir continuidade temporal exata (sem buracos no dado)
            if (ts_sec[idx_ent] - ts_sec[i] == latency_s) and (ts_sec[idx_exp] - ts_sec[idx_ent] == horizon_s):
                e_px = prices[idx_ent]
                x_px = prices[idx_exp]
                if half_spread_bps > 0:
                    e_eff = e_px * (1.0 + half_spread_bps / 10000.0) if s == 1 else e_px * (1.0 - half_spread_bps / 10000.0)
                else:
                    e_eff = e_px
                if x_px == e_eff:
                    ties += 1
                elif (s == 1 and x_px > e_eff) or (s == -1 and x_px < e_eff):
                    wins += 1
                bps_sum += s * (x_px - e_eff) / e_eff * 10000.0
                trades += 1
                i = idx_exp + 1  # cooldown: nao sobrepor operacoes!
                continue
        i += 1

    if trades == 0:
        return dict(name=name, n=0, acc=0.0, w_lo=0.0, w_hi=0.0, bps=0.0, tie=0.0)
    acc = wins / trades
    w_lo, w_hi = wilson_ci(wins, trades)
    return dict(name=name, n=trades, acc=acc, w_lo=w_lo, w_hi=w_hi,
                bps=bps_sum / trades, tie=ties / trades)


def print_table(title: str, rows: list):
    print(f"\n{'='*108}")
    print(f" {title}")
    print(f"{'='*108}")
    hdr = f"{'Estrategia Blitz':44s} | {'N':>6s} | {'Win%':>6s} | {'Wilson 95%':>15s} | {'Bps':>7s} | {'Empate':>6s}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        ci = f"[{r['w_lo']*100:4.1f}%, {r['w_hi']*100:4.1f}%]"
        flag = " **>55.6%" if (r["n"] >= 40 and r["w_lo"] > 0.556) else (" *>50%" if (r["n"] >= 40 and r["w_lo"] > 0.50) else "")
        print(
            f"{r['name']:44s} | {r['n']:6d} | {r['acc']*100:5.2f}% | "
            f"{ci:>15s} | {r['bps']:+7.2f} | {r['tie']*100:5.2f}%{flag}"
        )


# ==============================================================================
# BLOCO 1: BINANCE SPOT 1s (48h = 172.944s) — DEV (24h) vs HOLDOUT (24h)
# ==============================================================================
def run_binance_1s_blitz():
    df = pd.read_parquet(DATA_1S_BIN)
    df["ts"] = pd.to_datetime(df["open_time"], utc=True).astype("datetime64[s, UTC]").astype("int64")
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)

    c = df["close"].astype(np.float64)
    v = df["volume"].astype(np.float64)
    tb = df["taker_buy_base"].astype(np.float64)
    delta = 2.0 * tb - v  # agressao liquida em BTC por segundo

    # Features 100% causais (apenas <= t)
    df["r1"] = (c - c.shift(1)) / c.shift(1) * 10000.0
    df["r3"] = (c - c.shift(3)) / c.shift(3) * 10000.0
    df["r5"] = (c - c.shift(5)) / c.shift(5) * 10000.0
    df["r15"] = (c - c.shift(15)) / c.shift(15) * 10000.0
    df["r30"] = (c - c.shift(30)) / c.shift(30) * 10000.0

    # Agressao acumulada 3s e 5s + razao de volume vs media 60s
    df["d3"] = delta.rolling(3).sum()
    df["d5"] = delta.rolling(5).sum()
    df["v3"] = v.rolling(3).sum()
    df["v5"] = v.rolling(5).sum()
    df["v60_mean"] = v.rolling(60).mean().shift(1)
    df["imb3"] = np.where(df["v3"] > 0, df["d3"] / df["v3"], 0.0)  # [-1, +1]
    df["imb5"] = np.where(df["v5"] > 0, df["d5"] / df["v5"], 0.0)

    # Quantis causais (ultima 1 hora = 3600s)
    df["r3_q90"] = df["r3"].abs().rolling(3600, min_periods=600).quantile(0.90).shift(1)
    df["r5_q90"] = df["r5"].abs().rolling(3600, min_periods=600).quantile(0.90).shift(1)
    df["d3_q95"] = df["d3"].abs().rolling(3600, min_periods=600).quantile(0.95).shift(1)
    df["d5_q95"] = df["d5"].abs().rolling(3600, min_periods=600).quantile(0.95).shift(1)

    # Segundos consecutivos na mesma cor
    up1 = (df["r1"] > 0).astype(int)
    dn1 = (df["r1"] < 0).astype(int)
    df["streak4_up"] = (up1.rolling(4).sum() == 4)
    df["streak4_dn"] = (dn1.rolling(4).sum() == 4)

    df = df.dropna(subset=["r5_q90", "d5_q95", "v60_mean"]).reset_index(drop=True)
    mid = len(df) // 2

    for horizon in [5, 15]:
        for sp_name, sub in [("DEV (24h)", df.iloc[:mid]), ("HOLDOUT (24h)", df.iloc[mid:])]:
            px = sub["close"].to_numpy(dtype=np.float64)
            ts = sub["ts"].to_numpy(dtype=np.int64)
            r3 = sub["r3"].to_numpy()
            r5 = sub["r5"].to_numpy()
            r15 = sub["r15"].to_numpy()
            d3 = sub["d3"].to_numpy()
            d5 = sub["d5"].to_numpy()
            imb3 = sub["imb3"].to_numpy()
            imb5 = sub["imb5"].to_numpy()
            v3_ratio = (sub["v3"] / (3.0 * sub["v60_mean"] + 1e-9)).to_numpy()

            r3_q90 = sub["r3_q90"].to_numpy()
            r5_q90 = sub["r5_q90"].to_numpy()
            d3_q95 = sub["d3_q95"].to_numpy()
            d5_q95 = sub["d5_q95"].to_numpy()

            s_mom3 = np.where(r3 > r3_q90, 1, np.where(r3 < -r3_q90, -1, 0))
            s_mom5 = np.where(r5 > r5_q90, 1, np.where(r5 < -r5_q90, -1, 0))
            s_d3 = np.where((d3 > d3_q95) & (imb3 > 0.6), 1, np.where((d3 < -d3_q95) & (imb3 < -0.6), -1, 0))
            s_d5 = np.where((d5 > d5_q95) & (imb5 > 0.6), 1, np.where((d5 < -d5_q95) & (imb5 < -0.6), -1, 0))
            s_streak4 = np.where(sub["streak4_up"], 1, np.where(sub["streak4_dn"], -1, 0))
            # Confluencia completa: tendencia 15s + impulso 3s Q90 + agressao 3s forte + vol >= 3x media
            s_conf = np.where((np.sign(r15) == s_mom3) & (s_mom3 == np.sign(d3)) & (np.abs(imb3) > 0.6) & (v3_ratio >= 3.0), s_mom3, 0)
            # Divergencia / Absorcao 3s: volume >= 4x media, agressao forte num lado mas preco foi pro outro
            s_abs = np.where((v3_ratio >= 4.0) & (imb3 > 0.5) & (r3 < -0.5), -1,
                    np.where((v3_ratio >= 4.0) & (imb3 < -0.5) & (r3 > 0.5), 1, 0))

            rows = [
                eval_blitz_nonoverlap(f"Mom_3s Q90 (Cont) @0s", s_mom3, px, ts, horizon, 0),
                eval_blitz_nonoverlap(f"Mom_3s Q90 (Rev)  @0s", -s_mom3, px, ts, horizon, 0),
                eval_blitz_nonoverlap(f"Mom_3s Q90 (Cont) +1s clique", s_mom3, px, ts, horizon, 1),
                eval_blitz_nonoverlap(f"Mom_5s Q90 (Cont) @0s", s_mom5, px, ts, horizon, 0),
                eval_blitz_nonoverlap(f"Mom_5s Q90 (Rev)  @0s", -s_mom5, px, ts, horizon, 0),
                eval_blitz_nonoverlap(f"Streak_4s mesma cor (Cont) @0s", s_streak4, px, ts, horizon, 0),
                eval_blitz_nonoverlap(f"Streak_4s mesma cor (Rev)  @0s", -s_streak4, px, ts, horizon, 0),
                eval_blitz_nonoverlap(f"Agressao_Delta_3s Q95 (Cont) @0s", s_d3, px, ts, horizon, 0),
                eval_blitz_nonoverlap(f"Agressao_Delta_3s Q95 (Cont) +1s", s_d3, px, ts, horizon, 1),
                eval_blitz_nonoverlap(f"Agressao_Delta_3s Q95 (Cont) +2s", s_d3, px, ts, horizon, 2),
                eval_blitz_nonoverlap(f"Agressao_Delta_5s Q95 (Cont) @0s", s_d5, px, ts, horizon, 0),
                eval_blitz_nonoverlap(f"Confluencia 15s+3s+Delta+Vol3x @0s", s_conf, px, ts, horizon, 0),
                eval_blitz_nonoverlap(f"Confluencia 15s+3s+Delta+Vol3x +1s", s_conf, px, ts, horizon, 1),
                eval_blitz_nonoverlap(f"Confluencia 15s+3s+Delta+Vol3x +2s", s_conf, px, ts, horizon, 2),
                eval_blitz_nonoverlap(f"Absorcao_3s Vol4x (preco vs delta) @0s", s_abs, px, ts, horizon, 0),
            ]
            print_table(f"BINANCE SPOT 1s — BLITZ {horizon}s [{sp_name}] (nao-sobreposto)", rows)


# ==============================================================================
# BLOCO 2: DERIV cryBTCUSD TICKS REAIS (24h = 86.247s) — DEV (12h) vs HOLDOUT (12h)
# ==============================================================================
def run_deriv_blitz():
    df = pd.read_parquet(DATA_DERIV).sort_values("epoch").reset_index(drop=True)
    # Reindexar para grade exata de 1s (preenchendo buracos raros se houver)
    c = df["price"].astype(np.float64)
    df["r1"] = (c - c.shift(1)) / c.shift(1) * 10000.0
    df["r3"] = (c - c.shift(3)) / c.shift(3) * 10000.0
    df["r5"] = (c - c.shift(5)) / c.shift(5) * 10000.0
    df["r15"] = (c - c.shift(15)) / c.shift(15) * 10000.0

    df["r3_q90"] = df["r3"].abs().rolling(3600, min_periods=600).quantile(0.90).shift(1)
    df["r5_q90"] = df["r5"].abs().rolling(3600, min_periods=600).quantile(0.90).shift(1)
    up1 = (df["r1"] > 0).astype(int)
    dn1 = (df["r1"] < 0).astype(int)
    df["st4_up"] = (up1.rolling(4).sum() == 4)
    df["st4_dn"] = (dn1.rolling(4).sum() == 4)

    df = df.dropna(subset=["r5_q90"]).reset_index(drop=True)
    mid = len(df) // 2

    for horizon in [5, 15]:
        for sp_name, sub in [("DEV (12h)", df.iloc[:mid]), ("HOLDOUT (12h)", df.iloc[mid:])]:
            px = sub["price"].to_numpy(dtype=np.float64)
            ts = sub["epoch"].to_numpy(dtype=np.int64)
            r3 = sub["r3"].to_numpy()
            r5 = sub["r5"].to_numpy()
            r15 = sub["r15"].to_numpy()
            r3_q90 = sub["r3_q90"].to_numpy()
            r5_q90 = sub["r5_q90"].to_numpy()

            s_m3 = np.where(r3 > r3_q90, 1, np.where(r3 < -r3_q90, -1, 0))
            s_m5 = np.where(r5 > r5_q90, 1, np.where(r5 < -r5_q90, -1, 0))
            s_st4 = np.where(sub["st4_up"], 1, np.where(sub["st4_dn"], -1, 0))
            s_15_3 = np.where((np.sign(r15) == s_m3) & (s_m3 != 0), s_m3, 0)

            rows = [
                eval_blitz_nonoverlap("Deriv Mom_3s Q90 (Cont) @0s", s_m3, px, ts, horizon, 0, 0.16),
                eval_blitz_nonoverlap("Deriv Mom_3s Q90 (Rev)  @0s", -s_m3, px, ts, horizon, 0, 0.16),
                eval_blitz_nonoverlap("Deriv Mom_3s Q90 (Cont) +1s", s_m3, px, ts, horizon, 1, 0.16),
                eval_blitz_nonoverlap("Deriv Mom_5s Q90 (Cont) @0s", s_m5, px, ts, horizon, 0, 0.16),
                eval_blitz_nonoverlap("Deriv Streak_4s (Cont)  @0s", s_st4, px, ts, horizon, 0, 0.16),
                eval_blitz_nonoverlap("Deriv Streak_4s (Rev)   @0s", -s_st4, px, ts, horizon, 0, 0.16),
                eval_blitz_nonoverlap("Deriv 15s + Mom_3s Q90  @0s", s_15_3, px, ts, horizon, 0, 0.16),
                eval_blitz_nonoverlap("Deriv 15s + Mom_3s Q90  +1s", s_15_3, px, ts, horizon, 1, 0.16),
            ]
            print_table(f"DERIV cryBTCUSD — BLITZ {horizon}s [{sp_name}] (half_spread=0.16bp)", rows)


if __name__ == "__main__":
    run_binance_1s_blitz()
    run_deriv_blitz()
