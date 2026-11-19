"""
Auditoria Quantitativa do Esquema Oficial (blok_shema) do va-mod.ru:
- Filtro de Tendencia (👍 alinhado com tendencia vs ❓ contra tendencia)
- Entrada NAO e no :00s, mas no RETESTE (pullback) no preco exato do POC da vela anterior
- Avaliacao honesta: vitoria no PRECO DE ENTRADA (close_t > POC para CALL / close_t < POC para PUT),
  vitoria na COR DA VELA, retorno em bps a partir do POC, e controle contra nivel dummy na mesma distancia!
"""
import math
import os
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = r"D:\backup importantes\ficar-rico\btc-proxima-vela"
DATA_1S = r"C:\trading-agents\btc_color_research\data\BTCUSDT_1s_2d.parquet"
DATA_1M_65D = r"C:\trading-agents\btc_color_research\data\BTCUSDT_1m_65d.parquet"
DATA_1M_45D = os.path.join(ROOT, "audit_data", "BTCUSDT_spot_1m_virgin45d.parquet")


def wilson_ci(k: int, n: int, z: float = 1.959963984540054):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    half = (z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def eval_entries(name: str, side: np.ndarray, entry_px: np.ndarray,
                 open_t: np.ndarray, close_t: np.ndarray, total_candles: int):
    """
    side: +1 (CALL preenchido), -1 (PUT preenchido), 0 (nao ativou)
    """
    m = side != 0
    n = int(m.sum())
    if n == 0:
        return dict(name=name, n=0, cov=0.0, acc_entry=0.0, w_lo=0.0, w_hi=0.0,
                    acc_color=0.0, bps=0.0, dist_bps=0.0)
    s = side[m]
    e = entry_px[m]
    o = open_t[m]
    c = close_t[m]

    # Vitoria no preco de entrada (Opcoes Binarias) - empate = ERRO
    win_entry = np.where(s == 1, c > e, c < e)
    k = int(win_entry.sum())
    acc_e = k / n
    w_lo, w_hi = wilson_ci(k, n)

    # Vitoria na cor da vela (so como referencia)
    win_color = np.where(s == 1, c > o, c < o)
    acc_c = float(win_color.mean())

    # Retorno bruto a partir do preco de entrada
    ret_bps = s * ((c - e) / e) * 10000.0
    # Distancia favoravel do pullback (quanto melhor que o open foi a entrada)
    dist_bps = s * ((o - e) / o) * 10000.0

    return dict(
        name=name, n=n, cov=n / total_candles, acc_entry=acc_e,
        w_lo=w_lo, w_hi=w_hi, acc_color=acc_c,
        bps=float(ret_bps.mean()), dist_bps=float(dist_bps.mean())
    )


def print_table(title: str, rows: list):
    print(f"\n{'='*116}")
    print(f" {title}")
    print(f"{'='*116}")
    hdr = (f"{'Setup (Reteste no POC)':40s} | {'N':>6s} | {'Fill%':>5s} | "
           f"{'WinEntry':>8s} | {'Wilson 95%':>15s} | {'WinCor':>6s} | {'BpsEnt':>7s} | {'DistPB':>6s}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        ci = f"[{r['w_lo']*100:4.1f}%, {r['w_hi']*100:4.1f}%]"
        flag = " **>55.6%" if (r["n"] >= 30 and r["w_lo"] > 0.556) else (" *>50%" if (r["n"] >= 30 and r["w_lo"] > 0.50) else "")
        print(
            f"{r['name']:40s} | {r['n']:6d} | {r['cov']*100:4.1f}% | "
            f"{r['acc_entry']*100:7.2f}% | {ci:>15s} | {r['acc_color']*100:5.2f}% | "
            f"{r['bps']:+7.2f} | {r['dist_bps']:+5.2f}b{flag}"
        )


def compute_poc_price(prices: np.ndarray, weights: np.ndarray, bin_size: float):
    binned = np.floor(prices / bin_size) * bin_size
    uniq, inv = np.unique(binned, return_inverse=True)
    counts = np.bincount(inv, weights=weights) if weights is not None else np.bincount(inv)
    uniq = uniq[::-1]
    counts = counts[::-1]
    max_v = counts.max()
    idxs = np.where(counts == max_v)[0]
    idx = int(idxs[len(idxs) // 2])
    poc = float(uniq[idxs].mean()) + bin_size / 2.0
    pct_from_top = idx * 100.0 / len(uniq) if len(uniq) > 0 else 50.0
    return poc, pct_from_top


# ==============================================================================
# BLOCO 1: BINANCE 1s (2 DIAS, ~2.880 M1) — SIMULACAO SEGUNDO A SEGUNDO EXATA
# ==============================================================================
def run_1s_retest():
    df1s = pd.read_parquet(DATA_1S)
    df1s["minute"] = df1s["open_time"].dt.floor("min")
    df1s["sec"] = df1s["open_time"].dt.second
    grp_sizes = df1s.groupby("minute").size()
    valid_mins = grp_sizes[grp_sizes >= 55].index
    df1s = df1s[df1s["minute"].isin(valid_mins)].copy()

    # Montar tabela por minuto com arrays 1s de high/low nos primeiros 30s e 59s
    recs = []
    for minute, g in df1s.groupby("minute", sort=True):
        o = float(g["open"].iloc[0])
        h = float(g["high"].max())
        l = float(g["low"].min())
        c = float(g["close"].iloc[-1])
        closes = g["close"].to_numpy(dtype=np.float64)
        vols = g["volume"].to_numpy(dtype=np.float64)
        secs = g["sec"].to_numpy()
        highs = g["high"].to_numpy(dtype=np.float64)
        lows = g["low"].to_numpy(dtype=np.float64)

        vtot = vols.sum()
        vwap = float((closes * vols).sum() / vtot) if vtot > 0 else c
        poc_vol5, pct_vol5 = compute_poc_price(closes, vols, 5.0)
        poc_tpo5, pct_tpo5 = compute_poc_price(closes, None, 5.0)

        m30 = secs < 30
        m45 = secs < 45
        recs.append({
            "minute": minute, "open": o, "high": h, "low": l, "close": c,
            "vwap": vwap, "poc_vol5": poc_vol5, "pct_vol5": pct_vol5,
            "poc_tpo5": poc_tpo5, "pct_tpo5": pct_tpo5,
            "h_30s": float(highs[m30].max()) if m30.any() else o,
            "l_30s": float(lows[m30].min()) if m30.any() else o,
            "h_45s": float(highs[m45].max()) if m45.any() else o,
            "l_45s": float(lows[m45].min()) if m45.any() else o,
        })

    m1 = pd.DataFrame(recs).sort_values("minute").reset_index(drop=True)
    # Tendencias estritamente causais (calculadas ate t-1)
    m1["ema9"] = m1["close"].ewm(span=9, adjust=False).mean()
    m1["ema21"] = m1["close"].ewm(span=21, adjust=False).mean()
    # Tendencia 3 velas (como as 3 setinhas na imagem!)
    m1["c_up"] = (m1["close"] > m1["open"]).astype(int)
    m1["c_dn"] = (m1["close"] < m1["open"]).astype(int)
    m1["sum3_up"] = m1["c_up"].rolling(3).sum()
    m1["sum3_dn"] = m1["c_dn"].rolling(3).sum()
    m1["ret3"] = m1["close"] - m1["open"].shift(2)

    dt_ok = (m1["minute"].diff().dt.total_seconds() == 60.0) & (m1.index >= 25)
    prev = m1.shift(1).loc[dt_ok].reset_index(drop=True)
    curr = m1.loc[dt_ok].reset_index(drop=True)
    N = len(curr)

    o_t = curr["open"].to_numpy()
    c_t = curr["close"].to_numpy()
    h_30 = curr["h_30s"].to_numpy()
    l_30 = curr["l_30s"].to_numpy()
    h_45 = curr["h_45s"].to_numpy()
    l_45 = curr["l_45s"].to_numpy()
    h_60 = curr["high"].to_numpy()
    l_60 = curr["low"].to_numpy()

    c_p = prev["close"].to_numpy()
    # Tendencia 1: EMA9 > EMA21 (tendencia classica)
    trend_ema = np.where(prev["ema9"] > prev["ema21"], 1, -1)
    # Tendencia 2: 3 velas na mesma direcao (exato 3 setinhas!)
    trend_3v = np.where(prev["sum3_up"] == 3, 1, np.where(prev["sum3_dn"] == 3, -1, 0))
    # Tendencia 3: retorno das ultimas 3 velas
    trend_ret3 = np.where(prev["ret3"] > 0, 1, np.where(prev["ret3"] < 0, -1, 0))

    def test_retest(label, poc_arr, pct_arr, trend_arr, h_win, l_win, mode="thumbs_up"):
        """
        mode:
          'all'       = qualquer sinal close vs POC (com ou sem tendencia)
          'thumbs_up' = 👍 alinhado com a tendencia (Colunas 1 e 3)
          'question'  = ❓ contra a tendencia (Colunas 2 e 4)
          'col1'      = 👍 Coluna 1 (impulso: POC na metade final, mas fechou alem)
          'col3'      = 👍 Coluna 3 (base: POC na metade inicial, fechou bem alem)
        """
        sig = np.where(c_p > poc_arr, 1, np.where(c_p < poc_arr, -1, 0))
        if mode == "thumbs_up":
            sig = np.where((sig != 0) & (sig == trend_arr), sig, 0)
        elif mode == "question":
            sig = np.where((sig != 0) & (sig == -trend_arr), sig, 0)
        elif mode == "col1":
            # CALL: POC no topo (pct <= 50) & close > POC; PUT: POC no fundo (pct >= 50) & close < POC
            ok = np.where(sig == 1, pct_arr <= 50, pct_arr >= 50)
            sig = np.where((sig != 0) & (sig == trend_arr) & ok, sig, 0)
        elif mode == "col3":
            # CALL: POC no fundo (pct > 50) & close > POC; PUT: POC no topo (pct < 50) & close < POC
            ok = np.where(sig == 1, pct_arr > 50, pct_arr < 50)
            sig = np.where((sig != 0) & (sig == trend_arr) & ok, sig, 0)

        # Verifica se o preco de abertura esta do lado certo e se a vela tocou o POC na janela!
        # CALL: open_t > poc e l_win <= poc -> preenchido no poc
        # PUT:  open_t < poc e h_win >= poc -> preenchido no poc
        fill_call = (sig == 1) & (o_t > poc_arr) & (l_win <= poc_arr)
        fill_put = (sig == -1) & (o_t < poc_arr) & (h_win >= poc_arr)
        side = np.where(fill_call, 1, np.where(fill_put, -1, 0))
        return eval_entries(label, side, poc_arr, o_t, c_t, N)

    rows = []
    poc_v = prev["poc_vol5"].to_numpy()
    pct_v = prev["pct_vol5"].to_numpy()
    poc_t = prev["poc_tpo5"].to_numpy()
    pct_t = prev["pct_tpo5"].to_numpy()
    vwap_p = prev["vwap"].to_numpy()

    # Janela realista de Opcoes Binarias M1: toque nos primeiros 30 segundos!
    rows.append(test_retest("1s_M1 [0-30s] VOL_POC Todos", poc_v, pct_v, trend_ema, h_30, l_30, "all"))
    rows.append(test_retest("1s_M1 [0-30s] VOL_POC 👍 (EMA9/21)", poc_v, pct_v, trend_ema, h_30, l_30, "thumbs_up"))
    rows.append(test_retest("1s_M1 [0-30s] VOL_POC ❓ (Contra EMA)", poc_v, pct_v, trend_ema, h_30, l_30, "question"))
    rows.append(test_retest("1s_M1 [0-30s] VOL_POC 👍 Col1 (Impulso)", poc_v, pct_v, trend_ema, h_30, l_30, "col1"))
    rows.append(test_retest("1s_M1 [0-30s] VOL_POC 👍 Col3 (Base)", poc_v, pct_v, trend_ema, h_30, l_30, "col3"))
    rows.append(test_retest("1s_M1 [0-30s] VOL_POC 👍 (3 Velas=)", poc_v, pct_v, trend_3v, h_30, l_30, "thumbs_up"))
    rows.append(test_retest("1s_M1 [0-30s] VOL_POC 👍 (Ret 3v)", poc_v, pct_v, trend_ret3, h_30, l_30, "thumbs_up"))
    rows.append(test_retest("1s_M1 [0-30s] TPO_va-mod 👍 (EMA)", poc_t, pct_t, trend_ema, h_30, l_30, "thumbs_up"))
    rows.append(test_retest("1s_M1 [0-30s] VWAP 👍 (EMA)", vwap_p, pct_v, trend_ema, h_30, l_30, "thumbs_up"))
    rows.append(test_retest("1s_M1 [0-45s] VOL_POC 👍 (EMA)", poc_v, pct_v, trend_ema, h_45, l_45, "thumbs_up"))
    rows.append(test_retest("1s_M1 [0-59s] VOL_POC 👍 (EMA)", poc_v, pct_v, trend_ema, h_60, l_60, "thumbs_up"))

    # CONTROLE ANTI-ILUSAO: Pullback cego na MESMA distancia mediana do POC (sem usar POC nenhum!)
    med_dist = float(np.median(np.abs(c_p - poc_v) / c_p))
    dummy_lvl = np.where(trend_ema == 1, o_t * (1.0 - med_dist), o_t * (1.0 + med_dist))
    fill_dc = (trend_ema == 1) & (l_30 <= dummy_lvl)
    fill_dp = (trend_ema == -1) & (h_30 >= dummy_lvl)
    rows.append(eval_entries(
        f"CONTROLE_CEGO [0-30s] PB fixo {med_dist*10000:.1f}bp",
        np.where(fill_dc, 1, np.where(fill_dp, -1, 0)), dummy_lvl, o_t, c_t, N
    ))

    # E se o pullback cego entrar CONTRA a tendencia (reversao)?
    # Ja temos o ❓ acima, mas vamos testar tambem entrar AO CONTRARIO do reteste do POC (rompimento do POC!)
    sig_tu = np.where((c_p > poc_v) & (trend_ema == 1), 1,
             np.where((c_p < poc_v) & (trend_ema == -1), -1, 0))
    fill_c = (sig_tu == 1) & (o_t > poc_v) & (l_30 <= poc_v)
    fill_p = (sig_tu == -1) & (o_t < poc_v) & (h_30 >= poc_v)
    # Invertido: se tocou no POC, aposta que vai ROMPER o POC em vez de segurar!
    rows.append(eval_entries(
        "INVERTIDO [0-30s] Rompe POC 👍 (EMA)",
        np.where(fill_c, -1, np.where(fill_p, 1, 0)), poc_v, o_t, c_t, N
    ))

    print_table(f"BLOCO 1 — ESQUEMA DA IMAGEM EM M1 (BINANCE 1s, N={N} velas, 2 dias)", rows)


# ==============================================================================
# BLOCO 2: 110 DIAS COMPLETOS — M5 E M15 (COM SUB-VELAS M1 REAIS!) — DEV vs HOLDOUT
# ==============================================================================
def run_110d_m5_m15_retest():
    df_a = pd.read_parquet(DATA_1M_45D)
    df_b = pd.read_parquet(DATA_1M_65D)
    df = pd.concat([df_a, df_b], ignore_index=True)
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    df["vwap"] = np.where(df["volume"] > 0, df["quote_asset_volume"] / df["volume"], df["close"])
    split_ts = df["open_time"].max() - pd.Timedelta(days=30)

    for tf_min, entry_mins in [(5, 3), (15, 10)]:
        df["bucket"] = df["open_time"].dt.floor(f"{tf_min}min")
        df["sub_idx"] = ((df["open_time"] - df["bucket"]).dt.total_seconds() // 60).astype(int)

        g_size = df.groupby("bucket").size()
        full_b = g_size[g_size == tf_min].index
        sub = df[df["bucket"].isin(full_b)].copy()

        # POC real da vela M5/M15 = VWAP do minuto de maior volume real
        idx_max = sub.groupby("bucket")["volume"].idxmax()
        poc_df = sub.loc[idx_max, ["bucket", "vwap"]].set_index("bucket")
        poc_df.columns = ["poc_price"]

        # High/Low na janela inicial de entrada (ex: primeiros 3 min de M5, ou 10 min de M15)
        sub_win = sub[sub["sub_idx"] < entry_mins].groupby("bucket").agg(
            h_win=("high", "max"), l_win=("low", "min")
        )
        agg = sub.groupby("bucket").agg(
            open=("open", "first"), high=("high", "max"), low=("low", "min"),
            close=("close", "last"), volume=("volume", "sum"), qvol=("quote_asset_volume", "sum")
        )
        agg["vwap"] = np.where(agg["volume"] > 0, agg["qvol"] / agg["volume"], agg["close"])
        agg = agg.join(poc_df).join(sub_win).reset_index().sort_values("bucket").reset_index(drop=True)

        agg["ema9"] = agg["close"].ewm(span=9, adjust=False).mean()
        agg["ema21"] = agg["close"].ewm(span=21, adjust=False).mean()
        agg["c_up"] = (agg["close"] > agg["open"]).astype(int)
        agg["c_dn"] = (agg["close"] < agg["open"]).astype(int)
        agg["sum3_up"] = agg["c_up"].rolling(3).sum()
        agg["sum3_dn"] = agg["c_dn"].rolling(3).sum()
        rng = (agg["high"] - agg["low"]).replace(0, np.nan)
        agg["pct_poc"] = ((agg["high"] - agg["poc_price"]) / rng * 100.0).fillna(50.0)

        dt_ok = (agg["bucket"].diff().dt.total_seconds() == tf_min * 60.0) & (agg.index >= 25)
        prev = agg.shift(1)

        for lbl, m_split in [("DEV (80d)", agg["bucket"] < split_ts),
                             ("HOLDOUT (30d)", agg["bucket"] >= split_ts)]:
            m = dt_ok & m_split
            curr_s = agg.loc[m].reset_index(drop=True)
            prev_s = prev.loc[m].reset_index(drop=True)
            N = len(curr_s)

            o_t = curr_s["open"].to_numpy()
            c_t = curr_s["close"].to_numpy()
            h_w = curr_s["h_win"].to_numpy()
            l_w = curr_s["l_win"].to_numpy()
            c_p = prev_s["close"].to_numpy()
            poc_p = prev_s["poc_price"].to_numpy()
            vwap_p = prev_s["vwap"].to_numpy()
            pct_p = prev_s["pct_poc"].to_numpy()
            tr_ema = np.where(prev_s["ema9"] > prev_s["ema21"], 1, -1)
            tr_3v = np.where(prev_s["sum3_up"] == 3, 1, np.where(prev_s["sum3_dn"] == 3, -1, 0))

            def run_mode(name, lvl, tr, mode):
                sig = np.where(c_p > lvl, 1, np.where(c_p < lvl, -1, 0))
                if mode == "thumbs_up":
                    sig = np.where((sig != 0) & (sig == tr), sig, 0)
                elif mode == "question":
                    sig = np.where((sig != 0) & (sig == -tr), sig, 0)
                elif mode == "col1":
                    ok = np.where(sig == 1, pct_p <= 50, pct_p >= 50)
                    sig = np.where((sig != 0) & (sig == tr) & ok, sig, 0)
                elif mode == "col3":
                    ok = np.where(sig == 1, pct_p > 50, pct_p < 50)
                    sig = np.where((sig != 0) & (sig == tr) & ok, sig, 0)
                fc = (sig == 1) & (o_t > lvl) & (l_w <= lvl)
                fp = (sig == -1) & (o_t < lvl) & (h_w >= lvl)
                return eval_entries(name, np.where(fc, 1, np.where(fp, -1, 0)), lvl, o_t, c_t, N)

            rows = [
                run_mode(f"M{tf_min} [0-{entry_mins}m] POC Todos", poc_p, tr_ema, "all"),
                run_mode(f"M{tf_min} [0-{entry_mins}m] POC 👍 (EMA9/21)", poc_p, tr_ema, "thumbs_up"),
                run_mode(f"M{tf_min} [0-{entry_mins}m] POC ❓ (Contra EMA)", poc_p, tr_ema, "question"),
                run_mode(f"M{tf_min} [0-{entry_mins}m] POC 👍 Col1 (Impulso)", poc_p, tr_ema, "col1"),
                run_mode(f"M{tf_min} [0-{entry_mins}m] POC 👍 Col3 (Base)", poc_p, tr_ema, "col3"),
                run_mode(f"M{tf_min} [0-{entry_mins}m] POC 👍 (3 Velas=)", poc_p, tr_3v, "thumbs_up"),
                run_mode(f"M{tf_min} [0-{entry_mins}m] VWAP 👍 (EMA9/21)", vwap_p, tr_ema, "thumbs_up"),
            ]
            # Controle cego na mesma distancia mediana
            med_d = float(np.median(np.abs(c_p - poc_p) / c_p))
            dlvl = np.where(tr_ema == 1, o_t * (1.0 - med_d), o_t * (1.0 + med_d))
            fc = (tr_ema == 1) & (l_w <= dlvl)
            fp = (tr_ema == -1) & (h_w >= dlvl)
            rows.append(eval_entries(
                f"CONTROLE_CEGO M{tf_min} PB fixo {med_d*10000:.1f}bp",
                np.where(fc, 1, np.where(fp, -1, 0)), dlvl, o_t, c_t, N
            ))

            print_table(f"BLOCO 2 — ESQUEMA DA IMAGEM EM M{tf_min} [{lbl}] (N={N}, entrada 0-{entry_mins}m)", rows)


if __name__ == "__main__":
    run_1s_retest()
    run_110d_m5_m15_retest()
