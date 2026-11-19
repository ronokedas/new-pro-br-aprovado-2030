"""
audit_pullback_entry_5s.py
==========================
Testa empiricamente (sem achismo, 100% causal, segundo a segundo sobre BTCUSDT_1s_2d.parquet)
as duas ideias do usuario para melhorar o PRECO DE ENTRADA (close_60s vs entry_price):
  1. Sinal 1 travado em +5s (FISICA_SNR5s_Q70) -> entrar no recuo ate open_0s (6s..35s),
     com e sem confirmacao do Sinal 2 ao vivo (ret_1s + delta_2s virando a favor).
  2. Sinal 1 travado em +0s (REF_basis_z60_Q80) -> entrar no recuo contra a direcao (1s..20s),
     com e sem confirmacao do Sinal 2 ao vivo.
Mede SEMPRE a vitoria REAL no preco de entrada (close_60s > entry_price p/ CALL, < p/ PUT),
alem da cor da vela (close_60s vs open_0s), Wilson 95% e retorno em bps!
"""
import math
import sys
from pathlib import Path
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from audit_phase2_baseline import DATA_PARQUET, wilson_ci
from audit_phase3_hypotheses import PERP_PARQUET, KLINES_1S_PARQUET


def eval_entries(entries: list, title: str):
    if not entries:
        print(f"{title:<46} | N=   0")
        return
    n = len(entries)
    win_entry = np.array([e[0] for e in entries], dtype=bool)
    win_candle = np.array([e[1] for e in entries], dtype=bool)
    ret_entry_bps = np.array([e[2] for e in entries], dtype=float) * 1e4
    avg_sec = float(np.mean([e[3] for e in entries]))
    k_e = int(win_entry.sum())
    lo, hi = wilson_ci(k_e, n)
    print(
        f"{title:<46} | N={n:>4} (t={avg_sec:4.1f}s) | "
        f"Win_Entrada={k_e/n*100:5.2f}% [{lo*100:5.2f}%,{hi*100:5.2f}%] | "
        f"RetEntrada={ret_entry_bps.mean():+5.2f}bp | CorVela={win_candle.mean()*100:5.2f}%"
    )


def main():
    df_spot = pd.read_parquet(DATA_PARQUET).sort_values("open_time").reset_index(drop=True)
    df_perp = pd.read_parquet(PERP_PARQUET).sort_values("open_time").reset_index(drop=True)
    df_1s = pd.read_parquet(KLINES_1S_PARQUET).sort_values("open_time").reset_index(drop=True)

    m = df_spot.merge(df_perp[["open_time", "close"]], on="open_time", how="inner", suffixes=("", "_perp"))
    c = m["close"].astype(float)
    sig15 = c.pct_change(1).rolling(15).std().clip(lower=5e-5).shift(1)
    basis = (m["close_perp"].astype(float) - c) / c
    bz60 = ((basis - basis.rolling(60).mean()) / basis.rolling(60).std().replace(0, np.nan)).shift(1)
    # Limiar Q80 causal de |bz60| (rolling 1440 velas = 24h)
    bz_q80 = bz60.abs().rolling(1440, min_periods=200).quantile(0.80).shift(1)

    m_map = {}
    for ts, s15, bz, q80 in zip(m["open_time"], sig15, bz60, bz_q80):
        if not (np.isnan(s15) or np.isnan(bz) or np.isnan(q80)):
            m_map[ts] = (float(s15), float(bz), float(q80))

    df_1s["minute"] = df_1s["open_time"].dt.floor("1min")
    df_1s["sec"] = df_1s["open_time"].dt.second
    df_1s["delta"] = 2.0 * df_1s["taker_buy_base"] - df_1s["volume"]

    # Passagem 1: coletar snr_5s causal para quantil Q70 rolante (ultimas 720 velas = 12h)
    candles = []
    for m_ts, grp in df_1s.groupby("minute"):
        if len(grp) != 60 or m_ts not in m_map:
            continue
        s15, bz, q80 = m_map[m_ts]
        o0 = float(grp["open"].iloc[0])
        c_end = float(grp["close"].iloc[-1])
        closes = grp["close"].values.astype(float)
        deltas = grp["delta"].values.astype(float)
        ret5 = (closes[4] - o0) / o0
        snr5 = ret5 / (s15 * math.sqrt(55.0 / 60.0))
        candles.append((m_ts, o0, c_end, closes, deltas, snr5, bz, q80))

    abs_snrs = np.array([abs(x[5]) for x in candles])

    # Coletores
    b_imediata = []
    b_recuo_open = []
    b_recuo_50pct = []
    b_recuo_open_sinal2 = []
    b_recuo_50pct_sinal2 = []

    a_imediata_0s = []
    a_imediata_1s = []
    a_recuo_contra = []
    a_recuo_contra_sinal2 = []

    for idx in range(720, len(candles)):
        m_ts, o0, c_end, closes, deltas, snr5, bz, q80 = candles[idx]
        th_snr70 = float(np.quantile(abs_snrs[idx - 720:idx], 0.70))

        # --- TESTE 1: PRODUTO B (+5s, FISICA_SNR5s_Q70) ---
        if abs(snr5) >= th_snr70 and snr5 != 0:
            sig = 1 if snr5 > 0 else 0
            dir_s = 1.0 if sig == 1 else -1.0
            win_c = (c_end > o0) if sig == 1 else (c_end < o0)

            # 1a) Entrada imediata em +5s (closes[4])
            ep = closes[4]
            we = (c_end > ep) if sig == 1 else (c_end < ep)
            b_imediata.append((we, win_c, dir_s * (c_end - ep) / ep, 5))

            # 1b) Recuo ate open_0s entre t=6s e t=35s (sem sinal 2)
            for s in range(5, 35):
                px = closes[s]
                if (sig == 1 and px <= o0) or (sig == 0 and px >= o0):
                    we = (c_end > px) if sig == 1 else (c_end < px)
                    b_recuo_open.append((we, win_c, dir_s * (c_end - px) / px, s + 1))
                    break

            # 1c) Recuo de 50% do movimento de 5s entre t=6s e t=35s
            mid_lvl = o0 + 0.5 * (closes[4] - o0)
            for s in range(5, 35):
                px = closes[s]
                if (sig == 1 and px <= mid_lvl) or (sig == 0 and px >= mid_lvl):
                    we = (c_end > px) if sig == 1 else (c_end < px)
                    b_recuo_50pct.append((we, win_c, dir_s * (c_end - px) / px, s + 1))
                    break

            # 1d) Recuo ate open_0s + SINAL 2 virando a favor (tick 1s subindo + delta 2s positivo p/ CALL)
            touched_open = False
            for s in range(5, 40):
                px = closes[s]
                if (sig == 1 and px <= o0) or (sig == 0 and px >= o0):
                    touched_open = True
                if touched_open:
                    tick_ok = (closes[s] > closes[s - 1]) if sig == 1 else (closes[s] < closes[s - 1])
                    d2 = deltas[s] + deltas[s - 1]
                    delta_ok = (d2 > 0) if sig == 1 else (d2 < 0)
                    if tick_ok and delta_ok:
                        we = (c_end > px) if sig == 1 else (c_end < px)
                        b_recuo_open_sinal2.append((we, win_c, dir_s * (c_end - px) / px, s + 1))
                        break

            # 1e) Recuo de 50% + SINAL 2 virando a favor
            touched_50 = False
            for s in range(5, 40):
                px = closes[s]
                if (sig == 1 and px <= mid_lvl) or (sig == 0 and px >= mid_lvl):
                    touched_50 = True
                if touched_50:
                    tick_ok = (closes[s] > closes[s - 1]) if sig == 1 else (closes[s] < closes[s - 1])
                    d2 = deltas[s] + deltas[s - 1]
                    delta_ok = (d2 > 0) if sig == 1 else (d2 < 0)
                    if tick_ok and delta_ok:
                        we = (c_end > px) if sig == 1 else (c_end < px)
                        b_recuo_50pct_sinal2.append((we, win_c, dir_s * (c_end - px) / px, s + 1))
                        break

        # --- TESTE 2: PRODUTO A (+0s, REF_basis_z60_Q80) ---
        if abs(bz) >= q80 and bz != 0:
            sig = 1 if bz > 0 else 0
            dir_s = 1.0 if sig == 1 else -1.0
            win_c = (c_end > o0) if sig == 1 else (c_end < o0)

            a_imediata_0s.append((win_c, win_c, dir_s * (c_end - o0) / o0, 0))
            ep1 = closes[0]
            a_imediata_1s.append(((c_end > ep1) if sig == 1 else (c_end < ep1), win_c, dir_s * (c_end - ep1) / ep1, 1))

            # Recuo contra a direcao do Produto A nos primeiros 25s (vela ficou cor contraria!)
            for s in range(1, 25):
                px = closes[s]
                if (sig == 1 and px < o0) or (sig == 0 and px > o0):
                    we = (c_end > px) if sig == 1 else (c_end < px)
                    a_recuo_contra.append((we, win_c, dir_s * (c_end - px) / px, s + 1))
                    break

            # Recuo contra + Sinal 2 virando a favor
            touched = False
            for s in range(1, 30):
                px = closes[s]
                if (sig == 1 and px < o0) or (sig == 0 and px > o0):
                    touched = True
                if touched:
                    tick_ok = (closes[s] > closes[s - 1]) if sig == 1 else (closes[s] < closes[s - 1])
                    d2 = deltas[s] + deltas[s - 1]
                    delta_ok = (d2 > 0) if sig == 1 else (d2 < 0)
                    if tick_ok and delta_ok:
                        we = (c_end > px) if sig == 1 else (c_end < px)
                        a_recuo_contra_sinal2.append((we, win_c, dir_s * (c_end - px) / px, s + 1))
                        break

    print("=" * 130)
    print(f"TESTE CAUSAL DE ENTRADA NO RECUO + SINAL 2 AO VIVO (N_velas_OOS = {len(candles) - 720})")
    print("Win_Entrada = Close_60s vence o PRECO EXATO DA ENTRADA (o que importa em Opcoes Binarias / Spot!)")
    print("=" * 130)
    print("\n--- BLOCO 1: Sinal 1 travado em +5s (FISICA_SNR5s_Q70) ---")
    eval_entries(b_imediata,           "1a. Entrada imediata em +5s (sem recuo)")
    eval_entries(b_recuo_50pct,        "1b. Recuo 50% do mov de 5s (6s..35s)")
    eval_entries(b_recuo_50pct_sinal2, "1c. Recuo 50% + Sinal 2 virando a favor")
    eval_entries(b_recuo_open,         "1d. Recuo ate open_0s (6s..35s)")
    eval_entries(b_recuo_open_sinal2,  "1e. Recuo ate open_0s + Sinal 2 a favor")

    print("\n--- BLOCO 2: Sinal 1 travado em +0s (Produto A: REF_basis_z60_Q80) ---")
    eval_entries(a_imediata_0s,        "2a. Entrada teorica em +0s (open_0s)")
    eval_entries(a_imediata_1s,        "2b. Entrada em +1s")
    eval_entries(a_recuo_contra,       "2c. Vela contra (recuo < open_0s) 1..25s")
    eval_entries(a_recuo_contra_sinal2,"2d. Vela contra + Sinal 2 virando a favor")


if __name__ == "__main__":
    main()
