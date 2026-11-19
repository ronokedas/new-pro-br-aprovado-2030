"""
audit_product_b_ml_and_indicators.py
====================================
Teste rigoroso e 100% causal (sem vazamento, walk-forward temporal) no PRODUTO B (+5s)
sobre BTCUSDT_1s_2d.parquet (2.881 velas M1 completas de 2 dias) alinhado a 1m Spot + Perp:
  1. Baseline justo: BL_Follow_5s (sistema atual no Passo 2)
  2. Filtros individuais solicitados pelo usuario:
       - Expansao do MACD (macd_hist alinhado e expandindo)
       - Volume (vol_5s alto e vol_1m > 1.3x media)
       - Delta agressor nos primeiros 5s (delta_5s alinhado)
       - Basis Perp alinhado (basis_z60)
       - Deslocamento normalizado (|ret_5s| / sigma_15m — derivacao fisica de difusao!)
  3. IA Local (HistGradientBoostingClassifier em walk-forward temporal estrito, sem shuffle,
     treinada com TODAS as features causais acima) avaliada em 100%, 50%, 30% e 20% cobertura
     com limiar calibrado ESTRITAMENTE fora do teste.
  4. Reporta TAMBEM a acuracia sobre o movimento RESTANTE (close_60s vs price_5s) para
     transparencia total sobre o preco de entrada em +5s!
"""
import math
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from audit_phase2_baseline import DATA_PARQUET, wilson_ci, evaluate_predictions
from audit_phase3_hypotheses import PERP_PARQUET, KLINES_1S_PARQUET


def build_product_b_dataset(wait_sec: int = 5) -> pd.DataFrame:
    df_spot = pd.read_parquet(DATA_PARQUET).sort_values("open_time").reset_index(drop=True)
    df_perp = pd.read_parquet(PERP_PARQUET).sort_values("open_time").reset_index(drop=True)
    df_1s = pd.read_parquet(KLINES_1S_PARQUET).sort_values("open_time").reset_index(drop=True)

    # Features M1 estritamente fechadas ate t-1 (shift +1)
    m = df_spot.merge(df_perp[["open_time", "close"]], on="open_time", how="inner", suffixes=("", "_perp"))
    c = m["close"].astype(float)
    v = m["volume"].astype(float)

    # MACD (12, 26, 9) e expansao do histograma
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd_l = ema12 - ema26
    macd_sig = macd_l.ewm(span=9, adjust=False).mean()
    macd_hist = macd_l - macd_sig
    macd_exp = macd_hist.diff(1)  # >0 expandindo p/ cima, <0 expandindo p/ baixo

    # EMA 9/21/50
    ema9 = c.ewm(span=9, adjust=False).mean()
    ema21 = c.ewm(span=21, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()

    # Volatilidade M1 realizada (15m) e ratio de volume
    ret1 = c.pct_change(1)
    sig15 = ret1.rolling(15).std().clip(lower=5e-5)
    vol_ratio = v / v.rolling(20).mean().replace(0, np.nan)

    # Basis z60 Perp - Spot
    basis = (m["close_perp"].astype(float) - c) / c
    basis_z60 = (basis - basis.rolling(60).mean()) / basis.rolling(60).std().replace(0, np.nan)

    # Alinha causalmente: features observadas no fechamento de t-1 -> aplicadas a vela t
    feat_m1 = pd.DataFrame({
        "open_time": m["open_time"],
        "macd_hist_prev": macd_hist.shift(1),
        "macd_exp_prev": macd_exp.shift(1),
        "ema9_21_prev": ((ema9 - ema21) / c).shift(1),
        "price_vs_ema50_prev": ((c - ema50) / c).shift(1),
        "sig15_prev": sig15.shift(1),
        "vol_ratio_prev": vol_ratio.shift(1),
        "basis_z60_prev": basis_z60.shift(1),
        "prev_ret1": ret1.shift(1),
    })

    # Agrega klines 1s em velas M1 completas (60s) + features dos primeiros wait_sec segundos
    df_1s["minute"] = df_1s["open_time"].dt.floor("1min")
    df_1s["sec"] = df_1s["open_time"].dt.second
    df_1s["delta"] = 2.0 * df_1s["taker_buy_base"] - df_1s["volume"]

    rows = []
    for m_ts, grp in df_1s.groupby("minute"):
        if len(grp) != 60:
            continue
        o0 = float(grp["open"].iloc[0])
        c_final = float(grp["close"].iloc[-1])
        lbl = 1 if c_final > o0 else (0 if c_final < o0 else -1)

        g5 = grp[grp["sec"] < wait_sec]
        c5 = float(g5["close"].iloc[-1])
        h5 = float(g5["high"].max())
        l5 = float(g5["low"].min())
        v5 = float(g5["volume"].sum())
        d5 = float(g5["delta"].sum())

        ret5 = (c5 - o0) / o0 if o0 > 0 else 0.0
        # Label e retorno do movimento RESTANTE (de +5s ate +60s — o que voce ganha se entra no mercado em +5s!)
        ret_rem = (c_final - c5) / c5 if c5 > 0 else 0.0
        lbl_rem = 1 if c_final > c5 else (0 if c_final < c5 else -1)

        rows.append({
            "open_time": m_ts,
            "open": o0,
            "price_5s": c5,
            "close": c_final,
            "label": lbl,
            "label_rem": lbl_rem,
            "body_abs": abs(c_final - o0),
            "candle_ret": (c_final - o0) / o0 if o0 > 0 else 0.0,
            "ret_rem": ret_rem,
            "ret_5s": ret5,
            "abs_ret_5s": abs(ret5),
            "range_5s": (h5 - l5) / o0 if o0 > 0 else 0.0,
            "vol_5s": v5,
            "delta_5s_norm": d5 / v5 if v5 > 0 else 0.0,
            "follow_5s": 1 if c5 > o0 else (0 if c5 < o0 else -1),
        })

    df_b = pd.DataFrame(rows).merge(feat_m1, on="open_time", how="inner").sort_values("open_time").reset_index(drop=True)
    # SNR fisico dos primeiros 5s: deslocamento em 5s dividido pela volatilidade restante sqrt(55/60)*sigma_15
    df_b["snr_5s"] = df_b["ret_5s"] / (df_b["sig15_prev"] * math.sqrt(55.0 / 60.0))
    df_b["abs_snr_5s"] = df_b["snr_5s"].abs()
    df_b["vol_5s_ratio"] = df_b["vol_5s"] / df_b["vol_5s"].rolling(20).mean().replace(0, np.nan)
    return df_b.dropna().reset_index(drop=True)


def report_line(name: str, y: np.ndarray, y_rem: np.ndarray, p: np.ndarray, w: np.ndarray, r: np.ndarray, r_rem: np.ndarray, bl_acc: float):
    m = evaluate_predictions(y, p, w, r)
    m_rem = evaluate_predictions(y_rem, p, np.abs(r_rem), r_rem)
    lift = (m["acc"] - bl_acc) * 100
    print(
        f"{name:<32} | N={m['n_sig']:>4} ({m['coverage']*100:5.1f}%) | "
        f"Cor(0->60s)={m['acc']*100:5.2f}% [{m['wilson_lo']*100:5.2f}%,{m['wilson_hi']*100:5.2f}%] ({lift:+5.2f}pp) | "
        f"WAcc={m['acc_weighted']*100:5.2f}% | "
        f"Resto(5s->60s)={m_rem['acc']*100:5.2f}% ({m_rem['ret_dir_bps']:+5.2f}bp)"
    )


def main():
    print("=" * 145)
    print("AUDITORIA PRODUTO B (+5s): VOLUME + EXPANSAO MACD + BASIS + SNR_5s + IA LOCAL (WALK-FORWARD ESTRITO)")
    print("=" * 145)
    df = build_product_b_dataset(wait_sec=5)
    print(f"Dataset alinhado 1s + 1m Spot + Perp: N={len(df)} velas | {df['open_time'].iloc[0]} -> {df['open_time'].iloc[-1]}")
    print("AVISO DE AMOSTRA: 2 dias de dados 1s (~2.860 velas) = amostra curta (1 regime); resultados exigem validacao mais longa.")

    # Walk-forward temporal em blocos de 6 horas (360 velas): treina no passado (>= 18h = 1080 velas), testa proximas 6h!
    n = len(df)
    min_tr = 1080
    step = 360
    oos_mask = np.zeros(n, dtype=bool)
    oos_mask[min_tr:] = True

    feature_cols = [
        "ret_5s", "snr_5s", "range_5s", "vol_5s_ratio", "delta_5s_norm",
        "macd_hist_prev", "macd_exp_prev", "ema9_21_prev", "price_vs_ema50_prev",
        "vol_ratio_prev", "basis_z60_prev", "prev_ret1"
    ]

    # Sinais de filtros calibrados estritamente no treino passado (walk-forward)
    p_snr_q50 = np.full(n, -1, dtype=int)
    p_snr_q70 = np.full(n, -1, dtype=int)
    p_snr_q80 = np.full(n, -1, dtype=int)
    p_vol5_q50 = np.full(n, -1, dtype=int)

    # IA Local (HistGradientBoostingClassifier walk-forward)
    probs_ml = np.full(n, np.nan)
    p_ml_all = np.full(n, -1, dtype=int)
    p_ml_c50 = np.full(n, -1, dtype=int)
    p_ml_c30 = np.full(n, -1, dtype=int)
    p_ml_c20 = np.full(n, -1, dtype=int)

    y_all = df["label"].values
    f5 = df["follow_5s"].values
    abs_snr = df["abs_snr_5s"].values
    v5r = df["vol_5s_ratio"].values
    X_all = df[feature_cols].values

    for start in range(min_tr, n, step):
        end = min(n, start + step)
        tr_idx = np.arange(0, start - 20)  # purga 20 velas
        te_idx = np.arange(start, end)

        # Quantis causais no treino
        q50_snr = float(np.quantile(abs_snr[tr_idx], 0.50))
        q70_snr = float(np.quantile(abs_snr[tr_idx], 0.70))
        q80_snr = float(np.quantile(abs_snr[tr_idx], 0.80))
        q50_v5 = float(np.quantile(v5r[tr_idx], 0.50))

        p_snr_q50[te_idx] = np.where(abs_snr[te_idx] >= q50_snr, f5[te_idx], -1)
        p_snr_q70[te_idx] = np.where(abs_snr[te_idx] >= q70_snr, f5[te_idx], -1)
        p_snr_q80[te_idx] = np.where(abs_snr[te_idx] >= q80_snr, f5[te_idx], -1)
        p_vol5_q50[te_idx] = np.where(v5r[te_idx] >= q50_v5, f5[te_idx], -1)

        # Split interno treino / validacao (ultimas 240 velas do treino = validacao p/ limiar de confianca!)
        val_cut = len(tr_idx) - 240
        tr_sub = tr_idx[:val_cut]
        val_sub = tr_idx[val_cut:]
        tr_clean = tr_sub[y_all[tr_sub] >= 0]

        clf = HistGradientBoostingClassifier(
            max_iter=60, max_depth=3, learning_rate=0.05, min_samples_leaf=40, random_state=42
        )
        clf.fit(X_all[tr_clean], y_all[tr_clean])

        p_val = clf.predict_proba(X_all[val_sub])[:, 1]
        d_val = np.abs(p_val - 0.5)
        th50 = float(np.quantile(d_val, 0.50))
        th70 = float(np.quantile(d_val, 0.70))
        th80 = float(np.quantile(d_val, 0.80))

        p_te = clf.predict_proba(X_all[te_idx])[:, 1]
        probs_ml[te_idx] = p_te
        d_te = np.abs(p_te - 0.5)
        pred_dir = (p_te >= 0.5).astype(int)

        p_ml_all[te_idx] = pred_dir
        p_ml_c50[te_idx] = np.where(d_te >= th50, pred_dir, -1)
        p_ml_c30[te_idx] = np.where(d_te >= th70, pred_dir, -1)
        p_ml_c20[te_idx] = np.where(d_te >= th80, pred_dir, -1)

    # Filtros diretos sem parametro livre sobre Follow_5s
    mh = df["macd_hist_prev"].values
    me = df["macd_exp_prev"].values
    d5 = df["delta_5s_norm"].values
    vr = df["vol_ratio_prev"].values
    bz = df["basis_z60_prev"].values

    p_macd_exp = np.where(
        ((f5 == 1) & (mh > 0) & (me > 0)) | ((f5 == 0) & (mh < 0) & (me < 0)), f5, -1
    )
    p_vol1m = np.where(vr >= 1.2, f5, -1)
    p_delta5 = np.where(((f5 == 1) & (d5 > 0.2)) | ((f5 == 0) & (d5 < -0.2)), f5, -1)
    p_basis = np.where(((f5 == 1) & (bz > 0.5)) | ((f5 == 0) & (bz < -0.5)), f5, -1)
    p_combo_macd_vol_d5 = np.where(
        (p_macd_exp == f5) & (p_delta5 == f5) & (p_vol5_q50 == f5), f5, -1
    )

    y_o = y_all[oos_mask]
    yr_o = df["label_rem"].values[oos_mask]
    w_o = df["body_abs"].values[oos_mask]
    r_o = df["candle_ret"].values[oos_mask]
    rr_o = df["ret_rem"].values[oos_mask]

    m_bl = evaluate_predictions(y_o, f5[oos_mask], w_o, r_o)
    bl_acc = m_bl["acc"]

    print(f"\nJANELA OOS WALK-FORWARD (N={int(oos_mask.sum())} velas):")
    print("Coluna 'Cor(0->60s)' = vela finaliza da cor prevista (close_60s vs open_0s).")
    print("Coluna 'Resto(5s->60s)' = o que voce realmente ganha/perde se ENTRAR no preco do segundo +5s!")
    print("=" * 145)

    for nm, pv in [
        ("BL_Follow_5s (atual Passo 2)", f5),
        ("F1_Follow5s + Expansao_MACD", p_macd_exp),
        ("F2_Follow5s + Vol_1m_alto", p_vol1m),
        ("F3_Follow5s + Vol_5s_alto(Q50)", p_vol5_q50),
        ("F4_Follow5s + Delta_5s_forte", p_delta5),
        ("F5_Follow5s + Basis_Perp", p_basis),
        ("F6_Follow5s + MACD+Vol5+Delta5", p_combo_macd_vol_d5),
        ("FISICA_SNR5s_Q50 (top 50%)", p_snr_q50),
        ("FISICA_SNR5s_Q70 (top 30%)", p_snr_q70),
        ("FISICA_SNR5s_Q80 (top 20%)", p_snr_q80),
        ("IA_LOCAL_HGBC_100%_cobert", p_ml_all),
        ("IA_LOCAL_HGBC_50%_cobert", p_ml_c50),
        ("IA_LOCAL_HGBC_30%_cobert", p_ml_c30),
        ("IA_LOCAL_HGBC_20%_cobert", p_ml_c20),
    ]:
        report_line(nm, y_o, yr_o, pv[oos_mask], w_o, r_o, rr_o, bl_acc)

    # Teste de falsificacao F2 (shuffle) na IA e no SNR5s_Q70
    rng = np.random.default_rng(42)
    shuf_accs = []
    p_check = p_snr_q70[oos_mask]
    m_sig = p_check >= 0
    for _ in range(200):
        shuf_accs.append((rng.permutation(y_o[m_sig]) == p_check[m_sig]).mean())
    print("-" * 145)
    print(f"Falsificacao F2 (shuffle) em FISICA_SNR5s_Q70: {np.mean(shuf_accs)*100:.2f}% +/- {np.std(shuf_accs)*100:.2f}%")

    # Brier / Calibracao da IA Local no OOS
    valid_c = oos_mask & ~np.isnan(probs_ml) & (y_all >= 0)
    print(f"Calibracao IA Local OOS (N={int(valid_c.sum())}, Brier={brier_score_loss(y_all[valid_c], probs_ml[valid_c]):.4f}):")
    p_c = probs_ml[valid_c]
    y_c = y_all[valid_c]
    for lo, hi in [(0.0, 0.30), (0.30, 0.45), (0.45, 0.55), (0.55, 0.70), (0.70, 1.01)]:
        bm = (p_c >= lo) & (p_c < hi)
        if bm.sum() > 0:
            print(f"  Prob IA [{lo:.2f}, {hi:.2f}): N={int(bm.sum()):>4} | Prob_media={p_c[bm].mean():.3f} | Real_Verde={y_c[bm].mean():.3f}")


if __name__ == "__main__":
    main()
