"""
audit_engulfing_retest.py
=========================
Teste rigoroso e 100% CAUSAL do "Super Gatilho" proposto pelo usuario:
  COMPRA:
    - Vela i-3: VERDE (c > o)
    - Vela i-2: VERDE (c > o)
    - Vela i-1: VERMELHA (c < o)  [vela engolfada -> define lvl_high = high[i-1], lvl_open = open[i-1]]
    - Vela i  : VERDE (c > o) fechando ACIMA da maxima da vermelha (c[i] > high[i-1])
                E deixando pavio superior E inferior (nao careca: h[i] > c[i] e l[i] < o[i]).
    - Gatilho: nas proximas 1 a 3 velas (t in {i+1, i+2, i+3}), o preco recua e toca:
        (A) lvl_high (maxima da vela vermelha engolfada) — entrada exata em lvl_high
        (B) lvl_open (abertura da vela vermelha engolfada) — entrada exata em lvl_open
    - Avaliacao na vela t do toque:
        * Win_Entry: close[t] > entry_price (vitoria real no preco exato da entrada!)
        * Regra 3-estados do usuario: close[t] > lvl_high (WIN) | close[t] < lvl_open (LOSS) | entre os dois (MEIO)
        * Retorno medio em bps: (close[t] - entry_price) / entry_price
        * Em M5 e M15: testa TAMBEM confirmacao intrabar via M1 (apos tocar o nivel, so entra no
          fechamento da barra M1 que virar verde + delta agressor positivo dentro da propria vela M5/M15!).
  VENDA: exatamente simetrico (Verm -> Verm -> Verde -> Verm engolfando abaixo da minima da verde com pavios).

Testado em 110 DIAS REAIS (2026-07-30 a 2026-11-17) em M1, M5 e M15 (Dev 60d + Holdout 30d).
"""

import sys
from pathlib import Path
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from audit_phase2_baseline import wilson_ci
from audit_m5_m15_comparison import load_110d_m1, resample_canonical


def run_tf_engulfing_audit(tf_min: int, spot_m1: pd.DataFrame, require_vol15: bool = False, min_wick_frac: float = 0.05):
    df = spot_m1.copy() if tf_min == 1 else resample_canonical(spot_m1, tf_min)
    if tf_min == 1:
        df["date"] = df["open_time"].dt.date

    o = df["open"].astype(float).values
    h = df["high"].astype(float).values
    l = df["low"].astype(float).values
    c = df["close"].astype(float).values
    v = df["volume"].astype(float).values
    tbb = df["taker_buy_base"].astype(float).values
    dates = df["date"].values
    n = len(df)

    vol_ma20 = pd.Series(v).shift(1).rolling(20).mean().values
    vol_ratio = np.where(vol_ma20 > 0, v / vol_ma20, 0.0)

    unique_dates = sorted(df["date"].unique())
    ho_dates = set(unique_dates[-30:])
    dev_dates = set(unique_dates[21:-30])

    # Pre-indexa M1 por bucket de M5/M15 para teste de confirmacao intrabar em tempo real
    m1_by_bucket = None
    if tf_min > 1:
        m1_tmp = spot_m1[["open_time", "open", "high", "low", "close", "volume", "taker_buy_base"]].copy()
        m1_tmp["bucket_ns"] = m1_tmp["open_time"].dt.floor(f"{tf_min}min").astype("int64")
        m1_by_bucket = {
            int(k): grp[["open", "high", "low", "close", "volume", "taker_buy_base"]].values.astype(float)
            for k, grp in m1_tmp.groupby("bucket_ns")
        }
    df_ns = df["open_time"].astype("int64").values

    # Identifica todos os padroes de 4 velas (i-3, i-2, i-1, i)
    setups = []
    for i in range(3, n - 1):
        rng_i = h[i] - l[i]
        if rng_i <= 0:
            continue
        if require_vol15 and vol_ratio[i] < 1.5:
            continue

        # COMPRA: Verde(i-3), Verde(i-2), Vermelha(i-1), Verde(i) fechando > high[i-1] com pavio sup e inf
        if (
            c[i - 3] > o[i - 3]
            and c[i - 2] > o[i - 2]
            and c[i - 1] < o[i - 1]
            and c[i] > o[i]
            and c[i] > h[i - 1]
        ):
            up_w = (h[i] - c[i]) / rng_i
            lo_w = (o[i] - l[i]) / rng_i
            if up_w >= min_wick_frac and lo_w >= min_wick_frac:
                setups.append((i, 1, h[i - 1], o[i - 1]))  # (idx_engulf, side=1, lvl_ext, lvl_open)

        # VENDA: Verm(i-3), Verm(i-2), Verde(i-1), Verm(i) fechando < low[i-1] com pavio sup e inf
        elif (
            c[i - 3] < o[i - 3]
            and c[i - 2] < o[i - 2]
            and c[i - 1] > o[i - 1]
            and c[i] < o[i]
            and c[i] < l[i - 1]
        ):
            up_w = (h[i] - o[i]) / rng_i
            lo_w = (c[i] - l[i]) / rng_i
            if up_w >= min_wick_frac and lo_w >= min_wick_frac:
                setups.append((i, 0, l[i - 1], o[i - 1]))  # (idx_engulf, side=0, lvl_ext, lvl_open)

    def eval_split(split_dates: set, split_name: str):
        # Coletores:
        # 1) Entrada no toque da maxima(vermelha)/minima(verde) (lvl_ext) em ate 3 velas
        res_ext = []       # (win_entry, ret_bps, user_state: 'WIN'|'LOSS'|'MID', delay_bars)
        # 2) Entrada no toque da abertura da engolfada (lvl_open) em ate 3 velas
        res_open = []
        # 3) Entrada imediata na abertura de i+1 (sem esperar recuo, para comparacao!)
        res_imediata = []
        # 4) Em M5/M15: toque em lvl_ext + confirmacao intrabar M1 (barra M1 vira a favor + delta > 0)
        res_intrabar = []

        for idx_e, side, lvl_ext, lvl_open in setups:
            if dates[idx_e + 1] not in split_dates:
                continue
            dir_s = 1.0 if side == 1 else -1.0

            # 3) Imediata em i+1 (sem recuo)
            t1 = idx_e + 1
            ep0 = o[t1]
            we0 = (c[t1] > ep0) if side == 1 else (c[t1] < ep0)
            res_imediata.append((we0, dir_s * (c[t1] - ep0) / ep0 * 1e4))

            # 1) Reteste em lvl_ext nas proximas 1..3 velas (t in i+1..i+3)
            for k in range(1, 4):
                t = idx_e + k
                if t >= n:
                    break
                # Garante que nao ja abriu rompido contra; ordem limit em lvl_ext
                if side == 1:
                    if o[t] >= lvl_ext and l[t] <= lvl_ext:
                        we = c[t] > lvl_ext
                        ret = (c[t] - lvl_ext) / lvl_ext * 1e4
                        ust = "WIN" if c[t] > lvl_ext else ("LOSS" if c[t] < lvl_open else "MID")
                        res_ext.append((we, ret, ust, k))
                        break
                    elif o[t] < lvl_ext:
                        break  # ja perdeu o nivel na abertura
                else:
                    if o[t] <= lvl_ext and h[t] >= lvl_ext:
                        we = c[t] < lvl_ext
                        ret = (lvl_ext - c[t]) / lvl_ext * 1e4
                        ust = "WIN" if c[t] < lvl_ext else ("LOSS" if c[t] > lvl_open else "MID")
                        res_ext.append((we, ret, ust, k))
                        break
                    elif o[t] > lvl_ext:
                        break

            # 2) Reteste na abertura da engolfada (lvl_open) nas proximas 1..3 velas
            for k in range(1, 4):
                t = idx_e + k
                if t >= n:
                    break
                if side == 1:
                    if o[t] >= lvl_open and l[t] <= lvl_open:
                        we = c[t] > lvl_open
                        ret = (c[t] - lvl_open) / lvl_open * 1e4
                        ust = "WIN" if c[t] > lvl_ext else ("LOSS" if c[t] < lvl_open else "MID")
                        res_open.append((we, ret, ust, k))
                        break
                    elif o[t] < lvl_open:
                        break
                else:
                    if o[t] <= lvl_open and h[t] >= lvl_open:
                        we = c[t] < lvl_open
                        ret = (lvl_open - c[t]) / lvl_open * 1e4
                        ust = "WIN" if c[t] < lvl_ext else ("LOSS" if c[t] > lvl_open else "MID")
                        res_open.append((we, ret, ust, k))
                        break
                    elif o[t] > lvl_open:
                        break

            # 4) Confirmacao intrabar M1 dentro da vela M5/M15 (somente se tf_min > 1)
            if m1_by_bucket is not None:
                found_ib = False
                for k in range(1, 4):
                    t = idx_e + k
                    if t >= n:
                        break
                    sub = m1_by_bucket.get(int(df_ns[t]))
                    if sub is None or len(sub) < tf_min:
                        continue
                    touched = False
                    # So entra antes do ultimo minuto da vela M5/M15 (deixa pelo menos 1m p/ fechar)
                    for m_idx in range(len(sub) - 1):
                        mo, mh, ml, mc, mv, mtbb = sub[m_idx]
                        if (side == 1 and ml <= lvl_ext) or (side == 0 and mh >= lvl_ext):
                            touched = True
                        if touched:
                            delta_m1 = (2.0 * mtbb - mv)
                            # Confirmacao real: barra M1 fechou a favor (verde p/ CALL, verm p/ PUT) + fluxo agressor a favor
                            ok_dir = (mc > mo and delta_m1 > 0) if side == 1 else (mc < mo and delta_m1 < 0)
                            if ok_dir:
                                we = (c[t] > mc) if side == 1 else (c[t] < mc)
                                ret = dir_s * (c[t] - mc) / mc * 1e4
                                ust = "WIN" if ((c[t] > lvl_ext) if side == 1 else (c[t] < lvl_ext)) else (
                                    "LOSS" if ((c[t] < lvl_open) if side == 1 else (c[t] > lvl_open)) else "MID"
                                )
                                res_intrabar.append((we, ret, ust, k))
                                found_ib = True
                                break
                    if found_ib:
                        break

        def fmt_res(label: str, arr: list, has_ust: bool = True):
            if not arr:
                print(f"  [{split_name:<6}] {label:<38} | N=   0")
                return
            nn = len(arr)
            wins = sum(1 for x in arr if x[0])
            lo, hi = wilson_ci(wins, nn)
            avg_ret = float(np.mean([x[1] for x in arr]))
            extra = ""
            if has_ust:
                u_w = sum(1 for x in arr if x[2] == "WIN")
                u_l = sum(1 for x in arr if x[2] == "LOSS")
                u_m = sum(1 for x in arr if x[2] == "MID")
                u_wr = (u_w / max(1, u_w + u_l)) * 100
                extra = f" | Regra3Est(>Max vs <Open): {u_w}W/{u_l}L/{u_m}M ({u_wr:5.1f}%)"
            print(
                f"  [{split_name:<6}] {label:<38} | N={nn:>4} | Win_Entrada={wins/nn*100:5.2f}% [{lo*100:5.2f}%,{hi*100:5.2f}%] | Ret={avg_ret:+6.2f}bp{extra}"
            )

        fmt_res("0. Entrada imediata open[i+1] (sem recuo)", res_imediata, has_ust=False)
        fmt_res("1. Toque Maxima(V)/Minima(G) em 1..3v", res_ext, has_ust=True)
        fmt_res("2. Toque Abertura engolfada em 1..3v", res_open, has_ust=True)
        if tf_min > 1:
            fmt_res("3. Toque + Confirmacao Intrabar M1", res_intrabar, has_ust=True)

    tag = " + Vol>=1.5x" if require_vol15 else ""
    print("\n" + "=" * 145)
    print(f"M{tf_min} — SUPER GATILHO ENGOLFO (V-V-R-V com pavios >={int(min_wick_frac*100)}%{tag}) | Total setups 110d = {len(setups)}")
    print("=" * 145)
    eval_split(dev_dates, "DEV60d")
    eval_split(ho_dates, "HO30d")


def main():
    spot_m1, _ = load_110d_m1()
    for tf in [1, 5, 15]:
        run_tf_engulfing_audit(tf, spot_m1, require_vol15=False, min_wick_frac=0.05)
        run_tf_engulfing_audit(tf, spot_m1, require_vol15=True, min_wick_frac=0.05)


if __name__ == "__main__":
    main()
