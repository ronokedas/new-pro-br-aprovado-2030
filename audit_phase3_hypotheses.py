"""
audit_phase3_hypotheses.py
==========================
Fase 3 do protocolo de auditoria quantitativa (prompat.txt):
Avalia UMA hipótese por ciclo (ablação estrita), mantendo o HOLDOUT FINAL (7 dias:
2026-11-11 a 2026-11-17) COMPLETAMENTE FECHADO.

Ciclos executados (na ordem da Seção 2):
  - Ciclo 1 (Hipótese #2): Fluxo agressor causal (1m em 44d OOS; 5s/15s/60s em 2d amostra curta)
  - Ciclo 2 (Hipótese #4): Divergência Spot vs Perp do delta agressor e Basis (44d OOS)
  - Ciclo 3 (Hipótese #5): Filtros de regime (Vol 15m/60m, EMA 5m/15m fechada, OI 5m fechado)
  - Ciclo 4 (Hipótese #6): Sequência de cores (3-5 velas) e CCI(20) em M1 (44d OOS)
  - Produto B (SEPARADO): open_t + N segundos (N in {3, 5, 10}) vs baseline justo Follow_N (2d amostra curta)
  - Extra (exceção solicitada pelo usuário): Padrão OCO / OCOI causal no gráfico de fechamentos M1
    com atraso explícito de confirmação de pivôs (R barras à direita), zero look-ahead.

Critério obrigatório para SOBREVIVE (todos verdadeiros):
  1. Lift >= +3,00 p.p. sobre o melhor baseline ingênuo na mesma janela
  2. Limite inferior do IC Wilson 95% > acurácia do melhor baseline
  3. Mesmo sinal de lift positivo em >= 3 janelas walk-forward semanais
  4. Aprovação nos testes de falsificação (F1 futuro sobe como sonda; F2 shuffle cai ao nulo; F3 atraso 1 vela)
  5. Para filtros de regime: aumenta WAcc (acurácia ponderada por |close - open|) além do sinal base.
"""

import math
import sys
from pathlib import Path
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from audit_phase2_baseline import (
    DATA_PARQUET,
    TRAIN_DAYS,
    PURGE_BARS,
    HOLDOUT_DAYS,
    wilson_ci,
    compute_frozen_labels,
    build_last_closed_5m_signal,
    evaluate_predictions,
    falsification_shuffle_intraday,
)

PERP_PARQUET = REPO_ROOT / "audit_data" / "BTCUSDT_perp_1m_65d.parquet"
OI_PARQUET = REPO_ROOT / "audit_data" / "BTCUSDT_oi_5m.parquet"
KLINES_1S_PARQUET = Path(r"C:\trading-agents\btc_color_research\data\BTCUSDT_1s_2d.parquet")


def eval_hypothesis_full(
    name: str,
    preds_all: np.ndarray,
    df_raw: pd.DataFrame,
    oos_mask: np.ndarray,
    best_bl_acc: float,
    best_bl_wacc: float,
    weeks_dates: list,
    is_filter: bool = False,
    base_sig_wacc: float | None = None,
    n_shuffle: int = 100,
) -> dict:
    """Avalia uma hipótese no OOS comum, quebra semanal, falsificação F2/F3 e decide SOBREVIVE ou MORRE."""
    y_oos = df_raw["label"].values[oos_mask]
    w_oos = df_raw["body_abs"].values[oos_mask]
    r_oos = df_raw["candle_ret"].values[oos_mask]
    d_oos = df_raw["date"].values[oos_mask]
    p_oos = preds_all[oos_mask]

    m = evaluate_predictions(y_oos, p_oos, w_oos, r_oos)

    # Quebra por semana: contar quantas semanas têm lift positivo vs melhor baseline da mesma semana
    # (usamos best_bl_acc global e também checamos sinal de lift semanal contra B2 semanal)
    prev_lbl = np.full(len(df_raw), -1, dtype=int)
    prev_lbl[1:] = df_raw["label"].values[:-1]
    b2_oos = prev_lbl[oos_mask]

    pos_weeks_acc = 0
    pos_weeks_wacc = 0
    valid_weeks = 0
    week_lifts = []
    for wdays in weeks_dates:
        wmask = np.isin(d_oos, wdays)
        mw = evaluate_predictions(y_oos[wmask], p_oos[wmask], w_oos[wmask], r_oos[wmask])
        mb2 = evaluate_predictions(y_oos[wmask], b2_oos[wmask], w_oos[wmask], r_oos[wmask])
        if mw["n_sig"] >= 20:
            valid_weeks += 1
            lift_w = (mw["acc"] - mb2["acc"]) * 100
            wlift_w = (mw["acc_weighted"] - mb2["acc_weighted"]) * 100
            week_lifts.append(lift_w)
            if lift_w > 0:
                pos_weeks_acc += 1
            if wlift_w > 0:
                pos_weeks_wacc += 1

    # Falsificação F2 (shuffle intra-dia) e F3 (atraso 1 vela)
    f2_mean, f2_std = falsification_shuffle_intraday(y_oos, p_oos, d_oos, n_sims=n_shuffle)
    p_delay1 = np.full(len(preds_all), -1, dtype=int)
    p_delay1[1:] = preds_all[:-1]
    m_f3 = evaluate_predictions(y_oos, p_delay1[oos_mask], w_oos, r_oos)

    # Falsificação F1 (feature/sinal deslocado 1 barra p/ trás = usa vela t p/ prever t)
    p_fut1 = np.full(len(preds_all), -1, dtype=int)
    p_fut1[:-1] = preds_all[1:]
    m_f1 = evaluate_predictions(y_oos, p_fut1[oos_mask], w_oos, r_oos)

    lift_pp = (m["acc"] - best_bl_acc) * 100
    wlift_pp = (m["acc_weighted"] - best_bl_wacc) * 100
    gate_lift = lift_pp >= 3.0
    gate_wilson = m["wilson_lo"] > best_bl_acc
    gate_weeks = pos_weeks_acc >= 3
    gate_sample = m["n_sig"] >= 300
    gate_filter_wacc = True
    if is_filter and base_sig_wacc is not None:
        gate_filter_wacc = m["acc_weighted"] > base_sig_wacc

    survives = gate_lift and gate_wilson and gate_weeks and gate_sample and gate_filter_wacc
    decision = "SOBREVIVE" if survives else "MORRE"

    return {
        "name": name,
        "metrics": m,
        "lift_pp": lift_pp,
        "wlift_pp": wlift_pp,
        "pos_weeks_acc": pos_weeks_acc,
        "pos_weeks_wacc": pos_weeks_wacc,
        "valid_weeks": valid_weeks,
        "f1_acc": m_f1["acc"],
        "f2_mean": f2_mean,
        "f2_std": f2_std,
        "f3_acc": m_f3["acc"],
        "f3_wacc": m_f3["acc_weighted"],
        "decision": decision,
        "gates": {
            "lift>=3pp": gate_lift,
            "wilson>bl": gate_wilson,
            "3_weeks": gate_weeks,
            "N>=300": gate_sample,
            "wacc_filter": gate_filter_wacc,
        },
    }


def print_hyp_result(res: dict):
    m = res["metrics"]
    print(
        f"{res['name']:<28} | N={m['n_sig']:>6} ({m['coverage']*100:5.1f}%) | "
        f"Acc={m['acc']*100:5.2f}% [{m['wilson_lo']*100:5.2f}%,{m['wilson_hi']*100:5.2f}%] | "
        f"Lift={res['lift_pp']:+6.2f}pp | WAcc={m['acc_weighted']*100:5.2f}% ({res['wlift_pp']:+5.2f}pp) | "
        f"Dir={m['ret_dir_bps']:+5.2f}bp | Sem={res['pos_weeks_acc']}/{res['valid_weeks']} | "
        f"F2={res['f2_mean']*100:5.2f}% F3={res['f3_acc']*100:5.2f}% | {res['decision']}"
    )


def causal_rolling_threshold_mask(values: np.ndarray, dates: np.ndarray, wf_test_dates: list, q: float = 0.75) -> np.ndarray:
    """
    Para cada dia de teste walk-forward, calcula o quantil `q` de |values|
    ESTRITAMENTE na janela de treino (14d anteriores com purga de 200 velas).
    Retorna booleano (True se |values[t]| >= quantil do treino).
    """
    n = len(values)
    mask = np.zeros(n, dtype=bool)
    for td in wf_test_dates:
        te_idx = np.where(dates == td)[0]
        if len(te_idx) == 0:
            continue
        first_te = int(te_idx[0])
        tr_end = first_te - PURGE_BARS
        tr_start = max(0, tr_end - TRAIN_DAYS * 1440)
        if tr_end - tr_start < 1000:
            continue
        tr_vals = np.abs(values[tr_start:tr_end])
        tr_vals = tr_vals[~np.isnan(tr_vals)]
        if len(tr_vals) < 100:
            continue
        th = float(np.quantile(tr_vals, q))
        mask[te_idx] = np.abs(values[te_idx]) >= th
    return mask


def causal_rolling_threshold_above(values: np.ndarray, dates: np.ndarray, wf_test_dates: list, q: float = 0.50) -> np.ndarray:
    """Quantil causal sobre values (não absoluto) calibrado apenas no treino (14d + purga 200)."""
    n = len(values)
    mask = np.zeros(n, dtype=bool)
    for td in wf_test_dates:
        te_idx = np.where(dates == td)[0]
        if len(te_idx) == 0:
            continue
        first_te = int(te_idx[0])
        tr_end = first_te - PURGE_BARS
        tr_start = max(0, tr_end - TRAIN_DAYS * 1440)
        if tr_end - tr_start < 1000:
            continue
        tr_vals = values[tr_start:tr_end]
        tr_vals = tr_vals[~np.isnan(tr_vals)]
        if len(tr_vals) < 100:
            continue
        th = float(np.quantile(tr_vals, q))
        mask[te_idx] = values[te_idx] >= th
    return mask


def build_closed_htf_ema_bias(df: pd.DataFrame, freq: str = "5min", ema_span: int = 20) -> np.ndarray:
    """
    Calcula o estado da EMA lenta da última barra de tempo maior (5m ou 15m)
    ESTRITAMENTE JÁ FECHADA antes de open_t.
    Retorna: 1 se close_htf > ema_htf, 0 se close_htf < ema_htf, -1 se indefinido.
    """
    tmp = df[["open_time", "open", "close"]].copy()
    tmp["bucket"] = tmp["open_time"].dt.floor(freq)
    bars = tmp.groupby("bucket").agg(
        close_htf=("close", "last"),
        cnt=("close", "count"),
    ).reset_index()
    expected_cnt = 5 if freq == "5min" else 15
    bars["ema"] = bars["close_htf"].ewm(span=ema_span, adjust=False).mean()
    bars["bias"] = np.where(
        bars["cnt"] < expected_cnt, -1,
        np.where(bars["close_htf"] > bars["ema"], 1,
                 np.where(bars["close_htf"] < bars["ema"], 0, -1))
    )
    bias_map = dict(zip(bars["bucket"], bars["bias"]))
    delta = pd.Timedelta(minutes=expected_cnt)
    prev_closed = tmp["bucket"] - delta
    return prev_closed.map(bias_map).fillna(-1).astype(int).values


def detect_causal_oco_signals(
    closes: np.ndarray,
    order_r: int = 3,
    lookback: int = 60,
    sym_tol: float = 0.0025,
    hold_bars: int = 1,
) -> np.ndarray:
    """
    Detector 100% CAUSAL de OCO (Ombro-Cabeça-Ombro -> PUT=0) e OCOI (Invertido -> CALL=1)
    sobre o gráfico de linhas (série `close` M1).

    GARANTIA ANTI-VAZAMENTO:
    - Para emitir sinal na vela `t` (no instante open_t), só podemos ler fechamentos até `i = t - 1`.
    - Um pivô de ordem `order_r` no índice `p` compara `closes[p]` contra `order_r` barras à esquerda
      (`p-order_r .. p-1`) e `order_r` barras à direita (`p+1 .. p+order_r`).
    - Logo o pivô `p` SÓ É CONFIRMADO quando a barra `p + order_r` fecha!
    - Atraso explícito de confirmação = `order_r` barras (minutos).
    - Em `i = t - 1`, só são conhecidos pivôs com `p <= i - order_r`.
    - Rompimento da neckline (reta entre V1 e V2 para OCO, ou P1 e P2 para OCOI) é verificado
      no fechamento `i = t - 1` (ou até `hold_bars` barras após o rompimento se hold_bars > 1).
    """
    n = len(closes)
    # 1) Pré-computa pivôs locais (mas cada pivô p só passa a existir em conf_idx = p + order_r!)
    is_ph = np.zeros(n, dtype=bool)
    is_pl = np.zeros(n, dtype=bool)
    for p in range(order_r, n - order_r):
        win = closes[p - order_r:p + order_r + 1]
        val = closes[p]
        # Máximo estrito no centro (evita empates planos)
        if val == win.max() and (win == val).sum() == 1:
            is_ph[p] = True
        if val == win.min() and (win == val).sum() == 1:
            is_pl[p] = True

    # Lista incremental de pivôs CONFIRMADOS até o índice i
    conf_highs = []  # índices p confirmados
    conf_lows = []

    pred_for_t = np.full(n, -1, dtype=int)
    active_sig = -1
    active_rem = 0

    for i in range(n - 1):
        # Novo pivô que acabou de ser confirmado no fechamento de i: p = i - order_r
        p_new = i - order_r
        if p_new >= order_r:
            if is_ph[p_new]:
                conf_highs.append(p_new)
            if is_pl[p_new]:
                conf_lows.append(p_new)

        # Mantém apenas pivôs dentro da janela causal [i - lookback, i - order_r]
        min_idx = i - lookback
        while conf_highs and conf_highs[0] < min_idx:
            conf_highs.pop(0)
        while conf_lows and conf_lows[0] < min_idx:
            conf_lows.pop(0)

        sig_i = -1

        # Verifica OCO (topo -> PUT = 0): últimos 3 topos confirmados LS < H < RS
        if len(conf_highs) >= 3 and len(conf_lows) >= 2:
            ls, h_idx, rs = conf_highs[-3], conf_highs[-2], conf_highs[-1]
            c_ls, c_h, c_rs = closes[ls], closes[h_idx], closes[rs]
            if c_h > c_ls and c_h > c_rs and abs(c_ls - c_rs) / c_h <= sym_tol:
                # V1 entre LS e H; V2 entre H e RS
                v1_cands = [x for x in conf_lows if ls < x < h_idx]
                v2_cands = [x for x in conf_lows if h_idx < x < rs]
                if v1_cands and v2_cands:
                    v1 = min(v1_cands, key=lambda x: closes[x])
                    v2 = min(v2_cands, key=lambda x: closes[x])
                    if v2 > v1:
                        slope = (closes[v2] - closes[v1]) / (v2 - v1)
                        neck_now = closes[v2] + slope * (i - v2)
                        neck_prev = closes[v2] + slope * ((i - 1) - v2)
                        # Rompimento para baixo no fechamento i (e RS recente)
                        if closes[i - 1] >= neck_prev and closes[i] < neck_now:
                            sig_i = 0  # PUT

        # Verifica OCOI (fundo -> CALL = 1): últimos 3 fundos confirmados LS < H < RS
        if sig_i == -1 and len(conf_lows) >= 3 and len(conf_highs) >= 2:
            ls, h_idx, rs = conf_lows[-3], conf_lows[-2], conf_lows[-1]
            c_ls, c_h, c_rs = closes[ls], closes[h_idx], closes[rs]
            if c_h < c_ls and c_h < c_rs and abs(c_ls - c_rs) / max(c_h, 1e-9) <= sym_tol:
                p1_cands = [x for x in conf_highs if ls < x < h_idx]
                p2_cands = [x for x in conf_highs if h_idx < x < rs]
                if p1_cands and p2_cands:
                    p1 = max(p1_cands, key=lambda x: closes[x])
                    p2 = max(p2_cands, key=lambda x: closes[x])
                    if p2 > p1:
                        slope = (closes[p2] - closes[p1]) / (p2 - p1)
                        neck_now = closes[p2] + slope * (i - p2)
                        neck_prev = closes[p2] + slope * ((i - 1) - p2)
                        # Rompimento para cima no fechamento i
                        if closes[i - 1] <= neck_prev and closes[i] > neck_now:
                            sig_i = 1  # CALL

        if sig_i != -1:
            active_sig = sig_i
            active_rem = hold_bars

        if active_rem > 0:
            # Sinal gerado no fechamento i -> aplicado à próxima vela t = i + 1
            pred_for_t[i + 1] = active_sig
            active_rem -= 1

    return pred_for_t


def run_1s_hypotheses_and_product_b():
    """
    Avalia sobre BTCUSDT_1s_2d.parquet (AMOSTRA CURTA = 2 dias, ~2.880 velas):
      - Parte A (Produto A): Fluxo agressor dos últimos 5s, 15s e 60s ANTES de open_t
      - Parte B (Produto B — SEPARADO): open_t + N segundos (N in {3, 5, 10})
        comparando contra o baseline justo Follow_N (que também espera N segundos).
    """
    print("\n" + "=" * 135)
    print("AVALIACAO EM DADOS 1s (AMOSTRA CURTA = 2 DIAS — NAO SUSTENTA AFIRMACAO DE ROBUSTEZ)")
    print("=" * 135)
    if not KLINES_1S_PARQUET.exists():
        print("NAO_MEDIDO: BTCUSDT_1s_2d.parquet nao encontrado.")
        return

    df1s = pd.read_parquet(KLINES_1S_PARQUET).sort_values("open_time").reset_index(drop=True)
    df1s["minute"] = df1s["open_time"].dt.floor("1min")
    df1s["sec"] = df1s["open_time"].dt.second
    df1s["delta"] = 2.0 * df1s["taker_buy_base"] - df1s["volume"]

    # Constrói velas M1 completas (60 segundos exatos) e agrega features sub-minuto
    rows = []
    for m_ts, grp in df1s.groupby("minute"):
        if len(grp) != 60:
            continue
        o = float(grp["open"].iloc[0])
        c = float(grp["close"].iloc[-1])
        lbl = 1 if c > o else (0 if c < o else -1)
        vol_60 = float(grp["volume"].sum())
        d_60 = float(grp["delta"].sum())
        # Últimos 5s e 15s desta vela (serão usados com shift(+1) para prever a PRÓXIMA vela!)
        g_last5 = grp[grp["sec"] >= 55]
        g_last15 = grp[grp["sec"] >= 45]
        d_last5 = float(g_last5["delta"].sum())
        v_last5 = float(g_last5["volume"].sum())
        d_last15 = float(g_last15["delta"].sum())
        v_last15 = float(g_last15["volume"].sum())

        row = {
            "open_time": m_ts,
            "date": m_ts.date(),
            "open": o,
            "close": c,
            "label": lbl,
            "body_abs": abs(c - o),
            "candle_ret": (c - o) / o if o > 0 else 0.0,
            "d60_norm": d_60 / vol_60 if vol_60 > 0 else 0.0,
            "d15_norm": d_last15 / v_last15 if v_last15 > 0 else 0.0,
            "d5_norm": d_last5 / v_last5 if v_last5 > 0 else 0.0,
        }
        # Para Produto B: primeiros N segundos (sec < N) para N in {3, 5, 10}
        for N in [3, 5, 10]:
            g_first = grp[grp["sec"] < N]
            c_n = float(g_first["close"].iloc[-1])
            d_n = float(g_first["delta"].sum())
            v_n = float(g_first["volume"].sum())
            row[f"dir_first_{N}s"] = 1 if c_n > o else (0 if c_n < o else -1)
            row[f"delta_first_{N}s"] = d_n / v_n if v_n > 0 else 0.0
        rows.append(row)

    dfm = pd.DataFrame(rows).sort_values("open_time").reset_index(drop=True)
    y = dfm["label"].values
    w = dfm["body_abs"].values
    r = dfm["candle_ret"].values
    print(f"Velas M1 completas reconstruidas de 1s: N={len(dfm)} | {dfm['open_time'].iloc[0]} -> {dfm['open_time'].iloc[-1]}")
    print(f"Distribuicao 2d: Verde={(y==1).mean()*100:.2f}% | Vermelha={(y==0).mean()*100:.2f}% | Empate={(y==-1).mean()*100:.2f}%")

    # --- PRODUTO A (últimos 5s, 15s, 60s ANTES de open_t) ---
    print("\n--- PRODUTO A (2 dias): Fluxo agressor nos ultimos 5s, 15s, 60s ANTES de open_t ---")
    prev_lbl = np.full(len(dfm), -1, dtype=int)
    prev_lbl[1:] = y[:-1]
    m_b2 = evaluate_predictions(y[1:], prev_lbl[1:], w[1:], r[1:])
    print(f"{'B2_vela_anterior (2d)':<28} | N={m_b2['n_sig']:>5} ({m_b2['coverage']*100:5.1f}%) | "
          f"Acc={m_b2['acc']*100:5.2f}% [{m_b2['wilson_lo']*100:5.2f}%,{m_b2['wilson_hi']*100:5.2f}%] | "
          f"WAcc={m_b2['acc_weighted']*100:5.2f}% | Dir={m_b2['ret_dir_bps']:+5.2f}bp")

    for col, label_name in [("d5_norm", "H2b_delta_ult_5s"), ("d15_norm", "H2b_delta_ult_15s"), ("d60_norm", "H2b_delta_ult_60s")]:
        vals = dfm[col].values
        pred = np.full(len(dfm), -1, dtype=int)
        # Shift(+1): últimos Ns da vela t-1 preveem vela t
        pred[1:] = np.where(vals[:-1] > 0, 1, np.where(vals[:-1] < 0, 0, -1))
        m = evaluate_predictions(y[1:], pred[1:], w[1:], r[1:])
        lift = (m["acc"] - m_b2["acc"]) * 100
        dec = "MORRE (amostra curta + sem lift>=3pp)" if (lift < 3.0 or m["wilson_lo"] <= m_b2["acc"]) else "CANDIDATA (amostra curta!)"
        print(f"{label_name:<28} | N={m['n_sig']:>5} ({m['coverage']*100:5.1f}%) | "
              f"Acc={m['acc']*100:5.2f}% [{m['wilson_lo']*100:5.2f}%,{m['wilson_hi']*100:5.2f}%] | "
              f"Lift={lift:+5.2f}pp | WAcc={m['acc_weighted']*100:5.2f}% | Dir={m['ret_dir_bps']:+5.2f}bp | {dec}")

    # --- PRODUTO B (SEPARADO: decisão em open_t + N segundos, N in {3, 5, 10}) ---
    print("\n--- PRODUTO B (SEPARADO, 2 dias): Decisao em open_t + N s vs Baseline Justo Follow_N ---")
    for N in [3, 5, 10]:
        bl_follow = dfm[f"dir_first_{N}s"].values
        m_bl = evaluate_predictions(y[1:], bl_follow[1:], w[1:], r[1:])
        # Hipótese B1: apenas fluxo agressor dos primeiros N segundos (sem olhar preço)
        d_n = dfm[f"delta_first_{N}s"].values
        p_delta = np.where(d_n > 0, 1, np.where(d_n < 0, 0, -1))
        m_d = evaluate_predictions(y[1:], p_delta[1:], w[1:], r[1:])
        # Hipótese B2: filtro de concordância Follow_N + fluxo agressor dos primeiros N segundos
        p_both = np.where((bl_follow == p_delta) & (bl_follow >= 0), bl_follow, -1)
        m_both = evaluate_predictions(y[1:], p_both[1:], w[1:], r[1:])
        # Hipótese B3: Follow_N concordando com vela anterior t-1
        p_prev = np.where((bl_follow == prev_lbl) & (bl_follow >= 0), bl_follow, -1)
        m_prev = evaluate_predictions(y[1:], p_prev[1:], w[1:], r[1:])

        print(f"\n  [Produto B — N={N}s]")
        print(f"    {'BL_Follow_' + str(N) + 's':<26} | N={m_bl['n_sig']:>5} ({m_bl['coverage']*100:5.1f}%) | "
              f"Acc={m_bl['acc']*100:5.2f}% [{m_bl['wilson_lo']*100:5.2f}%,{m_bl['wilson_hi']*100:5.2f}%] | "
              f"WAcc={m_bl['acc_weighted']*100:5.2f}% | Dir={m_bl['ret_dir_bps']:+5.2f}bp")
        for nm, mm in [(f"ProdB_delta_{N}s", m_d), (f"ProdB_Follow+Delta_{N}s", m_both), (f"ProdB_Follow+Prev1m_{N}s", m_prev)]:
            lift = (mm["acc"] - m_bl["acc"]) * 100
            wlift = (mm["acc_weighted"] - m_bl["acc_weighted"]) * 100
            beats = (lift >= 3.0) and (mm["wilson_lo"] > m_bl["acc"]) and (wlift > 0)
            dec = "CANDIDATA (2d amostra curta)" if beats else "MORRE"
            print(f"    {nm:<26} | N={mm['n_sig']:>5} ({mm['coverage']*100:5.1f}%) | "
                  f"Acc={mm['acc']*100:5.2f}% [{mm['wilson_lo']*100:5.2f}%,{mm['wilson_hi']*100:5.2f}%] | "
                  f"Lift={lift:+5.2f}pp | WAcc={mm['acc_weighted']*100:5.2f}% ({wlift:+5.2f}pp) | Dir={mm['ret_dir_bps']:+5.2f}bp | {dec}")


def main():
    print("=" * 135)
    print("FASE 3 — TESTE DE HIPOTESES UMA POR CICLO (ABLACAO ESTRITA — HOLDOUT 7 DIAS FECHADO)")
    print("=" * 135)

    df_raw = compute_frozen_labels(pd.read_parquet(DATA_PARQUET))
    df_raw["date"] = df_raw["open_time"].dt.date
    unique_dates = sorted(df_raw["date"].unique())
    holdout_dates = unique_dates[-HOLDOUT_DAYS:]
    dev_dates = unique_dates[:-HOLDOUT_DAYS]
    wf_test_dates = dev_dates[TRAIN_DAYS + 1:]

    oos_mask = df_raw["date"].isin(wf_test_dates).values
    y_oos = df_raw["label"].values[oos_mask]
    w_oos = df_raw["body_abs"].values[oos_mask]
    r_oos = df_raw["candle_ret"].values[oos_mask]
    d_oos = df_raw["date"].values[oos_mask]
    oos_dates_sorted = sorted(np.unique(d_oos))
    weeks_dates = [oos_dates_sorted[i:i + 7] for i in range(0, len(oos_dates_sorted), 7)]

    # Melhor baseline da Fase 2 (B2_vela_anterior) na janela OOS comum
    prev_lbl = np.full(len(df_raw), -1, dtype=int)
    prev_lbl[1:] = df_raw["label"].values[:-1]
    m_b2 = evaluate_predictions(y_oos, prev_lbl[oos_mask], w_oos, r_oos)
    best_bl_acc = m_b2["acc"]
    best_bl_wacc = m_b2["acc_weighted"]

    print(f"Janela OOS Dev : {wf_test_dates[0]} -> {wf_test_dates[-1]} (N={len(y_oos)} velas, {len(weeks_dates)} semanas)")
    print(f"HOLDOUT FECHADO: {holdout_dates[0]} -> {holdout_dates[-1]} (NAO TOCADO)")
    print(f"Melhor Baseline: B2_vela_anterior | N={m_b2['n_sig']} ({m_b2['coverage']*100:.1f}%) | "
          f"Acc={best_bl_acc*100:.2f}% [{m_b2['wilson_lo']*100:.2f}%,{m_b2['wilson_hi']*100:.2f}%] | "
          f"WAcc={best_bl_wacc*100:.2f}% | Dir={m_b2['ret_dir_bps']:+.2f}bp")

    all_results = []
    n = len(df_raw)
    dates_arr = df_raw["date"].values

    # =========================================================================
    # CICLO 1 — Hipótese #2: Fluxo agressor do minuto fechado (Spot 1m)
    # =========================================================================
    print("\n" + "=" * 135)
    print("CICLO 1 — HIPOTESE #2: FLUXO AGRESSOR CAUSAL DO MINUTO FECHADO (SPOT M1, 44d OOS)")
    print("=" * 135)
    v = df_raw["volume"].astype(float).values
    tbb = df_raw["taker_buy_base"].astype(float).values
    delta_spot = np.where(v > 0, (2.0 * tbb - v) / v, 0.0)
    # Alinha causalmente: delta_{t-1} prevê vela t
    delta_spot_prev = np.full(n, np.nan)
    delta_spot_prev[1:] = delta_spot[:-1]

    p_h2_all = np.where(delta_spot_prev > 0, 1, np.where(delta_spot_prev < 0, 0, -1))
    res = eval_hypothesis_full("H2a_delta_spot_1m", p_h2_all, df_raw, oos_mask, best_bl_acc, best_bl_wacc, weeks_dates)
    print_hyp_result(res); all_results.append(res)

    # Versão extrema (top 25% |delta| calibrado apenas no treino de 14d)
    mask_q75 = causal_rolling_threshold_mask(delta_spot_prev, dates_arr, wf_test_dates, q=0.75)
    p_h2_q75 = np.where(mask_q75, p_h2_all, -1)
    res = eval_hypothesis_full("H2a_delta_spot_Q75", p_h2_q75, df_raw, oos_mask, best_bl_acc, best_bl_wacc, weeks_dates)
    print_hyp_result(res); all_results.append(res)

    # Top 10% |delta| calibrado no treino
    mask_q90 = causal_rolling_threshold_mask(delta_spot_prev, dates_arr, wf_test_dates, q=0.90)
    p_h2_q90 = np.where(mask_q90, p_h2_all, -1)
    res = eval_hypothesis_full("H2a_delta_spot_Q90", p_h2_q90, df_raw, oos_mask, best_bl_acc, best_bl_wacc, weeks_dates)
    print_hyp_result(res); all_results.append(res)

    # =========================================================================
    # CICLO 2 — Hipótese #4: Divergência Spot vs Perp + Basis (44d OOS)
    # =========================================================================
    print("\n" + "=" * 135)
    print("CICLO 2 — HIPOTESE #4: DIVERGENCIA SPOT vs PERP + BASIS (fapi/v1/klines, 44d OOS)")
    print("=" * 135)
    if PERP_PARQUET.exists():
        df_perp = pd.read_parquet(PERP_PARQUET).sort_values("open_time").reset_index(drop=True)
        m_sp = df_raw[["open_time", "open", "close", "volume", "taker_buy_base"]].merge(
            df_perp[["open_time", "open", "close", "volume", "taker_buy_base"]],
            on="open_time", how="left", suffixes=("_spot", "_perp")
        )
        vp = m_sp["volume_perp"].astype(float).values
        tbbp = m_sp["taker_buy_base_perp"].astype(float).values
        delta_perp = np.where(vp > 0, (2.0 * tbbp - vp) / vp, np.nan)
        perp_color = np.where(m_sp["close_perp"] > m_sp["open_perp"], 1,
                              np.where(m_sp["close_perp"] < m_sp["open_perp"], 0, -1))
        # Causal shift(+1): observado em t-1 prevê vela t
        delta_perp_prev = np.full(n, np.nan); delta_perp_prev[1:] = delta_perp[:-1]
        perp_color_prev = np.full(n, -1, dtype=int); perp_color_prev[1:] = perp_color[:-1]

        div_sp_prev = delta_spot_prev - delta_perp_prev  # >0: spot mais comprador que perp
        basis = (m_sp["close_perp"].astype(float).values - m_sp["close_spot"].astype(float).values) / m_sp["close_spot"].astype(float).values
        basis_s = pd.Series(basis)
        basis_z = ((basis_s - basis_s.rolling(60).mean()) / basis_s.rolling(60).std().replace(0, np.nan)).values
        basis_z_prev = np.full(n, np.nan); basis_z_prev[1:] = basis_z[:-1]

        # H4a: Perp delta isolado (perp puxa spot)
        p_h4_perp = np.where(delta_perp_prev > 0, 1, np.where(delta_perp_prev < 0, 0, -1))
        res = eval_hypothesis_full("H4a_delta_perp_1m", p_h4_perp, df_raw, oos_mask, best_bl_acc, best_bl_wacc, weeks_dates)
        print_hyp_result(res); all_results.append(res)

        # H4a2: Cor da vela anterior do PERP prevendo vela SPOT
        res = eval_hypothesis_full("H4b_cor_perp_prev", perp_color_prev, df_raw, oos_mask, best_bl_acc, best_bl_wacc, weeks_dates)
        print_hyp_result(res); all_results.append(res)

        # H4c: Divergência Spot - Perp (Spot lidera) e inversa (Perp lidera)
        p_div_spot = np.where(div_sp_prev > 0, 1, np.where(div_sp_prev < 0, 0, -1))
        res = eval_hypothesis_full("H4c_div_spot_minus_perp", p_div_spot, df_raw, oos_mask, best_bl_acc, best_bl_wacc, weeks_dates)
        print_hyp_result(res); all_results.append(res)

        p_div_perp = np.where(div_sp_prev < 0, 1, np.where(div_sp_prev > 0, 0, -1))
        res = eval_hypothesis_full("H4c_div_perp_minus_spot", p_div_perp, df_raw, oos_mask, best_bl_acc, best_bl_wacc, weeks_dates)
        print_hyp_result(res); all_results.append(res)

        # H4d: Divergência extrema (Q80 calibrado no treino)
        mask_div_q80 = causal_rolling_threshold_mask(div_sp_prev, dates_arr, wf_test_dates, q=0.80)
        res = eval_hypothesis_full("H4d_div_perp_Q80", np.where(mask_div_q80, p_div_perp, -1), df_raw, oos_mask, best_bl_acc, best_bl_wacc, weeks_dates)
        print_hyp_result(res); all_results.append(res)

        # H4e: Basis z-score (momentum: basis alto puxa spot p/ cima; reversão: basis alto reverte)
        mask_bz_q80 = causal_rolling_threshold_mask(basis_z_prev, dates_arr, wf_test_dates, q=0.80)
        p_bz_mom = np.where(mask_bz_q80 & (basis_z_prev > 0), 1, np.where(mask_bz_q80 & (basis_z_prev < 0), 0, -1))
        p_bz_rev = np.where(mask_bz_q80 & (basis_z_prev > 0), 0, np.where(mask_bz_q80 & (basis_z_prev < 0), 1, -1))
        res = eval_hypothesis_full("H4e_basis_z60_mom_Q80", p_bz_mom, df_raw, oos_mask, best_bl_acc, best_bl_wacc, weeks_dates)
        print_hyp_result(res); all_results.append(res)
        res = eval_hypothesis_full("H4e_basis_z60_rev_Q80", p_bz_rev, df_raw, oos_mask, best_bl_acc, best_bl_wacc, weeks_dates)
        print_hyp_result(res); all_results.append(res)
    else:
        print("NAO_MEDIDO: BTCUSDT_perp_1m_65d.parquet nao encontrado.")

    # =========================================================================
    # CICLO 3 — Hipótese #5: Filtros de regime (sobre B2_vela_anterior; exige WAcc > base)
    # =========================================================================
    print("\n" + "=" * 135)
    print("CICLO 3 — HIPOTESE #5: FILTROS DE REGIME SOBRE B2_vela_anterior (SO SOBREVIVE SE MELHORA WAcc + LIFT>=3pp)")
    print("=" * 135)
    ret1 = df_raw["close"].pct_change(1)
    vol15 = ret1.rolling(15).std().values
    vol60 = ret1.rolling(60).std().values
    vol15_prev = np.full(n, np.nan); vol15_prev[1:] = vol15[:-1]
    vol60_prev = np.full(n, np.nan); vol60_prev[1:] = vol60[:-1]

    mask_vol15_hi = causal_rolling_threshold_above(vol15_prev, dates_arr, wf_test_dates, q=0.50)
    mask_vol15_lo = ~mask_vol15_hi & ~np.isnan(vol15_prev)
    mask_vol60_hi = causal_rolling_threshold_above(vol60_prev, dates_arr, wf_test_dates, q=0.50)

    res = eval_hypothesis_full("H5a_B2_vol15_alta", np.where(mask_vol15_hi, prev_lbl, -1), df_raw, oos_mask, best_bl_acc, best_bl_wacc, weeks_dates, is_filter=True, base_sig_wacc=best_bl_wacc)
    print_hyp_result(res); all_results.append(res)
    res = eval_hypothesis_full("H5a_B2_vol15_baixa", np.where(mask_vol15_lo, prev_lbl, -1), df_raw, oos_mask, best_bl_acc, best_bl_wacc, weeks_dates, is_filter=True, base_sig_wacc=best_bl_wacc)
    print_hyp_result(res); all_results.append(res)
    res = eval_hypothesis_full("H5a_B2_vol60_alta", np.where(mask_vol60_hi, prev_lbl, -1), df_raw, oos_mask, best_bl_acc, best_bl_wacc, weeks_dates, is_filter=True, base_sig_wacc=best_bl_wacc)
    print_hyp_result(res); all_results.append(res)

    ema5m_bias = build_closed_htf_ema_bias(df_raw, "5min", ema_span=20)
    ema15m_bias = build_closed_htf_ema_bias(df_raw, "15min", ema_span=20)
    p_b2_ema5m = np.where((prev_lbl >= 0) & (prev_lbl == ema5m_bias), prev_lbl, -1)
    p_b2_ema15m = np.where((prev_lbl >= 0) & (prev_lbl == ema15m_bias), prev_lbl, -1)
    res = eval_hypothesis_full("H5b_B2_com_EMA20_5m_fech", p_b2_ema5m, df_raw, oos_mask, best_bl_acc, best_bl_wacc, weeks_dates, is_filter=True, base_sig_wacc=best_bl_wacc)
    print_hyp_result(res); all_results.append(res)
    res = eval_hypothesis_full("H5b_B2_com_EMA20_15m_fech", p_b2_ema15m, df_raw, oos_mask, best_bl_acc, best_bl_wacc, weeks_dates, is_filter=True, base_sig_wacc=best_bl_wacc)
    print_hyp_result(res); all_results.append(res)

    # H5c: OI 5m fechado (subjanela com OI disponível dentro do Dev OOS, holdout continua fechado!)
    if OI_PARQUET.exists():
        df_oi = pd.read_parquet(OI_PARQUET).sort_values("timestamp").reset_index(drop=True)
        df_oi["oi_chg"] = df_oi["sumOpenInterest"].diff()
        # Regra causal estrita: para open_t, usamos a última barra 5m fechada = floor(open_t, 5min) - 5min
        oi_map = dict(zip(df_oi["timestamp"], df_oi["oi_chg"]))
        prev_5m_ts = df_raw["open_time"].dt.floor("5min") - pd.Timedelta(minutes=5)
        oi_chg_causal = prev_5m_ts.map(oi_map).values
        sig_5m = build_last_closed_5m_signal(df_raw)

        # Subjanela OOS onde OI existe (sem tocar holdout!)
        oi_oos_mask = oos_mask & ~np.isnan(oi_chg_causal)
        if oi_oos_mask.sum() >= 1000:
            m_b2_oi = evaluate_predictions(df_raw["label"].values[oi_oos_mask], prev_lbl[oi_oos_mask],
                                           df_raw["body_abs"].values[oi_oos_mask], df_raw["candle_ret"].values[oi_oos_mask])
            oi_dates = sorted(np.unique(df_raw["date"].values[oi_oos_mask]))
            oi_weeks = [oi_dates[i:i + 7] for i in range(0, len(oi_dates), 7)]
            print(f"  [Subjanela OI 5m Dev (sem holdout): {oi_dates[0]} -> {oi_dates[-1]}, N={int(oi_oos_mask.sum())} | "
                  f"B2 subjanela: Acc={m_b2_oi['acc']*100:.2f}%, WAcc={m_b2_oi['acc_weighted']*100:.2f}%]")
            # Filtro: OI subindo + preço 5m fechado na mesma direção de B2
            p_oi_align = np.where((oi_chg_causal > 0) & (sig_5m >= 0) & (prev_lbl == sig_5m), prev_lbl, -1)
            res = eval_hypothesis_full("H5c_B2_OI5m_up_alinhado", p_oi_align, df_raw, oi_oos_mask, m_b2_oi["acc"], m_b2_oi["acc_weighted"], oi_weeks, is_filter=True, base_sig_wacc=m_b2_oi["acc_weighted"])
            print_hyp_result(res); all_results.append(res)
            p_oi_up = np.where(oi_chg_causal > 0, prev_lbl, -1)
            res = eval_hypothesis_full("H5c_B2_OI5m_up", p_oi_up, df_raw, oi_oos_mask, m_b2_oi["acc"], m_b2_oi["acc_weighted"], oi_weeks, is_filter=True, base_sig_wacc=m_b2_oi["acc_weighted"])
            print_hyp_result(res); all_results.append(res)
        else:
            print(f"  OI 5m com sobreposicao insuficiente na janela Dev OOS (N={int(oi_oos_mask.sum())}).")

    # =========================================================================
    # CICLO 4 — Hipótese #6: Sequência de cores (3-5 velas) e CCI(20) em M1
    # =========================================================================
    print("\n" + "=" * 135)
    print("CICLO 4 — HIPOTESE #6: SEQUENCIA DE CORES (3 a 5 VELAS) E CCI(20) EM M1 (44d OOS)")
    print("=" * 135)
    lbl = df_raw["label"].values
    for k_seq in [3, 4, 5]:
        p_rev = np.full(n, -1, dtype=int)
        p_cont = np.full(n, -1, dtype=int)
        for t in range(k_seq, n):
            win = lbl[t - k_seq:t]
            if (win == 1).all():
                p_rev[t] = 0
                p_cont[t] = 1
            elif (win == 0).all():
                p_rev[t] = 1
                p_cont[t] = 0
        res = eval_hypothesis_full(f"H6a_seq{k_seq}_reversao", p_rev, df_raw, oos_mask, best_bl_acc, best_bl_wacc, weeks_dates)
        print_hyp_result(res); all_results.append(res)
        res = eval_hypothesis_full(f"H6a_seq{k_seq}_continuacao", p_cont, df_raw, oos_mask, best_bl_acc, best_bl_wacc, weeks_dates)
        print_hyp_result(res); all_results.append(res)

    # CCI(20) causal (calculado até t-1)
    tp = (df_raw["high"] + df_raw["low"] + df_raw["close"]) / 3.0
    tp_ma = tp.rolling(20).mean()
    tp_md = tp.rolling(20).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True).replace(0, np.nan)
    cci20 = ((tp - tp_ma) / (0.015 * tp_md)).values
    cci_prev = np.full(n, np.nan); cci_prev[1:] = cci20[:-1]
    p_cci_rev = np.where(cci_prev < -100, 1, np.where(cci_prev > 100, 0, -1))
    p_cci_cont = np.where(cci_prev > 100, 1, np.where(cci_prev < -100, 0, -1))
    res = eval_hypothesis_full("H6b_CCI20_reversao_100", p_cci_rev, df_raw, oos_mask, best_bl_acc, best_bl_wacc, weeks_dates)
    print_hyp_result(res); all_results.append(res)
    res = eval_hypothesis_full("H6b_CCI20_continuacao_100", p_cci_cont, df_raw, oos_mask, best_bl_acc, best_bl_wacc, weeks_dates)
    print_hyp_result(res); all_results.append(res)

    # =========================================================================
    # CICLO EXTRA (EXCECAO SOLICITADA PELO USUARIO) — PADRAO OCO / OCOI CAUSAL
    # =========================================================================
    print("\n" + "=" * 135)
    print("TESTE EXTRA (EXCECAO DO USUARIO — FORA DA FILA SECAO 2): OCO / OCOI CAUSAL NO GRAFICO DE LINHAS (CLOSE M1)")
    print("=" * 135)
    closes = df_raw["close"].astype(float).values
    for r_ord, sym_t, hold_b in [(2, 0.0025, 1), (3, 0.0025, 1), (5, 0.0035, 1), (3, 0.0035, 3)]:
        p_oco = detect_causal_oco_signals(closes, order_r=r_ord, lookback=60, sym_tol=sym_t, hold_bars=hold_b)
        nm = f"EXTRA_OCO_R{r_ord}_tol{int(sym_t*10000)}bp_h{hold_b}"
        res = eval_hypothesis_full(nm, p_oco, df_raw, oos_mask, best_bl_acc, best_bl_wacc, weeks_dates)
        print_hyp_result(res); all_results.append(res)
        # Também imprime F1 (sonda) para confirmar ausência de vazamento
        print(f"    -> Atraso confirmacao pivo = {r_ord} velas M1 | F1(futuro)={res['f1_acc']*100:.2f}% | "
              f"CALL={res['metrics']['ret_call_bps']:+.2f}bp PUT={res['metrics']['ret_put_bps']:+.2f}bp")

    # =========================================================================
    # PRODUTO A (1s: 5s/15s/60s) e PRODUTO B (SEPARADO: open_t + 3/5/10s)
    # =========================================================================
    run_1s_hypotheses_and_product_b()

    # =========================================================================
    # RESUMO FINAL DA FASE 3
    # =========================================================================
    survivors = [r["name"] for r in all_results if r["decision"] == "SOBREVIVE"]
    print("\n" + "=" * 135)
    print(f"RESUMO FASE 3 (PRODUTO A): Total testadas = {len(all_results)} | Sobreviventes = {len(survivors)} -> {survivors}")
    print("=" * 135)


if __name__ == "__main__":
    main()
