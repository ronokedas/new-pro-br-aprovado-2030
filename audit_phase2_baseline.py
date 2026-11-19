"""
audit_phase2_baseline.py
========================
Fase 2 do protocolo de auditoria quantitativa (prompat.txt):
Reproduz e avalia o sistema ATUAL sem nenhuma feature nova, usando:
- Mercado congelado: Binance Spot BTCUSDT M1 (BTCUSDT_1m_65d.parquet)
- Label congelado (Seção 0):
    1  se close_t > open_t  (VERDE)
    0  se close_t < open_t  (VERMELHA)
   -1  se close_t == open_t (EMPATE / DOJI — classe à parte, nunca descartada após resultado)
- Política declarada previamente:
    * Empate (label == -1) é sempre contabilizado como ERRO quando um sinal direcional (CALL/PUT) é emitido.
    * Reportamos N total, N sinais, cobertura, taxa de empates, acurácia crua, IC Wilson 95%,
      acurácia ponderada por |close - open|, retorno médio condicional (CALL vs PUT) e retorno direcional médio (bps).
- Holdout final: últimos 7 dias (10.080 velas) RESERVADOS e INTOCADOS nesta fase.
- Walk-forward congelado (desenvolvimento):
    * Treino: 14 dias (20.160 velas)
    * Purga / Embargo: 200 velas (maior lookback = EMA 200)
    * Teste: 1 dia (1.440 velas), avançando 1 dia por vez.
- Modelos atuais avaliados (Produto A — decisão estritamente em open_t usando dados <= t-1):
    1. Sistema ativo real: engine/confluences.py::compute_technical_bias
    2. Modelo ML do repositório: trainer/train.py (StandardScaler + HistGradientBoostingClassifier,
       25 features originais de train.py, treinado em walk-forward sem shuffle e sem look-ahead)
- Quatro baselines ingênuos obrigatórios na mesma janela:
    B1: Classe majoritária do treino (entre verde=1 e vermelha=0)
    B2: Sinal da vela anterior (t-1)
    B3: Sinal contrário da vela anterior (t-1)
    B4: Sinal da última vela de 5m já fechada estritamente antes de open_t
- Três testes de falsificação obrigatórios:
    F1: Feature deslocada 1 barra para o futuro (shift(-1) nas features -> deve disparar se causalidade original estiver íntegra; também testa shift(+1) / futuro estrito)
    F2: Embaralhar label dentro de cada dia UTC preservando margem (200 permutações)
    F3: Atrasar o sinal em 1 vela (aplicar previsão de t na vela t+1)
"""

import sys
import math
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import HistGradientBoostingClassifier

# Caminhos
REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from engine.confluences import compute_technical_bias

DATA_PARQUET = Path(r"C:\trading-agents\btc_color_research\data\BTCUSDT_1m_65d.parquet")
FEATURES_PARQUET = Path(r"C:\trading-agents\btc_color_research\results\features_1m.parquet")

TRAIN_DAYS = 14
PURGE_BARS = 200
HOLDOUT_DAYS = 7

TRAIN_PY_FEATURES = [
    "ema9_21_cross", "ema50_150_cross", "price_vs_ema50", "price_vs_ema200",
    "rsi14", "rsi7", "rsi_diff", "macd_line", "macd_signal", "macd_hist",
    "cci20", "cci50", "vol_zscore_20", "vol_zscore_60", "vol_ma_ratio",
    "cvd5_norm", "cvd30_norm", "range_pos60", "ret1", "ret3", "ret5",
    "vol_real_10", "vol_real_60", "hl_range", "body_pct"
]


def wilson_ci(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1.0 + (z * z) / n
    center = (p + (z * z) / (2.0 * n)) / denom
    half = (z * math.sqrt((p * (1.0 - p) + (z * z) / (4.0 * n)) / n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def compute_frozen_labels(df: pd.DataFrame) -> pd.DataFrame:
    """
    Constrói o label congelado da Seção 0 para cada vela t:
      label =  1 se close_t > open_t
      label =  0 se close_t < open_t
      label = -1 se close_t == open_t (empate)
    """
    out = df.sort_values("open_time").reset_index(drop=True).copy()
    out["label"] = np.where(
        out["close"] > out["open"], 1,
        np.where(out["close"] < out["open"], 0, -1)
    ).astype(int)
    out["body_abs"] = (out["close"] - out["open"]).abs()
    out["candle_ret"] = (out["close"] - out["open"]) / out["open"]
    return out


def compute_confluences_vectorized(df: pd.DataFrame) -> np.ndarray:
    """
    Reproduz exatamente engine/confluences.py::compute_technical_bias sobre
    o histórico causal até a vela i (para prever a vela i+1).
    Retorna array de previsões para a vela t (1=CALL, 0=PUT, -1=sem sinal),
    alinhado ao índice t (ou seja, calculado usando dados até t-1!).
    """
    c = df["close"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    o = df["open"].astype(float)
    v = df["volume"].astype(float)
    tbb = df["taker_buy_base"].astype(float)

    n = len(df)
    alta = np.zeros(n, dtype=float)
    baixa = np.zeros(n, dtype=float)

    # 1. Fluxo real: CVD & Agressão (confluences.py:50-68)
    last_v = np.where(v > 0, v, 1.0)
    last_buy = tbb.values
    last_sell = np.maximum(0.0001, last_v - last_buy)
    cvd_1m = last_buy - last_sell
    agg_ratio = last_buy / last_sell

    cond_flow_bull = (agg_ratio >= 1.20) | (cvd_1m > 1.5)
    cond_flow_bear = (~cond_flow_bull) & ((agg_ratio <= 0.83) | (cvd_1m < -1.5))
    alta += np.where(cond_flow_bull, 4.0, 0.0)
    baixa += np.where(cond_flow_bear, 4.0, 0.0)

    # 2. Ação do preço (pavios) (confluences.py:73-87)
    candle_range = np.maximum(0.01, (h - l).values)
    lower_wick = (np.minimum(o.values, c.values) - l.values) / candle_range
    upper_wick = (h.values - np.maximum(o.values, c.values)) / candle_range

    cond_wick_bull = (lower_wick >= 0.40) & (cvd_1m >= 0)
    cond_wick_bear = (~cond_wick_bull) & (upper_wick >= 0.40) & (cvd_1m <= 0)
    alta += np.where(cond_wick_bull, 3.0, 0.0)
    baixa += np.where(cond_wick_bear, 3.0, 0.0)

    # 3. RSI (14) (confluences.py:93-105)
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi14 = (100 - (100 / (1 + rs))).fillna(50.0).values

    alta += np.where(rsi14 < 35, 2.0, 0.0)
    baixa += np.where(rsi14 > 65, 2.0, 0.0)

    # 4. Estocástico %K (14, 3, 3) (confluences.py:108-119)
    low14 = l.rolling(14, min_periods=3).min()
    high14 = h.rolling(14, min_periods=3).max()
    stoch_k = (((c - low14) / (high14 - low14 + 1e-9)) * 100.0).rolling(3, min_periods=1).mean().fillna(50.0).values

    alta += np.where(stoch_k < 20, 2.0, 0.0)
    baixa += np.where(stoch_k > 80, 2.0, 0.0)

    # 5. CCI (20) (confluences.py:122-134)
    tp = (h + l + c) / 3.0
    tp_ma = tp.rolling(20, min_periods=3).mean()
    tp_md = tp.rolling(20, min_periods=3).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True).replace(0, np.nan)
    cci20 = ((tp - tp_ma) / (0.015 * tp_md)).fillna(0.0).values

    alta += np.where(cci20 < -100, 2.0, 0.0)
    baixa += np.where(cci20 > 100, 2.0, 0.0)

    # 6. MACD (12, 26, 9) (confluences.py:137-148)
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd_l = ema12 - ema26
    macd_sig = macd_l.ewm(span=9, adjust=False).mean()
    macd_hist = (macd_l - macd_sig).values

    alta += np.where(macd_hist > 0, 1.0, 0.0)
    baixa += np.where(macd_hist < 0, 1.0, 0.0)

    # 7. EMAs [10, 20, 50, 100, 200] (confluences.py:153-159)
    c_vals = c.values
    for span in [10, 20, 50, 100, 200]:
        ema_val = c.ewm(span=span, adjust=False).mean().values
        alta += np.where(c_vals > ema_val, 1.0, 0.0)
        baixa += np.where(c_vals <= ema_val, 1.0, 0.0)

    # 8. VWMA (20) (confluences.py:162-168)
    pv = c * v
    vwma20 = (pv.rolling(20, min_periods=3).sum() / v.rolling(20, min_periods=3).sum().replace(0, np.nan)).values
    valid_vwma = ~np.isnan(vwma20)
    alta += np.where(valid_vwma & (c_vals > vwma20), 1.0, 0.0)
    baixa += np.where(valid_vwma & (c_vals <= vwma20), 1.0, 0.0)

    # Consolidação (confluences.py:173-188)
    total_dir = np.where((alta + baixa) == 0, 1.0, alta + baixa)
    prob_alta = alta / total_dir
    # Sinal gerado no fechamento da barra i -> aplicado à barra i+1!
    pred_at_close_i = np.where(prob_alta >= 0.50, 1, 0).astype(int)

    # Shift(+1): previsão para a vela t usa estritamente dados fechados até t-1
    pred_for_t = np.full(n, -1, dtype=int)
    pred_for_t[1:] = pred_at_close_i[:-1]
    return pred_for_t


def verify_confluences_exact_match(df: pd.DataFrame, vec_preds: np.ndarray, n_checks: int = 200) -> tuple[int, int]:
    """
    Valida em n_checks índices que a versão vetorizada bate com compute_technical_bias() real.
    Nota: compara usando janela longa (para EMA200 aquecida) e também reporta contra janela 200 (buffer WS).
    """
    rng = np.random.default_rng(42)
    idxs = rng.integers(500, len(df) - 1, size=n_checks)
    records = df[["open", "high", "low", "close", "volume", "taker_buy_base"]].to_dict("records")
    exact_full = 0
    exact_ws200 = 0
    for idx in idxs:
        # Para prever a vela idx, o sistema usa velas fechadas [:idx] (ou seja, até idx-1)
        res_full = compute_technical_bias(records[max(0, idx - 1000):idx])
        pred_full = 1 if res_full["direction"] == "CALL" else 0
        if pred_full == vec_preds[idx]:
            exact_full += 1

        res_ws = compute_technical_bias(records[max(0, idx - 200):idx])
        pred_ws = 1 if res_ws["direction"] == "CALL" else 0
        if pred_ws == vec_preds[idx]:
            exact_ws200 += 1
    return exact_full, exact_ws200


def build_last_closed_5m_signal(df: pd.DataFrame) -> np.ndarray:
    """
    Baseline B4: sinal da última vela de 5m JÁ FECHADA estritamente antes de open_t.
    Para open_t, a barra 5m em andamento começa em floor(open_t, '5min').
    A última barra 5m fechada começou em floor(open_t, '5min') - 5min.
    """
    tmp = df[["open_time", "open", "close"]].copy()
    tmp["bucket_5m"] = tmp["open_time"].dt.floor("5min")
    bars_5m = tmp.groupby("bucket_5m").agg(
        open_5m=("open", "first"),
        close_5m=("close", "last"),
        cnt=("open", "count"),
    ).reset_index()
    # Só considera barra 5m completa (5 velas M1)
    bars_5m["sig_5m"] = np.where(
        bars_5m["cnt"] < 5, -1,
        np.where(bars_5m["close_5m"] > bars_5m["open_5m"], 1,
                 np.where(bars_5m["close_5m"] < bars_5m["open_5m"], 0, -1))
    )
    sig_map = dict(zip(bars_5m["bucket_5m"], bars_5m["sig_5m"]))
    prev_closed_bucket = tmp["bucket_5m"] - pd.Timedelta(minutes=5)
    return prev_closed_bucket.map(sig_map).fillna(-1).astype(int).values


def evaluate_predictions(
    labels: np.ndarray,
    preds: np.ndarray,
    body_abs: np.ndarray,
    candle_ret: np.ndarray,
) -> dict:
    """
    Avalia predições (1=CALL, 0=PUT, -1=sem sinal) contra label congelado (1, 0, -1=empate).
    Política congelada: empate (-1) conta como ERRO para qualquer sinal direcional emitido.
    """
    n_total = len(labels)
    sig_mask = preds >= 0
    n_sig = int(sig_mask.sum())
    cov = n_sig / n_total if n_total > 0 else 0.0

    if n_sig == 0:
        return {
            "n_total": n_total, "n_sig": 0, "coverage": 0.0, "tie_rate": 0.0,
            "acc": 0.0, "wilson_lo": 0.0, "wilson_hi": 0.0,
            "acc_ex_tie": 0.0, "acc_weighted": 0.0,
            "ret_call_bps": 0.0, "ret_put_bps": 0.0, "ret_dir_bps": 0.0
        }

    y = labels[sig_mask]
    p = preds[sig_mask]
    w = body_abs[sig_mask]
    r = candle_ret[sig_mask]

    correct = (y == p)  # se y == -1 (empate), y == p é False (erro)
    k = int(correct.sum())
    acc = k / n_sig
    ci_lo, ci_hi = wilson_ci(k, n_sig)

    ties = (y == -1)
    tie_rate = float(ties.mean())
    non_tie = ~ties
    acc_ex_tie = float((y[non_tie] == p[non_tie]).mean()) if non_tie.sum() > 0 else 0.0

    w_sum = float(w.sum())
    acc_weighted = float(w[correct].sum() / w_sum) if w_sum > 0 else 0.0

    call_mask = (p == 1)
    put_mask = (p == 0)
    ret_call_bps = float(r[call_mask].mean() * 1e4) if call_mask.sum() > 0 else float("nan")
    ret_put_bps = float(r[put_mask].mean() * 1e4) if put_mask.sum() > 0 else float("nan")
    # Retorno direcional: +ret se CALL, -ret se PUT
    dir_sign = np.where(p == 1, 1.0, -1.0)
    ret_dir_bps = float((dir_sign * r).mean() * 1e4)

    return {
        "n_total": n_total,
        "n_sig": n_sig,
        "coverage": cov,
        "tie_rate": tie_rate,
        "acc": acc,
        "wilson_lo": ci_lo,
        "wilson_hi": ci_hi,
        "acc_ex_tie": acc_ex_tie,
        "acc_weighted": acc_weighted,
        "ret_call_bps": ret_call_bps,
        "ret_put_bps": ret_put_bps,
        "ret_dir_bps": ret_dir_bps,
    }


def run_walk_forward_train_py_model(df_raw: pd.DataFrame, df_feat: pd.DataFrame, test_dates: list, all_dates: pd.Series):
    """
    Reproduz o modelo de trainer/train.py (StandardScaler + HistGradientBoostingClassifier,
    25 features de train.py) em walk-forward congelado:
      - Treina em 14 dias anteriores com PURGA de 200 velas antes do início do dia de teste.
      - Avalia em 1 dia de teste.
      - Retorna dois vetores de previsão alinhados a df_raw:
          1. preds_orig_target: treinado exatamente como train.py (doji=0 no y_train)
          2. preds_clean_target: treinado excluindo empates (close==open) do y_train
          3. preds_shifted_future: Teste de falsificação F1 (features deslocadas 1 barra p/ futuro: usa features da própria barra t)
    """
    # Em features_1m.parquet, a linha i contém features da vela i e target = cor da vela i+1.
    # Portanto, para prever a vela t (índice t de df_raw), a feature causal é a linha (t-1) de df_feat!
    # Vamos alinhar por open_time da vela PREVISTA (t) de forma explícita e auditável.
    feat_cols = [c for c in TRAIN_PY_FEATURES if c in df_feat.columns]

    # Mapa: open_time da linha i -> deslocamos open_time para frente em 1 minuto para alinhar à vela t = i+1
    f = df_feat[["open_time"] + feat_cols].copy()
    f = f.sort_values("open_time").reset_index(drop=True)
    # Alinhamento causal: features observadas no close de (t - 1min) preveem a vela t
    f["target_open_time"] = f["open_time"] + pd.Timedelta(minutes=1)

    merged = df_raw[["open_time", "label", "body_abs", "candle_ret"]].merge(
        f.drop(columns=["open_time"]),
        left_on="open_time",
        right_on="target_open_time",
        how="left"
    )
    # Também preparar features deslocadas 1 barra para o futuro (observadas no close de t -> F1 vazamento)
    f_fut = df_feat[["open_time"] + feat_cols].copy()
    merged_fut = df_raw[["open_time"]].merge(f_fut, on="open_time", how="left")

    n = len(df_raw)
    preds_orig = np.full(n, -1, dtype=int)
    preds_clean = np.full(n, -1, dtype=int)
    preds_f1_future = np.full(n, -1, dtype=int)
    maj_baseline = np.full(n, -1, dtype=int)

    date_arr = all_dates.values
    X_all = merged[feat_cols].values
    X_fut_all = merged_fut[feat_cols].values
    valid_x = ~np.isnan(X_all).any(axis=1)
    valid_x_fut = ~np.isnan(X_fut_all).any(axis=1)
    y_frozen = df_raw["label"].values
    # Target como train.py fazia (1 se verde, 0 se vermelha OU empate)
    y_train_py_style = (y_frozen == 1).astype(int)

    for td in test_dates:
        test_idx = np.where(date_arr == td)[0]
        if len(test_idx) == 0:
            continue
        first_test = int(test_idx[0])
        train_end = first_test - PURGE_BARS  # purga/embargo de 200 velas
        train_start = max(0, train_end - TRAIN_DAYS * 1440)
        if train_end - train_start < 5000:
            continue

        tr_slice = np.arange(train_start, train_end)
        tr_valid = tr_slice[valid_x[tr_slice]]
        if len(tr_valid) < 5000:
            continue

        # B1: classe majoritária do treino entre verde (1) e vermelha (0)
        tr_labels = y_frozen[tr_valid]
        n_green = int((tr_labels == 1).sum())
        n_red = int((tr_labels == 0).sum())
        maj_class = 1 if n_green >= n_red else 0
        maj_baseline[test_idx] = maj_class

        te_valid = test_idx[valid_x[test_idx]]
        if len(te_valid) == 0:
            continue

        # 1) Modelo estilo train.py original (doji rotulado como 0 no treino)
        pipe_orig = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", HistGradientBoostingClassifier(
                max_iter=100, max_depth=4, learning_rate=0.04,
                min_samples_leaf=40, random_state=42
            ))
        ])
        pipe_orig.fit(X_all[tr_valid], y_train_py_style[tr_valid])
        preds_orig[te_valid] = pipe_orig.predict(X_all[te_valid]).astype(int)

        # 2) Mesmo modelo, mas excluindo empates do y_train (evita vício de prever doji=0)
        tr_clean = tr_valid[y_frozen[tr_valid] >= 0]
        pipe_clean = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", HistGradientBoostingClassifier(
                max_iter=100, max_depth=4, learning_rate=0.04,
                min_samples_leaf=40, random_state=42
            ))
        ])
        pipe_clean.fit(X_all[tr_clean], y_frozen[tr_clean])
        preds_clean[te_valid] = pipe_clean.predict(X_all[te_valid]).astype(int)

        # F1: mesmo modelo treinado e testado com features deslocadas 1 barra p/ futuro (inclui barra t)
        tr_fut = tr_slice[valid_x_fut[tr_slice] & (y_frozen[tr_slice] >= 0)]
        te_fut = test_idx[valid_x_fut[test_idx]]
        if len(tr_fut) >= 5000 and len(te_fut) > 0:
            pipe_fut = Pipeline([
                ("scaler", StandardScaler()),
                ("clf", HistGradientBoostingClassifier(
                    max_iter=100, max_depth=4, learning_rate=0.04,
                    min_samples_leaf=40, random_state=42
                ))
            ])
            pipe_fut.fit(X_fut_all[tr_fut], y_frozen[tr_fut])
            preds_f1_future[te_fut] = pipe_fut.predict(X_fut_all[te_fut]).astype(int)

    return preds_orig, preds_clean, preds_f1_future, maj_baseline


def falsification_shuffle_intraday(labels: np.ndarray, preds: np.ndarray, dates: np.ndarray, n_sims: int = 200, seed: int = 42) -> tuple[float, float]:
    """
    Teste F2: embaralha o label DENTRO de cada dia UTC preservando a margem diária (verdes/vermelhas/empates).
    Retorna (media_acc, std_acc) sobre n_sims simulações.
    """
    rng = np.random.default_rng(seed)
    sig_mask = preds >= 0
    if sig_mask.sum() == 0:
        return (0.0, 0.0)

    y_sig = labels[sig_mask]
    p_sig = preds[sig_mask]
    d_sig = dates[sig_mask]

    day_indices = [np.where(d_sig == d)[0] for d in np.unique(d_sig)]
    accs = np.empty(n_sims, dtype=float)
    for s in range(n_sims):
        y_shuf = y_sig.copy()
        for idx in day_indices:
            y_shuf[idx] = rng.permutation(y_sig[idx])
        accs[s] = (y_shuf == p_sig).mean()
    return float(accs.mean()), float(accs.std())


def fmt_row(name: str, m: dict) -> str:
    return (
        f"{name:<28} | N={m['n_sig']:>6} ({m['coverage']*100:5.1f}%) | "
        f"Acc={m['acc']*100:5.2f}% [{m['wilson_lo']*100:5.2f}%, {m['wilson_hi']*100:5.2f}%] | "
        f"ExTie={m['acc_ex_tie']*100:5.2f}% | WAcc={m['acc_weighted']*100:5.2f}% | "
        f"CALL={m['ret_call_bps']:+6.2f}bp PUT={m['ret_put_bps']:+6.2f}bp Dir={m['ret_dir_bps']:+6.2f}bp"
    )


def main():
    print("=" * 125)
    print("FASE 2 — REPRODUCAO DO BASELINE NO CODIGO ATUAL (SEM FEATURE NOVA)")
    print("=" * 125)

    if not DATA_PARQUET.exists():
        print(f"ERRO: Dataset nao encontrado: {DATA_PARQUET}")
        sys.exit(1)

    df_raw = compute_frozen_labels(pd.read_parquet(DATA_PARQUET))
    df_feat = pd.read_parquet(FEATURES_PARQUET)

    # Auditoria de datas e separação estrita do Holdout final (últimos 7 dias)
    df_raw["date"] = df_raw["open_time"].dt.date
    unique_dates = sorted(df_raw["date"].unique())

    holdout_dates = unique_dates[-HOLDOUT_DAYS:]
    dev_dates = unique_dates[:-HOLDOUT_DAYS]
    # Dias de teste no walk-forward dentro do dev (após 14 dias de treino + 1 dia p/ purga)
    wf_test_dates = dev_dates[TRAIN_DAYS + 1:]

    print(f"Ativo / Mercado congelado : Binance Spot BTCUSDT (M1)")
    print(f"Dataset total             : {len(df_raw)} velas | {df_raw['open_time'].iloc[0]} -> {df_raw['open_time'].iloc[-1]}")
    print(f"Distribuicao total        : Verde={(df_raw['label']==1).sum()} ({(df_raw['label']==1).mean()*100:.2f}%) | "
          f"Vermelha={(df_raw['label']==0).sum()} ({(df_raw['label']==0).mean()*100:.2f}%) | "
          f"Empate={(df_raw['label']==-1).sum()} ({(df_raw['label']==-1).mean()*100:.2f}%)")
    print(f"Janela Dev (WF)           : {dev_dates[0]} -> {dev_dates[-1]} ({len(dev_dates)} dias; {len(wf_test_dates)} dias de teste OOS)")
    print(f"HOLDOUT RESERVADO (FECHADO): {holdout_dates[0]} -> {holdout_dates[-1]} ({len(holdout_dates)} dias — NAO ABERTO NESTA FASE)")
    print(f"Protocolo WF              : Treino={TRAIN_DAYS}d | Purga={PURGE_BARS} velas | Teste=1d | Passo=1d")
    print("-" * 125)

    # 1. Reprodução exata do motor ativo (engine/confluences.py)
    preds_conf = compute_confluences_vectorized(df_raw)
    exact_full, exact_ws200 = verify_confluences_exact_match(df_raw, preds_conf, n_checks=200)
    print(f"Verificacao vetorizacao confluences.py (200 amostras): "
          f"match janela 1000={exact_full}/200 ({exact_full/2:.1f}%) | match janela WS 200={exact_ws200}/200 ({exact_ws200/2:.1f}%)")

    # 2. Baselines ingênuos B2, B3, B4
    prev_label = np.full(len(df_raw), -1, dtype=int)
    prev_label[1:] = df_raw["label"].values[:-1]
    preds_b2_prev = prev_label.copy()
    preds_b3_contra = np.where(prev_label == 1, 0, np.where(prev_label == 0, 1, -1))
    preds_b4_5m = build_last_closed_5m_signal(df_raw)

    # 3. Modelo ML de train.py em walk-forward + Baseline B1 (majoritária do treino)
    print("Rodando walk-forward do HistGradientBoostingClassifier (train.py) nos dias OOS de desenvolvimento...")
    preds_ml_orig, preds_ml_clean, preds_ml_f1, preds_b1_maj = run_walk_forward_train_py_model(
        df_raw, df_feat, wf_test_dates, df_raw["date"]
    )

    # Máscara exata da janela OOS de desenvolvimento (comum a TODOS os modelos e baselines)
    oos_mask = df_raw["date"].isin(wf_test_dates).values
    y_oos = df_raw["label"].values[oos_mask]
    w_oos = df_raw["body_abs"].values[oos_mask]
    r_oos = df_raw["candle_ret"].values[oos_mask]
    d_oos = df_raw["date"].values[oos_mask]
    t_oos = df_raw["open_time"].values[oos_mask]

    print(f"\nJanela OOS Comum: N={len(y_oos)} velas | "
          f"Verde={(y_oos==1).mean()*100:.2f}% | Vermelha={(y_oos==0).mean()*100:.2f}% | Empate={(y_oos==-1).mean()*100:.2f}%")
    print("=" * 125)
    print("TABELA 1 — COMPARACAO GLOBAL NA JANELA OOS DE DESENVOLVIMENTO (PRODUTO A, EMPATE = ERRO)")
    print("=" * 125)

    models = [
        ("B1_majoritaria_treino", preds_b1_maj[oos_mask]),
        ("B2_vela_anterior", preds_b2_prev[oos_mask]),
        ("B3_contrario_vela_ant", preds_b3_contra[oos_mask]),
        ("B4_ultima_5m_fechada", preds_b4_5m[oos_mask]),
        ("ATUAL_confluences_py", preds_conf[oos_mask]),
        ("ATUAL_train_py_doji0", preds_ml_orig[oos_mask]),
        ("ATUAL_train_py_sem_doji_tr", preds_ml_clean[oos_mask]),
    ]

    metrics_map = {}
    for name, p in models:
        m = evaluate_predictions(y_oos, p, w_oos, r_oos)
        metrics_map[name] = m
        print(fmt_row(name, m))

    # Melhor baseline
    bl_names = ["B1_majoritaria_treino", "B2_vela_anterior", "B3_contrario_vela_ant", "B4_ultima_5m_fechada"]
    best_bl_name = max(bl_names, key=lambda k: metrics_map[k]["acc"])
    best_bl = metrics_map[best_bl_name]
    print("-" * 125)
    print(f"Melhor baseline ingênuo (acuracia crua): {best_bl_name} = {best_bl['acc']*100:.2f}% "
          f"(WAcc={best_bl['acc_weighted']*100:.2f}%, Dir={best_bl['ret_dir_bps']:+.2f}bp)")
    for name in ["ATUAL_confluences_py", "ATUAL_train_py_doji0", "ATUAL_train_py_sem_doji_tr"]:
        m = metrics_map[name]
        lift_pp = (m["acc"] - best_bl["acc"]) * 100
        wlift_pp = (m["acc_weighted"] - best_bl["acc_weighted"]) * 100
        wilson_beats = m["wilson_lo"] > best_bl["acc"]
        print(f"  Lift {name:<26}: {lift_pp:+.2f} p.p. (WAcc lift: {wlift_pp:+.2f} p.p.) | Wilson95 > best_bl? {wilson_beats}")

    # Tabela por semana (blocos de 7 dias dentro do OOS)
    print("\n" + "=" * 125)
    print("TABELA 2 — QUEBRA SEMANAL (Janelas Walk-Forward de 7 dias no OOS)")
    print("=" * 125)
    oos_dates_sorted = sorted(np.unique(d_oos))
    weeks = [oos_dates_sorted[i:i + 7] for i in range(0, len(oos_dates_sorted), 7)]
    for wi, wdays in enumerate(weeks, start=1):
        wmask = np.isin(d_oos, wdays)
        print(f"\n--- Semana {wi}: {wdays[0]} a {wdays[-1]} (N={int(wmask.sum())}, Empates={(y_oos[wmask]==-1).mean()*100:.2f}%) ---")
        for name, p in models:
            m = evaluate_predictions(y_oos[wmask], p[wmask], w_oos[wmask], r_oos[wmask])
            print("  " + fmt_row(name, m))

    # Testes de falsificação obrigatórios
    print("\n" + "=" * 125)
    print("TABELA 3 — TESTES DE FALSIFICACAO (OBRIGATORIOS)")
    print("=" * 125)

    # F1a: Feature deslocada 1 barra para o futuro (usa barra t para prever t)
    # Para confluences.py: usar pred_at_close_i SEM shift(+1) -> usa dados até t para prever t
    preds_conf_f1 = np.full(len(df_raw), -1, dtype=int)
    preds_conf_f1[:-1] = preds_conf[1:]  # desloca sinal 1 barra p/ trás = feature 1 barra p/ frente
    m_conf_f1 = evaluate_predictions(y_oos, preds_conf_f1[oos_mask], w_oos, r_oos)
    m_ml_f1 = evaluate_predictions(y_oos, preds_ml_f1[oos_mask], w_oos, r_oos)

    # F3: Atrasar sinal em 1 vela (aplicar previsão de t na vela t+1)
    preds_conf_f3 = np.full(len(df_raw), -1, dtype=int)
    preds_conf_f3[1:] = preds_conf[:-1]
    preds_ml_clean_f3 = np.full(len(df_raw), -1, dtype=int)
    preds_ml_clean_f3[1:] = preds_ml_clean[:-1]
    preds_ml_orig_f3 = np.full(len(df_raw), -1, dtype=int)
    preds_ml_orig_f3[1:] = preds_ml_orig[:-1]

    m_conf_f3 = evaluate_predictions(y_oos, preds_conf_f3[oos_mask], w_oos, r_oos)
    m_ml_orig_f3 = evaluate_predictions(y_oos, preds_ml_orig_f3[oos_mask], w_oos, r_oos)
    m_ml_clean_f3 = evaluate_predictions(y_oos, preds_ml_clean_f3[oos_mask], w_oos, r_oos)

    # F2: Embaralhar label dentro do dia (200 sims)
    f2_conf_mean, f2_conf_std = falsification_shuffle_intraday(y_oos, preds_conf[oos_mask], d_oos, n_sims=200)
    f2_ml_orig_mean, f2_ml_orig_std = falsification_shuffle_intraday(y_oos, preds_ml_orig[oos_mask], d_oos, n_sims=200)
    f2_ml_clean_mean, f2_ml_clean_std = falsification_shuffle_intraday(y_oos, preds_ml_clean[oos_mask], d_oos, n_sims=200)

    print("F1 — Feature deslocada 1 barra p/ o futuro (inclui barra t -> sonda de causalidade):")
    print("  " + fmt_row("F1_confluences_futuro", m_conf_f1))
    print("  " + fmt_row("F1_train_py_futuro", m_ml_f1))

    print("\nF2 — Label embaralhado dentro do dia UTC (200 permutacoes, margem preservada):")
    print(f"  F2_confluences_py          : Acc={f2_conf_mean*100:.2f}% +/- {f2_conf_std*100:.2f}%")
    print(f"  F2_train_py_doji0          : Acc={f2_ml_orig_mean*100:.2f}% +/- {f2_ml_orig_std*100:.2f}%")
    print(f"  F2_train_py_sem_doji_tr    : Acc={f2_ml_clean_mean*100:.2f}% +/- {f2_ml_clean_std*100:.2f}%")

    print("\nF3 — Sinal atrasado em 1 vela (previsao de t aplicada em t+1):")
    print("  " + fmt_row("F3_confluences_delay1", m_conf_f3))
    print("  " + fmt_row("F3_train_py_doji0_delay1", m_ml_orig_f3))
    print("  " + fmt_row("F3_train_py_clean_delay1", m_ml_clean_f3))

    # Diagnóstico adicional: quantas vezes cada modelo previu CALL vs PUT
    print("\n" + "=" * 125)
    print("DIAGNOSTICO DE DISTRIBUICAO DE PREVISOES NO OOS")
    print("=" * 125)
    for name, p in models:
        sig = p[p >= 0]
        if len(sig) > 0:
            pct_call = (sig == 1).mean() * 100
            print(f"  {name:<28}: CALL={pct_call:5.2f}% | PUT={100-pct_call:5.2f}%")


if __name__ == "__main__":
    main()
