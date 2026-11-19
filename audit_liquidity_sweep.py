"""
audit_liquidity_sweep.py
========================
Teste rigoroso e 100% CAUSAL de Captura de Liquidez (Liquidity Sweep / Stop Hunt / SFP)
sobre 110 DIAS COMPLETOS de Binance Spot BTCUSDT (2026-07-30 a 2026-11-17):
  - Parte 1: Previsao da COR da proxima vela (M1, M5, M15) — Dev OOS (60d) + Holdout (30d),
    comparando varias definicoes pre-especificadas de Sweep (L=15/30/60, pavio >= 40%/50%,
    pico de volume >= 1.5x, fechamento a favor da reversao, delta agressor).
    Testa TAMBEM o sinal invertido (rompimento apos sweep falho) para evitar vies de confirmacao.
  - Parte 2: Simulacao realista de TRADE com Stop no extremo do pavio da captura e Alvo 1.5R / 2.0R
    em M5 e M15 (resolucao intrabar M1 conservadora: em ambiguidade, Stop primeiro!),
    descontando taxas reais Binance (Taker 0.05%/lado = 10bps RT e Maker 0.02%/lado = 4bps RT).
"""

import sys
from pathlib import Path
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from audit_phase2_baseline import wilson_ci, evaluate_predictions
from audit_m5_m15_comparison import load_110d_m1, resample_canonical


def detect_liquidity_sweeps(df: pd.DataFrame, lookback: int = 20, min_wick: float = 0.45,
                            min_vol_ratio: float = 0.0, require_reversal_close: bool = False,
                            require_delta_flip: bool = False) -> np.ndarray:
    """
    Detecta Captura de Liquidez (SFP) na vela fechada i = t - 1 -> sinal para a vela t.
    100% CAUSAL:
      - swing_low  = min(low[i - lookback : i])   (estritamente barras ANTERIORES a i)
      - swing_high = max(high[i - lookback : i])
      - Bullish Sweep (CALL=1): low[i] < swing_low E close[i] > swing_low E lower_wick[i] >= min_wick
      - Bearish Sweep (PUT=0) : high[i] > swing_high E close[i] < swing_high E upper_wick[i] >= min_wick
    """
    o = df["open"].astype(float).values
    h = df["high"].astype(float).values
    l = df["low"].astype(float).values
    c = df["close"].astype(float).values
    v = df["volume"].astype(float).values
    tbb = df["taker_buy_base"].astype(float).values
    n = len(df)

    # Swing highs/lows estritamente anteriores a barra i
    s_low = pd.Series(l).shift(1).rolling(lookback).min().values
    s_high = pd.Series(h).shift(1).rolling(lookback).max().values
    vol_ma = pd.Series(v).shift(1).rolling(20).mean().values
    vol_ratio = np.where(vol_ma > 0, v / vol_ma, 0.0)

    rng = np.maximum(1e-9, h - l)
    lo_wick = (np.minimum(o, c) - l) / rng
    hi_wick = (h - np.maximum(o, c)) / rng
    delta = np.where(v > 0, (2.0 * tbb - v) / v, 0.0)

    bull = (l < s_low) & (c > s_low) & (lo_wick >= min_wick) & (vol_ratio >= min_vol_ratio)
    bear = (h > s_high) & (c < s_high) & (hi_wick >= min_wick) & (vol_ratio >= min_vol_ratio)

    if require_reversal_close:
        bull &= (c > o)
        bear &= (c < o)
    if require_delta_flip:
        bull &= (delta > 0.0)
        bear &= (delta < 0.0)

    # Sinal observado no fechamento i -> aplicado a vela t = i + 1
    sig_at_i = np.where(bull & ~bear, 1, np.where(bear & ~bull, 0, -1)).astype(int)
    pred_for_t = np.full(n, -1, dtype=int)
    pred_for_t[1:] = sig_at_i[:-1]
    return pred_for_t


def eval_next_candle_tf(tf_min: int, spot_m1: pd.DataFrame):
    df = spot_m1.copy() if tf_min == 1 else resample_canonical(spot_m1, tf_min)
    if tf_min == 1:
        df["label"] = np.where(df["close"] > df["open"], 1, np.where(df["close"] < df["open"], 0, -1)).astype(int)
        df["body_abs"] = (df["close"] - df["open"]).abs()
        df["candle_ret"] = (df["close"] - df["open"]) / df["open"]
        df["date"] = df["open_time"].dt.date

    unique_dates = sorted(df["date"].unique())
    ho_dates = unique_dates[-30:]
    dev_dates = unique_dates[21:-30]
    dev_mask = df["date"].isin(dev_dates).values
    ho_mask = df["date"].isin(ho_dates).values

    y = df["label"].values
    w = df["body_abs"].values
    r = df["candle_ret"].values

    # Melhor baseline (B2 ou B3)
    prev = np.full(len(df), -1, dtype=int); prev[1:] = y[:-1]
    contra = np.where(prev == 1, 0, np.where(prev == 0, 1, -1))
    bl_dev = max(
        evaluate_predictions(y[dev_mask], prev[dev_mask], w[dev_mask], r[dev_mask])["acc"],
        evaluate_predictions(y[dev_mask], contra[dev_mask], w[dev_mask], r[dev_mask])["acc"],
    )
    bl_ho = max(
        evaluate_predictions(y[ho_mask], prev[ho_mask], w[ho_mask], r[ho_mask])["acc"],
        evaluate_predictions(y[ho_mask], contra[ho_mask], w[ho_mask], r[ho_mask])["acc"],
    )

    print("\n" + "=" * 140)
    print(f"PARTE 1 — CAPTURA DE LIQUIDEZ (SWEEP / SFP) PARA PROXIMA VELA EM M{tf_min} (Dev 60d BL={bl_dev*100:.2f}% | HO 30d BL={bl_ho*100:.2f}%)")
    print("=" * 140)

    configs = [
        ("Sweep_L15_wick40",            dict(lookback=15, min_wick=0.40)),
        ("Sweep_L20_wick50",            dict(lookback=20, min_wick=0.50)),
        ("Sweep_L30_wick50",            dict(lookback=30, min_wick=0.50)),
        ("Sweep_L20_wick50_Vol1.5x",    dict(lookback=20, min_wick=0.50, min_vol_ratio=1.5)),
        ("Sweep_L20_wick45_CorFavor",   dict(lookback=20, min_wick=0.45, require_reversal_close=True)),
        ("Sweep_L20_wick45_Vol1.5+Cor", dict(lookback=20, min_wick=0.45, min_vol_ratio=1.5, require_reversal_close=True)),
        ("Sweep_L20_wick45_DeltaFlip",  dict(lookback=20, min_wick=0.45, require_delta_flip=True)),
        ("Sweep_L40_wick50_Vol2.0x",    dict(lookback=40, min_wick=0.50, min_vol_ratio=2.0)),
    ]

    for nm, kw in configs:
        pv = detect_liquidity_sweeps(df, **kw)
        md = evaluate_predictions(y[dev_mask], pv[dev_mask], w[dev_mask], r[dev_mask])
        mh = evaluate_predictions(y[ho_mask], pv[ho_mask], w[ho_mask], r[ho_mask])
        ld = (md["acc"] - bl_dev) * 100
        lh = (mh["acc"] - bl_ho) * 100
        surv = (ld >= 3.0 and md["wilson_lo"] > bl_dev and md["n_sig"] >= 100 and
                lh >= 3.0 and mh["wilson_lo"] > bl_ho and mh["n_sig"] >= 60)
        dec = "SOBREVIVE" if surv else ("INCONC(N<60)" if mh["n_sig"] < 60 else "MORRE")
        print(
            f"{nm:<28} | DEV: N={md['n_sig']:>4} ({md['coverage']*100:4.1f}%) Acc={md['acc']*100:5.2f}% [{md['wilson_lo']*100:5.2f},{md['wilson_hi']*100:5.2f}] ({ld:+5.2f}pp) WAcc={md['acc_weighted']*100:5.2f}% Dir={md['ret_dir_bps']:+5.2f}bp"
            f" || HO30d: N={mh['n_sig']:>4} Acc={mh['acc']*100:5.2f}% ({lh:+5.2f}pp) WAcc={mh['acc_weighted']*100:5.2f}% Dir={mh['ret_dir_bps']:+5.2f}bp | {dec}"
        )


def simulate_rr_trades(tf_min: int, spot_m1: pd.DataFrame, lookback: int = 20, min_wick: float = 0.50,
                       min_vol_ratio: float = 1.5, rr_target: float = 2.0, max_hold_bars: int = 20):
    """
    Backtest realista de SCALP com Stop no extremo do pavio de captura e Alvo = rr_target * R:
      - Entrada no open da vela t (fechamento da vela de captura t-1).
      - Stop = low_{t-1} (Long) ou high_{t-1} (Short), com filtro minimo de risco >= 5 bps (evita stops de 1 tick).
      - Alvo = entry +/- rr_target * risk.
      - Resolucao intrabar usando M1 real (conservador: se Stop e Alvo tocam no mesmo minuto M1, Stop primeiro!).
      - Custos: Taker Binance Futures 0.05%/lado (10 bps RT) e Maker 0.02%/lado (4 bps RT).
    """
    df = resample_canonical(spot_m1, tf_min)
    pv = detect_liquidity_sweeps(df, lookback=lookback, min_wick=min_wick, min_vol_ratio=min_vol_ratio)

    unique_dates = sorted(df["date"].unique())
    ho_dates = set(unique_dates[-30:])
    dev_dates = set(unique_dates[21:-30])

    # Indexa M1 por open_time em int64 (ns UTC) para evitar mismatch Timestamp vs datetime64
    m1_ns = spot_m1["open_time"].astype("int64").values
    m1_h = spot_m1["high"].values.astype(float)
    m1_l = spot_m1["low"].values.astype(float)
    m1_c = spot_m1["close"].values.astype(float)
    ts_to_m1_idx = {int(ts): idx for idx, ts in enumerate(m1_ns)}
    df_ns = df["open_time"].astype("int64").values

    def run_split(split_dates: set, split_name: str):
        r_gross = []
        r_net_taker = []
        r_net_maker = []
        ambig = 0
        busy_until_m1 = -1

        for t in range(1, len(df)):
            if df["date"].iloc[t] not in split_dates or pv[t] < 0:
                continue
            m1_start = ts_to_m1_idx.get(int(df_ns[t]), -1)
            if m1_start < 0 or m1_start <= busy_until_m1:
                continue  # uma posicao por vez (sem sobreposicao)

            side = pv[t]  # 1=Long, 0=Short
            entry = float(df["open"].iloc[t])
            stop = float(df["low"].iloc[t - 1]) if side == 1 else float(df["high"].iloc[t - 1])
            risk_abs = abs(entry - stop)
            risk_pct = risk_abs / entry
            if risk_pct < 0.0005:  # minimo 5 bps de stop para nao explodir alavancagem/taxas
                continue
            target = entry + rr_target * risk_abs if side == 1 else entry - rr_target * risk_abs

            max_m1 = min(len(m1_ns), m1_start + max_hold_bars * tf_min)
            exit_px = m1_c[max_m1 - 1]
            exit_m1 = max_m1 - 1

            for k in range(m1_start, max_m1):
                if side == 1:
                    hit_sl = m1_l[k] <= stop
                    hit_tp = m1_h[k] >= target
                else:
                    hit_sl = m1_h[k] >= stop
                    hit_tp = m1_l[k] <= target

                if hit_sl and hit_tp:
                    ambig += 1
                    exit_px = stop  # conservador: stop primeiro
                    exit_m1 = k
                    break
                elif hit_sl:
                    exit_px = stop
                    exit_m1 = k
                    break
                elif hit_tp:
                    exit_px = target
                    exit_m1 = k
                    break

            busy_until_m1 = exit_m1
            pnl_pct = ((exit_px - entry) / entry) if side == 1 else ((entry - exit_px) / entry)
            r_g = pnl_pct / risk_pct
            r_nt = (pnl_pct - 0.0010) / risk_pct  # 10 bps RT taker
            r_nm = (pnl_pct - 0.0004) / risk_pct  # 4 bps RT maker
            r_gross.append(r_g)
            r_net_taker.append(r_nt)
            r_net_maker.append(r_nm)

        if not r_gross:
            print(f"  [{split_name}] N=0 trades")
            return
        rg = np.array(r_gross)
        rnt = np.array(r_net_taker)
        rnm = np.array(r_net_maker)
        wr = (rg > 0).mean() * 100
        pf_t = rnt[rnt > 0].sum() / max(1e-9, -rnt[rnt < 0].sum())
        pf_m = rnm[rnm > 0].sum() / max(1e-9, -rnm[rnm < 0].sum())
        print(
            f"  [{split_name:<6}] N={len(rg):>3} trades (ambig={ambig}) | WR={wr:5.1f}% | "
            f"ExpBruta={rg.mean():+.3f}R | LiqMaker(4bp)={rnm.mean():+.3f}R (PF={pf_m:.2f}, Tot={rnm.sum():+.1f}R) | "
            f"LiqTaker(10bp)={rnt.mean():+.3f}R (PF={pf_t:.2f}, Tot={rnt.sum():+.1f}R)"
        )

    print(f"\n[M{tf_min} | Sweep L={lookback} wick>={int(min_wick*100)}% vol>={min_vol_ratio}x | Alvo={rr_target:.1f}R | Stop=pavio]")
    run_split(dev_dates, "DEV60d")
    run_split(ho_dates, "HO30d")


def audit_m1_sweep_deep(spot_m1: pd.DataFrame, perp_m1: pd.DataFrame):
    """Auditoria profunda + falsificacao F2/F3 + confluencia Sweep + Basis em M1 (110 dias)."""
    print("\n" + "=" * 140)
    print("PARTE 3 — AUDITORIA ADVERSARIA DO UNICO SWEEP SOBREVIVENTE (M1) + CONFLUENCIA COM BASIS")
    print("=" * 140)
    df = spot_m1.merge(perp_m1[["open_time", "close"]], on="open_time", how="inner", suffixes=("", "_perp")).sort_values("open_time").reset_index(drop=True)
    df["label"] = np.where(df["close"] > df["open"], 1, np.where(df["close"] < df["open"], 0, -1)).astype(int)
    df["body_abs"] = (df["close"] - df["open"]).abs()
    df["candle_ret"] = (df["close"] - df["open"]) / df["open"]
    df["date"] = df["open_time"].dt.date

    unique_dates = sorted(df["date"].unique())
    ho_dates = unique_dates[-30:]
    dev_dates = unique_dates[21:-30]
    dev_mask = df["date"].isin(dev_dates).values
    ho_mask = df["date"].isin(ho_dates).values

    y = df["label"].values
    w = df["body_abs"].values
    r = df["candle_ret"].values

    p_sw = detect_liquidity_sweeps(df, lookback=20, min_wick=0.50)
    # Basis z60 causal
    c = df["close"].astype(float)
    basis = (df["close_perp"].astype(float) - c) / c
    bz60 = ((basis - basis.rolling(60).mean()) / basis.rolling(60).std().replace(0, np.nan)).shift(1).values
    bz_q80 = pd.Series(np.abs(bz60)).rolling(1440 * 14, min_periods=1000).quantile(0.80).shift(1).values
    p_bz = np.where((np.abs(bz60) >= bz_q80) & (bz60 > 0), 1, np.where((np.abs(bz60) >= bz_q80) & (bz60 < 0), 0, -1)).astype(int)

    # Confluencia 1: Sweep M1 + Basis concordando no sinal (qualquer magnitude de basis vs Q80)
    p_sw_bz_sign = np.where((p_sw >= 0) & (((p_sw == 1) & (bz60 > 0)) | ((p_sw == 0) & (bz60 < 0))), p_sw, -1)
    p_sw_bz_q80 = np.where((p_sw >= 0) & (p_sw == p_bz), p_sw, -1)

    for nm, pv in [
        ("M1_Sweep_L20_wick50 (solo)", p_sw),
        ("M1_Basis_z60_Q80 (solo)", p_bz),
        ("M1_Sweep + Basis_sinal", p_sw_bz_sign),
        ("M1_Sweep + Basis_Q80", p_sw_bz_q80),
    ]:
        md = evaluate_predictions(y[dev_mask], pv[dev_mask], w[dev_mask], r[dev_mask])
        mh = evaluate_predictions(y[ho_mask], pv[ho_mask], w[ho_mask], r[ho_mask])
        # Falsificacao F3 (atraso 1 vela) no Holdout
        p_d1 = np.full(len(pv), -1, dtype=int); p_d1[1:] = pv[:-1]
        md1 = evaluate_predictions(y[ho_mask], p_d1[ho_mask], w[ho_mask], r[ho_mask])
        print(
            f"{nm:<28} | DEV60d: N={md['n_sig']:>5} Acc={md['acc']*100:5.2f}% [{md['wilson_lo']*100:5.2f},{md['wilson_hi']*100:5.2f}] WAcc={md['acc_weighted']*100:5.2f}% Dir={md['ret_dir_bps']:+5.2f}bp"
            f" || HO30d: N={mh['n_sig']:>4} Acc={mh['acc']*100:5.2f}% [{mh['wilson_lo']*100:5.2f},{mh['wilson_hi']*100:5.2f}] WAcc={mh['acc_weighted']*100:5.2f}% Dir={mh['ret_dir_bps']:+5.2f}bp | F3={md1['acc']*100:5.2f}%"
        )


def main():
    spot_m1, perp_m1 = load_110d_m1()
    print(f"Carregado BTCUSDT Spot M1 (110 dias): {len(spot_m1)} linhas")

    for tf in [1, 5, 15]:
        eval_next_candle_tf(tf, spot_m1)

    print("\n" + "=" * 140)
    print("PARTE 2 — BACKTEST REALISTA DE SCALP COM STOP NO PAVIO + ALVO (R:R) E CUSTOS REAIS BINANCE (INTRABAR M1)")
    print("=" * 140)
    for tf in [5, 15]:
        for rr in [1.5, 2.0]:
            simulate_rr_trades(tf, spot_m1, lookback=20, min_wick=0.50, min_vol_ratio=1.5, rr_target=rr)
            simulate_rr_trades(tf, spot_m1, lookback=20, min_wick=0.45, min_vol_ratio=0.0, rr_target=rr)

    audit_m1_sweep_deep(spot_m1, perp_m1)


if __name__ == "__main__":
    main()
