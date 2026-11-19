"""
audit_quant_research_v2.py
==========================
Pesquisa quantitativa de primeiro princípio (microestrutura) na janela de DESENVOLVIMENTO
(2026-09-28 a 2026-11-10, N=63.360 + checagem secundária em 2026-11-11..17).
O NOVO HOLDOUT VIRGEM DE 30 DIAS (2026-08-14 a 2026-09-13) PERMANECE COMPLETAMENTE FECHADO!

Hipóteses testadas (todas 100% causais, quantis calibrados apenas no treino 14d com purga 200):
  Q1 — SNR Físico do Basis: (basis_{t-1} - EMA60(basis)_{t-1}) / sigma_M1_{t-1}
       Mecanismo: probabilidade Browniana P(verde) = Phi(alpha * delta_basis / sigma_M1).
       Normaliza o desvio do basis pela volatilidade REAL do Spot (não pelo std do próprio basis),
       para se adaptar automaticamente entre regimes calmos e voláteis!
  Q2 — Choque Rápido do Basis: (basis_{t-1} - EMA10(basis)_{t-1}) / sigma_M1_{t-1}
       e variação 1m (basis_{t-1} - basis_{t-2}) / sigma_M1_{t-1}.
  Q3 — Dislocação Multi-Venue: BTCUSDT Perp + BTCUSDC Spot contra BTCUSDT Spot.
       Mecanismo: se DOIS venues líquidos desviam na mesma direção contra o Spot BTCUSDT,
       reduz ruído específico de um único livro.
  Q4 — Kyle's Lambda / Fluxo Informado vs Vácuo de Liquidez:
       separa retorno t-1 por impacto |ret| / sqrt(vol) e agressão unilateral.
  Q5 — Fronteiras de Relógio (minute == 0, minute % 5 == 0, minute % 15 == 0):
       verifica se o efeito de microestrutura é mais forte em viradas de barra institucional.
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
    PERP_PARQUET, causal_rolling_threshold_mask, eval_hypothesis_full, print_hyp_result
)

USDC_65D = REPO_ROOT / "audit_data" / "BTCUSDC_spot_1m_65d.parquet"


def build_features_frame(df_spot: pd.DataFrame, df_perp: pd.DataFrame, df_usdc: pd.DataFrame) -> pd.DataFrame:
    df = compute_frozen_labels(df_spot)
    df = df.merge(
        df_perp[["open_time", "open", "close", "volume", "taker_buy_base"]],
        on="open_time", how="inner", suffixes=("", "_perp")
    ).merge(
        df_usdc[["open_time", "open", "close", "volume", "taker_buy_base"]],
        on="open_time", how="inner", suffixes=("", "_usdc")
    ).sort_values("open_time").reset_index(drop=True)
    return df


def compute_signals(df: pd.DataFrame, wf_test_dates: list) -> dict[str, np.ndarray]:
    n = len(df)
    dates_arr = df["date"].values
    c = df["close"].astype(float).values
    o = df["open"].astype(float).values
    h = df["high"].astype(float).values
    l = df["low"].astype(float).values
    v = df["volume"].astype(float).values
    tbb = df["taker_buy_base"].astype(float).values

    cp = df["close_perp"].astype(float).values
    cu = df["close_usdc"].astype(float).values

    # Volatilidade realizada causal do Spot M1 (rolling 15m e 60m dos retornos |close-open|/open e pct_change)
    ret_s = pd.Series(c).pct_change(1)
    sig15 = ret_s.rolling(15).std().replace(0, np.nan).values
    sig60 = ret_s.rolling(60).std().replace(0, np.nan).values
    # Proteção contra sigma minúsculo (piso = 0.5 bp = 5e-5)
    sig15_s = np.maximum(sig15, 5e-5)
    sig60_s = np.maximum(sig60, 5e-5)

    # 1. Basis Perp-Spot
    basis_p = (cp - c) / c
    bp_s = pd.Series(basis_p)
    bp_dev60 = (bp_s - bp_s.ewm(span=60, adjust=False).mean()).values
    bp_dev15 = (bp_s - bp_s.ewm(span=15, adjust=False).mean()).values
    bp_diff1 = bp_s.diff(1).values

    # Z-score clássico (referência da Fase 3)
    bp_z60 = ((bp_s - bp_s.rolling(60).mean()) / bp_s.rolling(60).std().replace(0, np.nan)).values

    # Q1: SNR Físico = desvio do basis / sigma_M1
    snr_p_60_15 = bp_dev60 / sig15_s
    snr_p_60_60 = bp_dev60 / sig60_s
    # Q2: Choque rápido do basis / sigma_M1
    snr_p_15_15 = bp_dev15 / sig15_s
    snr_p_diff1 = bp_diff1 / sig15_s

    # 2. Spread BTCUSDC - BTCUSDT (mesma lógica física: se USDC Spot está acima de USDT Spot vs média recente)
    basis_u = (cu - c) / c
    bu_s = pd.Series(basis_u)
    bu_dev60 = (bu_s - bu_s.ewm(span=60, adjust=False).mean()).values
    bu_dev15 = (bu_s - bu_s.ewm(span=15, adjust=False).mean()).values
    snr_u_60_15 = bu_dev60 / sig15_s
    snr_u_15_15 = bu_dev15 / sig15_s

    # Q3: Multi-venue composto (média padronizada de Perp + BTCUSDC vs BTCUSDT Spot)
    # Ambos têm mesma unidade (desvio relativo / sigma_M1)!
    snr_multi_15 = 0.5 * (snr_p_15_15 + snr_u_15_15)
    snr_multi_60 = 0.5 * (snr_p_60_15 + snr_u_60_15)

    # Shift(+1) estritamente causal: observado no fechamento de t-1 -> prevê vela t
    def lag1(arr):
        out = np.full(n, np.nan)
        out[1:] = arr[:-1]
        return out

    bp_z60_l = lag1(bp_z60)
    snr_p_60_15_l = lag1(snr_p_60_15)
    snr_p_60_60_l = lag1(snr_p_60_60)
    snr_p_15_15_l = lag1(snr_p_15_15)
    snr_p_diff1_l = lag1(snr_p_diff1)
    snr_u_15_15_l = lag1(snr_u_15_15)
    snr_u_60_15_l = lag1(snr_u_60_15)
    snr_multi_15_l = lag1(snr_multi_15)
    snr_multi_60_l = lag1(snr_multi_60)

    def sig_q(arr_l, q):
        m = causal_rolling_threshold_mask(arr_l, dates_arr, wf_test_dates, q=q)
        return np.where(m & (arr_l > 0), 1, np.where(m & (arr_l < 0), 0, -1)).astype(int)

    sigs = {
        "REF_basis_z60_Q80":       sig_q(bp_z60_l, 0.80),
        "Q1_SNR_perp60_sig15_Q80": sig_q(snr_p_60_15_l, 0.80),
        "Q1_SNR_perp60_sig15_Q90": sig_q(snr_p_60_15_l, 0.90),
        "Q1_SNR_perp60_sig15_Q95": sig_q(snr_p_60_15_l, 0.95),
        "Q1_SNR_perp60_sig60_Q80": sig_q(snr_p_60_60_l, 0.80),
        "Q2_SNR_perp15_sig15_Q80": sig_q(snr_p_15_15_l, 0.80),
        "Q2_SNR_perp15_sig15_Q90": sig_q(snr_p_15_15_l, 0.90),
        "Q2_SNR_perpDiff1_Q80":    sig_q(snr_p_diff1_l, 0.80),
        "Q3_SNR_usdc15_sig15_Q80": sig_q(snr_u_15_15_l, 0.80),
        "Q3_SNR_usdc60_sig15_Q80": sig_q(snr_u_60_15_l, 0.80),
        "Q3_MultiVenue_15_Q80":    sig_q(snr_multi_15_l, 0.80),
        "Q3_MultiVenue_15_Q90":    sig_q(snr_multi_15_l, 0.90),
        "Q3_MultiVenue_60_Q80":    sig_q(snr_multi_60_l, 0.80),
        "Q3_MultiVenue_60_Q90":    sig_q(snr_multi_60_l, 0.90),
        "Q3_MultiVenue_60_Q95":    sig_q(snr_multi_60_l, 0.95),
    }

    # Q3b: Concordância estrita Perp E BTCUSDC ambos acima/abaixo (mesmo sinal) + magnitude Q80 no composto
    m_mv80 = causal_rolling_threshold_mask(snr_multi_15_l, dates_arr, wf_test_dates, q=0.80)
    agree_up = (snr_p_15_15_l > 0) & (snr_u_15_15_l > 0)
    agree_dn = (snr_p_15_15_l < 0) & (snr_u_15_15_l < 0)
    sigs["Q3b_MultiAgree_15_Q80"] = np.where(m_mv80 & agree_up, 1, np.where(m_mv80 & agree_dn, 0, -1)).astype(int)

    m_mv90 = causal_rolling_threshold_mask(snr_multi_15_l, dates_arr, wf_test_dates, q=0.90)
    sigs["Q3b_MultiAgree_15_Q90"] = np.where(m_mv90 & agree_up, 1, np.where(m_mv90 & agree_dn, 0, -1)).astype(int)

    # Q4: Kyle's Lambda — Retorno informado (alto volume + agressão unilateral + corpo limpo) vs vácuo ilíquido
    ret1 = (c - o) / o
    delta = np.where(v > 0, (2.0 * tbb - v) / v, 0.0)
    vol_ma20 = pd.Series(v).rolling(20).mean().values
    vol_ratio = np.where(vol_ma20 > 0, v / vol_ma20, 1.0)
    kyle_lambda = np.where(v > 0, np.abs(ret1) / np.sqrt(v), np.nan)
    body_ratio = np.where((h - l) > 0, np.abs(c - o) / (h - l), 0.0)

    ret1_l = lag1(ret1)
    delta_l = lag1(delta)
    vol_ratio_l = lag1(vol_ratio)
    kyle_l = lag1(kyle_lambda)
    body_ratio_l = lag1(body_ratio)

    # Q4a: Fluxo informado (vol_ratio > 1.5, |delta| > 0.4, body_ratio > 0.6, mesmo sinal) -> continuação
    inf_up = (ret1_l > 0) & (delta_l > 0.4) & (vol_ratio_l > 1.5) & (body_ratio_l > 0.6)
    inf_dn = (ret1_l < 0) & (delta_l < -0.4) & (vol_ratio_l > 1.5) & (body_ratio_l > 0.6)
    sigs["Q4a_Kyle_FluxoInformado"] = np.where(inf_up, 1, np.where(inf_dn, 0, -1)).astype(int)

    # Q4b: Vácuo de liquidez (kyle_lambda no Q80 + volume baixo < 0.7x média) -> reversão
    m_kyle80 = causal_rolling_threshold_mask(kyle_l, dates_arr, wf_test_dates, q=0.80)
    vac_rev_up = m_kyle80 & (vol_ratio_l < 0.7) & (ret1_l < 0)
    vac_rev_dn = m_kyle80 & (vol_ratio_l < 0.7) & (ret1_l > 0)
    sigs["Q4b_Kyle_VacuoReversao"] = np.where(vac_rev_up, 1, np.where(vac_rev_dn, 0, -1)).astype(int)

    # Q5: Fronteira de relógio (viradas de 5m e 15m com Q3_MultiVenue_15_Q80)
    mins = df["open_time"].dt.minute.values
    p_mv80 = sigs["Q3_MultiVenue_15_Q80"]
    sigs["Q5_Multi15_Q80_virada5m"] = np.where((mins % 5 == 0), p_mv80, -1).astype(int)
    sigs["Q5_Multi15_Q80_meio5m"] = np.where((mins % 5 != 0), p_mv80, -1).astype(int)

    return sigs


def main():
    print("=" * 135)
    print("PESQUISA QUANTITATIVA V2 — MICROESTRUTURA DE PRIMEIRO PRINCIPIO (HOLDOUT VIRGEM 30d FECHADO!)")
    print("=" * 135)

    df = build_features_frame(
        pd.read_parquet(DATA_PARQUET),
        pd.read_parquet(PERP_PARQUET),
        pd.read_parquet(USDC_65D),
    )
    df["date"] = df["open_time"].dt.date
    unique_dates = sorted(df["date"].unique())
    sec7_dates = unique_dates[-HOLDOUT_DAYS:]
    dev_dates = unique_dates[:-HOLDOUT_DAYS]
    wf_test_dates = dev_dates[TRAIN_DAYS + 1:]

    # Calibramos quantis em todo o conjunto pós-warmup (Dev + Sec7) de forma causal (cada dia só vê 14d anteriores)
    all_eval_dates = wf_test_dates + sec7_dates
    sigs = compute_signals(df, all_eval_dates)

    oos_mask = df["date"].isin(wf_test_dates).values
    sec7_mask = df["date"].isin(sec7_dates).values
    dates_arr = df["date"].values

    y_oos = df["label"].values[oos_mask]
    w_oos = df["body_abs"].values[oos_mask]
    r_oos = df["candle_ret"].values[oos_mask]
    oos_dates_sorted = sorted(np.unique(dates_arr[oos_mask]))
    weeks_dates = [oos_dates_sorted[i:i + 7] for i in range(0, len(oos_dates_sorted), 7)]

    prev_lbl = np.full(len(df), -1, dtype=int); prev_lbl[1:] = df["label"].values[:-1]
    m_b2 = evaluate_predictions(y_oos, prev_lbl[oos_mask], w_oos, r_oos)

    print(f"Alinhamento Spot + Perp + BTCUSDC: N={len(df)} velas")
    print(f"Janela Dev OOS : {wf_test_dates[0]} -> {wf_test_dates[-1]} (N={len(y_oos)}, 7 semanas)")
    print(f"Melhor Baseline: B2_vela_anterior = {m_b2['acc']*100:.2f}% | WAcc={m_b2['acc_weighted']*100:.2f}% | Dir={m_b2['ret_dir_bps']:+.2f}bp")
    print("=" * 135)
    print("TABELA 1 — DEV OOS (44 DIAS, N=63.360, PRODUTO A, EMPATE = ERRO)")
    print("=" * 135)

    results = {}
    for nm, pv in sigs.items():
        res = eval_hypothesis_full(nm, pv, df, oos_mask, m_b2["acc"], m_b2["acc_weighted"], weeks_dates)
        results[nm] = res
        print_hyp_result(res)

    print("\n" + "=" * 135)
    print("TABELA 2 — CHECAGEM NO REGIME VOLATIL DE 7 DIAS (2026-11-11..17, N=9.863, Empates=0.44% — BL B1=50.87%)")
    print("=" * 135)
    y_s7 = df["label"].values[sec7_mask]
    w_s7 = df["body_abs"].values[sec7_mask]
    r_s7 = df["candle_ret"].values[sec7_mask]
    for nm, pv in sigs.items():
        if results[nm]["decision"] == "SOBREVIVE" or nm.startswith("REF_") or "Q80" in nm or "Q90" in nm:
            mm = evaluate_predictions(y_s7, pv[sec7_mask], w_s7, r_s7)
            lift7 = (mm["acc"] - 0.5087) * 100
            print(f"  {nm:<28} | N={mm['n_sig']:>4} ({mm['coverage']*100:5.1f}%) | "
                  f"Acc={mm['acc']*100:5.2f}% [{mm['wilson_lo']*100:5.2f}%,{mm['wilson_hi']*100:5.2f}%] | "
                  f"Lift7d={lift7:+5.2f}pp | WAcc={mm['acc_weighted']*100:5.2f}% | Dir={mm['ret_dir_bps']:+5.2f}bp")


if __name__ == "__main__":
    main()
