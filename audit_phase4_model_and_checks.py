"""
audit_phase4_model_and_checks.py
================================
1. Testes adversários adicionais em H4e_basis_z60_mom_Q80 (na janela Dev OOS — Holdout 7d AINDA FECHADO):
   - Verificação de timestamps open_time/close_time Spot vs Perp (zero desalinhamento)
   - Quebra por liquidez (num_trades do Spot em t-1 acima vs abaixo da mediana)
2. Fase 4 do protocolo (prompat.txt linha 158-162):
   - HistGradientBoostingClassifier com a única feature sobrevivente (basis_z60),
     walk-forward 14d + purga 200, sem shuffle, limiar de probabilidade calibrado
     EXCLUSIVAMENTE na validação interna (últimos 2d do bloco de treino, fora do teste),
     com tabela de calibração (reliability) reportada.
   - Comparação direta contra a regra simples H4e_basis_z60_mom_Q80 (mesma cobertura ~20%):
     se o ML não bater a regra simples por margem que passe Wilson 95%, o ML MORRE.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from audit_phase2_baseline import (
    DATA_PARQUET, TRAIN_DAYS, PURGE_BARS, HOLDOUT_DAYS,
    compute_frozen_labels, evaluate_predictions, wilson_ci
)
from audit_phase3_hypotheses import PERP_PARQUET, causal_rolling_threshold_mask


def main():
    df_spot = compute_frozen_labels(pd.read_parquet(DATA_PARQUET))
    df_perp = pd.read_parquet(PERP_PARQUET).sort_values("open_time").reset_index(drop=True)

    df = df_spot.merge(
        df_perp[["open_time", "close_time", "open", "close", "volume", "taker_buy_base"]],
        on="open_time", how="inner", suffixes=("", "_perp")
    ).sort_values("open_time").reset_index(drop=True)

    # 1. Verificação estrita de timestamps
    max_ct_diff = (df["close_time"] - df["close_time_perp"]).abs().max()
    print("=" * 120)
    print("FASE 4 — VERIFICACAO DE TIMESTAMPS, LIQUIDEZ E MODELO ML (HOLDOUT 7d AINDA FECHADO)")
    print("=" * 120)
    print(f"Alinhamento timestamps Spot vs Perp: rows={len(df)} | max |close_time_spot - close_time_perp| = {max_ct_diff}")

    df["date"] = df["open_time"].dt.date
    unique_dates = sorted(df["date"].unique())
    dev_dates = unique_dates[:-HOLDOUT_DAYS]
    wf_test_dates = dev_dates[TRAIN_DAYS + 1:]
    oos_mask = df["date"].isin(wf_test_dates).values
    dates_arr = df["date"].values
    n = len(df)

    # Feature sobrevivente única: basis_z60 causal (observado em t-1)
    basis = (df["close_perp"].values - df["close"].values) / df["close"].values
    basis_s = pd.Series(basis)
    basis_z = ((basis_s - basis_s.rolling(60).mean()) / basis_s.rolling(60).std().replace(0, np.nan)).values
    basis_z_prev = np.full(n, np.nan); basis_z_prev[1:] = basis_z[:-1]

    y = df["label"].values
    w = df["body_abs"].values
    r = df["candle_ret"].values

    # Regra simples sobrevivente: H4e_basis_z60_mom_Q80
    mask_q80 = causal_rolling_threshold_mask(basis_z_prev, dates_arr, wf_test_dates, q=0.80)
    pred_simple = np.where(mask_q80 & (basis_z_prev > 0), 1, np.where(mask_q80 & (basis_z_prev < 0), 0, -1))
    m_simple = evaluate_predictions(y[oos_mask], pred_simple[oos_mask], w[oos_mask], r[oos_mask])

    # Checagem de liquidez (num_trades do Spot em t-1)
    nt_prev = np.full(n, np.nan); nt_prev[1:] = df["num_trades"].astype(float).values[:-1]
    med_nt = np.nanmedian(nt_prev[oos_mask])
    hi_liq = oos_mask & (nt_prev >= med_nt)
    lo_liq = oos_mask & (nt_prev < med_nt)
    m_hi = evaluate_predictions(y[hi_liq], pred_simple[hi_liq], w[hi_liq], r[hi_liq])
    m_lo = evaluate_predictions(y[lo_liq], pred_simple[lo_liq], w[lo_liq], r[lo_liq])
    print(f"\nTeste de liquidez em H4e_basis_z60_mom_Q80 (mediana num_trades Spot t-1 = {med_nt:.0f}):")
    print(f"  Alta liquidez (trades >= {med_nt:.0f}): N={m_hi['n_sig']:>5} | Acc={m_hi['acc']*100:5.2f}% [{m_hi['wilson_lo']*100:5.2f}%,{m_hi['wilson_hi']*100:5.2f}%] | WAcc={m_hi['acc_weighted']*100:5.2f}% | Dir={m_hi['ret_dir_bps']:+.2f}bp")
    print(f"  Baixa liquidez (trades < {med_nt:.0f}): N={m_lo['n_sig']:>5} | Acc={m_lo['acc']*100:5.2f}% [{m_lo['wilson_lo']*100:5.2f}%,{m_lo['wilson_hi']*100:5.2f}%] | WAcc={m_lo['acc_weighted']*100:5.2f}% | Dir={m_lo['ret_dir_bps']:+.2f}bp")

    # 2. Fase 4: HistGradientBoostingClassifier usando SOMENTE basis_z_prev
    # Walk-forward: 12d treino + 200 purga + 2d validação (escolhe limiar p/ cobertura ~20%) + 200 purga + 1d teste
    probs_ml = np.full(n, np.nan)
    pred_ml_all = np.full(n, -1, dtype=int)
    pred_ml_cov20 = np.full(n, -1, dtype=int)

    for td in wf_test_dates:
        te_idx = np.where(dates_arr == td)[0]
        if len(te_idx) == 0:
            continue
        first_te = int(te_idx[0])
        val_end = first_te - PURGE_BARS
        val_start = val_end - 2 * 1440
        tr_end = val_start - PURGE_BARS
        tr_start = max(0, tr_end - 12 * 1440)
        if tr_end - tr_start < 4000 or val_end - val_start < 1000:
            continue

        tr_slice = np.arange(tr_start, tr_end)
        val_slice = np.arange(val_start, val_end)
        tr_valid = tr_slice[~np.isnan(basis_z_prev[tr_slice]) & (y[tr_slice] >= 0)]
        val_valid = val_slice[~np.isnan(basis_z_prev[val_slice])]
        te_valid = te_idx[~np.isnan(basis_z_prev[te_idx])]
        if len(tr_valid) < 3000 or len(val_valid) < 500 or len(te_valid) == 0:
            continue

        clf = HistGradientBoostingClassifier(
            max_iter=60, max_depth=3, learning_rate=0.05, min_samples_leaf=80, random_state=42
        )
        clf.fit(basis_z_prev[tr_valid].reshape(-1, 1), y[tr_valid])

        p_val = clf.predict_proba(basis_z_prev[val_valid].reshape(-1, 1))[:, 1]
        # Escolhe limiar de distância |p - 0.5| na VALIDAÇÃO para ~20% cobertura (fora do teste!)
        th_dist = float(np.quantile(np.abs(p_val - 0.5), 0.80))

        p_te = clf.predict_proba(basis_z_prev[te_valid].reshape(-1, 1))[:, 1]
        probs_ml[te_valid] = p_te
        pred_ml_all[te_valid] = (p_te >= 0.5).astype(int)
        pred_ml_cov20[te_valid] = np.where(
            np.abs(p_te - 0.5) >= th_dist,
            (p_te >= 0.5).astype(int),
            -1
        )

    m_ml_all = evaluate_predictions(y[oos_mask], pred_ml_all[oos_mask], w[oos_mask], r[oos_mask])
    m_ml_20 = evaluate_predictions(y[oos_mask], pred_ml_cov20[oos_mask], w[oos_mask], r[oos_mask])

    print("\n" + "=" * 120)
    print("COMPARACAO FASE 4: REGRA SIMPLES vs HistGradientBoostingClassifier (JANELA DEV OOS)")
    print("=" * 120)
    for nm, mm in [
        ("Regra_Simples_basis_z60_Q80", m_simple),
        ("HGBC_basis_z60_cov100", m_ml_all),
        ("HGBC_basis_z60_cov20_val", m_ml_20),
    ]:
        print(f"{nm:<28} | N={mm['n_sig']:>6} ({mm['coverage']*100:5.1f}%) | "
              f"Acc={mm['acc']*100:5.2f}% [{mm['wilson_lo']*100:5.2f}%,{mm['wilson_hi']*100:5.2f}%] | "
              f"WAcc={mm['acc_weighted']*100:5.2f}% | Dir={mm['ret_dir_bps']:+.2f}bp")

    # Calibração (reliability) do HGBC no OOS (sobre velas não-empate)
    valid_cal = oos_mask & ~np.isnan(probs_ml) & (y >= 0)
    brier = brier_score_loss(y[valid_cal], probs_ml[valid_cal])
    print(f"\nCalibracao (Reliability) HGBC no OOS (velas nao-empate, N={int(valid_cal.sum())}, Brier={brier:.5f}):")
    bins = np.linspace(0.35, 0.65, 7)
    p_cal = probs_ml[valid_cal]
    y_cal = y[valid_cal]
    for b_lo, b_hi in zip(bins[:-1], bins[1:]):
        bm = (p_cal >= b_lo) & (p_cal < b_hi)
        if bm.sum() > 0:
            print(f"  Prob [{b_lo:.2f}, {b_hi:.2f}): N={int(bm.sum()):>6} | P_prev_media={p_cal[bm].mean():.4f} | Freq_Verde_Real={y_cal[bm].mean():.4f}")

    beats_simple = (m_ml_20["acc"] > m_simple["acc"]) and (m_ml_20["wilson_lo"] > m_simple["acc"])
    print(f"\nDecisao Fase 4 (HGBC bate regra simples passando Wilson 95%?): {beats_simple} -> "
          f"{'SOBREVIVE HGBC' if beats_simple else 'MORRE HGBC — FICA A REGRA SIMPLES'}")


if __name__ == "__main__":
    main()
