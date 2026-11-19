"""
audit_m5_m15_comparison.py
==========================
Auditoria quantitativa rigorosa comparando previsibilidade de velas M5 e M15
sobre 110 DIAS COMPLETOS de Binance Spot + Perp BTCUSDT (2026-07-30 a 2026-11-17,
~158.400 velas M1 -> ~31.680 velas M5 e ~10.560 velas M15).

Protocolo (prompat.txt + quant_scalp_research_lab):
  - Resample canonico a partir de M1: open=first, high=max, low=min, close=last,
    volume=sum, taker_buy_base=sum (exigindo barra 100% completa).
  - Label congelado: 1 se close > open, 0 se close < open, -1 se empate (conta como erro).
  - Split temporal rigido:
      * Dev OOS (walk-forward): 2026-08-20 a 2026-10-18 (~60 dias; warmup 20d + purga)
      * Holdout Final:          2026-10-19 a 2026-11-17 (30 dias separados!)
  - Testa Produto A (+0s, sem ver nada da vela prevista):
      * 4 baselines ingenuos (B1 majoritaria, B2 vela anterior, B3 contrario, B4 HTF fechada)
      * Delta agressor Spot (geral e Q80)
      * Basis Perp-Spot z60 (Q80) — testa se o edge de 1-2s do M1 sobrevive em M5/M15
      * Expansao do MACD, EMA 20/50 trend, CCI(20) reversao, Sequencia 3 velas
      * IA Local (HistGradientBoostingClassifier walk-forward sem shuffle, 100% e 20% cobertura)
  - Testa Produto B separado (primeiros 1m de M5 e primeiros 1m/3m de M15):
      reporta TANTO Cor(0->T) QUANTO Resto(wait->T) no preco real de entrada!
  - Reporta corpo medio (bps), retorno direcional (bps), Wilson 95%, WAcc, semanas positivas e F2/F3.
"""

import math
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from audit_phase2_baseline import DATA_PARQUET, wilson_ci, evaluate_predictions

SPOT_45D = REPO_ROOT / "audit_data" / "BTCUSDT_spot_1m_virgin45d.parquet"
PERP_45D = REPO_ROOT / "audit_data" / "BTCUSDT_perp_1m_virgin45d.parquet"
PERP_65D = REPO_ROOT / "audit_data" / "BTCUSDT_perp_1m_65d.parquet"


def load_110d_m1() -> tuple[pd.DataFrame, pd.DataFrame]:
    s1 = pd.read_parquet(SPOT_45D)
    s2 = pd.read_parquet(DATA_PARQUET)
    spot = pd.concat([s1, s2], ignore_index=True).drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)

    p1 = pd.read_parquet(PERP_45D)
    p2 = pd.read_parquet(PERP_65D)
    perp = pd.concat([p1, p2], ignore_index=True).drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return spot, perp


def resample_canonical(df_m1: pd.DataFrame, minutes: int) -> pd.DataFrame:
    freq = f"{minutes}min"
    tmp = df_m1[["open_time", "open", "high", "low", "close", "volume", "taker_buy_base"]].copy()
    tmp["bucket"] = tmp["open_time"].dt.floor(freq)
    agg = tmp.groupby("bucket").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        taker_buy_base=("taker_buy_base", "sum"),
        cnt=("open", "count"),
    ).reset_index().rename(columns={"bucket": "open_time"})
    # Apenas barras 100% completas
    agg = agg[agg["cnt"] == minutes].sort_values("open_time").reset_index(drop=True)
    agg["label"] = np.where(agg["close"] > agg["open"], 1, np.where(agg["close"] < agg["open"], 0, -1)).astype(int)
    agg["body_abs"] = (agg["close"] - agg["open"]).abs()
    agg["candle_ret"] = (agg["close"] - agg["open"]) / agg["open"]
    agg["date"] = agg["open_time"].dt.date
    return agg


def build_htf_closed_signal(df_tf: pd.DataFrame, htf_minutes: int) -> np.ndarray:
    freq = f"{htf_minutes}min"
    tmp = df_tf[["open_time", "open", "close"]].copy()
    tmp["hb"] = tmp["open_time"].dt.floor(freq)
    bars = tmp.groupby("hb").agg(o=("open", "first"), c=("close", "last"), cnt=("open", "count")).reset_index()
    bars["sig"] = np.where(bars["c"] > bars["o"], 1, np.where(bars["c"] < bars["o"], 0, -1))
    smap = dict(zip(bars["hb"], bars["sig"]))
    prev_b = tmp["hb"] - pd.Timedelta(minutes=htf_minutes)
    return prev_b.map(smap).fillna(-1).astype(int).values


def causal_quantile_mask(arr: np.ndarray, dates: np.ndarray, eval_dates: list, train_days: int, purge_bars: int, q: float) -> np.ndarray:
    n = len(arr)
    mask = np.zeros(n, dtype=bool)
    bars_per_day = int(round(n / max(1, len(np.unique(dates)))))
    for td in eval_dates:
        te_idx = np.where(dates == td)[0]
        if len(te_idx) == 0:
            continue
        first_te = int(te_idx[0])
        tr_end = first_te - purge_bars
        tr_start = max(0, tr_end - train_days * bars_per_day)
        if tr_end - tr_start < 100:
            continue
        vals = np.abs(arr[tr_start:tr_end])
        vals = vals[~np.isnan(vals)]
        if len(vals) < 50:
            continue
        th = float(np.quantile(vals, q))
        mask[te_idx] = np.abs(arr[te_idx]) >= th
    return mask


def eval_tf(tf_minutes: int, htf_minutes: int, spot_m1: pd.DataFrame, perp_m1: pd.DataFrame):
    print("\n" + "=" * 140)
    print(f"AUDITORIA TIMEFRAME M{tf_minutes} (110 DIAS REAIS: 2026-07-30 -> 2026-11-17)")
    print("=" * 140)

    df_s = resample_canonical(spot_m1, tf_minutes)
    df_p = resample_canonical(perp_m1, tf_minutes)
    df = df_s.merge(
        df_p[["open_time", "open", "close", "volume", "taker_buy_base"]],
        on="open_time", how="inner", suffixes=("", "_perp")
    ).sort_values("open_time").reset_index(drop=True)

    n = len(df)
    dates = df["date"].values
    unique_dates = sorted(df["date"].unique())

    # Split rigido: primeiros 20d warmup, proximos ~60d Dev OOS, ultimos 30d Holdout!
    ho_dates = unique_dates[-30:]
    dev_dates = unique_dates[21:-30]
    all_eval_dates = dev_dates + ho_dates

    dev_mask = df["date"].isin(dev_dates).values
    ho_mask = df["date"].isin(ho_dates).values

    y = df["label"].values
    w = df["body_abs"].values
    r = df["candle_ret"].values
    avg_body_bps = float(np.abs(r).mean() * 1e4)

    print(f"Total barras M{tf_minutes} completas : {n} | Corpo medio |close-open| = {avg_body_bps:.2f} bps")
    print(f"Distribuicao total       : Verde={(y==1).mean()*100:.2f}% | Vermelha={(y==0).mean()*100:.2f}% | Empate={(y==-1).mean()*100:.2f}%")
    print(f"Dev OOS ({len(dev_dates)}d): {dev_dates[0]} -> {dev_dates[-1]} (N={int(dev_mask.sum())}) | "
          f"Holdout ({len(ho_dates)}d): {ho_dates[0]} -> {ho_dates[-1]} (N={int(ho_mask.sum())})")

    # Features estritamente causais (shift +1)
    c = df["close"].astype(float)
    o = df["open"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    v = df["volume"].astype(float)
    tbb = df["taker_buy_base"].astype(float)
    cp = df["close_perp"].astype(float)

    delta_spot = ((2.0 * tbb - v) / v.replace(0, np.nan)).fillna(0.0).shift(1).values
    basis = (cp - c) / c
    basis_z60 = ((basis - basis.rolling(60).mean()) / basis.rolling(60).std().replace(0, np.nan)).shift(1).values

    ema9 = c.ewm(span=9, adjust=False).mean()
    ema21 = c.ewm(span=21, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd_l = ema12 - ema26
    macd_sig = macd_l.ewm(span=9, adjust=False).mean()
    macd_hist = (macd_l - macd_sig).shift(1).values
    macd_exp = (macd_l - macd_sig).diff(1).shift(1).values

    tp = (h + l + c) / 3.0
    tp_ma = tp.rolling(20).mean()
    tp_md = tp.rolling(20).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True).replace(0, np.nan)
    cci20 = ((tp - tp_ma) / (0.015 * tp_md)).shift(1).values

    ret1 = c.pct_change(1).shift(1).values
    ret3 = c.pct_change(3).shift(1).values
    vol_ratio = (v / v.rolling(20).mean().replace(0, np.nan)).shift(1).values
    vol_real = c.pct_change(1).rolling(20).std().shift(1).values
    ema9_21 = ((ema9 - ema21) / c).shift(1).values
    p_ema50 = ((c - ema50) / c).shift(1).values
    body_pct = ((c - o).abs() / o).shift(1).values

    # Baselines
    purge_bars = max(60, int(200 / tf_minutes))
    bars_per_day = int(1440 / tf_minutes)

    b1 = np.full(n, -1, dtype=int)
    for td in all_eval_dates:
        te_idx = np.where(dates == td)[0]
        if len(te_idx) == 0:
            continue
        first_te = int(te_idx[0])
        tr_end = first_te - purge_bars
        tr_start = max(0, tr_end - 20 * bars_per_day)
        tr_y = y[tr_start:tr_end]
        b1[te_idx] = 1 if (tr_y == 1).sum() >= (tr_y == 0).sum() else 0

    b2 = np.full(n, -1, dtype=int); b2[1:] = y[:-1]
    b3 = np.where(b2 == 1, 0, np.where(b2 == 0, 1, -1))
    b4 = build_htf_closed_signal(df, htf_minutes)

    # Hipoteses individuais (Produto A — em +0s!)
    p_delta = np.where(delta_spot > 0, 1, np.where(delta_spot < 0, 0, -1))
    m_dq80 = causal_quantile_mask(delta_spot, dates, all_eval_dates, 20, purge_bars, 0.80)
    p_delta_q80 = np.where(m_dq80, p_delta, -1)

    m_bz80 = causal_quantile_mask(basis_z60, dates, all_eval_dates, 20, purge_bars, 0.80)
    p_basis_q80 = np.where(m_bz80 & (basis_z60 > 0), 1, np.where(m_bz80 & (basis_z60 < 0), 0, -1))
    p_basis_all = np.where(basis_z60 > 0, 1, np.where(basis_z60 < 0, 0, -1))

    p_macd_exp = np.where((macd_hist > 0) & (macd_exp > 0), 1, np.where((macd_hist < 0) & (macd_exp < 0), 0, -1))
    p_ema_trend = np.where((ema9_21 > 0) & (p_ema50 > 0), 1, np.where((ema9_21 < 0) & (p_ema50 < 0), 0, -1))
    p_cci_rev = np.where(cci20 < -100, 1, np.where(cci20 > 100, 0, -1))

    p_seq3_rev = np.full(n, -1, dtype=int)
    p_seq3_cont = np.full(n, -1, dtype=int)
    for t in range(3, n):
        win = y[t - 3:t]
        if (win == 1).all():
            p_seq3_rev[t] = 0; p_seq3_cont[t] = 1
        elif (win == 0).all():
            p_seq3_rev[t] = 1; p_seq3_cont[t] = 0

    # IA Local Walk-Forward (HistGradientBoostingClassifier, retrain semanal p/ velocidade e estabilidade)
    X_mat = np.column_stack([
        delta_spot, basis_z60, macd_hist, macd_exp, cci20, ret1, ret3,
        vol_ratio, vol_real, ema9_21, p_ema50, body_pct
    ])
    valid_x = ~np.isnan(X_mat).any(axis=1)
    p_ml_all = np.full(n, -1, dtype=int)
    p_ml_c20 = np.full(n, -1, dtype=int)

    # Retreina a cada 3 dias (janela treino 20d + validacao 3d p/ limiar Q80)
    for i_d in range(0, len(all_eval_dates), 3):
        blk_dates = all_eval_dates[i_d:i_d + 3]
        te_idx = np.where(np.isin(dates, blk_dates))[0]
        if len(te_idx) == 0:
            continue
        first_te = int(te_idx[0])
        val_end = first_te - purge_bars
        val_start = val_end - 3 * bars_per_day
        tr_end = val_start - purge_bars
        tr_start = max(0, tr_end - 20 * bars_per_day)
        if tr_end - tr_start < 300 or val_end - val_start < 50:
            continue

        tr_sl = np.arange(tr_start, tr_end)
        val_sl = np.arange(val_start, val_end)
        tr_ok = tr_sl[valid_x[tr_sl] & (y[tr_sl] >= 0)]
        val_ok = val_sl[valid_x[val_sl]]
        te_ok = te_idx[valid_x[te_idx]]
        if len(tr_ok) < 250 or len(val_ok) < 40 or len(te_ok) == 0:
            continue

        clf = HistGradientBoostingClassifier(
            max_iter=60, max_depth=3, learning_rate=0.04, min_samples_leaf=40, random_state=42
        )
        clf.fit(X_mat[tr_ok], y[tr_ok])
        p_val = clf.predict_proba(X_mat[val_ok])[:, 1]
        th20 = float(np.quantile(np.abs(p_val - 0.5), 0.80))

        p_te = clf.predict_proba(X_mat[te_ok])[:, 1]
        pred = (p_te >= 0.5).astype(int)
        p_ml_all[te_ok] = pred
        p_ml_c20[te_ok] = np.where(np.abs(p_te - 0.5) >= th20, pred, -1)

    items = [
        ("B1_majoritaria_treino", b1),
        ("B2_vela_anterior", b2),
        ("B3_contrario_vela_ant", b3),
        (f"B4_ultima_M{htf_minutes}_fechada", b4),
        ("H1_delta_spot_100%", p_delta),
        ("H1_delta_spot_Q80", p_delta_q80),
        ("H2_basis_z60_100%", p_basis_all),
        ("H2_basis_z60_Q80", p_basis_q80),
        ("H3_EMA_trend_9_21_50", p_ema_trend),
        ("H4_MACD_expansao", p_macd_exp),
        ("H5_CCI20_reversao", p_cci_rev),
        ("H6_seq3_reversao", p_seq3_rev),
        ("H6_seq3_continuacao", p_seq3_cont),
        ("IA_HGBC_100%_cobert", p_ml_all),
        ("IA_HGBC_20%_cobert", p_ml_c20),
    ]

    # Avaliacao DEV OOS e HOLDOUT 30d lado a lado
    bl_dev_best = max(evaluate_predictions(y[dev_mask], pv[dev_mask], w[dev_mask], r[dev_mask])["acc"] for _, pv in items[:4])
    bl_ho_best = max(evaluate_predictions(y[ho_mask], pv[ho_mask], w[ho_mask], r[ho_mask])["acc"] for _, pv in items[:4])

    print(f"\n--- PRODUTO A (+0s, SEM VER A VELA) — DEV OOS (60d, Melhor BL={bl_dev_best*100:.2f}%) | HOLDOUT 30d (Melhor BL={bl_ho_best*100:.2f}%) ---")
    for nm, pv in items:
        md = evaluate_predictions(y[dev_mask], pv[dev_mask], w[dev_mask], r[dev_mask])
        mh = evaluate_predictions(y[ho_mask], pv[ho_mask], w[ho_mask], r[ho_mask])
        ld = (md["acc"] - bl_dev_best) * 100
        lh = (mh["acc"] - bl_ho_best) * 100
        surv_dev = (ld >= 3.0) and (md["wilson_lo"] > bl_dev_best) and (md["n_sig"] >= 300)
        surv_ho = (lh >= 3.0) and (mh["wilson_lo"] > bl_ho_best) and (mh["n_sig"] >= 200)
        verdict = "SOBREVIVE" if (surv_dev and surv_ho) else ("SO_DEV" if surv_dev else "MORRE")
        print(
            f"{nm:<24} | DEV: N={md['n_sig']:>5} Acc={md['acc']*100:5.2f}% [{md['wilson_lo']*100:5.2f},{md['wilson_hi']*100:5.2f}] ({ld:+5.2f}pp) WAcc={md['acc_weighted']*100:5.2f}% Dir={md['ret_dir_bps']:+5.2f}bp"
            f" || HO30d: N={mh['n_sig']:>4} Acc={mh['acc']*100:5.2f}% ({lh:+5.2f}pp) WAcc={mh['acc_weighted']*100:5.2f}% Dir={mh['ret_dir_bps']:+5.2f}bp | {verdict}"
        )

    # --- PRODUTO B EM M5 / M15 (esperar primeiros k minutos da vela usando M1!) ---
    # M5: esperar 1m (20% da vela); M15: esperar 1m (6.7%) e 3m (20%)
    wait_mins_list = [1] if tf_minutes == 5 else [1, 3]
    print(f"\n--- PRODUTO B EM M{tf_minutes} (ESPERAR PRIMEIROS k MINUTOS, 110 DIAS COMPLETOS) ---")
    s_tmp = spot_m1[["open_time", "open", "close"]].copy()
    s_tmp["bucket"] = s_tmp["open_time"].dt.floor(f"{tf_minutes}min")
    s_tmp["offset_m"] = ((s_tmp["open_time"] - s_tmp["bucket"]).dt.total_seconds() // 60).astype(int)

    for wm in wait_mins_list:
        # Pega o close da barra M1 de indice (wm - 1) dentro de cada vela M5/M15
        g_k = s_tmp[s_tmp["offset_m"] == (wm - 1)][["bucket", "close"]].rename(columns={"bucket": "open_time", "close": "px_wait"})
        df_b = df.merge(g_k, on="open_time", how="inner")
        ret_w = (df_b["px_wait"].values - df_b["open"].values) / df_b["open"].values
        ret_rem = (df_b["close"].values - df_b["px_wait"].values) / df_b["px_wait"].values
        lbl_rem = np.where(ret_rem > 0, 1, np.where(ret_rem < 0, 0, -1))
        f_w = np.where(ret_w > 0, 1, np.where(ret_w < 0, 0, -1))

        # SNR fisico: |ret_w| / (sigma_20 * sqrt((T-wm)/T))
        sig20 = df_b["close"].pct_change(1).rolling(20).std().clip(lower=1e-4).shift(1).values
        snr_w = np.abs(ret_w) / (sig20 * math.sqrt((tf_minutes - wm) / tf_minutes))
        ho_b = df_b["date"].isin(ho_dates).values
        dev_b = df_b["date"].isin(dev_dates).values
        th70 = float(np.nanquantile(snr_w[dev_b], 0.70))
        p_snr70 = np.where(snr_w >= th70, f_w, -1)

        for tag, pv in [(f"BL_Follow_{wm}m", f_w), (f"SNR_{wm}m_Q70 (top 30%)", p_snr70)]:
            m_cor = evaluate_predictions(df_b["label"].values[ho_b], pv[ho_b], df_b["body_abs"].values[ho_b], df_b["candle_ret"].values[ho_b])
            m_rem = evaluate_predictions(lbl_rem[ho_b], pv[ho_b], np.abs(ret_rem[ho_b]), ret_rem[ho_b])
            print(
                f"  [HO 30d] {tag:<24} | N={m_cor['n_sig']:>4} ({m_cor['coverage']*100:5.1f}%) | "
                f"Cor(0->{tf_minutes}m)={m_cor['acc']*100:5.2f}% [{m_cor['wilson_lo']*100:5.2f}%,{m_cor['wilson_hi']*100:5.2f}%] WAcc={m_cor['acc_weighted']*100:5.2f}% | "
                f"Resto({wm}m->{tf_minutes}m)={m_rem['acc']*100:5.2f}% ({m_rem['ret_dir_bps']:+5.2f}bp)"
            )


def main():
    spot_m1, perp_m1 = load_110d_m1()
    print(f"Carregado M1 110 dias: Spot={len(spot_m1)} linhas | Perp={len(perp_m1)} linhas")
    eval_tf(5, 15, spot_m1, perp_m1)
    eval_tf(15, 60, spot_m1, perp_m1)


if __name__ == "__main__":
    main()
