"""
audit_extra_m5_wick_zone.py
===========================
Teste rigoroso e 100% causal da hipótese proposta pelo usuário:
  "Indicadores indicam alta, uma vela M1 desce e toca em uma região de pavios de
   duas velas M5 adjacentes de cores opostas (vermelha + verde), com reação/acúmulo
   de ordens contrárias apontando reversão para a próxima vela M1 (e inverso para venda)."

REGRAS ANTI-VAZAMENTO (prompat.txt):
  - Mercado congelado: Binance Spot BTCUSDT M1 (BTCUSDT_1m_65d.parquet).
  - Label congelado: 1 se close_t > open_t, 0 se close_t < open_t, -1 se close_t == open_t (empate = erro).
  - Causalidade M5 estrita: para prever a vela M1 `t` (em `open_t`), SÓ podem ser usadas barras M5
    cujo fechamento seja estritamente anterior a `open_t`, ou seja:
        bucket_5m <= floor(open_t, '5min') - 5min.
    A barra M5 em andamento NUNCA é usada.
  - Todas as combinações são pré-especificadas antes da execução (zero otimização no teste).
  - Relata TODAS as variantes (múltiplos testes declarados), com N, cobertura, acurácia, Wilson 95%,
    WAcc (ponderada por |close - open|), retorno CALL/PUT/Dir (bps), 7 semanas walk-forward,
    e os 3 testes de falsificação (F1, F2, F3).
  - NOTA DE HOLDOUT: o holdout de 7 dias (2026-11-11..17) já foi aberto na Fase 5 anterior, portanto
    não é mais um holdout virgem (declarado explicitamente). A avaliação principal usa os 44 dias OOS
    de desenvolvimento (2026-09-28..2026-11-10, N=63.360) e reporta os 7 dias finais apenas como
    checagem secundária.
"""

import sys
from pathlib import Path
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from audit_phase2_baseline import (
    DATA_PARQUET, TRAIN_DAYS, PURGE_BARS, HOLDOUT_DAYS,
    compute_frozen_labels, evaluate_predictions, falsification_shuffle_intraday
)
from audit_phase3_hypotheses import (
    PERP_PARQUET, KLINES_1S_PARQUET, build_closed_htf_ema_bias, eval_hypothesis_full, print_hyp_result
)


def build_causal_m5_wick_zones(df_m1: pd.DataFrame, lookback_m5: int = 24) -> dict[str, np.ndarray]:
    """
    Constrói, para cada vela M1 `t`, indicadores booleanos 100% CAUSAIS de toque
    da vela fechada `t-1` em zonas de pavios de pares adjacentes M5 (cores opostas),
    usando ESTRITAMENTE barras M5 já fechadas antes de `open_t` (`bucket_5m <= floor(open_t, 5min) - 5min`).

    Retorna dicionário de arrays booleanos de tamanho N (alinhados à vela prevista `t`):
      - touch_sup_overlap: M1[t-1] desce e toca zona de SOBREPOSIÇÃO exata dos pavios inferiores de par M5 (V/R ou R/V), fechando >= zone_lo
      - touch_res_overlap: simétrico para topo (M1[t-1] sobe e toca sobreposição de pavios superiores, fechando <= zone_hi)
      - touch_sup_flip   : M1[t-1] desce e toca zona de pavio inferior de par de reversão M5 (Vermelha -> Verde), fechando >= zone_lo
      - touch_res_flip   : simétrico para topo (par Verde -> Vermelha), M1[t-1] sobe e toca pavio superior, fechando <= zone_hi
      - touch_sup_any    : união ampla (M1[t-1] desce e toca faixa dos pavios inferiores de qualquer par oposto M5 nas últimas lookback_m5 barras, fechando >= zone_lo)
      - touch_res_any    : simétrico para topo
    """
    n = len(df_m1)
    tmp = df_m1[["open_time", "open", "high", "low", "close"]].copy()
    tmp["bucket_5m"] = tmp["open_time"].dt.floor("5min")

    bars5 = tmp.groupby("bucket_5m").agg(
        o=("open", "first"),
        h=("high", "max"),
        l=("low", "min"),
        c=("close", "last"),
        cnt=("open", "count"),
    ).reset_index().sort_values("bucket_5m").reset_index(drop=True)

    # Apenas barras 5m completas
    b_ts = bars5["bucket_5m"].values
    b_o = bars5["o"].values
    b_h = bars5["h"].values
    b_l = bars5["l"].values
    b_c = bars5["c"].values
    b_cnt = bars5["cnt"].values
    b_col = np.where(b_c > b_o, 1, np.where(b_c < b_o, 0, -1))

    # Pavios inferiores [l, min(o,c)] e superiores [max(o,c), h] de cada barra M5
    b_body_lo = np.minimum(b_o, b_c)
    b_body_hi = np.maximum(b_o, b_c)

    # Pré-computa zonas por índice de barra M5 `j` (par formado por j-1 e j, disponível somente após j fechar!)
    m = len(bars5)
    sup_ov_lo = np.full(m, np.nan); sup_ov_hi = np.full(m, np.nan)
    res_ov_lo = np.full(m, np.nan); res_ov_hi = np.full(m, np.nan)
    sup_fl_lo = np.full(m, np.nan); sup_fl_hi = np.full(m, np.nan)
    res_fl_lo = np.full(m, np.nan); res_fl_hi = np.full(m, np.nan)
    sup_un_lo = np.full(m, np.nan); sup_un_hi = np.full(m, np.nan)
    res_un_lo = np.full(m, np.nan); res_un_hi = np.full(m, np.nan)

    for j in range(1, m):
        if b_cnt[j - 1] < 5 or b_cnt[j] < 5:
            continue
        c1, c2 = b_col[j - 1], b_col[j]
        if c1 < 0 or c2 < 0 or c1 == c2:
            continue  # precisa ser par de cores opostas (uma verde e uma vermelha)

        # 1) Sobreposição exata dos pavios inferiores (ambas têm pavio e se sobrepõem)
        ov_l = max(b_l[j - 1], b_l[j])
        ov_h = min(b_body_lo[j - 1], b_body_lo[j])
        if ov_h > ov_l:
            sup_ov_lo[j] = ov_l
            sup_ov_hi[j] = ov_h

        # Sobreposição exata dos pavios superiores
        rov_l = max(b_body_hi[j - 1], b_body_hi[j])
        rov_h = min(b_h[j - 1], b_h[j])
        if rov_h > rov_l:
            res_ov_lo[j] = rov_l
            res_ov_hi[j] = rov_h

        # 2) União dos pavios (exige ambos terem algum pavio)
        if b_body_lo[j - 1] > b_l[j - 1] and b_body_lo[j] > b_l[j]:
            sup_un_lo[j] = min(b_l[j - 1], b_l[j])
            sup_un_hi[j] = max(b_body_lo[j - 1], b_body_lo[j])
        if b_h[j - 1] > b_body_hi[j - 1] and b_h[j] > b_body_hi[j]:
            res_un_lo[j] = min(b_body_hi[j - 1], b_body_hi[j])
            res_un_hi[j] = max(b_h[j - 1], b_h[j])

        # 3) Par de reversão específico: Vermelha -> Verde (suporte) / Verde -> Vermelha (resistência)
        if c1 == 0 and c2 == 1:
            lo_f = min(b_l[j - 1], b_l[j])
            hi_f = max(b_body_lo[j - 1], b_body_lo[j])
            if hi_f > lo_f:
                sup_fl_lo[j] = lo_f
                sup_fl_hi[j] = hi_f
        elif c1 == 1 and c2 == 0:
            lo_f = min(b_body_hi[j - 1], b_body_hi[j])
            hi_f = max(b_h[j - 1], b_h[j])
            if hi_f > lo_f:
                res_fl_lo[j] = lo_f
                res_fl_hi[j] = hi_f

    # Mapa: bucket_5m -> índice j na tabela bars5
    ts_to_j = {ts: idx for idx, ts in enumerate(b_ts)}

    # Última barra M5 JÁ FECHADA antes de open_t = floor(open_t, 5min) - 5min
    prev_closed_5m = (tmp["bucket_5m"] - pd.Timedelta(minutes=5)).values

    m1_o = df_m1["open"].values
    m1_h = df_m1["high"].values
    m1_l = df_m1["low"].values
    m1_c = df_m1["close"].values

    out = {
        "touch_sup_overlap": np.zeros(n, dtype=bool),
        "touch_res_overlap": np.zeros(n, dtype=bool),
        "touch_sup_flip": np.zeros(n, dtype=bool),
        "touch_res_flip": np.zeros(n, dtype=bool),
        "touch_sup_any": np.zeros(n, dtype=bool),
        "touch_res_any": np.zeros(n, dtype=bool),
    }

    # Para prever vela `t`, checamos o toque na vela fechada `i = t - 1`
    for t in range(1, n):
        j_last = ts_to_j.get(prev_closed_5m[t], -1)
        if j_last < 2:
            continue
        j_start = max(1, j_last - lookback_m5 + 1)

        i = t - 1  # vela M1 recém-fechada antes de open_t
        o_i, h_i, l_i, c_i = m1_o[i], m1_h[i], m1_l[i], m1_c[i]

        # "uma vela desce e toca..." -> vela t-1 fechou vermelha (descendo)
        if c_i < o_i:
            # Checa sobreposição de pavios inferiores M5 nas últimas lookback_m5 barras fechadas
            zl = sup_ov_lo[j_start:j_last + 1]
            zh = sup_ov_hi[j_start:j_last + 1]
            valid = ~np.isnan(zl)
            if valid.any():
                # Low da M1 entrou na faixa [zl, zh] e close não perdeu o piso zl (rejeição/toque)
                if np.any((l_i <= zh[valid]) & (l_i >= zl[valid]) & (c_i >= zl[valid])):
                    out["touch_sup_overlap"][t] = True

            # Checa par de reversão Vermelha -> Verde
            zl = sup_fl_lo[j_start:j_last + 1]
            zh = sup_fl_hi[j_start:j_last + 1]
            valid = ~np.isnan(zl)
            if valid.any():
                if np.any((l_i <= zh[valid]) & (l_i >= zl[valid]) & (c_i >= zl[valid])):
                    out["touch_sup_flip"][t] = True

            # Checa união ampla de pavios inferiores de qualquer par oposto
            zl = sup_un_lo[j_start:j_last + 1]
            zh = sup_un_hi[j_start:j_last + 1]
            valid = ~np.isnan(zl)
            if valid.any():
                if np.any((l_i <= zh[valid]) & (l_i >= zl[valid]) & (c_i >= zl[valid])):
                    out["touch_sup_any"][t] = True

        # Simétrico para VENDA: "uma vela sobe e toca pavios superiores M5"
        elif c_i > o_i:
            zl = res_ov_lo[j_start:j_last + 1]
            zh = res_ov_hi[j_start:j_last + 1]
            valid = ~np.isnan(zl)
            if valid.any():
                if np.any((h_i >= zl[valid]) & (h_i <= zh[valid]) & (c_i <= zh[valid])):
                    out["touch_res_overlap"][t] = True

            zl = res_fl_lo[j_start:j_last + 1]
            zh = res_fl_hi[j_start:j_last + 1]
            valid = ~np.isnan(zl)
            if valid.any():
                if np.any((h_i >= zl[valid]) & (h_i <= zh[valid]) & (c_i <= zh[valid])):
                    out["touch_res_flip"][t] = True

            zl = res_un_lo[j_start:j_last + 1]
            zh = res_un_hi[j_start:j_last + 1]
            valid = ~np.isnan(zl)
            if valid.any():
                if np.any((h_i >= zl[valid]) & (h_i <= zh[valid]) & (c_i <= zh[valid])):
                    out["touch_res_any"][t] = True

    return out


def make_signal(buy_mask: np.ndarray, sell_mask: np.ndarray) -> np.ndarray:
    """1 = CALL, 0 = PUT, -1 = sem sinal (se ambos True por acaso, anula para -1)."""
    return np.where(buy_mask & ~sell_mask, 1, np.where(sell_mask & ~buy_mask, 0, -1)).astype(int)


def run_product_b_after_m5_touch(df_raw: pd.DataFrame, zones: dict[str, np.ndarray]):
    """
    Testa a segunda parte da ideia do usuário:
    "ver o melhor momento depois do toque para confirmar a direção da próxima vela"
    -> Produto B (SEPARADO!): após o toque causal na zona M5 (em t-1), esperar os primeiros
       N segundos (N in {3, 5, 10}) da vela `t` e verificar se a reação contrária nos primeiros
       N segundos bate o baseline justo Follow_N!
    Usa BTCUSDT_1s_2d.parquet (2 dias = amostra curta declarada!).
    """
    print("\n" + "=" * 135)
    print("PRODUTO B APOS TOQUE EM ZONA DE PAVIOS M5 (1s, 2 DIAS = AMOSTRA CURTA DECLARADA)")
    print("Compara entrar apos N segundos confirmando reversao do toque M5 vs Baseline Justo Follow_N.")
    print("=" * 135)
    if not KLINES_1S_PARQUET.exists():
        print("NAO_MEDIDO: klines 1s nao encontrado.")
        return

    df1s = pd.read_parquet(KLINES_1S_PARQUET).sort_values("open_time").reset_index(drop=True)
    df1s["minute"] = df1s["open_time"].dt.floor("1min")
    df1s["sec"] = df1s["open_time"].dt.second
    df1s["delta"] = 2.0 * df1s["taker_buy_base"] - df1s["volume"]

    # Mapa de sinais de toque M5 por open_time da vela t
    sig_ov = make_signal(zones["touch_sup_overlap"], zones["touch_res_overlap"])
    sig_fl = make_signal(zones["touch_sup_flip"], zones["touch_res_flip"])
    touch_map = dict(zip(df_raw["open_time"], zip(sig_ov, sig_fl)))

    rows = []
    for m_ts, grp in df1s.groupby("minute"):
        if len(grp) != 60 or m_ts not in touch_map:
            continue
        t_ov, t_fl = touch_map[m_ts]
        o = float(grp["open"].iloc[0])
        c = float(grp["close"].iloc[-1])
        lbl = 1 if c > o else (0 if c < o else -1)
        row = {
            "open_time": m_ts, "label": lbl, "body_abs": abs(c - o),
            "candle_ret": (c - o) / o if o > 0 else 0.0,
            "t_ov": t_ov, "t_fl": t_fl
        }
        for N in [3, 5, 10]:
            g = grp[grp["sec"] < N]
            cn = float(g["close"].iloc[-1])
            dn = float(g["delta"].sum())
            row[f"follow_{N}"] = 1 if cn > o else (0 if cn < o else -1)
            row[f"delta_{N}"] = 1 if dn > 0 else (0 if dn < 0 else -1)
        rows.append(row)

    dfm = pd.DataFrame(rows)
    y = dfm["label"].values
    w = dfm["body_abs"].values
    r = dfm["candle_ret"].values

    for N in [3, 5, 10]:
        bl = dfm[f"follow_{N}"].values
        d_n = dfm[f"delta_{N}"].values
        t_ov = dfm["t_ov"].values
        t_fl = dfm["t_fl"].values

        m_bl = evaluate_predictions(y, bl, w, r)
        # Toque M5 (Overlap ou Flip) + primeiros N segundos virando a favor da reversão!
        p_ov_rev = np.where((t_ov >= 0) & (bl == t_ov), t_ov, -1)
        p_fl_rev = np.where((t_fl >= 0) & (bl == t_fl), t_fl, -1)
        p_fl_rev_d = np.where((t_fl >= 0) & (bl == t_fl) & (d_n == t_fl), t_fl, -1)

        print(f"\n  [Produto B — N={N}s]")
        print(f"    {'BL_Follow_' + str(N) + 's':<28} | N={m_bl['n_sig']:>4} ({m_bl['coverage']*100:5.1f}%) | "
              f"Acc={m_bl['acc']*100:5.2f}% [{m_bl['wilson_lo']*100:5.2f}%,{m_bl['wilson_hi']*100:5.2f}%] | "
              f"WAcc={m_bl['acc_weighted']*100:5.2f}% | Dir={m_bl['ret_dir_bps']:+5.2f}bp")
        for nm, pv in [
            (f"M5Overlap+Follow_{N}s", p_ov_rev),
            (f"M5Flip+Follow_{N}s", p_fl_rev),
            (f"M5Flip+Follow+Delta_{N}s", p_fl_rev_d),
        ]:
            mm = evaluate_predictions(y, pv, w, r)
            lift = (mm["acc"] - m_bl["acc"]) * 100
            wlift = (mm["acc_weighted"] - m_bl["acc_weighted"]) * 100
            beats = (mm["n_sig"] >= 300) and (lift >= 3.0) and (mm["wilson_lo"] > m_bl["acc"]) and (wlift > 0)
            dec = "CANDIDATA (2d curta)" if beats else ("INCONCLUSIVA (N<300)" if mm["n_sig"] < 300 else "MORRE")
            print(f"    {nm:<28} | N={mm['n_sig']:>4} ({mm['coverage']*100:5.1f}%) | "
                  f"Acc={mm['acc']*100:5.2f}% [{mm['wilson_lo']*100:5.2f}%,{mm['wilson_hi']*100:5.2f}%] | "
                  f"Lift={lift:+5.2f}pp | WAcc={mm['acc_weighted']*100:5.2f}% ({wlift:+5.2f}pp) | Dir={mm['ret_dir_bps']:+5.2f}bp | {dec}")


def main():
    print("=" * 135)
    print("TESTE RIGOROSO — HIPOTESE DO USUARIO: TOQUE EM ZONA DE PAVIOS M5 (PAR VERDE/VERMELHA) + CONFIRMACOES")
    print("=" * 135)

    df_spot = compute_frozen_labels(pd.read_parquet(DATA_PARQUET))
    df_perp = pd.read_parquet(PERP_PARQUET).sort_values("open_time").reset_index(drop=True)
    df = df_spot.merge(
        df_perp[["open_time", "close"]], on="open_time", how="inner", suffixes=("", "_perp")
    ).sort_values("open_time").reset_index(drop=True)

    df["date"] = df["open_time"].dt.date
    unique_dates = sorted(df["date"].unique())
    ho_dates = unique_dates[-HOLDOUT_DAYS:]
    dev_dates = unique_dates[:-HOLDOUT_DAYS]
    wf_test_dates = dev_dates[TRAIN_DAYS + 1:]

    oos_mask = df["date"].isin(wf_test_dates).values
    ho_mask = df["date"].isin(ho_dates).values
    dates_arr = df["date"].values
    n = len(df)

    y_oos = df["label"].values[oos_mask]
    w_oos = df["body_abs"].values[oos_mask]
    r_oos = df["candle_ret"].values[oos_mask]
    oos_dates_sorted = sorted(np.unique(dates_arr[oos_mask]))
    weeks_dates = [oos_dates_sorted[i:i + 7] for i in range(0, len(oos_dates_sorted), 7)]

    # Melhor baseline na janela Dev OOS (B2_vela_anterior)
    prev_lbl = np.full(n, -1, dtype=int); prev_lbl[1:] = df["label"].values[:-1]
    m_b2 = evaluate_predictions(y_oos, prev_lbl[oos_mask], w_oos, r_oos)
    best_bl_acc = m_b2["acc"]
    best_bl_wacc = m_b2["acc_weighted"]

    print(f"Janela Dev OOS : {wf_test_dates[0]} -> {wf_test_dates[-1]} (N={len(y_oos)}, 7 semanas)")
    print(f"Checagem 7d fin: {ho_dates[0]} -> {ho_dates[-1]} (NOTA: janela ja aberta na Fase 5 — NAO e holdout virgem)")
    print(f"Melhor Baseline: B2_vela_anterior | Acc={best_bl_acc*100:.2f}% | WAcc={best_bl_wacc*100:.2f}% | Dir={m_b2['ret_dir_bps']:+.2f}bp")
    print("-" * 135)

    # 1. Constrói zonas causais de pavios M5 (últimas 24 barras M5 fechadas = 2 horas)
    zones = build_causal_m5_wick_zones(df, lookback_m5=24)

    # 2. Features causais auxiliares (todas observadas no fechamento de t-1, alinhadas à vela t!)
    o = df["open"].astype(float).values
    h = df["high"].astype(float).values
    l = df["low"].astype(float).values
    c = df["close"].astype(float).values
    v = df["volume"].astype(float).values
    tbb = df["taker_buy_base"].astype(float).values

    # Tendência M5 (EMA20 da última barra M5 JÁ FECHADA) e M1 (EMA9 > EMA21 e close > EMA50)
    ema20_5m = build_closed_htf_ema_bias(df, "5min", ema_span=20)
    ema20_15m = build_closed_htf_ema_bias(df, "15min", ema_span=20)
    c_s = pd.Series(c)
    ema9_m1 = c_s.ewm(span=9, adjust=False).mean().values
    ema21_m1 = c_s.ewm(span=21, adjust=False).mean().values
    ema50_m1 = c_s.ewm(span=50, adjust=False).mean().values
    trend_m1_prev = np.full(n, -1, dtype=int)
    trend_m1_prev[1:] = np.where((ema9_m1[:-1] > ema21_m1[:-1]) & (c[:-1] > ema50_m1[:-1]), 1,
                                 np.where((ema9_m1[:-1] < ema21_m1[:-1]) & (c[:-1] < ema50_m1[:-1]), 0, -1))

    # Delta agressor M1 em t-1 (absorção de ordens contrárias!)
    delta_m1 = np.where(v > 0, (2.0 * tbb - v) / v, 0.0)
    delta_prev = np.full(n, 0.0); delta_prev[1:] = delta_m1[:-1]

    # Pavio de rejeição da vela M1 em t-1
    rng = np.maximum(1e-9, h - l)
    lo_wick = (np.minimum(o, c) - l) / rng
    hi_wick = (h - np.maximum(o, c)) / rng
    lo_wick_prev = np.full(n, 0.0); lo_wick_prev[1:] = lo_wick[:-1]
    hi_wick_prev = np.full(n, 0.0); hi_wick_prev[1:] = hi_wick[:-1]

    # RSI(14) causal em t-1
    dc = c_s.diff()
    gain = dc.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-dc.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi14 = (100 - 100 / (1 + gain / loss.replace(0, np.nan))).fillna(50.0).values
    rsi_prev = np.full(n, 50.0); rsi_prev[1:] = rsi14[:-1]

    # Volume spike causal em t-1 (vol > 1.5x média 20)
    vol_ma20 = pd.Series(v).rolling(20).mean().values
    vol_ratio_prev = np.full(n, 1.0)
    vol_ratio_prev[1:] = np.where(vol_ma20[:-1] > 0, v[:-1] / vol_ma20[:-1], 1.0)

    # Basis z60 causal em t-1
    basis = (df["close_perp"].values - c) / c
    basis_s = pd.Series(basis)
    basis_z = ((basis_s - basis_s.rolling(60).mean()) / basis_s.rolling(60).std().replace(0, np.nan)).values
    basis_z_prev = np.full(n, np.nan); basis_z_prev[1:] = basis_z[:-1]

    # Atalhos das zonas
    sup_ov, res_ov = zones["touch_sup_overlap"], zones["touch_res_overlap"]
    sup_fl, res_fl = zones["touch_sup_flip"], zones["touch_res_flip"]
    sup_an, res_an = zones["touch_sup_any"], zones["touch_res_any"]

    # Bateria pré-especificada de hipóteses (REVERSÃO no toque + teste de CONTINUAÇÃO/ROMPIMENTO)
    candidates = [
        # A. Ablação pura da zona (sem indicador extra)
        ("M5Wick_Overlap_REVERSAO",      make_signal(sup_ov, res_ov)),
        ("M5Wick_Overlap_ROMPIMENTO",    make_signal(res_ov, sup_ov)),  # sinal invertido!
        ("M5Wick_Flip_REVERSAO",         make_signal(sup_fl, res_fl)),
        ("M5Wick_Flip_ROMPIMENTO",       make_signal(res_fl, sup_fl)),  # sinal invertido!
        ("M5Wick_Any_REVERSAO",          make_signal(sup_an, res_an)),

        # B. Zona + Indicadores de Tendência Alta/Baixa (exatamente como usuário descreveu)
        ("M5Ov+EMA20_5m_tendencia",      make_signal(sup_ov & (ema20_5m == 1), res_ov & (ema20_5m == 0))),
        ("M5Fl+EMA20_5m_tendencia",      make_signal(sup_fl & (ema20_5m == 1), res_fl & (ema20_5m == 0))),
        ("M5Fl+EMA20_15m_tendencia",     make_signal(sup_fl & (ema20_15m == 1), res_fl & (ema20_15m == 0))),
        ("M5Fl+EMA_M1_9_21_50",          make_signal(sup_fl & (trend_m1_prev == 1), res_fl & (trend_m1_prev == 0))),

        # C. Zona + Acúmulo de ordens contrárias (Absorção CVD / Pavio / Volume / RSI)
        ("M5Ov+Absorcao_CVD_contrario",  make_signal(sup_ov & (delta_prev > 0), res_ov & (delta_prev < 0))),
        ("M5Fl+Absorcao_CVD_contrario",  make_signal(sup_fl & (delta_prev > 0), res_fl & (delta_prev < 0))),
        ("M5Fl+Absorcao_CVD+EMA5m",      make_signal(sup_fl & (delta_prev > 0) & (ema20_5m == 1), res_fl & (delta_prev < 0) & (ema20_5m == 0))),
        ("M5Ov+PavioRejeicao40_M1",      make_signal(sup_ov & (lo_wick_prev >= 0.40), res_ov & (hi_wick_prev >= 0.40))),
        ("M5Fl+PavioRejeicao40_M1",      make_signal(sup_fl & (lo_wick_prev >= 0.40), res_fl & (hi_wick_prev >= 0.40))),
        ("M5Fl+Pavio40+EMA5m",           make_signal(sup_fl & (lo_wick_prev >= 0.40) & (ema20_5m == 1), res_fl & (hi_wick_prev >= 0.40) & (ema20_5m == 0))),
        ("M5Fl+VolSpike1.5+AbsCVD",      make_signal(sup_fl & (vol_ratio_prev >= 1.5) & (delta_prev > 0), res_fl & (vol_ratio_prev >= 1.5) & (delta_prev < 0))),
        ("M5Fl+VolSpike1.5+Pavio40",     make_signal(sup_fl & (vol_ratio_prev >= 1.5) & (lo_wick_prev >= 0.40), res_fl & (vol_ratio_prev >= 1.5) & (hi_wick_prev >= 0.40))),
        ("M5Fl+RSI_extremo_35_65",       make_signal(sup_fl & (rsi_prev < 35), res_fl & (rsi_prev > 65))),

        # D. Zona + Basis Perp (confluência com único efeito físico real da Fase 3)
        ("M5Fl+Basis_z60_a_favor",       make_signal(sup_fl & (basis_z_prev > 0.5), res_fl & (basis_z_prev < -0.5))),
        ("M5Ov+Basis_z60_a_favor",       make_signal(sup_ov & (basis_z_prev > 0.5), res_ov & (basis_z_prev < -0.5))),
    ]

    print("TABELA 1 — RESULTADOS NA JANELA DEV OOS (44 DIAS, N=63.360, PRODUTO A, EMPATE = ERRO)")
    print("=" * 135)
    survivors = []
    for nm, pv in candidates:
        res = eval_hypothesis_full(nm, pv, df, oos_mask, best_bl_acc, best_bl_wacc, weeks_dates)
        print_hyp_result(res)
        if res["decision"] == "SOBREVIVE":
            survivors.append((nm, pv, res))

    # Checagem secundária nos últimos 7 dias (já abertos na Fase 5 — não é holdout virgem)
    print("\n" + "=" * 135)
    print("TABELA 2 — CHECAGEM SECUNDARIA NOS ULTIMOS 7 DIAS (2026-11-11..17, N=9.863 — NAO E HOLDOUT VIRGEM)")
    print("=" * 135)
    y_ho = df["label"].values[ho_mask]
    w_ho = df["body_abs"].values[ho_mask]
    r_ho = df["candle_ret"].values[ho_mask]
    for nm, pv in [
        ("M5Wick_Overlap_REVERSAO", candidates[0][1]),
        ("M5Wick_Flip_REVERSAO", candidates[2][1]),
        ("M5Fl+EMA20_5m_tendencia", candidates[6][1]),
        ("M5Fl+Absorcao_CVD_contrario", candidates[10][1]),
        ("M5Fl+PavioRejeicao40_M1", candidates[13][1]),
        ("M5Fl+Basis_z60_a_favor", candidates[18][1]),
    ]:
        mm = evaluate_predictions(y_ho, pv[ho_mask], w_ho, r_ho)
        print(f"  {nm:<28} | N={mm['n_sig']:>4} ({mm['coverage']*100:5.1f}%) | "
              f"Acc={mm['acc']*100:5.2f}% [{mm['wilson_lo']*100:5.2f}%,{mm['wilson_hi']*100:5.2f}%] | "
              f"WAcc={mm['acc_weighted']*100:5.2f}% | CALL={mm['ret_call_bps']:+5.2f}bp PUT={mm['ret_put_bps']:+5.2f}bp Dir={mm['ret_dir_bps']:+5.2f}bp")

    # Produto B após toque M5 (2 dias 1s)
    run_product_b_after_m5_touch(df, zones)

    print("\n" + "=" * 135)
    print(f"RESUMO FINAL (PRODUTO A, 20 variantes testadas): Sobreviventes = {[s[0] for s in survivors]}")
    print("=" * 135)


if __name__ == "__main__":
    main()
