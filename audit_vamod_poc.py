"""
Auditoria Quantitativa Rigorosa — Formula do va-mod.ru (Close vs POC) + Volume Real / Footprint Binance
Segue protocolo prompat.txt:
- Produto A estritamente causal no :00s (usa apenas vela fechada t-1 para prever vela t)
- Target: 1 se close_t > open_t, 0 se close_t < open_t; empate (close_t == open_t) contado como ERRO
- Metricas obrigatorias: N, Cobertura, Acc, Wilson 95% [lo, hi], Delta vs Base, WAcc (body-weighted), Retorno bruto em bps
- Comparacao direta contra baseline ingenuo B2 (cor da vela anterior)
"""
import math
import os
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = r"D:\backup importantes\ficar-rico\btc-proxima-vela"
DATA_1S = r"C:\trading-agents\btc_color_research\data\BTCUSDT_1s_2d.parquet"
DATA_AGG = r"C:\trading-agents\btc_color_research\data\BTCUSDT_aggTrades_6h.parquet"
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


def eval_signal(name: str, preds: np.ndarray, opens: np.ndarray, closes: np.ndarray,
                invert: bool = False):
    """
    preds: +1 (CALL), -1 (PUT), 0 (WAIT/neutral)
    Target real: +1 se close > open, -1 se close < open, 0 se doji (sempre ERRO quando opera)
    """
    p = -preds if invert else preds
    mask = p != 0
    n_tot = len(p)
    n = int(mask.sum())
    if n == 0:
        return {
            "name": name, "n": 0, "cov": 0.0, "acc": 0.0,
            "w_lo": 0.0, "w_hi": 0.0, "wacc": 0.0, "bps": 0.0, "call_pct": 0.0
        }
    p_m = p[mask]
    o_m = opens[mask]
    c_m = closes[mask]

    y = np.where(c_m > o_m, 1, np.where(c_m < o_m, -1, 0))
    hits = (p_m == y)
    k = int(hits.sum())
    acc = k / n
    w_lo, w_hi = wilson_ci(k, n)

    ret_bps = p_m * ((c_m - o_m) / o_m) * 10000.0
    body_abs = np.abs(c_m - o_m) / o_m * 10000.0
    w_sum = body_abs.sum()
    wacc = float((hits * body_abs).sum() / w_sum) if w_sum > 0 else 0.0
    mean_bps = float(ret_bps.mean())
    call_pct = float((p_m == 1).mean())

    return {
        "name": name, "n": n, "cov": n / n_tot, "acc": acc,
        "w_lo": w_lo, "w_hi": w_hi, "wacc": wacc, "bps": mean_bps, "call_pct": call_pct
    }


def print_table(title: str, rows: list, base_acc: float = None):
    print(f"\n{'='*108}")
    print(f" {title}")
    print(f"{'='*108}")
    hdr = f"{'Regra':38s} | {'N':>6s} | {'Cov':>5s} | {'Acc':>6s} | {'Wilson 95%':>15s} | {'WAcc':>6s} | {'Bps':>6s} | {'%CALL':>5s}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        ci = f"[{r['w_lo']*100:4.1f}%, {r['w_hi']*100:4.1f}%]"
        flag = ""
        if r["n"] >= 30 and r["w_lo"] > 0.50:
            flag = " *>"
        if base_acc is not None and r["n"] >= 30 and r["w_lo"] > base_acc:
            flag = " **>B"
        print(
            f"{r['name']:38s} | {r['n']:6d} | {r['cov']*100:4.1f}% | "
            f"{r['acc']*100:5.2f}% | {ci:>15s} | {r['wacc']*100:5.2f}% | "
            f"{r['bps']:+6.2f} | {r['call_pct']*100:4.1f}%{flag}"
        )


def compute_vamod_poc(prices: np.ndarray, weights: np.ndarray = None,
                      bin_size: float = 0.0, tie_lowest: bool = True):
    """
    Replica exatamente o algoritmo de POC do va-mod.ru (rish):
    - Opcionalmente arredonda/trunca para bin_size
    - Ordena niveis unicos de cima (max) para baixo (min)
    - Encontra max_price com vol >= max_vol (se tie_lowest=True, igual va-mod.ru)
      ou vol > max_vol (empate vai pro maior preco) ou media dos empatados (tie_lowest=False -> media)
    Retorna: (max_price, max_vol, raw_pct_from_top, n_levels)
    """
    if bin_size > 0:
        binned = np.floor(prices / bin_size) * bin_size
    else:
        binned = prices

    if weights is None:
        uniq, counts = np.unique(binned, return_counts=True)
    else:
        # soma pesos por preco unico
        uniq, inv = np.unique(binned, return_inverse=True)
        counts = np.bincount(inv, weights=weights)

    # ordenar decrescente (index 0 = High, index N-1 = Low) igual va-mod.ru
    uniq = uniq[::-1]
    counts = counts[::-1]
    n_lvls = len(uniq)
    max_vol = counts.max()
    if tie_lowest:
        # >= iterando de cima pra baixo => ultimo indice com max_vol (menor preco)
        idxs = np.where(counts == max_vol)[0]
        idx = int(idxs[-1])
        max_price = float(uniq[idx])
    else:
        idxs = np.where(counts == max_vol)[0]
        idx = int(idxs[len(idxs) // 2])
        max_price = float(uniq[idxs].mean())

    raw_pct = round(idx * 100.0 / n_lvls) if n_lvls > 0 else 50.0
    return max_price, float(max_vol), raw_pct, n_lvls


# ==============================================================================
# BLOCO 1: BINANCE 1s KLINES (2 DIAS, ~2.880 VELAS M1)
# ==============================================================================
def run_block_1_1s():
    df1s = pd.read_parquet(DATA_1S)
    df1s["minute"] = df1s["open_time"].dt.floor("min")
    # remover primeiro e ultimo minuto possivelmente incompletos (<55s)
    grp_sizes = df1s.groupby("minute").size()
    valid_mins = grp_sizes[grp_sizes >= 55].index
    df1s = df1s[df1s["minute"].isin(valid_mins)].copy()

    # construir features por minuto (vela t-1 -> prever vela t)
    records = []
    for minute, g in df1s.groupby("minute", sort=True):
        o = float(g["open"].iloc[0])
        h = float(g["high"].max())
        l = float(g["low"].min())
        c = float(g["close"].iloc[-1])
        vol = float(g["volume"].sum())
        tb = float(g["taker_buy_base"].sum())
        delta = 2.0 * tb - vol
        closes_1s = g["close"].to_numpy(dtype=np.float64)
        vols_1s = g["volume"].to_numpy(dtype=np.float64)
        vwap = float((closes_1s * vols_1s).sum() / vol) if vol > 0 else c

        # Filtro de ticks repetidos consecutivo igual va-mod.ru: quote !== previousClose
        dedup_mask = np.ones(len(closes_1s), dtype=bool)
        if len(closes_1s) > 1:
            dedup_mask[1:] = closes_1s[1:] != closes_1s[:-1]
        ticks_vamod = closes_1s[dedup_mask]

        rec = {
            "minute": minute, "open": o, "high": h, "low": l, "close": c,
            "volume": vol, "delta": delta, "vwap": vwap, "n_ticks_vamod": len(ticks_vamod)
        }

        # 1) Exato va-mod.ru sem bin (padrao de fabrica)
        p_raw, mv_raw, pct_raw, nl_raw = compute_vamod_poc(ticks_vamod, None, 0.0, tie_lowest=True)
        rec["vamod_raw_poc"] = p_raw
        rec["vamod_raw_mv"] = mv_raw
        rec["vamod_raw_pct"] = pct_raw
        rec["vamod_raw_nl"] = nl_raw

        # 2) va-mod.ru TPO com bins ($1, $5, $10, $20)
        for b in [1.0, 5.0, 10.0, 20.0]:
            # comparar close truncado no mesmo bin ou close real (va-mod compara close real vs bin_price)
            p_b, mv_b, pct_b, _ = compute_vamod_poc(ticks_vamod, None, b, tie_lowest=True)
            rec[f"tpo_b{int(b)}_poc"] = p_b
            rec[f"tpo_b{int(b)}_pct"] = pct_b
            rec[f"tpo_b{int(b)}_mv"] = mv_b

            # Volume REAL da Binance 1s no mesmo bin (centro do bin para nao criar vies sistematico!)
            p_vb, mv_vb, pct_vb, _ = compute_vamod_poc(closes_1s, vols_1s, b, tie_lowest=False)
            rec[f"vol_b{int(b)}_poc"] = p_vb + b / 2.0  # centro do bin
            rec[f"vol_b{int(b)}_pct"] = pct_vb

            # TPO centrado no bin sem vies de tie_lowest
            p_tc, _, pct_tc, _ = compute_vamod_poc(ticks_vamod, None, b, tie_lowest=False)
            rec[f"tpoc_b{int(b)}_poc"] = p_tc + b / 2.0
            rec[f"tpoc_b{int(b)}_pct"] = pct_tc

        records.append(rec)

    m1 = pd.DataFrame(records).sort_values("minute").reset_index(drop=True)
    # Alinhamento estritamente causal: features de t-1 preveem vela t (somente minutos consecutivos == 60s)
    dt_sec = m1["minute"].diff().dt.total_seconds()
    valid_next = (dt_sec == 60.0).to_numpy()

    opens_t = m1["open"].to_numpy()[valid_next]
    closes_t = m1["close"].to_numpy()[valid_next]

    prev = m1.shift(1).loc[valid_next].reset_index(drop=True)
    n_candles = len(opens_t)
    doji_rate = float((opens_t == closes_t).mean())

    print(f"\n[BLOCO 1 — BINANCE 1s -> M1] Periodo: {m1['minute'].iloc[0]} -> {m1['minute'].iloc[-1]}")
    print(f"Velas M1 consecutivas: N={n_candles} | Dojis: {doji_rate*100:.2f}% | "
          f"Media maxVol(sem bin): {prev['vamod_raw_mv'].mean():.2f} ticks em {prev['vamod_raw_nl'].mean():.1f} niveis")

    def sig_gt_lt(a, b):
        return np.where(a > b, 1, np.where(a < b, -1, 0))

    c_prev = prev["close"].to_numpy()
    o_prev = prev["open"].to_numpy()
    color_prev = sig_gt_lt(c_prev, o_prev)

    rows = []
    # Baselines
    b1 = eval_signal("B1_sempre_CALL", np.ones(n_candles, dtype=int), opens_t, closes_t)
    b2_cont = eval_signal("B2_cor_vela_anterior (Cont)", color_prev, opens_t, closes_t)
    b2_rev = eval_signal("B2_cor_vela_anterior (Rev)", color_prev, opens_t, closes_t, invert=True)
    rows.extend([b1, b2_cont, b2_rev])
    base_acc = max(b1["acc"], b2_cont["acc"], b2_rev["acc"])

    # 1) Exato va-mod.ru padrao de fabrica (sem bin, tie_lowest=True)
    s_raw = sig_gt_lt(c_prev, prev["vamod_raw_poc"].to_numpy())
    rows.append(eval_signal("VAMOD_EXATO_FABRICA (Cont)", s_raw, opens_t, closes_t))
    rows.append(eval_signal("VAMOD_EXATO_FABRICA (Rev)", s_raw, opens_t, closes_t, invert=True))

    # 2) va-mod.ru com binning (obrezka) — igual codigo do site vs centrado sem vies
    for b in [1, 5, 10, 20]:
        s_tpo = sig_gt_lt(c_prev, prev[f"tpo_b{b}_poc"].to_numpy())
        rows.append(eval_signal(f"VAMOD_TPO_bin${b}_site (Cont)", s_tpo, opens_t, closes_t))
        s_tpoc = sig_gt_lt(c_prev, prev[f"tpoc_b{b}_poc"].to_numpy())
        rows.append(eval_signal(f"VAMOD_TPO_bin${b}_limpo (Cont)", s_tpoc, opens_t, closes_t))
        rows.append(eval_signal(f"VAMOD_TPO_bin${b}_limpo (Rev)", s_tpoc, opens_t, closes_t, invert=True))

    # 3) Upgrade: POC de VOLUME REAL da Binance 1s
    for b in [1, 5, 10, 20]:
        s_vol = sig_gt_lt(c_prev, prev[f"vol_b{b}_poc"].to_numpy())
        rows.append(eval_signal(f"BINANCE_VOL_POC_bin${b} (Cont)", s_vol, opens_t, closes_t))
        rows.append(eval_signal(f"BINANCE_VOL_POC_bin${b} (Rev)", s_vol, opens_t, closes_t, invert=True))

    # 4) VWAP real 1s
    s_vwap = sig_gt_lt(c_prev, prev["vwap"].to_numpy())
    rows.append(eval_signal("BINANCE_VWAP_1s (Cont)", s_vwap, opens_t, closes_t))
    rows.append(eval_signal("BINANCE_VWAP_1s (Rev)", s_vwap, opens_t, closes_t, invert=True))

    # 5) Filtros "P" e "b" (Setas Pequenas >= 75% extremo + discordancia da cor da vela!)
    for b in [5, 10]:
        s_vol = sig_gt_lt(c_prev, prev[f"vol_b{b}_poc"].to_numpy())
        pct = prev[f"vol_b{b}_pct"].to_numpy()
        # Padrao "b" (POC na parte inferior: pct >= 75 e close > POC -> CALL)
        # Padrao "P" (POC na parte superior: pct <= 25 e close < POC -> PUT)
        s_pb = np.where((s_vol == 1) & (pct >= 75), 1,
               np.where((s_vol == -1) & (pct <= 25), -1, 0))
        rows.append(eval_signal(f"PADRAO_b_P_vol${b}_ext75 (Cont)", s_pb, opens_t, closes_t))
        rows.append(eval_signal(f"PADRAO_b_P_vol${b}_ext75 (Rev)", s_pb, opens_t, closes_t, invert=True))

        # Discordancia: close vs POC discorda da cor da vela anterior (absorcao pura)
        s_dis = np.where((s_vol != 0) & (s_vol == -color_prev), s_vol, 0)
        rows.append(eval_signal(f"ABSORCAO_POC!=Cor_vol${b} (Cont)", s_dis, opens_t, closes_t))
        rows.append(eval_signal(f"ABSORCAO_POC!=Cor_vol${b} (Rev)", s_dis, opens_t, closes_t, invert=True))

    # 6) Check de concordancia com B2 (cor da vela anterior)
    s_vol5 = sig_gt_lt(c_prev, prev["vol_b5_poc"].to_numpy())
    s_tpo5 = sig_gt_lt(c_prev, prev["tpoc_b5_poc"].to_numpy())
    agree_color = (s_vol5 == color_prev).mean()
    agree_tpo_vol = (s_tpo5 == s_vol5).mean()
    print(f"Concordancia VOL_POC($5) vs Cor Anterior (B2): {agree_color*100:.1f}% | "
          f"TPO($5) vs VOL_POC($5): {agree_tpo_vol*100:.1f}%")

    print_table("BLOCO 1 — BINANCE 1s (N ~ 2.880 velas M1, Produto A no :00s)", rows, base_acc)


# ==============================================================================
# BLOCO 2: BINANCE AGGTRADES REAIS (6 HORAS, 469.926 TRADES, ~360 VELAS M1)
# ==============================================================================
def run_block_2_agg():
    df = pd.read_parquet(DATA_AGG)
    df["minute"] = df["timestamp"].dt.floor("min")
    df["second"] = df["timestamp"].dt.floor("s")
    # is_buyer_maker == False => agressao compradora (+qty); True => agressao vendedora (-qty)
    df["signed_qty"] = np.where(df["is_buyer_maker"], -df["qty"], df["qty"])

    # descartar primeiro e ultimo minuto parciais
    mins = sorted(df["minute"].unique())
    if len(mins) > 2:
        df = df[df["minute"].isin(mins[1:-1])].copy()

    records = []
    for minute, g in df.groupby("minute", sort=True):
        prices = g["price"].to_numpy(dtype=np.float64)
        qtys = g["qty"].to_numpy(dtype=np.float64)
        sqtys = g["signed_qty"].to_numpy(dtype=np.float64)
        o = float(prices[0])
        c = float(prices[-1])
        h = float(prices.max())
        l = float(prices.min())
        vol = float(qtys.sum())
        delta = float(sqtys.sum())
        vwap = float((prices * qtys).sum() / vol) if vol > 0 else c

        # Simular ticks 1s (como va-mod.ru) a partir do ultimo preco de cada segundo
        sec_closes = g.groupby("second")["price"].last().to_numpy(dtype=np.float64)
        rec = {"minute": minute, "open": o, "close": c, "high": h, "low": l,
               "volume": vol, "delta": delta, "vwap": vwap}

        for b in [1.0, 5.0, 10.0]:
            # Footprint REAL por volume de aggTrades
            p_agg, mv_agg, pct_agg, _ = compute_vamod_poc(prices, qtys, b, tie_lowest=False)
            rec[f"agg_poc_b{int(b)}"] = p_agg + b / 2.0
            rec[f"agg_pct_b{int(b)}"] = pct_agg

            # Delta exatamente na faixa do POC real
            binned = np.floor(prices / b) * b
            poc_bin = math.floor(p_agg / b) * b
            poc_delta = float(sqtys[binned == poc_bin].sum())
            rec[f"agg_poc_delta_b{int(b)}"] = poc_delta

            # TPO 1s estilo va-mod no mesmo bin
            p_tpo, _, _, _ = compute_vamod_poc(sec_closes, None, b, tie_lowest=False)
            rec[f"tpo1s_poc_b{int(b)}"] = p_tpo + b / 2.0

        records.append(rec)

    m1 = pd.DataFrame(records).sort_values("minute").reset_index(drop=True)
    dt_sec = m1["minute"].diff().dt.total_seconds()
    valid_next = (dt_sec == 60.0).to_numpy()
    opens_t = m1["open"].to_numpy()[valid_next]
    closes_t = m1["close"].to_numpy()[valid_next]
    prev = m1.shift(1).loc[valid_next].reset_index(drop=True)
    n_candles = len(opens_t)

    def sig_gt_lt(a, b):
        return np.where(a > b, 1, np.where(a < b, -1, 0))

    c_prev = prev["close"].to_numpy()
    o_prev = prev["open"].to_numpy()
    color_prev = sig_gt_lt(c_prev, o_prev)

    # Concordancia entre TPO 1s (estilo va-mod) e Footprint real aggTrades
    agree_5 = (sig_gt_lt(c_prev, prev["tpo1s_poc_b5"].to_numpy()) ==
               sig_gt_lt(c_prev, prev["agg_poc_b5"].to_numpy())).mean()

    print(f"\n[BLOCO 2 — AGGTRADES REAIS FOOTPRINT] Periodo: {m1['minute'].iloc[0]} -> {m1['minute'].iloc[-1]} | "
          f"N={n_candles} | Concordancia TPO_1s($5) vs Footprint_Real($5): {agree_5*100:.1f}%")

    rows = [
        eval_signal("B2_cor_vela_anterior (Cont)", color_prev, opens_t, closes_t),
        eval_signal("AGG_VWAP_real (Cont)", sig_gt_lt(c_prev, prev["vwap"].to_numpy()), opens_t, closes_t),
    ]
    for b in [1, 5, 10]:
        s_agg = sig_gt_lt(c_prev, prev[f"agg_poc_b{b}"].to_numpy())
        rows.append(eval_signal(f"FOOTPRINT_REAL_POC_b${b} (Cont)", s_agg, opens_t, closes_t))
        rows.append(eval_signal(f"FOOTPRINT_REAL_POC_b${b} (Rev)", s_agg, opens_t, closes_t, invert=True))

    # Absorcao real no POC:
    # CALL se close > POC e Delta no POC < 0 (vendedores agrediram o POC mas foram absorvidos e preco fechou acima!)
    # PUT se close < POC e Delta no POC > 0 (compradores agrediram o POC mas foram absorvidos e preco fechou abaixo!)
    for b in [5, 10]:
        s_agg = sig_gt_lt(c_prev, prev[f"agg_poc_b{b}"].to_numpy())
        pd_poc = prev[f"agg_poc_delta_b{b}"].to_numpy()
        s_abs = np.where((s_agg == 1) & (pd_poc < 0), 1,
                np.where((s_agg == -1) & (pd_poc > 0), -1, 0))
        rows.append(eval_signal(f"ABSORCAO_DELTA_POC_b${b} (Cont)", s_abs, opens_t, closes_t))
        rows.append(eval_signal(f"ABSORCAO_DELTA_POC_b${b} (Rev)", s_abs, opens_t, closes_t, invert=True))

    print_table("BLOCO 2 — FOOTPRINT / AGGTRADES REAIS (469k trades, N ~ 360 M1)", rows, rows[0]["acc"])


# ==============================================================================
# BLOCO 3: 110 DIAS COMPLETOS (M1 ~158k, M5 ~31.6k, M15 ~10.5k) — DEV vs HOLDOUT
# ==============================================================================
def run_block_3_110d():
    df_a = pd.read_parquet(DATA_1M_45D)
    df_b = pd.read_parquet(DATA_1M_65D)
    df = pd.concat([df_a, df_b], ignore_index=True)
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)

    # Split temporal estrito: Dev = primeiros 80 dias, Holdout = ultimos 30 dias
    split_ts = df["open_time"].max() - pd.Timedelta(days=30)

    # --- 3A: M1 (VWAP exato da vela = quote_asset_volume / volume + VSA esforco vs resultado) ---
    df["vwap"] = np.where(df["volume"] > 0, df["quote_asset_volume"] / df["volume"], df["close"])
    df["body_bps"] = (df["close"] - df["open"]).abs() / df["open"] * 10000.0
    df["range_bps"] = (df["high"] - df["low"]) / df["open"] * 10000.0
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["vwap_dist_bps"] = (df["close"] - df["vwap"]) / df["open"] * 10000.0
    # Posicao relativa do VWAP dentro do range [Low, High] (0 = High, 100 = Low, igual va-mod)
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    df["vwap_pct_from_top"] = ((df["high"] - df["vwap"]) / rng * 100.0).fillna(50.0)

    dt_ok = (df["open_time"].diff().dt.total_seconds() == 60.0)
    prev = df.shift(1)

    for label, mask_split in [("DEV (80d)", df["open_time"] < split_ts),
                              ("HOLDOUT (30d)", df["open_time"] >= split_ts)]:
        m = dt_ok & mask_split & prev["vol_ma20"].notna()
        opens_t = df.loc[m, "open"].to_numpy()
        closes_t = df.loc[m, "close"].to_numpy()

        c_p = prev.loc[m, "close"].to_numpy()
        o_p = prev.loc[m, "open"].to_numpy()
        v_p = prev.loc[m, "vwap"].to_numpy()
        dist_p = prev.loc[m, "vwap_dist_bps"].to_numpy()
        pct_p = prev.loc[m, "vwap_pct_from_top"].to_numpy()
        vol_ratio = (prev.loc[m, "volume"] / prev.loc[m, "vol_ma20"]).to_numpy()
        body_ratio = (prev.loc[m, "body_bps"] / (prev.loc[m, "range_bps"] + 1e-9)).to_numpy()

        color_p = np.where(c_p > o_p, 1, np.where(c_p < o_p, -1, 0))
        s_vwap = np.where(c_p > v_p, 1, np.where(c_p < v_p, -1, 0))

        # Threshold Q80 calculado no Dev apenas (para nao vazar Holdout)
        if label.startswith("DEV"):
            q80_dist = float(np.quantile(np.abs(dist_p), 0.80))

        s_vwap_q80 = np.where(dist_p > q80_dist, 1, np.where(dist_p < -q80_dist, -1, 0))
        s_dis = np.where((s_vwap != 0) & (s_vwap == -color_p), s_vwap, 0)
        # VSA Absorcao (volume > 2x media, corpo < 35% do range, close > VWAP = defesa compradora; close < VWAP = defesa vendedora)
        s_vsa = np.where((vol_ratio >= 2.0) & (body_ratio <= 0.35), s_vwap, 0)
        # Padrao "b" / "P" no VWAP (VWAP nos 25% extremos + close do lado oposto)
        s_pb = np.where((s_vwap == 1) & (pct_p >= 70), 1,
               np.where((s_vwap == -1) & (pct_p <= 30), -1, 0))

        rows = [
            eval_signal("B2_cor_vela_anterior (Cont)", color_p, opens_t, closes_t),
            eval_signal("B2_cor_vela_anterior (Rev)", color_p, opens_t, closes_t, invert=True),
            eval_signal("M1_close_vs_VWAP (Cont)", s_vwap, opens_t, closes_t),
            eval_signal("M1_close_vs_VWAP (Rev)", s_vwap, opens_t, closes_t, invert=True),
            eval_signal("M1_close_vs_VWAP_Q80 (Cont)", s_vwap_q80, opens_t, closes_t),
            eval_signal("M1_close_vs_VWAP_Q80 (Rev)", s_vwap_q80, opens_t, closes_t, invert=True),
            eval_signal("M1_VWAP!=Cor_Absorcao (Cont)", s_dis, opens_t, closes_t),
            eval_signal("M1_VWAP!=Cor_Absorcao (Rev)", s_dis, opens_t, closes_t, invert=True),
            eval_signal("M1_VSA_Vol2x_CorpoPeq (Cont)", s_vsa, opens_t, closes_t),
            eval_signal("M1_VSA_Vol2x_CorpoPeq (Rev)", s_vsa, opens_t, closes_t, invert=True),
            eval_signal("M1_Padrao_b_P_VWAP70 (Cont)", s_pb, opens_t, closes_t),
            eval_signal("M1_Padrao_b_P_VWAP70 (Rev)", s_pb, opens_t, closes_t, invert=True),
        ]
        base_acc = max(rows[0]["acc"], rows[1]["acc"])
        print_table(f"BLOCO 3A — M1 110 DIAS [{label}] (N={len(opens_t)})", rows, base_acc)

    # --- 3B: M5 e M15 com POC REAL DE VOLUME (usando sub-velas M1!) ---
    for tf_min in [5, 15]:
        df["bucket"] = df["open_time"].dt.floor(f"{tf_min}min")
        # agrupar e calcular POC real = VWAP da sub-vela M1 que teve o MAIOR volume real dentro do candle M5/M15
        g_size = df.groupby("bucket").size()
        full_buckets = g_size[g_size == tf_min].index
        sub = df[df["bucket"].isin(full_buckets)].copy()

        # indice da sub-vela M1 com maior volume em cada bucket
        idx_max_vol = sub.groupby("bucket")["volume"].idxmax()
        poc_df = sub.loc[idx_max_vol, ["bucket", "vwap", "high", "low"]].set_index("bucket")
        poc_df.columns = ["poc_price", "poc_h", "poc_l"]

        agg_tf = sub.groupby("bucket").agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            quote_vol=("quote_asset_volume", "sum"),
        )
        agg_tf["vwap"] = np.where(agg_tf["volume"] > 0, agg_tf["quote_vol"] / agg_tf["volume"], agg_tf["close"])
        agg_tf = agg_tf.join(poc_df).reset_index().sort_values("bucket").reset_index(drop=True)

        dt_tf = agg_tf["bucket"].diff().dt.total_seconds() == (tf_min * 60.0)
        prev_tf = agg_tf.shift(1)

        for label, mask_split in [("DEV (80d)", agg_tf["bucket"] < split_ts),
                                  ("HOLDOUT (30d)", agg_tf["bucket"] >= split_ts)]:
            m = dt_tf & mask_split
            opens_t = agg_tf.loc[m, "open"].to_numpy()
            closes_t = agg_tf.loc[m, "close"].to_numpy()
            c_p = prev_tf.loc[m, "close"].to_numpy()
            o_p = prev_tf.loc[m, "open"].to_numpy()
            h_p = prev_tf.loc[m, "high"].to_numpy()
            l_p = prev_tf.loc[m, "low"].to_numpy()
            poc_p = prev_tf.loc[m, "poc_price"].to_numpy()
            vwap_p = prev_tf.loc[m, "vwap"].to_numpy()

            color_p = np.where(c_p > o_p, 1, np.where(c_p < o_p, -1, 0))
            s_poc = np.where(c_p > poc_p, 1, np.where(c_p < poc_p, -1, 0))
            s_vwap = np.where(c_p > vwap_p, 1, np.where(c_p < vwap_p, -1, 0))
            pct_poc = np.where(h_p > l_p, (h_p - poc_p) / (h_p - l_p) * 100.0, 50.0)
            s_pb = np.where((s_poc == 1) & (pct_poc >= 75), 1,
                   np.where((s_poc == -1) & (pct_poc <= 25), -1, 0))
            s_dis = np.where((s_poc != 0) & (s_poc == -color_p), s_poc, 0)

            rows = [
                eval_signal(f"B2_cor_anterior_M{tf_min} (Cont)", color_p, opens_t, closes_t),
                eval_signal(f"B2_cor_anterior_M{tf_min} (Rev)", color_p, opens_t, closes_t, invert=True),
                eval_signal(f"M{tf_min}_VAMOD_VOL_POC (Cont)", s_poc, opens_t, closes_t),
                eval_signal(f"M{tf_min}_VAMOD_VOL_POC (Rev)", s_poc, opens_t, closes_t, invert=True),
                eval_signal(f"M{tf_min}_VWAP_real (Cont)", s_vwap, opens_t, closes_t),
                eval_signal(f"M{tf_min}_VWAP_real (Rev)", s_vwap, opens_t, closes_t, invert=True),
                eval_signal(f"M{tf_min}_Padrao_b_P_POC75 (Cont)", s_pb, opens_t, closes_t),
                eval_signal(f"M{tf_min}_Padrao_b_P_POC75 (Rev)", s_pb, opens_t, closes_t, invert=True),
                eval_signal(f"M{tf_min}_POC!=Cor_Absorcao (Cont)", s_dis, opens_t, closes_t),
                eval_signal(f"M{tf_min}_POC!=Cor_Absorcao (Rev)", s_dis, opens_t, closes_t, invert=True),
            ]
            base_acc = max(rows[0]["acc"], rows[1]["acc"])
            print_table(f"BLOCO 3B — M{tf_min} POC VOLUME REAL 110 DIAS [{label}] (N={len(opens_t)})", rows, base_acc)


if __name__ == "__main__":
    run_block_1_1s()
    run_block_2_agg()
    run_block_3_110d()
