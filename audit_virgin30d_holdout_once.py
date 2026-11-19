"""
audit_virgin30d_holdout_once.py
===============================
ABERTURA UNICA DO NOVO HOLDOUT 100% VIRGEM DE 30 DIAS (2026-08-14 a 2026-09-13, N ~ 43.200 velas)!
Dataset completamente inédito (warmup de 15d anteriores 2026-07-30..2026-08-13 usado apenas para
calibrar os quantis causais de 14d com purga de 200 velas).

NENHUM parametro, janela ou limiar foi ajustado neste holdout.
Candidatos congelados previamente na janela Dev:
  1. Q1_SNR_perp60_sig15_Q80   (derivação física: (basis_perp - EMA60) / sigma_15m, Q80 causal)
  2. Q3b_MultiAgree_15_Q80     (multi-venue: Perp + BTCUSDC concordando em dev15 / sigma_15m, Q80 causal)
Referências:
  - 4 baselines ingênuos (B1, B2, B3, B4)
  - ATUAL_confluences_py (código antigo do repositório)
  - REF_basis_z60_Q80 (z-score clássico da Fase 3)
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from audit_phase2_baseline import (
    TRAIN_DAYS, PURGE_BARS,
    compute_frozen_labels, compute_confluences_vectorized,
    build_last_closed_5m_signal, evaluate_predictions, falsification_shuffle_intraday
)
from audit_phase3_hypotheses import causal_rolling_threshold_mask
from audit_quant_research_v2 import build_features_frame

V_SPOT = REPO_ROOT / "audit_data" / "BTCUSDT_spot_1m_virgin45d.parquet"
V_PERP = REPO_ROOT / "audit_data" / "BTCUSDT_perp_1m_virgin45d.parquet"
V_USDC = REPO_ROOT / "audit_data" / "BTCUSDC_spot_1m_virgin45d.parquet"


def fmt(name: str, m: dict, ref_acc: float) -> str:
    lift = (m["acc"] - ref_acc) * 100
    return (
        f"{name:<28} | N={m['n_sig']:>5} ({m['coverage']*100:5.1f}%) | "
        f"Acc={m['acc']*100:5.2f}% [{m['wilson_lo']*100:5.2f}%,{m['wilson_hi']*100:5.2f}%] | "
        f"Lift={lift:+6.2f}pp | ExTie={m['acc_ex_tie']*100:5.2f}% (Tie={m['tie_rate']*100:4.2f}%) | "
        f"WAcc={m['acc_weighted']*100:5.2f}% | Dir={m['ret_dir_bps']:+5.2f}bp"
    )


def main():
    df = build_features_frame(
        pd.read_parquet(V_SPOT),
        pd.read_parquet(V_PERP),
        pd.read_parquet(V_USDC),
    )
    df["date"] = df["open_time"].dt.date
    unique_dates = sorted(df["date"].unique())

    # Primeiros 15 dias = warmup/calibração causal de 14d + purga 200; últimos 30 dias = HOLDOUT VIRGEM!
    virgin_dates = unique_dates[TRAIN_DAYS + 1:]
    v_mask = df["date"].isin(virgin_dates).values
    dates_arr = df["date"].values
    n = len(df)

    y = df["label"].values
    w = df["body_abs"].values
    r = df["candle_ret"].values

    # Baselines
    b1 = np.full(n, -1, dtype=int)
    for td in virgin_dates:
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

    # Sinais congelados
    c = df["close"].astype(float).values
    cp = df["close_perp"].astype(float).values
    cu = df["close_usdc"].astype(float).values

    sig15 = pd.Series(c).pct_change(1).rolling(15).std().replace(0, np.nan).values
    sig15_s = np.maximum(sig15, 5e-5)

    basis_p = (cp - c) / c
    bp_s = pd.Series(basis_p)
    bp_z60 = ((bp_s - bp_s.rolling(60).mean()) / bp_s.rolling(60).std().replace(0, np.nan)).values
    bp_dev60 = (bp_s - bp_s.ewm(span=60, adjust=False).mean()).values
    bp_dev15 = (bp_s - bp_s.ewm(span=15, adjust=False).mean()).values

    basis_u = (cu - c) / c
    bu_s = pd.Series(basis_u)
    bu_dev15 = (bu_s - bu_s.ewm(span=15, adjust=False).mean()).values

    snr_p60 = bp_dev60 / sig15_s
    snr_p15 = bp_dev15 / sig15_s
    snr_u15 = bu_dev15 / sig15_s
    snr_mv15 = 0.5 * (snr_p15 + snr_u15)

    def lag1(arr):
        out = np.full(n, np.nan); out[1:] = arr[:-1]; return out

    bp_z60_l = lag1(bp_z60)
    snr_p60_l = lag1(snr_p60)
    snr_p15_l = lag1(snr_p15)
    snr_u15_l = lag1(snr_u15)
    snr_mv15_l = lag1(snr_mv15)

    m_z80 = causal_rolling_threshold_mask(bp_z60_l, dates_arr, virgin_dates, q=0.80)
    p_ref_z60 = np.where(m_z80 & (bp_z60_l > 0), 1, np.where(m_z80 & (bp_z60_l < 0), 0, -1)).astype(int)

    m_q1_80 = causal_rolling_threshold_mask(snr_p60_l, dates_arr, virgin_dates, q=0.80)
    p_q1_snr = np.where(m_q1_80 & (snr_p60_l > 0), 1, np.where(m_q1_80 & (snr_p60_l < 0), 0, -1)).astype(int)

    m_mv80 = causal_rolling_threshold_mask(snr_mv15_l, dates_arr, virgin_dates, q=0.80)
    agree_up = (snr_p15_l > 0) & (snr_u15_l > 0)
    agree_dn = (snr_p15_l < 0) & (snr_u15_l < 0)
    p_q3b_mv = np.where(m_mv80 & agree_up, 1, np.where(m_mv80 & agree_dn, 0, -1)).astype(int)

    print("=" * 140)
    print(f"ABERTURA UNICA — NOVO HOLDOUT 100% VIRGEM DE 30 DIAS: {virgin_dates[0]} -> {virgin_dates[-1]} (N={int(v_mask.sum())} velas)")
    print(f"Distribuicao Virgem: Verde={(y[v_mask]==1).mean()*100:.2f}% | Vermelha={(y[v_mask]==0).mean()*100:.2f}% | Empate={(y[v_mask]==-1).mean()*100:.2f}%")
    print("=" * 140)

    bl_metrics = [
        evaluate_predictions(y[v_mask], b1[v_mask], w[v_mask], r[v_mask]),
        evaluate_predictions(y[v_mask], b2[v_mask], w[v_mask], r[v_mask]),
        evaluate_predictions(y[v_mask], b3[v_mask], w[v_mask], r[v_mask]),
        evaluate_predictions(y[v_mask], b4[v_mask], w[v_mask], r[v_mask]),
    ]
    best_bl_acc = max(m["acc"] for m in bl_metrics)

    items = [
        ("B1_majoritaria_treino", b1),
        ("B2_vela_anterior", b2),
        ("B3_contrario_vela_ant", b3),
        ("B4_ultima_5m_fechada", b4),
        ("ATUAL_confluences_py", p_conf),
        ("REF_basis_z60_Q80", p_ref_z60),
        ("Q1_SNR_perp60_sig15_Q80", p_q1_snr),
        ("Q3b_MultiAgree_15_Q80", p_q3b_mv),
    ]
    for nm, pv in items:
        mm = evaluate_predictions(y[v_mask], pv[v_mask], w[v_mask], r[v_mask])
        print(fmt(nm, mm, best_bl_acc))

    # Quebra semanal dos 30 dias virgens para os 2 candidatos congelados vs melhor baseline (B2)
    print("\n" + "=" * 140)
    print("QUEBRA SEMANAL NO HOLDOUT VIRGEM DE 30 DIAS (TODAS AS SEMANAS)")
    print("=" * 140)
    weeks = [virgin_dates[i:i + 7] for i in range(0, len(virgin_dates), 7)]
    for wi, wdays in enumerate(weeks, start=1):
        wm = np.isin(dates_arr, wdays)
        mb2 = evaluate_predictions(y[wm], b2[wm], w[wm], r[wm])
        mref = evaluate_predictions(y[wm], p_ref_z60[wm], w[wm], r[wm])
        mq1 = evaluate_predictions(y[wm], p_q1_snr[wm], w[wm], r[wm])
        mq3 = evaluate_predictions(y[wm], p_q3b_mv[wm], w[wm], r[wm])
        print(f"Sem {wi} ({wdays[0]}..{wdays[-1]}, N={int(wm.sum())}, Tie={(y[wm]==-1).mean()*100:.2f}%):")
        print(f"  B2_vela_ant : Acc={mb2['acc']*100:5.2f}% WAcc={mb2['acc_weighted']*100:5.2f}% Dir={mb2['ret_dir_bps']:+.2f}bp")
        print(f"  REF_z60_Q80 : Acc={mref['acc']*100:5.2f}% [{mref['wilson_lo']*100:5.2f}%,{mref['wilson_hi']*100:5.2f}%] WAcc={mref['acc_weighted']*100:5.2f}% Dir={mref['ret_dir_bps']:+.2f}bp (N={mref['n_sig']})")
        print(f"  Q1_SNR_Q80  : Acc={mq1['acc']*100:5.2f}% [{mq1['wilson_lo']*100:5.2f}%,{mq1['wilson_hi']*100:5.2f}%] WAcc={mq1['acc_weighted']*100:5.2f}% Dir={mq1['ret_dir_bps']:+.2f}bp (N={mq1['n_sig']})")
        print(f"  Q3b_Multi80 : Acc={mq3['acc']*100:5.2f}% [{mq3['wilson_lo']*100:5.2f}%,{mq3['wilson_hi']*100:5.2f}%] WAcc={mq3['acc_weighted']*100:5.2f}% Dir={mq3['ret_dir_bps']:+.2f}bp (N={mq3['n_sig']})")

    # Falsificação F1, F2, F3 no Holdout Virgem de 30 dias
    print("\n" + "=" * 140)
    print("FALSIFICACAO (F1, F2, F3) NO HOLDOUT VIRGEM DE 30 DIAS")
    print("=" * 140)
    for nm, pv in [("REF_basis_z60_Q80", p_ref_z60), ("Q1_SNR_perp60_sig15_Q80", p_q1_snr), ("Q3b_MultiAgree_15_Q80", p_q3b_mv)]:
        p_fut = np.full(n, -1, dtype=int); p_fut[:-1] = pv[1:]
        p_d1 = np.full(n, -1, dtype=int); p_d1[1:] = pv[:-1]
        mf1 = evaluate_predictions(y[v_mask], p_fut[v_mask], w[v_mask], r[v_mask])
        f2m, f2s = falsification_shuffle_intraday(y[v_mask], pv[v_mask], dates_arr[v_mask], n_sims=200)
        md1 = evaluate_predictions(y[v_mask], p_d1[v_mask], w[v_mask], r[v_mask])
        print(f"  {nm:<26} | F1(futuro)={mf1['acc']*100:5.2f}% | F2(shuffle)={f2m*100:5.2f}% +/- {f2s*100:.2f}% | F3(delay1)={md1['acc']*100:5.2f}% (WAcc={md1['acc_weighted']*100:5.2f}%, Dir={md1['ret_dir_bps']:+.2f}bp)")


if __name__ == "__main__":
    main()
