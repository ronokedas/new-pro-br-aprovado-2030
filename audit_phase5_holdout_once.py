"""
audit_phase5_holdout_once.py
============================
ABERTURA UNICA DO HOLDOUT FINAL (ultimos 7 dias: 2026-11-11 a 2026-11-17).
Nenhum parametro, feature ou limiar e ajustado aqui — tudo foi congelado na Fase 3/4.

Avalia no Holdout Final:
  1. Os 4 baselines ingênuos (B1, B2, B3, B4)
  2. Sistema atual (engine/confluences.py)
  3. Regra simples sobrevivente congelada (H4e_basis_z60_mom_Q80)
  4. Teste extra OCO (apenas registro final)
  5. Testes de falsificação F1, F2, F3 no Holdout
  6. Teste adversário de LATENCIA DE EXECUCAO usando BTCUSDT_1s_2d.parquet (2026-11-15..17, dentro do Holdout):
     mede se o edge de H4e_basis_z60_mom_Q80 sobrevive ao entrar em open_t + 1s, +2s, +3s, +5s,
     ou se o ajuste Spot->Perp ja aconteceu nos primeiros segundos!
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from audit_phase2_baseline import (
    DATA_PARQUET, TRAIN_DAYS, PURGE_BARS, HOLDOUT_DAYS,
    compute_frozen_labels, compute_confluences_vectorized,
    build_last_closed_5m_signal, evaluate_predictions, falsification_shuffle_intraday
)
from audit_phase3_hypotheses import (
    PERP_PARQUET, KLINES_1S_PARQUET, causal_rolling_threshold_mask, detect_causal_oco_signals
)


def fmt_line(name: str, m: dict, ref_acc: float) -> str:
    lift = (m["acc"] - ref_acc) * 100
    return (
        f"{name:<28} | N={m['n_sig']:>5} ({m['coverage']*100:5.1f}%) | "
        f"Acc={m['acc']*100:5.2f}% [{m['wilson_lo']*100:5.2f}%,{m['wilson_hi']*100:5.2f}%] | "
        f"Lift={lift:+6.2f}pp | ExTie={m['acc_ex_tie']*100:5.2f}% | WAcc={m['acc_weighted']*100:5.2f}% | "
        f"CALL={m['ret_call_bps']:+5.2f}bp PUT={m['ret_put_bps']:+5.2f}bp Dir={m['ret_dir_bps']:+5.2f}bp"
    )


def main():
    df_spot = compute_frozen_labels(pd.read_parquet(DATA_PARQUET))
    df_perp = pd.read_parquet(PERP_PARQUET).sort_values("open_time").reset_index(drop=True)
    df = df_spot.merge(
        df_perp[["open_time", "open", "close", "volume", "taker_buy_base"]],
        on="open_time", how="inner", suffixes=("", "_perp")
    ).sort_values("open_time").reset_index(drop=True)

    df["date"] = df["open_time"].dt.date
    unique_dates = sorted(df["date"].unique())
    holdout_dates = unique_dates[-HOLDOUT_DAYS:]
    ho_mask = df["date"].isin(holdout_dates).values
    dates_arr = df["date"].values
    n = len(df)

    y = df["label"].values
    w = df["body_abs"].values
    r = df["candle_ret"].values

    # B1 (majoritaria treino 14d com purga 200)
    b1 = np.full(n, -1, dtype=int)
    for td in holdout_dates:
        te_idx = np.where(dates_arr == td)[0]
        first_te = int(te_idx[0])
        tr_end = first_te - PURGE_BARS
        tr_start = max(0, tr_end - TRAIN_DAYS * 1440)
        tr_y = y[tr_start:tr_end]
        b1[te_idx] = 1 if (tr_y == 1).sum() >= (tr_y == 0).sum() else 0

    b2 = np.full(n, -1, dtype=int); b2[1:] = y[:-1]
    b3 = np.where(b2 == 1, 0, np.where(b2 == 0, 1, -1))
    b4 = build_last_closed_5m_signal(df)
    p_conf = compute_confluences_vectorized(df)

    # Regra simples congelada: H4e_basis_z60_mom_Q80
    basis = (df["close_perp"].values - df["close"].values) / df["close"].values
    basis_s = pd.Series(basis)
    basis_z = ((basis_s - basis_s.rolling(60).mean()) / basis_s.rolling(60).std().replace(0, np.nan)).values
    basis_z_prev = np.full(n, np.nan); basis_z_prev[1:] = basis_z[:-1]

    mask_q80 = causal_rolling_threshold_mask(basis_z_prev, dates_arr, holdout_dates, q=0.80)
    p_basis = np.where(mask_q80 & (basis_z_prev > 0), 1, np.where(mask_q80 & (basis_z_prev < 0), 0, -1))

    # Extra OCO (melhor variante da Fase 3, apenas para auditoria final)
    p_oco = detect_causal_oco_signals(df["close"].astype(float).values, order_r=2, lookback=60, sym_tol=0.0025, hold_bars=1)

    print("=" * 135)
    print(f"ABERTURA UNICA DO HOLDOUT FINAL: {holdout_dates[0]} -> {holdout_dates[-1]} (N={int(ho_mask.sum())} velas)")
    print(f"Distribuicao Holdout: Verde={(y[ho_mask]==1).mean()*100:.2f}% | Vermelha={(y[ho_mask]==0).mean()*100:.2f}% | Empate={(y[ho_mask]==-1).mean()*100:.2f}%")
    print("=" * 135)

    m_b1 = evaluate_predictions(y[ho_mask], b1[ho_mask], w[ho_mask], r[ho_mask])
    m_b2 = evaluate_predictions(y[ho_mask], b2[ho_mask], w[ho_mask], r[ho_mask])
    m_b3 = evaluate_predictions(y[ho_mask], b3[ho_mask], w[ho_mask], r[ho_mask])
    m_b4 = evaluate_predictions(y[ho_mask], b4[ho_mask], w[ho_mask], r[ho_mask])
    best_bl_acc = max(m_b1["acc"], m_b2["acc"], m_b3["acc"], m_b4["acc"])

    for nm, pv in [
        ("B1_majoritaria_treino", b1),
        ("B2_vela_anterior", b2),
        ("B3_contrario_vela_ant", b3),
        ("B4_ultima_5m_fechada", b4),
        ("ATUAL_confluences_py", p_conf),
        ("H4e_basis_z60_mom_Q80", p_basis),
        ("EXTRA_OCO_R2_tol25bp_h1", p_oco),
    ]:
        mm = evaluate_predictions(y[ho_mask], pv[ho_mask], w[ho_mask], r[ho_mask])
        print(fmt_line(nm, mm, best_bl_acc))

    # Falsificação de H4e_basis_z60_mom_Q80 no Holdout
    print("\n--- Falsificacao de H4e_basis_z60_mom_Q80 no Holdout ---")
    p_fut = np.full(n, -1, dtype=int); p_fut[:-1] = p_basis[1:]
    p_d1 = np.full(n, -1, dtype=int); p_d1[1:] = p_basis[:-1]
    mf1 = evaluate_predictions(y[ho_mask], p_fut[ho_mask], w[ho_mask], r[ho_mask])
    f2m, f2s = falsification_shuffle_intraday(y[ho_mask], p_basis[ho_mask], dates_arr[ho_mask], n_sims=200)
    md1 = evaluate_predictions(y[ho_mask], p_d1[ho_mask], w[ho_mask], r[ho_mask])
    print(f"  F1 (feature +1 barra futuro): Acc={mf1['acc']*100:.2f}% WAcc={mf1['acc_weighted']*100:.2f}% Dir={mf1['ret_dir_bps']:+.2f}bp")
    print(f"  F2 (shuffle intra-dia)      : Acc={f2m*100:.2f}% +/- {f2s*100:.2f}%")
    print(f"  F3 (sinal atrasado +1 vela) : Acc={md1['acc']*100:.2f}% WAcc={md1['acc_weighted']*100:.2f}% Dir={md1['ret_dir_bps']:+.2f}bp")

    # Teste de sensibilidade a LATENCIA REAL DE EXECUCAO (1s, 2s, 3s, 5s, 10s dentro da vela t)
    print("\n" + "=" * 135)
    print("TESTE DE LATENCIA DE EXECUCAO EM H4e_basis_z60_mom_Q80 (USANDO KLINES 1s DE 2026-11-15..17)")
    print("Mede o retorno e acuracia de entrar em open_t + d segundos ate o close_t da vela M1.")
    print("=" * 135)
    if KLINES_1S_PARQUET.exists():
        df1s = pd.read_parquet(KLINES_1S_PARQUET).sort_values("open_time").reset_index(drop=True)
        df1s["minute"] = df1s["open_time"].dt.floor("1min")
        df1s["sec"] = df1s["open_time"].dt.second

        # Mapeia sinal p_basis por open_time (minuto)
        sig_df = df[["open_time", "open", "close", "label"]].copy()
        sig_df["pred_basis"] = p_basis
        sig_map = sig_df.set_index("open_time").to_dict("index")

        # Coleta preços em sec=0 (open_t) e close de sec=0,1,2,4,9 (ou seja, após 1s, 2s, 3s, 5s, 10s)
        delays = [0, 1, 2, 3, 5, 10]
        records = {d: [] for d in delays}
        for m_ts, grp in df1s.groupby("minute"):
            if len(grp) != 60 or m_ts not in sig_map:
                continue
            info = sig_map[m_ts]
            pred_b = info["pred_basis"]
            if pred_b < 0:
                continue
            c_final = float(grp["close"].iloc[-1])
            o_0 = float(grp["open"].iloc[0])
            dir_s = 1.0 if pred_b == 1 else -1.0
            for d in delays:
                entry_p = o_0 if d == 0 else float(grp["close"].iloc[d - 1])
                ret_rem = (c_final - entry_p) / entry_p
                lbl_rem = 1 if c_final > entry_p else (0 if c_final < entry_p else -1)
                records[d].append((lbl_rem == pred_b, abs(c_final - entry_p), dir_s * ret_rem, lbl_rem == -1))

        for d in delays:
            arr = records[d]
            if not arr:
                continue
            n_d = len(arr)
            corr = np.array([x[0] for x in arr], dtype=bool)
            w_d = np.array([x[1] for x in arr], dtype=float)
            ret_d = np.array([x[2] for x in arr], dtype=float)
            tie_d = np.array([x[3] for x in arr], dtype=bool)
            acc_d = corr.mean()
            wacc_d = w_d[corr].sum() / w_d.sum() if w_d.sum() > 0 else 0.0
            dir_bps = ret_d.mean() * 1e4
            print(f"  Latencia +{d:2d}s (entrada em open_t+{d:2d}s -> close_t): N={n_d:>4} | "
                  f"Acc={acc_d*100:5.2f}% | WAcc={wacc_d*100:5.2f}% | RetDir={dir_bps:+6.2f}bp | Empates={tie_d.mean()*100:.2f}%")
    else:
        print("NAO_MEDIDO: klines 1s nao encontrado.")


if __name__ == "__main__":
    main()
