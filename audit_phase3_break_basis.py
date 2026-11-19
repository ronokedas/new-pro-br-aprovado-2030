"""
audit_phase3_break_basis.py
===========================
Teste adversário para tentar QUEBRAR o único sobrevivente da Fase 3: H4e_basis_z60_mom_Q80.
Mantém o HOLDOUT FINAL (2026-11-11 a 2026-11-17) COMPLETAMENTE FECHADO!

Perguntas que queremos responder:
1. Quebra semanal completa (todas as 7 semanas do OOS de desenvolvimento): N, Acc, Wilson, WAcc, Dir (bps), CALL/PUT balance.
2. Qual é o mecanismo físico real?
   - Quando basis_z60 > Q80 (Perp acima do Spot no fechamento de t-1):
     * O que acontece com o SPOT na vela t (close_spot_t - open_spot_t)?
     * O que acontece com o GAP de abertura do Spot (open_spot_t - close_spot_{t-1})?
     * O que acontece com o PERP na vela t (close_perp_t - open_perp_t)? (O Perp reverte ou continua?)
     * Qual é a taxa de empates (dojis) nos sinais do basis vs no resto?
3. Teste de falsificação F1 (feature deslocada 1 barra p/ futuro), F2 (shuffle intra-dia) e F3 (atraso 1 vela).
4. Teste de latência de execução real (usando klines 1s se disponíveis em dev? NOTA: BTCUSDT_1s_2d.parquet é de 2026-11-15..17, que está DENTRO do holdout! NAO USAMOS 1s aqui para não vazar o holdout! Usamos apenas Spot+Perp 1m na janela Dev!).
5. E se testarmos o mesmo sinal com atraso de 1 barra (F3) ou usando apenas basis de t-2?
6. O sinal sobrevive se compararmos com `close_t > close_{t-1}` (sem bid-ask bounce do open_t)?
7. O sinal é tradável após custos (Spot/Perp fee taker 10 bps RT, ou binário payout 80% breakeven 55,6%)?
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from audit_phase2_baseline import (
    DATA_PARQUET, TRAIN_DAYS, PURGE_BARS, HOLDOUT_DAYS,
    compute_frozen_labels, evaluate_predictions, falsification_shuffle_intraday, wilson_ci
)
from audit_phase3_hypotheses import PERP_PARQUET, causal_rolling_threshold_mask


def main():
    df_spot = compute_frozen_labels(pd.read_parquet(DATA_PARQUET))
    df_perp = pd.read_parquet(PERP_PARQUET).sort_values("open_time").reset_index(drop=True)

    df = df_spot.merge(
        df_perp[["open_time", "open", "high", "low", "close", "volume", "taker_buy_base"]],
        on="open_time", how="inner", suffixes=("", "_perp")
    ).sort_values("open_time").reset_index(drop=True)

    df["date"] = df["open_time"].dt.date
    unique_dates = sorted(df["date"].unique())
    holdout_dates = unique_dates[-HOLDOUT_DAYS:]
    dev_dates = unique_dates[:-HOLDOUT_DAYS]
    wf_test_dates = dev_dates[TRAIN_DAYS + 1:]

    oos_mask = df["date"].isin(wf_test_dates).values
    n = len(df)
    dates_arr = df["date"].values

    # Basis no fechamento da barra i: (perp_close - spot_close) / spot_close
    basis = (df["close_perp"].values - df["close"].values) / df["close"].values
    basis_s = pd.Series(basis)
    basis_mean60 = basis_s.rolling(60).mean().values
    basis_std60 = basis_s.rolling(60).std().replace(0, np.nan).values
    basis_z = (basis - basis_mean60) / basis_std60

    # Causal shift(+1): observado no fechamento de t-1 para prever vela t
    basis_z_prev = np.full(n, np.nan); basis_z_prev[1:] = basis_z[:-1]
    basis_diff_prev = np.full(n, np.nan); basis_diff_prev[1:] = (basis - basis_mean60)[:-1]

    mask_q80 = causal_rolling_threshold_mask(basis_z_prev, dates_arr, wf_test_dates, q=0.80)
    pred = np.where(mask_q80 & (basis_z_prev > 0), 1, np.where(mask_q80 & (basis_z_prev < 0), 0, -1))

    y = df["label"].values
    w = df["body_abs"].values
    r_spot = df["candle_ret"].values
    r_perp = (df["close_perp"].values - df["open_perp"].values) / df["open_perp"].values
    y_perp = np.where(df["close_perp"].values > df["open_perp"].values, 1,
                      np.where(df["close_perp"].values < df["open_perp"].values, 0, -1))
    # Gap de abertura Spot: (open_t - close_{t-1}) / close_{t-1}
    gap_spot = np.full(n, np.nan)
    gap_spot[1:] = (df["open"].values[1:] - df["close"].values[:-1]) / df["close"].values[:-1]
    # Retorno close-to-close Spot: (close_t - close_{t-1}) / close_{t-1}
    c2c_spot = np.full(n, np.nan)
    c2c_spot[1:] = (df["close"].values[1:] - df["close"].values[:-1]) / df["close"].values[:-1]

    print("=" * 125)
    print("AUDITORIA ADVERSARIA: TENTATIVA DE QUEBRAR H4e_basis_z60_mom_Q80 (HOLDOUT 7d FECHADO)")
    print("=" * 125)
    print(f"Janela Dev OOS: {wf_test_dates[0]} -> {wf_test_dates[-1]} (N={int(oos_mask.sum())})")

    sig_oos = oos_mask & (pred >= 0)
    dir_sign = np.where(pred[sig_oos] == 1, 1.0, -1.0)
    print(f"\n1. MECANISMO FISICO NOS {int(sig_oos.sum())} SINAIS OOS:")
    print(f"   Distribuicao sinais   : CALL={(pred[sig_oos]==1).mean()*100:.2f}% | PUT={(pred[sig_oos]==0).mean()*100:.2f}%")
    print(f"   Desvio medio do basis : {np.mean(dir_sign * basis_diff_prev[sig_oos]) * 1e4:+.3f} bps (perp - media60 no close t-1)")
    print(f"   Gap abertura Spot     : {np.mean(dir_sign * gap_spot[sig_oos]) * 1e4:+.3f} bps (open_t - close_{{t-1}})")
    print(f"   Corpo Spot (label)    : {np.mean(dir_sign * r_spot[sig_oos]) * 1e4:+.3f} bps (close_t - open_t)")
    print(f"   Close-to-Close Spot   : {np.mean(dir_sign * c2c_spot[sig_oos]) * 1e4:+.3f} bps (close_t - close_{{t-1}})")
    print(f"   Corpo PERP na vela t  : {np.mean(dir_sign * r_perp[sig_oos]) * 1e4:+.3f} bps (close_perp_t - open_perp_t)")

    # Acurácia no PERP na mesma vela t (testa se perp reverte ou continua)
    m_spot = evaluate_predictions(y[oos_mask], pred[oos_mask], w[oos_mask], r_spot[oos_mask])
    m_perp = evaluate_predictions(y_perp[oos_mask], pred[oos_mask], np.abs(df["close_perp"].values - df["open_perp"].values)[oos_mask], r_perp[oos_mask])
    print(f"\n   No SPOT (alvo congelado): Acc={m_spot['acc']*100:.2f}% [{m_spot['wilson_lo']*100:.2f}%,{m_spot['wilson_hi']*100:.2f}%] | ExTie={m_spot['acc_ex_tie']*100:.2f}% | WAcc={m_spot['acc_weighted']*100:.2f}% | Empates={m_spot['tie_rate']*100:.2f}%")
    print(f"   No PERP (mesmo sinal)   : Acc={m_perp['acc']*100:.2f}% [{m_perp['wilson_lo']*100:.2f}%,{m_perp['wilson_hi']*100:.2f}%] | ExTie={m_perp['acc_ex_tie']*100:.2f}% | WAcc={m_perp['acc_weighted']*100:.2f}% | Empates={m_perp['tie_rate']*100:.2f}%")

    # Close-to-close accuracy (sem depender de open_t)
    y_c2c = np.where(c2c_spot > 0, 1, np.where(c2c_spot < 0, 0, -1))
    m_c2c = evaluate_predictions(y_c2c[oos_mask], pred[oos_mask], np.abs(c2c_spot)[oos_mask], c2c_spot[oos_mask])
    print(f"   No SPOT close-to-close  : Acc={m_c2c['acc']*100:.2f}% [{m_c2c['wilson_lo']*100:.2f}%,{m_c2c['wilson_hi']*100:.2f}%] | WAcc={m_c2c['acc_weighted']*100:.2f}%")

    # 2. Quebra semanal completa
    print("\n2. QUEBRA SEMANAL (TODAS AS 7 SEMANAS OOS DE DESENVOLVIMENTO):")
    oos_dates_sorted = sorted(np.unique(dates_arr[oos_mask]))
    weeks = [oos_dates_sorted[i:i + 7] for i in range(0, len(oos_dates_sorted), 7)]
    prev_lbl = np.full(n, -1, dtype=int); prev_lbl[1:] = y[:-1]
    for wi, wdays in enumerate(weeks, start=1):
        wm = np.isin(dates_arr, wdays)
        ms = evaluate_predictions(y[wm], pred[wm], w[wm], r_spot[wm])
        mb = evaluate_predictions(y[wm], prev_lbl[wm], w[wm], r_spot[wm])
        print(f"   Sem {wi} ({wdays[0]}..{wdays[-1]}): N={ms['n_sig']:>4} ({ms['coverage']*100:4.1f}%) | "
              f"Acc={ms['acc']*100:5.2f}% [{ms['wilson_lo']*100:5.2f}%,{ms['wilson_hi']*100:5.2f}%] vs B2={mb['acc']*100:5.2f}% "
              f"(Lift={(ms['acc']-mb['acc'])*100:+5.2f}pp) | WAcc={ms['acc_weighted']*100:5.2f}% vs B2={mb['acc_weighted']*100:5.2f}% | Dir={ms['ret_dir_bps']:+.2f}bp")

    # 3. Falsificação completa + Teste por quantil (monotonicidade do mecanismo!)
    print("\n3. MONOTONICIDADE POR QUANTIL DE |basis_z60| (CALIBRADO NO TREINO 14d):")
    for q in [0.0, 0.50, 0.75, 0.80, 0.90, 0.95]:
        if q == 0.0:
            mq = ~np.isnan(basis_z_prev)
        else:
            mq = causal_rolling_threshold_mask(basis_z_prev, dates_arr, wf_test_dates, q=q)
        pq = np.where(mq & (basis_z_prev > 0), 1, np.where(mq & (basis_z_prev < 0), 0, -1))
        ms = evaluate_predictions(y[oos_mask], pq[oos_mask], w[oos_mask], r_spot[oos_mask])
        mp = evaluate_predictions(y_perp[oos_mask], pq[oos_mask], np.abs(df["close_perp"].values - df["open_perp"].values)[oos_mask], r_perp[oos_mask])
        print(f"   Q{int(q*100):02d}: N={ms['n_sig']:>5} ({ms['coverage']*100:5.1f}%) | "
              f"Spot Acc={ms['acc']*100:5.2f}% [{ms['wilson_lo']*100:5.2f}%,{ms['wilson_hi']*100:5.2f}%] WAcc={ms['acc_weighted']*100:5.2f}% Dir={ms['ret_dir_bps']:+.2f}bp | "
              f"Perp Acc={mp['acc']*100:5.2f}% WAcc={mp['acc_weighted']*100:5.2f}% Dir={mp['ret_dir_bps']:+.2f}bp")

    # 4. Falsificação F1 (shift futuro), F2 (shuffle intra-dia), F3 (delay 1 e delay 2)
    print("\n4. FALSIFICACAO COMPLETA DE H4e_basis_z60_mom_Q80:")
    p_fut = np.full(n, -1, dtype=int); p_fut[:-1] = pred[1:]
    p_d1 = np.full(n, -1, dtype=int); p_d1[1:] = pred[:-1]
    p_d2 = np.full(n, -1, dtype=int); p_d2[2:] = pred[:-2]
    mf1 = evaluate_predictions(y[oos_mask], p_fut[oos_mask], w[oos_mask], r_spot[oos_mask])
    f2m, f2s = falsification_shuffle_intraday(y[oos_mask], pred[oos_mask], dates_arr[oos_mask], n_sims=200)
    md1 = evaluate_predictions(y[oos_mask], p_d1[oos_mask], w[oos_mask], r_spot[oos_mask])
    md2 = evaluate_predictions(y[oos_mask], p_d2[oos_mask], w[oos_mask], r_spot[oos_mask])
    print(f"   F1 (feature 1 barra p/ futuro, barra t): Acc={mf1['acc']*100:.2f}% WAcc={mf1['acc_weighted']*100:.2f}% Dir={mf1['ret_dir_bps']:+.2f}bp")
    print(f"   F2 (shuffle label intra-dia, 200 sims) : Acc={f2m*100:.2f}% +/- {f2s*100:.2f}%")
    print(f"   F3 (sinal atrasado +1 vela)            : Acc={md1['acc']*100:.2f}% WAcc={md1['acc_weighted']*100:.2f}% Dir={md1['ret_dir_bps']:+.2f}bp")
    print(f"   F3b (sinal atrasado +2 velas)          : Acc={md2['acc']*100:.2f}% WAcc={md2['acc_weighted']*100:.2f}% Dir={md2['ret_dir_bps']:+.2f}bp")

    # 5. Checagem de sensibilidade ao bid-ask / tamanho mínimo do corpo (velas grandes vs pequenas)
    print("\n5. PERFORMANCE POR TAMANHO DO CORPO DA VELA SPOT (ACERTA GRANDES OU SO PEQUENAS?):")
    abs_ret_bps = np.abs(r_spot[sig_oos]) * 1e4
    y_s = y[sig_oos]
    p_s = pred[sig_oos]
    for lo, hi, lbl in [(0.0, 0.5, "<0.5bp"), (0.5, 2.0, "0.5-2bp"), (2.0, 5.0, "2-5bp"), (5.0, 15.0, "5-15bp"), (15.0, 1e9, ">15bp")]:
        bm = (abs_ret_bps >= lo) & (abs_ret_bps < hi)
        if bm.sum() > 0:
            acc_b = (y_s[bm] == p_s[bm]).mean()
            print(f"   Corpo {lbl:<8}: N={int(bm.sum()):>5} | Acc={acc_b*100:5.2f}%")


if __name__ == "__main__":
    main()
